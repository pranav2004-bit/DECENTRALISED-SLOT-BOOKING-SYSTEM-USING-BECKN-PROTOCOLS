"""Regression tests for Phase 1.2 Gateway Foundation and Phase 3.3 Gateway Onboarding."""

import json
from pathlib import Path
from unittest.mock import ANY, patch

import pytest
import responses
from django.test import Client
from django.urls import reverse

from core import routing
from core.crypto import generate_signing_key_pair, sign_outbound_request


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


def test_sign_outbound_request_produces_an_x_gateway_authorization_ready_value():
    """Confirms real signing works and the header VALUE round-trips through
    verify_request_signature (the value format is identical to Authorization's — the
    caller is responsible for setting it under the X-Gateway-Authorization header name,
    per protocol_compliance_notes_v1.1.md §C.3/§H.3; not this function's job to name it)."""
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
            json={
                "signing_public_key": "irrelevant",
                "encryption_public_key": registry_encryption_pub,
            },
            status=200,
        )
        resp = client.post(
            reverse("on_subscribe"),
            data=json.dumps({"subscriber_id": "beckn-gateway.example.com", "challenge": encrypted}),
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
        body=body,
        subscriber_id="peer.example.com",
        unique_key_id="key-1",
        signing_private_key_b64=peer_priv,
    )

    with responses.RequestsMock() as rsps:
        rsps.add(
            responses.POST,
            "http://registry:8000/lookup",
            json=[
                {
                    "subscriber_id": "peer.example.com",
                    "status": "SUBSCRIBED",
                    "signing_public_key": peer_pub,
                }
            ],
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
        body=body,
        subscriber_id="peer.example.com",
        unique_key_id="key-1",
        signing_private_key_b64=attacker_priv,
    )

    with responses.RequestsMock() as rsps:
        rsps.add(
            responses.POST,
            "http://registry:8000/lookup",
            json=[
                {
                    "subscriber_id": "peer.example.com",
                    "status": "SUBSCRIBED",
                    "signing_public_key": real_pub,
                }
            ],
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


def _build_on_search_context(*, bap_id="bap.example.com", bpp_id="bpp.example.com"):
    return {
        "context": {
            "domain": "ONDC:RET13",
            "location": {"country": {"code": "IND"}},
            "action": "on_search",
            "version": "1.1.0",
            "bap_id": bap_id,
            "bap_uri": f"https://{bap_id}",
            "bpp_id": bpp_id,
            "bpp_uri": f"https://{bpp_id}",
            "transaction_id": "txn-1",
            "message_id": "msg-1",
            "timestamp": "2026-07-15T00:00:00Z",
        },
        "message": {"catalog": {"descriptor": {"name": "Beauty Catalog"}, "providers": []}},
    }


def _lookup_callback(bap_pub, bpp_entries):
    """Registry Lookup is called twice by validate_and_ack_search: once by trust
    verification (filtered by subscriber_id) and once for BPP discovery (filtered by
    domain+type). A single callback keyed on the request body handles both without
    relying on call order, which `responses` doesn't guarantee."""

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


@patch("core.routing.dispatch_search_in_background")
def test_search_view_acks_immediately_for_a_validly_signed_request(mock_dispatch, client):
    """The view returns the real ACK envelope synchronously — it does not wait on
    dispatch_search's background BPP forwarding, per the confirmed async mandate
    (protocol_compliance_notes_v1.1.md §H.1). dispatch_search_in_background is mocked
    deliberately: forwarding behavior itself is tested directly against
    routing.dispatch_search below (synchronous, no thread). Without this mock, the
    view fires a genuine daemon thread that outlives this test and the whole pytest
    process, making a real DNS lookup against whatever bap_uri the payload happened
    to use."""
    bap_pub, bap_priv = generate_signing_key_pair()
    payload = _build_search_context()
    body = json.dumps(payload).encode()
    header = sign_outbound_request(
        body=body,
        subscriber_id="bap.example.com",
        unique_key_id="key-1",
        signing_private_key_b64=bap_priv,
    )

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST,
            "http://registry:8000/lookup",
            callback=_lookup_callback(bap_pub, []),
        )
        resp = client.post(
            reverse("search"), data=body, content_type="application/json", HTTP_AUTHORIZATION=header
        )

    assert resp.status_code == 200
    assert resp.json() == {
        "context": payload["context"],
        "message": {"ack": {"status": "ACK"}},
    }
    mock_dispatch.assert_called_once_with(payload=payload, authorization_header=header)


def test_dispatch_search_forwards_to_each_subscribed_bpp_with_both_signatures(settings):
    """Real forwarding behavior, called directly and synchronously (not through the
    view's background thread) so the assertion isn't racing anything. Confirms the
    forwarded body is byte-identical to the original payload, the original
    Authorization header is preserved untouched, and a fresh X-Gateway-Authorization
    is added (protocol_compliance_notes_v1.1.md §H.3). SUBSCRIBER_ID/UNIQUE_KEY_ID are
    set explicitly here rather than relied on from the ambient environment — CI doesn't
    set them for this app (they default to ""), which made an earlier version of this
    test's `"gateway.local" in ...` assertion pass locally but fail in CI."""
    settings.SUBSCRIBER_ID = "gateway.local"
    settings.UNIQUE_KEY_ID = "key1"
    payload = _build_search_context()
    original_auth_header = 'Signature keyId="bap.example.com|key-1|ed25519",...(original)'
    bpp_entries = [
        {
            "subscriber_id": "bpp.example.com",
            "url": "https://bpp.example.com",
            "status": "SUBSCRIBED",
        },
        {
            "subscriber_id": "inactive-bpp.example.com",
            "url": "https://inactive-bpp.example.com",
            "status": "UNDER_SUBSCRIPTION",
        },
    ]

    captured_requests = []

    def bpp_search_callback(request):
        captured_requests.append(request)
        return (200, {}, json.dumps({"message": {"ack": {"status": "ACK"}}}))

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST,
            "http://registry:8000/lookup",
            callback=lambda request: (200, {}, json.dumps(bpp_entries)),
        )
        rsps.add_callback(
            responses.POST, "https://bpp.example.com/search", callback=bpp_search_callback
        )
        routing.dispatch_search(payload=payload, authorization_header=original_auth_header)

    assert len(captured_requests) == 1  # the UNDER_SUBSCRIPTION entry must not be forwarded to
    forwarded = captured_requests[0]
    assert json.loads(forwarded.body) == payload
    assert forwarded.headers["Authorization"] == original_auth_header
    assert forwarded.headers["X-Gateway-Authorization"].startswith("Signature keyId=")
    assert "gateway.local" in forwarded.headers["X-Gateway-Authorization"]


def test_dispatch_search_does_not_raise_when_a_bpp_is_unreachable():
    """A single unreachable BPP must not blow up the whole dispatch — failures are
    logged, not raised, so one bad BPP can't affect others or crash the background
    thread the view fired this from."""
    payload = _build_search_context()
    bpp_entries = [
        {
            "subscriber_id": "bpp.example.com",
            "url": "https://bpp.example.com",
            "status": "SUBSCRIBED",
        }
    ]

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST,
            "http://registry:8000/lookup",
            callback=lambda request: (200, {}, json.dumps(bpp_entries)),
        )
        rsps.add(responses.POST, "https://bpp.example.com/search", status=503)
        # must not raise:
        routing.dispatch_search(payload=payload, authorization_header="irrelevant")


def test_search_view_rejects_tampered_signature(client):
    bap_pub, _ = generate_signing_key_pair()
    _, attacker_priv = generate_signing_key_pair()
    payload = _build_search_context()
    body = json.dumps(payload).encode()
    forged_header = sign_outbound_request(
        body=body,
        subscriber_id="bap.example.com",
        unique_key_id="key-1",
        signing_private_key_b64=attacker_priv,
    )

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST, "http://registry:8000/lookup", callback=_lookup_callback(bap_pub, [])
        )
        resp = client.post(
            reverse("search"),
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION=forged_header,
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
        body=body,
        subscriber_id="bap.example.com",
        unique_key_id="key-1",
        signing_private_key_b64=bap_priv,
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

    resp = client.post(
        reverse("search"),
        data=body,
        content_type="application/json",
        HTTP_AUTHORIZATION="irrelevant",
    )
    assert resp.status_code == 400


@patch("core.routing.relay_on_search_in_background")
def test_on_search_view_acks_immediately_for_a_validly_signed_bpp_callback(mock_relay, client):
    """Mirrors test_search_view_acks_immediately... but the caller being verified is
    the BPP (identity checked against context.bpp_id, not bap_id) — the roles are
    reversed from /search, per protocol_compliance_notes_v1.1.md §H.4.
    relay_on_search_in_background is mocked for the same reason
    dispatch_search_in_background is mocked above — real relay behavior is tested
    directly against routing.relay_on_search below, without racing a thread."""
    bpp_pub, bpp_priv = generate_signing_key_pair()
    payload = _build_on_search_context()
    body = json.dumps(payload).encode()
    header = sign_outbound_request(
        body=body,
        subscriber_id="bpp.example.com",
        unique_key_id="key-1",
        signing_private_key_b64=bpp_priv,
    )

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST,
            "http://registry:8000/lookup",
            callback=lambda request: (
                200,
                {},
                json.dumps(
                    [
                        {
                            "subscriber_id": "bpp.example.com",
                            "status": "SUBSCRIBED",
                            "signing_public_key": bpp_pub,
                        }
                    ]
                ),
            ),
        )
        resp = client.post(
            reverse("on_search"),
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION=header,
        )

    assert resp.status_code == 200
    assert resp.json() == {
        "context": payload["context"],
        "message": {"ack": {"status": "ACK"}},
    }
    mock_relay.assert_called_once_with(payload=payload, authorization_header=header)


