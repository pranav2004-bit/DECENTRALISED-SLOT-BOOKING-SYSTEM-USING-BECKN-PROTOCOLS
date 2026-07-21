"""Phase 3.5 Test Gate (livetracker2.md §3.5) pieces owned by BPP: real /cancel
receipt (verifying both the BAP and the forwarding Gateway) and real /on_cancel
dispatch — performing the real ACTIVE -> CANCELLED transition and firing a real
SlotReleased/BookingCancelled event, including the IDOR-shaped
holder_ref-mismatch rejection and the still-HELD-booking rejection (§3.5's own
explicit scope decision: /cancel only applies to already-confirmed bookings).
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
from inventory_core.models import Booking, Resource, Slot
from inventory_core.reservation import confirm_hold, hold_slot

from core import cancel_service
from core.crypto import generate_signing_key_pair, sign_outbound_request
from core.events import get_event_bus

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


@pytest.fixture
def bus():
    b = get_event_bus()
    b._redis.delete(b.queue_name, b.dlq_name)
    yield b
    b._redis.delete(b.queue_name, b.dlq_name)


def _redis_client() -> redis.Redis:
    return redis.Redis.from_url(settings.REDIS_URL)


def _make_slot_and_business(*, price_value="899.00"):
    business = BusinessAccount.objects.create_user(
        contact=f"salon-{Resource.objects.count()}@example.com",
        business_name="Glow Salon",
        password=TEST_PASSWORD,
    )
    resource = Resource.objects.create(
        owner_ref=str(business.id),
        name="Stylist A",
        category_id="ONDC:RET13",
        price_currency="INR",
        price_value=price_value,
    )
    start_time = timezone.now().replace(microsecond=0)
    slot = Slot.objects.create(
        resource=resource,
        start_time=start_time,
        end_time=start_time + dt.timedelta(minutes=30),
        capacity_total=1,
        capacity_remaining=1,
    )
    return resource, slot


def _make_active_booking(*, holder_ref="txn-1"):
    resource, slot = _make_slot_and_business()
    booking = hold_slot(
        slot.id, holder_ref=holder_ref, redis_client=_redis_client(), ttl_seconds=600
    )
    confirm_hold(booking.id, redis_client=_redis_client())
    return resource, slot, booking


def _build_cancel_payload(*, order_id, bap_id="bap.example.com", transaction_id="txn-1"):
    return {
        "context": {
            "domain": "ONDC:RET13",
            "location": {"country": {"code": "IND"}},
            "action": "cancel",
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
def test_cancel_view_acks_when_both_bap_and_gateway_signatures_are_valid(
    bpp_identity_settings, client
):
    bap_pub, bap_priv = generate_signing_key_pair()
    gateway_pub, gateway_priv = generate_signing_key_pair()
    payload = _build_cancel_payload(order_id="11111111-1111-1111-1111-111111111111")
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
        patch("core.cancel_service.dispatch_on_cancel_in_background") as mock_dispatch,
        responses.RequestsMock() as rsps,
    ):
        rsps.add_callback(
            responses.POST, "http://registry:8000/lookup", callback=_lookup_callback(known)
        )
        resp = client.post(
            reverse("cancel"),
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION=bap_header,
            HTTP_X_GATEWAY_AUTHORIZATION=gateway_header,
        )

    assert resp.status_code == 200
    assert resp.json()["message"]["ack"]["status"] == "ACK"
    mock_dispatch.assert_called_once_with(payload=payload)


@pytest.mark.django_db
def test_cancel_view_rejects_a_missing_order_id_before_acking(client):
    bap_pub, bap_priv = generate_signing_key_pair()
    gateway_pub, gateway_priv = generate_signing_key_pair()
    payload = {"context": _build_cancel_payload(order_id="x")["context"], "message": {}}
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
            reverse("cancel"),
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION=bap_header,
            HTTP_X_GATEWAY_AUTHORIZATION=gateway_header,
        )

    assert resp.status_code == 400
    assert resp.json()["message"]["ack"]["status"] == "NACK"


@pytest.mark.django_db
def test_dispatch_on_cancel_cancels_the_booking_and_restores_capacity(bpp_identity_settings, bus):
    resource, slot, booking = _make_active_booking(holder_ref="txn-1")
    payload = _build_cancel_payload(order_id=booking.id, transaction_id="txn-1")

    captured_requests = []

    def gateway_on_cancel_callback(request):
        captured_requests.append(request)
        return (200, {}, json.dumps({"message": {"ack": {"status": "ACK"}}}))

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST, "http://gateway:8000/on_cancel", callback=gateway_on_cancel_callback
        )
        cancel_service.dispatch_on_cancel(payload=payload)

    booking.refresh_from_db()
    assert booking.status == Booking.Status.CANCELLED
    slot.refresh_from_db()
    assert slot.capacity_remaining == 1
    assert slot.status == Slot.Status.AVAILABLE

    assert len(captured_requests) == 1
    forwarded = json.loads(captured_requests[0].body)
    assert "error" not in forwarded
    assert forwarded["message"]["order"]["status"] == "CANCELLED"

    first = bus.consume_one(timeout_seconds=2)
    second = bus.consume_one(timeout_seconds=2)
    assert {first["event_type"], second["event_type"]} == {"SlotReleased", "BookingCancelled"}


@pytest.mark.django_db
def test_dispatch_on_cancel_rejects_a_still_held_booking(bpp_identity_settings, bus):
    """§3.5's own explicit scope decision: a still-HELD hold was never actually
    offered to the customer as a confirmed, cancellable Order."""
    resource, slot = _make_slot_and_business()
    booking = hold_slot(
        slot.id, holder_ref="txn-1", redis_client=_redis_client(), ttl_seconds=600
    )
    payload = _build_cancel_payload(order_id=booking.id, transaction_id="txn-1")

    captured_requests = []

    def gateway_on_cancel_callback(request):
        captured_requests.append(request)
        return (200, {}, json.dumps({"message": {"ack": {"status": "ACK"}}}))

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST, "http://gateway:8000/on_cancel", callback=gateway_on_cancel_callback
        )
        cancel_service.dispatch_on_cancel(payload=payload)

    forwarded = json.loads(captured_requests[0].body)
    assert forwarded["error"]["code"] == "SLOT_UNAVAILABLE"
    booking.refresh_from_db()
    assert booking.status == Booking.Status.HELD


@pytest.mark.django_db
def test_dispatch_on_cancel_rejects_a_booking_held_by_a_different_transaction(
    bpp_identity_settings, bus
):
    resource, slot, booking = _make_active_booking(holder_ref="txn-owner")
    payload = _build_cancel_payload(order_id=booking.id, transaction_id="txn-attacker")

    captured_requests = []

    def gateway_on_cancel_callback(request):
        captured_requests.append(request)
        return (200, {}, json.dumps({"message": {"ack": {"status": "ACK"}}}))

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST, "http://gateway:8000/on_cancel", callback=gateway_on_cancel_callback
        )
        cancel_service.dispatch_on_cancel(payload=payload)

    forwarded = json.loads(captured_requests[0].body)
    assert forwarded["error"]["code"] == "SLOT_UNAVAILABLE"
    booking.refresh_from_db()
    assert booking.status == Booking.Status.ACTIVE
