"""Real dispatch table for BPP's own event worker (livetracker4.md §2.1) — wires
the domain-agnostic `shared/event_bus.worker.run_worker` to this app's actual
`SlotEvent`/`BookingEvent` consumers: `inventory_core`'s own `audit_log_consumer`
(domain-agnostic, lives alongside the model it writes) plus this app's own
metrics/WebSocket consumers (BPP-specific concerns, live in `core/metrics.py`/
`core/realtime.py` respectively, not `inventory_core`).

See `management/commands/run_event_worker.py` for the real, long-lived process
that actually runs this.
"""

from inventory_core.consumers import audit_log_consumer
from inventory_core.events import BookingEvent, SlotEvent, process_event

from .metrics import booking_lifecycle_consumer, hold_created_consumer
from .realtime import broadcast_slot_update_consumer


def _combined(*handlers):
    """Composes multiple real consumers for the same event_type into one function,
    wrapped with `process_event`'s idempotency check exactly once — see
    `shared/event_bus/worker.py`'s own `run_worker` docstring for why wrapping each
    handler separately would be a real bug (a false-duplicate skip), not a stricter
    idempotency guarantee."""

    def run(event):
        for handler in handlers:
            handler(event)

    return lambda event: process_event(event, run)


DISPATCH: dict[str, callable] = {
    SlotEvent.RESERVED: _combined(hold_created_consumer, broadcast_slot_update_consumer),
    SlotEvent.RELEASED: _combined(broadcast_slot_update_consumer),
    SlotEvent.CONFIRMED: _combined(broadcast_slot_update_consumer),
    SlotEvent.RESCHEDULED: _combined(broadcast_slot_update_consumer),
    SlotEvent.COMPLETED: _combined(broadcast_slot_update_consumer),
    BookingEvent.CONFIRMED: _combined(audit_log_consumer, booking_lifecycle_consumer),
    BookingEvent.CANCELLED: _combined(audit_log_consumer, booking_lifecycle_consumer),
    BookingEvent.COMPLETED: _combined(audit_log_consumer),
    BookingEvent.RESCHEDULED: _combined(audit_log_consumer),
}
