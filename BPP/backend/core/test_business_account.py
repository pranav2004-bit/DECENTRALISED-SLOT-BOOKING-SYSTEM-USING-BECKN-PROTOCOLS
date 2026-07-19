"""Phase 2.2 Test Gate (livetracker2.md §2.2) for BPP's business account + Beauty domain
adapter.

E2E: the business account creates at least one real Resource and generates real Slot
rows via its Availability Calendar, confirmed by direct DB inspection, not just a 200
response; a deactivated business account's inventory stops appearing in search.
"""

import datetime as dt

import pytest
import redis
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from inventory_core.domain_adapter import get_adapter
from inventory_core.models import AvailabilityCalendar, Resource, Slot

from core.beauty_adapter import create_combo_booking

BusinessAccount = get_user_model()

# Test fixture value, not a real credential.
TEST_PASSWORD = "a-strong-passw0rd!"  # pragma: allowlist secret


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def redis_client():
    client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    yield client
    client.close()


def _signup_and_login(client, *, business_name="Glow Salon", contact="owner@example.com"):
    client.post(
        reverse("business-signup"),
        data={"business_name": business_name, "contact": contact, "password": TEST_PASSWORD},
        content_type="application/json",
    )
    return client.post(
        reverse("business-login"),
        data={"contact": contact, "password": TEST_PASSWORD},
        content_type="application/json",
    )


def _create_resource(client, *, name="Stylist A", resource_type="stylist"):
    return client.post(
        reverse("resource-create"),
        data={"name": name, "domain_data": {"resource_type": resource_type}},
        content_type="application/json",
    )


# --- Business account signup/login (mirrors §2.1's shape) --------------------------------------


@pytest.mark.django_db
def test_business_signup_and_login(client):
    login_resp = _signup_and_login(client)

    assert login_resp.status_code == 200
    assert login_resp.json()["business_name"] == "Glow Salon"
    me_resp = client.get(reverse("business-me"))
    assert me_resp.status_code == 200
    assert me_resp.json()["id"] == login_resp.json()["id"]


@pytest.mark.django_db
def test_deactivated_business_account_cannot_log_in(client):
    _signup_and_login(client)
    BusinessAccount.objects.filter(contact="owner@example.com").update(is_active=False)

    resp = client.post(
        reverse("business-login"),
        data={"contact": "owner@example.com", "password": TEST_PASSWORD},
        content_type="application/json",
    )

    assert resp.status_code == 401


# --- Resource creation + Beauty domain adapter validation ---------------------------------------


@pytest.mark.django_db
def test_business_account_creates_a_real_resource(client):
    _signup_and_login(client)

    resp = _create_resource(client)

    assert resp.status_code == 201
    resource_id = resp.json()["id"]
    resource = Resource.objects.get(id=resource_id)
    assert resource.name == "Stylist A"
    assert resource.domain_data == {"resource_type": "stylist"}


@pytest.mark.django_db
def test_resource_creation_rejects_an_invalid_beauty_resource_type(client):
    _signup_and_login(client)

    resp = _create_resource(client, resource_type="not-a-real-type")

    assert resp.status_code == 400
    assert resp.json()["error"]["field"] == "domain_data"
    assert Resource.objects.count() == 0


@pytest.mark.django_db
def test_resource_creation_requires_login(client):
    resp = _create_resource(client)
    assert resp.status_code == 401


# --- Availability Calendar generates real Slot rows (this phase's own Test Gate wording) --------


@pytest.mark.django_db
def test_business_account_generates_real_slots_via_availability_calendar(client):
    _signup_and_login(client)
    resource_id = _create_resource(client).json()["id"]

    resp = client.post(
        reverse("resource-availability-create", args=[resource_id]),
        data={
            "range_start": "2026-08-03T00:00:00Z",
            "range_end": "2026-08-03T23:59:59Z",
            "times": ["09:00", "14:00"],
            "slot_duration_minutes": 30,
            "slot_capacity": 1,
        },
        content_type="application/json",
    )

    assert resp.status_code == 201
    assert resp.json()["slots_created"] == 2

    # Confirmed by direct DB inspection, not just the 200 response.
    assert AvailabilityCalendar.objects.filter(resource_id=resource_id).count() == 1
    slots = Slot.objects.filter(resource_id=resource_id).order_by("start_time")
    assert slots.count() == 2
    assert slots[0].start_time.hour == 9
    assert slots[1].start_time.hour == 14
    assert all(s.capacity_remaining == 1 for s in slots)


@pytest.mark.django_db
def test_availability_creation_requires_owning_the_resource(client):
    _signup_and_login(client, contact="owner-a@example.com")
    resource_id = _create_resource(client).json()["id"]
    client.post(reverse("business-logout"))

    # A different, second business account must not be able to touch someone else's Resource.
    _signup_and_login(client, business_name="Other Salon", contact="owner-b@example.com")
    resp = client.post(
        reverse("resource-availability-create", args=[resource_id]),
        data={
            "range_start": "2026-08-03T00:00:00Z",
            "range_end": "2026-08-03T23:59:59Z",
            "times": ["09:00"],
        },
        content_type="application/json",
    )

    assert resp.status_code == 404


# --- Deactivated business account's inventory stops appearing in search ------------------------


@pytest.mark.django_db
def test_deactivated_business_accounts_resources_disappear_from_catalog(client):
    login_resp = _signup_and_login(client)
    account_id = login_resp.json()["id"]
    _create_resource(client)

    visible_before = client.get(reverse("resources-list")).json()["resources"]
    assert len(visible_before) == 1

    BusinessAccount.objects.filter(id=account_id).update(is_active=False)

    visible_after = client.get(reverse("resources-list")).json()["resources"]
    assert visible_after == []


# --- Beauty combo-service support: sequential slot chaining -------------------------------------


@pytest.mark.django_db
def test_combo_booking_chains_sequential_slots_on_one_resource(client, redis_client):
    _signup_and_login(client)
    resource_id = _create_resource(client).json()["id"]
    resource = Resource.objects.get(id=resource_id)

    start = timezone.now().replace(minute=0, second=0, microsecond=0) + dt.timedelta(days=1)
    bookings = create_combo_booking(
        resource,
        holder_ref="cust-1",
        steps=[
            {"service": "haircut", "duration_minutes": 30},
            {"service": "coloring", "duration_minutes": 45},
        ],
        start_time=start,
        redis_client=redis_client,
    )

    assert len(bookings) == 2
    first, second = bookings
    assert first.domain_data["service"] == "haircut"
    assert second.domain_data["service"] == "coloring"
    assert first.domain_data["combo_group_id"] == second.domain_data["combo_group_id"]
    # Sequential, contiguous — the second step starts exactly when the first ends.
    assert second.slot.start_time == first.slot.end_time
    assert first.slot.end_time - first.slot.start_time == dt.timedelta(minutes=30)
    assert second.slot.end_time - second.slot.start_time == dt.timedelta(minutes=45)


@pytest.mark.django_db
def test_beauty_adapter_is_registered_and_reachable_by_domain_code():
    adapter = get_adapter(settings.DOMAIN_BEAUTY)
    assert adapter.fulfillment_type({"combo": False}) == "STANDARD_SERVICE"
    assert adapter.fulfillment_type({"combo": True}) == "COMBO_SERVICE"
    assert adapter.required_resource_count({"combo": True}) == 1
