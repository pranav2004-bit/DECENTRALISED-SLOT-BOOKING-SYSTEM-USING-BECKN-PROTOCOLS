"""End-to-end tests for Phase 2.1/2.2/4.3: Subscribe -> on_subscribe challenge dispatch ->
verification -> Lookup, plus real server-side Authorization enforcement (Phase 4.3 — closes
the gap flagged open at Phase 3 exit). The participant side (their /on_subscribe endpoint)
is mocked via `responses`, per TESTING.md's "mocked HTTP boundary" convention — this proves
our Registry's own logic is correct without needing a real external participant or the
real ONDC network (that's what the Phase 2.0 sandbox spike is for, separately).
"""

import json
import time

import pytest
import responses
from beckn_crypto import (
    build_authorization_header,
    build_signing_string,
    build_verification_file_content,
    compute_blake512_digest,
    decrypt_challenge,
    generate_encryption_key_pair,
    generate_signing_key_pair,
    sign_request,
)
from django.core.cache import cache
from django.test import Client
from django.utils import timezone

from core.models import AuditLogEntry, Challenge, Participant
from core.registry_keys import get_registry_encryption_keys


@pytest.fixture(autouse=True)
def _clear_rate_limit_cache():
    """Found for real running the full Phase 4.3 suite: this file never cleared the
    rate-limit cache between tests (unlike test_security.py, which does) — harmless
    while the file had few /subscribe calls, but Subscribe's real 10/min limit
    (protocol_compliance_notes_v1.1.md §B.6) started tripping for real once enough
    tests accumulated calls within one pytest session, causing unrelated tests near the
    end of the file to fail with 429 instead of their expected status."""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def client():
    return Client()


def _sign_body(*, body: bytes, subscriber_id: str, signing_priv: str, unique_key_id: str = "key-1") -> str:
    """Builds a real Authorization header value for a test request — mirrors
    BAP/BPP/Gateway's core/crypto.py::sign_outbound_request, needed here only because
    Registry's own tests must now simulate a signed inbound caller (Phase 4.3)."""
    created = int(time.time())
    expires = created + 30
    digest_b64 = compute_blake512_digest(body)
    signing_string = build_signing_string(created=created, expires=expires, digest_b64=digest_b64)
    signature_b64 = sign_request(signing_string=signing_string, private_key_b64=signing_priv)
    return build_authorization_header(
        subscriber_id=subscriber_id,
        unique_key_id=unique_key_id,
        algorithm="ed25519",
        created=created,
        expires=expires,
        signature_b64=signature_b64,
    )


def _mock_valid_domain_verification(*, subscriber_url, request_id, signing_priv):
    """Registers a passing GET .../ondc-site-verification.html mock — every subscribe
    test needs this now that Subscribe fetches+validates it for real
    (protocol_compliance_notes_v1.1.md §B.2)."""
    content = build_verification_file_content(
        request_id=request_id, signing_private_key_b64=signing_priv
    )
    responses.add(
        responses.GET,
        subscriber_url.rstrip("/") + "/ondc-site-verification.html",
        body=content,
        status=200,
    )


def _build_subscribe_payload(
    *,
    subscriber_id,
    subscriber_url,
    domain,
    participant_type,
    signing_pub,
    encryption_pub,
    unique_key_id="key-1",
    request_id="req-1",
):
    ops_no = 1 if participant_type == "buyerApp" else 2
    now = timezone.now().isoformat()
    later = (timezone.now() + timezone.timedelta(days=365)).isoformat()
    return {
        "context": {"operation": {"ops_no": ops_no}},
        "message": {
            "request_id": request_id,
            "timestamp": now,
            "entity": {
                "subscriber_id": subscriber_id,
                "unique_key_id": unique_key_id,
                "callback_url": "/on_subscribe",
                "country": "IND",
                "key_pair": {
                    "signing_public_key": signing_pub,
                    "encryption_public_key": encryption_pub,
                    "valid_from": now,
                    "valid_until": later,
                },
            },
            "network_participant": [
                {"subscriber_url": subscriber_url, "domain": domain, "type": participant_type}
            ],
        },
    }


