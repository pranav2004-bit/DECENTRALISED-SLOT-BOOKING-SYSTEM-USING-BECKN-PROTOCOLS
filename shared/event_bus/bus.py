"""Minimal, real internal event bus backed by Redis lists, with a Dead Letter Queue
for events that fail processing — per livetracker1.md Phase 1.3/1.4 "Internal Event
Infrastructure (EDA bus) with a Dead Letter Queue" requirement.

Deliberately not Celery/Kafka/RabbitMQ — right-sized for foundation-stage internal
EDA between business modules within one app (BAP_details_v1.1.md §9,
BPP_details_v1.1.md §7), not a distributed message broker. Revisit only if real
throughput/durability needs outgrow this.
"""

import json
import uuid
from datetime import datetime, timezone

import redis


class EventBus:
    def __init__(self, *, redis_url: str, queue_name: str, dlq_name: str):
        self._redis = redis.from_url(redis_url, decode_responses=True)
        self.queue_name = queue_name
        self.dlq_name = dlq_name
        # livetracker4.md §2.1: the reliable-delivery half of consume_one() below —
        # a per-queue "in flight" list an event sits in between being popped and
        # being genuinely finished (ack()'d or DLQ'd), so a worker crash mid-
        # processing doesn't silently lose it. Named off queue_name so multiple
        # apps' own EventBus instances (BAP/BPP, each with a distinct queue_name)
        # never collide on the same Redis key.
        self.processing_queue_name = f"{queue_name}:processing"
        self._in_flight: dict[str, str] = {}

    def publish(self, event_type: str, payload: dict) -> str:
        event_id = str(uuid.uuid4())
        event = {
            "event_id": event_id,
            "event_type": event_type,
            "payload": payload,
            "published_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        }
        self._redis.rpush(self.queue_name, json.dumps(event))
        return event_id

    def consume_one(self, *, timeout_seconds: float = 1.0) -> dict | None:
        """Reliable pop, or None if timeout elapses with nothing queued.

        **Real gap found and fixed (livetracker4.md §2.1), before this bus had any
        real consumer to expose it live:** the original implementation here was a
        bare `BLPOP` — an atomic delete-on-pop with no way to recover the event if
        the process popping it crashed before finishing. `ProcessedEvent`'s own
        docstring already claims "At-Least-Once delivery is the only realistic
        guarantee shared/event_bus provides," but `BLPOP` alone only delivers
        at-most-once against a crash between pop and process — a real, previously
        unexercised gap (nothing had ever consumed from this bus outside its own
        test suite until this phase). Fixed with the standard reliable-queue
        pattern: atomically moves the item into `processing_queue_name` instead of
        deleting it outright.

        **Second, more serious real bug found live (not in code review): the first
        fix here used `BRPOPLPUSH`, which broke FIFO ordering.** `publish()` uses
        `RPUSH` (append to the list's right/tail) — the original `BLPOP` correctly
        popped from the *left*/head, the standard RPUSH+BLPOP queue pattern.
        `BRPOPLPUSH` pops from the *right* — the same end `RPUSH` pushes to,
        making the pair a LIFO stack, not a FIFO queue. This silently reversed
        delivery order for every real consumer, not just a test artifact — caught
        live by `test_events_for_same_slot_are_processed_in_publish_order`
        failing consistently and reproducibly (first misdiagnosed as test-isolation
        noise from concurrent subprocess-spawning tests before being root-caused
        properly by reproducing it with just two specific tests in isolation).
        Fixed with `BLMOVE(..., "LEFT", "RIGHT")` — pops from the left, restoring
        the original FIFO order, while still atomically landing in
        `processing_queue_name` for reliable delivery. The caller must call
        `ack()` (directly, or via `send_to_dlq()`) once processing is genuinely
        finished to remove it from there; `recover_orphaned()` is what reclaims
        anything left behind by a crashed predecessor, meant to run once at
        worker startup before the main loop."""
        raw = self._redis.blmove(
            self.queue_name, self.processing_queue_name, timeout_seconds, "LEFT", "RIGHT"
        )
        if raw is None:
            return None
        event = json.loads(raw)
        self._in_flight[event["event_id"]] = raw
        return event

    def ack(self, event: dict) -> None:
        """Marks an event `consume_one()` returned as fully, successfully done —
        removes it from the processing list. Every event `consume_one()` returns
        must eventually reach either this or `send_to_dlq()` (which calls this
        itself), or it stays "in flight" until the next `recover_orphaned()` call
        requeues it for reprocessing."""
        raw = self._in_flight.pop(event.get("event_id"), None)
        if raw is not None:
            self._redis.lrem(self.processing_queue_name, 1, raw)

    def send_to_dlq(self, event: dict, *, error: str) -> None:
        """Called by the consumer when processing an event fails — moves it to the
        DLQ instead of silently dropping it, per the resilience requirement that no
        internal event is lost on failure. Also acks it — landing in the DLQ is
        itself a terminal outcome, so it must leave the processing list too, or
        `recover_orphaned()` would requeue an already-DLQ'd event for a second,
        pointless attempt."""
        dlq_entry = {
            **event,
            "failed_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "error": error,
        }
        self._redis.rpush(self.dlq_name, json.dumps(dlq_entry))
        self.ack(event)

    def recover_orphaned(self) -> int:
        """Requeues anything left in this queue's own processing list back onto the
        main queue's front (so it's the next thing consumed, not stuck behind
        everything published since the crash) — meant to run once, at worker
        startup, before entering the main consume loop, recovering events whose
        prior consumer popped them via `consume_one()` but crashed before calling
        `ack()`/`send_to_dlq()`. Safe under this bus's own documented single-
        consumer-per-queue invariant (`events.py`'s own per-entity-ordering
        docstring) — only ever this worker's own now-dead predecessor could have
        left anything here. Returns the number of events recovered."""
        count = 0
        while self._redis.rpoplpush(self.processing_queue_name, self.queue_name):
            count += 1
        return count

    def dlq_length(self) -> int:
        return self._redis.llen(self.dlq_name)

    def peek_dlq(self, *, count: int = 10) -> list[dict]:
        raw_events = self._redis.lrange(self.dlq_name, 0, count - 1)
        return [json.loads(e) for e in raw_events]

    def requeue_from_dlq(self, event_id: str) -> bool:
        """Finds the poisoned event matching `event_id` in the DLQ, removes it from
        there, and re-publishes it to the main queue as a genuinely new event (fresh
        `event_id`, same `event_type`/payload) — the minimal, deliberately manual DLQ
        reprocessing tool this project didn't have before (livetracker4.md §2.1's own
        gap audit). Returns `True` if a matching entry was found and requeued, `False`
        if no DLQ entry has that `event_id` (already handled elsewhere, or a typo).

        Deliberately not automatic/scheduled — a poisoned event by definition already
        failed once for a real reason; blindly auto-retrying forever is exactly the
        failure mode a DLQ exists to prevent (matching this project's own Test Gate
        wording: "lands in the DLQ instead of retrying forever"). A human (or a
        deliberate, separate follow-up decision) is meant to look at *why* first."""
        raw_events = self._redis.lrange(self.dlq_name, 0, -1)
        for raw in raw_events:
            entry = json.loads(raw)
            if entry.get("event_id") != event_id:
                continue
            if self._redis.lrem(self.dlq_name, 1, raw) == 0:
                return False  # a concurrent requeue/inspection already removed it
            self.publish(entry["event_type"], entry["payload"])
            return True
        return False

    def queue_length(self) -> int:
        return self._redis.llen(self.queue_name)

    @property
    def heartbeat_key(self) -> str:
        return f"{self.queue_name}:worker-heartbeat"

    def write_heartbeat(self, *, ttl_seconds: float = 10.0) -> None:
        """Called by a running worker once per loop iteration (see
        `shared/event_bus/worker.py`'s `on_heartbeat` hook) — a real, cheap
        liveness signal this project had no way to observe before
        (livetracker4.md §2.1's own gap audit: nothing paged/alerted if the
        worker process silently died). Short TTL means a genuinely dead/hung
        worker's key naturally expires within `ttl_seconds`, no explicit
        "worker is down" event required.

        Real bug found live (2026-08-02): redis-py's `set(ex=...)` only
        accepts `int`/`timedelta`, not `float` — `ttl_seconds` is typed as
        `float` for test-friendly sub-second TTLs (see
        test_write_heartbeat_makes_the_worker_alive_until_it_expires), so this
        uses `px` (milliseconds) instead of `ex` (whole seconds only)."""
        self._redis.set(
            self.heartbeat_key, datetime.now(timezone.utc).isoformat(), px=int(ttl_seconds * 1000)
        )

    def worker_is_alive(self) -> bool:
        return bool(self._redis.exists(self.heartbeat_key))


def process_with_dlq(bus: EventBus, event: dict, handler) -> bool:
    """Runs `handler(event)`; on any exception, sends the event to the DLQ instead
    of raising, and returns False. On success, acks the event (removes it from the
    reliable-delivery processing list — see `consume_one()`). Returns True on
    success. This is the standard consume-loop wrapper every consumer should use."""
    try:
        handler(event)
        bus.ack(event)
        return True
    except Exception as exc:  # noqa: BLE001 — deliberately broad: any handler failure must route to DLQ, not crash the consumer
        bus.send_to_dlq(event, error=f"{type(exc).__name__}: {exc}")
        return False
