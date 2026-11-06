"""Dashboard home (/api/v1/dashboard/stats) — real aggregate counts/revenue for the
business owner's own Dashboard page (2026-11-06). Scoped to the same resource set as
orders_list_view/BusinessOrdersConsumer; every figure built directly against real
Resource/Slot/Booking rows (not the full select/confirm service chain — this view only
ever reads those models, so exercising it that way is both faster and a more direct
test of the view's own query logic).

`core.views.timezone.now` is frozen to a fixed instant for every date-bucketing
assertion below — without it, "today"/"this month" test data built from the real
wall-clock `now()` would be genuinely flaky around midnight/month-boundary runs
(not a hypothetical: a slot built at `now() - 3 days` lands in the *previous* month
whenever the suite happens to run on the 1st/2nd/3rd of a real month).
"""

import datetime as dt
import uuid
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from inventory_core.models import Booking, Resource, Slot

BusinessAccount = get_user_model()

TEST_PASSWORD = "a-strong-passw0rd!"  # pragma: allowlist secret

# 2026-06-15 12:00 UTC — safely mid-month and mid-day, so every offset used below
# (+/- a few hours or days) can never accidentally cross a month/day boundary.
_FROZEN_NOW = dt.datetime(2026, 6, 15, 12, 0, tzinfo=dt.timezone.utc)


@pytest.fixture
def client():
    return Client()


@pytest.fixture(autouse=True)
def frozen_now():
    with patch("core.views.timezone.now", return_value=_FROZEN_NOW):
        yield


def _signup_and_login(client, *, contact="owner@example.com"):
    client.post(
        reverse("business-signup"),
        data={
            "business_name": "Pavan Group Of Hospitals",
            "contact": contact,
            "password": TEST_PASSWORD,
        },
        content_type="application/json",
    )
    client.post(
        reverse("business-login"),
        data={"contact": contact, "password": TEST_PASSWORD},
        content_type="application/json",
    )


def _resource(owner_id, *, name="Dr. A", is_active=True, average_rating=None, rating_count=0):
    return Resource.objects.create(
        owner_ref=str(owner_id),
        name=name,
        is_active=is_active,
        price_value=Decimal("500.00"),
        average_rating=average_rating,
        rating_count=rating_count,
    )


def _slot(resource, *, start_time, status=Slot.Status.AVAILABLE, capacity_remaining=1):
    return Slot.objects.create(
        resource=resource,
        start_time=start_time,
        end_time=start_time + dt.timedelta(minutes=30),
        capacity_total=1,
        capacity_remaining=capacity_remaining,
        status=status,
    )


def _booking(slot, *, status=Booking.Status.ACTIVE, payment_status="SUCCEEDED", amount="500.00"):
    return Booking.objects.create(
        slot=slot,
        holder_ref=f"txn-{uuid.uuid4()}",
        status=status,
        payment_status=payment_status,
        confirmed_total_value=Decimal(amount),
        confirmed_total_currency="INR",
    )


@pytest.mark.django_db
def test_dashboard_stats_view_rejects_an_unauthenticated_request(client):
    resp = client.get(reverse("dashboard-stats"))
    assert resp.status_code == 401


@pytest.mark.django_db
def test_dashboard_stats_view_returns_real_counts_and_revenue(client):
    _signup_and_login(client)
    owner = BusinessAccount.objects.get(contact="owner@example.com")

    # Doctor A: active, with an open slot today -> counted as available today.
    # Rated 4.0 by 5 patients.
    doctor_a = _resource(
        owner.id, name="Dr. A", is_active=True, average_rating=Decimal("4.0"), rating_count=5
    )
    _slot(doctor_a, start_time=_FROZEN_NOW + dt.timedelta(hours=1))

    # Doctor B: active, but every slot today is already booked out -> NOT available today.
    # Rated 5.0 by 5 patients — the overall rating must be the review-weighted
    # average (4.5), not a plain mean of the two doctors' own averages.
    doctor_b = _resource(
        owner.id, name="Dr. B", is_active=True, average_rating=Decimal("5.0"), rating_count=5
    )
    fully_booked_slot = _slot(
        doctor_b, start_time=_FROZEN_NOW, capacity_remaining=0, status=Slot.Status.BOOKED
    )

    # Doctor C: deactivated entirely -> NOT available today either way. Never
    # rated yet — must not corrupt the overall average or its review count.
    _resource(owner.id, name="Dr. C", is_active=False)

    # A real, paid booking today for doctor B's fully-booked slot.
    _booking(fully_booked_slot, amount="500.00")

    # A real, paid booking earlier this month (not today) for doctor A.
    older_slot = _slot(
        doctor_a,
        start_time=_FROZEN_NOW - dt.timedelta(days=3),
        capacity_remaining=0,
        status=Slot.Status.BOOKED,
    )
    _booking(older_slot, amount="300.00")

    # An ACTIVE booking today that was never actually paid — must count toward
    # bookings_today but NOT toward earnings_today (Booking.payment_status's own
    # "best-effort mirror, not authoritative" contract).
    unpaid_slot = _slot(
        doctor_a,
        start_time=_FROZEN_NOW + dt.timedelta(hours=2),
        capacity_remaining=0,
        status=Slot.Status.BOOKED,
    )
    _booking(unpaid_slot, payment_status="", amount="500.00")

    # A HELD (never confirmed) booking must never be counted anywhere.
    held_slot = _slot(
        doctor_a,
        start_time=_FROZEN_NOW + dt.timedelta(hours=3),
        capacity_remaining=0,
        status=Slot.Status.HELD,
    )
    _booking(held_slot, status=Booking.Status.HELD, payment_status="")

    resp = client.get(reverse("dashboard-stats"), {"tz_offset_minutes": "0"})
    assert resp.status_code == 200
    body = resp.json()

    assert body["total_doctors"] == 3
    assert body["doctors_available_today"] == 1  # only Dr. A
    assert body["doctors_unavailable_today"] == 2  # Dr. B (fully booked) + Dr. C (inactive)
    assert body["overall_rating"] == "4.5"  # review-weighted: (4.0*5 + 5.0*5) / 10
    assert body["rating_count"] == 10

    assert body["earnings_today"] == "500.00"  # only the paid today booking
    assert body["earnings_this_month"] == "800.00"  # today's 500 + 3-days-ago's 300

    assert body["total_bookings"] == 3  # 2 paid + 1 unpaid-but-confirmed; HELD excluded
    assert body["bookings_today"] == 2  # today's paid + today's unpaid-but-confirmed

    assert body["currency"] == "INR"
    assert len(body["trend"]) == 7
    assert body["trend"][-1]["date"] == _FROZEN_NOW.date().isoformat()
    assert body["trend"][-1]["bookings"] == 2
    assert body["trend"][-1]["revenue"] == "500.00"


@pytest.mark.django_db
def test_dashboard_stats_view_handles_a_business_with_no_resources_yet(client):
    _signup_and_login(client)

    resp = client.get(reverse("dashboard-stats"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_doctors"] == 0
    assert body["doctors_available_today"] == 0
    assert body["doctors_unavailable_today"] == 0
    assert body["overall_rating"] is None
    assert body["rating_count"] == 0
    assert body["earnings_today"] == "0.00"
    assert body["earnings_this_month"] == "0.00"
    assert body["total_bookings"] == 0
    assert body["bookings_today"] == 0
