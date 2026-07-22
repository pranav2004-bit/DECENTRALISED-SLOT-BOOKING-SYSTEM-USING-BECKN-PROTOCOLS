"""Phase 3.8 Test Gate (livetracker2.md §3.8) piece owned by BPP: the read-through
catalog cache and its write-through invalidation on the two real triggers (Resource
creation, BusinessAccount active-status changes) — see catalog_cache.py's module
docstring for why Slot-level events were never the real trigger.
"""

import time

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from inventory_core.models import Resource

from core.catalog import build_beauty_catalog
from core.catalog_cache import (
    CACHE_KEY,
    get_cached_beauty_catalog,
    invalidate_beauty_catalog_cache,
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
