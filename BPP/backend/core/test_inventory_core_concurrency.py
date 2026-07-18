"""Phase 1.2 Test Gate (livetracker2.md §1.2) for shared/inventory_core's Slot capacity
concurrency-safety primitives (`SlotManager.try_reserve` / `lock_for_mutation`). Exercised here
for the same reason as Phase 1.1's tests (test_inventory_core.py) — a Django app's tests need a
real settings module + database, and BPP is its only current consumer.

LOAD/NEG: the tracker's own test gate is explicit that the concurrent-write test "must be run
against real Postgres, not mocked." `TransactionTestCase`-style behavior (`transaction=True`) is
required here, not the default `django_db` fixture — the default wraps each test in one
outer transaction that's rolled back at the end, which would make every thread below share a
single uncommitted transaction and never see real cross-connection row locking. `transaction=True`
disables that wrapping so each thread's Django DB connection is a genuine, independently
committing Postgres connection, per the tracker's own "not a theoretical claim" requirement.
"""

import datetime as dt
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from django.db import IntegrityError, connection, transaction
from django.db.transaction import TransactionManagementError
from django.utils import timezone
from inventory_core.models import Resource, Slot


@pytest.fixture
def resource(db):
    return Resource.objects.create(owner_ref="biz-1", name="Stylist A")


def _make_slot(resource, *, capacity):
    now = timezone.now()
    return Slot.objects.create(
        resource=resource,
        start_time=now,
        end_time=now + dt.timedelta(minutes=30),
        capacity_total=capacity,
        capacity_remaining=capacity,
    )


@pytest.mark.django_db
def test_try_reserve_succeeds_when_capacity_available(resource):
    slot = _make_slot(resource, capacity=3)

    assert Slot.objects.try_reserve(slot.id) is True

    slot.refresh_from_db()
    assert slot.capacity_remaining == 2


@pytest.mark.django_db
def test_try_reserve_fails_when_insufficient_capacity(resource):
    slot = _make_slot(resource, capacity=0)

    assert Slot.objects.try_reserve(slot.id) is False

    slot.refresh_from_db()
    assert slot.capacity_remaining == 0


@pytest.mark.django_db
def test_try_reserve_returns_false_for_nonexistent_slot(resource):
    assert Slot.objects.try_reserve(uuid.uuid4()) is False


@pytest.mark.django_db
def test_try_reserve_respects_quantity(resource):
    slot = _make_slot(resource, capacity=2)

    assert Slot.objects.try_reserve(slot.id, quantity=2) is True
    slot.refresh_from_db()
    assert slot.capacity_remaining == 0

    slot2 = _make_slot(resource, capacity=1)
    assert Slot.objects.try_reserve(slot2.id, quantity=2) is False
    slot2.refresh_from_db()
    assert slot2.capacity_remaining == 1


@pytest.mark.django_db(transaction=True)
def test_concurrent_try_reserve_against_capacity_one_slot_yields_exactly_one_success(
    django_db_blocker,
):
    with django_db_blocker.unblock():
        resource = Resource.objects.create(owner_ref="biz-1", name="Stylist A")
        slot = _make_slot(resource, capacity=1)

    n_attempts = 10
    results: list[bool] = []

    def attempt():
        try:
            results.append(Slot.objects.try_reserve(slot.id))
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=n_attempts) as executor:
        futures = [executor.submit(attempt) for _ in range(n_attempts)]
        for future in futures:
            future.result()

    assert results.count(True) == 1
    assert results.count(False) == n_attempts - 1

    slot.refresh_from_db()
    assert slot.capacity_remaining == 0


@pytest.mark.django_db
def test_lock_for_mutation_yields_locked_slot_and_persists_writes_inside_transaction(resource):
    slot = _make_slot(resource, capacity=1)

    with transaction.atomic(), Slot.objects.lock_for_mutation(slot.id) as locked:
        assert locked.id == slot.id
        locked.status = Slot.Status.BOOKED
        locked.capacity_remaining = 0
        locked.save(update_fields=["status", "capacity_remaining"])

    slot.refresh_from_db()
    assert slot.status == Slot.Status.BOOKED
    assert slot.capacity_remaining == 0


@pytest.mark.django_db(transaction=True)
def test_lock_for_mutation_outside_transaction_raises(django_db_blocker):
    # `transaction=True` disables pytest-django's own wrapping transaction (present under the
    # plain `django_db` marker used elsewhere in this file) — only with it truly disabled does
    # Django's own "select_for_update outside atomic()" guard have anything real to catch.
    with django_db_blocker.unblock():
        resource = Resource.objects.create(owner_ref="biz-1", name="Stylist A")
        slot = _make_slot(resource, capacity=1)

        with pytest.raises(TransactionManagementError):
            with Slot.objects.lock_for_mutation(slot.id):
                pass


@pytest.mark.django_db
def test_slot_capacity_remaining_gte_zero_constraint_enforced_at_db_level(resource):
    start = timezone.now()
    with pytest.raises(IntegrityError), transaction.atomic():
        Slot.objects.create(
            resource=resource,
            start_time=start,
            end_time=start + dt.timedelta(minutes=30),
            capacity_total=1,
            capacity_remaining=-1,
        )
