"""Phase 3.7 Test Gate (livetracker2.md §3.7) piece owned by BAP: proves the
Redis-backed rate limiter (`shared/django_observability/rate_limit.py`) actually
throttles, against BAP's own real Redis cache (not a mock) — matching the real
Test Gate wording ("rapid-fire ... spam is throttled").
"""

import json

import pytest
from django.core.cache import cache
from django.http import JsonResponse
from django.test import RequestFactory
from django_observability.rate_limit import rate_limit


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