def test_on_search_view_rejects_bpp_id_impersonation(client):
    bpp_pub, bpp_priv = generate_signing_key_pair()
    payload = _build_on_search_context(bpp_id="someone-else.example.com")
    body = json.dumps(payload).encode()
    header = sign_outbound_request(
        body=body,
        subscriber_id="bpp.example.com",
        unique_key_id="key-1",
        signing_private_key_b64=bpp_priv,
    )

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST,
            "http://registry:8000/lookup",
            callback=lambda request: (
                200,
                {},
                json.dumps(
                    [
                        {
                            "subscriber_id": "bpp.example.com",
                            "status": "SUBSCRIBED",
                            "signing_public_key": bpp_pub,
                        }
                    ]
                ),
            ),
        )
        resp = client.post(
            reverse("on_search"),
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION=header,
        )

    assert resp.status_code == 401


def test_relay_on_search_forwards_to_the_bap_with_both_signatures():
    """Real relay behavior, called directly and synchronously. Confirms the relayed
    body is byte-identical, the original (BPP's) Authorization header is preserved,
    and a fresh X-Gateway-Authorization is added — no Registry lookup needed since
    bap_uri already travels with the context."""
    payload = _build_on_search_context()
    original_auth_header = 'Signature keyId="bpp.example.com|key-1|ed25519",...(original)'

    captured_requests = []

    def bap_on_search_callback(request):
        captured_requests.append(request)
        return (200, {}, json.dumps({"message": {"ack": {"status": "ACK"}}}))

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST, "https://bap.example.com/on_search", callback=bap_on_search_callback
        )
        routing.relay_on_search(payload=payload, authorization_header=original_auth_header)

    assert len(captured_requests) == 1
    forwarded = captured_requests[0]
    assert json.loads(forwarded.body) == payload
    assert forwarded.headers["Authorization"] == original_auth_header
    assert forwarded.headers["X-Gateway-Authorization"].startswith("Signature keyId=")


def test_relay_on_search_does_not_raise_when_bap_is_unreachable():
    payload = _build_on_search_context()

    with responses.RequestsMock() as rsps:
        rsps.add(responses.POST, "https://bap.example.com/on_search", status=503)
        # must not raise:
        routing.relay_on_search(payload=payload, authorization_header="irrelevant")


def test_relay_on_search_logs_and_returns_when_bap_uri_missing():
    payload = _build_on_search_context()
    del payload["context"]["bap_uri"]
    routing.relay_on_search(payload=payload, authorization_header="irrelevant")  # must not raise


# --- select / on_select (livetracker2.md Phase 3.2) --------------------------------------


def _build_select_context(*, bap_id="bap.example.com", bpp_id="bpp.example.com"):
    """Unlike search's context, select's context already carries a real bpp_id/bpp_uri
    — the customer has already chosen a specific BPP from earlier on_search results,
    so /select targets that one BPP directly, not a domain-wide broadcast."""
    return {
        "context": {
            "domain": "ONDC:RET13",
            "location": {"country": {"code": "IND"}},
            "action": "select",
            "version": "1.1.0",
            "bap_id": bap_id,
            "bap_uri": f"https://{bap_id}",
            "bpp_id": bpp_id,
            "bpp_uri": f"https://{bpp_id}",
            "transaction_id": "txn-1",
            "message_id": "msg-1",
            "timestamp": "2026-07-20T00:00:00Z",
        },
        "message": {
            "order": {
                "items": [{"id": "item-1"}],
                "fulfillments": [
                    {"stops": [{"type": "start", "time": {"timestamp": "2026-07-25T10:00:00Z"}}]}
                ],
            }
        },
    }


def _build_on_select_context(*, bap_id="bap.example.com", bpp_id="bpp.example.com"):
    return {
        "context": {
            "domain": "ONDC:RET13",
            "location": {"country": {"code": "IND"}},
            "action": "on_select",
            "version": "1.1.0",
            "bap_id": bap_id,
            "bap_uri": f"https://{bap_id}",
            "bpp_id": bpp_id,
            "bpp_uri": f"https://{bpp_id}",
            "transaction_id": "txn-1",
            "message_id": "msg-1",
            "timestamp": "2026-07-20T00:00:00Z",
        },
        "message": {
            "order": {
                "provider": {"id": "biz-1"},
                "items": [{"id": "item-1"}],
                "fulfillments": [{"id": "booking-1"}],
                "quote": {"price": {"currency": "INR", "value": "500.00"}},
            }
        },
    }


@patch("core.routing.dispatch_select_in_background")
def test_select_view_acks_immediately_for_a_validly_signed_request(mock_dispatch, client):
    bap_pub, bap_priv = generate_signing_key_pair()
    payload = _build_select_context()
    body = json.dumps(payload).encode()
    header = sign_outbound_request(
        body=body,
        subscriber_id="bap.example.com",
        unique_key_id="key-1",
        signing_private_key_b64=bap_priv,
    )

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST,
            "http://registry:8000/lookup",
            callback=_lookup_callback(bap_pub, []),
        )
        resp = client.post(
            reverse("select"), data=body, content_type="application/json", HTTP_AUTHORIZATION=header
        )

    assert resp.status_code == 200
    assert resp.json() == {
        "context": payload["context"],
        "message": {"ack": {"status": "ACK"}},
    }
    mock_dispatch.assert_called_once_with(payload=payload, authorization_header=header)


def test_dispatch_select_forwards_to_the_specific_bpp_with_both_signatures(settings):
    """Real forwarding behavior, called directly and synchronously. Confirms a fresh
    SUBSCRIBED lookup happens for the specific bpp_id (not a domain-wide broadcast like
    dispatch_search), the forwarded body is byte-identical, the original Authorization
    header is preserved, and a fresh X-Gateway-Authorization is added."""
    settings.SUBSCRIBER_ID = "gateway.local"
    settings.UNIQUE_KEY_ID = "key1"
    payload = _build_select_context()
    original_auth_header = 'Signature keyId="bap.example.com|key-1|ed25519",...(original)'

    lookup_requests = []
    captured_requests = []

    def lookup_callback(request):
        lookup_requests.append(json.loads(request.body))
        return (
            200,
            {},
            json.dumps(
                [
                    {
                        "subscriber_id": "bpp.example.com",
                        "url": "https://bpp.example.com",
                        "status": "SUBSCRIBED",
                    }
                ]
            ),
        )

    def bpp_select_callback(request):
        captured_requests.append(request)
        return (200, {}, json.dumps({"message": {"ack": {"status": "ACK"}}}))

    with responses.RequestsMock() as rsps:
        rsps.add_callback(responses.POST, "http://registry:8000/lookup", callback=lookup_callback)
        rsps.add_callback(
            responses.POST, "https://bpp.example.com/select", callback=bpp_select_callback
        )
        routing.dispatch_select(payload=payload, authorization_header=original_auth_header)

    assert lookup_requests == [{"subscriber_id": "bpp.example.com"}]
    assert len(captured_requests) == 1
    forwarded = captured_requests[0]
    assert json.loads(forwarded.body) == payload
    assert forwarded.headers["Authorization"] == original_auth_header
    assert forwarded.headers["X-Gateway-Authorization"].startswith("Signature keyId=")
    assert "gateway.local" in forwarded.headers["X-Gateway-Authorization"]


def test_dispatch_select_does_not_forward_when_bpp_no_longer_subscribed():
    """The real, previously-missing gap this phase closed: a BPP's SUBSCRIBED status
    can go stale between search and select. dispatch_select must re-check it live and
    refuse to forward to a no-longer-SUBSCRIBED participant, even though its bpp_uri
    is already known from the select context — never a blind forward."""
    payload = _build_select_context()

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST,
            "http://registry:8000/lookup",
            callback=lambda request: (
                200,
                {},
                json.dumps(
                    [
                        {
                            "subscriber_id": "bpp.example.com",
                            "url": "https://bpp.example.com",
                            "status": "UNDER_SUBSCRIPTION",
                        }
                    ]
                ),
            ),
        )
        # deliberately no /select callback registered for bpp.example.com — if
        # dispatch_select forwards anyway, responses raises ConnectionError, failing
        # this test for real, not just via a call-count assertion.
        routing.dispatch_select(payload=payload, authorization_header="irrelevant")


def test_dispatch_select_does_not_raise_when_bpp_is_unreachable():
    payload = _build_select_context()

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST,
            "http://registry:8000/lookup",
            callback=lambda request: (
                200,
                {},
                json.dumps(
                    [
                        {
                            "subscriber_id": "bpp.example.com",
                            "url": "https://bpp.example.com",
                            "status": "SUBSCRIBED",
                        }
                    ]
                ),
            ),
        )
        rsps.add(responses.POST, "https://bpp.example.com/select", status=503)
        # must not raise:
        routing.dispatch_select(payload=payload, authorization_header="irrelevant")


def test_select_view_rejects_tampered_signature(client):
    bap_pub, _ = generate_signing_key_pair()
    _, attacker_priv = generate_signing_key_pair()
    payload = _build_select_context()
    body = json.dumps(payload).encode()
    forged_header = sign_outbound_request(
        body=body,
        subscriber_id="bap.example.com",
        unique_key_id="key-1",
        signing_private_key_b64=attacker_priv,
    )

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST, "http://registry:8000/lookup", callback=_lookup_callback(bap_pub, [])
        )
        resp = client.post(
            reverse("select"),
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION=forged_header,
        )

    assert resp.status_code == 401


def test_select_view_rejects_bap_id_impersonation(client):
    bap_pub, bap_priv = generate_signing_key_pair()
    payload = _build_select_context(bap_id="someone-else.example.com")
    body = json.dumps(payload).encode()
    header = sign_outbound_request(
        body=body,
        subscriber_id="bap.example.com",
        unique_key_id="key-1",
        signing_private_key_b64=bap_priv,
    )

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST, "http://registry:8000/lookup", callback=_lookup_callback(bap_pub, [])
        )
        resp = client.post(
            reverse("select"), data=body, content_type="application/json", HTTP_AUTHORIZATION=header
        )

    assert resp.status_code == 401


def test_select_view_rejects_missing_context_field(client):
    payload = _build_select_context()
    del payload["context"]["bap_id"]
    body = json.dumps(payload).encode()

    resp = client.post(
        reverse("select"),
        data=body,
        content_type="application/json",
        HTTP_AUTHORIZATION="irrelevant",
    )
    assert resp.status_code == 400


