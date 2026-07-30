"""Phase 4.5 Test Gate (livetracker2.md §4.5) pieces owned by BAP: real /rating
trigger (customer-facing, non-Beckn shape, targets the same BPP a prior
successful /confirm already resolved to), the result poll, and real /on_rating
receipt (verifying both the BPP and the forwarding Gateway).
"""

import json
from unittest.mock import patch

import pytest
import responses
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from core import rating_service
from core.crypto import generate_signing_key_pair, sign_outbound_request
from core.models import SearchSession

Customer = get_user_model()

# Test fixture value, not a real credential.
TEST_PASSWORD = "a-strong-passw0rd!"  # pragma: allowlist secret


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


def _build_on_rating_payload(*, bap_id="bap-backend.local", bpp_id="bpp.example.com", error=None):
    payload = {
        "context": {
            "domain": "ONDC:RET13",
            "location": {"country": {"code": "IND"}},
            "action": "on_rating",
            "version": "1.1.0",
            "bap_id": bap_id,
            "bap_uri": f"https://{bap_id}",
            "bpp_id": bpp_id,
            "bpp_uri": f"https://{bpp_id}",
            "transaction_id": "txn-1",
            "message_id": "msg-1",
            "timestamp": "2026-07-20T00:00:00Z",
        },
        "message": {},
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


def _mock_bpp_registry_lookup(rsps, *, bpp_id="bpp.example.com", url="https://bpp.example.com"):
    """livetracker4.md §1.1: every direct trigger_rating call now does a fresh
    Registry lookup (registry_client.resolve_subscribed_bpp) immediately before
    dispatch — every trigger test needs this mocked, same as it always needed
    Gateway mocked before this phase."""

    def callback(request):
        filters = json.loads(request.body)
        assert filters["subscriber_id"] == bpp_id
        return (200, {}, json.dumps([{"subscriber_id": bpp_id, "status": "SUBSCRIBED", "url": url}]))

    rsps.add_callback(responses.POST, "http://registry:8000/lookup", callback=callback)


@pytest.mark.django_db
def test_trigger_rating_targets_the_same_bpp_from_confirm_and_sends_a_real_signed_rating(
    bap_identity_settings, client
):
    _session_with_confirmed_order()
    captured_requests = []

    def bpp_rating_callback(request):
        captured_requests.append(request)
        body = json.loads(request.body)
        return (
            200,
            {},
            json.dumps({"context": body["context"], "message": {"ack": {"status": "ACK"}}}),
        )

    with responses.RequestsMock() as rsps:
        _mock_bpp_registry_lookup(rsps)
        rsps.add_callback(
            responses.POST, "https://bpp.example.com/rating", callback=bpp_rating_callback
        )
        resp = client.post(
            reverse("rating-trigger"),
            data=json.dumps(
                {"transaction_id": "txn-1", "rating_category": "Order", "value": "5"}
            ),
            content_type="application/json",
        )

    assert resp.status_code == 202
    assert len(captured_requests) == 1
    forwarded = json.loads(captured_requests[0].body)
    assert forwarded["context"]["action"] == "rating"
    assert forwarded["message"]["ratings"] == [
        {"id": "booking-1", "rating_category": "Order", "value": "5"}
    ]
    assert "Authorization" in captured_requests[0].headers


@pytest.mark.django_db
def test_trigger_rating_uses_an_explicit_entity_id_when_given(bap_identity_settings, client):
    _session_with_confirmed_order()
    captured_requests = []

    def bpp_rating_callback(request):
        captured_requests.append(request)
        return (200, {}, json.dumps({"message": {"ack": {"status": "ACK"}}}))

    with responses.RequestsMock() as rsps:
        _mock_bpp_registry_lookup(rsps)
        rsps.add_callback(
            responses.POST, "https://bpp.example.com/rating", callback=bpp_rating_callback
        )
        client.post(
            reverse("rating-trigger"),
            data=json.dumps(
                {
                    "transaction_id": "txn-1",
                    "rating_category": "Provider",
                    "value": "up",
                    "entity_id": "provider-xyz",
                }
            ),
            content_type="application/json",
        )

    forwarded = json.loads(captured_requests[0].body)
    assert forwarded["message"]["ratings"] == [
        {"id": "provider-xyz", "rating_category": "Provider", "value": "up"}
    ]


@pytest.mark.django_db
def test_trigger_rating_view_rejects_a_transaction_with_no_confirmed_booking(
    bap_identity_settings, client
):
    SearchSession.objects.create(transaction_id="txn-1", query="haircut", domain="ONDC:RET13")
    resp = client.post(
        reverse("rating-trigger"),
        data=json.dumps({"transaction_id": "txn-1", "rating_category": "Order", "value": "5"}),
        content_type="application/json",
    )
    assert resp.status_code == 400


@pytest.mark.django_db
def test_trigger_rating_view_rejects_a_missing_rating_category(bap_identity_settings, client):
    _session_with_confirmed_order()
    resp = client.post(
        reverse("rating-trigger"),
        data=json.dumps({"transaction_id": "txn-1", "value": "5"}),
        content_type="application/json",
    )
    assert resp.status_code == 400


@pytest.mark.django_db
def test_rating_result_view_returns_404_for_an_unknown_transaction(client):
    resp = client.get(reverse("rating-result", args=["nonexistent-txn"]))
    assert resp.status_code == 404


@pytest.mark.django_db
def test_rating_result_view_returns_the_recorded_result(client):
    session = _session_with_confirmed_order()
    session.rating_result = {}
    session.save()

    resp = client.get(reverse("rating-result", args=["txn-1"]))
    assert resp.status_code == 200
    body = resp.json()
    assert body["rating_result"] == {}
    assert body["rating_error"] is None


@pytest.mark.django_db
def test_rating_trigger_view_rejects_a_different_authenticated_customers_booking(
    bap_identity_settings, client
):
    """SEC (§3.7's own Test Gate): an authenticated customer attempting to rate
    another customer's booking is rejected with 403, not 404-leaked or silently
    allowed."""
    owner = Customer.objects.create_user(
        contact="owner@example.com", name="Owner", password=TEST_PASSWORD
    )
    attacker = Customer.objects.create_user(
        contact="attacker@example.com", name="Attacker", password=TEST_PASSWORD
    )
    _session_with_confirmed_order(customer=owner)

    client.force_login(attacker)
    resp = client.post(
        reverse("rating-trigger"),
        data=json.dumps({"transaction_id": "txn-1", "rating_category": "Order", "value": "5"}),
        content_type="application/json",
    )
    assert resp.status_code == 403


@pytest.mark.django_db
@patch("core.rating_service.record_on_rating_result")
def test_on_rating_view_acks_when_both_bpp_and_gateway_signatures_are_valid(mock_record, client):
    bpp_pub, bpp_priv = generate_signing_key_pair()
    gateway_pub, gateway_priv = generate_signing_key_pair()
    payload = _build_on_rating_payload()
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
            reverse("on_rating"),
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION=bpp_header,
            HTTP_X_GATEWAY_AUTHORIZATION=gateway_header,
        )

    assert resp.status_code == 200
    assert resp.json() == {"context": payload["context"], "message": {"ack": {"status": "ACK"}}}
    mock_record.assert_called_once_with(payload=payload)


