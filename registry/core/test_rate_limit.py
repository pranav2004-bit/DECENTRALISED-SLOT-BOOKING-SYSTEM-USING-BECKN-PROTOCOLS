"""livetracker8.md §1.1: Registry's rate limiter moved off in-memory (LocMemCache) to
the shared, Redis-backed `django_observability.rate_limit.rate_limit` (already proven
live for BAP/BPP, see BAP/backend/core/test_rate_limit.py) — this file covers the two
behaviors `test_security.py`'s real-endpoint tests don't: fail-open when Redis itself
is unreachable, and atomic counting under genuine concurrent requests. Both were
previously untested for Registry specifically (the old `core/rate_limit.py` had no
fail-open handling at all — a real connection error would have crashed the request).
"""

import threading
from unittest.mock import patch

import pytest
import redis.exceptions
from django.core.cache import cache
from django.http import JsonResponse
from django.test import RequestFactory
from django_observability.rate_limit import rate_limit
from django_redis.exceptions import ConnectionInterrupted


@pytest.fixture(autouse=True)
def _clear_rate_limit_cache():
    cache.clear()
    yield
    cache.clear()


@rate_limit(limit_per_minute=3, scope="test-scope")
def _dummy_view(request):
    return JsonResponse({"ok": True}, status=200)


def test_rate_limit_fails_open_when_redis_itself_is_unreachable():
    """The request must go through, not fail, when the rate limiter's own Redis
    dependency is down — a Redis blip must never become a second single point of
    failure for Subscribe/Lookup."""
    factory = RequestFactory()
    request = factory.post("/dummy")
    with patch(
        "django_observability.rate_limit.cache.incr", side_effect=ConnectionInterrupted("down")
    ):
        response = _dummy_view(request)
    assert response.status_code == 200


def test_rate_limit_fails_open_on_the_raw_redis_connection_error_too():
    """`cache.incr()`/`cache.add()` can also raise the raw `redis.exceptions.ConnectionError`
    directly — a genuinely different exception class from `ConnectionInterrupted`, not a
    subclass of it. Both must fail open."""
    factory = RequestFactory()
    request = factory.post("/dummy")
    with patch(
        "django_observability.rate_limit.cache.incr",
        side_effect=redis.exceptions.ConnectionError("down"),
    ):
        response = _dummy_view(request)
    assert response.status_code == 200


def test_rate_limit_counts_every_request_under_real_concurrent_traffic():
    """20 real concurrent requests against a generous limit must all be individually
    counted (proves the atomic `cache.add()`-then-`cache.incr()` shape is really in
    effect for Registry now, not the old non-atomic incr/except-ValueError version
    that could undercount under a race)."""
    factory = RequestFactory()
    request = factory.post("/dummy", REMOTE_ADDR="10.0.0.9")

    @rate_limit(limit_per_minute=1000, scope="registry-concurrency-test")
    def _concurrent_view(request):
        return JsonResponse({"ok": True}, status=200)

    responses = []
    lock = threading.Lock()

    def _call():
        resp = _concurrent_view(request)
        with lock:
            responses.append(resp.status_code)

    threads = [threading.Thread(target=_call) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert responses == [200] * 20
    assert cache.get("ratelimit:registry-concurrency-test:10.0.0.9") == 20