@patch("core.routing.relay_on_select_in_background")
def test_on_select_view_acks_immediately_for_a_validly_signed_bpp_callback(mock_relay, client):
    bpp_pub, bpp_priv = generate_signing_key_pair()
    payload = _build_on_select_context()
    body = json.dumps(payload).encode()
    header = sign_outbound_request(
        body=body,
        subscriber_id="bpp.example.com",
        unique_key_id="key-1",
        signing_private_key_b64=bpp_priv,
    )

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST,
            "http://registry:8000/lookup",
            callback=lambda request: (
                200,
                {},
                json.dumps(
                    [
                        {
                            "subscriber_id": "bpp.example.com",
                            "status": "SUBSCRIBED",
                            "signing_public_key": bpp_pub,
                        }
                    ]
                ),
            ),
        )
        resp = client.post(
            reverse("on_select"),
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION=header,
        )

    assert resp.status_code == 200
    assert resp.json() == {
        "context": payload["context"],
        "message": {"ack": {"status": "ACK"}},
    }
    mock_relay.assert_called_once_with(payload=payload, authorization_header=header)


def test_on_select_view_rejects_bpp_id_impersonation(client):
    bpp_pub, bpp_priv = generate_signing_key_pair()
    payload = _build_on_select_context(bpp_id="someone-else.example.com")
    body = json.dumps(payload).encode()
    header = sign_outbound_request(
        body=body,
        subscriber_id="bpp.example.com",
        unique_key_id="key-1",
        signing_private_key_b64=bpp_priv,
    )

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST,
            "http://registry:8000/lookup",
            callback=lambda request: (
                200,
                {},
                json.dumps(
                    [
                        {
                            "subscriber_id": "bpp.example.com",
                            "status": "SUBSCRIBED",
                            "signing_public_key": bpp_pub,
                        }
                    ]
                ),
            ),
        )
        resp = client.post(
            reverse("on_select"),
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION=header,
        )

    assert resp.status_code == 401


def test_relay_on_select_forwards_to_the_bap_with_both_signatures():
    payload = _build_on_select_context()
    original_auth_header = 'Signature keyId="bpp.example.com|key-1|ed25519",...(original)'

    captured_requests = []

    def bap_on_select_callback(request):
        captured_requests.append(request)
        return (200, {}, json.dumps({"message": {"ack": {"status": "ACK"}}}))

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST, "https://bap.example.com/on_select", callback=bap_on_select_callback
        )
        routing.relay_on_select(payload=payload, authorization_header=original_auth_header)

    assert len(captured_requests) == 1
    forwarded = captured_requests[0]
    assert json.loads(forwarded.body) == payload
    assert forwarded.headers["Authorization"] == original_auth_header
    assert forwarded.headers["X-Gateway-Authorization"].startswith("Signature keyId=")


def test_relay_on_select_does_not_raise_when_bap_is_unreachable():
    payload = _build_on_select_context()

    with responses.RequestsMock() as rsps:
        rsps.add(responses.POST, "https://bap.example.com/on_select", status=503)
        # must not raise:
        routing.relay_on_select(payload=payload, authorization_header="irrelevant")


def test_relay_on_select_logs_and_returns_when_bap_uri_missing():
    payload = _build_on_select_context()
    del payload["context"]["bap_uri"]
    routing.relay_on_select(payload=payload, authorization_header="irrelevant")  # must not raise


# --- init / on_init (livetracker2.md Phase 3.3) -------------------------------------------


def _build_init_context(*, bap_id="bap.example.com", bpp_id="bpp.example.com"):
    """Same already-know-the-BPP shape as select's context — /init targets the one
    BPP a prior /select already resolved to, not a domain-wide broadcast."""
    return {
        "context": {
            "domain": "ONDC:RET13",
            "location": {"country": {"code": "IND"}},
            "action": "init",
            "version": "1.1.0",
            "bap_id": bap_id,
            "bap_uri": f"https://{bap_id}",
            "bpp_id": bpp_id,
            "bpp_uri": f"https://{bpp_id}",
            "transaction_id": "txn-1",
            "message_id": "msg-1",
            "timestamp": "2026-07-20T00:00:00Z",
        },
        "message": {
            "order": {
                "provider": {"id": "biz-1"},
                "items": [{"id": "item-1"}],
                "fulfillments": [{"id": "booking-1"}],
            }
        },
    }


def _build_on_init_context(*, bap_id="bap.example.com", bpp_id="bpp.example.com"):
    return {
        "context": {
            "domain": "ONDC:RET13",
            "location": {"country": {"code": "IND"}},
            "action": "on_init",
            "version": "1.1.0",
            "bap_id": bap_id,
            "bap_uri": f"https://{bap_id}",
            "bpp_id": bpp_id,
            "bpp_uri": f"https://{bpp_id}",
            "transaction_id": "txn-1",
            "message_id": "msg-1",
            "timestamp": "2026-07-20T00:00:00Z",
        },
        "message": {
            "order": {
                "provider": {"id": "biz-1"},
                "items": [{"id": "item-1"}],
                "fulfillments": [{"id": "booking-1"}],
                "quote": {
                    "price": {"currency": "INR", "value": "500.00"},
                    "breakup": [
                        {
                            "item": {"id": "item-1"},
                            "title": "Stylist A",
                            "price": {"currency": "INR", "value": "500.00"},
                        }
                    ],
                    "ttl": "PT600S",
                },
            }
        },
    }


@patch("core.routing.dispatch_init_in_background")
def test_init_view_acks_immediately_for_a_validly_signed_request(mock_dispatch, client):
    bap_pub, bap_priv = generate_signing_key_pair()
    payload = _build_init_context()
    body = json.dumps(payload).encode()
    header = sign_outbound_request(
        body=body,
        subscriber_id="bap.example.com",
        unique_key_id="key-1",
        signing_private_key_b64=bap_priv,
    )

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST,
            "http://registry:8000/lookup",
            callback=_lookup_callback(bap_pub, []),
        )
        resp = client.post(
            reverse("init"), data=body, content_type="application/json", HTTP_AUTHORIZATION=header
        )

    assert resp.status_code == 200
    assert resp.json() == {
        "context": payload["context"],
        "message": {"ack": {"status": "ACK"}},
    }
    mock_dispatch.assert_called_once_with(payload=payload, authorization_header=header)


def test_dispatch_init_forwards_to_the_specific_bpp_with_both_signatures(settings):
    """Real forwarding behavior, called directly and synchronously. Confirms a fresh
    SUBSCRIBED lookup happens for the specific bpp_id (the same staleness re-check
    dispatch_select already established, now applied between select and init), the
    forwarded body is byte-identical, the original Authorization header is
    preserved, and a fresh X-Gateway-Authorization is added."""
    settings.SUBSCRIBER_ID = "gateway.local"
    settings.UNIQUE_KEY_ID = "key1"
    payload = _build_init_context()
    original_auth_header = 'Signature keyId="bap.example.com|key-1|ed25519",...(original)'

    lookup_requests = []
    captured_requests = []

    def lookup_callback(request):
        lookup_requests.append(json.loads(request.body))
        return (
            200,
            {},
            json.dumps(
                [
                    {
                        "subscriber_id": "bpp.example.com",
                        "url": "https://bpp.example.com",
                        "status": "SUBSCRIBED",
                    }
                ]
            ),
        )

    def bpp_init_callback(request):
        captured_requests.append(request)
        return (200, {}, json.dumps({"message": {"ack": {"status": "ACK"}}}))

    with responses.RequestsMock() as rsps:
        rsps.add_callback(responses.POST, "http://registry:8000/lookup", callback=lookup_callback)
        rsps.add_callback(
            responses.POST, "https://bpp.example.com/init", callback=bpp_init_callback
        )
        routing.dispatch_init(payload=payload, authorization_header=original_auth_header)

    assert lookup_requests == [{"subscriber_id": "bpp.example.com"}]
    assert len(captured_requests) == 1
    forwarded = captured_requests[0]
    assert json.loads(forwarded.body) == payload
    assert forwarded.headers["Authorization"] == original_auth_header
    assert forwarded.headers["X-Gateway-Authorization"].startswith("Signature keyId=")
    assert "gateway.local" in forwarded.headers["X-Gateway-Authorization"]


def test_dispatch_init_does_not_forward_when_bpp_no_longer_subscribed():
    """The same staleness gap already closed for dispatch_select applies equally
    between select and init: a BPP's SUBSCRIBED status can go stale in that window
    too. dispatch_init must re-check it live and refuse to forward, even though the
    context already carries a remembered bpp_uri."""
    payload = _build_init_context()

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST,
            "http://registry:8000/lookup",
            callback=lambda request: (
                200,
                {},
                json.dumps(
                    [
                        {
                            "subscriber_id": "bpp.example.com",
                            "url": "https://bpp.example.com",
                            "status": "UNDER_SUBSCRIPTION",
                        }
                    ]
                ),
            ),
        )
        # deliberately no /init callback registered for bpp.example.com — if
        # dispatch_init forwards anyway, responses raises ConnectionError, failing
        # this test for real, not just via a call-count assertion.
        routing.dispatch_init(payload=payload, authorization_header="irrelevant")


def test_dispatch_init_does_not_raise_when_bpp_is_unreachable():
    payload = _build_init_context()

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST,
            "http://registry:8000/lookup",
            callback=lambda request: (
                200,
                {},
                json.dumps(
                    [
                        {
                            "subscriber_id": "bpp.example.com",
                            "url": "https://bpp.example.com",
                            "status": "SUBSCRIBED",
                        }
                    ]
                ),
            ),
        )
        rsps.add(responses.POST, "https://bpp.example.com/init", status=503)
        # must not raise:
        routing.dispatch_init(payload=payload, authorization_header="irrelevant")


