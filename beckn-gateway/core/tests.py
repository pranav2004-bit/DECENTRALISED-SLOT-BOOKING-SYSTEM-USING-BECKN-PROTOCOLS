"""Regression tests for Phase 1.2 Gateway Foundation and Phase 3.3 Gateway Onboarding."""

import json
from pathlib import Path

import pytest
import responses
from django.test import Client
from django.urls import reverse

from core.crypto import generate_signing_key_pair, sign_outbound_request
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


def test_validate_context_accepts_all_required_fields():
    validate_context(
        {
            "domain": "ONDC:RET13",
            "location": {"country": {"code": "IND"}},
            "action": "search",
            "version": "1.1.0",
            "bap_id": "bap.example.com",
            "bap_uri": "https://bap.example.com",
            "transaction_id": "txn-1",
            "message_id": "msg-1",
            "timestamp": "2026-07-15T00:00:00Z",
        }
    )  # must not raise


def test_validate_context_rejects_missing_field():
    from core.validation import PayloadValidationError

    context = {
        "domain": "ONDC:RET13",
        "location": {"country": {"code": "IND"}},
        "action": "search",
        "version": "1.1.0",
        "bap_uri": "https://bap.example.com",
        "transaction_id": "txn-1",
        "message_id": "msg-1",
        "timestamp": "2026-07-15T00:00:00Z",
    }  # bap_id deliberately omitted
    with pytest.raises(PayloadValidationError, match="bap_id"):
        validate_context(context)


# --- Phase 3.3 Gateway Onboarding ---


@pytest.fixture
def onboarding_settings(settings, tmp_path):
    settings.SIGNING_PRIVATE_KEY_PATH = str(tmp_path / "signing.json")
    settings.ENCRYPTION_PRIVATE_KEY_PATH = str(tmp_path / "encryption.json")
    settings.ONBOARDING_STATE_PATH = str(tmp_path / "onboarding_state.json")
    settings.SUBSCRIBER_ID = "beckn-gateway.example.com"
    settings.UNIQUE_KEY_ID = "key-1"
    settings.SUBSCRIBER_URL = "https://beckn-gateway.example.com"
    from core import participant_keys

    participant_keys.get_signing_keys.cache_clear()
    participant_keys.get_encryption_keys.cache_clear()
    yield settings
    participant_keys.get_signing_keys.cache_clear()
    participant_keys.get_encryption_keys.cache_clear()


def test_participant_keys_persist_across_calls(onboarding_settings):
    """Real persistence, not per-process ephemeral — see BAP's equivalent test for the
    full rationale (identical here)."""
    from core import participant_keys

    pub1, priv1 = participant_keys.get_signing_keys()
    participant_keys.get_signing_keys.cache_clear()
    pub2, priv2 = participant_keys.get_signing_keys()
    assert (pub1, priv1) == (pub2, priv2)
    assert Path(onboarding_settings.SIGNING_PRIVATE_KEY_PATH).exists()


def test_onboarding_state_is_file_backed_not_a_django_model(onboarding_settings):
    """Confirms Gateway's onboarding state survives independent of the ORM — Gateway
    has no database (beckn_gateway_details_v1.1.md §4)."""
    from core import onboarding_state

    onboarding_state.approve("ONDC:RET13")
    assert Path(onboarding_settings.ONBOARDING_STATE_PATH).exists()
    entry = onboarding_state.get_domain_status("ONDC:RET13")
    assert entry["approved_for_subscribe"] is True


def test_onboarding_subscribe_refuses_unconfirmed_domain_code(onboarding_settings):
    """NEG: same guard as BPP — do not guess a domain code and submit it."""
    from core import onboarding_service

    with pytest.raises(onboarding_service.OnboardingError, match="unconfirmed"):
        onboarding_service.submit_subscribe("CONFIRM_BEFORE_USE")


def test_onboarding_subscribe_blocked_without_approval(onboarding_settings):
    from core import onboarding_service

    with pytest.raises(onboarding_service.OnboardingError, match="not approved"):
        onboarding_service.submit_subscribe("ONDC:RET13")


def test_onboarding_approve_then_verification_file_matches_subscribe_request_id(
    onboarding_settings,
):
    from beckn_crypto import verify_domain_ownership_file
    from core import onboarding_service, participant_keys

    onboarding_service.approve("ONDC:RET13")

    with responses.RequestsMock() as rsps:
        captured = {}

        def subscribe_callback(request):
            body = json.loads(request.body)
            captured["request_id"] = body["message"]["request_id"]
            assert body["message"]["network_participant"][0]["type"] == "gateway"
            assert body["context"]["operation"]["ops_no"] == 4
            return (200, {}, json.dumps({"status": "UNDER_SUBSCRIPTION"}))

        rsps.add_callback(
            responses.POST, "http://registry:8000/subscribe", callback=subscribe_callback
        )
        entry = onboarding_service.submit_subscribe("ONDC:RET13")

    assert entry["status"] == "UNDER_SUBSCRIPTION"
    served_content = onboarding_service.get_verification_file_content()
    signing_pub, _ = participant_keys.get_signing_keys()
    assert (
        verify_domain_ownership_file(
            file_content=served_content,
            request_id=captured["request_id"],
            signing_public_key_b64=signing_pub,
        )
        is True
    )


