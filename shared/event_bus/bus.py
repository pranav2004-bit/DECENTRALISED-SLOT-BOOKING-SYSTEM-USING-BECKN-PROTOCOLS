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
        """Blocking pop of a single event, or None if timeout elapses with nothing
        queued."""
        result = self._redis.blpop([self.queue_name], timeout=timeout_seconds)
        if result is None:
            return None
        _, raw = result
        return json.loads(raw)

    def send_to_dlq(self, event: dict, *, error: str) -> None:
        """Called by the consumer when processing an event fails — moves it to the
        DLQ instead of silently dropping it, per the resilience requirement that no
        internal event is lost on failure."""
        dlq_entry = {
            **event,
            "failed_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "error": error,
        }
        self._redis.rpush(self.dlq_name, json.dumps(dlq_entry))

    def dlq_length(self) -> int:
        return self._redis.llen(self.dlq_name)

    def peek_dlq(self, *, count: int = 10) -> list[dict]:
        raw_events = self._redis.lrange(self.dlq_name, 0, count - 1)
        return [json.loads(e) for e in raw_events]

    def queue_length(self) -> int:
        return self._redis.llen(self.queue_name)


def process_with_dlq(bus: EventBus, event: dict, handler) -> bool:
    """Runs `handler(event)`; on any exception, sends the event to the DLQ instead
    of raising, and returns False. Returns True on success. This is the standard
    consume-loop wrapper every consumer should use."""
    try:
        handler(event)
        return True
    except Exception as exc:  # noqa: BLE001 — deliberately broad: any handler failure must route to DLQ, not crash the consumer
        bus.send_to_dlq(event, error=f"{type(exc).__name__}: {exc}")
        return False