def test_init_view_rejects_tampered_signature(client):
    bap_pub, _ = generate_signing_key_pair()
    _, attacker_priv = generate_signing_key_pair()
    payload = _build_init_context()
    body = json.dumps(payload).encode()
    forged_header = sign_outbound_request(
        body=body,
        subscriber_id="bap.example.com",
        unique_key_id="key-1",
        signing_private_key_b64=attacker_priv,
    )

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST, "http://registry:8000/lookup", callback=_lookup_callback(bap_pub, [])
        )
        resp = client.post(
            reverse("init"),
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION=forged_header,
        )

    assert resp.status_code == 401


def test_init_view_rejects_bap_id_impersonation(client):
    bap_pub, bap_priv = generate_signing_key_pair()
    payload = _build_init_context(bap_id="someone-else.example.com")
    body = json.dumps(payload).encode()
    header = sign_outbound_request(
        body=body,
        subscriber_id="bap.example.com",
        unique_key_id="key-1",
        signing_private_key_b64=bap_priv,
    )

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST, "http://registry:8000/lookup", callback=_lookup_callback(bap_pub, [])
        )
        resp = client.post(
            reverse("init"), data=body, content_type="application/json", HTTP_AUTHORIZATION=header
        )

    assert resp.status_code == 401


def test_init_view_rejects_missing_context_field(client):
    payload = _build_init_context()
    del payload["context"]["bap_id"]
    body = json.dumps(payload).encode()

    resp = client.post(
        reverse("init"),
        data=body,
        content_type="application/json",
        HTTP_AUTHORIZATION="irrelevant",
    )
    assert resp.status_code == 400


@patch("core.routing.relay_on_init_in_background")
def test_on_init_view_acks_immediately_for_a_validly_signed_bpp_callback(mock_relay, client):
    bpp_pub, bpp_priv = generate_signing_key_pair()
    payload = _build_on_init_context()
    body = json.dumps(payload).encode()
    header = sign_outbound_request(
        body=body,
        subscriber_id="bpp.example.com",
        unique_key_id="key-1",
        signing_private_key_b64=bpp_priv,
    )

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST,
            "http://registry:8000/lookup",
            callback=lambda request: (
                200,
                {},
                json.dumps(
                    [
                        {
                            "subscriber_id": "bpp.example.com",
                            "status": "SUBSCRIBED",
                            "signing_public_key": bpp_pub,
                        }
                    ]
                ),
            ),
        )
        resp = client.post(
            reverse("on_init"),
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION=header,
        )

    assert resp.status_code == 200
    assert resp.json() == {
        "context": payload["context"],
        "message": {"ack": {"status": "ACK"}},
    }
    mock_relay.assert_called_once_with(payload=payload, authorization_header=header)


def test_on_init_view_rejects_bpp_id_impersonation(client):
    bpp_pub, bpp_priv = generate_signing_key_pair()
    payload = _build_on_init_context(bpp_id="someone-else.example.com")
    body = json.dumps(payload).encode()
    header = sign_outbound_request(
        body=body,
        subscriber_id="bpp.example.com",
        unique_key_id="key-1",
        signing_private_key_b64=bpp_priv,
    )

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST,
            "http://registry:8000/lookup",
            callback=lambda request: (
                200,
                {},
                json.dumps(
                    [
                        {
                            "subscriber_id": "bpp.example.com",
                            "status": "SUBSCRIBED",
                            "signing_public_key": bpp_pub,
                        }
                    ]
                ),
            ),
        )
        resp = client.post(
            reverse("on_init"),
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION=header,
        )

    assert resp.status_code == 401


def test_relay_on_init_forwards_to_the_bap_with_both_signatures():
    payload = _build_on_init_context()
    original_auth_header = 'Signature keyId="bpp.example.com|key-1|ed25519",...(original)'

    captured_requests = []

    def bap_on_init_callback(request):
        captured_requests.append(request)
        return (200, {}, json.dumps({"message": {"ack": {"status": "ACK"}}}))

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST, "https://bap.example.com/on_init", callback=bap_on_init_callback
        )
        routing.relay_on_init(payload=payload, authorization_header=original_auth_header)

    assert len(captured_requests) == 1
    forwarded = captured_requests[0]
    assert json.loads(forwarded.body) == payload
    assert forwarded.headers["Authorization"] == original_auth_header
    assert forwarded.headers["X-Gateway-Authorization"].startswith("Signature keyId=")


def test_relay_on_init_does_not_raise_when_bap_is_unreachable():
    payload = _build_on_init_context()

    with responses.RequestsMock() as rsps:
        rsps.add(responses.POST, "https://bap.example.com/on_init", status=503)
        # must not raise:
        routing.relay_on_init(payload=payload, authorization_header="irrelevant")


def test_relay_on_init_logs_and_returns_when_bap_uri_missing():
    payload = _build_on_init_context()
    del payload["context"]["bap_uri"]
    routing.relay_on_init(payload=payload, authorization_header="irrelevant")  # must not raise


# --- confirm / on_confirm (livetracker2.md Phase 3.4) --------------------------------------


def _build_confirm_context(*, bap_id="bap.example.com", bpp_id="bpp.example.com"):
    """Same already-know-the-BPP shape as init's context — /confirm targets the
    one BPP a prior /init already resolved to, not a domain-wide broadcast."""
    return {
        "context": {
            "domain": "ONDC:RET13",
            "location": {"country": {"code": "IND"}},
            "action": "confirm",
            "version": "1.1.0",
            "bap_id": bap_id,
            "bap_uri": f"https://{bap_id}",
            "bpp_id": bpp_id,
            "bpp_uri": f"https://{bpp_id}",
            "transaction_id": "txn-1",
            "message_id": "msg-1",
            "timestamp": "2026-07-20T00:00:00Z",
        },
        "message": {
            "order": {
                "provider": {"id": "biz-1"},
                "items": [{"id": "item-1"}],
                "fulfillments": [{"id": "booking-1"}],
            }
        },
    }


def _build_on_confirm_context(*, bap_id="bap.example.com", bpp_id="bpp.example.com"):
    return {
        "context": {
            "domain": "ONDC:RET13",
            "location": {"country": {"code": "IND"}},
            "action": "on_confirm",
            "version": "1.1.0",
            "bap_id": bap_id,
            "bap_uri": f"https://{bap_id}",
            "bpp_id": bpp_id,
            "bpp_uri": f"https://{bpp_id}",
            "transaction_id": "txn-1",
            "message_id": "msg-1",
            "timestamp": "2026-07-20T00:00:00Z",
        },
        "message": {
            "order": {
                "id": "booking-1",
                "status": "ACTIVE",
                "provider": {"id": "biz-1"},
                "items": [{"id": "item-1"}],
                "fulfillments": [{"id": "booking-1"}],
                "quote": {
                    "price": {"currency": "INR", "value": "500.00"},
                    "breakup": [
                        {
                            "item": {"id": "item-1"},
                            "title": "Stylist A",
                            "price": {"currency": "INR", "value": "500.00"},
                        }
                    ],
                },
                "payments": [{"status": "NOT-PAID"}],
            }
        },
    }


@patch("core.routing.dispatch_confirm_in_background")
def test_confirm_view_acks_immediately_for_a_validly_signed_request(mock_dispatch, client):
    bap_pub, bap_priv = generate_signing_key_pair()
    payload = _build_confirm_context()
    body = json.dumps(payload).encode()
    header = sign_outbound_request(
        body=body,
        subscriber_id="bap.example.com",
        unique_key_id="key-1",
        signing_private_key_b64=bap_priv,
    )

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST,
            "http://registry:8000/lookup",
            callback=_lookup_callback(bap_pub, []),
        )
        resp = client.post(
            reverse("confirm"),
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION=header,
        )

    assert resp.status_code == 200
    assert resp.json() == {
        "context": payload["context"],
        "message": {"ack": {"status": "ACK"}},
    }
    mock_dispatch.assert_called_once_with(
        payload=payload, authorization_header=header, correlation_id=ANY
    )


def test_dispatch_confirm_forwards_to_the_specific_bpp_with_both_signatures(settings):
    """Real forwarding behavior, called directly and synchronously. Confirms a
    fresh SUBSCRIBED lookup happens for the specific bpp_id (the same staleness
    re-check dispatch_init already established, now applied between init and
    confirm), the forwarded body is byte-identical, the original Authorization
    header is preserved, and a fresh X-Gateway-Authorization is added."""
    settings.SUBSCRIBER_ID = "gateway.local"
    settings.UNIQUE_KEY_ID = "key1"
    payload = _build_confirm_context()
    original_auth_header = 'Signature keyId="bap.example.com|key-1|ed25519",...(original)'

    lookup_requests = []
    captured_requests = []

    def lookup_callback(request):
        lookup_requests.append(json.loads(request.body))
        return (
            200,
            {},
            json.dumps(
                [
                    {
                        "subscriber_id": "bpp.example.com",
                        "url": "https://bpp.example.com",
                        "status": "SUBSCRIBED",
                    }
                ]
            ),
        )

    def bpp_confirm_callback(request):
        captured_requests.append(request)
        return (200, {}, json.dumps({"message": {"ack": {"status": "ACK"}}}))

    with responses.RequestsMock() as rsps:
        rsps.add_callback(responses.POST, "http://registry:8000/lookup", callback=lookup_callback)
        rsps.add_callback(
            responses.POST, "https://bpp.example.com/confirm", callback=bpp_confirm_callback
        )
        routing.dispatch_confirm(payload=payload, authorization_header=original_auth_header)

    assert lookup_requests == [{"subscriber_id": "bpp.example.com"}]
    assert len(captured_requests) == 1
    forwarded = captured_requests[0]
    assert json.loads(forwarded.body) == payload
    assert forwarded.headers["Authorization"] == original_auth_header
    assert forwarded.headers["X-Gateway-Authorization"].startswith("Signature keyId=")
    assert "gateway.local" in forwarded.headers["X-Gateway-Authorization"]


