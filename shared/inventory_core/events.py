"""Event vocabulary, versioning, and idempotent processing for `shared/inventory_core`, wired to
the existing `shared/event_bus` (livetracker2.md §1.4) — that bus itself (Redis-backed queue +
DLQ, `shared/event_bus/bus.py`) is reused exactly as-is, not rebuilt or forked.

**Scope boundary, explicit** (§1.4's own instruction): this carries **internal**, cross-module
coordination within one app (e.g. BPP's Inventory module telling its own Fulfillment module a
slot was confirmed) — matching BAP's/BPP's documented "Modular Monolith + internal EDA"
architecture. It never substitutes for the **external** Beckn protocol calls between
BAP <-> Gateway <-> BPP (`search`/`select`/`init`/`confirm`/...), which stay strictly signed
HTTP request/callback per the trust layer.

**Per-entity ordering**: the underlying bus is a single Redis list (`RPUSH`/`BLPOP`), so as long
as there is exactly one consumer loop draining a given queue (this project's current design —
no multi-worker consumer pool exists for this bus anywhere in the codebase), every event is
processed in the exact order it was published, which trivially satisfies per-entity ordering
(a stronger guarantee than required). This is a real property of the current architecture, not
an assumption — see `test_events_for_same_slot_are_processed_in_publish_order` in
`BPP/backend/core/test_inventory_core_events.py`. If this bus ever grows multiple concurrent
consumers competing for the same queue, per-entity ordering would need a real per-entity
partitioning scheme at that point — a genuine future concern, not one this design silently
assumes away.
"""

import logging

from django.db import transaction

from .models import ProcessedEvent

logger = logging.getLogger(__name__)

CURRENT_EVENT_VERSION = 1


class SlotEvent:
    """Confirmed, real-protocol-aligned event vocabulary for `Slot` state changes (§1.4).
    Wired to a real trigger point now: `CREATED` (`AvailabilityCalendar.generate_slots`),
    `RESERVED` (`reservation.hold_slot`), `CONFIRMED` (`reservation.confirm_hold`), `RELEASED`
    (`reservation.release_expired_hold`). `PUBLISHED` (catalog exposure), `LOCKED` (domain-level
    combo-service locking), `CANCELLED` (provider-driven slot cancellation), and `COMPLETED`
    (fulfillment completion) have no trigger point yet — those business flows aren't built until
    Phase 2/3 — but the vocabulary is adopted now, not invented ad hoc later.
    """

    CREATED = "SlotCreated"
    PUBLISHED = "SlotPublished"
    RESERVED = "SlotReserved"
    LOCKED = "SlotLocked"
    CONFIRMED = "SlotConfirmed"
    RELEASED = "SlotReleased"
    CANCELLED = "SlotCancelled"
    COMPLETED = "SlotCompleted"


class BookingEvent:
    """`Booking`-level counterparts to `SlotEvent` — a separate stream for consumers that only
    care about the booking/order side, not the underlying slot. Wired now: `CONFIRMED`
    (`reservation.confirm_hold`), `CANCELLED` (`reservation.release_expired_hold`).
    """

    CONFIRMED = "BookingConfirmed"
    CANCELLED = "BookingCancelled"


def publish_event(bus, event_type: str, *, version: int = CURRENT_EVENT_VERSION, **data) -> str:
    """Every event published through this helper carries a `version` field in its metadata from
    day one — cheap to add now, expensive to retrofit once Healthcare/Automotive consumers exist
    in Phase 4. A thin wrapper around the real, already-proven `EventBus.publish` — that method
    itself is untouched.
    """
    return bus.publish(event_type, {"version": version, **data})


def process_event(event: dict, handler, *, supported_versions=(CURRENT_EVENT_VERSION,)) -> str:
    """Wraps `handler(event)` with idempotency (by `event_id`) and version-safety. Meant to be
    passed as the `handler` argument to `shared/event_bus.process_with_dlq` — that wrapper is
    what routes a genuinely failing (poisoned) event to the DLQ; this one handles the two
    *non-failure* outcomes that must never reach the DLQ or crash a consumer:

    - `"duplicate"` — this exact `event_id` was already processed; `handler` is skipped, not
      re-run, so at-least-once delivery produces one business effect, not two.
    - `"unsupported_version"` — fails *safely*: logs and skips, instead of crashing a consumer
      that doesn't yet understand a newer event version.

    Returns `"processed"` on a first-time, successful run. The idempotency marker and `handler`
    run in one transaction — if `handler` raises, the marker insert rolls back too, so a
    legitimate retry (e.g. after `process_with_dlq` requeues it) isn't falsely skipped.
    """
    version = (event.get("payload") or {}).get("version")
    if version not in supported_versions:
        logger.warning(
            "Skipping event %s (%s): unsupported version %r, expected one of %s",
            event.get("event_id"),
            event.get("event_type"),
            version,
            supported_versions,
        )
        return "unsupported_version"

    event_id = event["event_id"]
    with transaction.atomic():
        _, created = ProcessedEvent.objects.get_or_create(
            event_id=event_id, defaults={"event_type": event.get("event_type", "")}
        )
        if not created:
            logger.info("Skipping duplicate delivery of event %s", event_id)
            return "duplicate"
        handler(event)
    return "processed"
