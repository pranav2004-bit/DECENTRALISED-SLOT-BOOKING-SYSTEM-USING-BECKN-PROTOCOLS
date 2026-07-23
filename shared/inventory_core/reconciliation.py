"""Periodic reconciliation sweep for HELD bookings whose Redis TTL hold has lapsed but
nothing has opportunistically touched since (livetracker2.md §3.11).

Distinct from `confirm_hold()`'s own on-touch release (§3.11 finding 1): that fix only
recovers a slot's capacity the next time someone actually acts on the same booking (a late
confirm attempt, most plausibly). A hold nobody ever touches again — the customer just closes
the tab — would otherwise still leak the slot's capacity forever. This sweep is the periodic,
proactive complement to that on-touch fix, not a duplicate of it: both funnel through the same
single `release_expired_hold()` decision, so there is one real code path for "should this hold
be released," not two independently-maintained copies.

Deliberately a plain function, not itself a background loop — the scheduling (a real periodic
thread, not a new task-queue dependency; see `reservation.py`'s own module docstring for why no
task queue exists in this project) is wired by the calling app (BPP's `core/apps.py`), matching
the same "generic primitive in `inventory_core`, app-specific wiring at startup" split already
used for `DomainAdapter` (§1.5).
"""

import logging

from .models import Booking
from .reservation import release_expired_hold

logger = logging.getLogger(__name__)


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
