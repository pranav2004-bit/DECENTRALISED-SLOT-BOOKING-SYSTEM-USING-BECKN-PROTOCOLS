"""Phase 3.4 Test Gate (livetracker2.md §3.4) pieces owned by BPP: real /confirm
receipt (verifying both the BAP and the forwarding Gateway) and real /on_confirm
dispatch — performing the real HELD -> ACTIVE transition and firing a real
BookingConfirmed event, including the IDOR-shaped holder_ref-mismatch rejection,
the expired-hold rejection, and the concurrent-confirm/idempotent-retry Test Gate.
"""

import datetime as dt
import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest
import redis
import responses
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from inventory_core.models import Booking, Resource, Slot
from inventory_core.reservation import hold_slot, release_hold_now

from core import confirm_service
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
    settings.RESERVATION_HOLD_TTL_SECONDS = 600
    from core import participant_keys

    participant_keys.get_signing_keys.cache_clear()
    yield settings


@pytest.fixture
def bus():
    # Real shared/event_bus, per this project's own established pattern
    # (test_inventory_core_events.py) — clear the queue/DLQ before and after so this
    # test never leaks state into (or reads stray state left by) another test.
    b = get_event_bus()
    b._redis.delete(b.queue_name, b.dlq_name)
    yield b
    b._redis.delete(b.queue_name, b.dlq_name)


def _redis_client() -> redis.Redis:
    return redis.Redis.from_url(settings.REDIS_URL)


def _make_held_booking(*, holder_ref="txn-1", price_value="899.00"):
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
    booking = hold_slot(
        slot.id, holder_ref=holder_ref, redis_client=_redis_client(), ttl_seconds=600
    )
    return resource, slot, booking


def _build_confirm_payload(
    *, booking_id, bap_id="bap.example.com", transaction_id="txn-1", provider_id="prov-1"
):
    return {
        "context": {
            "domain": "ONDC:RET13",
            "location": {"country": {"code": "IND"}},
            "action": "confirm",
            "version": "1.1.0",
            "bap_id": bap_id,
            "bap_uri": f"https://{bap_id}",
            "transaction_id": transaction_id,
            "message_id": "msg-1",
            "timestamp": "2026-07-20T00:00:00Z",
        },
        "message": {
            "order": {
                "provider": {"id": provider_id},
                "items": [{"id": "item-1"}],
                "fulfillments": [{"id": str(booking_id)}],
            }
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
def test_confirm_view_acks_when_both_bap_and_gateway_signatures_are_valid(
    bpp_identity_settings, client
):
    bap_pub, bap_priv = generate_signing_key_pair()
    gateway_pub, gateway_priv = generate_signing_key_pair()
    payload = _build_confirm_payload(booking_id="11111111-1111-1111-1111-111111111111")
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
        patch("core.confirm_service.dispatch_on_confirm_in_background") as mock_dispatch,
        responses.RequestsMock() as rsps,
    ):
        rsps.add_callback(
            responses.POST, "http://registry:8000/lookup", callback=_lookup_callback(known)
        )
        resp = client.post(
            reverse("confirm"),
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION=bap_header,
            HTTP_X_GATEWAY_AUTHORIZATION=gateway_header,
        )

    assert resp.status_code == 200
    assert resp.json()["message"]["ack"]["status"] == "ACK"
    mock_dispatch.assert_called_once_with(payload=payload)


@pytest.mark.django_db
def test_confirm_view_rejects_missing_gateway_authorization(client):
    bap_pub, bap_priv = generate_signing_key_pair()
    payload = _build_confirm_payload(booking_id="11111111-1111-1111-1111-111111111111")
    body = json.dumps(payload).encode()
    bap_header = sign_outbound_request(
        body=body,
        subscriber_id="bap.example.com",
        unique_key_id="key-1",
        signing_private_key_b64=bap_priv,
    )
    known = _known(bap_pub=bap_pub)

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST, "http://registry:8000/lookup", callback=_lookup_callback(known)
        )
        resp = client.post(
            reverse("confirm"),
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION=bap_header,
        )

    assert resp.status_code == 401


