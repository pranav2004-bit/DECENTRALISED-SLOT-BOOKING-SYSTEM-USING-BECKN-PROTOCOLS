"""Internal Event Infrastructure — wraps the shared EventBus, configured for BPP,
per BPP_details_v1.1.md §7 (Domain Events / Event-Driven Architecture for
asynchronous internal communication between business modules).
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
