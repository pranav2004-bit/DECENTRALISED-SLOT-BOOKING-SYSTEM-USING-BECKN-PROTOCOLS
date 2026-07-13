"""Regression tests for Phase 1.4 BPP Foundation."""

import pytest
from django.test import Client
from django.urls import reverse

from core.auth import authenticate_provider_session, authorize_provider_action
from core.crypto import generate_signing_key_pair
from core.events import get_event_bus
from core.registry_client import subscribe


@pytest.fixture
def client():
    return Client()


def test_health_returns_200(client):
    resp = client.get(reverse("health"))
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "service": "bpp-backend"}


@pytest.mark.django_db
def test_ready_checks_database_and_cache(client):
    resp = client.get(reverse("ready"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["checks"]["database"] == "ok"
    assert body["checks"]["cache"] == "ok"


def test_metrics_returns_prometheus_format(client):
    resp = client.get(reverse("metrics"))
    assert "app_uptime_seconds" in resp.content.decode()


def test_unhandled_exception_maps_to_standardized_error_schema(client, settings):
    settings.DEBUG = False
    settings.ROOT_URLCONF = "core.test_urls"
    resp = client.get("/__test_exception__")
    assert resp.status_code == 500
    assert resp.json()["error"]["code"] == "INTERNAL_ERROR"


def test_event_bus_publish_and_consume_round_trip():
    bus = get_event_bus()
    bus._redis.delete(bus.queue_name, bus.dlq_name)
    event_id = bus.publish("test.event", {"key": "value"})
    event = bus.consume_one(timeout_seconds=2)
    assert event is not None
    assert event["event_id"] == event_id
    bus._redis.delete(bus.queue_name, bus.dlq_name)


def test_event_bus_dlq_receives_failed_event():
    """livetracker1.md Phase 1.4 EDGE test case: 'event bus DLQ receives a
    deliberately-failed internal event'."""
    from event_bus import process_with_dlq

    bus = get_event_bus()
    bus._redis.delete(bus.queue_name, bus.dlq_name)
    bus.publish("test.will_fail", {"x": 1})
    event = bus.consume_one(timeout_seconds=2)

    def failing_handler(_e):
        raise RuntimeError("deliberate failure for DLQ test")

    success = process_with_dlq(bus, event, failing_handler)
    assert success is False
    assert bus.dlq_length() == 1
    bus._redis.delete(bus.queue_name, bus.dlq_name)


def test_crypto_stub_raises_not_implemented():
    with pytest.raises(NotImplementedError):
        generate_signing_key_pair()


def test_registry_client_stub_raises_not_implemented():
    with pytest.raises(NotImplementedError):
        subscribe({})


def test_authentication_stub_raises_not_implemented():
    with pytest.raises(NotImplementedError):
        authenticate_provider_session(session_token="fake-token-for-test")


def test_authorization_stub_raises_not_implemented():
    with pytest.raises(NotImplementedError):
        authorize_provider_action(provider_id="p1", action="update_catalog")