@pytest.mark.django_db
def test_confirm_view_rejects_a_malformed_order_before_acking(client):
    """A structurally-invalid order (no fulfillments[0].id) must NACK
    synchronously, not ACK and then silently fail in the background."""
    bap_pub, bap_priv = generate_signing_key_pair()
    gateway_pub, gateway_priv = generate_signing_key_pair()
    payload = {
        "context": _build_confirm_payload(booking_id="x")["context"],
        "message": {"order": {"provider": {"id": "prov-1"}, "fulfillments": []}},
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
            reverse("confirm"),
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION=bap_header,
            HTTP_X_GATEWAY_AUTHORIZATION=gateway_header,
        )

    assert resp.status_code == 400
    assert resp.json()["message"]["ack"]["status"] == "NACK"


@pytest.mark.django_db
def test_dispatch_on_confirm_activates_the_booking_and_returns_a_real_confirmed_order(
    bpp_identity_settings, bus
):
    resource, slot, booking = _make_held_booking(holder_ref="txn-1", price_value="899.00")
    payload = _build_confirm_payload(
        booking_id=booking.id, provider_id=resource.owner_ref, transaction_id="txn-1"
    )

    captured_requests = []

    def gateway_on_confirm_callback(request):
        captured_requests.append(request)
        return (200, {}, json.dumps({"message": {"ack": {"status": "ACK"}}}))

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST,
            "http://gateway:8000/on_confirm",
            callback=gateway_on_confirm_callback,
        )
        confirm_service.dispatch_on_confirm(payload=payload)

    booking.refresh_from_db()
    assert booking.status == Booking.Status.ACTIVE

    assert len(captured_requests) == 1
    forwarded = json.loads(captured_requests[0].body)
    assert forwarded["context"]["action"] == "on_confirm"
    assert "error" not in forwarded
    order = forwarded["message"]["order"]
    assert order["id"] == str(booking.id)
    assert order["status"] == "ACTIVE"
    assert order["fulfillments"][0]["id"] == str(booking.id)
    assert order["quote"]["price"] == {"currency": "INR", "value": "899.00"}
    assert order["payments"] == [{"status": "NOT-PAID"}]

    # Real BookingConfirmed/SlotConfirmed events actually fired.
    first = bus.consume_one(timeout_seconds=2)
    second = bus.consume_one(timeout_seconds=2)
    event_types = {first["event_type"], second["event_type"]}
    assert event_types == {"SlotConfirmed", "BookingConfirmed"}


@pytest.mark.django_db
def test_dispatch_on_confirm_rejects_a_booking_held_by_a_different_transaction(
    bpp_identity_settings, bus
):
    """The same IDOR-shaped protection as /init (protocol_compliance_notes_v1.1.md
    §J/§K): a booking held under one transaction must never be confirmable by a
    different transaction_id."""
    resource, slot, booking = _make_held_booking(holder_ref="txn-owner")
    payload = _build_confirm_payload(
        booking_id=booking.id, provider_id=resource.owner_ref, transaction_id="txn-attacker"
    )

    captured_requests = []

    def gateway_on_confirm_callback(request):
        captured_requests.append(request)
        return (200, {}, json.dumps({"message": {"ack": {"status": "ACK"}}}))

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST,
            "http://gateway:8000/on_confirm",
            callback=gateway_on_confirm_callback,
        )
        confirm_service.dispatch_on_confirm(payload=payload)

    forwarded = json.loads(captured_requests[0].body)
    assert forwarded["error"]["code"] == "SLOT_UNAVAILABLE"
    assert forwarded["error"]["message"] == "No matching booking for this order"

    booking.refresh_from_db()
    assert booking.status == Booking.Status.HELD
    assert bus.consume_one(timeout_seconds=1) is None


@pytest.mark.django_db
def test_dispatch_on_confirm_rejects_a_released_hold(bpp_identity_settings, bus):
    resource, slot, booking = _make_held_booking(holder_ref="txn-1")
    release_hold_now(booking.id, redis_client=_redis_client())
    payload = _build_confirm_payload(
        booking_id=booking.id, provider_id=resource.owner_ref, transaction_id="txn-1"
    )

    captured_requests = []

    def gateway_on_confirm_callback(request):
        captured_requests.append(request)
        return (200, {}, json.dumps({"message": {"ack": {"status": "ACK"}}}))

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST,
            "http://gateway:8000/on_confirm",
            callback=gateway_on_confirm_callback,
        )
        confirm_service.dispatch_on_confirm(payload=payload)

    forwarded = json.loads(captured_requests[0].body)
    assert forwarded["error"]["code"] == "SLOT_UNAVAILABLE"


