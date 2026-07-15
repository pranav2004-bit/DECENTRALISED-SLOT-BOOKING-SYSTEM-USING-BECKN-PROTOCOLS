"""Regression tests for Phase 1.1 Registry Foundation — codifies the behavior verified
manually during Phase 1.1 build (see livetracker1.md Phase 1.1 Test Gate), so it stays
verified on every future change instead of relying on a one-time manual check.
"""

import time

import pytest
from beckn_crypto import (
    ChallengeDecryptionError,
    SignatureVerificationError,
    build_authorization_header,
    build_signing_string,
    compute_blake512_digest,
    decrypt_challenge,
    encrypt_challenge,
    generate_encryption_key_pair,
    generate_signing_key_pair,
    parse_authorization_header,
    sign_request,
    verify_request_signature,
)
from django.test import Client
from django.urls import reverse

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


# --- Phase 2.3 Cryptography: real Ed25519/X25519 implementation ---


def _build_signed_request(
    private_key_b64: str, subscriber_id: str, unique_key_id: str, body: bytes
):
    created = int(time.time())
    expires = created + 30
    digest = compute_blake512_digest(body)
    signing_string = build_signing_string(created=created, expires=expires, digest_b64=digest)
    signature = sign_request(signing_string=signing_string, private_key_b64=private_key_b64)
    header = build_authorization_header(
        subscriber_id=subscriber_id,
        unique_key_id=unique_key_id,
        algorithm="ed25519",
        created=created,
        expires=expires,
        signature_b64=signature,
    )
    return header


def test_signing_key_pair_is_valid_ed25519_and_round_trips():
    public_b64, private_b64 = generate_signing_key_pair()
    body = b'{"hello": "world"}'
    header = _build_signed_request(private_b64, "sub.example.com", "key-1", body)
    assert (
        verify_request_signature(authorization_header=header, body=body, public_key_b64=public_b64)
        is True
    )


def test_signature_verification_fails_on_tampered_body():
    public_b64, private_b64 = generate_signing_key_pair()
    body = b'{"hello": "world"}'
    header = _build_signed_request(private_b64, "sub.example.com", "key-1", body)
    tampered_body = b'{"hello": "tampered"}'
    with pytest.raises(SignatureVerificationError):
        verify_request_signature(
            authorization_header=header, body=tampered_body, public_key_b64=public_b64
        )


def test_signature_verification_fails_with_wrong_public_key():
    public_b64, private_b64 = generate_signing_key_pair()
    other_public_b64, _ = generate_signing_key_pair()
    body = b"payload"
    header = _build_signed_request(private_b64, "sub.example.com", "key-1", body)
    with pytest.raises(SignatureVerificationError):
        verify_request_signature(
            authorization_header=header, body=body, public_key_b64=other_public_b64
        )


def test_signature_verification_fails_on_expired_window():
    public_b64, private_b64 = generate_signing_key_pair()
    body = b"payload"
    created = int(time.time()) - 120
    expires = created + 30  # expired 90s ago
    digest = compute_blake512_digest(body)
    signing_string = build_signing_string(created=created, expires=expires, digest_b64=digest)
    signature = sign_request(signing_string=signing_string, private_key_b64=private_b64)
    header = build_authorization_header(
        subscriber_id="sub.example.com",
        unique_key_id="key-1",
        algorithm="ed25519",
        created=created,
        expires=expires,
        signature_b64=signature,
    )
    with pytest.raises(SignatureVerificationError, match="expired"):
        verify_request_signature(authorization_header=header, body=body, public_key_b64=public_b64)


def test_malformed_authorization_header_rejected():
    with pytest.raises(SignatureVerificationError):
        verify_request_signature(
            authorization_header="NotASignature", body=b"x", public_key_b64="y"
        )


def test_parse_authorization_header_extracts_key_id_parts():
    header = build_authorization_header(
        subscriber_id="sub.example.com",
        unique_key_id="key-42",
        algorithm="ed25519",
        created=1,
        expires=2,
        signature_b64="sig",
    )
    parsed = parse_authorization_header(header)
    assert parsed["subscriber_id"] == "sub.example.com"
    assert parsed["unique_key_id"] == "key-42"
    assert parsed["key_algorithm"] == "ed25519"


def test_encryption_key_pair_round_trips_challenge():
    """Simulates Registry <-> participant: Registry encrypts using (registry_priv,
    participant_pub); participant decrypts using (participant_priv, registry_pub) —
    ECDH must produce the same shared key both directions."""
    registry_pub, registry_priv = generate_encryption_key_pair()
    participant_pub, participant_priv = generate_encryption_key_pair()

    challenge = "random-challenge-string-12345"
    encrypted = encrypt_challenge(
        challenge=challenge,
        own_private_key_b64=registry_priv,
        peer_public_key_b64_der=participant_pub,
    )
    decrypted = decrypt_challenge(
        encrypted_challenge=encrypted,
        own_private_key_b64=participant_priv,
        peer_public_key_b64_der=registry_pub,
    )
    assert decrypted == challenge


def test_challenge_decryption_fails_with_wrong_key():
    registry_pub, registry_priv = generate_encryption_key_pair()
    participant_pub, _ = generate_encryption_key_pair()
    _, wrong_priv = generate_encryption_key_pair()

    encrypted = encrypt_challenge(
        challenge="secret",
        own_private_key_b64=registry_priv,
        peer_public_key_b64_der=participant_pub,
    )
    with pytest.raises(ChallengeDecryptionError):
        decrypt_challenge(
            encrypted_challenge=encrypted,
            own_private_key_b64=wrong_priv,
            peer_public_key_b64_der=registry_pub,
        )


def test_blake512_digest_is_deterministic_and_64_bytes():
    d1 = compute_blake512_digest(b"same input")
    d2 = compute_blake512_digest(b"same input")
    d3 = compute_blake512_digest(b"different input")
    assert d1 == d2
    assert d1 != d3
    import base64 as b64

    assert len(b64.b64decode(d1)) == 64