def test_dispatch_confirm_forwards_the_real_correlation_id_to_bpp(settings):
    """livetracker2.md §3.10: a real gap found live — Gateway never forwarded
    X-Correlation-Id when relaying to BPP, so BPP's own CorrelationIdMiddleware
    always minted a fresh, disconnected id regardless of what BAP sent. Confirmed
    fixed: the exact id passed to dispatch_confirm is forwarded unchanged."""
    settings.SUBSCRIBER_ID = "gateway.local"
    settings.UNIQUE_KEY_ID = "key1"
    payload = _build_confirm_context()

    captured_requests = []

    def lookup_callback(request):
        return (
            200,
            {},
            json.dumps(
                [
                    {
                        "subscriber_id": "bpp.example.com",
                        "url": "https://bpp.example.com",
                        "status": "SUBSCRIBED",
                    }
                ]
            ),
        )

    def bpp_confirm_callback(request):
        captured_requests.append(request)
        return (200, {}, json.dumps({"message": {"ack": {"status": "ACK"}}}))

    with responses.RequestsMock() as rsps:
        rsps.add_callback(responses.POST, "http://registry:8000/lookup", callback=lookup_callback)
        rsps.add_callback(
            responses.POST, "https://bpp.example.com/confirm", callback=bpp_confirm_callback
        )
        routing.dispatch_confirm(
            payload=payload,
            authorization_header='Signature keyId="bap.example.com|key-1|ed25519",...',
            correlation_id="corr-real-hop-1",
        )

    assert captured_requests[0].headers["X-Correlation-Id"] == "corr-real-hop-1"


def test_dispatch_confirm_does_not_forward_when_bpp_no_longer_subscribed():
    """The same staleness gap already closed for dispatch_init applies equally
    between init and confirm: a BPP's SUBSCRIBED status can go stale in that
    window too. dispatch_confirm must re-check it live and refuse to forward,
    even though the context already carries a remembered bpp_uri."""
    payload = _build_confirm_context()

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST,
            "http://registry:8000/lookup",
            callback=lambda request: (
                200,
                {},
                json.dumps(
                    [
                        {
                            "subscriber_id": "bpp.example.com",
                            "url": "https://bpp.example.com",
                            "status": "UNDER_SUBSCRIPTION",
                        }
                    ]
                ),
            ),
        )
        # deliberately no /confirm callback registered for bpp.example.com — if
        # dispatch_confirm forwards anyway, responses raises ConnectionError,
        # failing this test for real, not just via a call-count assertion.
        routing.dispatch_confirm(payload=payload, authorization_header="irrelevant")


def test_dispatch_confirm_does_not_raise_when_bpp_is_unreachable():
    payload = _build_confirm_context()

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST,
            "http://registry:8000/lookup",
            callback=lambda request: (
                200,
                {},
                json.dumps(
                    [
                        {
                            "subscriber_id": "bpp.example.com",
                            "url": "https://bpp.example.com",
                            "status": "SUBSCRIBED",
                        }
                    ]
                ),
            ),
        )
        rsps.add(responses.POST, "https://bpp.example.com/confirm", status=503)
        # must not raise:
        routing.dispatch_confirm(payload=payload, authorization_header="irrelevant")


def test_confirm_view_rejects_tampered_signature(client):
    bap_pub, _ = generate_signing_key_pair()
    _, attacker_priv = generate_signing_key_pair()
    payload = _build_confirm_context()
    body = json.dumps(payload).encode()
    forged_header = sign_outbound_request(
        body=body,
        subscriber_id="bap.example.com",
        unique_key_id="key-1",
        signing_private_key_b64=attacker_priv,
    )

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST, "http://registry:8000/lookup", callback=_lookup_callback(bap_pub, [])
        )
        resp = client.post(
            reverse("confirm"),
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION=forged_header,
        )

    assert resp.status_code == 401


def test_confirm_view_rejects_bap_id_impersonation(client):
    bap_pub, bap_priv = generate_signing_key_pair()
    payload = _build_confirm_context(bap_id="someone-else.example.com")
    body = json.dumps(payload).encode()
    header = sign_outbound_request(
        body=body,
        subscriber_id="bap.example.com",
        unique_key_id="key-1",
        signing_private_key_b64=bap_priv,
    )

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST, "http://registry:8000/lookup", callback=_lookup_callback(bap_pub, [])
        )
        resp = client.post(
            reverse("confirm"),
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION=header,
        )

    assert resp.status_code == 401


def test_confirm_view_rejects_missing_context_field(client):
    payload = _build_confirm_context()
    del payload["context"]["bap_id"]
    body = json.dumps(payload).encode()

    resp = client.post(
        reverse("confirm"),
        data=body,
        content_type="application/json",
        HTTP_AUTHORIZATION="irrelevant",
    )
    assert resp.status_code == 400


@patch("core.routing.relay_on_confirm_in_background")
def test_on_confirm_view_acks_immediately_for_a_validly_signed_bpp_callback(mock_relay, client):
    bpp_pub, bpp_priv = generate_signing_key_pair()
    payload = _build_on_confirm_context()
    body = json.dumps(payload).encode()
    header = sign_outbound_request(
        body=body,
        subscriber_id="bpp.example.com",
        unique_key_id="key-1",
        signing_private_key_b64=bpp_priv,
    )

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST,
            "http://registry:8000/lookup",
            callback=lambda request: (
                200,
                {},
                json.dumps(
                    [
                        {
                            "subscriber_id": "bpp.example.com",
                            "status": "SUBSCRIBED",
                            "signing_public_key": bpp_pub,
                        }
                    ]
                ),
            ),
        )
        resp = client.post(
            reverse("on_confirm"),
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION=header,
        )

    assert resp.status_code == 200
    assert resp.json() == {
        "context": payload["context"],
        "message": {"ack": {"status": "ACK"}},
    }
    mock_relay.assert_called_once_with(payload=payload, authorization_header=header)


def test_on_confirm_view_rejects_bpp_id_impersonation(client):
    bpp_pub, bpp_priv = generate_signing_key_pair()
    payload = _build_on_confirm_context(bpp_id="someone-else.example.com")
    body = json.dumps(payload).encode()
    header = sign_outbound_request(
        body=body,
        subscriber_id="bpp.example.com",
        unique_key_id="key-1",
        signing_private_key_b64=bpp_priv,
    )

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST,
            "http://registry:8000/lookup",
            callback=lambda request: (
                200,
                {},
                json.dumps(
                    [
                        {
                            "subscriber_id": "bpp.example.com",
                            "status": "SUBSCRIBED",
                            "signing_public_key": bpp_pub,
                        }
                    ]
                ),
            ),
        )
        resp = client.post(
            reverse("on_confirm"),
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION=header,
        )

    assert resp.status_code == 401


def test_relay_on_confirm_forwards_to_the_bap_with_both_signatures():
    payload = _build_on_confirm_context()
    original_auth_header = 'Signature keyId="bpp.example.com|key-1|ed25519",...(original)'

    captured_requests = []

    def bap_on_confirm_callback(request):
        captured_requests.append(request)
        return (200, {}, json.dumps({"message": {"ack": {"status": "ACK"}}}))

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST, "https://bap.example.com/on_confirm", callback=bap_on_confirm_callback
        )
        routing.relay_on_confirm(payload=payload, authorization_header=original_auth_header)

    assert len(captured_requests) == 1
    forwarded = captured_requests[0]
    assert json.loads(forwarded.body) == payload
    assert forwarded.headers["Authorization"] == original_auth_header
    assert forwarded.headers["X-Gateway-Authorization"].startswith("Signature keyId=")


def test_relay_on_confirm_does_not_raise_when_bap_is_unreachable():
    payload = _build_on_confirm_context()

    with responses.RequestsMock() as rsps:
        rsps.add(responses.POST, "https://bap.example.com/on_confirm", status=503)
        # must not raise:
        routing.relay_on_confirm(payload=payload, authorization_header="irrelevant")


def test_relay_on_confirm_logs_and_returns_when_bap_uri_missing():
    payload = _build_on_confirm_context()
    del payload["context"]["bap_uri"]
    routing.relay_on_confirm(payload=payload, authorization_header="irrelevant")  # must not raise


# --- status / on_status (livetracker2.md Phase 3.5) -----------------------------------------


def _build_status_context(*, bap_id="bap.example.com", bpp_id="bpp.example.com"):
    """Unlike confirm's context, /status's message carries only order_id (§L.1)."""
    return {
        "context": {
            "domain": "ONDC:RET13",
            "location": {"country": {"code": "IND"}},
            "action": "status",
            "version": "1.1.0",
            "bap_id": bap_id,
            "bap_uri": f"https://{bap_id}",
            "bpp_id": bpp_id,
            "bpp_uri": f"https://{bpp_id}",
            "transaction_id": "txn-1",
            "message_id": "msg-1",
            "timestamp": "2026-07-20T00:00:00Z",
        },
        "message": {"order_id": "booking-1"},
    }


def _build_on_status_context(*, bap_id="bap.example.com", bpp_id="bpp.example.com"):
    return {
        "context": {
            "domain": "ONDC:RET13",
            "location": {"country": {"code": "IND"}},
            "action": "on_status",
            "version": "1.1.0",
            "bap_id": bap_id,
            "bap_uri": f"https://{bap_id}",
            "bpp_id": bpp_id,
            "bpp_uri": f"https://{bpp_id}",
            "transaction_id": "txn-1",
            "message_id": "msg-1",
            "timestamp": "2026-07-20T00:00:00Z",
        },
        "message": {"order": {"id": "booking-1", "status": "ACTIVE"}},
    }


@patch("core.routing.dispatch_status_in_background")
def test_status_view_acks_immediately_for_a_validly_signed_request(mock_dispatch, client):
    bap_pub, bap_priv = generate_signing_key_pair()
    payload = _build_status_context()
    body = json.dumps(payload).encode()
    header = sign_outbound_request(
        body=body,
        subscriber_id="bap.example.com",
        unique_key_id="key-1",
        signing_private_key_b64=bap_priv,
    )

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST,
            "http://registry:8000/lookup",
            callback=_lookup_callback(bap_pub, []),
        )
        resp = client.post(
            reverse("status"), data=body, content_type="application/json", HTTP_AUTHORIZATION=header
        )

    assert resp.status_code == 200
    assert resp.json() == {
        "context": payload["context"],
        "message": {"ack": {"status": "ACK"}},
    }
    mock_dispatch.assert_called_once_with(payload=payload, authorization_header=header)


