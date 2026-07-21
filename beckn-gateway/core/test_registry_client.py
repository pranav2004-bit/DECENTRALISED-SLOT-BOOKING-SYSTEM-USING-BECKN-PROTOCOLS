"""Phase 3.6 Test Gate (livetracker2.md §3.6) piece owned by Gateway: proves the
circuit-breaker isolation fix on Gateway's side. Before this fix, every
dispatch_X/relay_on_X in routing.py reused registry_client.get_client() — the same
single client used for Registry lookups — for its outbound call to every individual
BPP/BAP too. One genuinely-down BPP tripping that shared breaker would then also
fail-fast routing to every OTHER, healthy BPP/BAP (and to Registry itself) — the
opposite of what a circuit breaker exists to prevent (found via design audit before
this phase's implementation, not a hypothetical). get_participant_client(subscriber_id)
fixes this with one isolated client per real counterparty.
"""

from core import registry_client


def test_participant_client_is_a_separate_instance_from_registry_client():
    assert registry_client.get_participant_client("bpp-1.local") is not registry_client.get_client()


def test_participant_client_is_a_singleton_per_subscriber_id():
    assert registry_client.get_participant_client("bpp-1.local") is registry_client.get_participant_client(
        "bpp-1.local"
    )


def test_participant_clients_for_different_subscriber_ids_are_isolated_instances():
    """The core of the fix: two different BPPs/BAPs must never share a breaker, so
    one being down cannot spuriously fail-fast routing to the other."""
    client_a = registry_client.get_participant_client("bpp-1.local")
    client_b = registry_client.get_participant_client("bpp-2.local")
    assert client_a is not client_b
    assert client_a._circuit_breaker is not client_b._circuit_breaker
