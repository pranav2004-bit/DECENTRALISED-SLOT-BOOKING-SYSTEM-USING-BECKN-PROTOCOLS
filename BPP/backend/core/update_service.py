"""Real /update and /on_update business logic (livetracker2.md Phase 3.5) — a
reschedule: adjusts an already-`ACTIVE` booking's slot, not a general Order
field editor. Same synchronous-ACK/background-dispatch split as every other
action.

Wire shape confirmed before implementing (§L.3): /update's REQUEST DOES carry a
full `message.order` (like /select/init/confirm), plus `update_target` — a
free-form comma-separated string (e.g. `"item,billing,fulfillment"`), not a
fixed enum. This project's reschedule-only flow always sets/expects
`"fulfillment"`.

Scoped to already-`ACTIVE` bookings only (§3.5's own explicit decision, §L) —
`inventory_core.reservation.reschedule_active_booking()` enforces this, is
race-safe by the same `select_for_update()` discipline as `confirm_hold`, and
locks both the old and new slot rows in a deterministic order to prevent a real
deadlock between two concurrent reschedules swapping the same two slots.
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
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from inventory_core.models import Booking, Slot
from inventory_core.reservation import reschedule_active_booking

from . import registry_client, trust
from .crypto import sign_outbound_request
from .events import get_event_bus
from .participant_keys import get_signing_keys

logger = logging.getLogger("bpp")


def _extract_booking_id(order: dict) -> str:
    try:
        return order["fulfillments"][0]["id"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("message.order.fulfillments[0].id is required") from exc


def _extract_requested_timestamp(order: dict) -> str:
    try:
        stops = order["fulfillments"][0]["stops"]
        start_stop = next(s for s in stops if s.get("type") == "start")
        return start_stop["time"]["timestamp"]
    except (KeyError, IndexError, TypeError, StopIteration) as exc:
        raise ValueError(
            "message.order.fulfillments[0].stops[] must include a 'start' stop with "
            "time.timestamp"
        ) from exc


def validate_and_ack_update(
    *, payload: dict, authorization_header: str, gateway_authorization_header: str, body: bytes
) -> tuple[dict, int]:
    """Synchronous half of /update: verifies the BAP and the forwarding Gateway,
    and that the order draft is at least well-formed enough to resolve later.
    Does NOT attempt the real reschedule — that's dispatch_on_update's job,
    fired in the background after this returns."""
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
        trust.verify_bap_and_gateway(
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

    try:
        order = payload["message"]["order"]
        _extract_booking_id(order)
        _extract_requested_timestamp(order)
    except (KeyError, ValueError) as exc:
        return (
            build_nack_response(
                context=context, error={"code": "UPDATE_ERROR", "message": str(exc)}
            ),
            400,
        )

    return build_ack_response(context=context), 200


def _on_update_context(*, request_context: dict) -> dict:
    return build_context(
        domain=request_context["domain"],
        action="on_update",
        version=request_context["version"],
        bap_id=request_context["bap_id"],
        bap_uri=request_context["bap_uri"],
        bpp_id=settings.SUBSCRIBER_ID,
        bpp_uri=settings.SUBSCRIBER_URL,
        transaction_id=request_context["transaction_id"],
        message_id=request_context["message_id"],
        location=request_context["location"],
        timestamp=timezone.now().isoformat(),
    )


def dispatch_on_update(*, payload: dict) -> None:
    """Resolves the real Booking referenced by `fulfillments[0].id`, verifies
    it's held by *this* transaction, resolves the newly-requested time to a real
    `Slot` on the SAME resource (Source-of-Truth rule, same principle §3.2
    established), and delegates the actual slot move to
    `reschedule_active_booking()`. Sends the resulting /on_update (a real
    rescheduled Order, or a real error) to Gateway. Fire-and-forget: failures
    are logged, not raised."""
    context = payload["context"]
    order = payload["message"]["order"]
    booking_id = _extract_booking_id(order)
    requested_timestamp = _extract_requested_timestamp(order)

    error = None
    resolved_order = None

    try:
        booking = Booking.objects.select_related("slot__resource").get(pk=booking_id)
    except (Booking.DoesNotExist, ValueError):
        booking = None

    if booking is None:
        error = {"code": "SLOT_UNAVAILABLE", "message": "No matching booking for this order"}
    elif booking.holder_ref != context["transaction_id"]:
        error = {"code": "SLOT_UNAVAILABLE", "message": "No matching booking for this order"}
    else:
        requested_time = parse_datetime(requested_timestamp)
        if requested_time is None:
            error = {
                "code": "VALIDATION_ERROR",
                "message": f"Malformed time.timestamp: {requested_timestamp!r}",
            }
        else:
            resource = booking.slot.resource
            try:
                new_slot = Slot.objects.get(resource=resource, start_time=requested_time)
            except Slot.DoesNotExist:
                error = {
                    "code": "SLOT_UNAVAILABLE",
                    "message": "No matching slot for the requested time",
                }
            else:
                try:
                    rescheduled_booking = reschedule_active_booking(
                        booking.id, new_slot.id, event_bus=get_event_bus()
                    )
                except ValidationError:
                    error = {
                        "code": "SLOT_UNAVAILABLE",
                        "message": "The requested slot is no longer available",
                    }
                else:
                    resolved_order = {
                        "id": str(rescheduled_booking.id),
                        "status": rescheduled_booking.status,
                        "provider": {"id": resource.owner_ref},
                        "items": [{"id": str(resource.id)}],
                        "fulfillments": [
                            {
                                "id": str(rescheduled_booking.id),
                                "stops": [
                                    {
                                        "type": "start",
                                        "time": {"timestamp": requested_timestamp},
                                    }
                                ],
                            }
                        ],
                    }

    on_update_context = _on_update_context(request_context=context)
    on_update_message: dict = {"order": resolved_order if resolved_order is not None else order}
    on_update_payload = {"context": on_update_context, "message": on_update_message}
    if error is not None:
        on_update_payload["error"] = error
    body = json.dumps(on_update_payload).encode()

    _, signing_priv = get_signing_keys()
    auth_header = sign_outbound_request(
        body=body,
        subscriber_id=settings.SUBSCRIBER_ID,
        unique_key_id=settings.UNIQUE_KEY_ID,
        signing_private_key_b64=signing_priv,
    )

    gateway_on_update_url = settings.GATEWAY_BASE_URL.rstrip("/") + "/on_update"
    try:
        response = registry_client.get_gateway_client().post(
            gateway_on_update_url,
            data=body,
            headers={"Content-Type": "application/json", "Authorization": auth_header},
        )
        response.raise_for_status()
    except Exception:
        logger.exception(
            "dispatch_on_update: sending on_update to %s failed", gateway_on_update_url
        )


def dispatch_on_update_in_background(*, payload: dict) -> None:
    """Fires dispatch_on_update on a daemon thread — the actual fire-and-forget
    entry point the view uses. Kept separate so tests can call dispatch_on_update
    directly and synchronously without racing a thread."""
    thread = threading.Thread(
        target=dispatch_on_update, kwargs={"payload": payload}, daemon=True
    )
    thread.start()
