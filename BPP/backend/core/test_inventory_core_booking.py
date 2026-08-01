"""Phase 1.3 Test Gate (livetracker2.md §1.3) for shared/inventory_core's Booking/Fulfillment
state machines and the Redis-backed TTL `HELD` reservation window. Exercised here for the same
reason as Phase 1.1/1.2's tests — a Django app's tests need a real settings module + database,
and BPP is its only current consumer.

FUNC/EDGE: every valid transition succeeds, every invalid transition (e.g. CANCELLED -> ACTIVE)
is rejected; a HELD slot with an expired TTL auto-returns to AVAILABLE without manual
intervention, verified live against real Redis (not mocked — uses BPP's own real `REDIS_URL`).
"""

import datetime as dt
import time as time_module

import pytest
import redis
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone
from inventory_core.models import Booking, BookingAuditLogEntry, Resource, Slot
from inventory_core.reservation import (
    ReservationHold,
    cancel_booking,
    complete_active_booking,
    confirm_hold,
    find_group_bookings,
    hold_multi_resource_booking,
    hold_slot,
    release_expired_hold,
    release_hold_now,
    reschedule_active_booking,
)


@pytest.fixture
def redis_client():
    # A real redis-py client against BPP's own real REDIS_URL — not mocked, per §1.3's Test
    # Gate ("verified live against real Redis"). BPP has no standalone raw-client helper
    # (only `core.events.get_event_bus()`, which wraps its own internal one for the event bus
    # queue), so this connects directly, the same way `EventBus.__init__` does internally.
    client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    yield client
    client.close()


@pytest.fixture
def resource(db):
    return Resource.objects.create(owner_ref="biz-1", name="Stylist A")


def _make_slot(resource, *, capacity=1):
    now = timezone.now()
    return Slot.objects.create(
        resource=resource,
        start_time=now,
        end_time=now + dt.timedelta(minutes=30),
        capacity_total=capacity,
        capacity_remaining=capacity,
    )


# --- Booking status state machine -------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("start", "target"),
    [
        (Booking.Status.HELD, Booking.Status.ACTIVE),
        (Booking.Status.HELD, Booking.Status.CANCELLED),
        (Booking.Status.ACTIVE, Booking.Status.COMPLETE),
        (Booking.Status.ACTIVE, Booking.Status.CANCELLED),
    ],
)
def test_booking_status_valid_transitions_succeed(resource, start, target):
    slot = _make_slot(resource)
    booking = Booking.objects.create(slot=slot, holder_ref="cust-1", status=start)

    booking.transition_status(target)

    booking.refresh_from_db()
    assert booking.status == target


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("start", "target"),
    [
        (Booking.Status.CANCELLED, Booking.Status.ACTIVE),  # the tracker's own named example
        (Booking.Status.COMPLETE, Booking.Status.ACTIVE),
        (Booking.Status.HELD, Booking.Status.COMPLETE),  # can't skip ACTIVE
        (Booking.Status.ACTIVE, Booking.Status.HELD),  # no going backwards
    ],
)
def test_booking_status_invalid_transitions_rejected(resource, start, target):
    slot = _make_slot(resource)
    booking = Booking.objects.create(slot=slot, holder_ref="cust-1", status=start)

    with pytest.raises(ValidationError):
        booking.transition_status(target)

    booking.refresh_from_db()
    assert booking.status == start


# --- Fulfillment status state machine ---------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("start", "target"),
    [
        (Booking.FulfillmentStatus.SCHEDULED, Booking.FulfillmentStatus.IN_PROGRESS),
        (Booking.FulfillmentStatus.SCHEDULED, Booking.FulfillmentStatus.NO_SHOW),
        (Booking.FulfillmentStatus.IN_PROGRESS, Booking.FulfillmentStatus.COMPLETED),
    ],
)
def test_fulfillment_status_valid_transitions_succeed(resource, start, target):
    slot = _make_slot(resource)
    booking = Booking.objects.create(slot=slot, holder_ref="cust-1", fulfillment_status=start)

    booking.transition_fulfillment_status(target)

    booking.refresh_from_db()
    assert booking.fulfillment_status == target


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("start", "target"),
    [
        (Booking.FulfillmentStatus.SCHEDULED, Booking.FulfillmentStatus.COMPLETED),  # skip step
        (Booking.FulfillmentStatus.IN_PROGRESS, Booking.FulfillmentStatus.NO_SHOW),  # too late
        (Booking.FulfillmentStatus.COMPLETED, Booking.FulfillmentStatus.IN_PROGRESS),
        (Booking.FulfillmentStatus.NO_SHOW, Booking.FulfillmentStatus.SCHEDULED),
    ],
)
def test_fulfillment_status_invalid_transitions_rejected(resource, start, target):
    slot = _make_slot(resource)
    booking = Booking.objects.create(slot=slot, holder_ref="cust-1", fulfillment_status=start)

    with pytest.raises(ValidationError):
        booking.transition_fulfillment_status(target)

    booking.refresh_from_db()
    assert booking.fulfillment_status == start


