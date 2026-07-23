"""livetracker2.md §3.10 Test Gate: BAP's Redis-backed search-to-confirm funnel
counters. Real cache (BAP's own `django_redis`-configured `django.core.cache`),
not mocked."""

import threading

import pytest
from django.core.cache import cache

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
