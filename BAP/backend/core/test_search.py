"""Phase 3.1 Test Gate (livetracker2.md §3.1) pieces owned by BAP: real /search
trigger (customer-facing, non-Beckn shape, sends the real signed Beckn Intent to
Gateway), the results poll, and real /on_search receipt (verifying both the BPP and
the forwarding Gateway).
"""

import json
from unittest.mock import patch

import pytest
import responses
from django.test import Client
from django.urls import reverse

from core import search_service
from core.crypto import generate_signing_key_pair, sign_outbound_request
from core.models import SearchSession


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def bap_identity_settings(settings, tmp_path):
    """Deterministic BAP identity — never depend on ambient .env values (same
    reasoning as BPP/backend's core/test_search.py's identical fixture)."""
    settings.SIGNING_PRIVATE_KEY_PATH = str(tmp_path / "signing.json")
    settings.SUBSCRIBER_ID = "bap-backend.local"
    settings.UNIQUE_KEY_ID = "key-1"
    settings.SUBSCRIBER_URL = "https://bap-backend.local"
    settings.GATEWAY_BASE_URL = "http://gateway:8000"
    from core import participant_keys

    participant_keys.get_signing_keys.cache_clear()
    yield settings


def _build_on_search_payload(*, bap_id="bap-backend.local", bpp_id="bpp.example.com"):
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
            "timestamp": "2026-07-19T00:00:00Z",
        },
        "message": {"catalog": {"descriptor": {"name": "Beauty Catalog"}, "providers": []}},
    }


def _known(*, bpp_pub=None, gateway_pub=None):
    known = {}
    if bpp_pub is not None:
        known["bpp.example.com"] = {
            "subscriber_id": "bpp.example.com",
            "status": "SUBSCRIBED",
            "signing_public_key": bpp_pub,
        }
    if gateway_pub is not None:
        known["gateway.local"] = {
            "subscriber_id": "gateway.local",
            "status": "SUBSCRIBED",
            "signing_public_key": gateway_pub,
        }
    return known


def _lookup_callback(known_participants):
    def callback(request):
        filters = json.loads(request.body)
        subscriber_id = filters["subscriber_id"]
        entry = known_participants.get(subscriber_id)
        return (200, {}, json.dumps([entry] if entry else []))

    return callback


@pytest.mark.django_db
def test_trigger_search_creates_a_session_and_sends_a_real_signed_intent_to_gateway(
    bap_identity_settings, client
):
    """FUNC (the core §3.1 flow, BAP's side): a customer's search request results in
    a real, signed Beckn Intent reaching Gateway, and a SearchSession the customer can
    poll with the returned transaction_id."""
    captured_requests = []

    def gateway_search_callback(request):
        captured_requests.append(request)
        body = json.loads(request.body)
        return (
            200,
            {},
            json.dumps({"context": body["context"], "message": {"ack": {"status": "ACK"}}}),
        )

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST, "http://gateway:8000/search", callback=gateway_search_callback
        )
        resp = client.post(
            reverse("search-trigger"),
            data=json.dumps({"query": "haircut", "domain": "ONDC:RET13"}),
            content_type="application/json",
        )

    assert resp.status_code == 202
    transaction_id = resp.json()["transaction_id"]
    assert transaction_id

    assert len(captured_requests) == 1
    forwarded = json.loads(captured_requests[0].body)
    assert forwarded["context"]["action"] == "search"
    assert forwarded["context"]["bap_id"] == "bap-backend.local"
    assert forwarded["context"]["transaction_id"] == transaction_id
    assert forwarded["message"]["intent"]["item"]["descriptor"]["name"] == "haircut"
    assert "Authorization" in captured_requests[0].headers

    session = SearchSession.objects.get(transaction_id=transaction_id)
    assert session.query == "haircut"
    assert session.results == []


@pytest.mark.django_db
def test_trigger_search_view_rejects_missing_fields(client):
    resp = client.post(
        reverse("search-trigger"),
        data=json.dumps({"query": "haircut"}),
        content_type="application/json",
    )
    assert resp.status_code == 400


