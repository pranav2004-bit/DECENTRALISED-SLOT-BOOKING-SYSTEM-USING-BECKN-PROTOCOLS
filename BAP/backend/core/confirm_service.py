"""Real /confirm trigger and /on_confirm receipt (livetracker2.md Phase 3.4). Mirrors
init_service.py's split exactly:
- `trigger_confirm`/`get_confirm_result`: customer/web-facing, non-Beckn-protocol.
- `validate_and_ack_on_confirm`/`record_on_confirm_result`: the real Beckn wire endpoint.

Wire shape confirmed before implementing (protocol_compliance_notes_v1.1.md §J.1):
both /confirm's request and /on_confirm's response carry `message.order`, the same
pattern already proven for /select/on_select and /init/on_init.
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
from .session_authz import SessionAccessError, resolve_owned_session

logger = logging.getLogger("bap")


class ConfirmError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def trigger_confirm(*, transaction_id: str, customer=None) -> None:
    """Customer-facing confirmation trigger — builds and sends the real signed
    Beckn /confirm to Gateway, targeting the same BPP a prior successful /select
    already resolved to. Requires a real successful /init to have happened first
    (the customer must have seen the real itemized Quotation before confirming —
    the same real Beckn UX ordering this whole flow already follows). Resends only
    `provider`/`items`/`fulfillments` from the recorded init_order, deliberately
    dropping its `quote` — the BPP recomputes pricing fresh from live state one
    final time, same Source-of-Truth principle already established at Selection
    and Initialization. Clears any previous confirm result first, so a re-confirm
    doesn't briefly show stale data while the new one is in flight.

    `customer` (§3.7): IDOR protection — raises `ConfirmError(401/403)` if this
    transaction belongs to a different customer than the caller."""
    try:
        session = resolve_owned_session(transaction_id=transaction_id, requesting_customer=customer)
    except SessionAccessError as exc:
        raise ConfirmError(exc.message, status_code=exc.status_code) from exc

    if not session.init_order or not session.selected_bpp_id:
        raise ConfirmError(
            "No successful initialization to confirm for this transaction", status_code=400
        )

    session.confirmed_order = None
    session.confirmed_error = None
    session.save(update_fields=["confirmed_order", "confirmed_error", "updated_at"])

    context = build_context(
        domain=session.domain,
        action="confirm",
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
        "provider": session.init_order["provider"],
        "items": session.init_order["items"],
        "fulfillments": session.init_order["fulfillments"],
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

    gateway_confirm_url = settings.GATEWAY_BASE_URL.rstrip("/") + "/confirm"
    try:
        response = registry_client.get_gateway_client().post(
            gateway_confirm_url,
            data=body,
            headers={"Content-Type": "application/json", "Authorization": auth_header},
        )
        response.raise_for_status()
    except Exception as exc:
        raise ConfirmError(f"Gateway unreachable: {exc}", status_code=502) from exc

    ack_status = response.json().get("message", {}).get("ack", {}).get("status")
    if ack_status != "ACK":
        raise ConfirmError("Gateway rejected the confirm request (NACK)", status_code=502)


def get_confirm_result(*, transaction_id: str, customer=None) -> dict | None:
    """Returns the current confirmation outcome, or None if no such search session
    exists. Both fields are None while on_confirm hasn't arrived yet — a normal,
    honest in-progress state, not an error, same discipline as get_init_result.
    `customer` (§3.7): IDOR protection, same contract."""
    try:
        session = resolve_owned_session(transaction_id=transaction_id, requesting_customer=customer)
    except SessionAccessError as exc:
        if exc.status_code == 404:
            return None
        raise ConfirmError(exc.message, status_code=exc.status_code) from exc
    return {
        "transaction_id": session.transaction_id,
        "confirmed_order": session.confirmed_order,
        "confirmed_error": session.confirmed_error,
    }


def validate_and_ack_on_confirm(
    *, payload: dict, authorization_header: str, gateway_authorization_header: str, body: bytes
) -> tuple[dict, int]:
    """Synchronous half of /on_confirm: verifies the BPP and the forwarding
    Gateway, returns the ACK/NACK envelope. Does NOT record the result — that's
    record_on_confirm_result's job, called after this returns 200."""
    try:
        context = payload["context"]
        validate_context(context)
    except (KeyError, PayloadValidationError) as exc:
        return (
            build_nack_response(
                context=payload.get("context", {}),
                error={"code": "CONFIRM_ERROR", "message": f"Invalid context: {exc}"},
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
                context=context, error={"code": "CONFIRM_ERROR", "message": str(exc)}
            ),
            401,
        )

    return build_ack_response(context=context), 200


def record_on_confirm_result(*, payload: dict) -> None:
    """Records the real confirmation outcome (a real confirmed Order, or a real
    error like SLOT_UNAVAILABLE) against the matching SearchSession. A callback for
    a transaction_id this BAP has no record of is logged and dropped, same
    discipline as record_on_init_result."""
    context = payload["context"]
    transaction_id = context.get("transaction_id")
    try:
        session = SearchSession.objects.get(transaction_id=transaction_id)
    except SearchSession.DoesNotExist:
        logger.warning(
            "record_on_confirm_result: no SearchSession for transaction_id=%r, dropping",
            transaction_id,
        )
        return

    error = payload.get("error")
    if error is not None:
        session.confirmed_error = error
        session.confirmed_order = None
    else:
        session.confirmed_order = payload["message"]["order"]
        session.confirmed_error = None
    session.save(update_fields=["confirmed_order", "confirmed_error", "updated_at"])
