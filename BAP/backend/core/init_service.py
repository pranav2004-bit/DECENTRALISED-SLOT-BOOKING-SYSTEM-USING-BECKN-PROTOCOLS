"""Real /init trigger and /on_init receipt (livetracker2.md Phase 3.3). Mirrors
select_service.py's split exactly:
- `trigger_init`/`get_init_result`: customer/web-facing, non-Beckn-protocol.
- `validate_and_ack_on_init`/`record_on_init_result`: the real Beckn wire endpoint.

Wire shape confirmed before implementing (protocol_compliance_notes_v1.1.md §J.1):
both /init's request and /on_init's response carry `message.order`, the same
pattern already proven for /select/on_select.
"""

import json
import logging

from beckn_transaction import (
    PayloadValidationError,
    build_ack_response,
    build_context,
    build_nack_response,
    new_message_id,
    validate_context,
)
from django.conf import settings
from django.utils import timezone

from . import registry_client, trust
from .crypto import sign_outbound_request
from .models import SearchSession
from .participant_keys import get_signing_keys

logger = logging.getLogger("bap")


class InitError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def trigger_init(*, transaction_id: str) -> None:
    """Customer-facing initialization trigger — builds and sends the real signed
    Beckn /init to Gateway, targeting the same BPP a prior successful /select
    already resolved to. Resends only `provider`/`items`/`fulfillments` from the
    recorded selected_order, deliberately dropping its `quote` — the BPP recomputes
    pricing fresh from live state (Source-of-Truth rule, same principle §3.2
    established at Selection), not an echo of a client-held value. Clears any
    previous init result first, so a re-init doesn't briefly show stale data while
    the new one is in flight."""
    try:
        session = SearchSession.objects.get(transaction_id=transaction_id)
    except SearchSession.DoesNotExist:
        raise InitError("No such search transaction", status_code=404) from None

    if not session.selected_order or not session.selected_bpp_id:
        raise InitError(
            "No successful selection to initialize for this transaction", status_code=400
        )

    session.init_order = None
    session.init_error = None
    session.save(update_fields=["init_order", "init_error", "updated_at"])

    context = build_context(
        domain=session.domain,
        action="init",
        version="1.1.0",
        bap_id=settings.SUBSCRIBER_ID,
        bap_uri=settings.SUBSCRIBER_URL,
        bpp_id=session.selected_bpp_id,
        bpp_uri=session.selected_bpp_uri,
        transaction_id=transaction_id,
        message_id=new_message_id(),
        location={"country": {"code": "IND"}},
        timestamp=timezone.now().isoformat(),
    )
    order = {
        "provider": session.selected_order["provider"],
        "items": session.selected_order["items"],
        "fulfillments": session.selected_order["fulfillments"],
    }
    payload = {"context": context, "message": {"order": order}}
    body = json.dumps(payload).encode()

    _, signing_priv = get_signing_keys()
    auth_header = sign_outbound_request(
        body=body,
        subscriber_id=settings.SUBSCRIBER_ID,
        unique_key_id=settings.UNIQUE_KEY_ID,
        signing_private_key_b64=signing_priv,
    )

    gateway_init_url = settings.GATEWAY_BASE_URL.rstrip("/") + "/init"
    try:
        response = registry_client.get_gateway_client().post(
            gateway_init_url,
            data=body,
            headers={"Content-Type": "application/json", "Authorization": auth_header},
        )
        response.raise_for_status()
    except Exception as exc:
        raise InitError(f"Gateway unreachable: {exc}", status_code=502) from exc

    ack_status = response.json().get("message", {}).get("ack", {}).get("status")
    if ack_status != "ACK":
        raise InitError("Gateway rejected the init request (NACK)", status_code=502)


def get_init_result(*, transaction_id: str) -> dict | None:
    """Returns the current initialization outcome, or None if no such search
    session exists. Both fields are None while on_init hasn't arrived yet — a
    normal, honest in-progress state, not an error, same discipline as
    get_selection_result."""
    try:
        session = SearchSession.objects.get(transaction_id=transaction_id)
    except SearchSession.DoesNotExist:
        return None
    return {
        "transaction_id": session.transaction_id,
        "init_order": session.init_order,
        "init_error": session.init_error,
    }


def validate_and_ack_on_init(
    *, payload: dict, authorization_header: str, gateway_authorization_header: str, body: bytes
) -> tuple[dict, int]:
    """Synchronous half of /on_init: verifies the BPP and the forwarding Gateway,
    returns the ACK/NACK envelope. Does NOT record the result — that's
    record_on_init_result's job, called after this returns 200."""
    try:
        context = payload["context"]
        validate_context(context)
    except (KeyError, PayloadValidationError) as exc:
        return (
            build_nack_response(
                context=payload.get("context", {}),
                error={"code": "INIT_ERROR", "message": f"Invalid context: {exc}"},
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
                context=context, error={"code": "INIT_ERROR", "message": str(exc)}
            ),
            401,
        )

    return build_ack_response(context=context), 200


def record_on_init_result(*, payload: dict) -> None:
    """Records the real initialization outcome (a real Order+Quotation, or a real
    error like SLOT_UNAVAILABLE) against the matching SearchSession. A callback for
    a transaction_id this BAP has no record of is logged and dropped, same
    discipline as record_on_select_result."""
    context = payload["context"]
    transaction_id = context.get("transaction_id")
    try:
        session = SearchSession.objects.get(transaction_id=transaction_id)
    except SearchSession.DoesNotExist:
        logger.warning(
            "record_on_init_result: no SearchSession for transaction_id=%r, dropping",
            transaction_id,
        )
        return

    error = payload.get("error")
    if error is not None:
        session.init_error = error
        session.init_order = None
    else:
        session.init_order = payload["message"]["order"]
        session.init_error = None
    session.save(update_fields=["init_order", "init_error", "updated_at"])
