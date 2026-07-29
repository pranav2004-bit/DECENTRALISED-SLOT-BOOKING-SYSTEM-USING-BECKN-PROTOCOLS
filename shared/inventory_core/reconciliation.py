"""Periodic reconciliation sweeps for `Booking` state that would otherwise only ever
resolve as a side effect of some *other*, unrelated request touching the same row.

`sweep_expired_holds` (§3.11): HELD bookings whose Redis TTL hold has lapsed but nothing
has opportunistically touched since. Distinct from `confirm_hold()`'s own on-touch release
(§3.11 finding 1): that fix only recovers a slot's capacity the next time someone actually
acts on the same booking (a late confirm attempt, most plausibly). A hold nobody ever
touches again — the customer just closes the tab — would otherwise still leak the slot's
capacity forever. This sweep is the periodic, proactive complement to that on-touch fix,
not a duplicate of it: both funnel through the same single `release_expired_hold()`
decision, so there is one real code path for "should this hold be released," not two
independently-maintained copies.

`sweep_completed_bookings` (§4.5): the same "periodic, proactive" shape, applied to ACTIVE
bookings whose slot's fulfillment window has already ended — the real, honest trigger for
`Booking.Status.COMPLETE`, which otherwise has zero production callers (confirmed by direct
grep before this sweep was added; only referenced in the model's own state-machine tests).

Deliberately plain functions, not themselves a background loop — the scheduling (a real
periodic thread, not a new task-queue dependency; see `reservation.py`'s own module
docstring for why no task queue exists in this project) is wired by the calling app (BPP's
`core/apps.py`), matching the same "generic primitive in `inventory_core`, app-specific
wiring at startup" split already used for `DomainAdapter` (§1.5).
"""

import logging

from .models import Booking
from .reservation import complete_active_booking, release_expired_hold

logger = logging.getLogger(__name__)


def sweep_completed_bookings(*, event_bus=None) -> int:
    """Finds every still-`ACTIVE` `Booking` and completes the ones whose `Slot.end_time`
    has already passed, via the same `complete_active_booking()` a future on-touch path
    would use — one real decision, not a second copy of it (livetracker2.md §4.5's
    prerequisite: rating/support both need a genuinely completed booking to test against,
    and nothing else in this codebase reaches `Booking.Status.COMPLETE`). An `ACTIVE`
    booking whose slot hasn't ended yet is left untouched. Returns the number of bookings
    actually completed. Never raises for an individual booking's completion failing —
    logs and keeps sweeping the rest, matching `sweep_expired_holds`'s own contract."""
    completed = 0
    active_ids = list(Booking.objects.filter(status=Booking.Status.ACTIVE).values_list("id", flat=True))
    for booking_id in active_ids:
        try:
            if complete_active_booking(booking_id, event_bus=event_bus):
                completed += 1
        except Exception:
            logger.exception("sweep_completed_bookings: failed to complete booking %s", booking_id)
    return completed


def sweep_expired_holds(*, redis_client, event_bus=None) -> int:
    """Finds every still-`HELD` `Booking` and releases the ones whose Redis TTL has actually
    lapsed, via the same `release_expired_hold()` the on-touch path uses — one real decision,
    not a second copy of it. A booking whose hold is still genuinely active is left untouched
    (`release_expired_hold` itself is the source of truth for that check). Returns the number
    of bookings actually released. Never raises for an individual booking's release failing —
    logs and keeps sweeping the rest, so one bad row can't silently stop the whole sweep."""
    released = 0
    held_ids = list(Booking.objects.filter(status=Booking.Status.HELD).values_list("id", flat=True))
    for booking_id in held_ids:
        try:
            if release_expired_hold(booking_id, redis_client=redis_client, event_bus=event_bus):
                released += 1
        except Exception:
            logger.exception("sweep_expired_holds: failed to release booking %s", booking_id)
    return released
