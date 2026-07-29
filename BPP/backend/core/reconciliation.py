"""Real, periodic background reconciliation loop (livetracker2.md §3.11) — started once at
Django startup (`apps.py`'s `ready()`), not a request-time check and not a new task-queue
dependency (no Celery/beat exists anywhere in this project; see `inventory_core.reservation`'s
own module docstring for why). A single daemon thread, sleeping `settings.RECONCILIATION_
INTERVAL_SECONDS` between runs, calling the three real sweeps this project's audits identified:

1. `inventory_core.reconciliation.sweep_expired_holds()` — releases any `HELD` booking whose
   Redis TTL hold has lapsed but nothing has opportunistically touched since (the safety net
   for `confirm_hold()`'s own on-touch release, for holds nobody ever touches again).
2. `catalog_cache.reconcile_catalog_cache(domain_code)` — rebuilds each registered domain's
   catalog fresh and corrects its cached entry immediately if it's drifted, instead of
   passively waiting up to the cache's own TTL. §4.1: sweeps every domain currently registered
   with `inventory_core.domain_adapter` (`registered_domains()`), not just Beauty — a Healthcare
   catalog left uncorrected until its own TTL would be the same gap this sweep exists to close
   for Beauty.
3. `inventory_core.reconciliation.sweep_completed_bookings()` (§4.5) — completes any `ACTIVE`
   booking whose slot's fulfillment window has already ended, the real trigger `Booking.Status.
   COMPLETE` otherwise never reaches in production (needed so rating/support have a genuinely
   completed booking to test against, not a hand-set status in a fixture).

One failure in any sweep is logged and does not kill the loop — a transient DB/Redis hiccup
on one tick should not silently end reconciliation for the rest of the process's lifetime.
"""

import logging
import threading
import time

import redis
from django.conf import settings
from inventory_core.domain_adapter import registered_domains
from inventory_core.reconciliation import sweep_completed_bookings, sweep_expired_holds

from .catalog_cache import reconcile_catalog_cache
from .events import get_event_bus

logger = logging.getLogger("bpp")

_started = False
_started_lock = threading.Lock()


def _run_once() -> None:
    redis_client = redis.Redis.from_url(
        settings.REDIS_URL, socket_connect_timeout=0.5, socket_timeout=0.5
    )
    event_bus = get_event_bus()

    try:
        released = sweep_expired_holds(redis_client=redis_client, event_bus=event_bus)
        if released:
            logger.info("reconciliation: released %d expired HELD booking(s)", released)
    except Exception:
        logger.exception("reconciliation: sweep_expired_holds failed")

    try:
        completed = sweep_completed_bookings(event_bus=event_bus)
        if completed:
            logger.info("reconciliation: completed %d ACTIVE booking(s)", completed)
    except Exception:
        logger.exception("reconciliation: sweep_completed_bookings failed")

    for domain_code in registered_domains():
        try:
            corrected = reconcile_catalog_cache(domain_code)
            if corrected:
                logger.info(
                    "reconciliation: corrected a drifted/missing %s catalog cache entry",
                    domain_code,
                )
        except Exception:
            logger.exception("reconciliation: reconcile_catalog_cache(%s) failed", domain_code)


def _loop() -> None:
    while True:
        time.sleep(settings.RECONCILIATION_INTERVAL_SECONDS)
        _run_once()


def start_reconciliation_loop() -> None:
    """Idempotent (guarded by `_started`) — daphne is single-process for this project's
    Dockerfile (confirmed directly, no `--workers` flag, unlike Registry/Gateway's gunicorn),
    so `ready()` only ever runs once per real process; the guard exists for defense-in-depth
    (e.g. a future deployment change) rather than a known double-invocation today. Skipped
    entirely under `settings.TESTING` — the test suite drives both sweeps directly and
    explicitly, a real background thread firing mid-test-run would be non-deterministic noise,
    not a real safety net for a test process that doesn't stay up."""
    global _started
    if getattr(settings, "TESTING", False):
        return
    with _started_lock:
        if _started:
            return
        _started = True
    thread = threading.Thread(target=_loop, daemon=True, name="bpp-reconciliation")
    thread.start()
