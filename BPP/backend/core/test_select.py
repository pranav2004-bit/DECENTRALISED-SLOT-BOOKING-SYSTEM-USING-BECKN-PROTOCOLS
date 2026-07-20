"""Phase 3.2 Test Gate (livetracker2.md §3.2) pieces owned by BPP: real /select
receipt (verifying both the BAP and the forwarding Gateway) and real /on_select
dispatch — resolving the requested item+time against live availability and
attempting the real atomic hold, including the NEG concurrent-race requirement and
the re-selection-releases-prior-hold requirement.
"""

import datetime as dt
import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest
import responses
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from inventory_core.models import Booking, Resource, Slot

from core import select_service
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
    settings.RESERVATION_HOLD_TTL_SECONDS = 600
    from core import participant_keys

    participant_keys.get_signing_keys.cache_clear()
    yield settings


def _make_resource_with_slot(*, price_value="500.00", capacity=1, start_time=None):
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
    start_time = start_time or timezone.now().replace(microsecond=0)
    slot = Slot.objects.create(
        resource=resource,
        start_time=start_time,
        end_time=start_time + dt.timedelta(minutes=30),
        capacity_total=capacity,
        capacity_remaining=capacity,
    )
    return resource, slot


def _build_select_payload(
    *, item_id, requested_timestamp, bap_id="bap.example.com", transaction_id="txn-1"
):
    return {
        "context": {
            "domain": "ONDC:RET13",
            "location": {"country": {"code": "IND"}},
            "action": "select",
            "version": "1.1.0",
            "bap_id": bap_id,
            "bap_uri": f"https://{bap_id}",
            "transaction_id": transaction_id,
            "message_id": "msg-1",
            "timestamp": "2026-07-20T00:00:00Z",
        },
        "message": {
            "order": {
                "items": [{"id": item_id}],
                "fulfillments": [
                    {"stops": [{"type": "start", "time": {"timestamp": requested_timestamp}}]}
                ],
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
@patch("core.select_service.dispatch_on_select_in_background")
def test_select_view_acks_when_both_bap_and_gateway_signatures_are_valid(mock_dispatch, client):
    bap_pub, bap_priv = generate_signing_key_pair()
    gateway_pub, gateway_priv = generate_signing_key_pair()
    payload = _build_select_payload(
        item_id="11111111-1111-1111-1111-111111111111", requested_timestamp="2026-07-25T10:00:00Z"
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

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST, "http://registry:8000/lookup", callback=_lookup_callback(known)
        )
        resp = client.post(
            reverse("select"),
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION=bap_header,
            HTTP_X_GATEWAY_AUTHORIZATION=gateway_header,
        )

    assert resp.status_code == 200
    assert resp.json()["message"]["ack"]["status"] == "ACK"
    mock_dispatch.assert_called_once_with(payload=payload)


@pytest.mark.django_db
def test_select_view_rejects_missing_gateway_authorization(client):
    bap_pub, bap_priv = generate_signing_key_pair()
    payload = _build_select_payload(
        item_id="11111111-1111-1111-1111-111111111111", requested_timestamp="2026-07-25T10:00:00Z"
    )
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
            reverse("select"),
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION=bap_header,
        )

    assert resp.status_code == 401


@pytest.mark.django_db
def test_select_view_rejects_a_malformed_order_before_acking(client):
    """A structurally-invalid order (no fulfillments/stops) must NACK synchronously,
    not ACK and then silently fail in the background."""
    bap_pub, bap_priv = generate_signing_key_pair()
    gateway_pub, gateway_priv = generate_signing_key_pair()
    payload = {
        "context": _build_select_payload(item_id="x", requested_timestamp="2026-07-25T10:00:00Z")[
            "context"
        ],
        "message": {"order": {"items": [{"id": "x"}], "fulfillments": []}},
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
            reverse("select"),
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION=bap_header,
            HTTP_X_GATEWAY_AUTHORIZATION=gateway_header,
        )

    assert resp.status_code == 400
    assert resp.json()["message"]["ack"]["status"] == "NACK"


@pytest.mark.django_db
def test_dispatch_on_select_holds_the_real_slot_and_returns_a_real_quote(bpp_identity_settings):
    resource, slot = _make_resource_with_slot(price_value="750.00")
    requested_timestamp = slot.start_time.isoformat()
    payload = _build_select_payload(
        item_id=str(resource.id), requested_timestamp=requested_timestamp
    )

    captured_requests = []

    def gateway_on_select_callback(request):
        captured_requests.append(request)
        return (200, {}, json.dumps({"message": {"ack": {"status": "ACK"}}}))

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST, "http://gateway:8000/on_select", callback=gateway_on_select_callback
        )
        select_service.dispatch_on_select(payload=payload)

    slot.refresh_from_db()
    assert slot.capacity_remaining == 0
    booking = Booking.objects.get(slot=slot)
    assert booking.status == Booking.Status.HELD
    assert booking.holder_ref == "txn-1"

    assert len(captured_requests) == 1
    forwarded = json.loads(captured_requests[0].body)
    assert forwarded["context"]["action"] == "on_select"
    assert "error" not in forwarded
    order = forwarded["message"]["order"]
    assert order["items"][0]["id"] == str(resource.id)
    assert order["fulfillments"][0]["id"] == str(booking.id)
    assert order["quote"]["price"] == {"currency": "INR", "value": "750.00"}


@pytest.mark.django_db
def test_dispatch_on_select_returns_slot_unavailable_for_a_nonexistent_time(bpp_identity_settings):
    resource, _slot = _make_resource_with_slot()
    payload = _build_select_payload(
        item_id=str(resource.id), requested_timestamp="2099-01-01T00:00:00Z"
    )

    captured_requests = []

    def gateway_on_select_callback(request):
        captured_requests.append(request)
        return (200, {}, json.dumps({"message": {"ack": {"status": "ACK"}}}))

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST, "http://gateway:8000/on_select", callback=gateway_on_select_callback
        )
        select_service.dispatch_on_select(payload=payload)

    forwarded = json.loads(captured_requests[0].body)
    assert forwarded["error"]["code"] == "SLOT_UNAVAILABLE"


