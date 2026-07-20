"""Search routing — real business logic per livetracker2.md Phase 3.1, extending the
Phase 4.1 trust-chain-only plumbing (livetracker1.md) which only proved signature
verification + BPP discovery worked, without forwarding anything.

Async is mandatory, not optional (protocol_compliance_notes_v1.1.md §H.1, confirmed
against the real spec — a "sync as network policy" proposal was explicitly rejected
upstream): `validate_and_ack_search` does only synchronous work (context validation,
signature verification, identity-match check) and returns the immediate ACK/NACK
response; `dispatch_search` does the actual forwarding to each SUBSCRIBED BPP and is
meant to be run in the background (a thread, from the view) so the ACK returns without
waiting on any BPP. Split into two functions specifically so each can be tested without
racing a background thread — `dispatch_search` is directly callable/synchronous from a
test's point of view, the view is what makes it fire-and-forget in production.
"""

import json
import logging
import threading

from beckn_transaction import (
    PayloadValidationError,
    build_ack_response,
    build_nack_response,
    validate_context,
)
from django.conf import settings

from . import registry_client, trust
from .crypto import sign_outbound_request
from .participant_keys import get_signing_keys

logger = logging.getLogger("gateway")


class RoutingError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _verify_caller(
    *, context: dict, authorization_header: str, body: bytes, expected_id_field: str
) -> None:
    """Validates context, verifies the caller's signature against their Registry-
    registered key, and confirms the Authorization identity matches
    context[expected_id_field] (defense against a valid signature for one participant
    being replayed under a different claimed identity) — `bap_id` for an inbound
    /search from a BAP, `bpp_id` for an inbound /on_search from a BPP. Raises
    RoutingError on any failure."""
    try:
        validate_context(context)
    except PayloadValidationError as exc:
        raise RoutingError(f"Invalid context: {exc}", status_code=400) from exc

    if not authorization_header:
        raise RoutingError("Missing Authorization header", status_code=401)

    try:
        trust.verify_participant_signature(authorization_header=authorization_header, body=body)
    except trust.TrustEstablishmentError as exc:
        raise RoutingError(f"Signature verification failed: {exc}", status_code=401) from exc

    from beckn_crypto import parse_authorization_header

    signer_subscriber_id = parse_authorization_header(authorization_header)["subscriber_id"]
    expected_id = context.get(expected_id_field)
    if signer_subscriber_id != expected_id:
        raise RoutingError(
            f"Signature identity ({signer_subscriber_id!r}) does not match "
            f"context.{expected_id_field} ({expected_id!r})",
            status_code=401,
        )


def validate_and_ack_search(
    *, payload: dict, authorization_header: str, body: bytes
) -> tuple[dict, int]:
    """Synchronous half of /search: validates and verifies the calling BAP, returns
    the real ACK/NACK envelope (protocol_compliance_notes_v1.1.md §H.2) and the HTTP
    status to send. Does NOT forward to any BPP — that's dispatch_search's job, meant
    to run after this returns, not blocking the caller on it."""
    try:
        context = payload["context"]
    except KeyError as exc:
        raise RoutingError("Missing context", status_code=400) from exc

    try:
        _verify_caller(
            context=context,
            authorization_header=authorization_header,
            body=body,
            expected_id_field="bap_id",
        )
    except RoutingError as exc:
        return (
            build_nack_response(
                context=context, error={"code": "ROUTING_ERROR", "message": exc.message}
            ),
            exc.status_code,
        )

    return build_ack_response(context=context), 200


def validate_and_ack_on_search(
    *, payload: dict, authorization_header: str, body: bytes
) -> tuple[dict, int]:
    """Synchronous half of /on_search: validates and verifies the calling BPP (its
    identity must match context.bpp_id, not bap_id — the roles are reversed from
    /search), returns the ACK/NACK envelope. Does NOT relay to the BAP — that's
    relay_on_search's job."""
    try:
        context = payload["context"]
    except KeyError as exc:
        raise RoutingError("Missing context", status_code=400) from exc

    try:
        _verify_caller(
            context=context,
            authorization_header=authorization_header,
            body=body,
            expected_id_field="bpp_id",
        )
    except RoutingError as exc:
        return (
            build_nack_response(
                context=context, error={"code": "ROUTING_ERROR", "message": exc.message}
            ),
            exc.status_code,
        )

    return build_ack_response(context=context), 200


