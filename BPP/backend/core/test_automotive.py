"""Phase 4.2 Test Gate (livetracker2.md §4.2) for BPP's Automotive domain adapter and
its genuine multi-resource booking support (`Resource` = bay + mechanic).

EDGE (§4.2's own Test Gate wording, verbatim): "a multi-resource Automotive booking
correctly fails if only one of the two required resources is available, and succeeds
when both are." FUNC: confirm/cancel act on every resource in the group together, and
`/track` reports real depth (`active`/`inactive`) off the booking's own
`fulfillment_status`, not always `"inactive"`.
"""

import datetime as dt
import json

import pytest
import redis
import responses
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from inventory_core.domain_adapter import get_adapter
from inventory_core.models import Booking, Resource, Slot
from inventory_core.reservation import hold_multi_resource_booking

from core import (
    cancel_service,
    confirm_service,
    init_service,
    select_service,
    status_service,
    track_service,
    update_service,
)
from core.automotive_adapter import find_paired_resource
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
    b = get_event_bus()
    b._redis.delete(b.queue_name, b.dlq_name)
    yield b
    b._redis.delete(b.queue_name, b.dlq_name)


def _redis_client() -> redis.Redis:
    return redis.Redis.from_url(settings.REDIS_URL)


def _make_automotive_business(*, contact="garage@example.com"):
    return BusinessAccount.objects.create_user(
        contact=contact,
        business_name="City Garage",
        password=TEST_PASSWORD,
        domain_code=settings.DOMAIN_AUTOMOTIVE,
    )


def _make_bay_and_mechanic(business, *, bay_capacity=1, mechanic_capacity=1, start_time=None):
    start_time = start_time or timezone.now().replace(microsecond=0)
    mechanic = Resource.objects.create(
        owner_ref=str(business.id),
        name="Mechanic Rao",
        domain_data={"resource_type": "mechanic"},
        price_currency="INR",
        price_value="500.00",
    )
    bay = Resource.objects.create(
        owner_ref=str(business.id),
        name="Bay 1",
        domain_data={"resource_type": "bay"},
        price_currency="INR",
        price_value="200.00",
    )
    mechanic_slot = Slot.objects.create(
        resource=mechanic,
        start_time=start_time,
        end_time=start_time + dt.timedelta(minutes=60),
        capacity_total=mechanic_capacity,
        capacity_remaining=mechanic_capacity,
    )
    bay_slot = Slot.objects.create(
        resource=bay,
        start_time=start_time,
        end_time=start_time + dt.timedelta(minutes=60),
        capacity_total=bay_capacity,
        capacity_remaining=bay_capacity,
    )
    return mechanic, mechanic_slot, bay, bay_slot


# --- Adapter registration + resource validation -------------------------------------------------


@pytest.mark.django_db
def test_automotive_adapter_is_registered_and_reachable_by_domain_code():
    adapter = get_adapter(settings.DOMAIN_AUTOMOTIVE)
    assert adapter.required_resource_count({}) == 2
    assert adapter.fulfillment_type({}) == "TECHNICIAN_DISPATCH"


@pytest.mark.django_db
def test_automotive_business_creates_bay_and_mechanic_resources(client):
    _signup_and_login_automotive_business(client)

    for resource_type in ("bay", "mechanic"):
        resp = client.post(
            reverse("resource-create"),
            data={"name": f"A {resource_type}", "domain_data": {"resource_type": resource_type}},
            content_type="application/json",
        )
        assert resp.status_code == 201, resp.content


@pytest.mark.django_db
def test_automotive_business_cannot_create_a_beauty_shaped_resource(client):
    _signup_and_login_automotive_business(client)

    resp = client.post(
        reverse("resource-create"),
        data={"name": "Not A Bay", "domain_data": {"resource_type": "stylist"}},
        content_type="application/json",
    )

    assert resp.status_code == 400
    assert resp.json()["error"]["field"] == "domain_data"
    assert Resource.objects.count() == 0


def _signup_and_login_automotive_business(client, *, contact="garage2@example.com"):
    client.post(
        reverse("business-signup"),
        data={
            "business_name": "City Garage",
            "contact": contact,
            "password": TEST_PASSWORD,
            "domain_code": settings.DOMAIN_AUTOMOTIVE,
        },
        content_type="application/json",
    )
    return client.post(
        reverse("business-login"),
        data={"contact": contact, "password": TEST_PASSWORD},
        content_type="application/json",
    )


# --- find_paired_resource -------------------------------------------------------------------