def test_dispatch_status_forwards_to_the_specific_bpp_with_both_signatures(settings):
    settings.SUBSCRIBER_ID = "gateway.local"
    settings.UNIQUE_KEY_ID = "key1"
    payload = _build_status_context()
    original_auth_header = 'Signature keyId="bap.example.com|key-1|ed25519",...(original)'

    lookup_requests = []
    captured_requests = []

    def lookup_callback(request):
        lookup_requests.append(json.loads(request.body))
        return (
            200,
            {},
            json.dumps(
                [
                    {
                        "subscriber_id": "bpp.example.com",
                        "url": "https://bpp.example.com",
                        "status": "SUBSCRIBED",
                    }
                ]
            ),
        )

    def bpp_status_callback(request):
        captured_requests.append(request)
        return (200, {}, json.dumps({"message": {"ack": {"status": "ACK"}}}))

    with responses.RequestsMock() as rsps:
        rsps.add_callback(responses.POST, "http://registry:8000/lookup", callback=lookup_callback)
        rsps.add_callback(
            responses.POST, "https://bpp.example.com/status", callback=bpp_status_callback
        )
        routing.dispatch_status(payload=payload, authorization_header=original_auth_header)

    assert lookup_requests == [{"subscriber_id": "bpp.example.com"}]
    assert len(captured_requests) == 1
    forwarded = captured_requests[0]
    assert json.loads(forwarded.body) == payload
    assert forwarded.headers["Authorization"] == original_auth_header
    assert forwarded.headers["X-Gateway-Authorization"].startswith("Signature keyId=")
    assert "gateway.local" in forwarded.headers["X-Gateway-Authorization"]


def test_dispatch_status_does_not_forward_when_bpp_no_longer_subscribed():
    payload = _build_status_context()

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST,
            "http://registry:8000/lookup",
            callback=lambda request: (
                200,
                {},
                json.dumps(
                    [
                        {
                            "subscriber_id": "bpp.example.com",
                            "url": "https://bpp.example.com",
                            "status": "UNDER_SUBSCRIPTION",
                        }
                    ]
                ),
            ),
        )
        # deliberately no /status callback registered — if dispatch_status forwards
        # anyway, responses raises ConnectionError, failing this test for real.
        routing.dispatch_status(payload=payload, authorization_header="irrelevant")


def test_dispatch_status_does_not_raise_when_bpp_is_unreachable():
    payload = _build_status_context()

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST,
            "http://registry:8000/lookup",
            callback=lambda request: (
                200,
                {},
                json.dumps(
                    [
                        {
                            "subscriber_id": "bpp.example.com",
                            "url": "https://bpp.example.com",
                            "status": "SUBSCRIBED",
                        }
                    ]
                ),
            ),
        )
        rsps.add(responses.POST, "https://bpp.example.com/status", status=503)
        # must not raise:
        routing.dispatch_status(payload=payload, authorization_header="irrelevant")


def test_status_view_rejects_missing_context_field(client):
    payload = _build_status_context()
    del payload["context"]["bap_id"]
    body = json.dumps(payload).encode()

    resp = client.post(
        reverse("status"),
        data=body,
        content_type="application/json",
        HTTP_AUTHORIZATION="irrelevant",
    )
    assert resp.status_code == 400


@patch("core.routing.relay_on_status_in_background")
def test_on_status_view_acks_immediately_for_a_validly_signed_bpp_callback(mock_relay, client):
    bpp_pub, bpp_priv = generate_signing_key_pair()
    payload = _build_on_status_context()
    body = json.dumps(payload).encode()
    header = sign_outbound_request(
        body=body,
        subscriber_id="bpp.example.com",
        unique_key_id="key-1",
        signing_private_key_b64=bpp_priv,
    )

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST,
            "http://registry:8000/lookup",
            callback=lambda request: (
                200,
                {},
                json.dumps(
                    [
                        {
                            "subscriber_id": "bpp.example.com",
                            "status": "SUBSCRIBED",
                            "signing_public_key": bpp_pub,
                        }
                    ]
                ),
            ),
        )
        resp = client.post(
            reverse("on_status"),
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION=header,
        )

    assert resp.status_code == 200
    assert resp.json() == {
        "context": payload["context"],
        "message": {"ack": {"status": "ACK"}},
    }
    mock_relay.assert_called_once_with(payload=payload, authorization_header=header)


def test_relay_on_status_forwards_to_the_bap_with_both_signatures():
    payload = _build_on_status_context()
    original_auth_header = 'Signature keyId="bpp.example.com|key-1|ed25519",...(original)'

    captured_requests = []

    def bap_on_status_callback(request):
        captured_requests.append(request)
        return (200, {}, json.dumps({"message": {"ack": {"status": "ACK"}}}))

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST, "https://bap.example.com/on_status", callback=bap_on_status_callback
        )
        routing.relay_on_status(payload=payload, authorization_header=original_auth_header)

    assert len(captured_requests) == 1
    forwarded = captured_requests[0]
    assert json.loads(forwarded.body) == payload
    assert forwarded.headers["Authorization"] == original_auth_header
    assert forwarded.headers["X-Gateway-Authorization"].startswith("Signature keyId=")


def test_relay_on_status_does_not_raise_when_bap_is_unreachable():
    payload = _build_on_status_context()

    with responses.RequestsMock() as rsps:
        rsps.add(responses.POST, "https://bap.example.com/on_status", status=503)
        routing.relay_on_status(payload=payload, authorization_header="irrelevant")


def test_relay_on_status_logs_and_returns_when_bap_uri_missing():
    payload = _build_on_status_context()
    del payload["context"]["bap_uri"]
    routing.relay_on_status(payload=payload, authorization_header="irrelevant")


# --- cancel / on_cancel (livetracker2.md Phase 3.5) ------------------------------------------


def _build_cancel_context(*, bap_id="bap.example.com", bpp_id="bpp.example.com"):
    return {
        "context": {
            "domain": "ONDC:RET13",
            "location": {"country": {"code": "IND"}},
            "action": "cancel",
            "version": "1.1.0",
            "bap_id": bap_id,
            "bap_uri": f"https://{bap_id}",
            "bpp_id": bpp_id,
            "bpp_uri": f"https://{bpp_id}",
            "transaction_id": "txn-1",
            "message_id": "msg-1",
            "timestamp": "2026-07-20T00:00:00Z",
        },
        "message": {"order_id": "booking-1"},
    }


def _build_on_cancel_context(*, bap_id="bap.example.com", bpp_id="bpp.example.com"):
    return {
        "context": {
            "domain": "ONDC:RET13",
            "location": {"country": {"code": "IND"}},
            "action": "on_cancel",
            "version": "1.1.0",
            "bap_id": bap_id,
            "bap_uri": f"https://{bap_id}",
            "bpp_id": bpp_id,
            "bpp_uri": f"https://{bpp_id}",
            "transaction_id": "txn-1",
            "message_id": "msg-1",
            "timestamp": "2026-07-20T00:00:00Z",
        },
        "message": {"order": {"id": "booking-1", "status": "CANCELLED"}},
    }


@patch("core.routing.dispatch_cancel_in_background")
def test_cancel_view_acks_immediately_for_a_validly_signed_request(mock_dispatch, client):
    bap_pub, bap_priv = generate_signing_key_pair()
    payload = _build_cancel_context()
    body = json.dumps(payload).encode()
    header = sign_outbound_request(
        body=body,
        subscriber_id="bap.example.com",
        unique_key_id="key-1",
        signing_private_key_b64=bap_priv,
    )

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST,
            "http://registry:8000/lookup",
            callback=_lookup_callback(bap_pub, []),
        )
        resp = client.post(
            reverse("cancel"), data=body, content_type="application/json", HTTP_AUTHORIZATION=header
        )

    assert resp.status_code == 200
    assert resp.json() == {
        "context": payload["context"],
        "message": {"ack": {"status": "ACK"}},
    }
    mock_dispatch.assert_called_once_with(
        payload=payload, authorization_header=header, correlation_id=ANY
    )


def test_dispatch_cancel_forwards_to_the_specific_bpp_with_both_signatures(settings):
    settings.SUBSCRIBER_ID = "gateway.local"
    settings.UNIQUE_KEY_ID = "key1"
    payload = _build_cancel_context()
    original_auth_header = 'Signature keyId="bap.example.com|key-1|ed25519",...(original)'

    lookup_requests = []
    captured_requests = []

    def lookup_callback(request):
        lookup_requests.append(json.loads(request.body))
        return (
            200,
            {},
            json.dumps(
                [
                    {
                        "subscriber_id": "bpp.example.com",
                        "url": "https://bpp.example.com",
                        "status": "SUBSCRIBED",
                    }
                ]
            ),
        )

    def bpp_cancel_callback(request):
        captured_requests.append(request)
        return (200, {}, json.dumps({"message": {"ack": {"status": "ACK"}}}))

    with responses.RequestsMock() as rsps:
        rsps.add_callback(responses.POST, "http://registry:8000/lookup", callback=lookup_callback)
        rsps.add_callback(
            responses.POST, "https://bpp.example.com/cancel", callback=bpp_cancel_callback
        )
        routing.dispatch_cancel(payload=payload, authorization_header=original_auth_header)

    assert lookup_requests == [{"subscriber_id": "bpp.example.com"}]
    assert len(captured_requests) == 1
    forwarded = captured_requests[0]
    assert json.loads(forwarded.body) == payload
    assert forwarded.headers["Authorization"] == original_auth_header
    assert forwarded.headers["X-Gateway-Authorization"].startswith("Signature keyId=")
    assert "gateway.local" in forwarded.headers["X-Gateway-Authorization"]