def dispatch_search(*, payload: dict, authorization_header: str) -> None:
    """The actual forwarding work: looks up SUBSCRIBED BPPs for context.domain, and
    POSTs the intent to each one's own /search, signed with Gateway's own
    X-Gateway-Authorization (protocol_compliance_notes_v1.1.md §H.3) alongside the
    original, unmodified Authorization header from the caller — `authorization_header`
    is a separate parameter, not merged into `payload`, specifically so the forwarded
    body (`json.dumps(payload)`) is byte-identical to what the caller originally sent,
    not corrupted with routing metadata that was never part of the real wire payload.
    Fire-and-forget by design — Gateway does not wait for or process each BPP's
    response here; each BPP's own /on_search callback (routed back through Gateway
    separately, see on_search_view) is where the real catalog data arrives. Failures
    are logged, not raised — a single unreachable BPP must not affect the others or
    anything the original caller already received (the ACK)."""
    context = payload["context"]
    body = json.dumps(payload).encode()

    try:
        bpps = registry_client.lookup({"domain": context["domain"], "type": "sellerApp"})
    except Exception:
        logger.exception("dispatch_search: Registry lookup failed for domain %s", context["domain"])
        return

    subscribed_bpps = [bpp for bpp in bpps if bpp.get("status") == "SUBSCRIBED"]
    if not subscribed_bpps:
        logger.info("dispatch_search: no SUBSCRIBED BPPs for domain %s", context["domain"])
        return

    _, signing_priv = get_signing_keys()
    gateway_signature = sign_outbound_request(
        body=body,
        subscriber_id=settings.SUBSCRIBER_ID,
        unique_key_id=settings.UNIQUE_KEY_ID,
        signing_private_key_b64=signing_priv,
    )

    client = registry_client.get_client()
    for bpp in subscribed_bpps:
        bpp_search_url = bpp["url"].rstrip("/") + "/search"
        headers = {
            "Content-Type": "application/json",
            "Authorization": authorization_header,
            "X-Gateway-Authorization": gateway_signature,
        }
        try:
            response = client.post(bpp_search_url, data=body, headers=headers)
            response.raise_for_status()
        except Exception:
            logger.exception("dispatch_search: forwarding to %s failed", bpp_search_url)


def dispatch_search_in_background(*, payload: dict, authorization_header: str) -> None:
    """Fires dispatch_search on a daemon thread — the actual fire-and-forget entry
    point the view uses. Kept separate from dispatch_search so tests can call
    dispatch_search directly and synchronously without racing a thread."""
    thread = threading.Thread(
        target=dispatch_search,
        kwargs={"payload": payload, "authorization_header": authorization_header},
        daemon=True,
    )
    thread.start()


def relay_on_search(*, payload: dict, authorization_header: str) -> None:
    """Forwards a BPP's /on_search callback on to the originating BAP
    (protocol_compliance_notes_v1.1.md §H.4 — on_search routes back through Gateway,
    not directly BPP -> BAP). No Registry lookup needed here: context.bap_uri already
    travels with the context from the original /search request, echoed back by the BPP
    per protocol semantics, so Gateway already knows exactly where to send this without
    a fresh discovery step. Same fire-and-forget/log-don't-raise discipline as
    dispatch_search."""
    context = payload["context"]
    body = json.dumps(payload).encode()

    bap_uri = context.get("bap_uri")
    if not bap_uri:
        logger.error("relay_on_search: context missing bap_uri, cannot relay callback")
        return

    _, signing_priv = get_signing_keys()
    gateway_signature = sign_outbound_request(
        body=body,
        subscriber_id=settings.SUBSCRIBER_ID,
        unique_key_id=settings.UNIQUE_KEY_ID,
        signing_private_key_b64=signing_priv,
    )

    bap_on_search_url = bap_uri.rstrip("/") + "/on_search"
    headers = {
        "Content-Type": "application/json",
        "Authorization": authorization_header,
        "X-Gateway-Authorization": gateway_signature,
    }
    try:
        client = registry_client.get_client()
        response = client.post(bap_on_search_url, data=body, headers=headers)
        response.raise_for_status()
    except Exception:
        logger.exception("relay_on_search: forwarding to %s failed", bap_on_search_url)


def relay_on_search_in_background(*, payload: dict, authorization_header: str) -> None:
    """Fires relay_on_search on a daemon thread — same fire-and-forget entry point
    pattern as dispatch_search_in_background."""
    thread = threading.Thread(
        target=relay_on_search,
        kwargs={"payload": payload, "authorization_header": authorization_header},
        daemon=True,
    )
    thread.start()


# --- select / on_select (livetracker2.md Phase 3.2) --------------------------------------


def validate_and_ack_select(
    *, payload: dict, authorization_header: str, body: bytes
) -> tuple[dict, int]:
    """Synchronous half of /select: validates and verifies the calling BAP, returns
    the real ACK/NACK envelope. Does NOT forward to the BPP — that's dispatch_select's
    job, meant to run after this returns."""
    try:
        context = payload["context"]
    except KeyError as exc:
        raise RoutingError("Missing context", status_code=400) from exc

    try:
        _verify_caller(
            context=context,
            authorization_header=authorization_header,
            body=body,
            expected_id_field="bap_id",
        )
    except RoutingError as exc:
        return (
            build_nack_response(
                context=context, error={"code": "ROUTING_ERROR", "message": exc.message}
            ),
            exc.status_code,
        )

    return build_ack_response(context=context), 200


