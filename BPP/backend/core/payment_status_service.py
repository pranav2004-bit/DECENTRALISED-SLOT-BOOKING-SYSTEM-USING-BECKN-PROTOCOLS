"""Real /payment_status receipt (livetracker5.md Phase 4.1) — BPP-side half of the
signed BAP->BPP payment-status notification (`BAP/backend/core/payment_service.py`'s
`notify_bpp_of_payment_status_in_background`).

Not a real Beckn protocol action (no such action exists in the spec — payment
collection/status isn't part of it), but shaped and verified exactly like one
(`context`/`Authorization` signature/ack-nack envelope), reusing the same
`beckn_transaction`/`trust` machinery every other BAP<->BPP call in this codebase
uses — deliberately not a bespoke auth scheme or a shared-Redis shortcut. See
`shared/event_bus/bus.py`'s own docstring ("internal EDA between business modules
within one app... not a distributed message broker") and `shared/inventory_core/
events.py`'s own docstring ("never substitutes for the external Beckn protocol calls
between BAP <-> Gateway <-> BPP... which stay strictly signed HTTP") for why a direct
cross-app event_bus write was considered and rejected for this — those are both
pre-existing, explicit architectural boundaries in this codebase, not ones invented
for this phase.

Mirrors `cancel_service.py`'s split: `validate_and_ack_payment_status` verifies and
ACKs synchronously; `record_payment_status` does the real (cheap — no external
network calls) work, called directly after a 200 ack, same discipline as
`record_on_cancel_result` (not backgrounded like confirm's own capacity-transition
work, which is genuinely expensive)."""

import logging

from beckn_transaction import (
    PayloadValidationError,
    build_ack_response,
    build_nack_response,
    validate_context,
)
from inventory_core.events import BookingEvent, publish_event
from inventory_core.models import Booking

from . import trust
from .events import get_event_bus

logger = logging.getLogger("bpp")

_EVENT_TYPE_FOR_STATUS = {
    "SUCCEEDED": BookingEvent.PAYMENT_SUCCEEDED,
    "REFUNDED": BookingEvent.PAYMENT_REFUNDED,
    "PARTIALLY_REFUNDED": BookingEvent.PAYMENT_REFUNDED,
}


def validate_and_ack_payment_status(
    *, payload: dict, authorization_header: str, gateway_authorization_header: str, body: bytes
) -> tuple[dict, int]:
    try:
        context = payload["context"]
        validate_context(context)
    except (KeyError, PayloadValidationError) as exc:
        return (
            build_nack_response(
                context=payload.get("context", {}),
                error={"code": "PAYMENT_STATUS_ERROR", "message": f"Invalid context: {exc}"},
            ),
            400,
        )

    try:
        # Direct BAP->BPP dispatch, no Gateway hop, same as /confirm and /cancel
        # (livetracker4.md §1.1/§1.2) — this isn't even a real Beckn action a
        # Gateway would know how to route.
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
                context=context, error={"code": "PAYMENT_STATUS_ERROR", "message": str(exc)}
            ),
            401,
        )

    try:
        status = payload["message"]["payment_status"]
    except KeyError as exc:
        return (
            build_nack_response(
                context=context,
                error={"code": "PAYMENT_STATUS_ERROR", "message": f"Missing field: {exc}"},
            ),
            400,
        )
    if status not in _EVENT_TYPE_FOR_STATUS:
        return (
            build_nack_response(
                context=context,
                error={
                    "code": "PAYMENT_STATUS_ERROR",
                    "message": f"Unrecognized payment_status: {status!r}",
                },
            ),
            400,
        )

    return build_ack_response(context=context), 200


def record_payment_status(*, payload: dict) -> None:
    """Resolves every `Booking` this transaction_id covers (a multi-resource confirm
    group — e.g. Automotive's bay+mechanic pair — shares one `holder_ref`, same
    precedent as `confirm_service.dispatch_on_confirm`'s own bulk `confirmed_total_
    value` write) and records the new payment status on all of them together, then
    publishes one `BookingEvent` per booking for the real-time dashboard broadcast
    (`core/realtime.py::broadcast_payment_status_consumer`) and audit trail
    (`inventory_core.consumers.audit_log_consumer`) to react to.

    A notification for a transaction_id with no matching booking is logged and
    dropped, same discipline as `confirm_service`/`cancel_service`'s own receipt
    handlers for an unknown transaction."""
    context = payload["context"]
    transaction_id = context["transaction_id"]
    status = payload["message"]["payment_status"]
    event_type = _EVENT_TYPE_FOR_STATUS[status]

    booking_ids = list(
        Booking.objects.filter(holder_ref=transaction_id).values_list("id", flat=True)
    )
    if not booking_ids:
        logger.warning(
            "record_payment_status: no Booking for transaction_id=%r, dropping", transaction_id
        )
        return

    Booking.objects.filter(id__in=booking_ids).update(payment_status=status)

    bus = get_event_bus()
    for booking_id in booking_ids:
        publish_event(bus, event_type, booking_id=str(booking_id), payment_status=status)
