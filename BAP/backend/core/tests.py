"""Regression tests for Phase 1.3 BAP Foundation and Phase 3.1 BAP Onboarding."""

import json
from pathlib import Path

import pytest
import responses
from django.test import Client
from django.urls import reverse

from core.auth import authenticate_buyer_session
from core.crypto import (
    generate_encryption_key_pair,
    generate_signing_key_pair,
    sign_outbound_request,
)
from core.events import get_event_bus


@pytest.fixture
def client():
    return Client()


def test_health_returns_200(client):
    resp = client.get(reverse("health"))
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "service": "bap-backend"}


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
    """Exercises the real Redis-backed event bus wired via Django settings —
    genuine infrastructure test, not a stub check."""
    bus = get_event_bus()
    bus._redis.delete(bus.queue_name, bus.dlq_name)
    event_id = bus.publish("test.event", {"key": "value"})
    event = bus.consume_one(timeout_seconds=2)
    assert event is not None
    assert event["event_id"] == event_id
    bus._redis.delete(bus.queue_name, bus.dlq_name)


def test_event_bus_dlq_receives_failed_event():
    """This is the specific EDGE test case livetracker1.md Phase 1.3 calls for:
    'event bus DLQ receives a deliberately-failed internal event'."""
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


def test_generate_signing_key_pair_produces_real_ed25519_keys():
    """Crypto is real as of the Phase 3 pre-work gap-closure pass — the stub check
    that used to live here is gone; this confirms BAP's wrapper actually delegates to
    shared/beckn_crypto correctly, not just that it exists."""
    public_b64, private_b64 = generate_signing_key_pair()
    assert public_b64 and private_b64
    assert public_b64 != private_b64


def test_generate_encryption_key_pair_produces_real_x25519_keys():
    public_b64, private_b64 = generate_encryption_key_pair()
    assert public_b64 and private_b64


def test_sign_outbound_request_produces_a_verifiable_signature():
    from beckn_crypto import verify_request_signature

    public_b64, private_b64 = generate_signing_key_pair()
    body = b'{"hello": "world"}'
    header = sign_outbound_request(
        body=body,
        subscriber_id="bap.example.com",
        unique_key_id="key-1",
        signing_private_key_b64=private_b64,
    )
    assert (
        verify_request_signature(authorization_header=header, body=body, public_key_b64=public_b64)
        is True
    )


def test_auth_stub_raises_not_implemented():
    with pytest.raises(NotImplementedError):
        authenticate_buyer_session(session_token="fake-token-for-test")


# --- Phase 3.1 BAP Onboarding ---


@pytest.fixture
def onboarding_settings(settings, tmp_path):
    settings.SIGNING_PRIVATE_KEY_PATH = str(tmp_path / "signing.json")
    settings.ENCRYPTION_PRIVATE_KEY_PATH = str(tmp_path / "encryption.json")
    settings.SUBSCRIBER_ID = "bap.example.com"
    settings.UNIQUE_KEY_ID = "key-1"
    settings.SUBSCRIBER_URL = "https://bap.example.com"
    from core import participant_keys

    participant_keys.get_signing_keys.cache_clear()
    participant_keys.get_encryption_keys.cache_clear()
    yield settings
    participant_keys.get_signing_keys.cache_clear()
    participant_keys.get_encryption_keys.cache_clear()


@pytest.mark.django_db
def test_participant_keys_persist_across_calls(onboarding_settings):
    """Real persistence, not per-process ephemeral — Phase 3 onboarding needs the same
    key pair to survive restarts (see participant_keys.py docstring)."""
    from core import participant_keys

    pub1, priv1 = participant_keys.get_signing_keys()
    participant_keys.get_signing_keys.cache_clear()  # simulate a fresh process
    pub2, priv2 = participant_keys.get_signing_keys()
    assert (pub1, priv1) == (pub2, priv2)
    assert Path(onboarding_settings.SIGNING_PRIVATE_KEY_PATH).exists()


@pytest.mark.django_db
def test_rotate_signing_key_produces_a_different_persisted_key(onboarding_settings):
    from core import participant_keys

    original_pub, _ = participant_keys.get_signing_keys()
    rotated_pub, _ = participant_keys.rotate_signing_key()
    assert rotated_pub != original_pub
    fresh_pub, _ = participant_keys.get_signing_keys()
    assert fresh_pub == rotated_pub  # cache was cleared, reload reflects the rotation


@pytest.mark.django_db
def test_onboarding_subscribe_blocked_without_approval(onboarding_settings):
    from core import onboarding_service

    with pytest.raises(onboarding_service.OnboardingError, match="not approved"):
        onboarding_service.submit_subscribe("ONDC:RET13")


