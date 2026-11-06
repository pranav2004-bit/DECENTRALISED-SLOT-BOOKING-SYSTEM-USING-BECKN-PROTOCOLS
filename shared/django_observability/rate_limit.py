"""Rate limiting for BAP's/BPP's customer- and business-facing endpoints
(livetracker2.md §3.7) — these are now internet-exposed, a materially different
threat profile from the trust layer's participant-only endpoints. Mirrors the real,
proven pattern already established at `registry/core/rate_limit.py` (confirmed by
direct read, not assumed): a fixed-window counter via Django's cache framework.

**Redis-backed from day one, not in-memory** — the bullet's own explicit ask,
learning from the *proven* fix (`RedisCircuitBreaker`, `livetracker1.md` Phase 4.2)
rather than repeating Registry's own still-open, accepted-for-now `LocMemCache`
limitation a second time in new code. This works for free here: both BAP's and
BPP's `settings.py` already configure `django.core.cache` as
`django_redis.cache.RedisCache` (confirmed directly), so reusing Django's cache
framework, unlike Registry's default, is genuinely Redis-backed without any extra
wiring.
"""

import logging
from functools import wraps

import redis.exceptions
from django.core.cache import cache
from django_redis.exceptions import ConnectionInterrupted

from redis_safe import RedisHardTimeout, call_with_hard_timeout

from .errors import error_response

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


def resolve_client_ip(request) -> str:
    """Real gap found and closed live 2026-11-05: every customer-facing trigger view
    (search/select/init/confirm/etc.) is only ever reached browser -> Next.js's
    same-origin `/api/:path*` rewrite (added for BUG_TRACKER.md #3's cookie fix) ->
    this backend — never called directly by a browser. `REMOTE_ADDR` is therefore
    always the Next.js container's own hop, identical for every real visitor. That
    broke two independent things keyed on it: this module's own per-client rate
    limit (confirmed live: 21/21 recent requests across different browser sessions
    all logged the same REMOTE_ADDR, collapsing every "/min" limit into one
    global, site-wide bucket) and `SearchSession.client_ip`
    (`core/views.py`'s `search_trigger_view`), the anonymous-actor key
    `select_service.MAX_CONCURRENT_HOLDS` counts against — same collapse, different
    symptom: every anonymous visitor's holds counted against one shared total,
    confirmed live showing "Too many concurrent reservation holds" from a handful of
    an entire evening's unrelated test sessions.

    Fix: prefer `X-Forwarded-For`'s first (leftmost/original) entry — the standard
    convention both Next.js's rewrite proxy and Caddy (the real prod edge,
    BUG_TRACKER.md #1/#2) follow — falling back to `REMOTE_ADDR` when the header is
    absent (e.g. a non-browser Beckn callback hitting this backend directly, or any
    other caller that bypasses the proxy). Trusting this header is safe here because
    this backend is never itself directly internet-exposed in production (only
    Caddy's edge is); it's reachable un-proxied only on a local dev machine, where
    evading a rate limit or hold cap isn't a real threat. Any other call site that
    needs to identify "which real actor is this" (not just rate-limiting) should
    use this instead of reading `REMOTE_ADDR` directly — see `core/views.py`'s
    `search_trigger_view`.
    """
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    first_hop = forwarded.split(",")[0].strip()
    if first_hop:
        return first_hop
    return request.META.get("REMOTE_ADDR", "unknown")


def _client_ip_key(request) -> str:
    return resolve_client_ip(request)


def by_authenticated_account(request) -> str:
    """Key by the logged-in account's id rather than IP — for endpoints that are
    already session-authenticated (§3.7's own gap-closed note: BPP's
    `POST /api/v1/resources`/`.../availability`), so the throttle can't be evaded
    just by rotating IPs, and one business's own traffic never counts against
    another's. Falls back to per-IP for an unauthenticated caller — the view's own
    `request.user.is_authenticated` check still rejects them with a real 401, this
    is only about which counter an unauthenticated hammering attempt increments."""
    if request.user.is_authenticated:
        return f"user:{request.user.id}"
    return f"ip:{_client_ip_key(request)}"


def rate_limit(*, limit_per_minute: int, scope: str, key_func=_client_ip_key):
    """`limit_per_minute` values here are deliberately conservative starting
    points, not derived from real production traffic data (none exists yet,
    pre-launch) — honestly labelled as such rather than presented as precision
    they don't have, matching this project's own established convention
    (e.g. §3.10's alert thresholds)."""

    def decorator(view_func):
        @wraps(view_func)
        def wrapped(request, *args, **kwargs):
            # Real gap found and closed live 2026-07-26: the original shape here
            # was `cache.incr()`, catch `ValueError` (key doesn't exist yet), fall
            # back to `cache.set(key, 1)` — a non-atomic check-then-set. Under
            # genuine concurrent requests racing on a brand-new window key, several
            # could all see it as missing at once and each independently reset it
            # to 1, undercounting real traffic against the limit (identical bug,
            # same fix, as `metrics.py`'s `increment_counter()` — see that
            # docstring for the full live reproduction). Fixed with `cache.add()`
            # (atomic "SET if not exists") to guarantee the key exists before ever
            # calling `cache.incr()`, which is then always genuinely atomic.
            key = f"ratelimit:{scope}:{key_func(request)}"
            try:
                call_with_hard_timeout(cache.add, key, 0, timeout=60)
                count = call_with_hard_timeout(cache.incr, key)
            except _REDIS_UNAVAILABLE:
                # Real gap found and closed at Phase 3 Exit (livetracker2.md, 2026-07-24,
                # live "kill Redis" re-test): only `ValueError` (django_redis's own
                # "key doesn't exist yet" signal) was being caught here — a genuine Redis
                # connection failure raises `ConnectionInterrupted` instead, which was
                # uncaught and crashed/hung every rate-limited endpoint, including
                # `/search` itself. Fails open (same rationale as `RedisCircuitBreaker`,
                # shared/resilient_http/circuit_breaker.py): the rate limiter's own
                # dependency failing must not become a second single point of failure for
                # the request it exists to protect.
                logger.warning(
                    "rate_limit: Redis unreachable for scope=%s, failing open (request not rate-limited)",
                    scope,
                )
                return view_func(request, *args, **kwargs)
            if count > limit_per_minute:
                return error_response(
                    "RATE_LIMITED",
                    f"{scope} rate limit of {limit_per_minute}/min exceeded",
                    429,
                )
            return view_func(request, *args, **kwargs)

        return wrapped

    return decorator
