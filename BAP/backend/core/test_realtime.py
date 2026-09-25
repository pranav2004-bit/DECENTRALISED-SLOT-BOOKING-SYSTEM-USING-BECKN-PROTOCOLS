"""Phase 2.4 Test Gate (livetracker2.md §2.4) for BAP's WebSocket foundation — a real
connection opens, stays alive, and round-trips a message. `channels.testing.
WebsocketCommunicator` drives the real ASGI application in-process (a real protocol
handshake and consumer lifecycle, not a mock) — the live, real-network confirmation ("inspecting
actual socket traffic") is done separately via a running container, not by this test suite.

**Correction (livetracker5.md Phase 3.3, 2026-09-25):** these tests import the real
`bap.asgi.application`, which routes every connection — including `/ws/`'s own
`FoundationConsumer` — through `AuthMiddlewareStack`, and that middleware always
resolves a session via a real DB query regardless of which consumer ultimately
handles the connection. These tests had no `django_db` mark at all before, a real
latent gap only exposed once a second async, DB-marked test file
(`test_realtime_payment.py`, Phase 3.3's own new tests) started running in the same
session — `transaction=True` specifically, not the plain `django_db` fixture, since
Channels' `database_sync_to_async` runs the session lookup on a separate thread with
its own DB connection, which the default savepoint-wrapped fixture doesn't extend
to (the exact same reasoning BPP's own `test_realtime_availability.py` already
documents for this exact scenario).
"""

import json

import pytest
from channels.testing import WebsocketCommunicator

from bap.asgi import application


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_websocket_connects_and_sends_connected_ack():
    communicator = WebsocketCommunicator(application, "/ws/")
    connected, _ = await communicator.connect()
    assert connected is True

    message = json.loads(await communicator.receive_from())
    assert message == {"type": "connected"}

    await communicator.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_websocket_round_trips_a_ping_pong():
    communicator = WebsocketCommunicator(application, "/ws/")
    await communicator.connect()
    await communicator.receive_from()  # the initial "connected" ack

    await communicator.send_to(text_data=json.dumps({"type": "ping"}))
    message = json.loads(await communicator.receive_from())

    assert message == {"type": "pong"}
    await communicator.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_websocket_ignores_malformed_input_instead_of_crashing():
    communicator = WebsocketCommunicator(application, "/ws/")
    await communicator.connect()
    await communicator.receive_from()  # the initial "connected" ack

    await communicator.send_to(text_data="not valid json")

    assert await communicator.receive_nothing() is True
    await communicator.disconnect()
