"""Real /cancel trigger and /on_cancel receipt (livetracker2.md Phase 3.5). Mirrors
confirm_service.py's split, adapted for /cancel's real wire shape:
- `trigger_cancel`/`get_cancel_result`: customer/web-facing, non-Beckn-protocol.
- `validate_and_ack_on_cancel`/`record_on_cancel_result`: the real Beckn wire endpoint.

Wire shape confirmed before implementing (protocol_compliance_notes_v1.1.md §L.2):
/cancel's REQUEST carries `message.order_id` + `cancellation_reason_id` — not a
full `message.order`. `cancellation_reason_id` is accepted here as an optional
free-form string from the customer (no real cancellation-reason catalog exists
in this project — genuinely separate, unrequested scope).
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


class CancelError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def trigger_cancel(
    *, transaction_id: str, cancellation_reason_id: str = "", customer=None
) -> None:
    """Customer-facing cancellation trigger — builds and sends the real signed
    Beckn /cancel to Gateway, targeting the same BPP this transaction was
    confirmed with. Requires a real successful /confirm to have happened first
    — a still-HELD hold isn't a real, cancellable Order (§3.5's own explicit
    scope decision).

    `customer` (§3.7): IDOR protection — raises `CancelError(401/403)` if this
    transaction belongs to a different customer than the caller."""
    try:
        session = resolve_owned_session(transaction_id=transaction_id, requesting_customer=customer)
    except SessionAccessError as exc:
        raise CancelError(exc.message, status_code=exc.status_code) from exc

    if not session.confirmed_order or not session.selected_bpp_id:
        raise CancelError(
            "No confirmed booking to cancel for this transaction", status_code=400
        )

    session.cancelled_order = None
    session.cancelled_error = None
    session.save(update_fields=["cancelled_order", "cancelled_error", "updated_at"])

    context = build_context(
        domain=session.domain,
        action="cancel",
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
    message = {"order_id": session.confirmed_order["id"]}
    if cancellation_reason_id:
        message["cancellation_reason_id"] = cancellation_reason_id
    payload = {"context": context, "message": message}
    body = json.dumps(payload).encode()

    _, signing_priv = get_signing_keys()
    auth_header = sign_outbound_request(
        body=body,
        subscriber_id=settings.SUBSCRIBER_ID,
        unique_key_id=settings.UNIQUE_KEY_ID,
        signing_private_key_b64=signing_priv,
    )

    gateway_cancel_url = settings.GATEWAY_BASE_URL.rstrip("/") + "/cancel"
    try:
        response = registry_client.get_gateway_client().post(
            gateway_cancel_url,
            data=body,
            headers={"Content-Type": "application/json", "Authorization": auth_header},
        )
        response.raise_for_status()
    except Exception as exc:
        raise CancelError(f"Gateway unreachable: {exc}", status_code=502) from exc

    ack_status = response.json().get("message", {}).get("ack", {}).get("status")
    if ack_status != "ACK":
        raise CancelError("Gateway rejected the cancel request (NACK)", status_code=502)


def get_cancel_result(*, transaction_id: str, customer=None) -> dict | None:
    """Returns the current cancellation outcome, or None if no such search
    session exists. Both fields are None while on_cancel hasn't arrived yet — a
    normal, honest in-progress state, not an error. `customer` (§3.7): IDOR
    protection, same contract as get_confirm_result."""
    try:
        session = resolve_owned_session(transaction_id=transaction_id, requesting_customer=customer)
    except SessionAccessError as exc:
        if exc.status_code == 404:
            return None
        raise CancelError(exc.message, status_code=exc.status_code) from exc
    return {
        "transaction_id": session.transaction_id,
        "cancelled_order": session.cancelled_order,
        "cancelled_error": session.cancelled_error,
    }


def validate_and_ack_on_cancel(
    *, payload: dict, authorization_header: str, gateway_authorization_header: str, body: bytes
) -> tuple[dict, int]:
    """Synchronous half of /on_cancel: verifies the BPP and the forwarding
    Gateway, returns the ACK/NACK envelope. Does NOT record the result — that's
    record_on_cancel_result's job, called after this returns 200."""
    try:
        context = payload["context"]
        validate_context(context)
    except (KeyError, PayloadValidationError) as exc:
        return (
            build_nack_response(
                context=payload.get("context", {}),
                error={"code": "CANCEL_ERROR", "message": f"Invalid context: {exc}"},
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
                context=context, error={"code": "CANCEL_ERROR", "message": str(exc)}
            ),
            401,
        )

    return build_ack_response(context=context), 200


def record_on_cancel_result(*, payload: dict) -> None:
    """Records the real cancellation outcome against the matching SearchSession.
    A callback for a transaction_id this BAP has no record of is logged and
    dropped, same discipline as record_on_confirm_result."""
    context = payload["context"]
    transaction_id = context.get("transaction_id")
    try:
        session = SearchSession.objects.get(transaction_id=transaction_id)
    except SearchSession.DoesNotExist:
        logger.warning(
            "record_on_cancel_result: no SearchSession for transaction_id=%r, dropping",
            transaction_id,
        )
        return

    error = payload.get("error")
    if error is not None:
        session.cancelled_error = error
        session.cancelled_order = None
    else:
        session.cancelled_order = payload["message"]["order"]
        session.cancelled_error = None
    session.save(update_fields=["cancelled_order", "cancelled_error", "updated_at"])
