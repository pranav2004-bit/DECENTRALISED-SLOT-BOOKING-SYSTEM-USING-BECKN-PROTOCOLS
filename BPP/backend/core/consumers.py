"""Phase 4.4 (livetracker2.md §4.4): the real live-inventory-push feature, built on top of
§2.4's transport-only WebSocket foundation (`shared/realtime/consumers.py`'s
`FoundationConsumer`). Deliberately BPP-only, not added to that shared consumer: `Slot` data
lives exclusively in BPP's own database, and the real Beckn protocol is request/callback based,
not push-based — a business's own live availability dashboard (owner or the one assigned staff
member, per Phase 4.3's access model) is a genuine, honest in-app feature; inventing a push
channel to BAP customers over a wire the protocol never specifies would not be.
"""

import json

from channels.auth import get_user
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.contrib.auth import get_user_model
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
        authorization/locking/broadcast path.

        livetracker3.md §8.1's own fourth post-close audit: a real gap found and closed,
        made concretely exploitable (not just theoretical) by this phase's own new
        unassign capability. `connect()` only ever checks access *once*, when the socket
        first opens — for a connection that stays open, `receive()` never re-checked it,
        so a staff account unassigned (or reassigned away) mid-connection kept the
        ability to block slots on a resource it no longer had access to, for as long as
        it stayed connected. Every REST endpoint re-checks on every single call; this
        socket's own `block_slot` path is the one place that didn't. Re-validating here,
        closing with the same `4403` `connect()` already uses on failure, brings it to
        parity with the REST path instead of trusting a connect-time snapshot forever.

        Tenth post-close audit, prompted by the ninth's own new password-reset feature:
        re-checking `_resource_accessible_to()` alone still trusted `self.scope["user"]`
        — the *credential* snapshot Channels' `AuthMiddlewareStack` resolved once, at
        `connect()` time, not the resource-assignment one. An owner resetting a staff
        account's password (or deactivating it, which Django's own `authenticate()`
        already refuses at the *next* login for) does nothing to a socket connection
        already open under the *old* credentials — this method's own re-fetch of
        `self.scope["user"]` was still the stale object either way. `channels.auth.
        get_user(scope)` is the exact function `AuthMiddlewareStack` itself calls at
        connect time — re-calling it here re-verifies the session's own stored auth
        hash against the user's *current* password hash (`AbstractBaseUser`'s own
        built-in mechanism, the same one Django's plain HTTP `get_user()` already
        relies on for this project's REST endpoints for free), closing with `4401`
        (unauthenticated — the session itself is now stale, not merely unauthorized)
        if a password reset invalidated it since `connect()`."""
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

        user = await get_user(self.scope)
        if not user.is_authenticated:
            await self.close(code=4401)
            return
        if not await self._user_can_access_resource(user):
            await self.close(code=4403)
            return

        blocked, skipped = await self._block_slot(slot_id)
        await self.send(
            text_data=json.dumps({"type": "block_result", "blocked": blocked, "skipped": skipped})
        )

    async def slot_update(self, event):
        """livetracker3.md §8.1's own fifth post-close audit: the read-side twin of the
        `receive()` fix above — this handler forwarded every broadcast to a connected
        client unconditionally, with no re-check, so an unassigned staff account kept
        *watching* a resource's live booking activity (not just retaining the ability
        to mutate it) for as long as its connection happened to stay open. Same fix
        shape: re-validate before forwarding, close instead of silently dropping if
        access no longer holds — a revoked connection shouldn't linger in the group
        either. Tenth post-close audit: also re-verifies the session's own credential
        (`channels.auth.get_user`, same reasoning as `receive()`'s own updated
        docstring above) rather than just re-checking resource assignment against a
        stale `scope["user"]` snapshot."""
        user = await get_user(self.scope)
        if not user.is_authenticated:
            await self.close(code=4401)
            return
        if not await self._user_can_access_resource(user):
            await self.close(code=4403)
            return
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


class BusinessOrdersConsumer(AsyncWebsocketConsumer):
    """livetracker6.md §2.2: one connection per business Orders dashboard.

    Distinct from `ResourceAvailabilityConsumer` above by design, not oversight:
    that consumer is hard-coded to exactly one `resource_id` from its own URL
    route and can't join a variable number of groups. This one has no
    `resource_id` in its route at all — on connect it resolves the real, current
    set of resources the authenticated account can access (every owned resource
    for an owner, the one assigned resource for staff — the same
    `_resource_accessible_to`-underlying access model Phase 4.3 already
    established, not a second, independently-invented permission rule) and joins
    exactly those resources' own `resource-{resource_id}-orders` groups.

    Security discipline inherited from this exact file's own real incident
    history (`livetracker3.md` §8.1's fourth/fifth/tenth post-close audits), not
    left to be relearned: every forwarded broadcast re-validates access and
    re-verifies the session's own credential via `channels.auth.get_user`,
    rather than ever trusting a connect-time snapshot for the life of the
    connection."""

    async def connect(self):
        user = self.scope.get("user")
        if user is None or not user.is_authenticated:
            await self.close(code=4401)
            return
        self.group_names = await self._resolve_order_groups(user)
        for group_name in self.group_names:
            await self.channel_layer.group_add(group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        for group_name in getattr(self, "group_names", []):
            await self.channel_layer.group_discard(group_name, self.channel_name)

    async def order_confirmed(self, event):
        """Re-validates before forwarding — a resource unassigned/reassigned mid-
        connection (staff) or no longer owned (can't happen for an owner today,
        but checked the same way regardless, not assumed impossible) must not
        keep receiving that resource's own broadcasts, closing this exact gap
        from day one instead of shipping it and fixing it as a live incident
        the way `ResourceAvailabilityConsumer` originally did."""
        user = await get_user(self.scope)
        if not user.is_authenticated:
            await self.close(code=4401)
            return
        resource_id = event["order"]["resource_id"]
        group_name = f"resource-{resource_id}-orders"
        if not await self._user_can_access_resource_id(user, resource_id):
            await self.channel_layer.group_discard(group_name, self.channel_name)
            if group_name in self.group_names:
                self.group_names.remove(group_name)
            return
        await self.send(text_data=json.dumps({"type": "order.confirmed", "order": event["order"]}))

    @database_sync_to_async
    def _resolve_order_groups(self, user) -> list[str]:
        BusinessAccount = get_user_model()
        if user.role == BusinessAccount.Role.OWNER:
            resource_ids = Resource.objects.filter(owner_ref=str(user.id)).values_list(
                "id", flat=True
            )
        else:
            resource_ids = Resource.objects.filter(
                owner_ref=str(user.managed_by_id), domain_data__assigned_staff_id=str(user.id)
            ).values_list("id", flat=True)
        return [f"resource-{rid}-orders" for rid in resource_ids]

    @database_sync_to_async
    def _user_can_access_resource_id(self, user, resource_id) -> bool:
        try:
            resource = Resource.objects.get(id=resource_id)
        except (Resource.DoesNotExist, DjangoValidationError, ValueError):
            return False
        return _resource_accessible_to(user, resource)
