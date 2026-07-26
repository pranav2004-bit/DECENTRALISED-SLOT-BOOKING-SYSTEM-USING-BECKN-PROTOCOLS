"""livetracker2.md §3.10 Test Gate: BAP's Redis-backed search-to-confirm funnel
counters. Real cache (BAP's own `django_redis`-configured `django.core.cache`),
not mocked."""

import threading
from unittest.mock import patch

import pytest
import redis.exceptions
from django.core.cache import cache
from django_observability import metrics as shared_metrics
from django_redis.exceptions import ConnectionInterrupted

from . import metrics


@pytest.fixture(autouse=True)
def _clear_metrics_cache():
    cache.clear()
    yield
    cache.clear()


def test_each_funnel_stage_increments_its_own_real_redis_counter():
    metrics.record_search_triggered()
    metrics.record_search_triggered()
    metrics.record_select_succeeded()
    metrics.record_init_succeeded()
    metrics.record_confirm_succeeded()

    rendered = "\n".join(metrics.render_metrics())
    assert 'stage="search_triggered"} 2' in rendered
    assert 'stage="select_succeeded"} 1' in rendered
    assert 'stage="init_succeeded"} 1' in rendered
    assert 'stage="confirm_succeeded"} 1' in rendered


def test_zero_stages_render_zero_not_missing():
    rendered = "\n".join(metrics.render_metrics())
    assert 'stage="confirm_succeeded"} 0' in rendered


def test_two_real_concurrent_processes_produce_one_correct_combined_total():
    """Same real property as BPP's equivalent test — a Redis-backed counter gives
    one correct total under genuine concurrent writers, the honest substitute for
    a "multiple gunicorn workers" claim BAP's real daphne-based deployment can't
    produce (§3.10's own tracker finding)."""
    iterations = 200

    def _hammer():
        for _ in range(iterations):
            metrics.record_search_triggered()

    threads = [threading.Thread(target=_hammer) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    rendered = "\n".join(metrics.render_metrics())
    assert f'stage="search_triggered"}} {iterations * 4}' in rendered


def test_increment_counter_does_not_raise_when_redis_is_unreachable():
    """Real gap found and closed at Phase 3 Exit (livetracker2.md, 2026-07-24, live
    "kill Redis" re-test): only `ValueError` was caught here — a genuine Redis
    connection failure raises `ConnectionInterrupted` instead, left uncaught. This
    is called from real request-handling code (e.g. the search-trigger success
    path) — a dropped metrics increment must never crash the actual customer
    request it's attached to."""
    with patch(
        "django_observability.metrics.cache.incr", side_effect=ConnectionInterrupted("down")
    ):
        shared_metrics.increment_counter("some:key")  # must not raise


def test_get_counter_returns_zero_when_redis_is_unreachable():
    with patch(
        "django_observability.metrics.cache.get", side_effect=ConnectionInterrupted("down")
    ):
        assert shared_metrics.get_counter("some:key") == 0


def test_increment_counter_does_not_raise_on_the_raw_redis_connection_error_too():
    """`cache.incr()` was observed live raising the raw `redis.exceptions.ConnectionError`
    instead of `ConnectionInterrupted` — a genuinely different exception class. Both must be
    swallowed, not just one."""
    with patch(
        "django_observability.metrics.cache.incr",
        side_effect=redis.exceptions.ConnectionError("down"),
    ):
        shared_metrics.increment_counter("some:key")  # must not raise


def test_increment_counter_does_not_raise_when_cache_add_itself_is_unreachable():
    """§4.1-adjacent fix (2026-07-26): `increment_counter()` now calls `cache.add()`
    before `cache.incr()` (the atomic check-then-set race fix) — that new call site
    needs the same Redis-unavailable fail-open coverage as `cache.incr()` already
    had, not just the call that existed before this fix."""
    with patch(
        "django_observability.metrics.cache.add", side_effect=ConnectionInterrupted("down")
    ):
        shared_metrics.increment_counter("some:key")  # must not raise
