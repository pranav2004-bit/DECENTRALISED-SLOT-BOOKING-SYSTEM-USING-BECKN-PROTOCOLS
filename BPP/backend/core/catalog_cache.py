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

from django.core.cache import cache

from .catalog import build_beauty_catalog

CACHE_KEY = "bpp:beauty_catalog"
CACHE_TTL_SECONDS = 300


def get_cached_beauty_catalog() -> dict:
    """Read-through: returns the cached catalog if present, otherwise builds it
    fresh (the real, uncached `build_beauty_catalog()`) and populates the cache
    before returning."""
    catalog = cache.get(CACHE_KEY)
    if catalog is not None:
        return catalog
    catalog = build_beauty_catalog()
    cache.set(CACHE_KEY, catalog, timeout=CACHE_TTL_SECONDS)
    return catalog


def invalidate_beauty_catalog_cache() -> None:
    """Write-through invalidation — called at the exact two real mutation points
    (see module docstring), not on a schedule."""
    cache.delete(CACHE_KEY)


def reconcile_beauty_catalog_cache() -> bool:
    """Periodic reconciliation (livetracker2.md §3.11, corrected target — see the tracker's
    own audit finding 4): rebuilds the catalog fresh from Postgres and compares it against
    whatever is currently cached, overwriting the cache if they differ. Catches the case the
    TTL is already a safety net for (some future mutation path added and forgetting to call
    `invalidate_beauty_catalog_cache()`) but corrects it immediately instead of waiting up to
    `CACHE_TTL_SECONDS`. Returns `True` if the cache was missing or drifted and got corrected,
    `False` if it already matched (the ordinary, healthy case)."""
    fresh = build_beauty_catalog()
    cached = cache.get(CACHE_KEY)
    if cached == fresh:
        return False
    cache.set(CACHE_KEY, fresh, timeout=CACHE_TTL_SECONDS)
    return True
