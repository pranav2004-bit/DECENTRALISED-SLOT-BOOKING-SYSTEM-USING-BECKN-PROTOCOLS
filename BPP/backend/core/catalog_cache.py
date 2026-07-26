"""Read-through Redis cache in front of `build_catalog()` (livetracker2.md §3.8, widened
per-domain in §4.1) — real production infrastructure genuinely backed by BPP's own
already-`django_redis`-configured cache, not in-memory (confirmed directly in
`bpp/settings.py`).

One cache entry per domain, not one global entry: `build_catalog(domain_code)` now takes
a real parameter (§4.1 — BPP can serve more than one ONDC domain, e.g. Beauty and
Healthcare, at once), so a single shared cache key would let one domain's cached catalog
be served to a request for a completely different domain the moment more than one exists.
Every function here now takes `domain_code` and derives its own cache key from it.

**Never consulted by `confirm` or any other booking-mutation path** — `select_
service.py`/`shared/inventory_core/reservation.py`'s real slot-holding/confirming
code never imports this module, confirmed by direct read; those always hit
Postgres directly, matching this bullet's own Source-of-Truth requirement.

Invalidation (§3.8, see `livetracker2.md`'s own corrected bullet text for why
Slot-level events were never the real trigger): wired to the only two things that
actually change what's *in* a domain's catalog — a new `Resource` being created
(`views.resource_create_view`) and a `BusinessAccount`'s `is_active` status
changing (`signals.py`, catches Django-admin edits too, which don't go through
application code) — both now invalidate only the *saving business's own domain*,
not every domain's cache. A TTL is still set as a defense-in-depth safety net, not
the primary correctness mechanism — if some future mutation path is ever added and
forgets to call `invalidate()`, the cache self-heals within the TTL instead of
serving stale data forever.
"""

import logging

import redis.exceptions
from django.core.cache import cache
from django_redis.exceptions import ConnectionInterrupted
from redis_safe import RedisHardTimeout, call_with_hard_timeout

from .catalog import build_catalog

logger = logging.getLogger("bpp")

# Real gap found and closed at Phase 3 Exit (livetracker2.md, 2026-07-24, live "kill
# Redis" re-test, second pass): `cache.get()` raises django_redis's own
# `ConnectionInterrupted` on a connection failure, but `cache.set()` was observed
# raising the raw `redis.exceptions.ConnectionError` instead — the two are unrelated
# exception classes (confirmed: `ConnectionInterrupted` is not a `RedisError`
# subclass), so a fix that only caught one left the other still uncaught. Both are
# caught everywhere in this module now. `RedisHardTimeout` covers a third failure
# mode found later the same day: DNS resolution against a stopped-but-present
# container isn't bounded by either exception class above (see shared/redis_safe.py).
_REDIS_UNAVAILABLE = (ConnectionInterrupted, redis.exceptions.RedisError, RedisHardTimeout)

CACHE_TTL_SECONDS = 300


def _cache_key(domain_code: str) -> str:
    return f"bpp:catalog:{domain_code}"


def get_cached_catalog(domain_code: str) -> dict:
    """Read-through: returns `domain_code`'s cached catalog if present, otherwise builds
    it fresh (the real, uncached `build_catalog(domain_code)`) and populates the cache
    before returning.

    Real gap found and closed at Phase 3 Exit (livetracker2.md, 2026-07-24, live
    "kill Redis" re-test): neither `cache.get()` nor `cache.set()` here had any
    error handling — a genuine Redis outage raised an uncaught
    `ConnectionInterrupted`, which live-testing showed as `/search` hanging until
    Gateway's own client-side timeout gave up, not a clean failure. The Phase
    3.11 circuit-breaker fail-open fix (`shared/resilient_http`) never covered
    this code path — it's a completely separate Redis client (Django's own cache
    framework), not `resilient_http`'s outbound-HTTP breaker. Fails open onto the
    real, uncached path: a cache being unreachable must degrade to "slower,
    always-correct" (compute fresh from Postgres every time), never to "broken.\""""
    key = _cache_key(domain_code)
    try:
        catalog = call_with_hard_timeout(cache.get, key)
    except _REDIS_UNAVAILABLE:
        logger.warning(
            "catalog_cache: Redis unreachable, computing %s catalog fresh from Postgres",
            domain_code,
        )
        return build_catalog(domain_code)
    if catalog is not None:
        return catalog
    catalog = build_catalog(domain_code)
    try:
        call_with_hard_timeout(cache.set, key, catalog, timeout=CACHE_TTL_SECONDS)
    except _REDIS_UNAVAILABLE:
        logger.warning(
            "catalog_cache: Redis unreachable, could not persist the freshly-built %s catalog",
            domain_code,
        )
    return catalog


def invalidate_catalog_cache(domain_code: str) -> None:
    """Write-through invalidation for one domain — called at the exact two real mutation
    points (see module docstring), not on a schedule. Fails open (logs, doesn't raise)
    on a Redis outage, same reasoning as `get_cached_catalog()` — the TTL is already this
    cache's own designed safety net for exactly this kind of missed/failed invalidation,
    so a dropped invalidation self-heals within `CACHE_TTL_SECONDS` once Redis returns,
    rather than crashing the real mutation (e.g. resource creation) this is attached to."""
    try:
        call_with_hard_timeout(cache.delete, _cache_key(domain_code))
    except _REDIS_UNAVAILABLE:
        logger.warning(
            "catalog_cache: Redis unreachable, could not invalidate %s cache "
            "(will self-heal via TTL once Redis returns)",
            domain_code,
        )


def reconcile_catalog_cache(domain_code: str) -> bool:
    """Periodic reconciliation (livetracker2.md §3.11, corrected target — see the tracker's
    own audit finding 4; widened per-domain in §4.1) for one domain: rebuilds that domain's
    catalog fresh from Postgres and compares it against whatever is currently cached,
    overwriting the cache if they differ. Catches the case the TTL is already a safety net
    for (some future mutation path added and forgetting to call `invalidate_catalog_cache()`)
    but corrects it immediately instead of waiting up to `CACHE_TTL_SECONDS`. Returns `True`
    if the cache was missing or drifted and got corrected, `False` if it already matched (the
    ordinary, healthy case) or if Redis was unreachable this tick (Phase 3 Exit fix,
    livetracker2.md 2026-07-24 — previously an uncaught `ConnectionInterrupted` here logged
    as an unhandled-exception ERROR traceback on every tick while Redis was down; now a
    clean, expected WARNING, and the sweep just waits for the next tick instead). The
    calling loop (`reconciliation.py`) sweeps every currently-registered domain, not just
    one, so this function itself stays single-domain and simple."""
    fresh = build_catalog(domain_code)
    key = _cache_key(domain_code)
    try:
        cached = call_with_hard_timeout(cache.get, key)
    except _REDIS_UNAVAILABLE:
        logger.warning(
            "catalog_cache: Redis unreachable during %s reconciliation, skipping this tick",
            domain_code,
        )
        return False
    if cached == fresh:
        return False
    try:
        call_with_hard_timeout(cache.set, key, fresh, timeout=CACHE_TTL_SECONDS)
    except _REDIS_UNAVAILABLE:
        logger.warning(
            "catalog_cache: Redis unreachable, could not persist the reconciled %s catalog",
            domain_code,
        )
        return False
    return True