def test_onboarding_subscribe_marks_failed_on_registry_rejection(onboarding_settings):
    from core import onboarding_service, onboarding_state

    onboarding_service.approve("ONDC:RET13")

    with responses.RequestsMock() as rsps:
        rsps.add(
            responses.POST,
            "http://registry:8000/subscribe",
            json={"error": {"code": "DOMAIN_VERIFICATION_FAILED", "message": "no dice"}},
            status=422,
        )
        with pytest.raises(onboarding_service.OnboardingError):
            onboarding_service.submit_subscribe("ONDC:RET13")

    entry = onboarding_state.get_domain_status("ONDC:RET13")
    assert entry["status"] == "FAILED"
    assert "DOMAIN_VERIFICATION_FAILED" in entry["last_error"]


def test_on_subscribe_view_decrypts_challenge_and_marks_subscribed(onboarding_settings, client):
    from beckn_crypto import encrypt_challenge, generate_encryption_key_pair
    from core import onboarding_state, participant_keys

    onboarding_state.set_status("ONDC:RET13", "UNDER_SUBSCRIPTION")
    gateway_encryption_pub, gateway_encryption_priv = participant_keys.get_encryption_keys()
    registry_encryption_pub, registry_encryption_priv = generate_encryption_key_pair()

    encrypted = encrypt_challenge(
        challenge="the-secret-answer",
        own_private_key_b64=registry_encryption_priv,
        peer_public_key_b64_der=gateway_encryption_pub,
    )

    with responses.RequestsMock() as rsps:
        rsps.add(
            responses.GET,
            "http://registry:8000/identity",
            json={"signing_public_key": "irrelevant", "encryption_public_key": registry_encryption_pub},
            status=200,
        )
        resp = client.post(
            reverse("on_subscribe"),
            data=json.dumps(
                {"subscriber_id": "beckn-gateway.example.com", "challenge": encrypted}
            ),
            content_type="application/json",
        )

    assert resp.status_code == 200
    assert resp.json() == {"answer": "the-secret-answer"}
    entry = onboarding_state.get_domain_status("ONDC:RET13")
    assert entry["status"] == "SUBSCRIBED"


def test_ondc_site_verification_view_returns_404_before_any_verification_requested(
    onboarding_settings, client
):
    resp = client.get(reverse("ondc-site-verification"))
    assert resp.status_code == 404


def test_ondc_site_verification_view_serves_signed_content(onboarding_settings, client):
    from core import onboarding_service

    onboarding_service.request_domain_verification(request_id="req-xyz")
    resp = client.get(reverse("ondc-site-verification"))
    assert resp.status_code == 200
    assert "Signed Unique Request ID:" in resp.content.decode()


# --- Phase 3.4 Trust Establishment ---


def test_verify_participant_signature_accepts_a_genuine_subscribed_participant(
    onboarding_settings,
):
    from core import trust
    from core.crypto import sign_outbound_request

    peer_pub, peer_priv = generate_signing_key_pair()
    body = b'{"hello": "world"}'
    header = sign_outbound_request(
        body=body, subscriber_id="peer.example.com", unique_key_id="key-1", signing_private_key_b64=peer_priv
    )

    with responses.RequestsMock() as rsps:
        rsps.add(
            responses.POST,
            "http://registry:8000/lookup",
            json=[{"subscriber_id": "peer.example.com", "status": "SUBSCRIBED", "signing_public_key": peer_pub}],
            status=200,
        )
        assert trust.verify_participant_signature(authorization_header=header, body=body) is True


def test_verify_participant_signature_rejects_a_forged_signature(onboarding_settings):
    from core import trust
    from core.crypto import sign_outbound_request

    real_pub, _ = generate_signing_key_pair()
    _, attacker_priv = generate_signing_key_pair()
    body = b'{"hello": "world"}'
    forged_header = sign_outbound_request(
        body=body, subscriber_id="peer.example.com", unique_key_id="key-1", signing_private_key_b64=attacker_priv
    )

    with responses.RequestsMock() as rsps:
        rsps.add(
            responses.POST,
            "http://registry:8000/lookup",
            json=[{"subscriber_id": "peer.example.com", "status": "SUBSCRIBED", "signing_public_key": real_pub}],
            status=200,
        )
        with pytest.raises(trust.TrustEstablishmentError):
            trust.verify_participant_signature(authorization_header=forged_header, body=body)