# --- Redis-backed TTL HELD reservation window -------------------------------------------------


@pytest.mark.django_db
def test_hold_slot_creates_held_booking_and_decrements_capacity(resource, redis_client):
    slot = _make_slot(resource, capacity=1)

    booking = hold_slot(slot.id, holder_ref="cust-1", redis_client=redis_client, ttl_seconds=30)

    assert booking is not None
    assert booking.status == Booking.Status.HELD
    slot.refresh_from_db()
    assert slot.capacity_remaining == 0
    assert slot.status == Slot.Status.HELD


@pytest.mark.django_db
def test_hold_slot_fails_when_no_capacity(resource, redis_client):
    slot = _make_slot(resource, capacity=0)

    booking = hold_slot(slot.id, holder_ref="cust-1", redis_client=redis_client, ttl_seconds=30)

    assert booking is None


@pytest.mark.django_db
def test_expired_hold_auto_returns_slot_to_available_without_manual_intervention(
    resource, redis_client
):
    slot = _make_slot(resource, capacity=1)
    booking = hold_slot(
        slot.id, holder_ref="cust-1", redis_client=redis_client, ttl_seconds=1
    )
    assert booking is not None

    # Real Redis TTL expiry — not simulated/mocked. No code here manually flips anything;
    # the next line just waits for Redis itself to evict the key.
    time_module.sleep(1.5)

    released = release_expired_hold(booking.id, redis_client=redis_client)

    assert released is True
    slot.refresh_from_db()
    assert slot.status == Slot.Status.AVAILABLE
    assert slot.capacity_remaining == slot.capacity_total
    booking.refresh_from_db()
    assert booking.status == Booking.Status.CANCELLED


@pytest.mark.django_db
def test_release_expired_hold_is_a_noop_while_hold_still_active(resource, redis_client):
    slot = _make_slot(resource, capacity=1)
    booking = hold_slot(slot.id, holder_ref="cust-1", redis_client=redis_client, ttl_seconds=30)

    released = release_expired_hold(booking.id, redis_client=redis_client)

    assert released is False
    booking.refresh_from_db()
    assert booking.status == Booking.Status.HELD


@pytest.mark.django_db
def test_release_hold_now_releases_a_still_active_hold_immediately(resource, redis_client):
    """The §3.2 re-selection case: unlike release_expired_hold, this must succeed even
    though the TTL hasn't expired yet."""
    slot = _make_slot(resource, capacity=1)
    booking = hold_slot(slot.id, holder_ref="cust-1", redis_client=redis_client, ttl_seconds=30)

    released = release_hold_now(booking.id, redis_client=redis_client)

    assert released is True
    slot.refresh_from_db()
    assert slot.status == Slot.Status.AVAILABLE
    assert slot.capacity_remaining == slot.capacity_total
    booking.refresh_from_db()
    assert booking.status == Booking.Status.CANCELLED
    assert ReservationHold(redis_client=redis_client).is_active(booking.id) is False


@pytest.mark.django_db
def test_release_hold_now_is_a_noop_for_a_non_held_booking(resource, redis_client):
    slot = _make_slot(resource, capacity=1)
    booking = hold_slot(slot.id, holder_ref="cust-1", redis_client=redis_client, ttl_seconds=30)
    confirm_hold(booking.id, redis_client=redis_client)

    released = release_hold_now(booking.id, redis_client=redis_client)

    assert released is False
    booking.refresh_from_db()
    assert booking.status == Booking.Status.ACTIVE


@pytest.mark.django_db
def test_confirm_hold_transitions_to_active_and_clears_redis_key(resource, redis_client):
    slot = _make_slot(resource, capacity=1)
    booking = hold_slot(slot.id, holder_ref="cust-1", redis_client=redis_client, ttl_seconds=30)

    confirmed = confirm_hold(booking.id, redis_client=redis_client)

    assert confirmed.status == Booking.Status.ACTIVE
    assert redis_client.exists(f"inventory_core:hold:{booking.id}") == 0


