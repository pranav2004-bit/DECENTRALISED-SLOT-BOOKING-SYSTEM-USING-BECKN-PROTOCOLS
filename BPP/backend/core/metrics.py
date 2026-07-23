"""BPP-specific business metrics (livetracker2.md §3.10) — real booking-lifecycle
counters, the source data for the booking-success-rate/cancellation-rate/
hold-expiry-rate Grafana panels. Redis-backed via `django_observability.metrics`
(BPP's `settings.py` already configures `django.core.cache` as
`django_redis.cache.RedisCache`) — correct under real concurrent multi-process
writers, unlike Registry's own in-process `core/metrics.py` (that per-worker
limitation is explicitly not retroactively fixed here, see §3.10's tracker note).

Incremented at each event's own real, already-firing production call site — not
`inventory_core.reservation`'s generic primitives themselves (that library stays
Prometheus-agnostic, domain-agnostic, reusable by future Healthcare/Automotive
BPPs with their own metric names), and deliberately not the currently-dead
`release_expired_hold()` (confirmed by direct grep: it has zero production call
sites today, only test call sites — inventing a metric for a code path nothing
ever runs would be a fake number, not a real one). `hold_expired` is instead
counted where a hold's expiry actually first becomes observable in production:
`confirm_service.dispatch_on_confirm`'s existing `ValidationError` branch, when a
customer's real `/confirm` arrives after their hold's TTL already lapsed.
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


def render_metrics() -> list[str]:
    """Called by shared.django_observability.views.metrics_view via
    settings.EXTRA_METRICS_PROVIDERS."""
    return render_counter_family(
        metric_name="bpp_booking_lifecycle_total",
        help_text="Real booking-lifecycle event counts, Redis-backed (§3.10)",
        label_name="event",
        counters=_ALL_COUNTERS,
    )
