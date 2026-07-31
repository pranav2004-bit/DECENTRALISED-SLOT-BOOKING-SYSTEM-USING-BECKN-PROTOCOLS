"""Real /cancel and /on_cancel business logic (livetracker2.md Phase 3.5). Same
synchronous-ACK/background-dispatch split as every other action.

Wire shape confirmed before implementing (§L.2): /cancel's REQUEST carries
`message.order_id` + `cancellation_reason_id` + `descriptor` — not a full
`Order`, unlike /select/init/confirm/update. `cancellation_reason_id` is
accepted as an optional free-form string (this project has no real
cancellation-reason catalog — that would be genuinely separate, unrequested
scope); it's echoed back on the cancelled Order's `cancellation` info if
supplied, never validated against a controlled vocabulary.

Scoped to already-`ACTIVE` (confirmed) bookings only (§3.5's own explicit
decision, §L) — `inventory_core.reservation.cancel_booking()` enforces this and
is race-safe by the same `select_for_update()` discipline as `confirm_hold`.
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
from django_observability.context import correlation_id_var
from inventory_core.models import Booking
from inventory_core.reservation import cancel_booking, find_group_bookings

from . import registry_client, trust
from .crypto import sign_outbound_request
from .events import get_event_bus
from .metrics import record_booking_cancelled
from .participant_keys import get_signing_keys
from .realtime import broadcast_slot_update

logger = logging.getLogger("bpp")


def validate_and_ack_cancel(
    *, payload: dict, authorization_header: str, gateway_authorization_header: str, body: bytes
) -> tuple[dict, int]:
    """Synchronous half of /cancel: verifies the BAP and the forwarding Gateway,
    and that `message.order_id` is present. Does NOT attempt the real
    cancellation — that's dispatch_on_cancel's job, fired in the background
    after this returns."""
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
        # livetracker4.md §1.2: /cancel now arrives directly from BAP, no Gateway
        # hop — require_gateway=False (trust.py's own docstring explains why this
        # only makes the Gateway signature optional, not weaker when present).
        trust.verify_bap_and_gateway(
            context=context,
            authorization_header=authorization_header,
            gateway_authorization_header=gateway_authorization_header,
            body=body,
            require_gateway=False,
        )
    except trust.TrustEstablishmentError as exc:
        return (
            build_nack_response(
                context=context, error={"code": "CANCEL_ERROR", "message": str(exc)}
            ),
            401,
        )

    if not payload.get("message", {}).get("order_id"):
        return (
            build_nack_response(
                context=context,
                error={"code": "CANCEL_ERROR", "message": "message.order_id is required"},
            ),
            400,
        )

    return build_ack_response(context=context), 200


def _on_cancel_context(*, request_context: dict) -> dict:
    return build_context(
        domain=request_context["domain"],
        action="on_cancel",
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


def dispatch_on_cancel(*, payload: dict, correlation_id: str | None = None) -> None:
    """Resolves the real Booking referenced by `message.order_id`, verifies it's
    held by *this* transaction, then delegates the real `ACTIVE` -> `CANCELLED`
    transition + capacity release to `cancel_booking()`. Sends the resulting
    /on_cancel (a real cancelled Order, or a real error) to Gateway.
    Fire-and-forget: failures are logged, not raised.

    `correlation_id` (§3.10): same explicit-threading pattern as
    `confirm_service.dispatch_on_confirm`."""
    context = payload["context"]
    order_id = payload["message"]["order_id"]

    error = None
    resolved_order = None

    try:
        booking = Booking.objects.select_related("slot__resource").get(pk=order_id)
    except (Booking.DoesNotExist, ValueError):
        booking = None

    if booking is None:
        error = {"code": "SLOT_UNAVAILABLE", "message": "No matching booking for this order"}
    elif booking.holder_ref != context["transaction_id"]:
        error = {"code": "SLOT_UNAVAILABLE", "message": "No matching booking for this order"}
    else:
        # §4.2: cancel every booking in the group together (Automotive's bay+mechanic
        # pair) — `find_group_bookings` returns just `[booking]` unchanged for every
        # domain before this phase, so this is a no-op widening for them, not a
        # behavior change.
        group = find_group_bookings(booking)
        try:
            cancelled_bookings = [
                cancel_booking(b.id, event_bus=get_event_bus(), correlation_id=correlation_id)
                for b in group
            ]
        except ValidationError:
            error = {
                "code": "SLOT_UNAVAILABLE",
                "message": "This booking cannot be cancelled (not currently confirmed)",
            }
        else:
            record_booking_cancelled()
            cancelled_booking = next(b for b in cancelled_bookings if b.id == booking.id)
            resources = [b.slot.resource for b in cancelled_bookings]
            # Phase 4.4 (livetracker2.md §4.4): every freed slot in the group just went
            # back to AVAILABLE — broadcast each to its own resource's live dashboard.
            for b in cancelled_bookings:
                broadcast_slot_update(b.slot.resource_id, b.slot)
            resolved_order = {
                "id": str(cancelled_booking.id),
                "status": cancelled_booking.status,
                "provider": {"id": resources[0].owner_ref},
                "items": [{"id": str(r.id)} for r in resources],
                "fulfillments": [{"id": str(b.id)} for b in cancelled_bookings],
            }

    on_cancel_context = _on_cancel_context(request_context=context)
    on_cancel_message: dict = {
        "order": resolved_order if resolved_order is not None else {"id": order_id}
    }
    on_cancel_payload = {"context": on_cancel_context, "message": on_cancel_message}
    if error is not None:
        on_cancel_payload["error"] = error
    body = json.dumps(on_cancel_payload).encode()

    _, signing_priv = get_signing_keys()
    auth_header = sign_outbound_request(
        body=body,
        subscriber_id=settings.SUBSCRIBER_ID,
        unique_key_id=settings.UNIQUE_KEY_ID,
        signing_private_key_b64=signing_priv,
    )

    # livetracker4.md §1.2: sent directly to the calling BAP's own subscriber_url
    # (already known from the request's own context) instead of Gateway's relay —
    # /cancel is one of the 9 post-search actions the real protocol never routes
    # through Gateway once discovery has happened.
    bap_on_cancel_url = context["bap_uri"].rstrip("/") + "/on_cancel"
    try:
        response = registry_client.get_bap_client(context["bap_id"]).post(
            bap_on_cancel_url,
            data=body,
            headers={"Content-Type": "application/json", "Authorization": auth_header},
        )
        response.raise_for_status()
    except Exception:
        logger.exception(
            "dispatch_on_cancel: sending on_cancel to %s failed", bap_on_cancel_url
        )


def dispatch_on_cancel_in_background(*, payload: dict) -> None:
    """Fires dispatch_on_cancel on a daemon thread — the actual fire-and-forget
    entry point the view uses. Kept separate so tests can call dispatch_on_cancel
    directly and synchronously without racing a thread.

    Captures `correlation_id_var` here, in the real request-handling thread,
    before spawning the background thread (§3.10) — same pattern as
    `confirm_service.dispatch_on_confirm_in_background`."""
    correlation_id = correlation_id_var.get()
    thread = threading.Thread(
        target=dispatch_on_cancel,
        kwargs={"payload": payload, "correlation_id": correlation_id},
        daemon=True,
    )
    thread.start()
