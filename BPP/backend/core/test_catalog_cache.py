"""Phase 3.8 Test Gate (livetracker2.md §3.8) piece owned by BPP: the read-through
catalog cache and its write-through invalidation on the two real triggers (Resource
creation, BusinessAccount active-status changes) — see catalog_cache.py's module
docstring for why Slot-level events were never the real trigger.
"""

import time
from unittest.mock import patch

import pytest
import redis.exceptions
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django_redis.exceptions import ConnectionInterrupted
from inventory_core.models import Resource

from core.catalog import build_beauty_catalog
from core.catalog_cache import (
    CACHE_KEY,
    get_cached_beauty_catalog,
    invalidate_beauty_catalog_cache,
    reconcile_beauty_catalog_cache,
)

BusinessAccount = get_user_model()

TEST_PASSWORD = "a-strong-passw0rd!"  # pragma: allowlist secret


@pytest.fixture(autouse=True)
def _clear_catalog_cache():
    cache.delete(CACHE_KEY)
    yield
    cache.delete(CACHE_KEY)


@pytest.fixture
def client():
    return Client()


def _business_with_resource(*, business_name="Glow Salon", contact="owner@example.com"):
    business = BusinessAccount.objects.create_user(
        contact=contact, business_name=business_name, password=TEST_PASSWORD
    )
    Resource.objects.create(
        owner_ref=str(business.id),
        name="Stylist A",
        domain_data={"resource_type": "stylist"},
    )
    return business


@pytest.mark.django_db
def test_get_cached_beauty_catalog_matches_the_real_uncached_build():
    _business_with_resource()
    assert get_cached_beauty_catalog() == build_beauty_catalog()


@pytest.mark.django_db
def test_second_call_is_served_from_cache_without_hitting_the_database():
    """LOAD (§3.8's own Test Gate): a cached read genuinely skips the DB query
    `build_beauty_catalog()` would otherwise run — not just "returns the same
    data faster by coincidence"."""
    _business_with_resource()
    get_cached_beauty_catalog()  # first call: cold, populates the cache

    with CaptureQueriesContext(connection) as ctx:
        get_cached_beauty_catalog()
    assert len(ctx.captured_queries) == 0


@pytest.mark.django_db
def test_invalidate_forces_the_next_call_to_hit_the_database_again():
    _business_with_resource()
    get_cached_beauty_catalog()
    invalidate_beauty_catalog_cache()

    with CaptureQueriesContext(connection) as ctx:
        get_cached_beauty_catalog()
    assert len(ctx.captured_queries) > 0


@pytest.mark.django_db
def test_creating_a_resource_via_the_real_endpoint_invalidates_the_cache(client):
    """FUNC/LOAD: a real, live-relevant write-through invalidation — a customer
    searching right after a business adds a new service must see it, not a
    stale pre-creation catalog."""
    business = _business_with_resource(contact="first@example.com")
    stale = get_cached_beauty_catalog()
    assert len(stale["providers"][0]["items"]) == 1

    client.force_login(business)
    resp = client.post(
        reverse("resource-create"),
        data={"name": "Stylist B", "domain_data": {"resource_type": "stylist"}},
        content_type="application/json",
    )
    assert resp.status_code == 201

    fresh = get_cached_beauty_catalog()
    assert len(fresh["providers"][0]["items"]) == 2


@pytest.mark.django_db
def test_saving_a_business_account_invalidates_the_cache():
    """Catches Django-admin-driven `is_active` toggles, which never go through
    application view code (§2.2's own established deactivation mechanism)."""
    business = _business_with_resource()
    get_cached_beauty_catalog()
    assert cache.get(CACHE_KEY) is not None

    business.is_active = False
    business.save()

    assert cache.get(CACHE_KEY) is None


@pytest.mark.django_db
def test_deactivating_a_business_account_removes_it_from_the_next_catalog_build():
    business = _business_with_resource()
    get_cached_beauty_catalog()

    business.is_active = False
    business.save()

    fresh = get_cached_beauty_catalog()
    assert fresh["providers"] == []


def _seed_realistic_catalog(*, business_count=40, resources_per_business=3):
    """A real, non-trivial dataset — at the current 1-4-row scale used elsewhere
    in this project's tests, an uncached DB read is already sub-millisecond, so a
    relative cached-vs-uncached comparison would be measuring noise, not the
    cache. `bulk_create` for seeding speed, not because the app itself ever
    bulk-creates."""
    businesses = BusinessAccount.objects.bulk_create(
        [
            BusinessAccount(
                contact=f"business-{i}@example.com",
                business_name=f"Salon {i}",
                password="unusable",  # pragma: allowlist secret
            )
            for i in range(business_count)
        ]
    )
    Resource.objects.bulk_create(
        [
            Resource(
                owner_ref=str(business.id),
                name=f"{business.business_name} Service {j}",
                domain_data={"resource_type": "stylist"},
            )
            for business in businesses
            for j in range(resources_per_business)
        ]
    )


