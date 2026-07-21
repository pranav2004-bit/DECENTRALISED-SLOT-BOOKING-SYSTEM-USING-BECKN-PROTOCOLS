"""Phase 3.6 Test Gate (livetracker2.md §3.6) piece owned by BAP: the customer's
booking history endpoint — genuinely new (see booking_history_service's module
docstring for why no such endpoint existed before this phase), authenticated,
IDOR-safe, cursor-paginated.
"""

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from core.models import SearchSession

Customer = get_user_model()

TEST_PASSWORD = "a-strong-passw0rd!"  # pragma: allowlist secret


@pytest.fixture
def client():
    return Client()


def _customer(*, contact="jane@example.com"):
    return Customer.objects.create_user(contact=contact, name="Jane Doe", password=TEST_PASSWORD)


def _confirmed_session(*, customer, transaction_id, domain="ONDC:RET13"):
    return SearchSession.objects.create(
        transaction_id=transaction_id,
        customer=customer,
        domain=domain,
        confirmed_order={"id": transaction_id, "status": "ACTIVE"},
    )


@pytest.mark.django_db
def test_bookings_list_requires_authentication(client):
    resp = client.get(reverse("bookings-list"))
    assert resp.status_code == 401


@pytest.mark.django_db
def test_bookings_list_returns_only_the_logged_in_customers_own_bookings(client):
    """SEC/IDOR (§3.6): a different customer's confirmed bookings must never appear
    in this customer's history, even though both rows live in the same table."""
    me = _customer(contact="me@example.com")
    someone_else = _customer(contact="someone-else@example.com")
    _confirmed_session(customer=me, transaction_id="txn-mine")
    _confirmed_session(customer=someone_else, transaction_id="txn-not-mine")

    client.force_login(me)
    resp = client.get(reverse("bookings-list"))

    assert resp.status_code == 200
    transaction_ids = [b["transaction_id"] for b in resp.json()["bookings"]]
    assert transaction_ids == ["txn-mine"]


@pytest.mark.django_db
def test_bookings_list_excludes_sessions_that_never_reached_a_confirmed_order(client):
    me = _customer()
    SearchSession.objects.create(
        transaction_id="txn-never-confirmed", customer=me, domain="ONDC:RET13"
    )
    _confirmed_session(customer=me, transaction_id="txn-confirmed")

    client.force_login(me)
    resp = client.get(reverse("bookings-list"))

    transaction_ids = [b["transaction_id"] for b in resp.json()["bookings"]]
    assert transaction_ids == ["txn-confirmed"]


@pytest.mark.django_db
def test_bookings_list_is_cursor_paginated_newest_first(client):
    me = _customer()
    for i in range(3):
        _confirmed_session(customer=me, transaction_id=f"txn-{i}")
    client.force_login(me)

    first = client.get(reverse("bookings-list"), {"limit": "2"})
    first_body = first.json()
    assert [b["transaction_id"] for b in first_body["bookings"]] == ["txn-2", "txn-1"]
    assert first_body["next_cursor"] is not None

    second = client.get(
        reverse("bookings-list"), {"limit": "2", "cursor": first_body["next_cursor"]}
    )
    second_body = second.json()
    assert [b["transaction_id"] for b in second_body["bookings"]] == ["txn-0"]
    assert second_body["next_cursor"] is None
