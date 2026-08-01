"""Phase 4.3 Test Gate (livetracker2.md §4.3): "a staff member logs in independently of
the business owner account and blocks off their own availability."

Covers both §4.3 checklist items: individual staff-level logins (self-service calendar
management, IDOR-scoped to only the one Resource explicitly assigned to that staff
account) and richer business profile management (multi-location support).
"""

import datetime as dt

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from inventory_core.models import Resource, Slot

BusinessAccount = get_user_model()

# Test fixture value, not a real credential.
TEST_PASSWORD = "a-strong-passw0rd!"  # pragma: allowlist secret


@pytest.fixture
def client():
    return Client()


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


def _create_resource(client, *, name="Stylist A"):
    return client.post(
        reverse("resource-create"),
        data={"name": name, "domain_data": {"resource_type": "stylist"}},
        content_type="application/json",
    )


def _create_availability(client, resource_id, *, days_ahead=0):
    start = timezone.now().replace(microsecond=0) + dt.timedelta(days=days_ahead, hours=1)
    end = start + dt.timedelta(hours=3)
    return client.post(
        reverse("resource-availability-create", args=[resource_id]),
        data={
            "range_start": start.isoformat(),
            "range_end": end.isoformat(),
            "times": ["09:00", "10:00"],
            "frequency_days": 1,
            "slot_duration_minutes": 30,
            "slot_capacity": 1,
        },
        content_type="application/json",
    )


def _create_staff(client, *, contact="stylist1@example.com", business_name="Stylist One"):
    return client.post(
        reverse("staff"),
        data={"business_name": business_name, "contact": contact, "password": TEST_PASSWORD},
        content_type="application/json",
    )


# --- Staff account creation (owner-only) --------------------------------------------------------


@pytest.mark.django_db
def test_owner_can_create_and_list_staff(client):
    _signup_and_login(client)

    create_resp = _create_staff(client)
    assert create_resp.status_code == 201
    body = create_resp.json()
    assert body["role"] == "STAFF"
    owner = BusinessAccount.objects.get(contact="owner@example.com")
    assert body["managed_by"] == str(owner.id)

    list_resp = client.get(reverse("staff"))
    assert list_resp.status_code == 200
    assert len(list_resp.json()["staff"]) == 1
    assert list_resp.json()["staff"][0]["contact"] == "stylist1@example.com"


@pytest.mark.django_db
def test_staff_account_cannot_create_or_list_staff(client):
    _signup_and_login(client)
    _create_staff(client)

    staff_client = Client()
    staff_client.post(
        reverse("business-login"),
        data={"contact": "stylist1@example.com", "password": TEST_PASSWORD},
        content_type="application/json",
    )

    resp = staff_client.post(
        reverse("staff"),
        data={
            "business_name": "Sub Staff",
            "contact": "sub@example.com",
            "password": TEST_PASSWORD,
        },
        content_type="application/json",
    )
    assert resp.status_code == 403

    resp = staff_client.get(reverse("staff"))
    assert resp.status_code == 403


@pytest.mark.django_db
def test_staff_account_cannot_create_resources_or_availability(client):
    owner_client = client
    _signup_and_login(owner_client)
    _create_staff(owner_client)

    staff_client = Client()
    staff_client.post(
        reverse("business-login"),
        data={"contact": "stylist1@example.com", "password": TEST_PASSWORD},
        content_type="application/json",
    )

    resp = _create_resource(staff_client, name="Rogue Resource")
    assert resp.status_code == 403
    assert not Resource.objects.filter(name="Rogue Resource").exists()


# --- Business-account role/managed_by DB constraint (defense in depth) ---------------------------


@pytest.mark.django_db
def test_owner_with_managed_by_violates_db_constraint():
    owner = BusinessAccount.objects.create_user(
        contact="a@example.com", business_name="A", password=TEST_PASSWORD
    )
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            BusinessAccount.objects.create_user(
                contact="b@example.com",
                business_name="B",
                password=TEST_PASSWORD,
                role=BusinessAccount.Role.OWNER,
                managed_by=owner,
            )


