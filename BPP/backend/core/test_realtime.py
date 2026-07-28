"""Phase 2.4 Test Gate (livetracker2.md §2.4) for BPP's WebSocket foundation — mirrors
BAP/backend/core/test_realtime.py (same shared consumer, `shared/realtime/consumers.py`),
proving BPP's own `asgi.py` routing wires it up correctly too, not just the shared logic once.

`@pytest.mark.django_db` on every test here (added at Phase 4.4, livetracker2.md §4.4):
`FoundationConsumer` itself never touches the DB, but Channels' own base
`AsyncConsumer.dispatch()` unconditionally calls `close_old_connections()` on *every*
message including disconnect, for *every* consumer — real DB access pytest-django must
be told to allow, not a change these tests actually need functionally. Became load-bearing
once Phase 4.4 added this project's first DB-touching async Channels tests
(`test_realtime_availability.py`): a real connection opened on one of `asgiref`'s shared
worker-pool threads there can still be attached (Django's connection handling is
thread-local) the next time that same OS thread is reused for one of *these* tests' own
dispatch — tripping the guard for a test that was never the one that opened it.
"""

import json

import pytest
from channels.testing import WebsocketCommunicator

from bpp.asgi import application


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_websocket_connects_and_sends_connected_ack():
    communicator = WebsocketCommunicator(application, "/ws/")
    connected, _ = await communicator.connect()
    assert connected is True

    message = json.loads(await communicator.receive_from())
    assert message == {"type": "connected"}

    await communicator.disconnect()


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_websocket_round_trips_a_ping_pong():
    communicator = WebsocketCommunicator(application, "/ws/")
    await communicator.connect()
    await communicator.receive_from()  # the initial "connected" ack

    await communicator.send_to(text_data=json.dumps({"type": "ping"}))
    message = json.loads(await communicator.receive_from())

    assert message == {"type": "pong"}
    await communicator.disconnect()


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_websocket_ignores_malformed_input_instead_of_crashing():
    communicator = WebsocketCommunicator(application, "/ws/")
    await communicator.connect()
    await communicator.receive_from()  # the initial "connected" ack

    await communicator.send_to(text_data="not valid json")

    assert await communicator.receive_nothing() is True
    await communicator.disconnect()