@pytest.mark.django_db
def test_confirm_hold_rejects_an_already_expired_hold(resource, redis_client):
    slot = _make_slot(resource, capacity=1)
    booking = hold_slot(slot.id, holder_ref="cust-1", redis_client=redis_client, ttl_seconds=1)

    time_module.sleep(1.5)

    with pytest.raises(ValidationError):
        confirm_hold(booking.id, redis_client=redis_client)


@pytest.mark.django_db
def test_confirm_hold_on_an_expired_booking_actually_restores_slot_capacity(resource, redis_client):
    """livetracker2.md §3.11 finding 1's real Test Gate: before this phase, a confirm attempt
    against an expired hold raised without ever releasing it — the slot's capacity stayed
    leaked at 0 forever, confirmed live by direct code read (release_expired_hold had zero
    production callers). confirm_hold now actually recovers the capacity on this exact path,
    not just on some future unrelated release call."""
    slot = _make_slot(resource, capacity=1)
    booking = hold_slot(slot.id, holder_ref="cust-1", redis_client=redis_client, ttl_seconds=1)

    time_module.sleep(1.5)

    with pytest.raises(ValidationError):
        confirm_hold(booking.id, redis_client=redis_client)

    slot.refresh_from_db()
    assert slot.status == Slot.Status.AVAILABLE
    assert slot.capacity_remaining == slot.capacity_total
    booking.refresh_from_db()
    assert booking.status == Booking.Status.CANCELLED


@pytest.mark.django_db
def test_confirm_hold_is_idempotent_for_an_already_active_booking(resource, redis_client):
    """livetracker2.md §3.4's real gap, closed before implementing Confirm: retrying the
    identical confirm request against an already-ACTIVE booking must not raise and must not
    re-fire BookingConfirmed — a real double-submit/flaky-retry scenario, not hypothetical."""
    slot = _make_slot(resource, capacity=1)
    booking = hold_slot(slot.id, holder_ref="cust-1", redis_client=redis_client, ttl_seconds=30)
    confirm_hold(booking.id, redis_client=redis_client)

    published = []
    fake_bus = type("FakeBus", (), {"publish": lambda self, *a, **kw: published.append(a)})()

    confirmed_again = confirm_hold(booking.id, redis_client=redis_client, event_bus=fake_bus)

    assert confirmed_again.status == Booking.Status.ACTIVE
    assert published == []


# --- cancel_booking (livetracker2.md §3.5) --------------------------------------------------


@pytest.mark.django_db
def test_cancel_booking_transitions_to_cancelled_and_restores_capacity(resource, redis_client):
    slot = _make_slot(resource, capacity=1)
    booking = hold_slot(slot.id, holder_ref="cust-1", redis_client=redis_client, ttl_seconds=30)
    confirm_hold(booking.id, redis_client=redis_client)

    cancelled = cancel_booking(booking.id)

    assert cancelled.status == Booking.Status.CANCELLED
    slot.refresh_from_db()
    assert slot.capacity_remaining == 1
    assert slot.status == Slot.Status.AVAILABLE


@pytest.mark.django_db
def test_cancel_booking_rejects_a_still_held_booking(resource, redis_client):
    """A still-HELD hold was never actually offered to the customer as a confirmed,
    cancellable Order — use release_hold_now for that case instead."""
    slot = _make_slot(resource, capacity=1)
    booking = hold_slot(slot.id, holder_ref="cust-1", redis_client=redis_client, ttl_seconds=30)

    with pytest.raises(ValidationError):
        cancel_booking(booking.id)


@pytest.mark.django_db
def test_cancel_booking_publishes_slot_released_and_booking_cancelled_events(
    resource, redis_client
):
    slot = _make_slot(resource, capacity=1)
    booking = hold_slot(slot.id, holder_ref="cust-1", redis_client=redis_client, ttl_seconds=30)
    confirm_hold(booking.id, redis_client=redis_client)

    published = []
    fake_bus = type("FakeBus", (), {"publish": lambda self, *a, **kw: published.append(a[0])})()

    cancel_booking(booking.id, event_bus=fake_bus)

    assert set(published) == {"SlotReleased", "BookingCancelled"}


# --- reschedule_active_booking (livetracker2.md §3.5) ----------------------------------------


