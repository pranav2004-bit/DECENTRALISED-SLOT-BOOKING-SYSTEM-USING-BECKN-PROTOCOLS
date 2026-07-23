import os
import time

import pytest
import requests
import responses

from .circuit_breaker import CircuitBreaker, CircuitOpenError, RedisCircuitBreaker
from .client import ResilientHttpClient

TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6390/0")


@pytest.fixture
def redis_client():
    redis = pytest.importorskip("redis")
    client = redis.Redis.from_url(TEST_REDIS_URL)
    client.flushdb()
    yield client
    client.flushdb()


def test_circuit_stays_closed_under_threshold():
    cb = CircuitBreaker(failure_threshold=3, reset_timeout_seconds=10)
    cb.record_failure()
    cb.record_failure()
    assert cb.state == CircuitBreaker.CLOSED
    cb.before_call()  # must not raise


def test_circuit_opens_at_threshold_and_fails_fast():
    cb = CircuitBreaker(failure_threshold=3, reset_timeout_seconds=10)
    cb.record_failure()
    cb.record_failure()
    cb.record_failure()
    assert cb.state == CircuitBreaker.OPEN
    with pytest.raises(CircuitOpenError):
        cb.before_call()


def test_circuit_transitions_to_half_open_after_cooldown():
    cb = CircuitBreaker(failure_threshold=1, reset_timeout_seconds=0.1)
    cb.record_failure()
    assert cb.state == CircuitBreaker.OPEN
    time.sleep(0.15)
    assert cb.state == CircuitBreaker.HALF_OPEN
    cb.before_call()  # half-open must allow a trial call through


def test_circuit_reopens_on_failure_during_half_open():
    cb = CircuitBreaker(failure_threshold=1, reset_timeout_seconds=0.1)
    cb.record_failure()
    time.sleep(0.15)
    assert cb.state == CircuitBreaker.HALF_OPEN
    cb.record_failure()
    assert cb.state == CircuitBreaker.OPEN


def test_circuit_closes_on_success():
    cb = CircuitBreaker(failure_threshold=3, reset_timeout_seconds=10)
    cb.record_failure()
    cb.record_failure()
    cb.record_success()
    assert cb.state == CircuitBreaker.CLOSED


@responses.activate
def test_client_get_succeeds_and_records_success():
    responses.add(responses.GET, "http://x.test/ok", json={"ok": True}, status=200)
    client = ResilientHttpClient(timeout_seconds=1, max_retries=0)
    resp = client.get("http://x.test/ok")
    assert resp.status_code == 200
    assert client.circuit_state == CircuitBreaker.CLOSED


@responses.activate
def test_client_opens_circuit_after_repeated_server_errors():
    """With max_retries=0 and status_forcelist including 500, urllib3 raises
    RetryError on a forced-retry status rather than passing the response through —
    real, verified urllib3 behavior, not a bug. Our client's `except
    requests.exceptions.RequestException` catches it (RetryError is a subclass)
    and still records it as a circuit-breaker failure, which is what we're testing."""
    responses.add(responses.GET, "http://x.test/fail", json={"error": "boom"}, status=500)
    client = ResilientHttpClient(timeout_seconds=1, max_retries=0, circuit_breaker_threshold=2)
    with pytest.raises(requests.exceptions.RequestException):
        client.get("http://x.test/fail")
    with pytest.raises(requests.exceptions.RequestException):
        client.get("http://x.test/fail")
    assert client.circuit_state == CircuitBreaker.OPEN
    with pytest.raises(CircuitOpenError):
        client.get("http://x.test/fail")


@responses.activate
def test_client_retries_on_5xx_before_returning():
    responses.add(responses.GET, "http://x.test/flaky", status=503)
    responses.add(responses.GET, "http://x.test/flaky", status=503)
    responses.add(responses.GET, "http://x.test/flaky", json={"ok": True}, status=200)
    client = ResilientHttpClient(timeout_seconds=1, max_retries=3, backoff_factor=0.01)
    resp = client.get("http://x.test/flaky")
    assert resp.status_code == 200
    assert len(responses.calls) == 3


# --- RedisCircuitBreaker: same semantics as CircuitBreaker, shared across instances ---


def test_redis_circuit_stays_closed_under_threshold(redis_client):
    cb = RedisCircuitBreaker(
        redis_client=redis_client, key_prefix="test-cb-1", failure_threshold=3, reset_timeout_seconds=10
    )
    cb.record_failure()
    cb.record_failure()
    assert cb.state == RedisCircuitBreaker.CLOSED
    cb.before_call()  # must not raise


def test_redis_circuit_opens_at_threshold_and_fails_fast(redis_client):
    cb = RedisCircuitBreaker(
        redis_client=redis_client, key_prefix="test-cb-2", failure_threshold=3, reset_timeout_seconds=10
    )
    cb.record_failure()
    cb.record_failure()
    cb.record_failure()
    assert cb.state == RedisCircuitBreaker.OPEN
    with pytest.raises(CircuitOpenError):
        cb.before_call()


def test_redis_circuit_transitions_to_half_open_after_cooldown(redis_client):
    cb = RedisCircuitBreaker(
        redis_client=redis_client, key_prefix="test-cb-3", failure_threshold=1, reset_timeout_seconds=0.1
    )
    cb.record_failure()
    assert cb.state == RedisCircuitBreaker.OPEN
    time.sleep(0.15)
    assert cb.state == RedisCircuitBreaker.HALF_OPEN
    cb.before_call()  # half-open must allow a trial call through


