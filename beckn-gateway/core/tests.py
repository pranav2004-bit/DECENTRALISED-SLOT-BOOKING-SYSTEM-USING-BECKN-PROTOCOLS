"""Regression tests for Phase 1.2 Gateway Foundation."""

import pytest
from django.test import Client
from django.urls import reverse

from core.crypto import generate_signing_key_pair, sign_outbound_request
from core.registry_client import lookup
from core.validation import validate_context


@pytest.fixture
def client():
    return Client()


def test_health_returns_200_with_correct_shape(client):
    resp = client.get(reverse("health"))
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "service": "beckn-gateway"}


def test_ready_returns_200_with_no_hard_dependencies(client):
    """Gateway is stateless (beckn_gateway_details_v1.1.md §4) — /ready must report
    ok with an empty checks dict, not fail due to having nothing to check."""
    resp = client.get(reverse("ready"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["checks"] == {}


def test_metrics_returns_prometheus_text_format(client):
    resp = client.get(reverse("metrics"))
    assert resp.status_code == 200
    assert "app_uptime_seconds" in resp.content.decode()


def test_correlation_id_generated_and_echoed(client):
    resp = client.get(reverse("health"), headers={"X-Correlation-Id": "gw-test-id"})
    assert resp.headers["X-Correlation-Id"] == "gw-test-id"


def test_unhandled_exception_maps_to_standardized_error_schema(client, settings):
    settings.DEBUG = False
    settings.ROOT_URLCONF = "core.test_urls"
    resp = client.get("/__test_exception__")
    assert resp.status_code == 500
    body = resp.json()
    assert body["error"]["code"] == "INTERNAL_ERROR"
    assert body["error"]["message"] == "Internal server error"


def test_sign_outbound_request_produces_a_proxy_authorization_ready_value():
    """Confirms real signing works and the header VALUE round-trips through
    verify_request_signature (the value format is identical to Authorization's — the
    caller is responsible for setting it under the Proxy-Authorization header name,
    per protocol_compliance_notes_v1.1.md §C.3; not this function's job to name it)."""
    from beckn_crypto import verify_request_signature

    public_b64, private_b64 = generate_signing_key_pair()
    body = b'{"hello": "world"}'
    header_value = sign_outbound_request(
        body=body,
        subscriber_id="beckn-gateway.example.com",
        unique_key_id="key-1",
        signing_private_key_b64=private_b64,
    )
    assert (
        verify_request_signature(
            authorization_header=header_value, body=body, public_key_b64=public_b64
        )
        is True
    )


def test_registry_client_stub_raises_not_implemented():
    with pytest.raises(NotImplementedError):
        lookup({"domain": "ONDC:RET13"})


def test_validation_stub_raises_not_implemented():
    with pytest.raises(NotImplementedError):
        validate_context({})
