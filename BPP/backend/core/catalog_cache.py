"""Read-through Redis cache in front of `build_beauty_catalog()` (livetracker2.md
§3.8) — real production infrastructure genuinely backed by BPP's own already-`django_
redis`-configured cache, not in-memory (confirmed directly in `bpp/settings.py`).

Deliberately a single global cache entry, not one keyed per query: `build_beauty_
catalog()` itself takes no parameters — it always returns BPP's entire visible
catalog (every `ACTIVE` business's `Resource`s) — this project serves exactly one
domain (Beauty), so there is no per-domain/per-query variation to key on.

**Never consulted by `confirm` or any other booking-mutation path** — `select_
service.py`/`shared/inventory_core/reservation.py`'s real slot-holding/confirming
code never imports this module, confirmed by direct read; those always hit
Postgres directly, matching this bullet's own Source-of-Truth requirement.

Invalidation (§3.8, see `livetracker2.md`'s own corrected bullet text for why
Slot-level events were never the real trigger): wired to the only two things that
actually change what's *in* the catalog — a new `Resource` being created
(`views.resource_create_view`) and a `BusinessAccount`'s `is_active` status
changing (`signals.py`, catches Django-admin edits too, which don't go through
application code). A TTL is still set as a defense-in-depth safety net, not the
primary correctness mechanism — if some future mutation path is ever added and
forgets to call `invalidate()`, the cache self-heals within the TTL instead of
serving stale data forever.
"""

import logging

import redis.exceptions
from django.core.cache import cache
from django_redis.exceptions import ConnectionInterrupted
from redis_safe import RedisHardTimeout, call_with_hard_timeout

from .catalog import build_beauty_catalog

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

CACHE_KEY = "bpp:beauty_catalog"
CACHE_TTL_SECONDS = 300


def get_cached_beauty_catalog() -> dict:
    """Read-through: returns the cached catalog if present, otherwise builds it
    fresh (the real, uncached `build_beauty_catalog()`) and populates the cache
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
    try:
        catalog = call_with_hard_timeout(cache.get, CACHE_KEY)
    except _REDIS_UNAVAILABLE:
        logger.warning("catalog_cache: Redis unreachable, computing catalog fresh from Postgres")
        return build_beauty_catalog()
    if catalog is not None:
        return catalog
    catalog = build_beauty_catalog()
    try:
        call_with_hard_timeout(cache.set, CACHE_KEY, catalog, timeout=CACHE_TTL_SECONDS)
    except _REDIS_UNAVAILABLE:
        logger.warning(
            "catalog_cache: Redis unreachable, could not persist the freshly-built catalog"
        )
    return catalog


def invalidate_beauty_catalog_cache() -> None:
    """Write-through invalidation — called at the exact two real mutation points
    (see module docstring), not on a schedule. Fails open (logs, doesn't raise)
    on a Redis outage, same reasoning as `get_cached_beauty_catalog()` — the TTL
    is already this cache's own designed safety net for exactly this kind of
    missed/failed invalidation, so a dropped invalidation self-heals within
    `CACHE_TTL_SECONDS` once Redis returns, rather than crashing the real
    mutation (e.g. resource creation) this is attached to."""
    try:
        call_with_hard_timeout(cache.delete, CACHE_KEY)
    except _REDIS_UNAVAILABLE:
        logger.warning(
            "catalog_cache: Redis unreachable, could not invalidate cache "
            "(will self-heal via TTL once Redis returns)"
        )


def reconcile_beauty_catalog_cache() -> bool:
    """Periodic reconciliation (livetracker2.md §3.11, corrected target — see the tracker's
    own audit finding 4): rebuilds the catalog fresh from Postgres and compares it against
    whatever is currently cached, overwriting the cache if they differ. Catches the case the
    TTL is already a safety net for (some future mutation path added and forgetting to call
    `invalidate_beauty_catalog_cache()`) but corrects it immediately instead of waiting up to
    `CACHE_TTL_SECONDS`. Returns `True` if the cache was missing or drifted and got corrected,
    `False` if it already matched (the ordinary, healthy case) or if Redis was unreachable this
    tick (Phase 3 Exit fix, livetracker2.md 2026-07-24 — previously an uncaught
    `ConnectionInterrupted` here logged as an unhandled-exception ERROR traceback on every tick
    while Redis was down; now a clean, expected WARNING, and the sweep just waits for the next
    tick instead)."""
    fresh = build_beauty_catalog()
    try:
        cached = call_with_hard_timeout(cache.get, CACHE_KEY)
    except _REDIS_UNAVAILABLE:
        logger.warning("catalog_cache: Redis unreachable during reconciliation, skipping this tick")
        return False
    if cached == fresh:
        return False
    try:
        call_with_hard_timeout(cache.set, CACHE_KEY, fresh, timeout=CACHE_TTL_SECONDS)
    except _REDIS_UNAVAILABLE:
        logger.warning("catalog_cache: Redis unreachable, could not persist the reconciled catalog")
        return False
    return True
