"""Real /search trigger and /on_search receipt (livetracker2.md Phase 3.1). Two
distinct surfaces, deliberately not conflated (API_CONVENTIONS.md's scope line, §3.6):
- `trigger_search`/`get_search_results`: customer/web-facing, non-Beckn-protocol,
  simple JSON in/out — the browser never speaks raw Beckn Intent/Catalog shapes.
- `validate_and_ack_on_search`/`record_on_search_result`: the real Beckn wire endpoint,
  full context/signature verification, exact confirmed schema shapes.
"""

import json
import logging

from beckn_transaction import (
    PayloadValidationError,
    build_ack_response,
    build_context,
    build_nack_response,
    new_message_id,
    new_transaction_id,
    validate_context,
)
from django.conf import settings
from django.utils import timezone

from . import registry_client, trust
from .crypto import sign_outbound_request
from .models import SearchSession
from .participant_keys import get_signing_keys

logger = logging.getLogger("bap")


class SearchError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def trigger_search(*, query: str, domain: str, customer=None) -> str:
    """Customer-facing entry point: builds a real Beckn Intent + context, signs it,
    and sends it to Gateway's /search — synchronously, but only waiting for Gateway's
    immediate ACK (a fast local call), never for the actual catalog data, which arrives
    later via /on_search. Creates the SearchSession real results accumulate into.
    Returns the transaction_id the customer polls with. Raises SearchError if Gateway
    itself rejects the request (NACK) or is unreachable."""
    transaction_id = new_transaction_id()
    context = build_context(
        domain=domain,
        action="search",
        version="1.1.0",
        bap_id=settings.SUBSCRIBER_ID,
        bap_uri=settings.SUBSCRIBER_URL,
        transaction_id=transaction_id,
        message_id=new_message_id(),
        location={"country": {"code": "IND"}},
        timestamp=timezone.now().isoformat(),
    )
    payload = {"context": context, "message": {"intent": {"item": {"descriptor": {"name": query}}}}}
    body = json.dumps(payload).encode()

    _, signing_priv = get_signing_keys()
    auth_header = sign_outbound_request(
        body=body,
        subscriber_id=settings.SUBSCRIBER_ID,
        unique_key_id=settings.UNIQUE_KEY_ID,
        signing_private_key_b64=signing_priv,
    )

    gateway_search_url = settings.GATEWAY_BASE_URL.rstrip("/") + "/search"
    try:
        response = registry_client.get_client().post(
            gateway_search_url,
            data=body,
            headers={"Content-Type": "application/json", "Authorization": auth_header},
        )
        response.raise_for_status()
    except Exception as exc:
        raise SearchError(f"Gateway unreachable: {exc}", status_code=502) from exc

    ack_status = response.json().get("message", {}).get("ack", {}).get("status")
    if ack_status != "ACK":
        raise SearchError("Gateway rejected the search request (NACK)", status_code=502)

    SearchSession.objects.create(
        transaction_id=transaction_id, customer=customer, query=query, domain=domain
    )
    return transaction_id


def get_search_results(*, transaction_id: str) -> dict | None:
    """Returns the current accumulated results for a transaction_id, or None if no
    such search session exists. Results may be an empty list if on_search hasn't
    arrived yet — that's a normal, honest in-progress state, not an error."""
    try:
        session = SearchSession.objects.get(transaction_id=transaction_id)
    except SearchSession.DoesNotExist:
        return None
    return {
        "transaction_id": session.transaction_id,
        "query": session.query,
        "domain": session.domain,
        "results": session.results,
    }


def validate_and_ack_on_search(
    *, payload: dict, authorization_header: str, gateway_authorization_header: str, body: bytes
) -> tuple[dict, int]:
    """Synchronous half of /on_search: verifies the BPP and the forwarding Gateway,
    returns the ACK/NACK envelope. Does NOT record the result — that's
    record_on_search_result's job, called after this returns 200."""
    try:
        context = payload["context"]
        validate_context(context)
    except (KeyError, PayloadValidationError) as exc:
        return (
            build_nack_response(
                context=payload.get("context", {}),
                error={"code": "SEARCH_ERROR", "message": f"Invalid context: {exc}"},
            ),
            400,
        )

    try:
        trust.verify_bpp_and_gateway(
            context=context,
            authorization_header=authorization_header,
            gateway_authorization_header=gateway_authorization_header,
            body=body,
        )
    except trust.TrustEstablishmentError as exc:
        return (
            build_nack_response(
                context=context, error={"code": "SEARCH_ERROR", "message": str(exc)}
            ),
            401,
        )

    return build_ack_response(context=context), 200


def record_on_search_result(*, payload: dict) -> None:
    """Appends the real catalog to the matching SearchSession's results, wrapped with
    the responding BPP's own bpp_id/bpp_uri from the enveloping context — not just the
    bare catalog. §3.2 needs this: selecting a specific item requires knowing which
    BPP actually offered it, and the catalog alone doesn't carry that (it's a property
    of the on_search envelope, not the catalog payload itself). A callback for a
    transaction_id this BAP has no record of (already expired, or a stray/malicious
    callback) is logged and dropped, not silently accepted into a new, unexplained
    session."""
    context = payload["context"]
    transaction_id = context.get("transaction_id")
    try:
        session = SearchSession.objects.get(transaction_id=transaction_id)
    except SearchSession.DoesNotExist:
        logger.warning(
            "record_on_search_result: no SearchSession for transaction_id=%r, dropping",
            transaction_id,
        )
        return

    result = {
        "bpp_id": context.get("bpp_id"),
        "bpp_uri": context.get("bpp_uri"),
        "catalog": payload["message"]["catalog"],
    }
    session.results = [*session.results, result]
    session.save(update_fields=["results", "updated_at"])
