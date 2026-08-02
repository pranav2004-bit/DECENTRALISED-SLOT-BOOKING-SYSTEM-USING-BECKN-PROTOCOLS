"""Real event replay & recovery (livetracker4.md §2.2) — rebuilds a consumer's
state after a failure (or catches up a newly-registered consumer) by re-driving
`BookingAuditLogEntry` rows through a given handler.

**Why `BookingAuditLogEntry`, not the transient `shared/event_bus` queue itself:**
a real, durable, already-built queryable log this project already has enough of
to replay from (confirmed by direct read, not built fresh) — the queue itself is
transient by design (`consume_one()` pops it, `ack()`/`recover_orphaned()`
account for a crash mid-processing, not for "replay this again on purpose,
long after it was already consumed"). `ProcessedEvent` (the idempotency ledger)
carries even less — just `event_id`/`event_type`, no payload — so it can't be
replayed from either.

**Explicitly distinct from `reconciliation.py`'s own sweeps** (its own module
docstring already draws this line): those are periodic *state-comparison*
corrections (is this `HELD` booking's hold actually still active, checked
against current reality) — this is re-driving a specific, already-recorded
sequence of *past* events through a consumer that missed them, not comparing
anything to current state.
"""

from .models import BookingAuditLogEntry


def replay_events(*, start, end, booking_id=None, handler) -> int:
    """Re-drives every `BookingAuditLogEntry` in `[start, end)` (optionally scoped
    to one `booking_id`) through `handler`, oldest first — the same order they
    originally happened in, matching the bus's own per-entity ordering guarantee
    (`events.py`'s own docstring) so a handler that cares about sequence sees the
    same one on replay. Returns the number of entries replayed.

    Reconstructs a real, event-shaped dict from each row's own already-durable
    fields (not the original wire event, which no longer exists once consumed off
    the transient queue) — the same shape every real consumer already expects
    (`payload["booking_id"]`/`["correlation_id"]`/...), so a handler written
    against a live event needs no special-casing to also work against a replayed
    one. `event_id` is synthesized as `f"replay:{entry.id}"` — deliberately *not*
    the original live event's own id (never stored on the audit log row, and
    genuinely a different act from the original delivery) — so a caller wrapping
    `handler` with `process_event`'s idempotency check treats one replay run as
    its own, independently deduplicated pass, not a false "duplicate" of the
    original live delivery.

    **Honest limitation, not silently assumed away:** a handler with a
    non-transactional side effect (e.g. a Redis-backed metrics counter, which
    `process_event`'s own DB-transaction rollback can't undo) can double-count if
    the *same* window is replayed more than once — scoping replay precisely to a
    genuinely-missed window/entity is the caller's own responsibility, the same
    "best-effort, not exactly-once" honesty this project's Redis-backed counters
    already carry elsewhere (`RUNBOOK.md`'s own per-worker-undercounting notes)."""
    qs = BookingAuditLogEntry.objects.filter(created_at__gte=start, created_at__lt=end)
    if booking_id is not None:
        qs = qs.filter(booking_id_text=str(booking_id))
    qs = qs.order_by("created_at", "id")

    count = 0
    for entry in qs:
        event = {
            "event_id": f"replay:{entry.id}",
            "event_type": entry.event_type,
            "payload": {
                "version": 1,
                "booking_id": entry.booking_id_text,
                "correlation_id": entry.correlation_id,
                **entry.detail,
            },
            "published_at": entry.created_at.isoformat(),
        }
        handler(event)
        count += 1
    return count
