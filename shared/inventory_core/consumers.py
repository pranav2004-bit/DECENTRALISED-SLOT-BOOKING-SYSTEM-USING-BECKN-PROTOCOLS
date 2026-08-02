"""Real, queue-driven consumer handlers for `shared/inventory_core`'s own
`BookingEvent`/`SlotEvent` stream (livetracker4.md §2.1) — the audit-log write
`reservation.py`'s own lifecycle functions used to call inline, now the sole
writer of `BookingAuditLogEntry`, triggered by a worker consuming the event off
the bus instead of a direct function call in the same request/thread.

**Cutover completed 2026-08-02**, after `reservation.py`'s own inline
`log_booking_audit_event` calls were removed: this consumer is now the only
thing that writes a `BookingAuditLogEntry`, so it must actually be running (via
`run_event_worker`, deployed as its own `bpp-worker` service in
`docker-compose.yml`) for audit logging to happen at all. See
`BPP/backend/core/events_worker.py` for where this is wired into that process.
"""

from .audit import log_booking_audit_event
from .events import BookingEvent
from .models import Booking


def audit_log_consumer(event: dict) -> None:
    """Handles every real `BookingEvent.*` this project's own event vocabulary
    defines, recording the identical `BookingAuditLogEntry` `reservation.py`'s own
    inline call already writes for the same transition. Reads `booking_id`/
    `correlation_id`/`reason`/`slot_id`/`old_slot_id`/`new_slot_id` straight from
    the event's own payload (`reservation.py`'s own producer-side enrichment,
    §2.1) rather than a live Python object — this consumer runs detached from the
    original request/thread that published the event, so a DB refetch (for the
    `booking` FK) is the only real object it has, and even that can legitimately
    be `None` (e.g. a very old event replayed long after the booking itself was
    deleted — `BookingAuditLogEntry.booking` is nullable for exactly this)."""
    payload = event.get("payload") or {}
    event_type = event.get("event_type")
    booking_id = payload.get("booking_id")
    if not booking_id:
        return

    if event_type == BookingEvent.CONFIRMED:
        detail = {"slot_id": payload.get("slot_id")}
    elif event_type == BookingEvent.CANCELLED:
        detail = {"reason": payload.get("reason"), "slot_id": payload.get("slot_id")}
    elif event_type == BookingEvent.COMPLETED:
        detail = {"reason": payload.get("reason"), "slot_id": payload.get("slot_id")}
    elif event_type == BookingEvent.RESCHEDULED:
        detail = {
            "old_slot_id": payload.get("old_slot_id"),
            "new_slot_id": payload.get("new_slot_id"),
        }
    else:
        return  # a SlotEvent, or a BookingEvent this consumer doesn't record — not an error

    log_booking_audit_event(
        booking=Booking.objects.filter(pk=booking_id).first(),
        booking_id=str(booking_id),
        event_type=event_type,
        detail=detail,
        correlation_id=payload.get("correlation_id"),
    )