def _post_subscribe(client, payload, *, signing_priv, unique_key_id="key-1"):
    body = json.dumps(payload).encode()
    header = _sign_body(
        body=body,
        subscriber_id=payload["message"]["entity"]["subscriber_id"],
        signing_priv=signing_priv,
        unique_key_id=unique_key_id,
    )
    return client.post("/subscribe", data=body, content_type="application/json", HTTP_AUTHORIZATION=header)


@pytest.mark.django_db
@responses.activate
def test_subscribe_full_flow_reaches_subscribed_status(client):
    """Full real flow: participant calls /subscribe -> Registry dispatches an encrypted
    challenge to the participant's mocked on_subscribe -> participant (mock) decrypts it
    for real using beckn_crypto -> Registry verifies the answer -> status becomes SUBSCRIBED."""
    signing_pub, signing_priv = generate_signing_key_pair()
    encryption_pub, encryption_priv = generate_encryption_key_pair()

    payload = _build_subscribe_payload(
        subscriber_id="bpp.example.com",
        subscriber_url="https://bpp.example.com",
        domain="ONDC:RET13",
        participant_type="sellerApp",
        signing_pub=signing_pub,
        encryption_pub=encryption_pub,
    )
    _mock_valid_domain_verification(
        subscriber_url="https://bpp.example.com", request_id="req-1", signing_priv=signing_priv
    )

    def on_subscribe_callback(request):
        body = json.loads(request.body)
        registry_pub, _ = get_registry_encryption_keys()
        answer = decrypt_challenge(
            encrypted_challenge=body["challenge"],
            own_private_key_b64=encryption_priv,
            peer_public_key_b64_der=registry_pub,
        )
        return (200, {}, json.dumps({"answer": answer}))

    responses.add_callback(
        responses.POST, "https://bpp.example.com/on_subscribe", callback=on_subscribe_callback
    )

    resp = _post_subscribe(client, payload, signing_priv=signing_priv)
    assert resp.status_code == 200
    assert resp.json() == {"status": "UNDER_SUBSCRIPTION"}  # confirmed immediate response shape

    participant = Participant.objects.get(subscriber_id="bpp.example.com")
    assert (
        participant.status == Participant.Status.SUBSCRIBED
    )  # verified synchronously within the call


@pytest.mark.django_db
@responses.activate
def test_subscribe_stays_under_subscription_on_wrong_answer(client):
    signing_pub, signing_priv = generate_signing_key_pair()
    encryption_pub, _ = generate_encryption_key_pair()
    payload = _build_subscribe_payload(
        subscriber_id="bad.example.com",
        subscriber_url="https://bad.example.com",
        domain="ONDC:RET13",
        participant_type="sellerApp",
        signing_pub=signing_pub,
        encryption_pub=encryption_pub,
    )
    _mock_valid_domain_verification(
        subscriber_url="https://bad.example.com", request_id="req-1", signing_priv=signing_priv
    )
    responses.add(
        responses.POST,
        "https://bad.example.com/on_subscribe",
        json={"answer": "wrong-answer-entirely"},
        status=200,
    )

    resp = _post_subscribe(client, payload, signing_priv=signing_priv)
    assert resp.status_code == 200  # subscribe itself still ACKs synchronously

    participant = Participant.objects.get(subscriber_id="bad.example.com")
    assert participant.status == Participant.Status.UNDER_SUBSCRIPTION  # never promoted

    assert AuditLogEntry.objects.filter(event_type="CHALLENGE_ANSWER_MISMATCH").exists()