@pytest.mark.django_db
def test_find_paired_resource_finds_the_complementary_type_from_the_same_business():
    business = _make_automotive_business()
    mechanic, mechanic_slot, bay, bay_slot = _make_bay_and_mechanic(business)

    found = find_paired_resource(mechanic, mechanic_slot.start_time)

    assert found is not None
    found_resource, found_slot = found
    assert found_resource.id == bay.id
    assert found_slot.id == bay_slot.id


@pytest.mark.django_db
def test_find_paired_resource_returns_none_when_no_compatible_slot_exists():
    business = _make_automotive_business()
    mechanic, mechanic_slot, _bay, bay_slot = _make_bay_and_mechanic(business, bay_capacity=0)

    assert find_paired_resource(mechanic, mechanic_slot.start_time) is None


# --- select_service: genuine multi-resource booking (§4.2's own Test Gate wording) --------------


def _select_payload(*, item_id, requested_timestamp, transaction_id="txn-auto-1"):
    return {
        "context": {
            "domain": settings.DOMAIN_AUTOMOTIVE,
            "location": {"country": {"code": "IND"}},
            "action": "select",
            "version": "1.1.0",
            "bap_id": "bap.example.com",
            "bap_uri": "https://bap.example.com",
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


@pytest.mark.django_db
def test_select_succeeds_when_both_bay_and_mechanic_are_available(bpp_identity_settings):
    """EDGE (§4.2's own Test Gate wording): "succeeds when both are" available."""
    business = _make_automotive_business()
    mechanic, mechanic_slot, bay, _bay_slot = _make_bay_and_mechanic(business)
    payload = _select_payload(
        item_id=str(mechanic.id), requested_timestamp=mechanic_slot.start_time.isoformat()
    )

    captured = []
    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST,
            "https://bap.example.com/on_select",
            callback=lambda r: (captured.append(r), (200, {}, json.dumps({"message": {}})))[1],
        )
        select_service.dispatch_on_select(payload=payload)

    forwarded = json.loads(captured[0].body)
    assert "error" not in forwarded
    order = forwarded["message"]["order"]
    assert {item["id"] for item in order["items"]} == {str(mechanic.id), str(bay.id)}
    assert len(order["fulfillments"]) == 2
    assert order["quote"]["price"]["value"] == "700.00"  # 500.00 mechanic + 200.00 bay

    mechanic_slot.refresh_from_db()
    assert mechanic_slot.capacity_remaining == 0
    bookings = Booking.objects.filter(holder_ref="txn-auto-1")
    assert bookings.count() == 2
    assert all(b.status == Booking.Status.HELD for b in bookings)
    group_ids = {b.domain_data["booking_group_id"] for b in bookings}
    assert len(group_ids) == 1


@pytest.mark.django_db
def test_select_fails_cleanly_when_only_the_mechanic_is_available(bpp_identity_settings):
    """EDGE (§4.2's own Test Gate wording, verbatim): "correctly fails if only one
    of the two required resources is available." The mechanic's own slot must stay
    untouched (no partial hold), matching `hold_multi_resource_booking`'s own
    all-or-nothing guarantee proven directly in test_inventory_core_booking.py."""
    business = _make_automotive_business()
    mechanic, mechanic_slot, _bay, _bay_slot = _make_bay_and_mechanic(business, bay_capacity=0)
    payload = _select_payload(
        item_id=str(mechanic.id), requested_timestamp=mechanic_slot.start_time.isoformat()
    )

    captured = []
    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST,
            "https://bap.example.com/on_select",
            callback=lambda r: (captured.append(r), (200, {}, json.dumps({"message": {}})))[1],
        )
        select_service.dispatch_on_select(payload=payload)

    forwarded = json.loads(captured[0].body)
    assert forwarded["error"]["code"] == "SLOT_UNAVAILABLE"
    mechanic_slot.refresh_from_db()
    assert mechanic_slot.capacity_remaining == 1  # untouched
    assert Booking.objects.count() == 0


# --- confirm/cancel: act on the whole group together --------------------------------------------


def _hold_both(business, mechanic_slot, bay_slot, *, holder_ref="txn-auto-2"):
    return hold_multi_resource_booking(
        [mechanic_slot.id, bay_slot.id],
        holder_ref=holder_ref,
        redis_client=_redis_client(),
        ttl_seconds=600,
    )