@pytest.mark.django_db
def test_dispatch_on_confirm_rejects_an_unknown_booking_id(bpp_identity_settings, bus):
    payload = _build_confirm_payload(
        booking_id="99999999-9999-9999-9999-999999999999", transaction_id="txn-1"
    )

    captured_requests = []

    def gateway_on_confirm_callback(request):
        captured_requests.append(request)
        return (200, {}, json.dumps({"message": {"ack": {"status": "ACK"}}}))

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST,
            "http://gateway:8000/on_confirm",
            callback=gateway_on_confirm_callback,
        )
        confirm_service.dispatch_on_confirm(payload=payload)

    forwarded = json.loads(captured_requests[0].body)
    assert forwarded["error"]["code"] == "SLOT_UNAVAILABLE"


@pytest.mark.django_db
def test_dispatch_on_confirm_retried_is_idempotent_and_fires_the_event_only_once(
    bpp_identity_settings, bus
):
    """livetracker2.md §3.4's own idempotency Test Gate: the exact same confirm
    request retried (e.g. a flaky-network browser retry) must not double-confirm
    or double-fire BookingConfirmed."""
    resource, slot, booking = _make_held_booking(holder_ref="txn-1", price_value="899.00")
    payload = _build_confirm_payload(
        booking_id=booking.id, provider_id=resource.owner_ref, transaction_id="txn-1"
    )

    captured_requests = []

    def gateway_on_confirm_callback(request):
        captured_requests.append(request)
        return (200, {}, json.dumps({"message": {"ack": {"status": "ACK"}}}))

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST,
            "http://gateway:8000/on_confirm",
            callback=gateway_on_confirm_callback,
        )
        confirm_service.dispatch_on_confirm(payload=payload)
        confirm_service.dispatch_on_confirm(payload=payload)

    assert len(captured_requests) == 2
    for request in captured_requests:
        forwarded = json.loads(request.body)
        assert "error" not in forwarded
        assert forwarded["message"]["order"]["status"] == "ACTIVE"

    booking.refresh_from_db()
    assert booking.status == Booking.Status.ACTIVE

    # SlotConfirmed + BookingConfirmed exactly once, not twice.
    first = bus.consume_one(timeout_seconds=2)
    second = bus.consume_one(timeout_seconds=2)
    assert {first["event_type"], second["event_type"]} == {"SlotConfirmed", "BookingConfirmed"}
    assert bus.consume_one(timeout_seconds=1) is None


@pytest.mark.django_db(transaction=True)
def test_concurrent_confirm_on_the_same_booking_yields_exactly_one_real_transition(
    django_db_blocker, bpp_identity_settings, bus
):
    """The real §3.4 Test Gate, re-scoped to match the real mechanics (see
    livetracker2.md §3.4's own note): since a capacity-1 slot can only ever have
    one real HELD booking at a time (§1.2/§3.2 already prevent that race), the
    genuine race reachable at Confirm time is two near-simultaneous /confirm calls
    for the SAME booking. One shared `responses.RequestsMock()` wraps both
    threads deliberately — activating two independent RequestsMock contexts
    concurrently in different threads isn't thread-safe."""
    with django_db_blocker.unblock():
        resource, slot, booking = _make_held_booking(holder_ref="txn-1")
    payload = _build_confirm_payload(
        booking_id=booking.id, provider_id=resource.owner_ref, transaction_id="txn-1"
    )

    captured_requests = []

    def gateway_on_confirm_callback(request):
        captured_requests.append(request)
        return (200, {}, json.dumps({"message": {"ack": {"status": "ACK"}}}))

    def attempt():
        confirm_service.dispatch_on_confirm(payload=payload)
        connection.close()

    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        rsps.add_callback(
            responses.POST,
            "http://gateway:8000/on_confirm",
            callback=gateway_on_confirm_callback,
        )
        with ThreadPoolExecutor(max_workers=2) as executor:
            list(executor.map(lambda _: attempt(), range(2)))

    assert len(captured_requests) == 2
    for request in captured_requests:
        forwarded = json.loads(request.body)
        assert "error" not in forwarded
        assert forwarded["message"]["order"]["status"] == "ACTIVE"

    booking.refresh_from_db()
    assert booking.status == Booking.Status.ACTIVE

    first = bus.consume_one(timeout_seconds=2)
    second = bus.consume_one(timeout_seconds=2)
    assert {first["event_type"], second["event_type"]} == {"SlotConfirmed", "BookingConfirmed"}
    assert bus.consume_one(timeout_seconds=1) is None