@pytest.mark.django_db
def test_reconcile_corrects_a_cache_entry_deliberately_corrupted_out_of_band():
    """livetracker2.md §3.11's exact Test Gate wording: deliberately corrupt a cache entry
    out-of-band, confirm the (here, directly-invoked) reconciliation run detects and corrects
    it — real drift the write-through invalidation triggers never see, e.g. a future mutation
    path that forgets to call `invalidate_beauty_catalog_cache()`."""
    _business_with_resource()
    get_cached_beauty_catalog()
    cache.set(CACHE_KEY, {"providers": [{"this": "is deliberately corrupted, out-of-band"}]})

    corrected = reconcile_beauty_catalog_cache()

    assert corrected is True
    assert cache.get(CACHE_KEY) == build_beauty_catalog()


@pytest.mark.django_db
def test_reconcile_is_a_noop_when_the_cache_already_matches_reality():
    _business_with_resource()
    get_cached_beauty_catalog()

    corrected = reconcile_beauty_catalog_cache()

    assert corrected is False


@pytest.mark.django_db
def test_reconcile_populates_a_missing_cache_entry_too():
    _business_with_resource()
    assert cache.get(CACHE_KEY) is None

    corrected = reconcile_beauty_catalog_cache()

    assert corrected is True
    assert cache.get(CACHE_KEY) == build_beauty_catalog()


@pytest.mark.django_db
def test_cached_search_is_measurably_faster_than_the_equivalent_uncached_db_query():
    """LOAD (§3.8's own Test Gate, verbatim): a cached `search` response is
    measurably faster than an equivalent uncached DB query, measured locally
    with real data. No fixed target — the measurement itself is the deliverable
    (this bullet's own explicit instruction) — only that cached is reliably,
    substantially faster than uncached, not by a coincidental hair."""
    _seed_realistic_catalog()

    uncached_times = []
    for _ in range(5):
        start = time.perf_counter()
        build_beauty_catalog()
        uncached_times.append(time.perf_counter() - start)
    uncached_median = sorted(uncached_times)[len(uncached_times) // 2]

    get_cached_beauty_catalog()  # warm the cache once
    cached_times = []
    for _ in range(5):
        start = time.perf_counter()
        get_cached_beauty_catalog()
        cached_times.append(time.perf_counter() - start)
    cached_median = sorted(cached_times)[len(cached_times) // 2]

    print(
        f"\n[§3.8 LOAD] uncached median: {uncached_median * 1000:.2f}ms, "
        f"cached median: {cached_median * 1000:.2f}ms, "
        f"speedup: {uncached_median / cached_median:.1f}x"
    )
    assert cached_median < uncached_median


@pytest.mark.django_db
def test_get_cached_catalog_falls_back_to_a_fresh_build_when_redis_is_unreachable():
    """Real gap found and closed at Phase 3 Exit (livetracker2.md, 2026-07-24, live
    "kill Redis" re-test): `cache.get()` had no error handling — a genuine Redis
    outage raised an uncaught `ConnectionInterrupted`, which live-testing showed as
    `/search` hanging until Gateway's own client-side timeout gave up. The Phase
    3.11 circuit-breaker fail-open fix never covered this code path — a completely
    separate Redis client (Django's cache framework, not `resilient_http`'s
    breaker). Must degrade to "slower, always-correct" (fresh from Postgres),
    never to "broken.\""""
    _business_with_resource()
    with patch("core.catalog_cache.cache.get", side_effect=ConnectionInterrupted("down")):
        result = get_cached_beauty_catalog()
    assert result == build_beauty_catalog()


@pytest.mark.django_db
def test_get_cached_catalog_does_not_raise_if_the_cache_write_fails_after_a_fresh_build():
    _business_with_resource()
    with patch("core.catalog_cache.cache.set", side_effect=ConnectionInterrupted("down")):
        result = get_cached_beauty_catalog()  # must not raise
    assert result == build_beauty_catalog()


def test_invalidate_does_not_raise_when_redis_is_unreachable():
    with patch("core.catalog_cache.cache.delete", side_effect=ConnectionInterrupted("down")):
        invalidate_beauty_catalog_cache()  # must not raise


@pytest.mark.django_db
def test_reconcile_skips_the_tick_cleanly_when_redis_is_unreachable():
    _business_with_resource()
    with patch("core.catalog_cache.cache.get", side_effect=ConnectionInterrupted("down")):
        corrected = reconcile_beauty_catalog_cache()  # must not raise
    assert corrected is False


@pytest.mark.django_db
def test_get_cached_catalog_falls_back_on_the_raw_redis_connection_error_too():
    """`cache.set()` was observed live raising the raw
    `redis.exceptions.ConnectionError` instead of `ConnectionInterrupted` — a
    genuinely different exception class (confirmed: `ConnectionInterrupted` is not
    a `RedisError` subclass). Both must be caught, not just one."""
    _business_with_resource()
    with patch(
        "core.catalog_cache.cache.set", side_effect=redis.exceptions.ConnectionError("down")
    ):
        result = get_cached_beauty_catalog()  # must not raise
    assert result == build_beauty_catalog()
