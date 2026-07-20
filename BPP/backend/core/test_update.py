"""Phase 3.5 Test Gate (livetracker2.md §3.5) pieces owned by BPP: real /update
receipt (verifying both the BAP and the forwarding Gateway) and real /on_update
dispatch — a real reschedule, moving an ACTIVE booking to a different real Slot
on the same Resource, including the IDOR-shaped holder_ref-mismatch rejection,
the still-HELD-booking rejection, and the full-target-slot rejection.
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

from core import update_service
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


def _make_resource_with_two_slots(*, price_value="899.00", second_slot_capacity=1):
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
    slot_a = Slot.objects.create(
        resource=resource,
        start_time=start_time,
        end_time=start_time + dt.timedelta(minutes=30),
        capacity_total=1,
        capacity_remaining=1,
    )
    slot_b = Slot.objects.create(
        resource=resource,
        start_time=start_time + dt.timedelta(hours=1),
        end_time=start_time + dt.timedelta(hours=1, minutes=30),
        capacity_total=1,
        capacity_remaining=second_slot_capacity,
    )
    return resource, slot_a, slot_b


def _make_active_booking_on(resource, slot, *, holder_ref="txn-1"):
    booking = hold_slot(
        slot.id, holder_ref=holder_ref, redis_client=_redis_client(), ttl_seconds=600
    )
    confirm_hold(booking.id, redis_client=_redis_client())
    return booking


def _build_update_payload(
    *, booking_id, provider_id, requested_timestamp, bap_id="bap.example.com",
    transaction_id="txn-1",
):
    return {
        "context": {
            "domain": "ONDC:RET13",
            "location": {"country": {"code": "IND"}},
            "action": "update",
            "version": "1.1.0",
            "bap_id": bap_id,
            "bap_uri": f"https://{bap_id}",
            "transaction_id": transaction_id,
            "message_id": "msg-1",
            "timestamp": "2026-07-20T00:00:00Z",
        },
        "message": {
            "update_target": "fulfillment",
            "order": {
                "provider": {"id": provider_id},
                "items": [{"id": "item-1"}],
                "fulfillments": [
                    {
                        "id": str(booking_id),
                        "stops": [
                            {"type": "start", "time": {"timestamp": requested_timestamp}}
                        ],
                    }
                ],
            },
        },
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
def test_update_view_acks_when_both_bap_and_gateway_signatures_are_valid(
    bpp_identity_settings, client
):
    bap_pub, bap_priv = generate_signing_key_pair()
    gateway_pub, gateway_priv = generate_signing_key_pair()
    payload = _build_update_payload(
        booking_id="11111111-1111-1111-1111-111111111111",
        provider_id="prov-1",
        requested_timestamp="2026-07-25T10:00:00Z",
    )
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
        patch("core.update_service.dispatch_on_update_in_background") as mock_dispatch,
        responses.RequestsMock() as rsps,
    ):
        rsps.add_callback(
            responses.POST, "http://registry:8000/lookup", callback=_lookup_callback(known)
        )
        resp = client.post(
            reverse("update"),
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION=bap_header,
            HTTP_X_GATEWAY_AUTHORIZATION=gateway_header,
        )

    assert resp.status_code == 200
    assert resp.json()["message"]["ack"]["status"] == "ACK"
    mock_dispatch.assert_called_once_with(payload=payload)


@pytest.mark.django_db
def test_update_view_rejects_a_malformed_order_before_acking(client):
    bap_pub, bap_priv = generate_signing_key_pair()
    gateway_pub, gateway_priv = generate_signing_key_pair()
    payload = {
        "context": _build_update_payload(
            booking_id="x", provider_id="prov-1", requested_timestamp="2026-07-25T10:00:00Z"
        )["context"],
        "message": {"update_target": "fulfillment", "order": {"fulfillments": []}},
    }
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
            reverse("update"),
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION=bap_header,
            HTTP_X_GATEWAY_AUTHORIZATION=gateway_header,
        )

    assert resp.status_code == 400
    assert resp.json()["message"]["ack"]["status"] == "NACK"


@pytest.mark.django_db
def test_dispatch_on_update_reschedules_to_the_real_new_slot(bpp_identity_settings, bus):
    resource, slot_a, slot_b = _make_resource_with_two_slots()
    booking = _make_active_booking_on(resource, slot_a, holder_ref="txn-1")
    payload = _build_update_payload(
        booking_id=booking.id,
        provider_id=resource.owner_ref,
        requested_timestamp=slot_b.start_time.isoformat(),
        transaction_id="txn-1",
    )

    captured_requests = []

    def gateway_on_update_callback(request):
        captured_requests.append(request)
        return (200, {}, json.dumps({"message": {"ack": {"status": "ACK"}}}))

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST, "http://gateway:8000/on_update", callback=gateway_on_update_callback
        )
        update_service.dispatch_on_update(payload=payload)

    booking.refresh_from_db()
    assert booking.slot_id == slot_b.id
    assert booking.status == Booking.Status.ACTIVE
    slot_a.refresh_from_db()
    slot_b.refresh_from_db()
    assert slot_a.capacity_remaining == 1
    assert slot_b.capacity_remaining == 0

    assert len(captured_requests) == 1
    forwarded = json.loads(captured_requests[0].body)
    assert "error" not in forwarded
    order = forwarded["message"]["order"]
    assert order["fulfillments"][0]["stops"][0]["time"]["timestamp"] == (
        slot_b.start_time.isoformat()
    )

    events = {bus.consume_one(timeout_seconds=2)["event_type"] for _ in range(3)}
    assert events == {"SlotReleased", "SlotRescheduled", "BookingRescheduled"}


@pytest.mark.django_db
def test_dispatch_on_update_rejects_a_full_new_slot(bpp_identity_settings, bus):
    resource, slot_a, slot_b = _make_resource_with_two_slots(second_slot_capacity=0)
    booking = _make_active_booking_on(resource, slot_a, holder_ref="txn-1")
    payload = _build_update_payload(
        booking_id=booking.id,
        provider_id=resource.owner_ref,
        requested_timestamp=slot_b.start_time.isoformat(),
        transaction_id="txn-1",
    )

    captured_requests = []

    def gateway_on_update_callback(request):
        captured_requests.append(request)
        return (200, {}, json.dumps({"message": {"ack": {"status": "ACK"}}}))

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST, "http://gateway:8000/on_update", callback=gateway_on_update_callback
        )
        update_service.dispatch_on_update(payload=payload)

    forwarded = json.loads(captured_requests[0].body)
    assert forwarded["error"]["code"] == "SLOT_UNAVAILABLE"
    booking.refresh_from_db()
    assert booking.slot_id == slot_a.id


@pytest.mark.django_db
def test_dispatch_on_update_rejects_a_nonexistent_target_time(bpp_identity_settings, bus):
    resource, slot_a, slot_b = _make_resource_with_two_slots()
    booking = _make_active_booking_on(resource, slot_a, holder_ref="txn-1")
    payload = _build_update_payload(
        booking_id=booking.id,
        provider_id=resource.owner_ref,
        requested_timestamp="2099-01-01T00:00:00Z",
        transaction_id="txn-1",
    )

    captured_requests = []

    def gateway_on_update_callback(request):
        captured_requests.append(request)
        return (200, {}, json.dumps({"message": {"ack": {"status": "ACK"}}}))

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST, "http://gateway:8000/on_update", callback=gateway_on_update_callback
        )
        update_service.dispatch_on_update(payload=payload)

    forwarded = json.loads(captured_requests[0].body)
    assert forwarded["error"]["code"] == "SLOT_UNAVAILABLE"


@pytest.mark.django_db
def test_dispatch_on_update_rejects_a_booking_held_by_a_different_transaction(
    bpp_identity_settings, bus
):
    resource, slot_a, slot_b = _make_resource_with_two_slots()
    booking = _make_active_booking_on(resource, slot_a, holder_ref="txn-owner")
    payload = _build_update_payload(
        booking_id=booking.id,
        provider_id=resource.owner_ref,
        requested_timestamp=slot_b.start_time.isoformat(),
        transaction_id="txn-attacker",
    )

    captured_requests = []

    def gateway_on_update_callback(request):
        captured_requests.append(request)
        return (200, {}, json.dumps({"message": {"ack": {"status": "ACK"}}}))

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST, "http://gateway:8000/on_update", callback=gateway_on_update_callback
        )
        update_service.dispatch_on_update(payload=payload)

    forwarded = json.loads(captured_requests[0].body)
    assert forwarded["error"]["code"] == "SLOT_UNAVAILABLE"
    booking.refresh_from_db()
    assert booking.slot_id == slot_a.id