@pytest.mark.django_db
def test_reschedule_active_booking_moves_capacity_between_slots(resource, redis_client):
    old_slot = _make_slot(resource, capacity=1)
    new_slot = Slot.objects.create(
        resource=resource,
        start_time=old_slot.start_time + dt.timedelta(hours=1),
        end_time=old_slot.end_time + dt.timedelta(hours=1),
        capacity_total=1,
        capacity_remaining=1,
    )
    booking = hold_slot(
        old_slot.id, holder_ref="cust-1", redis_client=redis_client, ttl_seconds=30
    )
    confirm_hold(booking.id, redis_client=redis_client)

    rescheduled = reschedule_active_booking(booking.id, new_slot.id)

    assert rescheduled.slot_id == new_slot.id
    assert rescheduled.status == Booking.Status.ACTIVE
    old_slot.refresh_from_db()
    new_slot.refresh_from_db()
    assert old_slot.capacity_remaining == 1
    assert old_slot.status == Slot.Status.AVAILABLE
    assert new_slot.capacity_remaining == 0
    assert new_slot.status == Slot.Status.HELD


@pytest.mark.django_db
def test_reschedule_active_booking_rejects_a_still_held_booking(resource, redis_client):
    old_slot = _make_slot(resource, capacity=1)
    new_slot = Slot.objects.create(
        resource=resource,
        start_time=old_slot.start_time + dt.timedelta(hours=1),
        end_time=old_slot.end_time + dt.timedelta(hours=1),
        capacity_total=1,
        capacity_remaining=1,
    )
    booking = hold_slot(
        old_slot.id, holder_ref="cust-1", redis_client=redis_client, ttl_seconds=30
    )

    with pytest.raises(ValidationError):
        reschedule_active_booking(booking.id, new_slot.id)


@pytest.mark.django_db
def test_reschedule_active_booking_rejects_a_full_new_slot(resource, redis_client):
    old_slot = _make_slot(resource, capacity=1)
    new_slot = Slot.objects.create(
        resource=resource,
        start_time=old_slot.start_time + dt.timedelta(hours=1),
        end_time=old_slot.end_time + dt.timedelta(hours=1),
        capacity_total=1,
        capacity_remaining=0,
    )
    booking = hold_slot(
        old_slot.id, holder_ref="cust-1", redis_client=redis_client, ttl_seconds=30
    )
    confirm_hold(booking.id, redis_client=redis_client)

    with pytest.raises(ValidationError):
        reschedule_active_booking(booking.id, new_slot.id)

    old_slot.refresh_from_db()
    assert old_slot.capacity_remaining == 0  # unchanged — the reschedule never committed


@pytest.mark.django_db
def test_reschedule_active_booking_rejects_the_same_slot(resource, redis_client):
    slot = _make_slot(resource, capacity=1)
    booking = hold_slot(slot.id, holder_ref="cust-1", redis_client=redis_client, ttl_seconds=30)
    confirm_hold(booking.id, redis_client=redis_client)

    with pytest.raises(ValidationError):
        reschedule_active_booking(booking.id, slot.id)


# --- BookingAuditLogEntry + correlation_id (livetracker2.md §3.10) -----------------------------


@pytest.mark.django_db
def test_confirm_hold_records_a_real_audit_log_entry_with_correlation_id(resource, redis_client):
    slot = _make_slot(resource, capacity=1)
    booking = hold_slot(slot.id, holder_ref="cust-1", redis_client=redis_client, ttl_seconds=30)

    fake_bus = type("FakeBus", (), {"publish": lambda self, *a, **kw: None})()
    confirm_hold(
        booking.id, redis_client=redis_client, event_bus=fake_bus, correlation_id="corr-abc-123"
    )

    entry = BookingAuditLogEntry.objects.get(booking_id_text=str(booking.id))
    assert entry.event_type == "BookingConfirmed"
    assert entry.correlation_id == "corr-abc-123"
    assert entry.booking_id == booking.id


@pytest.mark.django_db
def test_confirm_hold_without_an_event_bus_records_no_audit_entry(resource, redis_client):
    """`event_bus=None` (the default) means "no business-event observability wired
    for this call" — both the event publish and the audit-log write are gated on
    the same flag, not two independent ones (see reservation.py's own docstring)."""
    slot = _make_slot(resource, capacity=1)
    booking = hold_slot(slot.id, holder_ref="cust-1", redis_client=redis_client, ttl_seconds=30)

    confirm_hold(booking.id, redis_client=redis_client)

    assert not BookingAuditLogEntry.objects.filter(booking_id_text=str(booking.id)).exists()


