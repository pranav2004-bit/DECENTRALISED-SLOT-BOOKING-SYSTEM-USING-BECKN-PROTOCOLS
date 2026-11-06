"""livetracker5.md Phase 3.3 — fans a real payment status change out to whichever
browser is watching that transaction's payment page (`core/consumers.py`'s
`PaymentStatusConsumer`), the moment `payment_service.record_webhook_event` resolves
it. Mirrors BPP's own `core/realtime.py::broadcast_slot_update` pattern exactly (a
plain function, `get_channel_layer()` + `async_to_sync(layer.group_send)`, a graceful
no-op if no layer is configured) — the first time BAP itself has needed this, not
just BPP.
"""

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer


def broadcast_payment_status_changed(*, transaction_id: str, status: str) -> None:
    layer = get_channel_layer()
    if layer is None:
        return
    async_to_sync(layer.group_send)(
        f"payment-{transaction_id}",
        {"type": "payment.status_changed", "status": status},
    )


def broadcast_slot_held(
    *, provider_id: str, item_id: str, start_time: str, expires_at: str
) -> None:
    """Fans a real "this slot just got held" signal out to every browser
    currently watching this provider's own search-results card
    (`core/consumers.py`'s `AvailabilityConsumer`) — fired from
    `select_service.record_on_select_result()` the instant a real successful
    hold lands (the one moment BAP itself genuinely learns a slot changed
    state; BPP's own `broadcast_slot_update` already pushes the same fact to
    *its* business dashboard, this is the customer-facing counterpart).

    `expires_at` (real ISO timestamp, UTC): BPP never calls back into BAP for
    a passive TTL lapse (only for a genuine customer-triggered action), so
    there is no server push for "the hold lapsed" to relay — instead, every
    receiving browser is told exactly when this specific hold is due to
    expire and runs its own countdown off that same real deadline
    (search/page.tsx's own `heldByOthers`), clearing it client-side the
    instant that time passes. This is correct, not a workaround: every
    client — the holder's own page and everyone else's — is already
    counting down against this exact same deadline for their own UI, so
    there is no separate "truth" being approximated here. The one case this
    alone would get wrong (the hold converting into a real, permanent
    booking before this deadline) is handled separately by
    `broadcast_slot_confirmed` below, which overrides it.

    Deliberately success-only: a `SLOT_UNAVAILABLE` outcome means nothing
    changed (whoever already held it still does) — there is no new fact
    worth telling anyone else."""
    layer = get_channel_layer()
    if layer is None:
        return
    async_to_sync(layer.group_send)(
        f"provider-{provider_id}-availability",
        {
            "type": "slot.held",
            "item_id": item_id,
            "start_time": start_time,
            "expires_at": expires_at,
        },
    )


def broadcast_slot_confirmed(*, provider_id: str, item_id: str, start_time: str) -> None:
    """The permanent counterpart to `broadcast_slot_held` above — fired from
    `confirm_service.record_on_confirm_result()` the instant a hold becomes
    a real, final booking. Every browser currently treating this slot as a
    temporary, expiring hold (from the earlier `slot.held` push) must stop
    expecting it back: without this, a slot that got genuinely booked
    *before* its original 45s hold countdown ran out would incorrectly
    un-grey itself client-side the moment that old countdown reached zero,
    letting someone try to book an already-confirmed appointment. No
    `expires_at` here — a confirmed booking never comes back."""
    layer = get_channel_layer()
    if layer is None:
        return
    async_to_sync(layer.group_send)(
        f"provider-{provider_id}-availability",
        {"type": "slot.confirmed", "item_id": item_id, "start_time": start_time},
    )
