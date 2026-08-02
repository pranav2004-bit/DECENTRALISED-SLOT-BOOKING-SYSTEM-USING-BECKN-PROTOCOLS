import os

import pytest

from .bus import EventBus, process_with_dlq
from .worker import run_worker

REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6390/0")


@pytest.fixture
def bus():
    b = EventBus(redis_url=REDIS_URL, queue_name="test-queue", dlq_name="test-dlq")
    b._redis.delete(b.queue_name, b.dlq_name, b.processing_queue_name, b.heartbeat_key)
    yield b
    b._redis.delete(b.queue_name, b.dlq_name, b.processing_queue_name, b.heartbeat_key)


def test_publish_and_consume_round_trip(bus):
    event_id = bus.publish("booking.created", {"booking_id": "b1"})
    event = bus.consume_one(timeout_seconds=2)
    assert event is not None
    assert event["event_id"] == event_id
    assert event["event_type"] == "booking.created"
    assert event["payload"] == {"booking_id": "b1"}


def test_consume_times_out_on_empty_queue(bus):
    event = bus.consume_one(timeout_seconds=0.5)
    assert event is None


def test_queue_length_reflects_pending_events(bus):
    bus.publish("a", {})
    bus.publish("b", {})
    assert bus.queue_length() == 2
    bus.consume_one(timeout_seconds=1)
    assert bus.queue_length() == 1


def test_failed_event_is_routed_to_dlq_not_lost(bus):
    bus.publish("booking.created", {"booking_id": "b2"})
    event = bus.consume_one(timeout_seconds=2)

    def failing_handler(_event):
        raise ValueError("simulated processing failure")

    success = process_with_dlq(bus, event, failing_handler)
    assert success is False
    assert bus.dlq_length() == 1
    dlq_events = bus.peek_dlq()
    assert dlq_events[0]["payload"] == {"booking_id": "b2"}
    assert "simulated processing failure" in dlq_events[0]["error"]


def test_successful_event_is_not_routed_to_dlq(bus):
    bus.publish("booking.created", {"booking_id": "b3"})
    event = bus.consume_one(timeout_seconds=2)

    processed = []

    def good_handler(e):
        processed.append(e)

    success = process_with_dlq(bus, event, good_handler)
    assert success is True
    assert bus.dlq_length() == 0
    assert len(processed) == 1


def test_consume_one_holds_the_event_in_a_processing_list_until_acked(bus):
    """livetracker4.md §2.1's own reliable-delivery fix: consume_one() must not
    delete the event outright — a worker crash between pop and finish must not
    silently lose it. Confirmed here at the primitive level, before any real
    worker exists to exercise it end-to-end."""
    bus.publish("booking.created", {"booking_id": "b4"})
    event = bus.consume_one(timeout_seconds=2)

    assert bus.queue_length() == 0  # gone from the main queue...
    assert bus._redis.llen(bus.processing_queue_name) == 1  # ...but not lost

    bus.ack(event)
    assert bus._redis.llen(bus.processing_queue_name) == 0


def test_recover_orphaned_requeues_an_event_left_by_a_crashed_consumer(bus):
    """Simulates the exact crash this fix exists for: consume_one() pops an event,
    the "worker" dies before ever calling ack()/send_to_dlq(). A fresh
    recover_orphaned() call (what a real worker runs once at startup) must put it
    back on the main queue so the next consume_one() picks it up again — not lose
    it, not leave it stuck in the processing list forever."""
    bus.publish("booking.created", {"booking_id": "b5"})
    crashed_event = bus.consume_one(timeout_seconds=2)  # popped, never acked — simulated crash
    assert bus._redis.llen(bus.processing_queue_name) == 1

    recovered_count = bus.recover_orphaned()
    assert recovered_count == 1
    assert bus._redis.llen(bus.processing_queue_name) == 0
    assert bus.queue_length() == 1

    fresh_bus = EventBus(redis_url=REDIS_URL, queue_name="test-queue", dlq_name="test-dlq")
    redelivered = fresh_bus.consume_one(timeout_seconds=2)
    assert redelivered["event_id"] == crashed_event["event_id"]
    assert redelivered["payload"] == {"booking_id": "b5"}
    fresh_bus.ack(redelivered)


