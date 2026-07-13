"""Internal Event Infrastructure — wraps the shared EventBus (Redis-backed, with
DLQ), configured for BAP, per BAP_details_v1.1.md §9 "Internal Architecture Style:
Event-Driven Architecture (EDA)". Real domain events (buyer onboarding, discovery,
transaction workflow) get published/consumed here starting in Phase 2+; this
establishes the wired, tested infrastructure now.
"""

from django.conf import settings
from event_bus import EventBus

_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    global _bus
    if _bus is None:
        _bus = EventBus(
            redis_url=settings.EVENT_BUS_URL,
            queue_name=settings.EVENT_BUS_QUEUE_NAME,
            dlq_name=settings.EVENT_BUS_DLQ_NAME,
        )
    return _bus