def test_dispatch_cancel_does_not_forward_when_bpp_no_longer_subscribed():
    payload = _build_cancel_context()

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST,
            "http://registry:8000/lookup",
            callback=lambda request: (
                200,
                {},
                json.dumps(
                    [
                        {
                            "subscriber_id": "bpp.example.com",
                            "url": "https://bpp.example.com",
                            "status": "UNDER_SUBSCRIPTION",
                        }
                    ]
                ),
            ),
        )
        routing.dispatch_cancel(payload=payload, authorization_header="irrelevant")


def test_dispatch_cancel_does_not_raise_when_bpp_is_unreachable():
    payload = _build_cancel_context()

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST,
            "http://registry:8000/lookup",
            callback=lambda request: (
                200,
                {},
                json.dumps(
                    [
                        {
                            "subscriber_id": "bpp.example.com",
                            "url": "https://bpp.example.com",
                            "status": "SUBSCRIBED",
                        }
                    ]
                ),
            ),
        )
        rsps.add(responses.POST, "https://bpp.example.com/cancel", status=503)
        routing.dispatch_cancel(payload=payload, authorization_header="irrelevant")


def test_cancel_view_rejects_missing_context_field(client):
    payload = _build_cancel_context()
    del payload["context"]["bap_id"]
    body = json.dumps(payload).encode()

    resp = client.post(
        reverse("cancel"),
        data=body,
        content_type="application/json",
        HTTP_AUTHORIZATION="irrelevant",
    )
    assert resp.status_code == 400


@patch("core.routing.relay_on_cancel_in_background")
def test_on_cancel_view_acks_immediately_for_a_validly_signed_bpp_callback(mock_relay, client):
    bpp_pub, bpp_priv = generate_signing_key_pair()
    payload = _build_on_cancel_context()
    body = json.dumps(payload).encode()
    header = sign_outbound_request(
        body=body,
        subscriber_id="bpp.example.com",
        unique_key_id="key-1",
        signing_private_key_b64=bpp_priv,
    )

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST,
            "http://registry:8000/lookup",
            callback=lambda request: (
                200,
                {},
                json.dumps(
                    [
                        {
                            "subscriber_id": "bpp.example.com",
                            "status": "SUBSCRIBED",
                            "signing_public_key": bpp_pub,
                        }
                    ]
                ),
            ),
        )
        resp = client.post(
            reverse("on_cancel"),
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION=header,
        )

    assert resp.status_code == 200
    assert resp.json() == {
        "context": payload["context"],
        "message": {"ack": {"status": "ACK"}},
    }
    mock_relay.assert_called_once_with(payload=payload, authorization_header=header)


def test_relay_on_cancel_forwards_to_the_bap_with_both_signatures():
    payload = _build_on_cancel_context()
    original_auth_header = 'Signature keyId="bpp.example.com|key-1|ed25519",...(original)'

    captured_requests = []

    def bap_on_cancel_callback(request):
        captured_requests.append(request)
        return (200, {}, json.dumps({"message": {"ack": {"status": "ACK"}}}))

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST, "https://bap.example.com/on_cancel", callback=bap_on_cancel_callback
        )
        routing.relay_on_cancel(payload=payload, authorization_header=original_auth_header)

    assert len(captured_requests) == 1
    forwarded = captured_requests[0]
    assert json.loads(forwarded.body) == payload
    assert forwarded.headers["Authorization"] == original_auth_header
    assert forwarded.headers["X-Gateway-Authorization"].startswith("Signature keyId=")


def test_relay_on_cancel_does_not_raise_when_bap_is_unreachable():
    payload = _build_on_cancel_context()

    with responses.RequestsMock() as rsps:
        rsps.add(responses.POST, "https://bap.example.com/on_cancel", status=503)
        routing.relay_on_cancel(payload=payload, authorization_header="irrelevant")


def test_relay_on_cancel_logs_and_returns_when_bap_uri_missing():
    payload = _build_on_cancel_context()
    del payload["context"]["bap_uri"]
    routing.relay_on_cancel(payload=payload, authorization_header="irrelevant")


# --- update / on_update (livetracker2.md Phase 3.5) ------------------------------------------


def _build_update_context(*, bap_id="bap.example.com", bpp_id="bpp.example.com"):
    """Unlike status/cancel/track, /update DOES carry a full message.order (§L.3)."""
    return {
        "context": {
            "domain": "ONDC:RET13",
            "location": {"country": {"code": "IND"}},
            "action": "update",
            "version": "1.1.0",
            "bap_id": bap_id,
            "bap_uri": f"https://{bap_id}",
            "bpp_id": bpp_id,
            "bpp_uri": f"https://{bpp_id}",
            "transaction_id": "txn-1",
            "message_id": "msg-1",
            "timestamp": "2026-07-20T00:00:00Z",
        },
        "message": {
            "update_target": "fulfillment",
            "order": {
                "provider": {"id": "biz-1"},
                "items": [{"id": "item-1"}],
                "fulfillments": [
                    {
                        "id": "booking-1",
                        "stops": [
                            {"type": "start", "time": {"timestamp": "2026-07-25T10:00:00Z"}}
                        ],
                    }
                ],
            },
        },
    }


def _build_on_update_context(*, bap_id="bap.example.com", bpp_id="bpp.example.com"):
    return {
        "context": {
            "domain": "ONDC:RET13",
            "location": {"country": {"code": "IND"}},
            "action": "on_update",
            "version": "1.1.0",
            "bap_id": bap_id,
            "bap_uri": f"https://{bap_id}",
            "bpp_id": bpp_id,
            "bpp_uri": f"https://{bpp_id}",
            "transaction_id": "txn-1",
            "message_id": "msg-1",
            "timestamp": "2026-07-20T00:00:00Z",
        },
        "message": {"order": {"id": "booking-1", "status": "ACTIVE"}},
    }


@patch("core.routing.dispatch_update_in_background")
def test_update_view_acks_immediately_for_a_validly_signed_request(mock_dispatch, client):
    bap_pub, bap_priv = generate_signing_key_pair()
    payload = _build_update_context()
    body = json.dumps(payload).encode()
    header = sign_outbound_request(
        body=body,
        subscriber_id="bap.example.com",
        unique_key_id="key-1",
        signing_private_key_b64=bap_priv,
    )

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST,
            "http://registry:8000/lookup",
            callback=_lookup_callback(bap_pub, []),
        )
        resp = client.post(
            reverse("update"), data=body, content_type="application/json", HTTP_AUTHORIZATION=header
        )

    assert resp.status_code == 200
    assert resp.json() == {
        "context": payload["context"],
        "message": {"ack": {"status": "ACK"}},
    }
    mock_dispatch.assert_called_once_with(
        payload=payload, authorization_header=header, correlation_id=ANY
    )


def test_dispatch_update_forwards_to_the_specific_bpp_with_both_signatures(settings):
    settings.SUBSCRIBER_ID = "gateway.local"
    settings.UNIQUE_KEY_ID = "key1"
    payload = _build_update_context()
    original_auth_header = 'Signature keyId="bap.example.com|key-1|ed25519",...(original)'

    lookup_requests = []
    captured_requests = []

    def lookup_callback(request):
        lookup_requests.append(json.loads(request.body))
        return (
            200,
            {},
            json.dumps(
                [
                    {
                        "subscriber_id": "bpp.example.com",
                        "url": "https://bpp.example.com",
                        "status": "SUBSCRIBED",
                    }
                ]
            ),
        )

    def bpp_update_callback(request):
        captured_requests.append(request)
        return (200, {}, json.dumps({"message": {"ack": {"status": "ACK"}}}))

    with responses.RequestsMock() as rsps:
        rsps.add_callback(responses.POST, "http://registry:8000/lookup", callback=lookup_callback)
        rsps.add_callback(
            responses.POST, "https://bpp.example.com/update", callback=bpp_update_callback
        )
        routing.dispatch_update(payload=payload, authorization_header=original_auth_header)

    assert lookup_requests == [{"subscriber_id": "bpp.example.com"}]
    assert len(captured_requests) == 1
    forwarded = captured_requests[0]
    assert json.loads(forwarded.body) == payload
    assert forwarded.headers["Authorization"] == original_auth_header
    assert forwarded.headers["X-Gateway-Authorization"].startswith("Signature keyId=")
    assert "gateway.local" in forwarded.headers["X-Gateway-Authorization"]


def test_dispatch_update_does_not_forward_when_bpp_no_longer_subscribed():
    payload = _build_update_context()

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST,
            "http://registry:8000/lookup",
            callback=lambda request: (
                200,
                {},
                json.dumps(
                    [
                        {
                            "subscriber_id": "bpp.example.com",
                            "url": "https://bpp.example.com",
                            "status": "UNDER_SUBSCRIPTION",
                        }
                    ]
                ),
            ),
        )
        routing.dispatch_update(payload=payload, authorization_header="irrelevant")


def test_dispatch_update_does_not_raise_when_bpp_is_unreachable():
    payload = _build_update_context()

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST,
            "http://registry:8000/lookup",
            callback=lambda request: (
                200,
                {},
                json.dumps(
                    [
                        {
                            "subscriber_id": "bpp.example.com",
                            "url": "https://bpp.example.com",
                            "status": "SUBSCRIBED",
                        }
                    ]
                ),
            ),
        )
        rsps.add(responses.POST, "https://bpp.example.com/update", status=503)
        routing.dispatch_update(payload=payload, authorization_header="irrelevant")


def test_update_view_rejects_missing_context_field(client):
    payload = _build_update_context()
    del payload["context"]["bap_id"]
    body = json.dumps(payload).encode()

    resp = client.post(
        reverse("update"),
        data=body,
        content_type="application/json",
        HTTP_AUTHORIZATION="irrelevant",
    )
    assert resp.status_code == 400


