"""Real /track trigger and /on_track receipt (livetracker2.md Phase 3.5). Mirrors
confirm_service.py's split, adapted for /track's real wire shape:
- `trigger_track`/`get_track_result`: customer/web-facing, non-Beckn-protocol.
- `validate_and_ack_on_track`/`record_on_track_result`: the real Beckn wire endpoint.

Wire shape confirmed before implementing (protocol_compliance_notes_v1.1.md
§L.4/§L.5): /track's REQUEST carries `message.order_id` (+ an optional
`callback_url`, not used here). **`/on_track`'s message carries `tracking`
($ref `Tracking.yaml`), NOT an `order`** — real fulfillment-progress state is
`/status`'s job (`status_service.py`), not this one.
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


class TrackError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def trigger_track(*, transaction_id: str) -> None:
    """Customer-facing tracking trigger — builds and sends the real signed
    Beckn /track to Gateway, targeting the same BPP this transaction was
    confirmed with. Requires a real successful /confirm to have happened first."""
    try:
        session = SearchSession.objects.get(transaction_id=transaction_id)
    except SearchSession.DoesNotExist:
        raise TrackError("No such search transaction", status_code=404) from None

    if not session.confirmed_order or not session.selected_bpp_id:
        raise TrackError(
            "No confirmed booking to track for this transaction", status_code=400
        )

    session.tracking = None
    session.tracking_error = None
    session.save(update_fields=["tracking", "tracking_error", "updated_at"])

    context = build_context(
        domain=session.domain,
        action="track",
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

    gateway_track_url = settings.GATEWAY_BASE_URL.rstrip("/") + "/track"
    try:
        response = registry_client.get_client().post(
            gateway_track_url,
            data=body,
            headers={"Content-Type": "application/json", "Authorization": auth_header},
        )
        response.raise_for_status()
    except Exception as exc:
        raise TrackError(f"Gateway unreachable: {exc}", status_code=502) from exc

    ack_status = response.json().get("message", {}).get("ack", {}).get("status")
    if ack_status != "ACK":
        raise TrackError("Gateway rejected the track request (NACK)", status_code=502)


def get_track_result(*, transaction_id: str) -> dict | None:
    """Returns the current tracking outcome, or None if no such search session
    exists. Both fields are None while on_track hasn't arrived yet — a normal,
    honest in-progress state, not an error."""
    try:
        session = SearchSession.objects.get(transaction_id=transaction_id)
    except SearchSession.DoesNotExist:
        return None
    return {
        "transaction_id": session.transaction_id,
        "tracking": session.tracking,
        "tracking_error": session.tracking_error,
    }


def validate_and_ack_on_track(
    *, payload: dict, authorization_header: str, gateway_authorization_header: str, body: bytes
) -> tuple[dict, int]:
    """Synchronous half of /on_track: verifies the BPP and the forwarding
    Gateway, returns the ACK/NACK envelope. Does NOT record the result — that's
    record_on_track_result's job, called after this returns 200."""
    try:
        context = payload["context"]
        validate_context(context)
    except (KeyError, PayloadValidationError) as exc:
        return (
            build_nack_response(
                context=payload.get("context", {}),
                error={"code": "TRACK_ERROR", "message": f"Invalid context: {exc}"},
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
                context=context, error={"code": "TRACK_ERROR", "message": str(exc)}
            ),
            401,
        )

    return build_ack_response(context=context), 200


def record_on_track_result(*, payload: dict) -> None:
    """Records the real tracking outcome (`message.tracking`, not an `order`)
    against the matching SearchSession. A callback for a transaction_id this BAP
    has no record of is logged and dropped, same discipline as
    record_on_confirm_result."""
    context = payload["context"]
    transaction_id = context.get("transaction_id")
    try:
        session = SearchSession.objects.get(transaction_id=transaction_id)
    except SearchSession.DoesNotExist:
        logger.warning(
            "record_on_track_result: no SearchSession for transaction_id=%r, dropping",
            transaction_id,
        )
        return

    error = payload.get("error")
    if error is not None:
        session.tracking_error = error
        session.tracking = None
    else:
        session.tracking = payload["message"]["tracking"]
        session.tracking_error = None
    session.save(update_fields=["tracking", "tracking_error", "updated_at"])
