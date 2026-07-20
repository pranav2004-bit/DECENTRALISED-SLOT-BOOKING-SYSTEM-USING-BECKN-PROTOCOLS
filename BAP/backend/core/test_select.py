"""Phase 3.2 Test Gate (livetracker2.md §3.2) pieces owned by BAP: real /select
trigger (customer-facing, non-Beckn shape, finds the item's real BPP/provider from
the session's own accumulated search results and sends a real signed Beckn order),
the result poll, and real /on_select receipt (verifying both the BPP and the
forwarding Gateway).
"""

import json
from unittest.mock import patch

import pytest
import responses
from django.test import Client
from django.urls import reverse

from core import select_service
from core.crypto import generate_signing_key_pair, sign_outbound_request
from core.models import SearchSession


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def bap_identity_settings(settings, tmp_path):
    settings.SIGNING_PRIVATE_KEY_PATH = str(tmp_path / "signing.json")
    settings.SUBSCRIBER_ID = "bap-backend.local"
    settings.UNIQUE_KEY_ID = "key-1"
    settings.SUBSCRIBER_URL = "https://bap-backend.local"
    settings.GATEWAY_BASE_URL = "http://gateway:8000"
    from core import participant_keys

    participant_keys.get_signing_keys.cache_clear()
    yield settings


def _session_with_result(*, transaction_id="txn-1", bpp_id="bpp.example.com"):
    session = SearchSession.objects.create(
        transaction_id=transaction_id, query="haircut", domain="ONDC:RET13"
    )
    session.results = [
        {
            "bpp_id": bpp_id,
            "bpp_uri": f"https://{bpp_id}",
            "catalog": {
                "descriptor": {"name": "Beauty Catalog"},
                "providers": [
                    {
                        "id": "biz-1",
                        "descriptor": {"name": "Glow Salon"},
                        "category_id": "ONDC:RET13",
                        "items": [{"id": "item-1", "descriptor": {"name": "Stylist A"}}],
                    }
                ],
            },
        }
    ]
    session.save()
    return session


def _build_on_select_payload(*, bap_id="bap-backend.local", bpp_id="bpp.example.com", error=None):
    payload = {
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
    if error is not None:
        payload["error"] = error
    return payload


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
def test_trigger_select_finds_the_real_bpp_and_sends_a_real_signed_order(
    bap_identity_settings, client
):
    """FUNC (the core §3.2 flow, BAP's side): selecting an item found in a real
    earlier search resolves to the real BPP/provider that offered it and reaches
    Gateway as a real, signed Beckn Order — the customer never supplies bpp_id/
    provider_id directly."""
    _session_with_result()
    captured_requests = []

    def gateway_select_callback(request):
        captured_requests.append(request)
        body = json.loads(request.body)
        return (
            200,
            {},
            json.dumps({"context": body["context"], "message": {"ack": {"status": "ACK"}}}),
        )

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST, "http://gateway:8000/select", callback=gateway_select_callback
        )
        resp = client.post(
            reverse("select-trigger"),
            data=json.dumps(
                {
                    "transaction_id": "txn-1",
                    "item_id": "item-1",
                    "requested_timestamp": "2026-07-25T10:00:00Z",
                }
            ),
            content_type="application/json",
        )

    assert resp.status_code == 202
    assert len(captured_requests) == 1
    forwarded = json.loads(captured_requests[0].body)
    assert forwarded["context"]["action"] == "select"
    assert forwarded["context"]["bap_id"] == "bap-backend.local"
    assert forwarded["context"]["bpp_id"] == "bpp.example.com"
    assert forwarded["context"]["bpp_uri"] == "https://bpp.example.com"
    order = forwarded["message"]["order"]
    assert order["provider"]["id"] == "biz-1"
    assert order["items"][0]["id"] == "item-1"
    assert order["fulfillments"][0]["stops"][0]["time"]["timestamp"] == "2026-07-25T10:00:00Z"
    assert "Authorization" in captured_requests[0].headers


@pytest.mark.django_db
def test_trigger_select_view_rejects_an_unknown_item(bap_identity_settings, client):
    """NEG: a client-supplied item_id that wasn't actually in this transaction's real
    search results is rejected — never routed to an arbitrary/guessed BPP."""
    _session_with_result()
    resp = client.post(
        reverse("select-trigger"),
        data=json.dumps(
            {
                "transaction_id": "txn-1",
                "item_id": "not-a-real-item",
                "requested_timestamp": "2026-07-25T10:00:00Z",
            }
        ),
        content_type="application/json",
    )
    assert resp.status_code == 404


