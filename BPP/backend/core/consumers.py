"""Phase 4.4 (livetracker2.md §4.4): the real live-inventory-push feature, built on top of
§2.4's transport-only WebSocket foundation (`shared/realtime/consumers.py`'s
`FoundationConsumer`). Deliberately BPP-only, not added to that shared consumer: `Slot` data
lives exclusively in BPP's own database, and the real Beckn protocol is request/callback based,
not push-based — a business's own live availability dashboard (owner or the one assigned staff
member, per Phase 4.3's access model) is a genuine, honest in-app feature; inventing a push
channel to BAP customers over a wire the protocol never specifies would not be.
"""

import json

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.core.exceptions import ValidationError as DjangoValidationError
from inventory_core.models import Resource

from .views import _resource_accessible_to, block_slots


class ResourceAvailabilityConsumer(AsyncWebsocketConsumer):
    """One connection per resource-availability dashboard. Joins
    `resource-{resource_id}-availability` only after confirming the connecting
    `BusinessAccount` (owner or assigned staff, reusing Phase 4.3's own `_resource_accessible_to`
    check) actually has access — the same IDOR posture as the equivalent REST endpoints, just
    enforced at connect time instead of per-request."""

    async def connect(self):
        self.resource_id = self.scope["url_route"]["kwargs"]["resource_id"]
        self.group_name = f"resource-{self.resource_id}-availability"

        user = self.scope.get("user")
        if user is None or not user.is_authenticated:
            await self.close(code=4401)
            return
        if not await self._user_can_access_resource(user):
            await self.close(code=4403)
            return

        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive(self, text_data=None, bytes_data=None):
        """livetracker2.md §4.4: "enough client → server signal to support an interactive
        booking screen (not just a one-way dashboard feed)" — a connected owner/staff member
        can block off a slot directly from the live dashboard, over this same socket, instead
        of only ever watching it. Reuses `views.block_slots` (the same code Phase 4.3's REST
        endpoint calls) rather than a parallel implementation, so both entry points share one
        authorization/locking/broadcast path."""
        if text_data is None:
            return
        try:
            message = json.loads(text_data)
        except json.JSONDecodeError:
            return
        if message.get("type") != "block_slot":
            return
        slot_id = message.get("slot_id")
        if not slot_id:
            return

        blocked, skipped = await self._block_slot(slot_id)
        await self.send(
            text_data=json.dumps({"type": "block_result", "blocked": blocked, "skipped": skipped})
        )

    async def slot_update(self, event):
        await self.send(text_data=json.dumps({"type": "slot.update", "slot": event["slot"]}))

    @database_sync_to_async
    def _user_can_access_resource(self, user) -> bool:
        try:
            resource = Resource.objects.get(id=self.resource_id)
        except (Resource.DoesNotExist, DjangoValidationError, ValueError):
            return False
        return _resource_accessible_to(user, resource)

    @database_sync_to_async
    def _block_slot(self, slot_id):
        resource = Resource.objects.get(id=self.resource_id)
        return block_slots(resource, [slot_id])