@pytest.mark.django_db
def test_staff_without_managed_by_violates_db_constraint():
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            BusinessAccount.objects.create_user(
                contact="c@example.com",
                business_name="C",
                password=TEST_PASSWORD,
                role=BusinessAccount.Role.STAFF,
            )


# --- Resource-staff assignment (owner-only) -------------------------------------------------------


@pytest.mark.django_db
def test_owner_can_assign_resource_to_own_staff(client):
    _signup_and_login(client)
    resource_id = _create_resource(client).json()["id"]
    staff_id = _create_staff(client).json()["id"]

    resp = client.post(
        reverse("resource-assign-staff", args=[resource_id]),
        data={"staff_id": staff_id},
        content_type="application/json",
    )
    assert resp.status_code == 200
    resource = Resource.objects.get(id=resource_id)
    assert resource.domain_data["assigned_staff_id"] == staff_id


@pytest.mark.django_db
def test_owner_cannot_assign_someone_elses_staff(client):
    _signup_and_login(client, business_name="Salon A", contact="ownerA@example.com")
    resource_id = _create_resource(client).json()["id"]

    other_client = Client()
    _signup_and_login(other_client, business_name="Salon B", contact="ownerB@example.com")
    foreign_staff_id = _create_staff(
        other_client, contact="foreign-stylist@example.com"
    ).json()["id"]

    resp = client.post(
        reverse("resource-assign-staff", args=[resource_id]),
        data={"staff_id": foreign_staff_id},
        content_type="application/json",
    )
    assert resp.status_code == 400
    resource = Resource.objects.get(id=resource_id)
    assert "assigned_staff_id" not in resource.domain_data


# --- §4.3 Test Gate: staff logs in independently and blocks off their own availability -----------


@pytest.mark.django_db
def test_staff_logs_in_independently_and_blocks_own_availability(client):
    owner_client = client
    _signup_and_login(owner_client)
    resource_id = _create_resource(owner_client).json()["id"]
    availability_resp = _create_availability(owner_client, resource_id)
    assert availability_resp.status_code == 201
    slot_ids = list(
        Slot.objects.filter(resource_id=resource_id).values_list("id", flat=True)
    )
    assert len(slot_ids) >= 2

    staff_id = _create_staff(owner_client).json()["id"]
    owner_client.post(
        reverse("resource-assign-staff", args=[resource_id]),
        data={"staff_id": staff_id},
        content_type="application/json",
    )

    # A fresh, independent client/session — not the owner's — proving the staff member
    # logs in on their own, per the Test Gate's exact wording.
    staff_client = Client()
    login_resp = staff_client.post(
        reverse("business-login"),
        data={"contact": "stylist1@example.com", "password": TEST_PASSWORD},
        content_type="application/json",
    )
    assert login_resp.status_code == 200
    assert login_resp.json()["role"] == "STAFF"
    assert login_resp.json()["assigned_resource_ids"] == [resource_id]

    to_block = [str(slot_ids[0])]
    block_resp = staff_client.post(
        reverse("resource-availability-block", args=[resource_id]),
        data={"slot_ids": to_block},
        content_type="application/json",
    )
    assert block_resp.status_code == 200
    body = block_resp.json()
    assert body["blocked"] == to_block
    assert body["skipped"] == []

    blocked_slot = Slot.objects.get(id=slot_ids[0])
    assert blocked_slot.status == Slot.Status.CANCELLED
    untouched_slot = Slot.objects.get(id=slot_ids[1])
    assert untouched_slot.status == Slot.Status.AVAILABLE


