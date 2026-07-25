"""Phase 3.7 Test Gate (livetracker2.md §3.7) piece owned by BAP: proves the
Redis-backed rate limiter (`shared/django_observability/rate_limit.py`) actually
throttles, against BAP's own real Redis cache (not a mock) — matching the real
Test Gate wording ("rapid-fire ... spam is throttled").
"""

import json
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


def test_rate_limit_allows_requests_under_the_limit():
    factory = RequestFactory()
    request = factory.post("/dummy")
    for _ in range(3):
        response = _dummy_view(request)
        assert response.status_code == 200


def test_rate_limit_blocks_requests_over_the_limit_with_429():
    factory = RequestFactory()
    request = factory.post("/dummy")
    for _ in range(3):
        _dummy_view(request)
    response = _dummy_view(request)
    assert response.status_code == 429
    body = json.loads(response.content)
    assert body["error"]["code"] == "RATE_LIMITED"
    assert body["error"]["retryable"] is True


def test_rate_limit_is_scoped_per_client_ip():
    """A different IP gets its own independent counter, not blocked by another
    client's spam."""
    factory = RequestFactory()
    spammer = factory.post("/dummy", REMOTE_ADDR="10.0.0.1")
    for _ in range(3):
        _dummy_view(spammer)
    assert _dummy_view(spammer).status_code == 429

    other_client = factory.post("/dummy", REMOTE_ADDR="10.0.0.2")
    assert _dummy_view(other_client).status_code == 200


def test_rate_limit_fails_open_when_redis_itself_is_unreachable():
    """Real gap found and closed at Phase 3 Exit (livetracker2.md, 2026-07-24, live
    "kill Redis" re-test): only `ValueError` was ever caught here — a genuine Redis
    connection failure raises `ConnectionInterrupted` instead, which was uncaught and
    crashed/hung every rate-limited endpoint, including `/search` itself. The request
    must go through, not fail, when the rate limiter's own dependency is down."""
    factory = RequestFactory()
    request = factory.post("/dummy")
    with patch(
        "django_observability.rate_limit.cache.incr", side_effect=ConnectionInterrupted("down")
    ):
        response = _dummy_view(request)
    assert response.status_code == 200


def test_rate_limit_fails_open_on_the_raw_redis_connection_error_too():
    """`cache.incr()`'s fallback path (`cache.set()`, on a cache-miss `ValueError`)
    was observed live raising the raw `redis.exceptions.ConnectionError` instead of
    `ConnectionInterrupted` — a genuinely different exception class, confirmed not a
    subclass of the other. Both must fail open, not just one."""
    factory = RequestFactory()
    request = factory.post("/dummy")
    with (
        patch("django_observability.rate_limit.cache.incr", side_effect=ValueError()),
        patch(
            "django_observability.rate_limit.cache.set",
            side_effect=redis.exceptions.ConnectionError("down"),
        ),
    ):
        response = _dummy_view(request)
    assert response.status_code == 200
