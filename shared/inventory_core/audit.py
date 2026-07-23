"""Single creation chokepoint for `BookingAuditLogEntry` (livetracker2.md §3.10),
mirroring Registry's own `_log_audit()` (`registry/core/registry_service.py`) exactly —
the model lives in `models.py` (Django migration-autodiscovery requirement, same
reason `ProcessedEvent` does), this thin helper is the only place that ever calls
`BookingAuditLogEntry.objects.create()`, so every booking-lifecycle event is recorded
the same way, not ad hoc per call site.
"""

from .models import Booking, BookingAuditLogEntry


def log_booking_audit_event(
    *,
    booking: Booking | None,
    booking_id: str,
    event_type: str,
    detail: dict,
    correlation_id: str | None = None,
) -> BookingAuditLogEntry:
    return BookingAuditLogEntry.objects.create(
        booking=booking,
        booking_id_text=booking_id,
        event_type=event_type,
        detail=detail,
        correlation_id=correlation_id,
    )
