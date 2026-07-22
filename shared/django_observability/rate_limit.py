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

from functools import wraps

from django.core.cache import cache

from .errors import error_response


def _client_ip_key(request) -> str:
    return request.META.get("REMOTE_ADDR", "unknown")


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
            key = f"ratelimit:{scope}:{key_func(request)}"
            try:
                count = cache.incr(key)
            except ValueError:
                cache.set(key, 1, timeout=60)
                count = 1
            if count > limit_per_minute:
                return error_response(
                    "RATE_LIMITED",
                    f"{scope} rate limit of {limit_per_minute}/min exceeded",
                    429,
                )
            return view_func(request, *args, **kwargs)

        return wrapped

    return decorator
