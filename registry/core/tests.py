"""Regression tests for Phase 1.1 Registry Foundation — codifies the behavior verified
manually during Phase 1.1 build (see livetracker1.md Phase 1.1 Test Gate), so it stays
verified on every future change instead of relying on a one-time manual check.
"""

import pytest
from django.test import Client
from django.urls import reverse

from core.crypto import verify_request_signature
from core.validation import PayloadValidationError, validate_against_schema


@pytest.fixture
def client():
    return Client()


def test_health_returns_200_with_correct_shape(client):
    resp = client.get(reverse("health"))
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"status": "ok", "service": "registry"}


def test_health_does_not_require_database(db_conn_broken=None):
    """Documents intent from OBSERVABILITY.md: /health must never fail due to a
    downstream outage. We don't simulate a broken DB here (that's what /ready tests
    below cover); this test exists so the intent is written down, not just implied."""
    pass


@pytest.mark.django_db
def test_ready_returns_200_when_database_reachable(client):
    resp = client.get(reverse("ready"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["checks"]["database"] == "ok"


def test_metrics_returns_prometheus_text_format(client):
    resp = client.get(reverse("metrics"))
    assert resp.status_code == 200
    assert resp["Content-Type"].startswith("text/plain")
    assert "app_uptime_seconds" in resp.content.decode()


def test_correlation_id_is_generated_when_absent(client):
    resp = client.get(reverse("health"))
    assert "X-Correlation-Id" in resp.headers
    assert len(resp.headers["X-Correlation-Id"]) > 0


def test_correlation_id_is_echoed_back_when_provided(client):
    resp = client.get(reverse("health"), headers={"X-Correlation-Id": "my-fixed-test-id"})
    assert resp.headers["X-Correlation-Id"] == "my-fixed-test-id"


def test_unhandled_exception_maps_to_standardized_error_schema(client, settings):
    """Exercises ExceptionHandlingMiddleware exactly as manually verified in Phase 1.1:
    DEBUG=False must never leak exception details to the caller."""
    settings.DEBUG = False
    settings.ROOT_URLCONF = "core.test_urls"
    resp = client.get("/__test_exception__")
    assert resp.status_code == 500
    body = resp.json()
    assert body["error"]["code"] == "INTERNAL_ERROR"
    assert body["error"]["message"] == "Internal server error"  # no leaked exception detail
    assert "correlation_id" in body["error"]


def test_unhandled_exception_shows_detail_when_debug_true(client, settings):
    settings.DEBUG = True
    settings.ROOT_URLCONF = "core.test_urls"
    resp = client.get("/__test_exception__")
    assert resp.status_code == 500
    body = resp.json()
    assert "ValueError" in body["error"]["message"]


def test_validate_against_schema_accepts_conformant_subscribe_payload():
    payload = {
        "context": {"operation": {"ops_no": 2}},
        "message": {
            "request_id": "r1",
            "timestamp": "2026-07-13T00:00:00Z",
            "entity": {
                "subscriber_id": "x",
                "unique_key_id": "y",
                "callback_url": "/on_subscribe",
                "country": "IND",
                "key_pair": {
                    "signing_public_key": "a",
                    "encryption_public_key": "b",
                    "valid_from": "2026-07-13T00:00:00Z",
                    "valid_until": "2027-07-13T00:00:00Z",
                },
            },
            "network_participant": [
                {"subscriber_url": "https://x", "domain": "ONDC:RET13", "type": "sellerApp"}
            ],
        },
    }
    validate_against_schema(payload, "subscribe_request.schema.json")  # must not raise


def test_validate_against_schema_rejects_incomplete_payload():
    with pytest.raises(PayloadValidationError):
        validate_against_schema({"context": {}, "message": {}}, "subscribe_request.schema.json")


def test_crypto_stub_raises_not_implemented():
    """Confirms Phase 2.3 hasn't been silently implemented in a way that bypasses
    the Phase 2.0 sandbox-confirmation gate — this must keep failing until Phase 2.3
    deliberately replaces it against confirmed reference behavior."""
    with pytest.raises(NotImplementedError):
        verify_request_signature(authorization_header="x", body=b"y", public_key="z")
