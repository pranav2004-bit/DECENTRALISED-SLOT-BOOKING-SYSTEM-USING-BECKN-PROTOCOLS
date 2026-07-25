"""Redis-backed business-metrics counters (livetracker2.md §3.10) — the same
Redis-via-Django's-cache-framework pattern already proven in `rate_limit.py`
(confirmed by direct read: identical `cache.incr()`/`ValueError`-then-`set()`
shape), deliberately NOT Registry's own in-process `core/metrics.py` pattern —
that one carries the exact per-worker-undercounting limitation `RUNBOOK.md`
documents, and §3.10's own bullet decided Redis-backed counters from the start
here rather than repeating that still-open gap a third time. Registry's own
metrics are explicitly not retroactively fixed by this module.

Generic, app-agnostic primitive — each app (BAP/BPP) defines its own real
counter names and wires increments at its own real trigger points; this module
only owns "how a counter is stored/read/rendered," not what any app counts.
"""

import logging

import redis.exceptions
from django.core.cache import cache
from django_redis.exceptions import ConnectionInterrupted

from redis_safe import RedisHardTimeout, call_with_hard_timeout

logger = logging.getLogger("django_observability")

# `cache.get()`/`cache.incr()` raise django_redis's own `ConnectionInterrupted` on a
# connection failure, but `cache.set()` was observed live raising the raw
# `redis.exceptions.ConnectionError` instead — confirmed the two are unrelated
# exception classes (`ConnectionInterrupted` is not a `RedisError` subclass), so both
# must be caught (Phase 3 Exit fix, livetracker2.md 2026-07-24, second pass).
# `RedisHardTimeout` covers a third failure mode found later the same day: DNS
# resolution against a stopped-but-present container isn't bounded by either
# exception class above (see shared/redis_safe.py).
_REDIS_UNAVAILABLE = (ConnectionInterrupted, redis.exceptions.RedisError, RedisHardTimeout)

# A long-lived running total, not a rate-limit window — 30 days is generous
# headroom for this project's current dev/pilot scale, revisited if this ever
# needs to survive longer without a scrape.
_COUNTER_TTL_SECONDS = 60 * 60 * 24 * 30


def increment_counter(key: str, amount: int = 1) -> None:
    """Atomic Redis `INCR` via Django's cache framework (`django_redis` maps
    `cache.incr()` to a real Redis `INCR`, not a read-modify-write) — correct
    under real concurrent writers from independent processes, the actual
    property this section's Test Gate cares about.

    Real gap found and closed at Phase 3 Exit (livetracker2.md, 2026-07-24, live
    "kill Redis" re-test): a genuine Redis connection failure raises
    `ConnectionInterrupted`, not the `ValueError` this function already handled —
    left uncaught, incrementing a counter (a non-critical observability
    side-effect called from real request-handling code, e.g. BAP's search-trigger
    success path) could crash the actual customer-facing request it's attached
    to. A dropped counter increment during a Redis outage is acceptable (see
    DATABASE.md's Redis Persistence section); crashing the request over it is not."""
    try:
        try:
            call_with_hard_timeout(cache.incr, key, amount)
        except ValueError:
            call_with_hard_timeout(cache.set, key, amount, timeout=_COUNTER_TTL_SECONDS)
    except _REDIS_UNAVAILABLE:
        logger.warning("metrics: Redis unreachable, dropping increment for %s", key)


def get_counter(key: str) -> int:
    try:
        return call_with_hard_timeout(cache.get, key, 0)
    except _REDIS_UNAVAILABLE:
        logger.warning("metrics: Redis unreachable, reporting 0 for %s", key)
        return 0


def render_counter_family(
    *, metric_name: str, help_text: str, label_name: str, counters: dict[str, str]
) -> list[str]:
    """Real Prometheus text-exposition lines for one counter family, e.g.
    `render_counter_family(metric_name="bap_booking_funnel_total",
    help_text="...", label_name="stage",
    counters={"search_triggered": "bap:metrics:search_triggered", ...})`.
    `counters` maps a Prometheus label value to the real Redis cache key
    backing it — called by each app's own `core/metrics.py`
    (`EXTRA_METRICS_PROVIDERS` entry), not by this shared module directly."""
    lines = [f"# HELP {metric_name} {help_text}", f"# TYPE {metric_name} counter"]
    for label, cache_key in sorted(counters.items()):
        lines.append(f'{metric_name}{{{label_name}="{label}"}} {get_counter(cache_key)}')
    return lines
