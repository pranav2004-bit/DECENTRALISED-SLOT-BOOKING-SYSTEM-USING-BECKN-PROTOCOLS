"""Real /confirm and /on_confirm business logic (livetracker2.md Phase 3.4). Same
synchronous-ACK/background-dispatch split as §3.1/§3.2/§3.3, for the same reason
(protocol_compliance_notes_v1.1.md §H.1: async is mandatory).

Wire shape confirmed before implementing (§J.1, verified during §3.3's research
pass): both /confirm's request and /on_confirm's response carry `message.order`,
the same pattern already proven for /select/on_select and /init/on_init.

Reuses /init's exact booking-resolution shape (`fulfillments[0].id` -> real
`Booking`, same IDOR-safe `holder_ref` check, §J's own "Gap closed" note) since
the same risk applies equally here — a `/confirm` naively trusting an arbitrary
`booking_id` would let one transaction confirm (and be billed for) a slot it
never actually held. The real state transition itself (`HELD` -> `ACTIVE`) is
delegated entirely to `inventory_core.reservation.confirm_hold()`, which is both
idempotent and race-safe by construction (protocol_compliance_notes_v1.1.md §K) —
this module never needs its own locking.
"""

import json
import logging
import threading

import redis
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
from inventory_core.models import Booking
from inventory_core.reservation import confirm_hold

from . import registry_client, trust
from .crypto import sign_outbound_request
from .events import get_event_bus
from .participant_keys import get_signing_keys

logger = logging.getLogger("bpp")


def _redis_client() -> redis.Redis:
    return redis.Redis.from_url(settings.REDIS_URL)


def _extract_booking_id(order: dict) -> str:
    """Pulls the real Booking id out of the order draft's fulfillment. Raises
    ValueError with a human-readable message on any missing/malformed piece — the
    caller turns that into a real NACK, never a raw 500."""
    try:
        return order["fulfillments"][0]["id"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("message.order.fulfillments[0].id is required") from exc


def validate_and_ack_confirm(
    *, payload: dict, authorization_header: str, gateway_authorization_header: str, body: bytes
) -> tuple[dict, int]:
    """Synchronous half of /confirm: verifies the BAP and the forwarding Gateway,
    and that the order draft is at least well-formed enough to resolve later. Does
    NOT attempt the real confirmation — that's dispatch_on_confirm's job, fired in
    the background after this returns, matching init's own split."""
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
        trust.verify_bap_and_gateway(
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

    try:
        order = payload["message"]["order"]
        _extract_booking_id(order)
    except (KeyError, ValueError) as exc:
        return (
            build_nack_response(
                context=context, error={"code": "CONFIRM_ERROR", "message": str(exc)}
            ),
            400,
        )

    return build_ack_response(context=context), 200


def _on_confirm_context(*, request_context: dict) -> dict:
    return build_context(
        domain=request_context["domain"],
        action="on_confirm",
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


def dispatch_on_confirm(*, payload: dict) -> None:
    """Resolves the real Booking referenced by `fulfillments[0].id`, verifies it's
    held by *this* transaction (same IDOR-safe check as §3.3's /init), then
    delegates the real `HELD` -> `ACTIVE` transition + capacity commit to
    `confirm_hold()` — idempotent and race-safe by construction, so a genuine
    double-submit here never double-confirms or double-fires `BookingConfirmed`.
    Sends the resulting /on_confirm (a real confirmed Order, or a real error) to
    Gateway. Fire-and-forget: failures are logged, not raised, same discipline as
    dispatch_on_init."""
    context = payload["context"]
    order = payload["message"]["order"]
    booking_id = _extract_booking_id(order)

    error = None
    resolved_order = None

    try:
        booking = Booking.objects.select_related("slot__resource").get(pk=booking_id)
    except (Booking.DoesNotExist, ValueError):
        booking = None

    if booking is None:
        error = {"code": "SLOT_UNAVAILABLE", "message": "No matching booking for this order"}
    elif booking.holder_ref != context["transaction_id"]:
        # Never distinguish "wrong owner" from "genuinely gone" in the response —
        # same IDOR-safety reasoning as /init (protocol_compliance_notes_v1.1.md §J).
        error = {"code": "SLOT_UNAVAILABLE", "message": "No matching booking for this order"}
    else:
        try:
            confirmed_booking = confirm_hold(
                booking.id, redis_client=_redis_client(), event_bus=get_event_bus()
            )
        except ValidationError:
            error = {
                "code": "SLOT_UNAVAILABLE",
                "message": "This booking's hold is no longer active",
            }
        else:
            resource = confirmed_booking.slot.resource
            resolved_order = {
                "id": str(confirmed_booking.id),
                "status": confirmed_booking.status,
                "provider": {"id": resource.owner_ref},
                "items": [{"id": str(resource.id)}],
                "fulfillments": [
                    {
                        "id": str(confirmed_booking.id),
                        "stops": [
                            {
                                "type": "start",
                                "time": {
                                    "timestamp": confirmed_booking.slot.start_time.isoformat()
                                },
                            }
                        ],
                    }
                ],
                "quote": {
                    "price": {
                        "currency": resource.price_currency,
                        "value": str(resource.price_value),
                    },
                    "breakup": [
                        {
                            "item": {"id": str(resource.id)},
                            "title": resource.name,
                            "price": {
                                "currency": resource.price_currency,
                                "value": str(resource.price_value),
                            },
                        }
                    ],
                },
                "payments": [{"status": "NOT-PAID"}],
            }

    on_confirm_context = _on_confirm_context(request_context=context)
    on_confirm_message: dict = {
        "order": resolved_order if resolved_order is not None else order
    }
    on_confirm_payload = {"context": on_confirm_context, "message": on_confirm_message}
    if error is not None:
        on_confirm_payload["error"] = error
    body = json.dumps(on_confirm_payload).encode()

    _, signing_priv = get_signing_keys()
    auth_header = sign_outbound_request(
        body=body,
        subscriber_id=settings.SUBSCRIBER_ID,
        unique_key_id=settings.UNIQUE_KEY_ID,
        signing_private_key_b64=signing_priv,
    )

    gateway_on_confirm_url = settings.GATEWAY_BASE_URL.rstrip("/") + "/on_confirm"
    try:
        response = registry_client.get_gateway_client().post(
            gateway_on_confirm_url,
            data=body,
            headers={"Content-Type": "application/json", "Authorization": auth_header},
        )
        response.raise_for_status()
    except Exception:
        logger.exception(
            "dispatch_on_confirm: sending on_confirm to %s failed", gateway_on_confirm_url
        )


def dispatch_on_confirm_in_background(*, payload: dict) -> None:
    """Fires dispatch_on_confirm on a daemon thread — the actual fire-and-forget
    entry point the view uses. Kept separate so tests can call dispatch_on_confirm
    directly and synchronously without racing a thread."""
    thread = threading.Thread(
        target=dispatch_on_confirm, kwargs={"payload": payload}, daemon=True
    )
    thread.start()