def test_consume_one_preserves_fifo_order_with_multiple_events_queued(bus):
    """Real bug found live (livetracker4.md §2.1, second pass): the reliable-
    delivery fix above initially used BRPOPLPUSH, which pops from the *same*
    end publish()'s own RPUSH pushes to — turning the pair into a LIFO stack,
    silently reversing delivery order for every real consumer. No existing test
    published more than one event before consuming, so this went undetected
    until a real consumer (a BPP worker's own ordering test) caught it live.
    Fixed with BLMOVE(..., "LEFT", "RIGHT"), restoring the original
    RPUSH+BLPOP FIFO behavior. This test is the one that would have caught the
    regression at this primitive level directly."""
    bus.publish("first", {"order": 1})
    bus.publish("second", {"order": 2})
    bus.publish("third", {"order": 3})

    first = bus.consume_one(timeout_seconds=2)
    second = bus.consume_one(timeout_seconds=2)
    third = bus.consume_one(timeout_seconds=2)

    assert [e["payload"]["order"] for e in (first, second, third)] == [1, 2, 3]


def test_send_to_dlq_also_removes_the_event_from_the_processing_list(bus):
    """A DLQ'd event is a terminal outcome — it must not also be recoverable by
    recover_orphaned() afterward, or a later worker restart would requeue an
    event already sitting in the DLQ for a second, pointless attempt."""
    bus.publish("booking.created", {"booking_id": "b6"})
    event = bus.consume_one(timeout_seconds=2)

    bus.send_to_dlq(event, error="simulated poison")

    assert bus._redis.llen(bus.processing_queue_name) == 0
    assert bus.dlq_length() == 1
    assert bus.recover_orphaned() == 0


# --- livetracker4.md §2.1 gap-closure (2026-08-02): worker liveness + DLQ reprocessing ---


def test_worker_is_not_alive_before_any_heartbeat_is_written(bus):
    assert bus.worker_is_alive() is False


def test_write_heartbeat_makes_the_worker_alive_until_it_expires(bus):
    bus.write_heartbeat(ttl_seconds=0.3)
    assert bus.worker_is_alive() is True

    import time

    time.sleep(0.5)
    assert bus.worker_is_alive() is False


def test_requeue_from_dlq_moves_a_specific_poisoned_event_back_to_the_main_queue(bus):
    event_id = bus.publish("booking.created", {"booking_id": "b7"})
    event = bus.consume_one(timeout_seconds=2)
    bus.send_to_dlq(event, error="simulated poison")
    assert bus.dlq_length() == 1
    assert bus.queue_length() == 0

    requeued = bus.requeue_from_dlq(event_id)

    assert requeued is True
    assert bus.dlq_length() == 0
    assert bus.queue_length() == 1
    redelivered = bus.consume_one(timeout_seconds=2)
    assert redelivered["event_type"] == "booking.created"
    assert redelivered["payload"] == {"booking_id": "b7"}
    # A genuinely new event — the DLQ reprocessing tool doesn't resurrect the
    # exact same event_id, avoiding any doubt about how the idempotency ledger
    # (keyed by event_id, layered on top of this bus by callers) would treat it.
    assert redelivered["event_id"] != event_id


def test_requeue_from_dlq_returns_false_for_an_unknown_event_id(bus):
    assert bus.requeue_from_dlq("no-such-event-id") is False


def test_run_worker_calls_on_heartbeat_once_per_loop_iteration(bus):
    """The real seam `write_heartbeat` is wired through for a genuine worker
    process (see BPP/backend/core/management/commands/run_event_worker.py) —
    verified here at the generic run_worker level, without needing a real
    Django app/DISPATCH table."""
    calls = []
    iterations = {"n": 0}

    def should_stop():
        iterations["n"] += 1
        return iterations["n"] > 3

    run_worker(
        bus,
        dispatch={},
        poll_timeout_seconds=0.1,
        should_stop=should_stop,
        on_heartbeat=lambda: calls.append(1),
    )

    assert len(calls) == 3


def test_requeue_from_dlq_only_touches_the_matching_entry_not_every_dlq_entry(bus):
    bus.publish("a", {"n": 1})
    bus.publish("b", {"n": 2})
    first = bus.consume_one(timeout_seconds=2)
    second = bus.consume_one(timeout_seconds=2)
    bus.send_to_dlq(first, error="poison a")
    bus.send_to_dlq(second, error="poison b")
    assert bus.dlq_length() == 2

    requeued = bus.requeue_from_dlq(first["event_id"])

    assert requeued is True
    assert bus.dlq_length() == 1
    remaining = bus.peek_dlq()
    assert remaining[0]["event_id"] == second["event_id"]
