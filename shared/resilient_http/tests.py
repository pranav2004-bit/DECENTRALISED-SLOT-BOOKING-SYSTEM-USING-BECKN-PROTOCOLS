import time

import pytest
import requests
import responses

from .circuit_breaker import CircuitBreaker, CircuitOpenError
from .client import ResilientHttpClient


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