@pytest.mark.django_db
def test_on_rating_view_accepts_missing_gateway_authorization_now_that_gateway_is_optional(
    client,
):
    """livetracker4.md §1.2: on_rating now arrives directly from the BPP with no
    Gateway hop — a missing X-Gateway-Authorization header must be accepted, not
    rejected, as long as the BPP's own signature is genuinely valid
    (require_gateway=False for this action)."""
    bpp_pub, bpp_priv = generate_signing_key_pair()
    payload = _build_on_rating_payload()
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
            reverse("on_rating"),
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION=bpp_header,
        )

    assert resp.status_code == 200
    assert resp.json()["message"]["ack"]["status"] == "ACK"


@pytest.mark.django_db
def test_on_rating_view_rejects_missing_bpp_authorization_even_without_gateway(client):
    """NEG: require_gateway=False only makes the Gateway signature optional — the
    BPP's own signature is still mandatory."""
    payload = _build_on_rating_payload()
    body = json.dumps(payload).encode()

    resp = client.post(
        reverse("on_rating"),
        data=body,
        content_type="application/json",
    )

    assert resp.status_code == 401


@pytest.mark.django_db
def test_record_on_rating_result_stores_the_real_result():
    _session_with_confirmed_order()
    payload = _build_on_rating_payload()

    rating_service.record_on_rating_result(payload=payload)

    session = SearchSession.objects.get(transaction_id="txn-1")
    assert session.rating_result == {}
    assert session.rating_error is None


@pytest.mark.django_db
def test_record_on_rating_result_stores_a_real_error_instead_of_a_result():
    _session_with_confirmed_order()
    payload = _build_on_rating_payload(
        error={"code": "RATING_ERROR", "message": "each ratings[] entry requires a valid id"}
    )

    rating_service.record_on_rating_result(payload=payload)

    session = SearchSession.objects.get(transaction_id="txn-1")
    assert session.rating_error == {
        "code": "RATING_ERROR",
        "message": "each ratings[] entry requires a valid id",
    }
    assert session.rating_result is None


@pytest.mark.django_db
def test_record_on_rating_result_drops_a_callback_for_an_unknown_transaction():
    payload = _build_on_rating_payload()
    payload["context"]["transaction_id"] = "unknown-txn"
    # must not raise:
    rating_service.record_on_rating_result(payload=payload)
