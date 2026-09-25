"""livetracker5.md Phase 3.3: the real payment-status push feature, built on top of
`shared/realtime/consumers.py`'s transport-only `FoundationConsumer` — mirrors BPP's
own `core/consumers.py::ResourceAvailabilityConsumer` pattern exactly (per-entity
group, IDOR-checked at connect time, a named event-handler method matching the
`group_send` "type" key), the first time BAP itself has needed real business logic
on this transport, not just BPP.
"""

import json

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer

from .session_authz import SessionAccessError, resolve_owned_session


class PaymentStatusConsumer(AsyncWebsocketConsumer):
    """One connection per payment-status page. Joins `payment-{transaction_id}`
    only after confirming the connecting caller can actually see this
    transaction — reuses `resolve_owned_session`, the exact same IDOR check
    every REST trigger/result endpoint in this app already applies, not a
    second, independently-invented rule. A still-anonymous session (no real
    owner) is allowed through, matching this app's established anonymous-
    browsing posture (`session_authz.py`'s own documented contract)."""

    async def connect(self):
        self.transaction_id = self.scope["url_route"]["kwargs"]["transaction_id"]
        self.group_name = f"payment-{self.transaction_id}"

        user = self.scope.get("user")
        customer = user if user is not None and user.is_authenticated else None
        if not await self._can_access(customer):
            await self.close(code=4403)
            return

        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def payment_status_changed(self, event):
        """Handler name must match `broadcast_payment_status_changed()`'s own
        `"type": "payment.status_changed"` (Channels maps `.` to `_` when
        resolving the type string to a method name) — confirmed against the
        exact same convention `ResourceAvailabilityConsumer.slot_update` /
        `BusinessOrdersConsumer.order_confirmed` already establish."""
        await self.send(
            text_data=json.dumps({"type": "payment.status_changed", "status": event["status"]})
        )

    @database_sync_to_async
    def _can_access(self, customer) -> bool:
        try:
            resolve_owned_session(transaction_id=self.transaction_id, requesting_customer=customer)
        except SessionAccessError:
            return False
        return True