@pytest.mark.django_db
def test_search_results_view_returns_404_for_an_unknown_transaction(client):
    resp = client.get(reverse("search-results", args=["nonexistent-txn"]))
    assert resp.status_code == 404


@pytest.mark.django_db
def test_search_results_view_returns_accumulated_results(client):
    session = SearchSession.objects.create(
        transaction_id="txn-1", query="haircut", domain="ONDC:RET13"
    )
    session.results = [{"descriptor": {"name": "Beauty Catalog"}, "providers": []}]
    session.save()

    resp = client.get(reverse("search-results", args=["txn-1"]))
    assert resp.status_code == 200
    assert resp.json()["results"] == session.results


@pytest.mark.django_db
@patch("core.search_service.record_on_search_result")
def test_on_search_view_acks_when_both_bpp_and_gateway_signatures_are_valid(
    mock_record, client
):
    bpp_pub, bpp_priv = generate_signing_key_pair()
    gateway_pub, gateway_priv = generate_signing_key_pair()
    payload = _build_on_search_payload()
    body = json.dumps(payload).encode()

    bpp_header = sign_outbound_request(
        body=body, subscriber_id="bpp.example.com", unique_key_id="key-1",
        signing_private_key_b64=bpp_priv,
    )
    gateway_header = sign_outbound_request(
        body=body, subscriber_id="gateway.local", unique_key_id="key-1",
        signing_private_key_b64=gateway_priv,
    )
    known = _known(bpp_pub=bpp_pub, gateway_pub=gateway_pub)

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST, "http://registry:8000/lookup", callback=_lookup_callback(known)
        )
        resp = client.post(
            reverse("on_search"),
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION=bpp_header,
            HTTP_X_GATEWAY_AUTHORIZATION=gateway_header,
        )

    assert resp.status_code == 200
    assert resp.json() == {"context": payload["context"], "message": {"ack": {"status": "ACK"}}}
    mock_record.assert_called_once_with(payload=payload)


@pytest.mark.django_db
def test_on_search_view_rejects_missing_gateway_authorization(client):
    bpp_pub, bpp_priv = generate_signing_key_pair()
    payload = _build_on_search_payload()
    body = json.dumps(payload).encode()
    bpp_header = sign_outbound_request(
        body=body, subscriber_id="bpp.example.com", unique_key_id="key-1",
        signing_private_key_b64=bpp_priv,
    )
    known = _known(bpp_pub=bpp_pub)

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST, "http://registry:8000/lookup", callback=_lookup_callback(known)
        )
        resp = client.post(
            reverse("on_search"),
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION=bpp_header,
        )

    assert resp.status_code == 401


@pytest.mark.django_db
def test_record_on_search_result_appends_real_catalog_to_matching_session():
    SearchSession.objects.create(transaction_id="txn-1", query="haircut", domain="ONDC:RET13")
    payload = _build_on_search_payload()

    search_service.record_on_search_result(payload=payload)

    session = SearchSession.objects.get(transaction_id="txn-1")
    assert len(session.results) == 1
    assert session.results[0]["descriptor"]["name"] == "Beauty Catalog"


@pytest.mark.django_db
def test_record_on_search_result_accumulates_from_multiple_bpps():
    SearchSession.objects.create(transaction_id="txn-1", query="haircut", domain="ONDC:RET13")
    first = _build_on_search_payload(bpp_id="salon-a.example.com")
    second = _build_on_search_payload(bpp_id="salon-b.example.com")

    search_service.record_on_search_result(payload=first)
    search_service.record_on_search_result(payload=second)

    session = SearchSession.objects.get(transaction_id="txn-1")
    assert len(session.results) == 2


@pytest.mark.django_db
def test_record_on_search_result_drops_a_callback_for_an_unknown_transaction():
    payload = _build_on_search_payload()
    # must not raise, must not create a stray session:
    search_service.record_on_search_result(payload=payload)
    assert not SearchSession.objects.filter(transaction_id="txn-1").exists()
