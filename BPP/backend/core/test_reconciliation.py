"""Tests for `core/reconciliation.py`'s `_run_once()` orchestration itself —
the periodic loop that wires `inventory_core`'s own `sweep_expired_holds`/
`sweep_completed_bookings` (each already covered at the pure-function level by
test_inventory_core_booking.py) and `reconcile_catalog_cache` (covered by
test_catalog_cache.py) together. Nothing here re-derives those sweeps' own
domain logic — only `_run_once()`'s own real responsibilities: connection
hygiene and "one sweep's failure must not stop the others."
"""

from unittest.mock import patch

import pytest

from core import reconciliation


@pytest.mark.django_db
def test_run_once_closes_stale_connections_before_sweeping():
    """Regression test for a real, live-found bug (2026-11-04): this loop runs
    on its own background daemon thread, outside Django's request/response
    cycle — the `request_started`/`request_finished` signals that normally
    call `close_old_connections()` around every ordinary view never fire
    here. When the database (Neon, this project's real Postgres host) closes
    this thread's one long-held connection from its side, every subsequent
    query on this thread used to fail forever with "connection already
    closed" — confirmed happening on *every* tick, in all three sweeps below,
    in live production logs before this fix. Asserts the real fix:
    `close_old_connections()` is called, and called *before* any sweep ever
    touches the database."""
    call_order = []
    with (
        patch(
            "core.reconciliation.close_old_connections",
            side_effect=lambda: call_order.append("close"),
        ),
        patch(
            "core.reconciliation.sweep_expired_holds",
            side_effect=lambda **kwargs: call_order.append("sweep_expired_holds") or 0,
        ),
        patch(
            "core.reconciliation.sweep_completed_bookings",
            side_effect=lambda **kwargs: call_order.append("sweep_completed_bookings") or 0,
        ),
        patch("core.reconciliation.registered_domains", return_value=[]),
    ):
        reconciliation._run_once()

    assert call_order == ["close", "sweep_expired_holds", "sweep_completed_bookings"]


@pytest.mark.django_db
def test_run_once_still_runs_the_other_sweeps_after_one_raises():
    """The module's own documented contract (see reconciliation.py's own
    docstring: "One failure in any sweep is logged and does not kill the
    loop") — a transient DB/Redis hiccup on one sweep must not silently skip
    the rest for the remainder of this tick."""
    with (
        patch("core.reconciliation.close_old_connections"),
        patch("core.reconciliation.sweep_expired_holds", side_effect=RuntimeError("boom")),
        patch("core.reconciliation.sweep_completed_bookings") as mock_completed,
        patch("core.reconciliation.registered_domains", return_value=["ONDC:RET13"]),
        patch("core.reconciliation.reconcile_catalog_cache") as mock_reconcile,
    ):
        reconciliation._run_once()  # must not raise

    mock_completed.assert_called_once()
    mock_reconcile.assert_called_once_with("ONDC:RET13")


@pytest.mark.django_db
def test_run_once_sweeps_every_registered_domain_even_if_one_fails():
    """§4.1's own multi-domain widening — a drifted Healthcare catalog cache
    left uncorrected because Beauty's own reconcile raised first would be the
    same gap this sweep exists to close, just moved one domain over."""
    with (
        patch("core.reconciliation.close_old_connections"),
        patch("core.reconciliation.sweep_expired_holds", return_value=0),
        patch("core.reconciliation.sweep_completed_bookings", return_value=0),
        patch(
            "core.reconciliation.registered_domains",
            return_value=["ONDC:RET13", "ONDC:SRV13", "BECKN:AUTO01"],
        ),
        patch(
            "core.reconciliation.reconcile_catalog_cache",
            side_effect=[RuntimeError("boom"), True, False],
        ) as mock_reconcile,
    ):
        reconciliation._run_once()  # must not raise

    assert mock_reconcile.call_count == 3
    mock_reconcile.assert_any_call("ONDC:RET13")
    mock_reconcile.assert_any_call("ONDC:SRV13")
    mock_reconcile.assert_any_call("BECKN:AUTO01")