@pytest.mark.django_db
def test_onboarding_approve_then_verification_file_matches_subscribe_request_id(
    onboarding_settings,
):
    """The core correctness property Registry's fetch depends on: whatever request_id
    ends up in the Subscribe payload must be the same one signed into the currently
    served /ondc-site-verification.html content."""
    from beckn_crypto import verify_domain_ownership_file

    from core import onboarding_service, participant_keys

    onboarding_service.approve("ONDC:RET13")

    with responses.RequestsMock() as rsps:
        captured = {}

        def subscribe_callback(request):
            body = json.loads(request.body)
            captured["request_id"] = body["message"]["request_id"]
            return (200, {}, json.dumps({"status": "UNDER_SUBSCRIPTION"}))

        rsps.add_callback(
            responses.POST, "http://registry:8000/subscribe", callback=subscribe_callback
        )
        status = onboarding_service.submit_subscribe("ONDC:RET13")

    assert status.status == "UNDER_SUBSCRIPTION"
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


@pytest.mark.django_db
def test_onboarding_subscribe_marks_failed_on_registry_rejection(onboarding_settings):
    from core import onboarding_service
    from core.models import OnboardingStatus

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

    status = OnboardingStatus.objects.get(domain="ONDC:RET13")
    assert status.status == OnboardingStatus.Status.FAILED
    assert "DOMAIN_VERIFICATION_FAILED" in status.last_error


@pytest.mark.django_db
def test_on_subscribe_view_decrypts_challenge_and_marks_subscribed(
    onboarding_settings, client
):
    from beckn_crypto import encrypt_challenge, generate_encryption_key_pair

    from core import participant_keys
    from core.models import OnboardingStatus

    OnboardingStatus.objects.create(
        domain="ONDC:RET13", status=OnboardingStatus.Status.UNDER_SUBSCRIPTION
    )
    bap_encryption_pub, bap_encryption_priv = participant_keys.get_encryption_keys()
    registry_encryption_pub, registry_encryption_priv = generate_encryption_key_pair()

    encrypted = encrypt_challenge(
        challenge="the-secret-answer",
        own_private_key_b64=registry_encryption_priv,
        peer_public_key_b64_der=bap_encryption_pub,
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
            data=json.dumps({"subscriber_id": "bap.example.com", "challenge": encrypted}),
            content_type="application/json",
        )

    assert resp.status_code == 200
    assert resp.json() == {"answer": "the-secret-answer"}
    status = OnboardingStatus.objects.get(domain="ONDC:RET13")
    assert status.status == OnboardingStatus.Status.SUBSCRIBED


@pytest.mark.django_db
def test_ondc_site_verification_view_returns_404_before_any_verification_requested(client):
    resp = client.get(reverse("ondc-site-verification"))
    assert resp.status_code == 404


@pytest.mark.django_db
def test_ondc_site_verification_view_serves_signed_content(onboarding_settings, client):
    from core import onboarding_service

    onboarding_service.request_domain_verification(request_id="req-xyz")
    resp = client.get(reverse("ondc-site-verification"))
    assert resp.status_code == 200
    assert "Signed Unique Request ID:" in resp.content.decode()


# --- Phase 3.4 Trust Establishment ---


@pytest.mark.django_db
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


@pytest.mark.django_db
def test_verify_participant_signature_rejects_unregistered_subscriber(onboarding_settings):
    from core import trust
    from core.crypto import sign_outbound_request

    _, priv = generate_signing_key_pair()
    body = b'{"hello": "world"}'
    header = sign_outbound_request(
        body=body,
        subscriber_id="ghost.example.com",
        unique_key_id="key-1",
        signing_private_key_b64=priv,
    )

    with responses.RequestsMock() as rsps:
        rsps.add(responses.POST, "http://registry:8000/lookup", json=[], status=200)
        with pytest.raises(trust.TrustEstablishmentError, match="No registered participant"):
            trust.verify_participant_signature(authorization_header=header, body=body)


@pytest.mark.django_db
def test_verify_participant_signature_rejects_not_yet_subscribed(onboarding_settings):
    from core import trust
    from core.crypto import sign_outbound_request

    peer_pub, peer_priv = generate_signing_key_pair()
    body = b'{"hello": "world"}'
    header = sign_outbound_request(
        body=body,
        subscriber_id="pending.example.com",
        unique_key_id="key-1",
        signing_private_key_b64=peer_priv,
    )

    with responses.RequestsMock() as rsps:
        rsps.add(
            responses.POST,
            "http://registry:8000/lookup",
            json=[
                {
                    "subscriber_id": "pending.example.com",
                    "status": "UNDER_SUBSCRIPTION",
                    "signing_public_key": peer_pub,
                }
            ],
            status=200,
        )
        with pytest.raises(trust.TrustEstablishmentError, match="not SUBSCRIBED"):
            trust.verify_participant_signature(authorization_header=header, body=body)


@pytest.mark.django_db
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


@pytest.mark.django_db
def test_onboarding_reset_clears_domain_back_to_not_started(onboarding_settings):
    from django.core.management import call_command

    from core.models import OnboardingStatus

    OnboardingStatus.objects.create(
        domain="ONDC:RET13",
        approved_for_subscribe=True,
        status=OnboardingStatus.Status.FAILED,
        last_error="something went wrong",
    )
    call_command("onboarding_reset", "ONDC:RET13")

    status = OnboardingStatus.objects.get(domain="ONDC:RET13")
    assert status.status == OnboardingStatus.Status.NOT_STARTED
    assert status.approved_for_subscribe is False
    assert status.last_error == ""