def test_onboarding_reset_clears_domain_back_to_not_started(onboarding_settings):
    from django.core.management import call_command

    from core import onboarding_state

    onboarding_state.set_status("ONDC:RET13", "FAILED", last_error="something went wrong")
    call_command("onboarding_reset", "ONDC:RET13")

    entry = onboarding_state.get_domain_status("ONDC:RET13")
    assert entry["status"] == "NOT_STARTED"
    assert entry["approved_for_subscribe"] is False
    assert entry["last_error"] == ""


# --- Phase 4.1 End-to-End Trust Chain Verification ---


def _build_search_context(*, bap_id="bap.example.com", domain="ONDC:RET13"):
    return {
        "context": {
            "domain": domain,
            "location": {"country": {"code": "IND"}},
            "action": "search",
            "version": "1.1.0",
            "bap_id": bap_id,
            "bap_uri": f"https://{bap_id}",
            "transaction_id": "txn-1",
            "message_id": "msg-1",
            "timestamp": "2026-07-15T00:00:00Z",
        },
        "message": {"intent": {}},
    }


def _lookup_callback(bap_pub, bpp_entries):
    """Registry Lookup is called twice by route_search: once by trust verification
    (filtered by subscriber_id) and once for BPP discovery (filtered by domain+type).
    A single callback keyed on the request body handles both without relying on call
    order, which `responses` doesn't guarantee."""

    def callback(request):
        filters = json.loads(request.body)
        if "subscriber_id" in filters:
            return (
                200,
                {},
                json.dumps(
                    [
                        {
                            "subscriber_id": filters["subscriber_id"],
                            "status": "SUBSCRIBED",
                            "signing_public_key": bap_pub,
                        }
                    ]
                ),
            )
        return (200, {}, json.dumps(bpp_entries))

    return callback


def test_search_view_routes_to_subscribed_bpps_for_a_validly_signed_request(client):
    bap_pub, bap_priv = generate_signing_key_pair()
    payload = _build_search_context()
    body = json.dumps(payload).encode()
    header = sign_outbound_request(
        body=body, subscriber_id="bap.example.com", unique_key_id="key-1", signing_private_key_b64=bap_priv
    )
    bpp_entries = [{"subscriber_id": "bpp.example.com", "url": "https://bpp.example.com", "status": "SUBSCRIBED"}]

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST, "http://registry:8000/lookup", callback=_lookup_callback(bap_pub, bpp_entries)
        )
        resp = client.post(
            reverse("search"), data=body, content_type="application/json", HTTP_AUTHORIZATION=header
        )

    assert resp.status_code == 200
    assert resp.json() == {"routed_to": [{"subscriber_id": "bpp.example.com", "url": "https://bpp.example.com"}]}


def test_search_view_rejects_tampered_signature(client):
    bap_pub, _ = generate_signing_key_pair()
    _, attacker_priv = generate_signing_key_pair()
    payload = _build_search_context()
    body = json.dumps(payload).encode()
    forged_header = sign_outbound_request(
        body=body, subscriber_id="bap.example.com", unique_key_id="key-1", signing_private_key_b64=attacker_priv
    )

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST, "http://registry:8000/lookup", callback=_lookup_callback(bap_pub, [])
        )
        resp = client.post(
            reverse("search"), data=body, content_type="application/json", HTTP_AUTHORIZATION=forged_header
        )

    assert resp.status_code == 401


def test_search_view_rejects_bap_id_impersonation(client):
    """NEG: a validly-signed request whose signer identity doesn't match the claimed
    context.bap_id must be rejected — a valid signature for participant A can't be used
    to claim to be participant B."""
    bap_pub, bap_priv = generate_signing_key_pair()
    payload = _build_search_context(bap_id="someone-else.example.com")
    body = json.dumps(payload).encode()
    header = sign_outbound_request(
        body=body, subscriber_id="bap.example.com", unique_key_id="key-1", signing_private_key_b64=bap_priv
    )

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST, "http://registry:8000/lookup", callback=_lookup_callback(bap_pub, [])
        )
        resp = client.post(
            reverse("search"), data=body, content_type="application/json", HTTP_AUTHORIZATION=header
        )

    assert resp.status_code == 401


def test_search_view_rejects_missing_context_field(client):
    payload = _build_search_context()
    del payload["context"]["bap_id"]
    body = json.dumps(payload).encode()

    resp = client.post(reverse("search"), data=body, content_type="application/json", HTTP_AUTHORIZATION="irrelevant")
    assert resp.status_code == 400
