"""Real vendor-facing transactional email (livetracker6.md §2.3) — the business
side's own equivalent of BAP's own `notifications.py` (livetracker3.md Phase 4),
reusing that module's own message-building/send discipline (build the whole
message inside one try/except, log-not-raise, never affect the booking action
this reacts to either way).

Unlike BAP's notifications, which are called inline from `record_on_*_result`
(BAP has no real event-consumer pattern, per that module's own documented
reasoning), this one is wired into `events_worker.py`'s own `DISPATCH` table for
`BookingEvent.CONFIRMED` — the same 2026-08-02 event-driven cutover
`broadcast_order_confirmed_consumer` already follows for the identical event, not
a new, separate mechanism.
"""

import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.mail import send_mail

logger = logging.getLogger("bpp")


def _send(*, subject: str, message: str, recipient: str) -> None:
    try:
        send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, [recipient], fail_silently=False)
    except Exception:
        logger.exception("vendor_notifications: failed to send %r to %r", subject, recipient)


def notify_vendor_order_confirmed(booking) -> None:
    """`booking` is a real, fresh `Booking` row (the caller already applies
    `select_related('slot__resource')`) — the same fresh-DB-read discipline
    `broadcast_order_confirmed_consumer` already uses, not a stale event
    payload. IDOR-safe by construction, not by an explicit check: the recipient
    is always resolved from *this* booking's own resource's own `owner_ref`,
    never from caller-supplied input, so a booking can only ever notify the
    business that actually owns the resource it was made against."""
    resource = booking.slot.resource
    BusinessAccount = get_user_model()
    # `owner_ref` is an opaque reference, not a real foreign key (`Resource`'s own
    # docstring) — a malformed/non-UUID value (real production data already has at
    # least one: a "biz-debug" test resource) must fail safely here, the same
    # discipline `core/consumers.py`'s own resource/access lookups already use for
    # exactly this class of input, not let the ORM's own ValidationError escape
    # uncaught into `process_event()`'s single `transaction.atomic()` block and roll
    # back this same event's own audit-log write too.
    try:
        owner = BusinessAccount.objects.filter(id=resource.owner_ref).first()
    except (DjangoValidationError, ValueError):
        return
    # Same ambiguous-single-field heuristic already used BAP-side (`support_service.py`,
    # `notifications.py`) — `contact` may be a phone number, not an email.
    if owner is None or "@" not in owner.contact:
        return
    try:
        subject = "You have a new confirmed order"
        message = (
            f"A new booking was confirmed for {resource.name}.\n"
            f"Time: {booking.slot.start_time.isoformat()}\n"
            f"Booking reference: {booking.holder_ref}"
        )
    except Exception:
        logger.exception(
            "vendor_notifications: failed to build order-confirmed email for resource %r",
            resource.id,
        )
        return
    _send(subject=subject, message=message, recipient=owner.contact)


def notify_vendor_order_confirmed_consumer(event: dict) -> None:
    from inventory_core.models import Booking

    payload = event.get("payload") or {}
    booking_id = payload.get("booking_id")
    if not booking_id:
        return
    booking = Booking.objects.select_related("slot__resource").filter(pk=booking_id).first()
    if booking is None:
        return
    notify_vendor_order_confirmed(booking)
