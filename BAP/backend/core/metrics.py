"""BAP-specific business metrics (livetracker2.md §3.10) — the real
search-to-confirm conversion funnel, the source data for the funnel Grafana
panel. Redis-backed via `django_observability.metrics` (BAP's `settings.py`
already configures `django.core.cache` as `django_redis.cache.RedisCache`) —
correct under real concurrent multi-process writers.

Each stage is incremented where that stage's real *outcome* is first known —
`search_triggered` when Gateway ACKs the trigger (matching `search_service`'s
own definition of a successful trigger), the later three stages when their
real `record_on_X_result()` callback handler records a genuine success (not
their `trigger_X()` calls, which only prove the request was sent, not that it
succeeded) — so the funnel counts real successful progressions, not attempts.
"""

from django_observability.metrics import increment_counter, render_counter_family

_SEARCH_TRIGGERED = "bap:metrics:search_triggered"
_SELECT_SUCCEEDED = "bap:metrics:select_succeeded"
_INIT_SUCCEEDED = "bap:metrics:init_succeeded"
_CONFIRM_SUCCEEDED = "bap:metrics:confirm_succeeded"

_ALL_COUNTERS = {
    "search_triggered": _SEARCH_TRIGGERED,
    "select_succeeded": _SELECT_SUCCEEDED,
    "init_succeeded": _INIT_SUCCEEDED,
    "confirm_succeeded": _CONFIRM_SUCCEEDED,
}


def record_search_triggered() -> None:
    increment_counter(_SEARCH_TRIGGERED)


def record_select_succeeded() -> None:
    increment_counter(_SELECT_SUCCEEDED)


def record_init_succeeded() -> None:
    increment_counter(_INIT_SUCCEEDED)


def record_confirm_succeeded() -> None:
    increment_counter(_CONFIRM_SUCCEEDED)


def render_metrics() -> list[str]:
    """Called by shared.django_observability.views.metrics_view via
    settings.EXTRA_METRICS_PROVIDERS."""
    return render_counter_family(
        metric_name="bap_booking_funnel_total",
        help_text="Real search-to-confirm conversion funnel counts, Redis-backed (§3.10)",
        label_name="stage",
        counters=_ALL_COUNTERS,
    )