@pytest.mark.django_db
def test_cancel_booking_records_a_real_audit_log_entry(resource, redis_client):
    slot = _make_slot(resource, capacity=1)
    booking = hold_slot(slot.id, holder_ref="cust-1", redis_client=redis_client, ttl_seconds=30)
    confirm_hold(booking.id, redis_client=redis_client)

    fake_bus = type("FakeBus", (), {"publish": lambda self, *a, **kw: None})()
    cancel_booking(booking.id, event_bus=fake_bus, correlation_id="corr-cancel-1")

    entry = BookingAuditLogEntry.objects.get(
        booking_id_text=str(booking.id), event_type="BookingCancelled"
    )
    assert entry.correlation_id == "corr-cancel-1"
    assert entry.detail["reason"] == "customer_cancel"


@pytest.mark.django_db
def test_reschedule_active_booking_records_a_real_audit_log_entry(resource, redis_client):
    old_slot = _make_slot(resource, capacity=1)
    new_slot = Slot.objects.create(
        resource=resource,
        start_time=old_slot.start_time + dt.timedelta(hours=1),
        end_time=old_slot.end_time + dt.timedelta(hours=1),
        capacity_total=1,
        capacity_remaining=1,
    )
    booking = hold_slot(
        old_slot.id, holder_ref="cust-1", redis_client=redis_client, ttl_seconds=30
    )
    confirm_hold(booking.id, redis_client=redis_client)

    fake_bus = type("FakeBus", (), {"publish": lambda self, *a, **kw: None})()
    reschedule_active_booking(
        booking.id, new_slot.id, event_bus=fake_bus, correlation_id="corr-update-1"
    )

    entry = BookingAuditLogEntry.objects.get(
        booking_id_text=str(booking.id), event_type="BookingRescheduled"
    )
    assert entry.correlation_id == "corr-update-1"
    assert entry.detail["new_slot_id"] == str(new_slot.id)


@pytest.mark.django_db
def test_release_expired_hold_records_an_audit_entry_with_no_correlation_id(
    resource, redis_client
):
    """Opportunistic expiry (§1.3) has no single customer action to honestly
    attribute a correlation id to — `correlation_id` stays `None` by design, not
    an oversight (see release_expired_hold's own docstring)."""
    slot = _make_slot(resource, capacity=1)
    booking = hold_slot(slot.id, holder_ref="cust-1", redis_client=redis_client, ttl_seconds=1)
    time_module.sleep(1.5)

    fake_bus = type("FakeBus", (), {"publish": lambda self, *a, **kw: None})()
    released = release_expired_hold(booking.id, redis_client=redis_client, event_bus=fake_bus)

    assert released is True
    entry = BookingAuditLogEntry.objects.get(booking_id_text=str(booking.id))
    assert entry.event_type == "BookingCancelled"
    assert entry.correlation_id is None
    assert entry.detail["reason"] == "hold_expired"


# --- sweep_expired_holds (livetracker2.md §3.11) --------------------------------------------


@pytest.mark.django_db
def test_sweep_expired_holds_releases_only_the_ones_that_actually_expired(resource, redis_client):
    """The real safety net for a hold nobody ever touches again — the customer just closes
    the tab, so nothing else would ever call release_expired_hold for it. A still-active hold
    in the same sweep must be left alone."""
    slot_expired = _make_slot(resource, capacity=1)
    slot_active = _make_slot(resource, capacity=1)
    expired_booking = hold_slot(
        slot_expired.id, holder_ref="cust-1", redis_client=redis_client, ttl_seconds=1
    )
    active_booking = hold_slot(
        slot_active.id, holder_ref="cust-2", redis_client=redis_client, ttl_seconds=30
    )
    time_module.sleep(1.5)

    from inventory_core.reconciliation import sweep_expired_holds

    released_count = sweep_expired_holds(redis_client=redis_client)

    assert released_count == 1
    expired_booking.refresh_from_db()
    assert expired_booking.status == Booking.Status.CANCELLED
    slot_expired.refresh_from_db()
    assert slot_expired.capacity_remaining == 1
    active_booking.refresh_from_db()
    assert active_booking.status == Booking.Status.HELD
    slot_active.refresh_from_db()
    assert slot_active.capacity_remaining == 0


@pytest.mark.django_db
def test_sweep_expired_holds_returns_zero_when_nothing_is_held(resource, redis_client):
    from inventory_core.reconciliation import sweep_expired_holds

    assert sweep_expired_holds(redis_client=redis_client) == 0