@pytest.mark.django_db
def test_confirm_activates_every_booking_in_the_group(bpp_identity_settings, bus):
    business = _make_automotive_business()
    _mechanic, mechanic_slot, _bay, bay_slot = _make_bay_and_mechanic(business)
    bookings = _hold_both(business, mechanic_slot, bay_slot)
    primary = bookings[0]

    payload = {
        "context": {
            "domain": settings.DOMAIN_AUTOMOTIVE,
            "location": {"country": {"code": "IND"}},
            "action": "confirm",
            "version": "1.1.0",
            "bap_id": "bap.example.com",
            "bap_uri": "https://bap.example.com",
            "transaction_id": "txn-auto-2",
            "message_id": "msg-1",
            "timestamp": "2026-07-20T00:00:00Z",
        },
        "message": {"order": {"fulfillments": [{"id": str(primary.id)}]}},
    }

    captured = []
    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST,
            "https://bap.example.com/on_confirm",
            callback=lambda r: (captured.append(r), (200, {}, json.dumps({"message": {}})))[1],
        )
        confirm_service.dispatch_on_confirm(payload=payload)

    forwarded = json.loads(captured[0].body)
    assert "error" not in forwarded
    assert len(forwarded["message"]["order"]["fulfillments"]) == 2
    for b in bookings:
        b.refresh_from_db()
        assert b.status == Booking.Status.ACTIVE


@pytest.mark.django_db
def test_cancel_cancels_every_booking_in_the_group(bpp_identity_settings, bus):
    business = _make_automotive_business()
    _mechanic, mechanic_slot, _bay, bay_slot = _make_bay_and_mechanic(business)
    bookings = _hold_both(business, mechanic_slot, bay_slot, holder_ref="txn-auto-3")
    for b in bookings:
        b.transition_status(Booking.Status.ACTIVE)
    primary = bookings[0]

    payload = {
        "context": {
            "domain": settings.DOMAIN_AUTOMOTIVE,
            "location": {"country": {"code": "IND"}},
            "action": "cancel",
            "version": "1.1.0",
            "bap_id": "bap.example.com",
            "bap_uri": "https://bap.example.com",
            "transaction_id": "txn-auto-3",
            "message_id": "msg-1",
            "timestamp": "2026-07-20T00:00:00Z",
        },
        "message": {"order_id": str(primary.id)},
    }

    captured = []
    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST,
            "https://bap.example.com/on_cancel",
            callback=lambda r: (captured.append(r), (200, {}, json.dumps({"message": {}})))[1],
        )
        cancel_service.dispatch_on_cancel(payload=payload)

    forwarded = json.loads(captured[0].body)
    assert "error" not in forwarded
    assert len(forwarded["message"]["order"]["fulfillments"]) == 2
    for b in bookings:
        b.refresh_from_db()
        assert b.status == Booking.Status.CANCELLED

    mechanic_slot.refresh_from_db()
    bay_slot.refresh_from_db()
    assert mechanic_slot.capacity_remaining == 1
    assert bay_slot.capacity_remaining == 1


# --- track: real depth for technician dispatch ---------------------------------------------


def _track_payload(*, order_id, transaction_id):
    return {
        "context": {
            "domain": settings.DOMAIN_AUTOMOTIVE,
            "location": {"country": {"code": "IND"}},
            "action": "track",
            "version": "1.1.0",
            "bap_id": "bap.example.com",
            "bap_uri": "https://bap.example.com",
            "transaction_id": transaction_id,
            "message_id": "msg-1",
            "timestamp": "2026-07-20T00:00:00Z",
        },
        "message": {"order_id": order_id, "callback_url": "https://bap.example.com/callback"},
    }


@pytest.mark.django_db
def test_track_reports_active_once_a_technician_is_in_progress(bpp_identity_settings):
    business = _make_automotive_business()
    _mechanic, mechanic_slot, _bay, bay_slot = _make_bay_and_mechanic(business)
    bookings = _hold_both(business, mechanic_slot, bay_slot, holder_ref="txn-auto-4")
    primary = bookings[0]
    primary.transition_status(Booking.Status.ACTIVE)
    primary.transition_fulfillment_status(Booking.FulfillmentStatus.IN_PROGRESS)

    payload = _track_payload(order_id=str(primary.id), transaction_id="txn-auto-4")

    captured = []
    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST,
            "https://bap.example.com/on_track",
            callback=lambda r: (captured.append(r), (200, {}, json.dumps({"message": {}})))[1],
        )
        track_service.dispatch_on_track(payload=payload)

    forwarded = json.loads(captured[0].body)
    assert forwarded["message"]["tracking"]["status"] == "active"


