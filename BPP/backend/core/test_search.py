"""Phase 3.1 Test Gate (livetracker2.md §3.1) pieces owned by BPP: real /search
receipt (verifying both the BAP and the forwarding Gateway) and real /on_search
dispatch (building the real Beauty catalog and sending it to Gateway).
"""

import json
from unittest.mock import patch

import pytest
import responses
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from inventory_core.models import Resource

from core import search_service
from core.crypto import generate_signing_key_pair, sign_outbound_request

BusinessAccount = get_user_model()

# Test fixture value, not a real credential.
TEST_PASSWORD = "unused-in-this-test"  # pragma: allowlist secret


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def bpp_identity_settings(settings, tmp_path):
    """Deterministic BPP identity for these tests — never depend on ambient .env
    values, which differ between local dev and CI (e.g. GATEWAY_BASE_URL is
    `http://beckn-gateway:8000` in this repo's real .env, a real Docker service name,
    not something a unit test should assume or accidentally hit)."""
    settings.SIGNING_PRIVATE_KEY_PATH = str(tmp_path / "signing.json")
    settings.SUBSCRIBER_ID = "bpp-backend.local"
    settings.UNIQUE_KEY_ID = "key-1"
    settings.SUBSCRIBER_URL = "https://bpp-backend.local"
    settings.GATEWAY_BASE_URL = "http://gateway:8000"
    from core import participant_keys

    participant_keys.get_signing_keys.cache_clear()
    yield settings


def _build_search_payload(*, bap_id="bap.example.com"):
    return {
        "context": {
            "domain": "ONDC:RET13",
            "location": {"country": {"code": "IND"}},
            "action": "search",
            "version": "1.1.0",
            "bap_id": bap_id,
            "bap_uri": f"https://{bap_id}",
            "transaction_id": "txn-1",
            "message_id": "msg-1",
            "timestamp": "2026-07-19T00:00:00Z",
        },
        "message": {"intent": {}},
    }


def _lookup_callback(known_participants):
    def callback(request):
        filters = json.loads(request.body)
        subscriber_id = filters["subscriber_id"]
        entry = known_participants.get(subscriber_id)
        return (200, {}, json.dumps([entry] if entry else []))

    return callback


def _known(*, bap_pub=None, gateway_pub=None):
    known = {}
    if bap_pub is not None:
        known["bap.example.com"] = {
            "subscriber_id": "bap.example.com",
            "status": "SUBSCRIBED",
            "signing_public_key": bap_pub,
        }
    if gateway_pub is not None:
        known["gateway.local"] = {
            "subscriber_id": "gateway.local",
            "status": "SUBSCRIBED",
            "signing_public_key": gateway_pub,
        }
    return known


@pytest.mark.django_db
@patch("core.search_service.dispatch_on_search_in_background")
def test_search_view_acks_when_both_bap_and_gateway_signatures_are_valid(
    mock_dispatch, client
):
    """`dispatch_on_search_in_background` is mocked here deliberately — this test
    only checks the synchronous ACK response shape; real dispatch behavior (the
    actual catalog build + send to Gateway) is covered by
    test_dispatch_on_search_sends_the_real_catalog_to_gateway below, called directly
    and synchronously so it isn't racing a background thread. Without this mock, the
    view would fire a genuine daemon thread that outlives this test (and the whole
    pytest process), hits the ambient GATEWAY_BASE_URL for real, and can trip a
    Postgres "database is being accessed by other users" teardown warning."""
    bap_pub, bap_priv = generate_signing_key_pair()
    gateway_pub, gateway_priv = generate_signing_key_pair()
    payload = _build_search_payload()
    body = json.dumps(payload).encode()

    bap_header = sign_outbound_request(
        body=body, subscriber_id="bap.example.com", unique_key_id="key-1",
        signing_private_key_b64=bap_priv,
    )
    gateway_header = sign_outbound_request(
        body=body, subscriber_id="gateway.local", unique_key_id="key-1",
        signing_private_key_b64=gateway_priv,
    )
    known = _known(bap_pub=bap_pub, gateway_pub=gateway_pub)

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST, "http://registry:8000/lookup", callback=_lookup_callback(known)
        )
        resp = client.post(
            reverse("search"),
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION=bap_header,
            HTTP_X_GATEWAY_AUTHORIZATION=gateway_header,
        )

    assert resp.status_code == 200
    assert resp.json() == {"context": payload["context"], "message": {"ack": {"status": "ACK"}}}
    mock_dispatch.assert_called_once_with(payload=payload)


