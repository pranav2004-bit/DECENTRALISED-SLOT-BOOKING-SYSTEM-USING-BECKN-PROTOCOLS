"""BPP-specific business metrics (livetracker2.md §3.10) — real booking-lifecycle
counters, the source data for the booking-success-rate/cancellation-rate/
hold-expiry-rate Grafana panels. Redis-backed via `django_observability.metrics`
(BPP's `settings.py` already configures `django.core.cache` as
`django_redis.cache.RedisCache`) — correct under real concurrent multi-process
writers, unlike Registry's own in-process `core/metrics.py` (that per-worker
limitation is explicitly not retroactively fixed here, see §3.10's tracker note).

**Cutover completed 2026-08-02 (livetracker4.md §2.1):** these counters are now
incremented exclusively by the queue-driven consumers below (`hold_created_consumer`/
`booking_lifecycle_consumer`), run by the real `run_event_worker` process (its own
`bpp-worker` service in `docker-compose.yml`) — the inline calls this module's
functions used to be called from directly, in `select_service.py`/`confirm_service.py`/
`cancel_service.py`, have been removed. `hold_expired` is counted from the
`BookingEvent.CANCELLED` (`reason="hold_expired"`) event `release_expired_hold()`
publishes when a hold's expiry first becomes observable — via `confirm_hold()`'s own
internal call to it on a customer's real `/confirm` arriving after the TTL lapsed, or
via `sweep_expired_holds()`'s scheduled sweep catching a hold nobody ever touched
again — not the currently-dead `release_expired_hold()` docstring's original design
intent alone. A multi-resource booking (e.g. Automotive's bay+mechanic pair) counts as
N holds/confirms/cancellations, one per real `Booking` row — a deliberate
metric-semantics decision made as part of this cutover, not the prior "one count per
customer action" behavior these functions had when called inline.
"""

from django_observability.metrics import increment_counter, render_counter_family

_HOLD_CREATED = "bpp:metrics:hold_created"
_HOLD_EXPIRED = "bpp:metrics:hold_expired"
_BOOKING_CONFIRMED = "bpp:metrics:booking_confirmed"
_BOOKING_CANCELLED = "bpp:metrics:booking_cancelled"

_ALL_COUNTERS = {
    "hold_created": _HOLD_CREATED,
    "hold_expired": _HOLD_EXPIRED,
    "confirmed": _BOOKING_CONFIRMED,
    "cancelled": _BOOKING_CANCELLED,
}


def record_hold_created() -> None:
    increment_counter(_HOLD_CREATED)


def record_hold_expired() -> None:
    increment_counter(_HOLD_EXPIRED)


def record_booking_confirmed() -> None:
    increment_counter(_BOOKING_CONFIRMED)


def record_booking_cancelled() -> None:
    increment_counter(_BOOKING_CANCELLED)


def hold_created_consumer(_event: dict) -> None:
    """Real, queue-driven counterpart to `record_hold_created()` (livetracker4.md
    §2.1) — triggered by consuming a real `SlotEvent.RESERVED` off the bus. The
    sole way this counter is incremented as of the 2026-08-02 cutover;
    `select_service.py` no longer calls `record_hold_created()` inline. No
    payload fields are actually needed — a RESERVED event firing at all is the
    whole signal."""
    record_hold_created()


def booking_lifecycle_consumer(event: dict) -> None:
    """Real, queue-driven counterpart to `record_booking_confirmed()`/
    `record_hold_expired()`/`record_booking_cancelled()` (livetracker4.md §2.1).
    A `BookingEvent.CANCELLED`'s own `reason` (read from the enriched event
    payload — see `reservation.py`'s own producer-side note) is what
    distinguishes "hold expired before the customer could confirm" from a
    genuine customer-initiated cancel; `superseded_by_reselect` (choosing a
    different slot before ever confirming) was never counted by the inline path
    either, matching it exactly rather than inventing a new metric here."""
    from inventory_core.events import BookingEvent

    payload = event.get("payload") or {}
    event_type = event.get("event_type")
    if event_type == BookingEvent.CONFIRMED:
        record_booking_confirmed()
    elif event_type == BookingEvent.CANCELLED:
        reason = payload.get("reason")
        if reason == "hold_expired":
            record_hold_expired()
        elif reason == "customer_cancel":
            record_booking_cancelled()


def render_metrics() -> list[str]:
    """Called by shared.django_observability.views.metrics_view via
    settings.EXTRA_METRICS_PROVIDERS."""
    return [
        *render_counter_family(
            metric_name="bpp_booking_lifecycle_total",
            help_text="Real booking-lifecycle event counts, Redis-backed (§3.10)",
            label_name="event",
            counters=_ALL_COUNTERS,
        ),
        *_render_event_bus_health(),
    ]


def _render_event_bus_health() -> list[str]:
    """livetracker4.md §2.1's own gap audit: nothing exposed whether the real
    event worker (`run_event_worker`, `bpp-worker` in docker-compose.yml) was
    actually alive, or whether poisoned events were piling up in the DLQ — both
    real operational blind spots (see RUNBOOK.md's alerting table and Known
    Operational Facts entry for this same date). `EventBus.worker_is_alive()`
    reads a short-TTL heartbeat key the worker itself writes once per loop
    iteration (`shared/event_bus/worker.py`'s `on_heartbeat` hook) — a genuinely
    dead/hung worker's key naturally expires, no explicit "worker died" event
    required."""
    from core.events import get_event_bus

    bus = get_event_bus()
    return [
        "# HELP bpp_event_bus_dlq_length Real, live count of events currently sitting in the DLQ",
        "# TYPE bpp_event_bus_dlq_length gauge",
        f"bpp_event_bus_dlq_length {bus.dlq_length()}",
        "# HELP bpp_event_bus_worker_alive 1 if run_event_worker's heartbeat is fresh, else 0",
        "# TYPE bpp_event_bus_worker_alive gauge",
        f"bpp_event_bus_worker_alive {1 if bus.worker_is_alive() else 0}",
    ]