@pytest.mark.django_db
def test_track_reports_inactive_before_a_technician_is_dispatched(bpp_identity_settings):
    business = _make_automotive_business()
    _mechanic, mechanic_slot, _bay, bay_slot = _make_bay_and_mechanic(business)
    bookings = _hold_both(business, mechanic_slot, bay_slot, holder_ref="txn-auto-5")
    primary = bookings[0]
    primary.transition_status(Booking.Status.ACTIVE)  # still SCHEDULED, not yet IN_PROGRESS

    payload = _track_payload(order_id=str(primary.id), transaction_id="txn-auto-5")

    captured = []
    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST,
            "https://bap.example.com/on_track",
            callback=lambda r: (captured.append(r), (200, {}, json.dumps({"message": {}})))[1],
        )
        track_service.dispatch_on_track(payload=payload)

    forwarded = json.loads(captured[0].body)
    assert forwarded["message"]["tracking"]["status"] == "inactive"


# --- init/status: quote and item-list consistency across the whole group -----------------------


def _init_payload(*, order_id, transaction_id):
    return {
        "context": {
            "domain": settings.DOMAIN_AUTOMOTIVE,
            "location": {"country": {"code": "IND"}},
            "action": "init",
            "version": "1.1.0",
            "bap_id": "bap.example.com",
            "bap_uri": "https://bap.example.com",
            "transaction_id": transaction_id,
            "message_id": "msg-1",
            "timestamp": "2026-07-20T00:00:00Z",
        },
        "message": {"order": {"fulfillments": [{"id": order_id}]}},
    }


@pytest.mark.django_db
def test_init_reports_the_combined_quote_for_every_resource_in_the_group(bpp_identity_settings):
    """Real gap found live 2026-07-26: /select returns a combined quote across every
    resource in the group, but /init only revalidated the one booking its own
    `fulfillments[0].id` named — a customer would see the combined price at /select
    and then a misleadingly smaller single-resource price at /init. Fixed the same way
    as confirm/cancel: `find_group_bookings` widens this to the whole group."""
    business = _make_automotive_business()
    _mechanic, mechanic_slot, _bay, bay_slot = _make_bay_and_mechanic(business)
    bookings = _hold_both(business, mechanic_slot, bay_slot, holder_ref="txn-auto-6")
    primary = bookings[0]

    payload = _init_payload(order_id=str(primary.id), transaction_id="txn-auto-6")

    captured = []
    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST,
            "https://bap.example.com/on_init",
            callback=lambda r: (captured.append(r), (200, {}, json.dumps({"message": {}})))[1],
        )
        init_service.dispatch_on_init(payload=payload)

    forwarded = json.loads(captured[0].body)
    assert "error" not in forwarded
    order = forwarded["message"]["order"]
    assert len(order["fulfillments"]) == 2
    assert len(order["items"]) == 2
    assert order["quote"]["price"]["value"] == "700.00"


def _status_payload(*, order_id, transaction_id):
    return {
        "context": {
            "domain": settings.DOMAIN_AUTOMOTIVE,
            "location": {"country": {"code": "IND"}},
            "action": "status",
            "version": "1.1.0",
            "bap_id": "bap.example.com",
            "bap_uri": "https://bap.example.com",
            "transaction_id": transaction_id,
            "message_id": "msg-1",
            "timestamp": "2026-07-20T00:00:00Z",
        },
        "message": {"order_id": order_id},
    }


@pytest.mark.django_db
def test_status_reports_every_resource_in_the_group(bpp_identity_settings):
    business = _make_automotive_business()
    _mechanic, mechanic_slot, _bay, bay_slot = _make_bay_and_mechanic(business)
    bookings = _hold_both(business, mechanic_slot, bay_slot, holder_ref="txn-auto-7")
    for b in bookings:
        b.transition_status(Booking.Status.ACTIVE)
    primary = bookings[0]

    payload = _status_payload(order_id=str(primary.id), transaction_id="txn-auto-7")

    captured = []
    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST,
            "https://bap.example.com/on_status",
            callback=lambda r: (captured.append(r), (200, {}, json.dumps({"message": {}})))[1],
        )
        status_service.dispatch_on_status(payload=payload)

    forwarded = json.loads(captured[0].body)
    assert "error" not in forwarded
    order = forwarded["message"]["order"]
    assert len(order["fulfillments"]) == 2
    assert len(order["items"]) == 2


# --- update (reschedule): the whole group must move together, all-or-nothing ------------------


