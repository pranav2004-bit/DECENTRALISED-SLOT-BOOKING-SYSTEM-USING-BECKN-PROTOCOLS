"""Phase 4.4 (livetracker2.md §4.4): fans a real `Slot` state change out to every browser
watching that resource's live availability dashboard (`core/consumers.py`'s
`ResourceAvailabilityConsumer`).

`broadcast_slot_update()` itself stays framework-agnostic-adjacent (no `reservation.py`
dependency) and is called from two places: directly by `test_realtime_availability.py`'s
own infra-level tests, and by `broadcast_slot_update_consumer` below, the sole real trigger
path since the 2026-08-02 cutover (livetracker4.md §2.1) — BPP's service-layer files
(`select_service.py`/`cancel_service.py`/`update_service.py`) no longer call it inline;
they publish a `SlotEvent` (via `shared/inventory_core/reservation.py`) that this consumer
reacts to instead, run by the real `run_event_worker` process.
"""

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer


def broadcast_slot_update(resource_id, slot) -> None:
    layer = get_channel_layer()
    if layer is None:
        return
    async_to_sync(layer.group_send)(
        f"resource-{resource_id}-availability",
        {
            "type": "slot.update",
            "slot": {
                "id": str(slot.id),
                "status": slot.status,
                "capacity_remaining": slot.capacity_remaining,
                "capacity_total": slot.capacity_total,
                "start_time": slot.start_time.isoformat(),
                "end_time": slot.end_time.isoformat(),
            },
        },
    )


def broadcast_slot_update_consumer(event: dict) -> None:
    """Real, queue-driven counterpart to the direct `broadcast_slot_update()` calls
    `select_service.py`/`cancel_service.py`/`update_service.py` used to make inline —
    the sole trigger path as of the 2026-08-02 cutover (livetracker4.md §2.1) — reacts
    to any real `SlotEvent` carrying a `slot_id`. Refetches the real, current `Slot` row
    rather than trusting a payload snapshot: this worker runs detached from the
    original request that published the event, so a fresh read is the only
    trustworthy source for the slot's live state, not a staleness risk to work
    around. A `slot_id` for an already-deleted `Slot` (never happens today — slots
    are never hard-deleted — but a defensive, honest no-op regardless) is skipped,
    not an error."""
    from inventory_core.models import Slot

    payload = event.get("payload") or {}
    slot_id = payload.get("slot_id")
    if not slot_id:
        return
    slot = Slot.objects.filter(pk=slot_id).first()
    if slot is None:
        return
    broadcast_slot_update(slot.resource_id, slot)
