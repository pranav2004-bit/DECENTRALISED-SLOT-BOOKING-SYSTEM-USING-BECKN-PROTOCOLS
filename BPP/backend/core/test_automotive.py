"""Test Gate for BPP's Automotive domain adapter (livetracker2.md §4.2, revised).

Automotive's own `Resource` is a "bay" only now — a mechanic is staff, not a
Resource (the existing Staff feature already covers "a mechanic is staff assigned
to a bay"), so `required_resource_count()` is 1, same as Beauty/Healthcare. The
genuine multi-resource (bay+mechanic pairing) booking path this file used to test
no longer exists — `find_paired_resource`/`hold_multi_resource_booking` were
removed from `select_service.py`'s /select handling, which now takes the exact
same single-resource branch every other domain already did.
"""

import datetime as dt
import json

import pytest
import responses
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from inventory_core.domain_adapter import get_adapter
from inventory_core.models import Booking, Resource, Slot

from core import select_service

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
    yield settings


def _make_automotive_business(*, contact="garage@example.com"):
    return BusinessAccount.objects.create_user(
        contact=contact,
        business_name="City Garage",
        password=TEST_PASSWORD,
        domain_code=settings.DOMAIN_AUTOMOTIVE,
    )


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


# --- Adapter registration + resource validation -------------------------------------------------


@pytest.mark.django_db
def test_automotive_adapter_is_registered_and_reachable_by_domain_code():
    adapter = get_adapter(settings.DOMAIN_AUTOMOTIVE)
    # Always 1 now — a bay is the only Resource this domain books against; a
    # mechanic is staff, not a second resource requiring simultaneous pairing.
    assert adapter.required_resource_count({}) == 1
    assert adapter.fulfillment_type({}) == "TECHNICIAN_DISPATCH"


@pytest.mark.django_db
def test_automotive_business_creates_a_bay_resource(client):
    """A "bay" is this domain's one and only resource_type now — no profile
    fields (unlike a doctor/stylist), but it does get a server-generated
    "BAY-0001"-style id, same as doctor_id/staff_id, sharing the one
    next_doctor_sequence counter (BaysView's own dedicated UI, matching
    DoctorsView/StylistsView, needs this id to display/filter by)."""
    _signup_and_login_automotive_business(client)

    resp = client.post(
        reverse("resource-create"),
        data={"name": "Bay 1", "domain_data": {"resource_type": "bay"}},
        content_type="application/json",
    )

    assert resp.status_code == 201, resp.content
    resource = Resource.objects.get(id=resp.json()["id"])
    assert resource.domain_data == {"resource_type": "bay", "bay_id": "BAY-0001"}


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


# --- select_service: the bay-only single-resource booking path --------------------------------


@pytest.mark.django_db
def test_select_holds_a_single_bay_slot(bpp_identity_settings):
    """The real path a search->select->confirm cycle now takes for Automotive —
    identical to Beauty's/Healthcare's own single-resource /select, confirming the
    former bay+mechanic pairing branch's removal didn't leave Automotive unable to
    book at all."""
    business = _make_automotive_business()
    start_time = timezone.now().replace(microsecond=0)
    bay = Resource.objects.create(
        owner_ref=str(business.id),
        name="Bay 1",
        domain_data={"resource_type": "bay"},
        price_currency="INR",
        price_value="200.00",
    )
    slot = Slot.objects.create(
        resource=bay,
        start_time=start_time,
        end_time=start_time + dt.timedelta(minutes=60),
        capacity_total=1,
        capacity_remaining=1,
    )
    payload = {
        "context": {
            "domain": settings.DOMAIN_AUTOMOTIVE,
            "location": {"country": {"code": "IND"}},
            "action": "select",
            "version": "1.1.0",
            "bap_id": "bap.example.com",
            "bap_uri": "https://bap.example.com",
            "transaction_id": "txn-auto-bay-1",
            "message_id": "msg-1",
            "timestamp": "2026-07-20T00:00:00Z",
        },
        "message": {
            "order": {
                "items": [{"id": str(bay.id)}],
                "fulfillments": [
                    {
                        "stops": [
                            {"type": "start", "time": {"timestamp": slot.start_time.isoformat()}}
                        ]
                    }
                ],
            }
        },
    }

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
    assert [item["id"] for item in order["items"]] == [str(bay.id)]
    assert len(order["fulfillments"]) == 1
    assert order["quote"]["price"]["value"] == "200.00"

    slot.refresh_from_db()
    assert slot.capacity_remaining == 0
    bookings = Booking.objects.filter(holder_ref="txn-auto-bay-1")
    assert bookings.count() == 1
    assert bookings.first().status == Booking.Status.HELD
