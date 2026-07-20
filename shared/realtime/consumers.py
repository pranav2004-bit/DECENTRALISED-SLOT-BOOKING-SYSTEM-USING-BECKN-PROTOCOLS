"""Minimal WebSocket foundation consumer (livetracker2.md §2.4) — proves the real-time
transport itself works (a connection opens, stays alive, and responds to a heartbeat), with
deliberately no business logic on top. Both BAP_details_v1.1.md and BPP_details_v1.1.md's
Communication Mechanism tables document WebSockets as part of the *standing* Web App <->
Backend channel (alongside HTTP/HTTPS), not a Phase-4-only add-on — Phase 4.4 builds the real
live-inventory-push feature on top of this connection once Phase 3's core booking flow is
proven; this consumer is only ever the transport, not a preview of that feature.

Shared between BAP and BPP (both real, working Django Channels consumers imported identically)
because backend `shared/` package-sharing is an already-established, working pattern here —
unlike the frontend, where introducing new JS-package sharing would be new infrastructure (see
docs/adr/0004-web-ui-duplicated-not-shared-package.md).
"""

import json

from channels.generic.websocket import AsyncWebsocketConsumer


class FoundationConsumer(AsyncWebsocketConsumer):
    """Accepts any connection (no auth gate yet — this is transport-proof only, not a real
    channel carrying customer/business data), sends a `connected` acknowledgment, and replies
    to a `ping` message with `pong` so a real round-trip over the socket is observable, not
    just a one-way accept."""

    async def connect(self):
        await self.accept()
        await self.send(text_data=json.dumps({"type": "connected"}))

    async def receive(self, text_data=None, bytes_data=None):
        if text_data is None:
            return
        try:
            message = json.loads(text_data)
        except json.JSONDecodeError:
            return
        if message.get("type") == "ping":
            await self.send(text_data=json.dumps({"type": "pong"}))

    async def disconnect(self, close_code):
        pass
