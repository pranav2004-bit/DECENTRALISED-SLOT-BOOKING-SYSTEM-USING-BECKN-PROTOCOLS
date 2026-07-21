"""Phase 3.6 Test Gate (livetracker2.md §3.6) piece owned by BPP: proves the
circuit-breaker isolation fix — see BAP/backend/core/test_registry_client.py for the
full rationale (a Gateway outage previously tripping the shared Registry breaker too,
found via design audit before implementation, mirrored here for BPP)."""

from core import registry_client


def test_gateway_client_is_a_separate_instance_from_registry_client():
    assert registry_client.get_gateway_client() is not registry_client.get_client()


def test_gateway_client_and_registry_client_have_different_breaker_keys():
    gateway_key = registry_client.get_gateway_client()._circuit_breaker._failures_key
    registry_key = registry_client.get_client()._circuit_breaker._failures_key
    assert gateway_key != registry_key
    assert gateway_key == "bpp-gateway-client:cb:failures"
    assert registry_key == "bpp-registry-client:cb:failures"


def test_get_gateway_client_is_a_singleton():
    assert registry_client.get_gateway_client() is registry_client.get_gateway_client()


def test_get_client_is_a_singleton():
    assert registry_client.get_client() is registry_client.get_client()
