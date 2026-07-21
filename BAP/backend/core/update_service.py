"""Real /update trigger and /on_update receipt (livetracker2.md Phase 3.5) — a
reschedule. Mirrors confirm_service.py's split:
- `trigger_update`/`get_update_result`: customer/web-facing, non-Beckn-protocol.
- `validate_and_ack_on_update`/`record_on_update_result`: the real Beckn wire endpoint.

Wire shape confirmed before implementing (protocol_compliance_notes_v1.1.md §L.3):
/update's REQUEST DOES carry a full `message.order`, plus `update_target` — a
free-form string this project sets to `"fulfillment"` (a reschedule only ever
changes the fulfillment's stop time, never billing/item/etc.).
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


class UpdateError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def trigger_update(*, transaction_id: str, requested_timestamp: str) -> None:
    """Customer-facing reschedule trigger — builds and sends the real signed
    Beckn /update to Gateway, targeting the same BPP this transaction was
    confirmed with, requesting the booking be moved to `requested_timestamp`.
    Requires a real successful /confirm to have happened first — rescheduling a
    still-HELD hold is already the existing re-selection behavior (§3.2), not
    this action's job (§3.5's own explicit scope decision)."""
    try:
        session = SearchSession.objects.get(transaction_id=transaction_id)
    except SearchSession.DoesNotExist:
        raise UpdateError("No such search transaction", status_code=404) from None

    if not session.confirmed_order or not session.selected_bpp_id:
        raise UpdateError(
            "No confirmed booking to reschedule for this transaction", status_code=400
        )

    session.updated_order = None
    session.updated_error = None
    session.save(update_fields=["updated_order", "updated_error", "updated_at"])

    context = build_context(
        domain=session.domain,
        action="update",
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
        "provider": session.confirmed_order["provider"],
        "items": session.confirmed_order["items"],
        "fulfillments": [
            {
                "id": session.confirmed_order["fulfillments"][0]["id"],
                "stops": [{"type": "start", "time": {"timestamp": requested_timestamp}}],
            }
        ],
    }
    payload = {
        "context": context,
        "message": {"update_target": "fulfillment", "order": order},
    }
    body = json.dumps(payload).encode()

    _, signing_priv = get_signing_keys()
    auth_header = sign_outbound_request(
        body=body,
        subscriber_id=settings.SUBSCRIBER_ID,
        unique_key_id=settings.UNIQUE_KEY_ID,
        signing_private_key_b64=signing_priv,
    )

    gateway_update_url = settings.GATEWAY_BASE_URL.rstrip("/") + "/update"
    try:
        response = registry_client.get_gateway_client().post(
            gateway_update_url,
            data=body,
            headers={"Content-Type": "application/json", "Authorization": auth_header},
        )
        response.raise_for_status()
    except Exception as exc:
        raise UpdateError(f"Gateway unreachable: {exc}", status_code=502) from exc

    ack_status = response.json().get("message", {}).get("ack", {}).get("status")
    if ack_status != "ACK":
        raise UpdateError("Gateway rejected the update request (NACK)", status_code=502)


def get_update_result(*, transaction_id: str) -> dict | None:
    """Returns the current reschedule outcome, or None if no such search session
    exists. Both fields are None while on_update hasn't arrived yet — a normal,
    honest in-progress state, not an error."""
    try:
        session = SearchSession.objects.get(transaction_id=transaction_id)
    except SearchSession.DoesNotExist:
        return None
    return {
        "transaction_id": session.transaction_id,
        "updated_order": session.updated_order,
        "updated_error": session.updated_error,
    }


def validate_and_ack_on_update(
    *, payload: dict, authorization_header: str, gateway_authorization_header: str, body: bytes
) -> tuple[dict, int]:
    """Synchronous half of /on_update: verifies the BPP and the forwarding
    Gateway, returns the ACK/NACK envelope. Does NOT record the result — that's
    record_on_update_result's job, called after this returns 200."""
    try:
        context = payload["context"]
        validate_context(context)
    except (KeyError, PayloadValidationError) as exc:
        return (
            build_nack_response(
                context=payload.get("context", {}),
                error={"code": "UPDATE_ERROR", "message": f"Invalid context: {exc}"},
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
                context=context, error={"code": "UPDATE_ERROR", "message": str(exc)}
            ),
            401,
        )

    return build_ack_response(context=context), 200


def record_on_update_result(*, payload: dict) -> None:
    """Records the real reschedule outcome against the matching SearchSession. A
    callback for a transaction_id this BAP has no record of is logged and
    dropped, same discipline as record_on_confirm_result."""
    context = payload["context"]
    transaction_id = context.get("transaction_id")
    try:
        session = SearchSession.objects.get(transaction_id=transaction_id)
    except SearchSession.DoesNotExist:
        logger.warning(
            "record_on_update_result: no SearchSession for transaction_id=%r, dropping",
            transaction_id,
        )
        return

    error = payload.get("error")
    if error is not None:
        session.updated_error = error
        session.updated_order = None
    else:
        session.updated_order = payload["message"]["order"]
        session.updated_error = None
    session.save(update_fields=["updated_order", "updated_error", "updated_at"])
