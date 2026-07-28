"""Phase 4.4 (livetracker2.md §4.4): fans a real `Slot` state change out to every browser
watching that resource's live availability dashboard (`core/consumers.py`'s
`ResourceAvailabilityConsumer`).

Called directly from BPP's own service-layer files after each real Slot mutation in
`shared/inventory_core/reservation.py` returns — that library stays framework-agnostic (no
Django Channels import there, same decoupling already used for `owner_ref`), so the broadcast
is this app's own side effect layered on top, mirroring how BPP already wraps `event_bus`/
catalog-cache-invalidation calls around `reservation.py`'s return value rather than baking them
into the domain-agnostic library itself.
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