@pytest.mark.django_db
def test_sweep_expired_holds_publishes_real_events_when_given_an_event_bus(resource, redis_client):
    slot = _make_slot(resource, capacity=1)
    booking = hold_slot(slot.id, holder_ref="cust-1", redis_client=redis_client, ttl_seconds=1)
    time_module.sleep(1.5)

    published = []
    fake_bus = type("FakeBus", (), {"publish": lambda self, *a, **kw: published.append(a)})()

    from inventory_core.reconciliation import sweep_expired_holds

    released_count = sweep_expired_holds(redis_client=redis_client, event_bus=fake_bus)

    assert released_count == 1
    assert len(published) == 2  # SlotReleased + BookingCancelled
    entry = BookingAuditLogEntry.objects.get(booking_id_text=str(booking.id))
    assert entry.detail["reason"] == "hold_expired"


# --- complete_active_booking / sweep_completed_bookings (livetracker2.md §4.5) -----------------


def _make_ended_slot(resource, *, capacity=1):
    now = timezone.now()
    return Slot.objects.create(
        resource=resource,
        start_time=now - dt.timedelta(hours=2),
        end_time=now - dt.timedelta(hours=1),
        capacity_total=capacity,
        capacity_remaining=capacity - 1,
    )


@pytest.mark.django_db
def test_complete_active_booking_completes_once_the_slots_end_time_has_passed(resource):
    slot = _make_ended_slot(resource)
    booking = Booking.objects.create(
        slot=slot, holder_ref="cust-1", status=Booking.Status.ACTIVE
    )

    completed = complete_active_booking(booking.id)

    assert completed is True
    booking.refresh_from_db()
    assert booking.status == Booking.Status.COMPLETE


@pytest.mark.django_db
def test_complete_active_booking_is_a_noop_while_the_slot_hasnt_ended_yet(resource):
    slot = _make_slot(resource)  # ends 30 minutes from now
    booking = Booking.objects.create(
        slot=slot, holder_ref="cust-1", status=Booking.Status.ACTIVE
    )

    completed = complete_active_booking(booking.id)

    assert completed is False
    booking.refresh_from_db()
    assert booking.status == Booking.Status.ACTIVE


@pytest.mark.django_db
def test_complete_active_booking_is_a_noop_for_a_non_active_booking(resource):
    slot = _make_ended_slot(resource)
    booking = Booking.objects.create(
        slot=slot, holder_ref="cust-1", status=Booking.Status.CANCELLED
    )

    completed = complete_active_booking(booking.id)

    assert completed is False
    booking.refresh_from_db()
    assert booking.status == Booking.Status.CANCELLED


@pytest.mark.django_db
def test_complete_active_booking_does_not_touch_slot_capacity(resource):
    """Distinct from cancel/release: completion doesn't return capacity to the pool — the
    fulfillment window that just ended already consumed it for real."""
    slot = _make_ended_slot(resource, capacity=1)
    remaining_before = slot.capacity_remaining
    booking = Booking.objects.create(
        slot=slot, holder_ref="cust-1", status=Booking.Status.ACTIVE
    )

    complete_active_booking(booking.id)

    slot.refresh_from_db()
    assert slot.capacity_remaining == remaining_before


@pytest.mark.django_db
def test_complete_active_booking_records_a_real_audit_log_entry(resource):
    slot = _make_ended_slot(resource)
    booking = Booking.objects.create(
        slot=slot, holder_ref="cust-1", status=Booking.Status.ACTIVE
    )

    fake_bus = type("FakeBus", (), {"publish": lambda self, *a, **kw: None})()
    complete_active_booking(booking.id, event_bus=fake_bus)

    entry = BookingAuditLogEntry.objects.get(
        booking_id_text=str(booking.id), event_type="BookingCompleted"
    )
    assert entry.correlation_id is None
    assert entry.detail["reason"] == "fulfillment_window_ended"


@pytest.mark.django_db
def test_sweep_completed_bookings_completes_only_the_ones_that_actually_ended(resource):
    ended_slot = _make_ended_slot(resource)
    ongoing_slot = _make_slot(resource)
    ended_booking = Booking.objects.create(
        slot=ended_slot, holder_ref="cust-1", status=Booking.Status.ACTIVE
    )
    ongoing_booking = Booking.objects.create(
        slot=ongoing_slot, holder_ref="cust-2", status=Booking.Status.ACTIVE
    )

    from inventory_core.reconciliation import sweep_completed_bookings

    completed_count = sweep_completed_bookings()

    assert completed_count == 1
    ended_booking.refresh_from_db()
    assert ended_booking.status == Booking.Status.COMPLETE
    ongoing_booking.refresh_from_db()
    assert ongoing_booking.status == Booking.Status.ACTIVE