def validate_and_ack_on_select(
    *, payload: dict, authorization_header: str, body: bytes
) -> tuple[dict, int]:
    """Synchronous half of /on_select: validates and verifies the calling BPP (its
    identity must match context.bpp_id). Does NOT relay to the BAP — that's
    relay_on_select's job."""
    try:
        context = payload["context"]
    except KeyError as exc:
        raise RoutingError("Missing context", status_code=400) from exc

    try:
        _verify_caller(
            context=context,
            authorization_header=authorization_header,
            body=body,
            expected_id_field="bpp_id",
        )
    except RoutingError as exc:
        return (
            build_nack_response(
                context=context, error={"code": "ROUTING_ERROR", "message": exc.message}
            ),
            exc.status_code,
        )

    return build_ack_response(context=context), 200


def dispatch_select(*, payload: dict, authorization_header: str) -> None:
    """Forwards a customer's selection to the ONE specific BPP they already chose from
    an earlier search's real on_search results — unlike dispatch_search's broadcast to
    every SUBSCRIBED BPP in the domain, /select's context already carries a real
    bpp_id/bpp_uri (populated by the BPP's own on_search response and echoed back
    through the whole flow).

    Re-checks that BPP's SUBSCRIBED status via a fresh Registry lookup before
    forwarding — a real, not hypothetical, gap: a BPP's status can go stale between
    search and select, and dispatch_search already re-checks SUBSCRIBED on every call,
    so forwarding select to a remembered bpp_uri without the same re-check would be a
    real trust-chain regression (protocol_compliance_notes_v1.1.md §I, `livetracker2.md`
    §3.2's own gap-closed note). Same fire-and-forget/log-don't-raise discipline as
    dispatch_search."""
    context = payload["context"]
    body = json.dumps(payload).encode()

    bpp_id = context.get("bpp_id")
    bpp_uri = context.get("bpp_uri")
    if not bpp_id or not bpp_uri:
        logger.error("dispatch_select: context missing bpp_id/bpp_uri, cannot forward")
        return

    try:
        results = registry_client.lookup({"subscriber_id": bpp_id})
    except Exception:
        logger.exception("dispatch_select: Registry lookup failed for bpp_id %s", bpp_id)
        return

    if not results or results[0].get("status") != "SUBSCRIBED":
        logger.info(
            "dispatch_select: bpp_id %s is no longer SUBSCRIBED, refusing to forward", bpp_id
        )
        return

    _, signing_priv = get_signing_keys()
    gateway_signature = sign_outbound_request(
        body=body,
        subscriber_id=settings.SUBSCRIBER_ID,
        unique_key_id=settings.UNIQUE_KEY_ID,
        signing_private_key_b64=signing_priv,
    )

    bpp_select_url = bpp_uri.rstrip("/") + "/select"
    headers = {
        "Content-Type": "application/json",
        "Authorization": authorization_header,
        "X-Gateway-Authorization": gateway_signature,
    }
    try:
        client = registry_client.get_client()
        response = client.post(bpp_select_url, data=body, headers=headers)
        response.raise_for_status()
    except Exception:
        logger.exception("dispatch_select: forwarding to %s failed", bpp_select_url)


def dispatch_select_in_background(*, payload: dict, authorization_header: str) -> None:
    """Fires dispatch_select on a daemon thread — same fire-and-forget entry point
    pattern as dispatch_search_in_background."""
    thread = threading.Thread(
        target=dispatch_select,
        kwargs={"payload": payload, "authorization_header": authorization_header},
        daemon=True,
    )
    thread.start()


def relay_on_select(*, payload: dict, authorization_header: str) -> None:
    """Forwards a BPP's /on_select callback on to the originating BAP — on_select
    routes back through Gateway, same as on_search (§H.4). Same
    fire-and-forget/log-don't-raise discipline as relay_on_search."""
    context = payload["context"]
    body = json.dumps(payload).encode()

    bap_uri = context.get("bap_uri")
    if not bap_uri:
        logger.error("relay_on_select: context missing bap_uri, cannot relay callback")
        return

    _, signing_priv = get_signing_keys()
    gateway_signature = sign_outbound_request(
        body=body,
        subscriber_id=settings.SUBSCRIBER_ID,
        unique_key_id=settings.UNIQUE_KEY_ID,
        signing_private_key_b64=signing_priv,
    )

    bap_on_select_url = bap_uri.rstrip("/") + "/on_select"
    headers = {
        "Content-Type": "application/json",
        "Authorization": authorization_header,
        "X-Gateway-Authorization": gateway_signature,
    }
    try:
        client = registry_client.get_client()
        response = client.post(bap_on_select_url, data=body, headers=headers)
        response.raise_for_status()
    except Exception:
        logger.exception("relay_on_select: forwarding to %s failed", bap_on_select_url)


def relay_on_select_in_background(*, payload: dict, authorization_header: str) -> None:
    """Fires relay_on_select on a daemon thread — same fire-and-forget entry point
    pattern as relay_on_search_in_background."""
    thread = threading.Thread(
        target=relay_on_select,
        kwargs={"payload": payload, "authorization_header": authorization_header},
        daemon=True,
    )
    thread.start()
