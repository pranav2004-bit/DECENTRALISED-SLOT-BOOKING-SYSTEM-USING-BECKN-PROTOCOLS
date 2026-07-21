"""Real /init and /on_init business logic (livetracker2.md Phase 3.3). Same
synchronous-ACK/background-dispatch split as §3.1/§3.2, for the same reason
(protocol_compliance_notes_v1.1.md §H.1: async is mandatory).

Wire shape confirmed before implementing (§J.1): both /init's request and
/on_init's response carry `message.order`, the same pattern already proven for
/select/on_select.

Unlike /select (which resolves a customer's requested item+time to a Slot for the
first time), /init resolves the real Booking directly via `fulfillments[0].id` —
already round-tripped since Selection — and revalidates it against live state
(§J's "inventory revalidation" requirement) rather than re-resolving from scratch.
Two real design decisions from that research, not repeated in the module-level
docstrings of every function below: (1) a `Booking.holder_ref` mismatch is treated
identically to an expired hold (never leaks whether a given booking_id exists to a
caller who doesn't actually own it — a real IDOR-shaped gap found via self-audit,
§J's own "Gap closed" note); (2) /init deliberately never extends the hold's TTL,
only reports its real remaining time — extending it would let a customer call
/init repeatedly to hold a slot indefinitely.
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
from django.utils import timezone
from inventory_core.models import Booking
from inventory_core.reservation import ReservationHold

from . import registry_client, trust
from .crypto import sign_outbound_request
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


def validate_and_ack_init(
    *, payload: dict, authorization_header: str, gateway_authorization_header: str, body: bytes
) -> tuple[dict, int]:
    """Synchronous half of /init: verifies the BAP and the forwarding Gateway, and
    that the order draft is at least well-formed enough to resolve later. Does NOT
    revalidate the booking — that's dispatch_on_init's job, fired in the background
    after this returns, matching select's own split."""
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
        trust.verify_bap_and_gateway(
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

    try:
        order = payload["message"]["order"]
        _extract_booking_id(order)
    except (KeyError, ValueError) as exc:
        return (
            build_nack_response(
                context=context, error={"code": "INIT_ERROR", "message": str(exc)}
            ),
            400,
        )

    return build_ack_response(context=context), 200


def _on_init_context(*, request_context: dict) -> dict:
    return build_context(
        domain=request_context["domain"],
        action="on_init",
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


def dispatch_on_init(*, payload: dict) -> None:
    """Revalidates the real Booking referenced by `fulfillments[0].id` against live
    state (Source-of-Truth rule, same principle §3.2 already established) — still
    genuinely `HELD`, its Redis hold still active, and held by *this* transaction,
    not merely an existing booking_id belonging to someone else. On success, returns
    a real `Quotation` (`price`+`breakup[]`+the hold's real remaining TTL) via
    /on_init. Fire-and-forget: failures are logged, not raised, same discipline as
    dispatch_on_select."""
    context = payload["context"]
    order = payload["message"]["order"]
    booking_id = _extract_booking_id(order)

    error = None
    resolved_order = None
    hold = ReservationHold(redis_client=_redis_client())

    try:
        booking = Booking.objects.select_related("slot__resource").get(pk=booking_id)
    except (Booking.DoesNotExist, ValueError):
        booking = None

    if booking is None:
        error = {"code": "SLOT_UNAVAILABLE", "message": "No matching booking for this order"}
    elif booking.holder_ref != context["transaction_id"]:
        # Never distinguish "wrong owner" from "genuinely gone" in the response —
        # doing so would let a caller probe for the existence of another
        # transaction's real booking_id (protocol_compliance_notes_v1.1.md §J).
        error = {"code": "SLOT_UNAVAILABLE", "message": "No matching booking for this order"}
    elif booking.status != Booking.Status.HELD:
        error = {"code": "SLOT_UNAVAILABLE", "message": "This booking's hold is no longer active"}
    else:
        remaining_ttl = hold.remaining_ttl_seconds(booking.id)
        if remaining_ttl is None:
            error = {
                "code": "SLOT_UNAVAILABLE",
                "message": "This booking's hold is no longer active",
            }
        else:
            resource = booking.slot.resource
            resolved_order = {
                "provider": {"id": resource.owner_ref},
                "items": [{"id": str(resource.id)}],
                "fulfillments": [
                    {
                        "id": str(booking.id),
                        "stops": [
                            {
                                "type": "start",
                                "time": {"timestamp": booking.slot.start_time.isoformat()},
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
                    "ttl": f"PT{int(remaining_ttl)}S",
                },
            }

    on_init_context = _on_init_context(request_context=context)
    on_init_message: dict = {"order": resolved_order if resolved_order is not None else order}
    on_init_payload = {"context": on_init_context, "message": on_init_message}
    if error is not None:
        on_init_payload["error"] = error
    body = json.dumps(on_init_payload).encode()

    _, signing_priv = get_signing_keys()
    auth_header = sign_outbound_request(
        body=body,
        subscriber_id=settings.SUBSCRIBER_ID,
        unique_key_id=settings.UNIQUE_KEY_ID,
        signing_private_key_b64=signing_priv,
    )

    gateway_on_init_url = settings.GATEWAY_BASE_URL.rstrip("/") + "/on_init"
    try:
        response = registry_client.get_gateway_client().post(
            gateway_on_init_url,
            data=body,
            headers={"Content-Type": "application/json", "Authorization": auth_header},
        )
        response.raise_for_status()
    except Exception:
        logger.exception("dispatch_on_init: sending on_init to %s failed", gateway_on_init_url)


def dispatch_on_init_in_background(*, payload: dict) -> None:
    """Fires dispatch_on_init on a daemon thread — the actual fire-and-forget entry
    point the view uses. Kept separate so tests can call dispatch_on_init directly
    and synchronously without racing a thread."""
    thread = threading.Thread(target=dispatch_on_init, kwargs={"payload": payload}, daemon=True)
    thread.start()