@pytest.mark.django_db
def test_staff_cannot_block_a_resource_not_assigned_to_them(client):
    owner_client = client
    _signup_and_login(owner_client)
    resource_a = _create_resource(owner_client, name="Stylist A").json()["id"]
    resource_b = _create_resource(owner_client, name="Stylist B").json()["id"]
    _create_availability(owner_client, resource_a)
    _create_availability(owner_client, resource_b)

    staff_id = _create_staff(owner_client).json()["id"]
    owner_client.post(
        reverse("resource-assign-staff", args=[resource_a]),
        data={"staff_id": staff_id},
        content_type="application/json",
    )

    staff_client = Client()
    staff_client.post(
        reverse("business-login"),
        data={"contact": "stylist1@example.com", "password": TEST_PASSWORD},
        content_type="application/json",
    )

    other_resource_slot = str(
        Slot.objects.filter(resource_id=resource_b).values_list("id", flat=True).first()
    )
    resp = staff_client.post(
        reverse("resource-availability-block", args=[resource_b]),
        data={"slot_ids": [other_resource_slot]},
        content_type="application/json",
    )
    assert resp.status_code == 404
    slot = Slot.objects.get(id=other_resource_slot)
    assert slot.status == Slot.Status.AVAILABLE


@pytest.mark.django_db
def test_staff_cannot_block_a_foreign_businesss_resource(client):
    _signup_and_login(client, business_name="Salon A", contact="ownerA@example.com")
    resource_id = _create_resource(client).json()["id"]
    _create_availability(client, resource_id)
    slot_id = str(Slot.objects.filter(resource_id=resource_id).values_list("id", flat=True).first())

    other_client = Client()
    _signup_and_login(other_client, business_name="Salon B", contact="ownerB@example.com")
    _create_staff(other_client, contact="foreign-stylist@example.com")
    foreign_staff_client = Client()
    foreign_staff_client.post(
        reverse("business-login"),
        data={"contact": "foreign-stylist@example.com", "password": TEST_PASSWORD},
        content_type="application/json",
    )

    resp = foreign_staff_client.post(
        reverse("resource-availability-block", args=[resource_id]),
        data={"slot_ids": [slot_id]},
        content_type="application/json",
    )
    assert resp.status_code == 404
    slot = Slot.objects.get(id=slot_id)
    assert slot.status == Slot.Status.AVAILABLE