@pytest.mark.django_db
def test_search_view_rejects_missing_gateway_authorization(client):
    """The defense-in-depth check: a genuinely BAP-signed request with no
    X-Gateway-Authorization at all must be rejected — BPP must never accept search
    traffic that bypassed Gateway, even with a valid BAP signature."""
    bap_pub, bap_priv = generate_signing_key_pair()
    payload = _build_search_payload()
    body = json.dumps(payload).encode()
    bap_header = sign_outbound_request(
        body=body, subscriber_id="bap.example.com", unique_key_id="key-1",
        signing_private_key_b64=bap_priv,
    )
    known = _known(bap_pub=bap_pub)

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST, "http://registry:8000/lookup", callback=_lookup_callback(known)
        )
        resp = client.post(
            reverse("search"),
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION=bap_header,
        )

    assert resp.status_code == 401


@pytest.mark.django_db
def test_search_view_rejects_bap_id_impersonation(client):
    bap_pub, bap_priv = generate_signing_key_pair()
    gateway_pub, gateway_priv = generate_signing_key_pair()
    payload = _build_search_payload(bap_id="someone-else.example.com")
    body = json.dumps(payload).encode()
    bap_header = sign_outbound_request(
        body=body, subscriber_id="bap.example.com", unique_key_id="key-1",
        signing_private_key_b64=bap_priv,
    )
    gateway_header = sign_outbound_request(
        body=body, subscriber_id="gateway.local", unique_key_id="key-1",
        signing_private_key_b64=gateway_priv,
    )
    known = _known(bap_pub=bap_pub, gateway_pub=gateway_pub)

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST, "http://registry:8000/lookup", callback=_lookup_callback(known)
        )
        resp = client.post(
            reverse("search"),
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION=bap_header,
            HTTP_X_GATEWAY_AUTHORIZATION=gateway_header,
        )

    assert resp.status_code == 401


@pytest.mark.django_db
def test_dispatch_on_search_sends_the_real_catalog_to_gateway(bpp_identity_settings):
    """Real end-to-end proof, not structural-only: a real BusinessAccount + Resource
    exist, dispatch_on_search is called directly (not through the view/thread, to
    avoid racing it), and the exact catalog data (real business/resource names) is
    confirmed present in the payload actually sent to Gateway's /on_search."""
    business = BusinessAccount.objects.create_user(
        contact="salon@example.com", business_name="Glow Salon", password=TEST_PASSWORD
    )
    Resource.objects.create(
        owner_ref=str(business.id), name="Stylist A", code="STY-A", category_id="ONDC:RET13"
    )

    payload = _build_search_payload()
    captured_requests = []

    def gateway_on_search_callback(request):
        captured_requests.append(request)
        return (200, {}, json.dumps({"message": {"ack": {"status": "ACK"}}}))

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST, "http://gateway:8000/on_search", callback=gateway_on_search_callback
        )
        search_service.dispatch_on_search(payload=payload)

    assert len(captured_requests) == 1
    forwarded = json.loads(captured_requests[0].body)
    assert forwarded["context"]["action"] == "on_search"
    assert forwarded["context"]["transaction_id"] == "txn-1"
    assert forwarded["context"]["message_id"] == "msg-1"
    assert forwarded["context"]["bap_id"] == "bap.example.com"
    assert forwarded["context"]["bpp_id"] == "bpp-backend.local"
    provider = forwarded["message"]["catalog"]["providers"][0]
    assert provider["descriptor"]["name"] == "Glow Salon"
    assert provider["items"][0]["descriptor"]["name"] == "Stylist A"
    assert "Authorization" in captured_requests[0].headers
