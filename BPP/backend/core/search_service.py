"""Real /search and /on_search business logic (livetracker2.md Phase 3.1) — this is
what §2.3's `build_beauty_catalog()` was built for but explicitly not yet wired to, per
that function's own docstring ("Not yet wired to search/on_search — that's Phase 3's
job"). Same synchronous-validation/background-dispatch split as beckn-gateway's
core/routing.py, for the same reason (protocol_compliance_notes_v1.1.md §H.1: async is
mandatory, ACK returns without waiting on the actual catalog build/relay).
"""

import json
import logging
import threading

from beckn_transaction import (
    PayloadValidationError,
    build_ack_response,
    build_context,
    build_nack_response,
    validate_context,
)
from django.conf import settings
from django.utils import timezone

from . import registry_client, trust
from .catalog import build_beauty_catalog
from .crypto import sign_outbound_request
from .participant_keys import get_signing_keys

logger = logging.getLogger("bpp")


def validate_and_ack_search(
    *, payload: dict, authorization_header: str, gateway_authorization_header: str, body: bytes
) -> tuple[dict, int]:
    """Synchronous half of /search: verifies the BAP and the forwarding Gateway,
    returns the ACK/NACK envelope. Does NOT build or send the catalog — that's
    dispatch_on_search's job, fired in the background after this returns."""
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
        trust.verify_bap_and_gateway(
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


def dispatch_on_search(*, payload: dict) -> None:
    """Builds the real Beauty catalog (§2.3's build_beauty_catalog(), untouched) and
    sends it as a real, signed /on_search callback to Gateway (not directly to the
    BAP — protocol_compliance_notes_v1.1.md §H.4: on_search routes back through
    Gateway). Fire-and-forget: failures are logged, not raised, same discipline as
    Gateway's own dispatch_search/relay_on_search."""
    context = payload["context"]
    catalog = build_beauty_catalog()

    on_search_context = build_context(
        domain=context["domain"],
        action="on_search",
        version=context["version"],
        bap_id=context["bap_id"],
        bap_uri=context["bap_uri"],
        bpp_id=settings.SUBSCRIBER_ID,
        bpp_uri=settings.SUBSCRIBER_URL,
        transaction_id=context["transaction_id"],
        message_id=context["message_id"],
        location=context["location"],
        timestamp=timezone.now().isoformat(),
    )
    on_search_payload = {"context": on_search_context, "message": {"catalog": catalog}}
    body = json.dumps(on_search_payload).encode()

    _, signing_priv = get_signing_keys()
    auth_header = sign_outbound_request(
        body=body,
        subscriber_id=settings.SUBSCRIBER_ID,
        unique_key_id=settings.UNIQUE_KEY_ID,
        signing_private_key_b64=signing_priv,
    )

    gateway_on_search_url = settings.GATEWAY_BASE_URL.rstrip("/") + "/on_search"
    try:
        response = registry_client.get_gateway_client().post(
            gateway_on_search_url,
            data=body,
            headers={"Content-Type": "application/json", "Authorization": auth_header},
        )
        response.raise_for_status()
    except Exception:
        logger.exception(
            "dispatch_on_search: sending on_search to %s failed", gateway_on_search_url
        )


def dispatch_on_search_in_background(*, payload: dict) -> None:
    """Fires dispatch_on_search on a daemon thread — the actual fire-and-forget entry
    point the view uses. Kept separate so tests can call dispatch_on_search directly
    and synchronously without racing a thread."""
    thread = threading.Thread(target=dispatch_on_search, kwargs={"payload": payload}, daemon=True)
    thread.start()