@pytest.mark.django_db
def test_block_skips_already_booked_slot_but_blocks_the_rest(client):
    _signup_and_login(client)
    resource_id = _create_resource(client).json()["id"]
    _create_availability(client, resource_id)
    slots = list(Slot.objects.filter(resource_id=resource_id).order_by("start_time"))
    assert len(slots) >= 2

    slots[0].status = Slot.Status.BOOKED
    slots[0].save(update_fields=["status"])

    resp = client.post(
        reverse("resource-availability-block", args=[resource_id]),
        data={"slot_ids": [str(slots[0].id), str(slots[1].id)]},
        content_type="application/json",
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["blocked"] == [str(slots[1].id)]
    assert body["skipped"] == [
        {"slot_id": str(slots[0].id), "reason": "not blockable (status=BOOKED)"}
    ]


@pytest.mark.django_db
def test_owner_can_also_block_their_own_resource_directly(client):
    _signup_and_login(client)
    resource_id = _create_resource(client).json()["id"]
    _create_availability(client, resource_id)
    slot_id = str(Slot.objects.filter(resource_id=resource_id).values_list("id", flat=True).first())

    resp = client.post(
        reverse("resource-availability-block", args=[resource_id]),
        data={"slot_ids": [slot_id]},
        content_type="application/json",
    )
    assert resp.status_code == 200
    assert resp.json()["blocked"] == [slot_id]


# --- Multi-location support ----------------------------------------------------------------------


@pytest.mark.django_db
def test_owner_can_create_and_list_locations(client):
    _signup_and_login(client)

    resp = client.post(
        reverse("locations"),
        data={"name": "Downtown Branch", "address": "1 Main St", "city": "Metropolis"},
        content_type="application/json",
    )
    assert resp.status_code == 201

    list_resp = client.get(reverse("locations"))
    assert list_resp.status_code == 200
    assert len(list_resp.json()["locations"]) == 1
    assert list_resp.json()["locations"][0]["name"] == "Downtown Branch"


@pytest.mark.django_db
def test_staff_account_cannot_manage_locations(client):
    _signup_and_login(client)
    _create_staff(client)
    staff_client = Client()
    staff_client.post(
        reverse("business-login"),
        data={"contact": "stylist1@example.com", "password": TEST_PASSWORD},
        content_type="application/json",
    )

    resp = staff_client.post(
        reverse("locations"), data={"name": "Rogue Branch"}, content_type="application/json"
    )
    assert resp.status_code == 403
    resp = staff_client.get(reverse("locations"))
    assert resp.status_code == 403


@pytest.mark.django_db
def test_resource_can_be_tagged_with_own_location(client):
    _signup_and_login(client)
    location_id = client.post(
        reverse("locations"), data={"name": "Downtown Branch"}, content_type="application/json"
    ).json()["id"]

    resp = client.post(
        reverse("resource-create"),
        data={
            "name": "Stylist A",
            "domain_data": {"resource_type": "stylist"},
            "location_id": location_id,
        },
        content_type="application/json",
    )
    assert resp.status_code == 201
    resource = Resource.objects.get(id=resp.json()["id"])
    assert resource.domain_data["location_id"] == location_id


@pytest.mark.django_db
def test_resource_create_rejects_a_foreign_location_id(client):
    _signup_and_login(client, business_name="Salon A", contact="ownerA@example.com")

    other_client = Client()
    _signup_and_login(other_client, business_name="Salon B", contact="ownerB@example.com")
    foreign_location_id = other_client.post(
        reverse("locations"), data={"name": "Their Branch"}, content_type="application/json"
    ).json()["id"]

    resp = client.post(
        reverse("resource-create"),
        data={
            "name": "Stylist A",
            "domain_data": {"resource_type": "stylist"},
            "location_id": foreign_location_id,
        },
        content_type="application/json",
    )
    assert resp.status_code == 400


# --- livetracker3.md §7.1: owner dashboard needs its own resource list -------------------


@pytest.mark.django_db
def test_business_me_lists_an_owners_own_resources(client):
    """Mirrors the staff-side `assigned_resource_ids` field this file already tests
    above (`test_staff_logs_in_independently_and_blocks_own_availability`) — an owner
    previously got nothing back from /api/v1/auth/me listing its own resources at all,
    the real backend gap §7.1's dashboard page depends on."""
    _signup_and_login(client)
    resource_id = _create_resource(client, name="Stylist A").json()["id"]
    second_resource_id = _create_resource(client, name="Stylist B").json()["id"]

    resp = client.get(reverse("business-me"))

    assert resp.status_code == 200
    assert resp.json()["role"] == "OWNER"
    assert set(resp.json()["owned_resource_ids"]) == {resource_id, second_resource_id}


@pytest.mark.django_db
def test_business_me_returns_an_empty_resource_list_for_a_brand_new_owner(client):
    """The real empty state §7.1's own checklist calls out explicitly: a brand-new
    owner has zero resources, and this must read as an empty list, not an error or a
    missing key."""
    _signup_and_login(client)

    resp = client.get(reverse("business-me"))

    assert resp.status_code == 200
    assert resp.json()["owned_resource_ids"] == []


@pytest.mark.django_db
def test_business_me_never_lists_a_different_owners_resources(client):
    """The real IDOR-shaped risk this field could introduce if scoped wrong — a
    second business's resources must never leak into another owner's own list."""
    _signup_and_login(client, business_name="Salon A", contact="ownerA@example.com")
    _create_resource(client, name="Stylist A")

    other_client = Client()
    _signup_and_login(other_client, business_name="Salon B", contact="ownerB@example.com")
    other_resource_id = _create_resource(other_client, name="Stylist B").json()["id"]

    resp = other_client.get(reverse("business-me"))

    assert resp.status_code == 200
    assert resp.json()["owned_resource_ids"] == [other_resource_id]