@pytest.mark.django_db
@responses.activate
def test_subscribe_handles_unreachable_participant_gracefully(client):
    signing_pub, signing_priv = generate_signing_key_pair()
    encryption_pub, _ = generate_encryption_key_pair()
    payload = _build_subscribe_payload(
        subscriber_id="unreachable.example.com",
        subscriber_url="https://unreachable.example.com",
        domain="ONDC:RET13",
        participant_type="sellerApp",
        signing_pub=signing_pub,
        encryption_pub=encryption_pub,
    )
    _mock_valid_domain_verification(
        subscriber_url="https://unreachable.example.com",
        request_id="req-1",
        signing_priv=signing_priv,
    )
    responses.add(
        responses.POST,
        "https://unreachable.example.com/on_subscribe",
        body=ConnectionError("simulated network failure"),
    )

    resp = _post_subscribe(client, payload, signing_priv=signing_priv)
    assert resp.status_code == 200  # /subscribe itself doesn't fail even if dispatch does

    participant = Participant.objects.get(subscriber_id="unreachable.example.com")
    assert participant.status == Participant.Status.UNDER_SUBSCRIPTION
    assert AuditLogEntry.objects.filter(event_type="ON_SUBSCRIBE_DISPATCH_FAILED").exists()


@pytest.mark.django_db
@responses.activate
def test_subscribe_rejects_when_domain_verification_file_missing(client):
    """NEG: no ondc-site-verification.html hosted at all — Registry must reject Subscribe
    rather than silently trusting the submitted keys (protocol_compliance_notes_v1.1.md
    §B.2 — 'do not treat as optional')."""
    signing_pub, signing_priv = generate_signing_key_pair()
    encryption_pub, _ = generate_encryption_key_pair()
    payload = _build_subscribe_payload(
        subscriber_id="unverified.example.com",
        subscriber_url="https://unverified.example.com",
        domain="ONDC:RET13",
        participant_type="sellerApp",
        signing_pub=signing_pub,
        encryption_pub=encryption_pub,
    )
    responses.add(
        responses.GET,
        "https://unverified.example.com/ondc-site-verification.html",
        status=404,
    )

    resp = _post_subscribe(client, payload, signing_priv=signing_priv)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "DOMAIN_VERIFICATION_FAILED"
    assert not Participant.objects.filter(subscriber_id="unverified.example.com").exists()


@pytest.mark.django_db
@responses.activate
def test_subscribe_rejects_when_domain_verification_signature_is_wrong(client):
    """NEG: file is hosted but signed with a different key than the one submitted in the
    Subscribe payload — a spoofing attempt, must be rejected."""
    signing_pub, signing_priv = generate_signing_key_pair()
    _, wrong_signing_priv = generate_signing_key_pair()
    encryption_pub, _ = generate_encryption_key_pair()
    payload = _build_subscribe_payload(
        subscriber_id="spoofed.example.com",
        subscriber_url="https://spoofed.example.com",
        domain="ONDC:RET13",
        participant_type="sellerApp",
        signing_pub=signing_pub,
        encryption_pub=encryption_pub,
    )
    _mock_valid_domain_verification(
        subscriber_url="https://spoofed.example.com",
        request_id="req-1",
        signing_priv=wrong_signing_priv,  # signed with the WRONG key
    )

    resp = _post_subscribe(client, payload, signing_priv=signing_priv)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "DOMAIN_VERIFICATION_FAILED"
    assert AuditLogEntry.objects.filter(
        event_type="DOMAIN_VERIFICATION_SIGNATURE_INVALID"
    ).exists()