def test_redis_circuit_reopens_on_failure_during_half_open(redis_client):
    cb = RedisCircuitBreaker(
        redis_client=redis_client, key_prefix="test-cb-4", failure_threshold=1, reset_timeout_seconds=0.1
    )
    cb.record_failure()
    time.sleep(0.15)
    assert cb.state == RedisCircuitBreaker.HALF_OPEN
    cb.record_failure()
    assert cb.state == RedisCircuitBreaker.OPEN


def test_redis_circuit_closes_on_success(redis_client):
    cb = RedisCircuitBreaker(
        redis_client=redis_client, key_prefix="test-cb-5", failure_threshold=3, reset_timeout_seconds=10
    )
    cb.record_failure()
    cb.record_failure()
    cb.record_success()
    assert cb.state == RedisCircuitBreaker.CLOSED


def test_redis_circuit_state_is_shared_across_separate_instances(redis_client):
    """The actual bug this exists to fix: two independent RedisCircuitBreaker
    instances (standing in for two gunicorn worker processes) pointed at the same
    key_prefix must see each other's failures — unlike the in-memory CircuitBreaker,
    where each process's breaker is completely blind to the others'."""
    worker_a = RedisCircuitBreaker(
        redis_client=redis_client, key_prefix="test-cb-shared", failure_threshold=3, reset_timeout_seconds=10
    )
    worker_b = RedisCircuitBreaker(
        redis_client=redis_client, key_prefix="test-cb-shared", failure_threshold=3, reset_timeout_seconds=10
    )
    worker_a.record_failure()
    worker_b.record_failure()
    worker_a.record_failure()  # 3rd failure total, split across two "processes"
    assert worker_a.state == RedisCircuitBreaker.OPEN
    assert worker_b.state == RedisCircuitBreaker.OPEN  # worker_b sees worker_a's failures
    with pytest.raises(CircuitOpenError):
        worker_b.before_call()


# --- RedisCircuitBreaker fails open on its OWN Redis outage (livetracker2.md §3.11
# follow-up) — found live: killing an app's Redis crashed *every* inbound request with
# a raw, unhandled 500, because before_call() couldn't even decide whether to attempt
# the real downstream call without Redis answering first. A circuit breaker must not
# become a second single point of failure for its own dependency. ---


class _DeadRedisClient:
    """Stands in for a real redis-py client that can't reach Redis — every method
    raises `redis.exceptions.ConnectionError`, matching what a genuine outage
    produces (confirmed live; see `circuit_breaker.RedisCircuitBreaker`'s own
    docstring). No real Redis connection needed to exercise the fail-open path."""

    def __getattr__(self, _name):
        def _raise(*args, **kwargs):
            redis = pytest.importorskip("redis")
            raise redis.exceptions.ConnectionError("simulated Redis outage")

        return _raise


def test_redis_circuit_fails_open_when_redis_itself_is_unreachable():
    cb = RedisCircuitBreaker(
        redis_client=_DeadRedisClient(),
        key_prefix="test-cb-dead-redis-1",
        failure_threshold=3,
        reset_timeout_seconds=10,
    )
    assert cb.state == RedisCircuitBreaker.CLOSED
    cb.before_call()  # must not raise


def test_redis_circuit_record_success_does_not_raise_when_redis_is_unreachable():
    cb = RedisCircuitBreaker(redis_client=_DeadRedisClient(), key_prefix="test-cb-dead-redis-2")
    cb.record_success()  # must not raise


def test_redis_circuit_record_failure_does_not_raise_when_redis_is_unreachable():
    cb = RedisCircuitBreaker(redis_client=_DeadRedisClient(), key_prefix="test-cb-dead-redis-3")
    cb.record_failure()  # must not raise


@responses.activate
def test_client_with_dead_redis_still_attempts_the_real_call_and_surfaces_its_own_error():
    """Exercised through the real ResilientHttpClient, matching how e.g.
    registry_client.py actually constructs one. Redis being down must not replace
    the real downstream failure with a misleading Redis-internal error — the caller
    still sees the real, honest error from the actual call it made."""
    responses.add(responses.GET, "http://x.test/real-failure", status=503)
    client = ResilientHttpClient(
        timeout_seconds=1,
        max_retries=0,
        redis_client=_DeadRedisClient(),
        circuit_breaker_key="test-dead-redis-client",
    )
    with pytest.raises(requests.exceptions.RequestException):
        client.get("http://x.test/real-failure")
    # before_call() and record_failure() both silently failed open/no-op rather than
    # raising over the dead Redis — the breaker stayed CLOSED, not crashed.
    assert client.circuit_state == RedisCircuitBreaker.CLOSED


@responses.activate
def test_client_with_redis_backend_shares_state_across_client_instances(redis_client):
    """Same real bug, exercised through the actual ResilientHttpClient a
    registry_client.py would construct — two client instances (one per simulated
    worker process) sharing a circuit breaker via redis_client."""
    responses.add(responses.GET, "http://x.test/fail", json={"error": "boom"}, status=500)
    client_a = ResilientHttpClient(
        timeout_seconds=1, max_retries=0, circuit_breaker_threshold=2,
        redis_client=redis_client, circuit_breaker_key="test-shared-client",
    )
    client_b = ResilientHttpClient(
        timeout_seconds=1, max_retries=0, circuit_breaker_threshold=2,
        redis_client=redis_client, circuit_breaker_key="test-shared-client",
    )
    with pytest.raises(requests.exceptions.RequestException):
        client_a.get("http://x.test/fail")
    with pytest.raises(requests.exceptions.RequestException):
        client_b.get("http://x.test/fail")  # 2nd failure, different instance -> trips it
    assert client_a.circuit_state == RedisCircuitBreaker.OPEN
    with pytest.raises(CircuitOpenError):
        client_a.get("http://x.test/fail")  # fails fast, no real request attempted
