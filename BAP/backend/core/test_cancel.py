"""Phase 3.5 Test Gate (livetracker2.md §3.5) pieces owned by BAP: real /cancel
trigger (customer-facing, non-Beckn shape, targets the same BPP a prior
successful /confirm already resolved to), the result poll, and real /on_cancel
receipt (verifying both the BPP and the forwarding Gateway).
"""

import json
from unittest.mock import patch

import pytest
import responses
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from core import cancel_service
from core.crypto import generate_signing_key_pair, sign_outbound_request
from core.models import SearchSession

Customer = get_user_model()


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


def _session_with_confirmed_order(
    *, transaction_id="txn-1", bpp_id="bpp.example.com", customer=None
):
    session = SearchSession.objects.create(
        transaction_id=transaction_id, query="haircut", domain="ONDC:RET13", customer=customer
    )
    session.selected_bpp_id = bpp_id
    session.selected_bpp_uri = f"https://{bpp_id}"
    session.confirmed_order = {
        "id": "booking-1",
        "status": "ACTIVE",
        "provider": {"id": "biz-1"},
        "items": [{"id": "item-1"}],
        "fulfillments": [{"id": "booking-1"}],
    }
    session.save()
    return session


def _build_on_cancel_payload(*, bap_id="bap-backend.local", bpp_id="bpp.example.com", error=None):
    payload = {
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
def test_trigger_cancel_targets_the_same_bpp_from_confirm_and_sends_a_real_signed_order_id(
    bap_identity_settings, client
):
    _session_with_confirmed_order()
    captured_requests = []

    def gateway_cancel_callback(request):
        captured_requests.append(request)
        body = json.loads(request.body)
        return (
            200,
            {},
            json.dumps({"context": body["context"], "message": {"ack": {"status": "ACK"}}}),
        )

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST, "http://gateway:8000/cancel", callback=gateway_cancel_callback
        )
        resp = client.post(
            reverse("cancel-trigger"),
            data=json.dumps({"transaction_id": "txn-1"}),
            content_type="application/json",
        )

    assert resp.status_code == 202
    assert len(captured_requests) == 1
    forwarded = json.loads(captured_requests[0].body)
    assert forwarded["context"]["action"] == "cancel"
    assert forwarded["message"]["order_id"] == "booking-1"
    assert "Authorization" in captured_requests[0].headers


@pytest.mark.django_db
def test_trigger_cancel_forwards_the_optional_cancellation_reason(bap_identity_settings, client):
    _session_with_confirmed_order()
    captured_requests = []

    def gateway_cancel_callback(request):
        captured_requests.append(request)
        return (200, {}, json.dumps({"message": {"ack": {"status": "ACK"}}}))

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST, "http://gateway:8000/cancel", callback=gateway_cancel_callback
        )
        client.post(
            reverse("cancel-trigger"),
            data=json.dumps(
                {"transaction_id": "txn-1", "cancellation_reason_id": "change-of-plans"}
            ),
            content_type="application/json",
        )

    forwarded = json.loads(captured_requests[0].body)
    assert forwarded["message"]["cancellation_reason_id"] == "change-of-plans"


@pytest.mark.django_db
def test_trigger_cancel_view_rejects_a_transaction_with_no_confirmed_booking(
    bap_identity_settings, client
):
    SearchSession.objects.create(transaction_id="txn-1", query="haircut", domain="ONDC:RET13")
    resp = client.post(
        reverse("cancel-trigger"),
        data=json.dumps({"transaction_id": "txn-1"}),
        content_type="application/json",
    )
    assert resp.status_code == 400


@pytest.mark.django_db
def test_cancel_result_view_returns_404_for_an_unknown_transaction(client):
    resp = client.get(reverse("cancel-result", args=["nonexistent-txn"]))
    assert resp.status_code == 404


@pytest.mark.django_db
def test_cancel_result_view_returns_the_recorded_order(client):
    session = _session_with_confirmed_order()
    session.cancelled_order = {"status": "CANCELLED"}
    session.save()

    resp = client.get(reverse("cancel-result", args=["txn-1"]))
    assert resp.status_code == 200
    body = resp.json()
    assert body["cancelled_order"] == session.cancelled_order
    assert body["cancelled_error"] is None