@pytest.mark.django_db
def test_sweep_completed_bookings_returns_zero_when_nothing_is_active(resource):
    from inventory_core.reconciliation import sweep_completed_bookings

    assert sweep_completed_bookings() == 0


# --- §4.2 multi-resource booking (hold_multi_resource_booking / find_group_bookings) -----------


@pytest.fixture
def second_resource(db):
    return Resource.objects.create(owner_ref="biz-1", name="Bay 1")


@pytest.mark.django_db
def test_hold_multi_resource_booking_succeeds_when_every_slot_has_capacity(
    resource, second_resource, redis_client
):
    """FUNC: §4.2's own Test Gate wording — "succeeds when both are" available."""
    mechanic_slot = _make_slot(resource, capacity=1)
    bay_slot = _make_slot(second_resource, capacity=1)

    bookings = hold_multi_resource_booking(
        [mechanic_slot.id, bay_slot.id],
        holder_ref="cust-1",
        redis_client=redis_client,
        ttl_seconds=60,
    )

    assert bookings is not None
    assert len(bookings) == 2
    assert [b.slot_id for b in bookings] == [mechanic_slot.id, bay_slot.id]
    assert all(b.status == Booking.Status.HELD for b in bookings)
    group_ids = {b.domain_data["booking_group_id"] for b in bookings}
    assert len(group_ids) == 1  # both share the same group id

    mechanic_slot.refresh_from_db()
    bay_slot.refresh_from_db()
    assert mechanic_slot.capacity_remaining == 0
    assert bay_slot.capacity_remaining == 0
    for b in bookings:
        assert ReservationHold(redis_client=redis_client).is_active(b.id)


@pytest.mark.django_db
def test_hold_multi_resource_booking_fails_cleanly_if_only_one_resource_is_available(
    resource, second_resource, redis_client
):
    """EDGE: §4.2's own Test Gate wording — "correctly fails if only one of the two
    required resources is available." The already-full slot's capacity must stay
    exactly as it was (0), and the *other*, genuinely-available slot must NOT be
    left partially held either — a real all-or-nothing guarantee, not just "the
    overall call reports failure while quietly leaking a real hold on one side."""
    mechanic_slot = _make_slot(resource, capacity=1)
    bay_slot = _make_slot(second_resource, capacity=0)  # already fully booked

    bookings = hold_multi_resource_booking(
        [mechanic_slot.id, bay_slot.id],
        holder_ref="cust-1",
        redis_client=redis_client,
        ttl_seconds=60,
    )

    assert bookings is None
    mechanic_slot.refresh_from_db()
    bay_slot.refresh_from_db()
    assert mechanic_slot.capacity_remaining == 1  # untouched, not partially held
    assert bay_slot.capacity_remaining == 0
    assert Booking.objects.count() == 0  # no partial Booking row left behind either


@pytest.mark.django_db
def test_hold_multi_resource_booking_returns_bookings_in_the_order_slot_ids_were_given(
    resource, second_resource, redis_client
):
    """The internal deterministic locking order (sorted by slot id, the same
    deadlock-avoidance precedent as `reschedule_active_booking`) must never leak
    into the caller-visible result order — callers pass `[mechanic_slot_id,
    bay_slot_id]` and must get bookings back in that same order regardless of
    which slot id happens to sort first."""
    mechanic_slot = _make_slot(resource, capacity=1)
    bay_slot = _make_slot(second_resource, capacity=1)

    # Deliberately request in both possible orders and confirm the result always
    # mirrors the requested order, not the internal sort order.
    for slot_ids in (
        [mechanic_slot.id, bay_slot.id],
        [bay_slot.id, mechanic_slot.id],
    ):
        Booking.objects.all().delete()
        mechanic_slot.capacity_remaining = 1
        mechanic_slot.status = Slot.Status.AVAILABLE
        mechanic_slot.save()
        bay_slot.capacity_remaining = 1
        bay_slot.status = Slot.Status.AVAILABLE
        bay_slot.save()

        bookings = hold_multi_resource_booking(
            slot_ids, holder_ref="cust-1", redis_client=redis_client, ttl_seconds=60
        )
        assert [b.slot_id for b in bookings] == slot_ids


