"""livetracker4.md §2.2 Test Gate — Event Replay & Recovery.

FUNC/DR: deliberately leave a consumer un-driven for a real window (simulating
it being down, or freshly registered), then confirm the replay tool catches it
up correctly from the durable `BookingAuditLogEntry` log — not just the events
that happened to be consumed live.
"""

import datetime as dt
from unittest.mock import patch

import pytest
from django.utils import timezone
from inventory_core.consumers import audit_log_consumer
from inventory_core.events import BookingEvent
from inventory_core.models import BookingAuditLogEntry, Resource, Slot
from inventory_core.replay import replay_events
from inventory_core.reservation import confirm_hold, hold_slot

from core.events import get_event_bus
from core.metrics import booking_lifecycle_consumer


@pytest.fixture
def bus():
    b = get_event_bus()
    b._redis.delete(b.queue_name, b.dlq_name, b.processing_queue_name)
    yield b
    b._redis.delete(b.queue_name, b.dlq_name, b.processing_queue_name)


@pytest.fixture
def redis_client(bus):
    return bus._redis


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


def _simulate_live_audit_log_writes(bus):
    """livetracker4.md §2.1 cutover (2026-08-02): `confirm_hold()` no longer
    writes `BookingAuditLogEntry` inline — the real worker's `audit_log_consumer`
    does, by consuming the published event. This simulates that worker having
    already run live and reliably (the real, expected shape in production —
    audit logging is the durable log replay itself reads from), populating
    exactly the durable log replay is meant to backfill a *different*,
    less-critical consumer (metrics, the one actually under test here) from.
    Deliberately does not drive `booking_lifecycle_consumer` — that's the
    consumer this file's tests are proving replay catches up correctly."""
    while bus.queue_length() > 0:
        event = bus.consume_one(timeout_seconds=2)
        if event is None:
            break
        audit_log_consumer(event)
        bus.ack(event)


@pytest.mark.django_db
def test_replay_catches_up_a_metrics_consumer_that_missed_the_live_events(
    resource, bus, redis_client
):
    """The real §2.2 Test Gate. Calling `confirm_hold()` directly (bypassing
    `confirm_service.py`'s own HTTP-dispatch-layer call site — a genuinely
    separate one, see §2.1's own design-audit finding) plus manually driving
    only the audit-log consumer (`_simulate_live_audit_log_writes`, simulating
    the worker having reliably run live for that consumer) means the metrics
    consumer specifically never sees these 3 confirms live, while the durable
    audit log still exists — exactly the "one consumer was down/never wired"
    scenario replay exists for."""
    window_start = timezone.now()

    for _ in range(3):
        slot = _make_slot(resource)
        booking = hold_slot(slot.id, holder_ref="cust-1", redis_client=redis_client, ttl_seconds=30)
        confirm_hold(booking.id, redis_client=redis_client, event_bus=bus)
    _simulate_live_audit_log_writes(bus)

    window_end = timezone.now() + dt.timedelta(seconds=1)

    assert (
        BookingAuditLogEntry.objects.filter(
            event_type=BookingEvent.CONFIRMED, created_at__gte=window_start
        ).count()
        == 3
    )

    with patch("core.metrics.increment_counter") as mock_incr:
        replayed = replay_events(
            start=window_start, end=window_end, handler=booking_lifecycle_consumer
        )

    assert replayed == 3
    assert mock_incr.call_count == 3
    mock_incr.assert_called_with("bpp:metrics:booking_confirmed")


@pytest.mark.django_db
def test_replay_respects_the_time_window_boundaries(resource, bus, redis_client):
    slot = _make_slot(resource)
    booking = hold_slot(slot.id, holder_ref="cust-1", redis_client=redis_client, ttl_seconds=30)
    confirm_hold(booking.id, redis_client=redis_client, event_bus=bus)

    far_future_start = timezone.now() + dt.timedelta(days=1)
    far_future_end = far_future_start + dt.timedelta(hours=1)

    with patch("core.metrics.increment_counter") as mock_incr:
        replayed = replay_events(
            start=far_future_start, end=far_future_end, handler=booking_lifecycle_consumer
        )

    assert replayed == 0
    mock_incr.assert_not_called()


@pytest.mark.django_db
def test_replay_can_scope_to_a_single_booking(resource, bus, redis_client):
    window_start = timezone.now()
    slot_a = _make_slot(resource)
    booking_a = hold_slot(slot_a.id, holder_ref="cust-1", redis_client=redis_client, ttl_seconds=30)
    confirm_hold(booking_a.id, redis_client=redis_client, event_bus=bus)

    slot_b = _make_slot(resource)
    booking_b = hold_slot(slot_b.id, holder_ref="cust-2", redis_client=redis_client, ttl_seconds=30)
    confirm_hold(booking_b.id, redis_client=redis_client, event_bus=bus)
    _simulate_live_audit_log_writes(bus)

    window_end = timezone.now() + dt.timedelta(seconds=1)

    with patch("core.metrics.increment_counter") as mock_incr:
        replayed = replay_events(
            start=window_start,
            end=window_end,
            booking_id=booking_a.id,
            handler=booking_lifecycle_consumer,
        )

    assert replayed == 1
    mock_incr.assert_called_once_with("bpp:metrics:booking_confirmed")


@pytest.mark.django_db
def test_replay_does_not_create_a_duplicate_audit_log_entry(resource, bus, redis_client):
    """Real design constraint, recorded in replay_events.py's own docstring: the
    audit-log consumer is deliberately excluded from the replayable-consumer
    registry (see management/commands/replay_events.py) because it isn't itself
    idempotent on repeated calls — replaying it would create a second,
    misleading BookingAuditLogEntry row for the same real transition. This test
    proves replay (scoped to metrics, the intended real use) leaves the audit
    log's own row count completely unchanged."""
    window_start = timezone.now()
    slot = _make_slot(resource)
    booking = hold_slot(slot.id, holder_ref="cust-1", redis_client=redis_client, ttl_seconds=30)
    confirm_hold(booking.id, redis_client=redis_client, event_bus=bus)
    _simulate_live_audit_log_writes(bus)
    window_end = timezone.now() + dt.timedelta(seconds=1)

    before = BookingAuditLogEntry.objects.count()
    assert before > 0, "test needs a real pre-existing audit-log row to prove replay doesn't dup it"
    with patch("core.metrics.increment_counter"):
        replay_events(start=window_start, end=window_end, handler=booking_lifecycle_consumer)
    after = BookingAuditLogEntry.objects.count()

    assert after == before