@patch("core.routing.relay_on_update_in_background")
def test_on_update_view_acks_immediately_for_a_validly_signed_bpp_callback(mock_relay, client):
    bpp_pub, bpp_priv = generate_signing_key_pair()
    payload = _build_on_update_context()
    body = json.dumps(payload).encode()
    header = sign_outbound_request(
        body=body,
        subscriber_id="bpp.example.com",
        unique_key_id="key-1",
        signing_private_key_b64=bpp_priv,
    )

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST,
            "http://registry:8000/lookup",
            callback=lambda request: (
                200,
                {},
                json.dumps(
                    [
                        {
                            "subscriber_id": "bpp.example.com",
                            "status": "SUBSCRIBED",
                            "signing_public_key": bpp_pub,
                        }
                    ]
                ),
            ),
        )
        resp = client.post(
            reverse("on_update"),
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION=header,
        )

    assert resp.status_code == 200
    assert resp.json() == {
        "context": payload["context"],
        "message": {"ack": {"status": "ACK"}},
    }
    mock_relay.assert_called_once_with(payload=payload, authorization_header=header)


def test_relay_on_update_forwards_to_the_bap_with_both_signatures():
    payload = _build_on_update_context()
    original_auth_header = 'Signature keyId="bpp.example.com|key-1|ed25519",...(original)'

    captured_requests = []

    def bap_on_update_callback(request):
        captured_requests.append(request)
        return (200, {}, json.dumps({"message": {"ack": {"status": "ACK"}}}))

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST, "https://bap.example.com/on_update", callback=bap_on_update_callback
        )
        routing.relay_on_update(payload=payload, authorization_header=original_auth_header)

    assert len(captured_requests) == 1
    forwarded = captured_requests[0]
    assert json.loads(forwarded.body) == payload
    assert forwarded.headers["Authorization"] == original_auth_header
    assert forwarded.headers["X-Gateway-Authorization"].startswith("Signature keyId=")


def test_relay_on_update_does_not_raise_when_bap_is_unreachable():
    payload = _build_on_update_context()

    with responses.RequestsMock() as rsps:
        rsps.add(responses.POST, "https://bap.example.com/on_update", status=503)
        routing.relay_on_update(payload=payload, authorization_header="irrelevant")


def test_relay_on_update_logs_and_returns_when_bap_uri_missing():
    payload = _build_on_update_context()
    del payload["context"]["bap_uri"]
    routing.relay_on_update(payload=payload, authorization_header="irrelevant")


# --- track / on_track (livetracker2.md Phase 3.5) -------------------------------------------


def _build_track_context(*, bap_id="bap.example.com", bpp_id="bpp.example.com"):
    return {
        "context": {
            "domain": "ONDC:RET13",
            "location": {"country": {"code": "IND"}},
            "action": "track",
            "version": "1.1.0",
            "bap_id": bap_id,
            "bap_uri": f"https://{bap_id}",
            "bpp_id": bpp_id,
            "bpp_uri": f"https://{bpp_id}",
            "transaction_id": "txn-1",
            "message_id": "msg-1",
            "timestamp": "2026-07-20T00:00:00Z",
        },
        "message": {"order_id": "booking-1"},
    }


def _build_on_track_context(*, bap_id="bap.example.com", bpp_id="bpp.example.com"):
    """/on_track's message carries `tracking`, not `order` (§L.4/§L.5)."""
    return {
        "context": {
            "domain": "ONDC:RET13",
            "location": {"country": {"code": "IND"}},
            "action": "on_track",
            "version": "1.1.0",
            "bap_id": bap_id,
            "bap_uri": f"https://{bap_id}",
            "bpp_id": bpp_id,
            "bpp_uri": f"https://{bpp_id}",
            "transaction_id": "txn-1",
            "message_id": "msg-1",
            "timestamp": "2026-07-20T00:00:00Z",
        },
        "message": {"tracking": {"status": "inactive"}},
    }


@patch("core.routing.dispatch_track_in_background")
def test_track_view_acks_immediately_for_a_validly_signed_request(mock_dispatch, client):
    bap_pub, bap_priv = generate_signing_key_pair()
    payload = _build_track_context()
    body = json.dumps(payload).encode()
    header = sign_outbound_request(
        body=body,
        subscriber_id="bap.example.com",
        unique_key_id="key-1",
        signing_private_key_b64=bap_priv,
    )

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST,
            "http://registry:8000/lookup",
            callback=_lookup_callback(bap_pub, []),
        )
        resp = client.post(
            reverse("track"), data=body, content_type="application/json", HTTP_AUTHORIZATION=header
        )

    assert resp.status_code == 200
    assert resp.json() == {
        "context": payload["context"],
        "message": {"ack": {"status": "ACK"}},
    }
    mock_dispatch.assert_called_once_with(payload=payload, authorization_header=header)


def test_dispatch_track_forwards_to_the_specific_bpp_with_both_signatures(settings):
    settings.SUBSCRIBER_ID = "gateway.local"
    settings.UNIQUE_KEY_ID = "key1"
    payload = _build_track_context()
    original_auth_header = 'Signature keyId="bap.example.com|key-1|ed25519",...(original)'

    lookup_requests = []
    captured_requests = []

    def lookup_callback(request):
        lookup_requests.append(json.loads(request.body))
        return (
            200,
            {},
            json.dumps(
                [
                    {
                        "subscriber_id": "bpp.example.com",
                        "url": "https://bpp.example.com",
                        "status": "SUBSCRIBED",
                    }
                ]
            ),
        )

    def bpp_track_callback(request):
        captured_requests.append(request)
        return (200, {}, json.dumps({"message": {"ack": {"status": "ACK"}}}))

    with responses.RequestsMock() as rsps:
        rsps.add_callback(responses.POST, "http://registry:8000/lookup", callback=lookup_callback)
        rsps.add_callback(
            responses.POST, "https://bpp.example.com/track", callback=bpp_track_callback
        )
        routing.dispatch_track(payload=payload, authorization_header=original_auth_header)

    assert lookup_requests == [{"subscriber_id": "bpp.example.com"}]
    assert len(captured_requests) == 1
    forwarded = captured_requests[0]
    assert json.loads(forwarded.body) == payload
    assert forwarded.headers["Authorization"] == original_auth_header
    assert forwarded.headers["X-Gateway-Authorization"].startswith("Signature keyId=")
    assert "gateway.local" in forwarded.headers["X-Gateway-Authorization"]


def test_dispatch_track_does_not_forward_when_bpp_no_longer_subscribed():
    payload = _build_track_context()

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST,
            "http://registry:8000/lookup",
            callback=lambda request: (
                200,
                {},
                json.dumps(
                    [
                        {
                            "subscriber_id": "bpp.example.com",
                            "url": "https://bpp.example.com",
                            "status": "UNDER_SUBSCRIPTION",
                        }
                    ]
                ),
            ),
        )
        routing.dispatch_track(payload=payload, authorization_header="irrelevant")


def test_dispatch_track_does_not_raise_when_bpp_is_unreachable():
    payload = _build_track_context()

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST,
            "http://registry:8000/lookup",
            callback=lambda request: (
                200,
                {},
                json.dumps(
                    [
                        {
                            "subscriber_id": "bpp.example.com",
                            "url": "https://bpp.example.com",
                            "status": "SUBSCRIBED",
                        }
                    ]
                ),
            ),
        )
        rsps.add(responses.POST, "https://bpp.example.com/track", status=503)
        routing.dispatch_track(payload=payload, authorization_header="irrelevant")


def test_track_view_rejects_missing_context_field(client):
    payload = _build_track_context()
    del payload["context"]["bap_id"]
    body = json.dumps(payload).encode()

    resp = client.post(
        reverse("track"),
        data=body,
        content_type="application/json",
        HTTP_AUTHORIZATION="irrelevant",
    )
    assert resp.status_code == 400


@patch("core.routing.relay_on_track_in_background")
def test_on_track_view_acks_immediately_for_a_validly_signed_bpp_callback(mock_relay, client):
    bpp_pub, bpp_priv = generate_signing_key_pair()
    payload = _build_on_track_context()
    body = json.dumps(payload).encode()
    header = sign_outbound_request(
        body=body,
        subscriber_id="bpp.example.com",
        unique_key_id="key-1",
        signing_private_key_b64=bpp_priv,
    )

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST,
            "http://registry:8000/lookup",
            callback=lambda request: (
                200,
                {},
                json.dumps(
                    [
                        {
                            "subscriber_id": "bpp.example.com",
                            "status": "SUBSCRIBED",
                            "signing_public_key": bpp_pub,
                        }
                    ]
                ),
            ),
        )
        resp = client.post(
            reverse("on_track"),
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION=header,
        )

    assert resp.status_code == 200
    assert resp.json() == {
        "context": payload["context"],
        "message": {"ack": {"status": "ACK"}},
    }
    mock_relay.assert_called_once_with(payload=payload, authorization_header=header)


def test_relay_on_track_forwards_to_the_bap_with_both_signatures():
    payload = _build_on_track_context()
    original_auth_header = 'Signature keyId="bpp.example.com|key-1|ed25519",...(original)'

    captured_requests = []

    def bap_on_track_callback(request):
        captured_requests.append(request)
        return (200, {}, json.dumps({"message": {"ack": {"status": "ACK"}}}))

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST, "https://bap.example.com/on_track", callback=bap_on_track_callback
        )
        routing.relay_on_track(payload=payload, authorization_header=original_auth_header)

    assert len(captured_requests) == 1
    forwarded = captured_requests[0]
    assert json.loads(forwarded.body) == payload
    assert forwarded.headers["Authorization"] == original_auth_header
    assert forwarded.headers["X-Gateway-Authorization"].startswith("Signature keyId=")


def test_relay_on_track_does_not_raise_when_bap_is_unreachable():
    payload = _build_on_track_context()

    with responses.RequestsMock() as rsps:
        rsps.add(responses.POST, "https://bap.example.com/on_track", status=503)
        routing.relay_on_track(payload=payload, authorization_header="irrelevant")


def test_relay_on_track_logs_and_returns_when_bap_uri_missing():
    payload = _build_on_track_context()
    del payload["context"]["bap_uri"]
    routing.relay_on_track(payload=payload, authorization_header="irrelevant")
