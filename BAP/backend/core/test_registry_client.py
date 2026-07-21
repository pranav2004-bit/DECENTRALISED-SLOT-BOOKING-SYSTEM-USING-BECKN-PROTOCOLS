"""Phase 3.6 Test Gate (livetracker2.md §3.6) piece owned by BAP: proves the
circuit-breaker isolation fix — get_client() (Registry-only) and get_gateway_client()
(Gateway-only) must be genuinely separate ResilientHttpClient instances with separate
circuit_breaker_key values, so a Gateway outage can no longer trip Registry calls
(and vice versa). Before this fix, every *_service.py module's Gateway-bound call
reused get_client(), conflating the two failure domains — a real bug found by design
audit before this phase's implementation, not a hypothetical.
"""

from core import registry_client


def test_gateway_client_is_a_separate_instance_from_registry_client():
    assert registry_client.get_gateway_client() is not registry_client.get_client()


def test_gateway_client_and_registry_client_have_different_breaker_keys():
    gateway_key = registry_client.get_gateway_client()._circuit_breaker._failures_key
    registry_key = registry_client.get_client()._circuit_breaker._failures_key
    assert gateway_key != registry_key
    assert gateway_key == "bap-gateway-client:cb:failures"
    assert registry_key == "bap-registry-client:cb:failures"


def test_get_gateway_client_is_a_singleton():
    assert registry_client.get_gateway_client() is registry_client.get_gateway_client()


def test_get_client_is_a_singleton():
    assert registry_client.get_client() is registry_client.get_client()
