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
import threading
import time as time_module
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
import redis
from django.conf import settings
from django.db import IntegrityError, connection, transaction
from django.db.transaction import TransactionManagementError
from django.utils import timezone
from inventory_core.models import Booking, Resource, Slot
from inventory_core.reservation import (
    confirm_hold,
    hold_slot,
    release_expired_hold,
    reschedule_active_booking,
)


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


@pytest.mark.django_db(transaction=True)
def test_concurrent_confirm_on_the_same_booking_fires_exactly_one_event(django_db_blocker):
    """livetracker2.md §3.4's real Test Gate: since `hold_slot()` already prevents two
    customers from ever holding the same capacity-1 slot simultaneously, the only genuine
    race reachable at Confirm time is two near-simultaneous `/confirm` calls for the SAME
    booking (a real double-submit/flaky-retry scenario). Both must observe the booking end
    up ACTIVE; only one may perform the real transition and fire `BookingConfirmed` — the
    other must be a safe, non-erroring, non-duplicate-publishing no-op."""
    with django_db_blocker.unblock():
        resource = Resource.objects.create(owner_ref="biz-1", name="Stylist A")
        slot = _make_slot(resource, capacity=1)
        redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
        booking = hold_slot(slot.id, holder_ref="cust-1", redis_client=redis_client, ttl_seconds=30)

    published = []
    publish_lock = threading.Lock()

    class FakeBus:
        def publish(self, *args, **kwargs):
            with publish_lock:
                published.append(args)

    fake_bus = FakeBus()
    n_attempts = 8
    errors: list[Exception] = []

    def attempt():
        try:
            confirm_hold(booking.id, redis_client=redis_client, event_bus=fake_bus)
        except Exception as exc:  # noqa: BLE001 - captured for assertion, not swallowed
            errors.append(exc)
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=n_attempts) as executor:
        futures = [executor.submit(attempt) for _ in range(n_attempts)]
        for future in futures:
            future.result()

    assert errors == []
    booking.refresh_from_db()
    assert booking.status == Booking.Status.ACTIVE
    # SlotConfirmed + BookingConfirmed, published exactly once each — never once per thread.
    assert len(published) == 2


@pytest.mark.django_db(transaction=True)
def test_concurrent_release_expired_hold_on_the_same_booking_does_not_double_credit_capacity(
    django_db_blocker,
):
    """livetracker2.md §3.11 finding 2's real Test Gate: `release_expired_hold()` gained two
    genuine concurrent callers this phase (`confirm_hold`'s on-touch path + the new scheduled
    sweep) — a real double-submit race on the SAME expired booking must restore capacity
    exactly once, not once per racing caller. Uses a slot with real headroom below
    `capacity_total` (3 total, 1 held, so 2 already free) — the old unlocked-status-check bug
    would have double-credited here (ending above the real total), a case the previous
    `_restore_capacity` clamp only accidentally masked for a fully-held (capacity_total=1)
    slot, not this one."""
    with django_db_blocker.unblock():
        resource = Resource.objects.create(owner_ref="biz-1", name="Stylist A")
        slot = _make_slot(resource, capacity=3)
        redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
        booking = hold_slot(slot.id, holder_ref="cust-1", redis_client=redis_client, ttl_seconds=1)

    time_module.sleep(1.5)

    published = []
    publish_lock = threading.Lock()

    class FakeBus:
        def publish(self, *args, **kwargs):
            with publish_lock:
                published.append(args)

    fake_bus = FakeBus()
    n_attempts = 8
    results: list[bool] = []
    errors: list[Exception] = []

    def attempt():
        try:
            results.append(
                release_expired_hold(booking.id, redis_client=redis_client, event_bus=fake_bus)
            )
        except Exception as exc:  # noqa: BLE001 - captured for assertion, not swallowed
            errors.append(exc)
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=n_attempts) as executor:
        futures = [executor.submit(attempt) for _ in range(n_attempts)]
        for future in futures:
            future.result()

    assert errors == []
    assert results.count(True) == 1
    assert results.count(False) == n_attempts - 1

    slot.refresh_from_db()
    assert slot.capacity_remaining == 3
    booking.refresh_from_db()
    assert booking.status == Booking.Status.CANCELLED
    # SlotReleased + BookingCancelled, published exactly once each — never once per thread.
    assert len(published) == 2


@pytest.mark.django_db(transaction=True)
def test_concurrent_reschedules_swapping_two_slots_do_not_deadlock(django_db_blocker):
    """livetracker2.md §3.5's real deadlock-safety Test Gate, found and fixed by
    design before `reschedule_active_booking` was first written
    (protocol_compliance_notes_v1.1.md §L): naively locking a booking's old slot
    then its new slot would let two concurrent reschedules moving bookings in
    opposite directions between the SAME two slots deadlock against each other.
    Both slots here have real spare capacity (2 total, 1 occupied), so both
    reschedules are genuinely satisfiable concurrently — this proves the
    deterministic sorted-id lock ordering prevents a real deadlock, not just that
    one of the two eventually wins a capacity race."""
    with django_db_blocker.unblock():
        resource = Resource.objects.create(owner_ref="biz-1", name="Stylist A")
        redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
        slot_1 = _make_slot(resource, capacity=2)
        slot_2 = Slot.objects.create(
            resource=resource,
            start_time=slot_1.start_time + dt.timedelta(hours=1),
            end_time=slot_1.end_time + dt.timedelta(hours=1),
            capacity_total=2,
            capacity_remaining=2,
        )
        booking_1 = hold_slot(
            slot_1.id, holder_ref="cust-1", redis_client=redis_client, ttl_seconds=30
        )
        booking_2 = hold_slot(
            slot_2.id, holder_ref="cust-2", redis_client=redis_client, ttl_seconds=30
        )
        confirm_hold(booking_1.id, redis_client=redis_client)
        confirm_hold(booking_2.id, redis_client=redis_client)

    errors: list[Exception] = []

    def move(booking_id, target_slot_id):
        try:
            reschedule_active_booking(booking_id, target_slot_id)
        except Exception as exc:  # noqa: BLE001 - captured for assertion, not swallowed
            errors.append(exc)
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        future_a = executor.submit(move, booking_1.id, slot_2.id)
        future_b = executor.submit(move, booking_2.id, slot_1.id)
        # A real deadlock would hang forever; a generous timeout turns that into a
        # real, diagnosable test failure instead of the whole suite hanging.
        future_a.result(timeout=15)
        future_b.result(timeout=15)

    assert errors == []
    booking_1.refresh_from_db()
    booking_2.refresh_from_db()
    assert booking_1.slot_id == slot_2.id
    assert booking_2.slot_id == slot_1.id
    slot_1.refresh_from_db()
    slot_2.refresh_from_db()
    assert slot_1.capacity_remaining == 1
    assert slot_2.capacity_remaining == 1


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