def _update_payload(*, order_id, new_timestamp, transaction_id):
    return {
        "context": {
            "domain": settings.DOMAIN_AUTOMOTIVE,
            "location": {"country": {"code": "IND"}},
            "action": "update",
            "version": "1.1.0",
            "bap_id": "bap.example.com",
            "bap_uri": "https://bap.example.com",
            "transaction_id": transaction_id,
            "message_id": "msg-1",
            "timestamp": "2026-07-20T00:00:00Z",
        },
        "message": {
            "update_target": "fulfillment",
            "order": {
                "fulfillments": [
                    {
                        "id": order_id,
                        "stops": [{"type": "start", "time": {"timestamp": new_timestamp}}],
                    }
                ]
            },
        },
    }


@pytest.mark.django_db
def test_update_reschedules_every_resource_in_the_group_together(bpp_identity_settings, bus):
    """Real gap found live 2026-07-26: the original single-booking reschedule
    primitive only ever moved the *one* resource the wire request named, silently
    leaving the other resource in a multi-resource group at its old time — a real
    half-rescheduled state. Both resources must move to the new time together."""
    business = _make_automotive_business()
    _mechanic, mechanic_slot, _bay, bay_slot = _make_bay_and_mechanic(business)
    new_start = mechanic_slot.start_time + dt.timedelta(hours=2)
    Slot.objects.create(
        resource=_mechanic,
        start_time=new_start,
        end_time=new_start + dt.timedelta(hours=1),
        capacity_total=1,
        capacity_remaining=1,
    )
    Slot.objects.create(
        resource=_bay,
        start_time=new_start,
        end_time=new_start + dt.timedelta(hours=1),
        capacity_total=1,
        capacity_remaining=1,
    )

    bookings = _hold_both(business, mechanic_slot, bay_slot, holder_ref="txn-auto-8")
    for b in bookings:
        b.transition_status(Booking.Status.ACTIVE)
    primary = bookings[0]

    payload = _update_payload(
        order_id=str(primary.id),
        new_timestamp=new_start.isoformat(),
        transaction_id="txn-auto-8",
    )

    captured = []
    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST,
            "https://bap.example.com/on_update",
            callback=lambda r: (captured.append(r), (200, {}, json.dumps({"message": {}})))[1],
        )
        update_service.dispatch_on_update(payload=payload)

    forwarded = json.loads(captured[0].body)
    assert "error" not in forwarded
    order = forwarded["message"]["order"]
    assert len(order["fulfillments"]) == 2
    for fulfillment in order["fulfillments"]:
        assert fulfillment["stops"][0]["time"]["timestamp"] == new_start.isoformat()

    for b in bookings:
        b.refresh_from_db()
        assert b.slot.start_time == new_start

    mechanic_slot.refresh_from_db()
    bay_slot.refresh_from_db()
    assert mechanic_slot.capacity_remaining == 1  # old slots restored
    assert bay_slot.capacity_remaining == 1


@pytest.mark.django_db
def test_update_fails_the_whole_group_if_only_one_new_slot_is_available(
    bpp_identity_settings, bus
):
    """EDGE: the same all-or-nothing guarantee as the original booking itself —
    if the new time only has room for one of the two resources, neither resource
    moves; both bookings must stay at their original slot, not a half-moved
    group."""
    business = _make_automotive_business()
    _mechanic, mechanic_slot, _bay, bay_slot = _make_bay_and_mechanic(business)
    new_start = mechanic_slot.start_time + dt.timedelta(hours=2)
    Slot.objects.create(
        resource=_mechanic,
        start_time=new_start,
        end_time=new_start + dt.timedelta(hours=1),
        capacity_total=1,
        capacity_remaining=1,
    )
    # Deliberately no matching bay slot at new_start — the bay side of the group
    # has nowhere to move to.

    bookings = _hold_both(business, mechanic_slot, bay_slot, holder_ref="txn-auto-9")
    for b in bookings:
        b.transition_status(Booking.Status.ACTIVE)
    primary = bookings[0]

    payload = _update_payload(
        order_id=str(primary.id),
        new_timestamp=new_start.isoformat(),
        transaction_id="txn-auto-9",
    )

    captured = []
    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST,
            "https://bap.example.com/on_update",
            callback=lambda r: (captured.append(r), (200, {}, json.dumps({"message": {}})))[1],
        )
        update_service.dispatch_on_update(payload=payload)

    forwarded = json.loads(captured[0].body)
    assert forwarded["error"]["code"] == "SLOT_UNAVAILABLE"

    for b in bookings:
        b.refresh_from_db()
        assert b.slot.start_time == mechanic_slot.start_time  # neither one moved

    mechanic_slot.refresh_from_db()
    bay_slot.refresh_from_db()
    assert mechanic_slot.capacity_remaining == 0  # still held by the original booking
    assert bay_slot.capacity_remaining == 0