@pytest.mark.django_db
def test_cancel_trigger_view_rejects_a_different_authenticated_customers_booking(
    bap_identity_settings, client
):
    """SEC (§3.7's own Test Gate, literal example): an authenticated customer
    attempting to cancel another customer's booking ID is rejected with 403, not
    404-leaked or silently allowed."""
    owner = Customer.objects.create_user(
        contact="owner@example.com", name="Owner", password="a-strong-pw!"
    )
    attacker = Customer.objects.create_user(
        contact="attacker@example.com", name="Attacker", password="a-strong-pw!"
    )
    _session_with_confirmed_order(customer=owner)

    client.force_login(attacker)
    resp = client.post(
        reverse("cancel-trigger"),
        data=json.dumps({"transaction_id": "txn-1"}),
        content_type="application/json",
    )
    assert resp.status_code == 403


@pytest.mark.django_db
def test_cancel_result_view_rejects_a_different_authenticated_customers_booking(client):
    owner = Customer.objects.create_user(
        contact="owner@example.com", name="Owner", password="a-strong-pw!"
    )
    attacker = Customer.objects.create_user(
        contact="attacker@example.com", name="Attacker", password="a-strong-pw!"
    )
    _session_with_confirmed_order(customer=owner)

    client.force_login(attacker)
    resp = client.get(reverse("cancel-result", args=["txn-1"]))
    assert resp.status_code == 403


@pytest.mark.django_db
def test_cancel_trigger_view_allows_the_owning_customer(bap_identity_settings, client):
    owner = Customer.objects.create_user(
        contact="owner@example.com", name="Owner", password="a-strong-pw!"
    )
    _session_with_confirmed_order(customer=owner)
    client.force_login(owner)

    def gateway_cancel_callback(request):
        body = json.loads(request.body)
        return (
            200,
            {},
            json.dumps({"context": body["context"], "message": {"ack": {"status": "ACK"}}}),
        )

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST, "http://gateway:8000/cancel", callback=gateway_cancel_callback
        )
        resp = client.post(
            reverse("cancel-trigger"),
            data=json.dumps({"transaction_id": "txn-1"}),
            content_type="application/json",
        )
    assert resp.status_code == 202


@pytest.mark.django_db
@patch("core.cancel_service.record_on_cancel_result")
def test_on_cancel_view_acks_when_both_bpp_and_gateway_signatures_are_valid(mock_record, client):
    bpp_pub, bpp_priv = generate_signing_key_pair()
    gateway_pub, gateway_priv = generate_signing_key_pair()
    payload = _build_on_cancel_payload()
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
            reverse("on_cancel"),
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION=bpp_header,
            HTTP_X_GATEWAY_AUTHORIZATION=gateway_header,
        )

    assert resp.status_code == 200
    assert resp.json() == {"context": payload["context"], "message": {"ack": {"status": "ACK"}}}
    mock_record.assert_called_once_with(payload=payload)


@pytest.mark.django_db
def test_on_cancel_view_rejects_missing_gateway_authorization(client):
    bpp_pub, bpp_priv = generate_signing_key_pair()
    payload = _build_on_cancel_payload()
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
            reverse("on_cancel"),
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION=bpp_header,
        )

    assert resp.status_code == 401


@pytest.mark.django_db
def test_record_on_cancel_result_stores_the_real_cancelled_order():
    _session_with_confirmed_order()
    payload = _build_on_cancel_payload()

    cancel_service.record_on_cancel_result(payload=payload)

    session = SearchSession.objects.get(transaction_id="txn-1")
    assert session.cancelled_order["status"] == "CANCELLED"
    assert session.cancelled_error is None


@pytest.mark.django_db
def test_record_on_cancel_result_stores_a_real_error_instead_of_an_order():
    _session_with_confirmed_order()
    payload = _build_on_cancel_payload(
        error={"code": "SLOT_UNAVAILABLE", "message": "No matching booking for this order"}
    )

    cancel_service.record_on_cancel_result(payload=payload)

    session = SearchSession.objects.get(transaction_id="txn-1")
    assert session.cancelled_error == {
        "code": "SLOT_UNAVAILABLE",
        "message": "No matching booking for this order",
    }
    assert session.cancelled_order is None


@pytest.mark.django_db
def test_record_on_cancel_result_drops_a_callback_for_an_unknown_transaction():
    payload = _build_on_cancel_payload()
    payload["context"]["transaction_id"] = "unknown-txn"
    # must not raise:
    cancel_service.record_on_cancel_result(payload=payload)
