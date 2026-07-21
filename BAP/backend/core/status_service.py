"""Real /status trigger and /on_status receipt (livetracker2.md Phase 3.5). Mirrors
confirm_service.py's split, adapted for /status's real wire shape:
- `trigger_status`/`get_status_result`: customer/web-facing, non-Beckn-protocol.
- `validate_and_ack_on_status`/`record_on_status_result`: the real Beckn wire endpoint.

Wire shape confirmed before implementing (protocol_compliance_notes_v1.1.md §L.1):
unlike /select/init/confirm, /status's REQUEST carries only `message.order_id` (a
bare id), not a full `message.order` — /on_status's request is the one that
carries the full order.
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


class StatusError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def trigger_status(*, transaction_id: str) -> None:
    """Customer-facing status trigger — builds and sends the real signed Beckn
    /status to Gateway, targeting the same BPP this transaction was confirmed
    with. Requires a real successful /confirm to have happened first — there's
    no real, permanent Order to look up the status of before that."""
    try:
        session = SearchSession.objects.get(transaction_id=transaction_id)
    except SearchSession.DoesNotExist:
        raise StatusError("No such search transaction", status_code=404) from None

    if not session.confirmed_order or not session.selected_bpp_id:
        raise StatusError(
            "No confirmed booking to check the status of for this transaction", status_code=400
        )

    session.status_order = None
    session.status_error = None
    session.save(update_fields=["status_order", "status_error", "updated_at"])

    context = build_context(
        domain=session.domain,
        action="status",
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
    payload = {"context": context, "message": {"order_id": session.confirmed_order["id"]}}
    body = json.dumps(payload).encode()

    _, signing_priv = get_signing_keys()
    auth_header = sign_outbound_request(
        body=body,
        subscriber_id=settings.SUBSCRIBER_ID,
        unique_key_id=settings.UNIQUE_KEY_ID,
        signing_private_key_b64=signing_priv,
    )

    gateway_status_url = settings.GATEWAY_BASE_URL.rstrip("/") + "/status"
    try:
        response = registry_client.get_client().post(
            gateway_status_url,
            data=body,
            headers={"Content-Type": "application/json", "Authorization": auth_header},
        )
        response.raise_for_status()
    except Exception as exc:
        raise StatusError(f"Gateway unreachable: {exc}", status_code=502) from exc

    ack_status = response.json().get("message", {}).get("ack", {}).get("status")
    if ack_status != "ACK":
        raise StatusError("Gateway rejected the status request (NACK)", status_code=502)


def get_status_result(*, transaction_id: str) -> dict | None:
    """Returns the current status-lookup outcome, or None if no such search
    session exists. Both fields are None while on_status hasn't arrived yet — a
    normal, honest in-progress state, not an error."""
    try:
        session = SearchSession.objects.get(transaction_id=transaction_id)
    except SearchSession.DoesNotExist:
        return None
    return {
        "transaction_id": session.transaction_id,
        "status_order": session.status_order,
        "status_error": session.status_error,
    }


def validate_and_ack_on_status(
    *, payload: dict, authorization_header: str, gateway_authorization_header: str, body: bytes
) -> tuple[dict, int]:
    """Synchronous half of /on_status: verifies the BPP and the forwarding
    Gateway, returns the ACK/NACK envelope. Does NOT record the result — that's
    record_on_status_result's job, called after this returns 200."""
    try:
        context = payload["context"]
        validate_context(context)
    except (KeyError, PayloadValidationError) as exc:
        return (
            build_nack_response(
                context=payload.get("context", {}),
                error={"code": "STATUS_ERROR", "message": f"Invalid context: {exc}"},
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
                context=context, error={"code": "STATUS_ERROR", "message": str(exc)}
            ),
            401,
        )

    return build_ack_response(context=context), 200


def record_on_status_result(*, payload: dict) -> None:
    """Records the real status-lookup outcome against the matching
    SearchSession. A callback for a transaction_id this BAP has no record of is
    logged and dropped, same discipline as record_on_confirm_result."""
    context = payload["context"]
    transaction_id = context.get("transaction_id")
    try:
        session = SearchSession.objects.get(transaction_id=transaction_id)
    except SearchSession.DoesNotExist:
        logger.warning(
            "record_on_status_result: no SearchSession for transaction_id=%r, dropping",
            transaction_id,
        )
        return

    error = payload.get("error")
    if error is not None:
        session.status_error = error
        session.status_order = None
    else:
        session.status_order = payload["message"]["order"]
        session.status_error = None
    session.save(update_fields=["status_order", "status_error", "updated_at"])