@pytest.mark.django_db
def test_subscribe_rejects_deprecated_ops_no(client):
    payload = _build_subscribe_payload(
        subscriber_id="x.example.com",
        subscriber_url="https://x.example.com",
        domain="ONDC:RET13",
        participant_type="sellerApp",
        signing_pub="a",
        encryption_pub="b",
    )
    payload["context"]["operation"]["ops_no"] = (
        3  # deprecated per protocol_compliance_notes_v1.1.md §B.3
    )
    resp = client.post("/subscribe", data=json.dumps(payload), content_type="application/json")
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.django_db
def test_subscribe_rejects_malformed_payload(client):
    resp = client.post(
        "/subscribe", data=json.dumps({"not": "valid"}), content_type="application/json"
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.django_db
def test_subscribe_rejects_missing_authorization_header(client):
    """NEG/SEC (Phase 4.3): a syntactically valid Subscribe payload with no Authorization
    header at all must be rejected — closes the gap flagged open at Phase 3 exit."""
    signing_pub, _ = generate_signing_key_pair()
    encryption_pub, _ = generate_encryption_key_pair()
    payload = _build_subscribe_payload(
        subscriber_id="noauth.example.com",
        subscriber_url="https://noauth.example.com",
        domain="ONDC:RET13",
        participant_type="sellerApp",
        signing_pub=signing_pub,
        encryption_pub=encryption_pub,
    )
    resp = client.post("/subscribe", data=json.dumps(payload), content_type="application/json")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHORIZED"
    assert not Participant.objects.filter(subscriber_id="noauth.example.com").exists()


@pytest.mark.django_db
def test_subscribe_rejects_signature_from_a_key_not_matching_the_payload(client):
    """SEC (Phase 4.3): first-time Subscribe signed with a key that is NOT the one being
    submitted in the payload — proof-of-possession must fail, this would otherwise let
    an attacker register a public key they don't control."""
    signing_pub, _ = generate_signing_key_pair()
    _, attacker_priv = generate_signing_key_pair()
    encryption_pub, _ = generate_encryption_key_pair()
    payload = _build_subscribe_payload(
        subscriber_id="impersonated.example.com",
        subscriber_url="https://impersonated.example.com",
        domain="ONDC:RET13",
        participant_type="sellerApp",
        signing_pub=signing_pub,
        encryption_pub=encryption_pub,
    )
    resp = _post_subscribe(client, payload, signing_priv=attacker_priv)
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHORIZED"


@pytest.mark.django_db
@responses.activate
def test_resubscribe_same_subscriber_is_idempotent_not_duplicated(client):
    """Re-subscribing (e.g. key rotation) updates the existing row, per confirmed
    protocol_compliance_notes_v1.1.md §B.4 — no separate rotation endpoint, no duplicate rows.
    Per Phase 4.3, the second call must be signed with the CURRENTLY REGISTERED key (proving
    the caller is the legitimate current key holder), not the new key being rotated in."""
    signing_pub, signing_priv = generate_signing_key_pair()
    encryption_pub, encryption_priv = generate_encryption_key_pair()
    payload = _build_subscribe_payload(
        subscriber_id="rotate.example.com",
        subscriber_url="https://rotate.example.com",
        domain="ONDC:RET13",
        participant_type="sellerApp",
        signing_pub=signing_pub,
        encryption_pub=encryption_pub,
    )
    _mock_valid_domain_verification(
        subscriber_url="https://rotate.example.com", request_id="req-1", signing_priv=signing_priv
    )
    responses.add(
        responses.POST,
        "https://rotate.example.com/on_subscribe",
        json={"answer": "irrelevant"},
        status=200,
    )

    _post_subscribe(client, payload, signing_priv=signing_priv)
    assert Participant.objects.filter(subscriber_id="rotate.example.com").count() == 1

    new_signing_pub, new_signing_priv = generate_signing_key_pair()
    payload["message"]["entity"]["key_pair"]["signing_public_key"] = new_signing_pub
    payload["message"]["request_id"] = "req-2"
    _mock_valid_domain_verification(
        subscriber_url="https://rotate.example.com",
        request_id="req-2",
        signing_priv=new_signing_priv,
    )
    # Signed with the OLD key — the legitimate rotation path.
    _post_subscribe(client, payload, signing_priv=signing_priv)

    assert (
        Participant.objects.filter(subscriber_id="rotate.example.com").count() == 1
    )  # still one row
    participant = Participant.objects.get(subscriber_id="rotate.example.com")
    assert participant.signing_public_key == new_signing_pub  # rotated in place


@pytest.mark.django_db
@responses.activate
def test_resubscribe_signed_with_new_key_instead_of_old_is_rejected(client):
    """SEC (Phase 4.3): a third party who doesn't hold the CURRENT key can't "steal" an
    already-registered subscriber_id by generating their own new key pair and signing
    the re-Subscribe with that new key instead of the one on file."""
    signing_pub, signing_priv = generate_signing_key_pair()
    encryption_pub, _ = generate_encryption_key_pair()
    payload = _build_subscribe_payload(
        subscriber_id="protected.example.com",
        subscriber_url="https://protected.example.com",
        domain="ONDC:RET13",
        participant_type="sellerApp",
        signing_pub=signing_pub,
        encryption_pub=encryption_pub,
    )
    _mock_valid_domain_verification(
        subscriber_url="https://protected.example.com", request_id="req-1", signing_priv=signing_priv
    )
    responses.add(
        responses.POST,
        "https://protected.example.com/on_subscribe",
        json={"answer": "irrelevant"},
        status=200,
    )
    _post_subscribe(client, payload, signing_priv=signing_priv)
    assert Participant.objects.get(subscriber_id="protected.example.com").signing_public_key == signing_pub

    attacker_new_pub, attacker_new_priv = generate_signing_key_pair()
    payload["message"]["entity"]["key_pair"]["signing_public_key"] = attacker_new_pub
    payload["message"]["request_id"] = "req-2"
    # Signed with the NEW (attacker's) key, not the currently-registered one.
    resp = _post_subscribe(client, payload, signing_priv=attacker_new_priv)

    assert resp.status_code == 401
    assert (
        Participant.objects.get(subscriber_id="protected.example.com").signing_public_key
        == signing_pub  # unchanged
    )


@pytest.mark.django_db
def test_challenge_replay_is_rejected():
    """Direct unit test of the replay-protection path (EDGE case), independent of the
    HTTP dispatch flow above."""
    from core.registry_service import verify_challenge_answer

    encryption_pub, encryption_priv = generate_encryption_key_pair()
    signing_pub, _ = generate_signing_key_pair()
    payload = _build_subscribe_payload(
        subscriber_id="replay.example.com",
        subscriber_url="https://replay.example.com",
        domain="ONDC:RET13",
        participant_type="sellerApp",
        signing_pub=signing_pub,
        encryption_pub=encryption_pub,
    )
    entity = payload["message"]["entity"]
    from django.utils.dateparse import parse_datetime

    participant = Participant.objects.create(
        subscriber_id=entity["subscriber_id"],
        subscriber_url="https://replay.example.com",
        participant_type="sellerApp",
        domain="ONDC:RET13",
        unique_key_id=entity["unique_key_id"],
        signing_public_key=signing_pub,
        encryption_public_key=encryption_pub,
        key_valid_from=parse_datetime(entity["key_pair"]["valid_from"]),
        key_valid_until=parse_datetime(entity["key_pair"]["valid_until"]),
        callback_url="/on_subscribe",
        status=Participant.Status.UNDER_SUBSCRIPTION,
    )
    challenge = Challenge.objects.create(
        participant=participant,
        plaintext_challenge="secret-value",
        encrypted_challenge="irrelevant-for-this-test",
        expires_at=timezone.now() + timezone.timedelta(seconds=60),
    )

    first = verify_challenge_answer(challenge, "secret-value")
    assert first is True

    challenge.refresh_from_db()
    second = verify_challenge_answer(challenge, "secret-value")  # replay attempt
    assert second is False
    assert AuditLogEntry.objects.filter(event_type="CHALLENGE_REPLAY_REJECTED").exists()


def _create_participant_with_real_key(*, subscriber_id, domain="ONDC:RET13"):
    """Test helper (Phase 4.3): Lookup now requires a real, verifiable caller identity,
    so tests need a Participant backed by a real key pair, not the placeholder "sp"/"ep"
    strings the pre-4.3 tests used (which can't produce a valid signature)."""
    from django.utils.dateparse import parse_datetime

    signing_pub, signing_priv = generate_signing_key_pair()
    encryption_pub, _ = generate_encryption_key_pair()
    participant = Participant.objects.create(
        subscriber_id=subscriber_id,
        subscriber_url=f"https://{subscriber_id}",
        participant_type="sellerApp",
        domain=domain,
        country="IND",
        unique_key_id="k1",
        signing_public_key=signing_pub,
        encryption_public_key=encryption_pub,
        key_valid_from=parse_datetime("2026-01-01T00:00:00Z"),
        key_valid_until=parse_datetime("2027-01-01T00:00:00Z"),
        callback_url="/on_subscribe",
        status=Participant.Status.SUBSCRIBED,
    )
    return participant, signing_priv


@pytest.mark.django_db
def test_lookup_returns_matching_participants(client):
    _, signing_priv = _create_participant_with_real_key(subscriber_id="lookup-me.example.com")

    body = json.dumps({"domain": "ONDC:RET13"}).encode()
    header = _sign_body(body=body, subscriber_id="lookup-me.example.com", signing_priv=signing_priv)
    resp = client.post("/lookup", data=body, content_type="application/json", HTTP_AUTHORIZATION=header)

    assert resp.status_code == 200
    results = resp.json()
    assert len(results) == 1
    assert results[0]["subscriber_id"] == "lookup-me.example.com"
    assert results[0]["status"] == "SUBSCRIBED"


@pytest.mark.django_db
def test_lookup_with_no_matches_returns_empty_array(client):
    # The caller must be a known, registered participant; the query itself can still
    # legitimately match nothing.
    _, caller_priv = _create_participant_with_real_key(subscriber_id="caller.example.com")

    body = json.dumps({"subscriber_id": "nobody.example.com"}).encode()
    header = _sign_body(body=body, subscriber_id="caller.example.com", signing_priv=caller_priv)
    resp = client.post("/lookup", data=body, content_type="application/json", HTTP_AUTHORIZATION=header)

    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.django_db
def test_lookup_rejects_missing_authorization_header(client):
    """NEG/SEC (Phase 4.3): closes the gap flagged open at Phase 3 exit — Lookup without
    any Authorization header must be rejected, not silently served."""
    _create_participant_with_real_key(subscriber_id="lookup-me.example.com")
    resp = client.post(
        "/lookup", data=json.dumps({"domain": "ONDC:RET13"}), content_type="application/json"
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHORIZED"


@pytest.mark.django_db
def test_lookup_rejects_unknown_subscriber(client):
    """SEC (Phase 4.3): a syntactically valid signature from a subscriber_id Registry has
    never seen must be rejected — signing alone isn't enough, the caller must be a known
    registered participant."""
    _, ghost_priv = generate_signing_key_pair()
    body = json.dumps({"domain": "ONDC:RET13"}).encode()
    header = _sign_body(body=body, subscriber_id="ghost.example.com", signing_priv=ghost_priv)
    resp = client.post("/lookup", data=body, content_type="application/json", HTTP_AUTHORIZATION=header)
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHORIZED"


@pytest.mark.django_db
def test_lookup_rejects_forged_signature(client):
    """SEC (Phase 4.3): a request claiming to be a real registered subscriber_id, but
    signed with a different key, must be rejected — impersonation attempt."""
    _create_participant_with_real_key(subscriber_id="real.example.com")
    _, attacker_priv = generate_signing_key_pair()
    body = json.dumps({"domain": "ONDC:RET13"}).encode()
    header = _sign_body(body=body, subscriber_id="real.example.com", signing_priv=attacker_priv)
    resp = client.post("/lookup", data=body, content_type="application/json", HTTP_AUTHORIZATION=header)
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHORIZED"
