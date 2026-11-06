"""Audit finding closed live 2026-11-05 (BPP's half of the gap BAP's own
core/notifications.py::_run_then_close_connection already closed for itself):
a `threading.Thread` that touches the ORM opens a real DB connection on that
thread — Django only auto-closes connections at the end of a real
request/response cycle (`request_finished`), never for an arbitrary
background thread. Left unclosed, a `daemon=True` thread's connection lingers
until Python garbage-collects it, which is not deterministic and, under
enough volume (every `on_X` Beckn callback dispatch — search, select, init,
confirm, cancel, update, track, rating, status, support — spawns one of
these), can exhaust a real connection limit.

Extracted into `shared/` (rather than duplicated per-service) because every
BPP instance (medical, automotive, beauty, ...) runs this same codebase, and
the identical pattern was about to be copy-pasted into 10 call sites.
"""

import threading
from typing import Callable

from django.db import connection


def _run_then_close_connection(target: Callable, kwargs: dict) -> None:
    try:
        target(**kwargs)
    finally:
        connection.close()


def run_in_background(target: Callable, kwargs: dict, *, name: str | None = None) -> threading.Thread:
    """Starts `target(**kwargs)` on a daemon thread that closes its own DB
    connection when `target` returns or raises. Deliberately does NOT touch
    the calling thread's connection (no `close_old_connections()` here) — the
    calling thread is usually a live request (or test) thread with its own
    connection/transaction already in flight; closing it out from under the
    caller is a bug, not a safety net. Same scope as BAP's own proven
    `core/notifications.py::_run_then_close_connection`: close only what this
    helper itself opened."""
    thread = threading.Thread(
        target=_run_then_close_connection,
        kwargs={"target": target, "kwargs": kwargs},
        daemon=True,
        name=name,
    )
    thread.start()
    return thread
