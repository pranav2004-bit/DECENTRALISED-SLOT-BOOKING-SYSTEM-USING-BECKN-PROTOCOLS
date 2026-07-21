"""Phase 3.5 Test Gate (livetracker2.md §3.5) pieces owned by BPP: real /track
receipt (verifying both the BAP and the forwarding Gateway) and real /on_track
dispatch — a read-only, always-`inactive` `Tracking` object (no live GPS feed is
meaningful for a walk-in Beauty appointment, §L.5), including the IDOR-shaped
holder_ref-mismatch rejection.
"""

import datetime as dt
import json
from unittest.mock import patch

import pytest
import redis
import responses
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from inventory_core.models import Resource, Slot
from inventory_core.reservation import confirm_hold, hold_slot

from core import track_service
from core.crypto import generate_signing_key_pair, sign_outbound_request

BusinessAccount = get_user_model()

# Test fixture value, not a real credential.
TEST_PASSWORD = "unused-in-this-test"  # pragma: allowlist secret


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def bpp_identity_settings(settings, tmp_path):
    settings.SIGNING_PRIVATE_KEY_PATH = str(tmp_path / "signing.json")
    settings.SUBSCRIBER_ID = "bpp-backend.local"
    settings.UNIQUE_KEY_ID = "key-1"
    settings.SUBSCRIBER_URL = "https://bpp-backend.local"
    settings.GATEWAY_BASE_URL = "http://gateway:8000"
    from core import participant_keys

    participant_keys.get_signing_keys.cache_clear()
    yield settings


def _redis_client() -> redis.Redis:
    return redis.Redis.from_url(settings.REDIS_URL)


def _make_active_booking(*, holder_ref="txn-1"):
    business = BusinessAccount.objects.create_user(
        contact=f"salon-{Resource.objects.count()}@example.com",
        business_name="Glow Salon",
        password=TEST_PASSWORD,
    )
    resource = Resource.objects.create(
        owner_ref=str(business.id), name="Stylist A", category_id="ONDC:RET13"
    )
    start_time = timezone.now().replace(microsecond=0)
    slot = Slot.objects.create(
        resource=resource,
        start_time=start_time,
        end_time=start_time + dt.timedelta(minutes=30),
        capacity_total=1,
        capacity_remaining=1,
    )
    booking = hold_slot(
        slot.id, holder_ref=holder_ref, redis_client=_redis_client(), ttl_seconds=600
    )
    confirm_hold(booking.id, redis_client=_redis_client())
    return resource, slot, booking