@pytest.mark.django_db
def test_find_group_bookings_returns_every_sibling_including_itself(
    resource, second_resource, redis_client
):
    mechanic_slot = _make_slot(resource, capacity=1)
    bay_slot = _make_slot(second_resource, capacity=1)
    bookings = hold_multi_resource_booking(
        [mechanic_slot.id, bay_slot.id],
        holder_ref="cust-1",
        redis_client=redis_client,
        ttl_seconds=60,
    )

    siblings = find_group_bookings(bookings[0])

    assert {b.id for b in siblings} == {b.id for b in bookings}


@pytest.mark.django_db
def test_find_group_bookings_preserves_the_customers_own_selection_order(
    resource, second_resource, redis_client
):
    """livetracker3.md §6.1 fix (2026-08-01): found live during that phase's own
    E2E verification — a real customer saw "Bay + Mechanic" at select time and
    "Mechanic + Bay" at confirm time for the exact same booking, because
    `find_group_bookings` used to order by `.id` (a random UUID unrelated to
    which resource the customer actually picked first), while
    `hold_multi_resource_booking` itself already correctly returned bookings in
    the caller's own order. Requesting in both possible id-sort orders (as
    `test_hold_multi_resource_booking_returns_bookings_in_the_order_slot_ids_were_given`
    already does above, for the same "must not leak the internal sort order"
    reason) proves this isn't order working by UUID-sort coincidence."""
    mechanic_slot = _make_slot(resource, capacity=1)
    bay_slot = _make_slot(second_resource, capacity=1)

    for slot_ids in (
        [mechanic_slot.id, bay_slot.id],
        [bay_slot.id, mechanic_slot.id],
    ):
        Booking.objects.all().delete()
        mechanic_slot.capacity_remaining = 1
        mechanic_slot.status = Slot.Status.AVAILABLE
        mechanic_slot.save()
        bay_slot.capacity_remaining = 1
        bay_slot.status = Slot.Status.AVAILABLE
        bay_slot.save()

        bookings = hold_multi_resource_booking(
            slot_ids, holder_ref="cust-1", redis_client=redis_client, ttl_seconds=60
        )

        siblings = find_group_bookings(bookings[0])
        assert [b.slot_id for b in siblings] == slot_ids
        # ...and every other member of the group agrees on the same order, not
        # just the one `find_group_bookings` happened to be called on.
        assert [b.slot_id for b in find_group_bookings(bookings[1])] == slot_ids


@pytest.mark.django_db
def test_find_group_bookings_returns_just_itself_for_an_ordinary_single_resource_booking(
    resource, redis_client
):
    """§4.1-and-earlier's ordinary case — a plain `hold_slot()` booking has no
    `booking_group_id` at all, and must not be mistaken for a group of one
    something-else; it's simply not part of any group."""
    slot = _make_slot(resource, capacity=1)
    booking = hold_slot(slot.id, holder_ref="cust-1", redis_client=redis_client, ttl_seconds=60)

    assert find_group_bookings(booking) == [booking]


@pytest.mark.django_db(transaction=True)
def test_hold_multi_resource_booking_locks_slots_in_a_deterministic_order_to_avoid_deadlock(
    resource, second_resource, redis_client
):
    """Real concurrency proof, not just code inspection: two concurrent multi-resource
    holds racing over the *same pair* of slots, submitted in opposite id order, must
    both complete (one succeeding, one correctly failing on capacity) rather than
    deadlocking — proving the internal sort-by-id locking order is actually applied,
    the same deadlock-avoidance precedent already proven for
    `reschedule_active_booking`.

    `transaction=True` (not the default `django_db`), same reasoning as Phase 1.2's
    own concurrent-write test: the background threads below open their own real DB
    connections, which can't see fixture-created rows still sitting inside the
    default single-wrapped-transaction the plain `django_db` marker would use."""
    import threading

    from django.db import connection

    mechanic_slot = _make_slot(resource, capacity=1)
    bay_slot = _make_slot(second_resource, capacity=1)
    results = []

    def _hold(slot_ids):
        try:
            results.append(
                hold_multi_resource_booking(
                    slot_ids, holder_ref="cust-1", redis_client=redis_client, ttl_seconds=60
                )
            )
        finally:
            connection.close()

    t1 = threading.Thread(target=_hold, args=([mechanic_slot.id, bay_slot.id],))
    t2 = threading.Thread(target=_hold, args=([bay_slot.id, mechanic_slot.id],))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert not t1.is_alive() and not t2.is_alive()  # neither thread deadlocked
    successes = [r for r in results if r is not None]
    assert len(successes) == 1  # only one of the two could win the shared capacity
