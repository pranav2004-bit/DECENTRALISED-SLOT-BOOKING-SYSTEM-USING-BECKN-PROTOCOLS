"""Phase 2.5 Registry Security Hardening tests: rate limiting at the real ONDC
thresholds (protocol_compliance_notes_v1.1.md §B.6), and basic abuse/malformed-input
resilience.
"""

import json
import time

import pytest
from beckn_crypto import (
    build_authorization_header,
    build_signing_string,
    compute_blake512_digest,
    generate_signing_key_pair,
    sign_request,
)
from django.core.cache import cache
from django.test import Client
from django.utils.dateparse import parse_datetime

from core.models import Participant


@pytest.fixture(autouse=True)
def _clear_rate_limit_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def client():
    return Client()


def _sign_body(
    *, body: bytes, subscriber_id: str, signing_priv: str, unique_key_id: str = "key-1"
) -> str:
    """Same helper as test_subscribe_flow.py — Phase 4.3 requires a real signed caller
    for /lookup now."""
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


def _create_registered_caller(subscriber_id="caller.example.com"):
    signing_pub, signing_priv = generate_signing_key_pair()
    Participant.objects.create(
        subscriber_id=subscriber_id,
        subscriber_url=f"https://{subscriber_id}",
        participant_type="sellerApp",
        domain="ONDC:RET13",
        country="IND",
        unique_key_id="k1",
        signing_public_key=signing_pub,
        encryption_public_key="irrelevant-for-this-test",
        key_valid_from=parse_datetime("2026-01-01T00:00:00Z"),
        key_valid_until=parse_datetime("2027-01-01T00:00:00Z"),
        callback_url="/on_subscribe",
        status=Participant.Status.SUBSCRIBED,
    )
    return subscriber_id, signing_priv


@pytest.mark.django_db
def test_subscribe_rate_limit_blocks_after_threshold(client):
    """Real ONDC limit: Subscribe = 10 req/min (protocol_compliance_notes_v1.1.md §B.6)."""
    bad_payload = json.dumps({"not": "valid"})  # 400s still count against the rate limit
    for _ in range(10):
        resp = client.post("/subscribe", data=bad_payload, content_type="application/json")
        assert resp.status_code != 429

    resp = client.post("/subscribe", data=bad_payload, content_type="application/json")
    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "RATE_LIMITED"


@pytest.mark.django_db
def test_lookup_rate_limit_is_much_higher_than_subscribe(client):
    """Real ONDC limit: Lookup = 7,600 req/min — confirms it's NOT sharing the
    Subscribe counter/limit (a plausible copy-paste bug this test would catch)."""
    subscriber_id, signing_priv = _create_registered_caller()
    body = json.dumps({}).encode()
    header = _sign_body(body=body, subscriber_id=subscriber_id, signing_priv=signing_priv)
    for _ in range(50):
        resp = client.post(
            "/lookup", data=body, content_type="application/json", HTTP_AUTHORIZATION=header
        )
        assert resp.status_code == 200  # far below both the 10/min and 7600/min ceilings


@pytest.mark.django_db
def test_rate_limit_is_scoped_per_client_ip(client):
    """Two different client IPs must not share one counter."""
    bad_payload = json.dumps({"not": "valid"})
    for _ in range(10):
        client.post(
            "/subscribe", data=bad_payload, content_type="application/json", REMOTE_ADDR="1.1.1.1"
        )
    blocked = client.post(
        "/subscribe", data=bad_payload, content_type="application/json", REMOTE_ADDR="1.1.1.1"
    )
    assert blocked.status_code == 429

    still_allowed = client.post(
        "/subscribe", data=bad_payload, content_type="application/json", REMOTE_ADDR="2.2.2.2"
    )
    assert still_allowed.status_code != 429


@pytest.mark.django_db
def test_oversized_payload_rejected_cleanly_not_500(client):
    """SEC/EDGE: a large garbage body must be rejected as a clean 400, never crash the
    handler with an unhandled 500."""
    huge_garbage = "x" * (2 * 1024 * 1024)  # 2MB of non-JSON garbage
    resp = client.post("/subscribe", data=huge_garbage, content_type="application/json")
    assert resp.status_code == 400


@pytest.mark.django_db
def test_sql_injection_like_input_in_lookup_is_handled_safely(client):
    """POS/SEC: Django ORM parameterizes queries — this input must not error, and must
    simply match nothing rather than doing anything unsafe."""
    subscriber_id, signing_priv = _create_registered_caller()
    body = json.dumps({"subscriber_id": "'; DROP TABLE core_participant; --"}).encode()
    header = _sign_body(body=body, subscriber_id=subscriber_id, signing_priv=signing_priv)
    resp = client.post(
        "/lookup", data=body, content_type="application/json", HTTP_AUTHORIZATION=header
    )
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.django_db
def test_get_method_not_allowed_on_subscribe(client):
    resp = client.get("/subscribe")
    assert resp.status_code == 405


@pytest.mark.django_db
def test_get_method_not_allowed_on_lookup(client):
    resp = client.get("/lookup")
    assert resp.status_code == 405