def _build_track_payload(*, order_id, bap_id="bap.example.com", transaction_id="txn-1"):
    return {
        "context": {
            "domain": "ONDC:RET13",
            "location": {"country": {"code": "IND"}},
            "action": "track",
            "version": "1.1.0",
            "bap_id": bap_id,
            "bap_uri": f"https://{bap_id}",
            "transaction_id": transaction_id,
            "message_id": "msg-1",
            "timestamp": "2026-07-20T00:00:00Z",
        },
        "message": {"order_id": str(order_id)},
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
def test_track_view_acks_when_both_bap_and_gateway_signatures_are_valid(
    bpp_identity_settings, client
):
    bap_pub, bap_priv = generate_signing_key_pair()
    gateway_pub, gateway_priv = generate_signing_key_pair()
    payload = _build_track_payload(order_id="11111111-1111-1111-1111-111111111111")
    body = json.dumps(payload).encode()

    bap_header = sign_outbound_request(
        body=body,
        subscriber_id="bap.example.com",
        unique_key_id="key-1",
        signing_private_key_b64=bap_priv,
    )
    gateway_header = sign_outbound_request(
        body=body,
        subscriber_id="gateway.local",
        unique_key_id="key-1",
        signing_private_key_b64=gateway_priv,
    )
    known = _known(bap_pub=bap_pub, gateway_pub=gateway_pub)

    with (
        patch("core.track_service.dispatch_on_track_in_background") as mock_dispatch,
        responses.RequestsMock() as rsps,
    ):
        rsps.add_callback(
            responses.POST, "http://registry:8000/lookup", callback=_lookup_callback(known)
        )
        resp = client.post(
            reverse("track"),
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION=bap_header,
            HTTP_X_GATEWAY_AUTHORIZATION=gateway_header,
        )

    assert resp.status_code == 200
    assert resp.json()["message"]["ack"]["status"] == "ACK"
    mock_dispatch.assert_called_once_with(payload=payload)


@pytest.mark.django_db
def test_track_view_rejects_a_missing_order_id_before_acking(client):
    bap_pub, bap_priv = generate_signing_key_pair()
    gateway_pub, gateway_priv = generate_signing_key_pair()
    payload = {"context": _build_track_payload(order_id="x")["context"], "message": {}}
    body = json.dumps(payload).encode()
    bap_header = sign_outbound_request(
        body=body,
        subscriber_id="bap.example.com",
        unique_key_id="key-1",
        signing_private_key_b64=bap_priv,
    )
    gateway_header = sign_outbound_request(
        body=body,
        subscriber_id="gateway.local",
        unique_key_id="key-1",
        signing_private_key_b64=gateway_priv,
    )
    known = _known(bap_pub=bap_pub, gateway_pub=gateway_pub)

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST, "http://registry:8000/lookup", callback=_lookup_callback(known)
        )
        resp = client.post(
            reverse("track"),
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION=bap_header,
            HTTP_X_GATEWAY_AUTHORIZATION=gateway_header,
        )

    assert resp.status_code == 400
    assert resp.json()["message"]["ack"]["status"] == "NACK"


@pytest.mark.django_db
def test_dispatch_on_track_returns_a_real_inactive_tracking_object(bpp_identity_settings):
    resource, slot, booking = _make_active_booking(holder_ref="txn-1")
    payload = _build_track_payload(order_id=booking.id, transaction_id="txn-1")

    captured_requests = []

    def gateway_on_track_callback(request):
        captured_requests.append(request)
        return (200, {}, json.dumps({"message": {"ack": {"status": "ACK"}}}))

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST, "http://gateway:8000/on_track", callback=gateway_on_track_callback
        )
        track_service.dispatch_on_track(payload=payload)

    assert len(captured_requests) == 1
    forwarded = json.loads(captured_requests[0].body)
    assert forwarded["context"]["action"] == "on_track"
    assert "error" not in forwarded
    assert forwarded["message"]["tracking"] == {"status": "inactive"}


@pytest.mark.django_db
def test_dispatch_on_track_rejects_a_booking_held_by_a_different_transaction(
    bpp_identity_settings,
):
    resource, slot, booking = _make_active_booking(holder_ref="txn-owner")
    payload = _build_track_payload(order_id=booking.id, transaction_id="txn-attacker")

    captured_requests = []

    def gateway_on_track_callback(request):
        captured_requests.append(request)
        return (200, {}, json.dumps({"message": {"ack": {"status": "ACK"}}}))

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST, "http://gateway:8000/on_track", callback=gateway_on_track_callback
        )
        track_service.dispatch_on_track(payload=payload)

    forwarded = json.loads(captured_requests[0].body)
    assert forwarded["error"]["code"] == "SLOT_UNAVAILABLE"


@pytest.mark.django_db
def test_dispatch_on_track_rejects_an_unknown_order_id(bpp_identity_settings):
    payload = _build_track_payload(
        order_id="99999999-9999-9999-9999-999999999999", transaction_id="txn-1"
    )

    captured_requests = []

    def gateway_on_track_callback(request):
        captured_requests.append(request)
        return (200, {}, json.dumps({"message": {"ack": {"status": "ACK"}}}))

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST, "http://gateway:8000/on_track", callback=gateway_on_track_callback
        )
        track_service.dispatch_on_track(payload=payload)

    forwarded = json.loads(captured_requests[0].body)
    assert forwarded["error"]["code"] == "SLOT_UNAVAILABLE"
