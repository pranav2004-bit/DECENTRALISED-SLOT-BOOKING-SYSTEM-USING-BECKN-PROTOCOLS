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