@pytest.mark.django_db
def test_dispatch_on_select_returns_item_not_found_for_an_unknown_resource(bpp_identity_settings):
    payload = _build_select_payload(
        item_id="99999999-9999-9999-9999-999999999999", requested_timestamp="2026-07-25T10:00:00Z"
    )

    captured_requests = []

    def gateway_on_select_callback(request):
        captured_requests.append(request)
        return (200, {}, json.dumps({"message": {"ack": {"status": "ACK"}}}))

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST, "http://gateway:8000/on_select", callback=gateway_on_select_callback
        )
        select_service.dispatch_on_select(payload=payload)

    forwarded = json.loads(captured_requests[0].body)
    assert forwarded["error"]["code"] == "ITEM_NOT_FOUND"


@pytest.mark.django_db(transaction=True)
def test_concurrent_select_on_the_same_slot_yields_exactly_one_winner(bpp_identity_settings):
    """The real §3.2 Test Gate: a slot selected by someone else microseconds earlier
    is correctly rejected, not silently accepted. Two genuinely concurrent threads
    race the same capacity-1 slot via dispatch_on_select — the real hold_slot()
    atomicity (already proven in test_inventory_core_booking.py) must surface here as
    a real ITEM/SLOT rejection for exactly one of the two callers.

    One shared `responses.RequestsMock()` wraps both threads deliberately — activating
    two independent RequestsMock contexts concurrently in different threads isn't
    thread-safe (it patches the requests library's transport globally); registering
    the callback once and letting both threads' calls hit it is."""
    resource, slot = _make_resource_with_slot(capacity=1)
    requested_timestamp = slot.start_time.isoformat()

    results = {}

    def on_select_callback(request):
        forwarded = json.loads(request.body)
        transaction_id = forwarded["context"]["transaction_id"]
        results[transaction_id] = "error" not in forwarded
        return (200, {}, json.dumps({"message": {"ack": {"status": "ACK"}}}))

    def attempt(customer_id):
        payload = _build_select_payload(
            item_id=str(resource.id),
            requested_timestamp=requested_timestamp,
            transaction_id=f"txn-{customer_id}",
        )
        select_service.dispatch_on_select(payload=payload)
        connection.close()

    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        rsps.add_callback(
            responses.POST, "http://gateway:8000/on_select", callback=on_select_callback
        )
        with ThreadPoolExecutor(max_workers=2) as executor:
            list(executor.map(attempt, [1, 2]))

    assert len(results) == 2
    successes = sum(1 for won in results.values() if won)
    assert successes == 1

    slot.refresh_from_db()
    assert slot.capacity_remaining == 0
    assert Booking.objects.filter(slot=slot, status=Booking.Status.HELD).count() == 1


@pytest.mark.django_db
def test_reselecting_a_different_slot_releases_the_first_hold(bpp_identity_settings):
    resource, slot_a = _make_resource_with_slot(
        capacity=1, start_time=timezone.now().replace(microsecond=0)
    )
    slot_b = Slot.objects.create(
        resource=resource,
        start_time=slot_a.start_time + dt.timedelta(hours=1),
        end_time=slot_a.start_time + dt.timedelta(hours=1, minutes=30),
        capacity_total=1,
        capacity_remaining=1,
    )

    def run_select(slot):
        payload = _build_select_payload(
            item_id=str(resource.id), requested_timestamp=slot.start_time.isoformat()
        )
        with responses.RequestsMock() as rsps:
            rsps.add_callback(
                responses.POST,
                "http://gateway:8000/on_select",
                callback=lambda r: (200, {}, json.dumps({"message": {"ack": {"status": "ACK"}}})),
            )
            select_service.dispatch_on_select(payload=payload)

    run_select(slot_a)
    slot_a.refresh_from_db()
    assert slot_a.capacity_remaining == 0
    first_booking = Booking.objects.get(slot=slot_a)
    assert first_booking.status == Booking.Status.HELD

    run_select(slot_b)

    slot_a.refresh_from_db()
    slot_b.refresh_from_db()
    first_booking.refresh_from_db()
    assert first_booking.status == Booking.Status.CANCELLED
    assert slot_a.capacity_remaining == 1
    assert slot_b.capacity_remaining == 0
    assert Booking.objects.filter(slot=slot_b, status=Booking.Status.HELD).count() == 1
