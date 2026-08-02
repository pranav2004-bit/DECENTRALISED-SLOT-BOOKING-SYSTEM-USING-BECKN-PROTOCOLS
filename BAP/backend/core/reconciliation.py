"""Real, periodic background reconciliation sweep for BAP (livetracker4.md §2.4)
— closes a real, previously-undocumented asymmetry: BPP has had automatic
reconciliation sweeps since `livetracker2.md` §3.11/§4.5 (expired holds,
catalog-cache drift, completed bookings); BAP had none. If a real `/on_confirm`
callback is ever lost in transit (a network partition, a dropped message — the
exact class of failure `shared/resilient_http`'s circuit breaker and
`shared/event_bus`'s own DLQ machinery exist to catch elsewhere in this
project), a customer today has no way to resync except manually re-triggering
`/status`.

**Real design-audit finding, before implementing — the checklist's own original
"/status" wording doesn't actually work for this specific case.** `/status`
operates on an already-known `order_id` (`session.confirmed_order["id"]`) — but
that's exactly the piece of information a lost `/on_confirm` callback never
delivered. There is no `order_id` to ask BPP about. The real, correct recovery
is to re-trigger `/confirm` itself — safe and idempotent by construction: BPP's
own `confirm_hold()` (`livetracker2.md` §3.4, `protocol_compliance_notes_v1.1.md`
§K) short-circuits an already-genuinely-confirmed booking to a no-op re-confirm
and still sends a fresh, correct `/on_confirm` callback — exactly the
self-healing this sweep needs, reusing an already-proven guarantee rather than
inventing a new one.

**Second real finding, also found before implementing, not discovered as a live
incident:** a naive "session has init_order but no confirmed_order/
confirmed_error" query can't distinguish "a real /confirm was dispatched and its
callback got lost" from "the customer saw the quote and simply never clicked
confirm at all" — both leave those two fields null. Auto-triggering confirm for
the second case would commit/bill a booking nobody asked to confirm, a real,
serious correctness bug, not a cosmetic one. `SearchSession.confirm_triggered_at`
(a new, explicit field, only ever read by this sweep) is what makes the
distinction real rather than assumed.
"""

import logging
import threading
import time

from django.conf import settings
from django.utils import timezone

from . import confirm_service
from .models import SearchSession

logger = logging.getLogger("bap")

_started = False
_started_lock = threading.Lock()


def sweep_stale_confirmations(*, stale_after_seconds: float) -> int:
    """Finds every `SearchSession` where a real `/confirm` was genuinely
    dispatched (`confirm_triggered_at` set) more than `stale_after_seconds` ago,
    and neither a success nor an error ever arrived for it since — the real "a
    confirm was triggered and its callback never arrived" symptom. Re-triggers
    `/confirm` for each, reusing the exact real customer-facing path
    (`confirm_service.trigger_confirm`) with the session's own real `customer`
    (or `None` for a still-anonymous session) — the identical ownership check a
    real customer request would pass, not a privileged bypass. Returns the
    number of sessions resynced. Never raises for an individual session's resync
    failing (e.g. the BPP is genuinely unreachable right now) — logs and keeps
    sweeping the rest, matching BPP's own sweep contracts
    (`shared/inventory_core/reconciliation.py`)."""
    threshold = timezone.now() - timezone.timedelta(seconds=stale_after_seconds)
    stale_sessions = SearchSession.objects.filter(
        confirm_triggered_at__isnull=False,
        confirm_triggered_at__lt=threshold,
        confirmed_order__isnull=True,
        confirmed_error__isnull=True,
    )
    resynced = 0
    for session in stale_sessions:
        try:
            confirm_service.trigger_confirm(
                transaction_id=session.transaction_id, customer=session.customer
            )
            resynced += 1
        except confirm_service.ConfirmError:
            logger.exception(
                "reconciliation: failed to resync stale session %s", session.transaction_id
            )
    return resynced


def _run_once() -> None:
    try:
        resynced = sweep_stale_confirmations(
            stale_after_seconds=settings.RECONCILIATION_STALE_CONFIRM_SECONDS
        )
        if resynced:
            logger.info("reconciliation: resynced %d stale confirmation(s)", resynced)
    except Exception:
        logger.exception("reconciliation: sweep_stale_confirmations failed")


def _loop() -> None:
    while True:
        time.sleep(settings.RECONCILIATION_INTERVAL_SECONDS)
        _run_once()


def start_reconciliation_loop() -> None:
    """Idempotent (guarded by `_started`), skipped entirely under
    `settings.TESTING` — same contract as BPP's own `start_reconciliation_loop()`,
    not a new convention."""
    global _started
    if getattr(settings, "TESTING", False):
        return
    with _started_lock:
        if _started:
            return
        _started = True
    thread = threading.Thread(target=_loop, daemon=True, name="bap-reconciliation")
    thread.start()