@pytest.mark.django_db
def test_trigger_select_view_rejects_missing_fields(client):
    resp = client.post(
        reverse("select-trigger"),
        data=json.dumps({"transaction_id": "txn-1"}),
        content_type="application/json",
    )
    assert resp.status_code == 400


@pytest.mark.django_db
def test_select_result_view_returns_404_for_an_unknown_transaction(client):
    resp = client.get(reverse("select-result", args=["nonexistent-txn"]))
    assert resp.status_code == 404


@pytest.mark.django_db
def test_select_result_view_returns_the_recorded_order(client):
    session = _session_with_result()
    session.selected_order = {"items": [{"id": "item-1"}]}
    session.save()

    resp = client.get(reverse("select-result", args=["txn-1"]))
    assert resp.status_code == 200
    body = resp.json()
    assert body["selected_order"] == session.selected_order
    assert body["selected_error"] is None


@pytest.mark.django_db
@patch("core.select_service.record_on_select_result")
def test_on_select_view_acks_when_both_bpp_and_gateway_signatures_are_valid(mock_record, client):
    bpp_pub, bpp_priv = generate_signing_key_pair()
    gateway_pub, gateway_priv = generate_signing_key_pair()
    payload = _build_on_select_payload()
    body = json.dumps(payload).encode()

    bpp_header = sign_outbound_request(
        body=body,
        subscriber_id="bpp.example.com",
        unique_key_id="key-1",
        signing_private_key_b64=bpp_priv,
    )
    gateway_header = sign_outbound_request(
        body=body,
        subscriber_id="gateway.local",
        unique_key_id="key-1",
        signing_private_key_b64=gateway_priv,
    )
    known = _known(bpp_pub=bpp_pub, gateway_pub=gateway_pub)

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST, "http://registry:8000/lookup", callback=_lookup_callback(known)
        )
        resp = client.post(
            reverse("on_select"),
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION=bpp_header,
            HTTP_X_GATEWAY_AUTHORIZATION=gateway_header,
        )

    assert resp.status_code == 200
    assert resp.json() == {"context": payload["context"], "message": {"ack": {"status": "ACK"}}}
    mock_record.assert_called_once_with(payload=payload)


@pytest.mark.django_db
def test_on_select_view_rejects_missing_gateway_authorization(client):
    bpp_pub, bpp_priv = generate_signing_key_pair()
    payload = _build_on_select_payload()
    body = json.dumps(payload).encode()
    bpp_header = sign_outbound_request(
        body=body,
        subscriber_id="bpp.example.com",
        unique_key_id="key-1",
        signing_private_key_b64=bpp_priv,
    )
    known = _known(bpp_pub=bpp_pub)

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST, "http://registry:8000/lookup", callback=_lookup_callback(known)
        )
        resp = client.post(
            reverse("on_select"),
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION=bpp_header,
        )

    assert resp.status_code == 401


@pytest.mark.django_db
def test_record_on_select_result_stores_the_real_order_and_quote():
    _session_with_result()
    payload = _build_on_select_payload()

    select_service.record_on_select_result(payload=payload)

    session = SearchSession.objects.get(transaction_id="txn-1")
    assert session.selected_order["quote"]["price"] == {"currency": "INR", "value": "500.00"}
    assert session.selected_error is None


@pytest.mark.django_db
def test_record_on_select_result_stores_a_real_error_instead_of_an_order():
    """NEG: the §3.2 Test Gate's rejection case — a real SLOT_UNAVAILABLE error from
    the BPP is recorded as an error, not silently swallowed or misread as a success."""
    _session_with_result()
    payload = _build_on_select_payload(
        error={"code": "SLOT_UNAVAILABLE", "message": "Slot no longer available"}
    )

    select_service.record_on_select_result(payload=payload)

    session = SearchSession.objects.get(transaction_id="txn-1")
    assert session.selected_error == {
        "code": "SLOT_UNAVAILABLE",
        "message": "Slot no longer available",
    }
    assert session.selected_order is None


@pytest.mark.django_db
def test_record_on_select_result_drops_a_callback_for_an_unknown_transaction():
    payload = _build_on_select_payload()
    payload["context"]["transaction_id"] = "unknown-txn"
    # must not raise:
    select_service.record_on_select_result(payload=payload)
