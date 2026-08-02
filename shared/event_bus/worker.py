"""Generic consumer-loop runner (livetracker4.md §2.1) — the smallest correct
worker-process shape for this project's current single-process-per-app
deployment: block on the real queue, dispatch by `event_type` to every
registered handler, wrap with the existing `process_with_dlq` failure-routing
machinery (unchanged). Deliberately a plain loop, not a new framework — matches
this project's own explicit scoping decision against a heavier broker
(Celery+beat, Kafka) this project's real event volume doesn't justify.

Idempotency (`shared/inventory_core/events.py`'s `process_event`, keyed by
`ProcessedEvent`) is deliberately NOT baked in here — that mechanism is
domain-specific (BPP's own `inventory_core` model), and this module stays
domain-agnostic infra, reusable by any app's own event vocabulary. Each caller's
own dispatch table is expected to already wrap its handlers with whatever
idempotency layer is appropriate for it before registering them here.
"""

import logging

from .bus import EventBus, process_with_dlq

logger = logging.getLogger(__name__)


def run_worker(
    bus: EventBus,
    dispatch: dict[str, callable],
    *,
    poll_timeout_seconds: float = 1.0,
    should_stop=None,
    on_heartbeat=None,
) -> None:
    """Runs until `should_stop()` returns `True` (or forever, if `should_stop` is
    `None` — the real production shape: a long-lived process meant to be stopped
    by the process manager, e.g. `docker stop`/SIGTERM, not an internal flag).

    `dispatch` maps `event_type` -> exactly one handler callable. **Deliberately
    one, not a list this worker fans out to itself** — a real bug found and fixed
    at design time, before this had any real second consumer to expose it: an
    idempotency wrapper keyed by `event_id` alone (`inventory_core.events.
    process_event`, the mechanism every real caller of this function wraps its own
    handler with) can't distinguish "consumer A already ran for this event" from
    "consumer B hasn't run yet" — wrapping two handlers for the same `event_type`
    independently would silently skip the second one as a false duplicate. When an
    event genuinely needs more than one real effect (e.g. `BookingEvent.CONFIRMED`
    needs both an audit-log write and a metrics increment), the caller composes
    them into one function *before* wrapping it with idempotency and registering
    it here — see `BPP/backend/core/events_worker.py`'s own `_combined()` helper.
    An `event_type` with no registered handler is acked and skipped, not an error
    — this worker only reacts to what it's told to, the same "unrecognized is
    skipped, not fatal" discipline `process_event` already uses for versions.

    Reliable-delivery recovery (`EventBus.recover_orphaned()`, §2.1's own fix) runs
    once here, before the main loop starts — the real place a crash-and-restart
    cycle picks back up whatever its own prior run left mid-flight.

    `on_heartbeat` is optional (`None` by default) — called once per loop
    iteration (whether a real event was consumed or the poll simply timed out),
    so a caller can record real worker liveness (e.g. a short-TTL Redis key) for
    genuinely-alive-process monitoring — livetracker4.md §2.1's own gap audit
    found nothing anywhere paged/alerted if this process silently died; this
    hook is the domain-agnostic seam for whatever a specific app wires there
    (see `BPP/backend/core/management/commands/run_event_worker.py`), kept out
    of this module itself since a heartbeat's own storage/naming is an app
    concern, not infra this module should hardcode."""
    recovered = bus.recover_orphaned()
    if recovered:
        logger.warning("run_worker: recovered %d orphaned event(s) on startup", recovered)

    while should_stop is None or not should_stop():
        if on_heartbeat is not None:
            on_heartbeat()

        event = bus.consume_one(timeout_seconds=poll_timeout_seconds)
        if event is None:
            continue

        handler = dispatch.get(event.get("event_type"))
        if handler is None:
            bus.ack(event)  # not an event_type this worker cares about — done, not a failure
            continue

        success = process_with_dlq(bus, event, handler)
        if not success:
            logger.warning(
                "run_worker: event %s (%s) routed to DLQ",
                event.get("event_id"),
                event.get("event_type"),
            )
