"""Reservation Window / TTL-based `HELD` state (livetracker2.md §1.3), Redis-backed — reuses
the real Redis dependency and TTL/state pattern already proven live via Phase 4.2's
`RedisCircuitBreaker` (`shared/resilient_http/circuit_breaker.py`), the one real, live-proven
Redis precedent for this (not Phase 2's in-memory-only rate limiter — see §1.3's own correction
note in the tracker).

Kept separate from models.py because it orchestrates two different real dependencies (Postgres
+ Redis) together, matching this codebase's existing service-layer pattern (e.g. BPP's
`core/onboarding_service.py`) rather than folding cross-cutting orchestration into a fat model
method.

Right-sized for this project's current `[MVP]` stage: expiry is detected opportunistically (a
caller checks "is this hold still live?" the next time it touches that booking/slot — e.g.
before attempting a new hold on the same slot), not via a background daemon subscribed to Redis
keyspace-notification events. That would require enabling keyspace notifications and running a
perpetual listener process — real operational complexity this project doesn't have infrastructure
for yet (no task queue/worker process exists in any of the four apps). The TTL key itself is the
actual source of truth for "is this hold still active"; the DB-side reconciliation
(`release_expired_hold`) needs to run before the result is trusted.

**Correction (livetracker2.md §3.11):** the paragraph above describes the intended design, but
until this phase `release_expired_hold()` had zero real callers anywhere (confirmed by grep) —
`confirm_hold()` detected an expired hold and raised without ever releasing it, so a `HELD`
reservation nobody re-touched leaked its slot capacity forever. `confirm_hold()` now actually
calls `release_expired_hold()` on expiry detection, closing the on-touch loop this docstring
always claimed existed. A genuinely-scheduled periodic sweep (`reconciliation.py`,
`sweep_expired_holds()`) is the remaining safety net for holds nobody ever touches again.
"""

import uuid

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .audit import log_booking_audit_event
from .events import BookingEvent, SlotEvent, publish_event
from .models import Booking, Slot


class ReservationHold:
    """Thin Redis TTL primitive — one key per `Booking`, no value semantics beyond existence."""

    def __init__(self, *, redis_client, key_prefix: str = "inventory_core:hold"):
        self._redis = redis_client
        self._key_prefix = key_prefix

    def _key(self, booking_id) -> str:
        return f"{self._key_prefix}:{booking_id}"

    def start(self, booking_id, *, ttl_seconds: float) -> None:
        self._redis.set(self._key(booking_id), "1", ex=ttl_seconds)

    def is_active(self, booking_id) -> bool:
        return bool(self._redis.exists(self._key(booking_id)))

    def remaining_ttl_seconds(self, booking_id) -> float | None:
        """Real seconds left on this hold's Redis TTL, or `None` if the key doesn't
        exist (never active, or already expired/cleared) — the honest "how long is
        this quote actually still good for" value, not the full configured window
        restated (livetracker2.md §3.3's `Quotation.ttl`)."""
        ttl = self._redis.ttl(self._key(booking_id))
        return ttl if ttl and ttl > 0 else None

    def clear(self, booking_id) -> None:
        self._redis.delete(self._key(booking_id))


def find_group_bookings(booking: Booking) -> list[Booking]:
    """Returns every `Booking` sharing `booking`'s own `booking_group_id`
    (`domain_data`), including `booking` itself, ordered by `group_sequence` (the
    original customer-facing order established at hold time — see
    `hold_multi_resource_booking`'s own docstring) — or just `[booking]` if it has
    no group id (the ordinary single-resource case every domain before §4.2 used
    exclusively).

    **livetracker3.md §6.1 fix (2026-08-01):** previously ordered by `.id`, a
    random UUID with no relationship to which resource the customer actually
    picked first — deterministic *within* this function (repeated calls agree
    with each other) but not consistent with the order `hold_multi_resource_
    booking` itself returned at select time, so a customer could see "Bay +
    Mechanic" at select and "Mechanic + Bay" at confirm for the exact same
    booking. `group_sequence` is stamped once, at hold time, in the customer's
    own original order, and is what every later stage (init/confirm/cancel)
    reads back here — the single source of truth for group member order, not
    re-derived differently by each caller.

    §4.2's multi-resource booking support (e.g. Automotive's bay+mechanic pair,
    held together by `hold_multi_resource_booking` below): lets a caller
    confirm/cancel every resource in a genuine multi-resource booking together,
    without `confirm_service.py`/`cancel_service.py` (or any other call site)
    needing its own awareness of whether a given booking is part of a group —
    they just call this first and act on whatever it returns.
    """
    group_id = (booking.domain_data or {}).get("booking_group_id")
    if not group_id:
        return [booking]
    bookings = Booking.objects.filter(domain_data__booking_group_id=group_id)
    return sorted(bookings, key=lambda b: (b.domain_data or {}).get("group_sequence", 0))


def hold_multi_resource_booking(
    slot_ids,
    *,
    holder_ref: str,
    redis_client,
    ttl_seconds: float,
    event_bus=None,
) -> list[Booking] | None:
    """§4.2's genuine multi-resource booking primitive: atomically holds one unit of
    capacity across *every* slot in `slot_ids` (each on a different `Resource` —
    e.g. Automotive's bay + mechanic, both needed simultaneously for one real
    customer booking), succeeding only if **every** slot has capacity, failing
    cleanly (returns `None`, nothing held, nothing partially decremented) the
    moment even one doesn't — never a partial hold left dangling.

    All slots are locked inside **one** `transaction.atomic()` block, in a
    deterministic order (sorted by id, not the order passed in) — the exact
    deadlock-avoidance precedent already established by `reschedule_active_booking`
    above: two concurrent multi-resource holds racing over the same set of slots,
    submitted in different orders, could otherwise deadlock against each other.
    Capacity is only checked (never mutated) in a first pass over the locked
    slots — if any lacks capacity, this returns before mutating anything, so the
    transaction has nothing to roll back and no torn state is possible either way.

    Returns the new `Booking` rows (all `HELD`, one per slot, quantity 1, sharing
    a fresh `booking_group_id` in `domain_data` — the same domain_data-linking
    convention as Beauty's `create_combo_booking`, not a schema change), in the
    **same order as `slot_ids`** (not the internal locking order) — or `None` if
    any slot lacked capacity. `event_bus` is optional (`None` by default) — pass a
    real `EventBus` to also publish one `SlotEvent.RESERVED` per held slot.

    Each row's `domain_data` also carries `group_sequence` — its position in the
    caller's own `slot_ids` order (0 for the first/customer-clicked resource, 1
    for the paired one) — the single source of truth `find_group_bookings` reads
    back later, so every later stage (init/confirm/cancel) agrees with what the
    customer saw at select time instead of each re-deriving its own order
    (livetracker3.md §6.1 fix, 2026-08-01: this used to be silently re-sorted by
    `.id`, a random UUID unrelated to selection order).
    """
    slot_ids = list(slot_ids)
    sorted_ids = sorted(slot_ids, key=str)
    group_id = str(uuid.uuid4())
    sequence_by_sid = {str(sid): idx for idx, sid in enumerate(slot_ids)}

    with transaction.atomic():
        locked = {}
        for sid in sorted_ids:
            with Slot.objects.lock_for_mutation(sid) as slot:
                if slot.status not in (Slot.Status.AVAILABLE, Slot.Status.HELD):
                    return None
                if slot.capacity_remaining < 1:
                    return None
                locked[str(sid)] = slot

        bookings_by_slot = {}
        for sid in sorted_ids:
            slot = locked[str(sid)]
            slot.capacity_remaining -= 1
            if slot.capacity_remaining == 0:
                slot.status = Slot.Status.HELD
            slot.save(update_fields=["capacity_remaining", "status", "updated_at"])
            bookings_by_slot[str(sid)] = Booking.objects.create(
                slot=slot,
                holder_ref=holder_ref,
                quantity=1,
                domain_data={
                    "booking_group_id": group_id,
                    "group_sequence": sequence_by_sid[str(sid)],
                },
            )

    bookings = [bookings_by_slot[str(sid)] for sid in slot_ids]

    hold = ReservationHold(redis_client=redis_client)
    for booking in bookings:
        hold.start(booking.id, ttl_seconds=ttl_seconds)

    if event_bus is not None:
        for slot_id, booking in zip(slot_ids, bookings, strict=True):
            publish_event(
                event_bus,
                SlotEvent.RESERVED,
                slot_id=str(slot_id),
                booking_id=str(booking.id),
                holder_ref=holder_ref,
                quantity=1,
            )

    return bookings


def hold_slot(
    slot_id,
    *,
    holder_ref: str,
    redis_client,
    quantity: int = 1,
    ttl_seconds: float,
    event_bus=None,
) -> Booking | None:
    """Atomically holds `quantity` capacity on `slot_id` for `holder_ref` and starts the
    Redis-backed TTL reservation window. Returns the new `Booking` (status `HELD`) on success,
    or `None` if the slot doesn't have enough capacity — never raises for that ordinary outcome,
    matching `Slot.objects.try_reserve`'s own contract (§1.2).

    `event_bus` is optional (`None` by default) — pass a real `EventBus` to also publish a
    `SlotEvent.RESERVED` (§1.4) on success.
    """
    with transaction.atomic():
        with Slot.objects.lock_for_mutation(slot_id) as slot:
            if slot.status not in (Slot.Status.AVAILABLE, Slot.Status.HELD):
                return None
            if slot.capacity_remaining < quantity:
                return None
            slot.capacity_remaining -= quantity
            if slot.capacity_remaining == 0:
                slot.status = Slot.Status.HELD
            slot.save(update_fields=["capacity_remaining", "status", "updated_at"])
            booking = Booking.objects.create(slot=slot, holder_ref=holder_ref, quantity=quantity)

    ReservationHold(redis_client=redis_client).start(booking.id, ttl_seconds=ttl_seconds)

    if event_bus is not None:
        publish_event(
            event_bus,
            SlotEvent.RESERVED,
            slot_id=str(slot_id),
            booking_id=str(booking.id),
            holder_ref=holder_ref,
            quantity=quantity,
        )

    return booking


def _restore_capacity(slot: Slot, quantity: int) -> None:
    """Shared capacity-restoration block (livetracker2.md §3.5's Rule-of-Three
    extraction — `release_expired_hold`/`release_hold_now` already duplicated this
    exact logic; §3.5's `cancel_booking` would have made a third copy). Caller must
    already hold `slot`'s row lock (via `Slot.objects.lock_for_mutation`) and be
    inside a `transaction.atomic()` block; this only mutates the in-memory instance
    and saves it, it does not lock or start a transaction itself."""
    slot.capacity_remaining = min(slot.capacity_remaining + quantity, slot.capacity_total)
    if slot.status == Slot.Status.HELD and slot.capacity_remaining > 0:
        slot.status = Slot.Status.AVAILABLE
    slot.save(update_fields=["capacity_remaining", "status", "updated_at"])


def release_expired_hold(booking_id, *, redis_client, event_bus=None, correlation_id=None) -> bool:
    """If `booking_id`'s Redis TTL hold has expired (or was never active) and the `Booking` is
    still `HELD`, atomically cancels the `Booking` and restores the `Slot`'s held capacity —
    the "HELD slot with an expired TTL auto-returns to AVAILABLE" behavior §1.3's Test Gate asks
    for. Returns `True` if it performed a release, `False` if the hold is still active or the
    booking was already resolved (never raises for either ordinary outcome).

    `event_bus` is optional (`None` by default) — pass a real `EventBus` to also publish
    `SlotEvent.RELEASED` + `BookingEvent.CANCELLED` (§1.4) when a release actually happens, and
    to record a `BookingAuditLogEntry` (§3.10) alongside it — both are real business-event
    observability, gated on the same flag rather than adding a second one. `correlation_id` is
    genuinely optional here (default `None`): this release is opportunistic, detected as a side
    effect of some *other*, unrelated request touching this slot (§1.3's own docstring), so there
    is no single customer action whose id would honestly describe it.

    Race-safe (§3.11): the status check and the release happen under one `select_for_update()`
    on the `Booking` row, held for the whole function — not a plain unlocked read followed by a
    separately-locked write. Needed once this function gained two real, genuinely concurrent
    callers (the on-touch path in `confirm_hold` plus the new scheduled sweep,
    `reconciliation.sweep_expired_holds`): without the lock, two callers racing to release the
    *same* expired booking could both pass an unlocked "is this still HELD" check and both
    restore capacity — a real double-credit, masked only by luck for a fully-held slot, not for
    one with headroom below `capacity_total`. Was harmless before this phase (zero real callers
    to race), not harmless now.
    """
    with transaction.atomic():
        try:
            booking = Booking.objects.select_for_update().select_related("slot").get(pk=booking_id)
        except Booking.DoesNotExist:
            return False

        if booking.status != Booking.Status.HELD:
            return False

        if ReservationHold(redis_client=redis_client).is_active(booking_id):
            return False

        booking.transition_status(Booking.Status.CANCELLED)
        with Slot.objects.lock_for_mutation(booking.slot_id) as slot:
            _restore_capacity(slot, booking.quantity)

    if event_bus is not None:
        publish_event(event_bus, SlotEvent.RELEASED, slot_id=str(booking.slot_id))
        publish_event(event_bus, BookingEvent.CANCELLED, booking_id=str(booking_id))
        log_booking_audit_event(
            booking=booking,
            booking_id=str(booking_id),
            event_type=BookingEvent.CANCELLED,
            detail={"reason": "hold_expired", "slot_id": str(booking.slot_id)},
            correlation_id=correlation_id,
        )

    return True


def release_hold_now(booking_id, *, redis_client, event_bus=None, correlation_id=None) -> bool:
    """Releases a `HELD` booking immediately, regardless of whether its Redis TTL has
    actually expired yet — the deliberate counterpart to `release_expired_hold`, which
    only acts once the TTL is already gone. Needed for §3.2's real re-selection case: a
    customer selecting a different slot after already holding one from an earlier
    `select` in the same transaction must not leak the first hold until its TTL
    eventually expires on its own. Same capacity-restoration logic as
    `release_expired_hold`, minus the `is_active()` gate, plus an explicit
    `ReservationHold.clear()` so the now-stale Redis key doesn't linger either. Returns
    `True` if it performed a release, `False` if the booking wasn't `HELD` (never raises
    for that ordinary outcome).

    Race-safe (§3.11), same reasoning and pattern as `release_expired_hold`: the status
    check and the release happen under one `select_for_update()` on the `Booking` row."""
    with transaction.atomic():
        try:
            booking = Booking.objects.select_for_update().select_related("slot").get(pk=booking_id)
        except Booking.DoesNotExist:
            return False

        if booking.status != Booking.Status.HELD:
            return False

        booking.transition_status(Booking.Status.CANCELLED)
        with Slot.objects.lock_for_mutation(booking.slot_id) as slot:
            _restore_capacity(slot, booking.quantity)

    ReservationHold(redis_client=redis_client).clear(booking_id)

    if event_bus is not None:
        publish_event(event_bus, SlotEvent.RELEASED, slot_id=str(booking.slot_id))
        publish_event(event_bus, BookingEvent.CANCELLED, booking_id=str(booking_id))
        log_booking_audit_event(
            booking=booking,
            booking_id=str(booking_id),
            event_type=BookingEvent.CANCELLED,
            detail={"reason": "superseded_by_reselect", "slot_id": str(booking.slot_id)},
            correlation_id=correlation_id,
        )

    return True


def confirm_hold(booking_id, *, redis_client, event_bus=None, correlation_id=None) -> Booking:
    """Transitions a `HELD` booking to `ACTIVE` (the real confirm business-flow itself is
    Phase 3's job — this is just the state-machine + Redis-cleanup half of it) and clears its
    Redis TTL key, since an `ACTIVE` booking is no longer time-limited. Raises `ValidationError`
    if the booking isn't currently `HELD` and isn't already `ACTIVE` either (e.g. its hold
    already expired, or it was cancelled) — never silently confirms a booking that shouldn't be
    confirmable anymore. An already-`ACTIVE` booking is a real, idempotent no-op (see below), not
    an error.

    Idempotent and race-safe by design (livetracker2.md §3.4, `protocol_compliance_notes_v1.1.md`
    §K): the read+check+transition happens under a real `select_for_update()` row lock, and an
    already-`ACTIVE` booking short-circuits to a no-op return instead of re-running the
    transition. Two genuinely concurrent callers confirming the *same* booking (a real
    double-submit/flaky-retry scenario) would otherwise both pass the "is this HELD" check
    before either committed, and each independently fire a fresh `BookingConfirmed` event —
    Phase 1.4's event-idempotency layer dedupes by `event_id` (per publish call), not by
    business key, so it would NOT catch two independently-published confirmations of the same
    booking as duplicates. The second (blocked, then unblocked) caller here instead observes
    the already-`ACTIVE` state and returns without re-transitioning or re-publishing.

    `event_bus` is optional (`None` by default) — pass a real `EventBus` to also publish
    `SlotEvent.CONFIRMED` + `BookingEvent.CONFIRMED` (§1.4) on a genuine (non-idempotent) success,
    and to record a `BookingAuditLogEntry` (§3.10) alongside it. `correlation_id` (§3.10) is the
    real `X-Correlation-Id` of the customer's `/confirm` action that reached this call — the
    caller (BPP's `dispatch_on_confirm`) is responsible for capturing it before spawning its
    background-dispatch thread, since a `ContextVar` doesn't cross a manually-created thread.

    §3.11: an expired hold now also actually releases the slot's capacity via
    `release_expired_hold`, not just raises — closing the real leak an expired `HELD` booking
    used to leave behind (confirmed live: previously nothing restored capacity on this path,
    the slot stayed leaked until something else happened to call the same, then-uncalled-anywhere
    function). Done as a separate call *after* this function's own `select_for_update()` block
    commits, not nested inside it — nesting would put the release under this call's own
    transaction, and this call's subsequent `raise` would roll the whole thing back, undoing the
    just-performed release along with it.
    """
    with transaction.atomic():
        booking = Booking.objects.select_for_update().get(pk=booking_id)
        if booking.status == Booking.Status.ACTIVE:
            return booking
        hold_is_active = ReservationHold(redis_client=redis_client).is_active(booking_id)
        if hold_is_active:
            booking.transition_status(Booking.Status.ACTIVE)
    if not hold_is_active:
        release_expired_hold(
            booking_id, redis_client=redis_client, event_bus=event_bus, correlation_id=correlation_id
        )
        raise ValidationError(
            f"cannot confirm booking {booking_id}: its reservation hold has already expired."
        )
    ReservationHold(redis_client=redis_client).clear(booking_id)

    if event_bus is not None:
        publish_event(event_bus, SlotEvent.CONFIRMED, slot_id=str(booking.slot_id))
        publish_event(event_bus, BookingEvent.CONFIRMED, booking_id=str(booking_id))
        log_booking_audit_event(
            booking=booking,
            booking_id=str(booking_id),
            event_type=BookingEvent.CONFIRMED,
            detail={"slot_id": str(booking.slot_id)},
            correlation_id=correlation_id,
        )

    return booking


def cancel_booking(booking_id, *, event_bus=None, correlation_id=None) -> Booking:
    """Cancels an already-`ACTIVE` (confirmed) booking (livetracker2.md §3.5) — a
    real `/cancel`, distinct from `release_hold_now`'s pre-order `HELD` release: a
    still-`HELD` hold was never actually offered to the customer as a confirmed,
    cancellable thing, so this deliberately only accepts `ACTIVE` bookings. Raises
    `ValidationError` for any other status (already `CANCELLED`/`COMPLETE`, or
    still `HELD` — use `release_hold_now` for that case instead).

    Race-safe by the same `select_for_update()` discipline as `confirm_hold`
    (§3.4) — two concurrent cancel attempts on the same booking can't both pass
    the "is this ACTIVE" check and each fire a duplicate `BookingCancelled`.

    `correlation_id` (§3.10): the real `X-Correlation-Id` of the customer's
    `/cancel` action, threaded through the same way as `confirm_hold`'s.
    """
    with transaction.atomic():
        booking = Booking.objects.select_for_update().get(pk=booking_id)
        if booking.status != Booking.Status.ACTIVE:
            raise ValidationError(
                f"cannot cancel booking {booking_id}: not currently ACTIVE "
                f"(status={booking.status!r})."
            )
        booking.transition_status(Booking.Status.CANCELLED)
        with Slot.objects.lock_for_mutation(booking.slot_id) as slot:
            _restore_capacity(slot, booking.quantity)

    if event_bus is not None:
        publish_event(event_bus, SlotEvent.RELEASED, slot_id=str(booking.slot_id))
        publish_event(event_bus, BookingEvent.CANCELLED, booking_id=str(booking_id))
        log_booking_audit_event(
            booking=booking,
            booking_id=str(booking_id),
            event_type=BookingEvent.CANCELLED,
            detail={"reason": "customer_cancel", "slot_id": str(booking.slot_id)},
            correlation_id=correlation_id,
        )

    return booking


def complete_active_booking(booking_id, *, event_bus=None, correlation_id=None) -> bool:
    """If `booking_id` is still `ACTIVE` and its `Slot.end_time` has already passed,
    atomically transitions it to `COMPLETE` (livetracker2.md §4.5's prerequisite for a
    real completed booking — rating/support both need one to genuinely exist against).
    Returns `True` if it performed a completion, `False` if the booking isn't `ACTIVE`
    or its slot hasn't ended yet (never raises for either ordinary outcome, matching
    `release_expired_hold`'s contract).

    Deliberately does not touch `Slot`/capacity: completion is a booking-lifecycle
    transition, not a capacity-release one — the slot's capacity was already consumed
    for the fulfillment window that just ended, not returned to the pool the way a
    cancellation returns it.

    Race-safe by the same `select_for_update()` discipline as `release_expired_hold`:
    two concurrent sweep/on-touch callers can't both pass the "is this ACTIVE and
    ended" check and each fire a duplicate `BookingCompleted`.

    `correlation_id` is optional (`None` by default) — like `release_expired_hold`,
    this is opportunistic (a scheduled sweep noticing time has passed), not a single
    customer action with an `X-Correlation-Id` to thread through.
    """
    with transaction.atomic():
        try:
            booking = Booking.objects.select_for_update().select_related("slot").get(pk=booking_id)
        except Booking.DoesNotExist:
            return False

        if booking.status != Booking.Status.ACTIVE:
            return False

        if booking.slot.end_time > timezone.now():
            return False

        booking.transition_status(Booking.Status.COMPLETE)

    if event_bus is not None:
        publish_event(event_bus, SlotEvent.COMPLETED, slot_id=str(booking.slot_id))
        publish_event(event_bus, BookingEvent.COMPLETED, booking_id=str(booking_id))
        log_booking_audit_event(
            booking=booking,
            booking_id=str(booking_id),
            event_type=BookingEvent.COMPLETED,
            detail={"reason": "fulfillment_window_ended", "slot_id": str(booking.slot_id)},
            correlation_id=correlation_id,
        )

    return True


def reschedule_active_booking(
    booking_id, new_slot_id, *, event_bus=None, correlation_id=None
) -> Booking:
    """Moves an already-`ACTIVE` booking from its current `Slot` to a different
    one on the same `Resource` (livetracker2.md §3.5's `/update` reschedule),
    atomically: claims capacity on the new slot (raising cleanly if unavailable),
    releases capacity on the old slot, and reassigns the booking. Raises
    `ValidationError` if the booking isn't `ACTIVE`, the new slot is the same as
    the current one, or the new slot doesn't have capacity.

    Deadlock-safety, found and fixed by design before this was first written
    (`protocol_compliance_notes_v1.1.md` §L): naively locking the old slot then
    the new slot (in that order) would let two concurrent reschedules moving
    bookings in opposite directions between the same two slots deadlock against
    each other. Both slot rows are locked in a deterministic order (sorted by id)
    regardless of which is "old" and which is "new" here instead.
    """
    with transaction.atomic():
        booking = Booking.objects.select_for_update().get(pk=booking_id)
        if booking.status != Booking.Status.ACTIVE:
            raise ValidationError(
                f"cannot reschedule booking {booking_id}: not currently ACTIVE "
                f"(status={booking.status!r})."
            )
        old_slot_id = booking.slot_id
        if str(new_slot_id) == str(old_slot_id):
            raise ValidationError("new slot is the same as the booking's current slot")

        first_id, second_id = sorted([str(old_slot_id), str(new_slot_id)])
        with (
            Slot.objects.lock_for_mutation(first_id) as first_slot,
            Slot.objects.lock_for_mutation(second_id) as second_slot,
        ):
            new_slot = first_slot if str(first_slot.id) == str(new_slot_id) else second_slot
            old_slot = first_slot if str(first_slot.id) == str(old_slot_id) else second_slot

            if new_slot.capacity_remaining < booking.quantity:
                raise ValidationError(f"slot {new_slot_id} does not have enough capacity")
            new_slot.capacity_remaining -= booking.quantity
            if new_slot.capacity_remaining == 0:
                new_slot.status = Slot.Status.HELD
            new_slot.save(update_fields=["capacity_remaining", "status", "updated_at"])

            _restore_capacity(old_slot, booking.quantity)

        booking.slot_id = new_slot.id
        booking.save(update_fields=["slot", "updated_at"])

    if event_bus is not None:
        publish_event(event_bus, SlotEvent.RELEASED, slot_id=str(old_slot_id))
        publish_event(event_bus, SlotEvent.RESCHEDULED, slot_id=str(new_slot_id))
        publish_event(event_bus, BookingEvent.RESCHEDULED, booking_id=str(booking_id))
        log_booking_audit_event(
            booking=booking,
            booking_id=str(booking_id),
            event_type=BookingEvent.RESCHEDULED,
            detail={"old_slot_id": str(old_slot_id), "new_slot_id": str(new_slot_id)},
            correlation_id=correlation_id,
        )

    booking.refresh_from_db()
    return booking


def reschedule_booking_group(booking_new_slot_pairs, *, event_bus=None, correlation_id=None):
    """§4.2's group-aware generalization of `reschedule_active_booking` — moves *every*
    booking in a multi-resource group (e.g. Automotive's bay+mechanic pair) to its own
    new slot, atomically as one unit, rather than moving only the one resource a naive
    caller might otherwise name. `booking_new_slot_pairs` is an iterable of
    `(booking_id, new_slot_id)` tuples, one per resource being rescheduled together.

    Real gap found live, not in code review (2026-07-26): the original
    `reschedule_active_booking` only ever moved *one* booking — calling it once per
    group member independently (matching this project's existing group-widening
    pattern used for `confirm`/`cancel`) would have left the *other* resource's booking
    at the old time whenever the new time only had room for the first one processed,
    a real half-rescheduled state no single-booking function call can prevent on its
    own. This function instead locks every old *and* new slot row involved across the
    whole group inside **one** `transaction.atomic()`, checks capacity on every new
    slot first (before mutating any of them), and only then moves them all — the same
    all-or-nothing discipline as `hold_multi_resource_booking`. Raises
    `ValidationError` if any booking isn't `ACTIVE`, any new slot equals that
    booking's current slot, or any new slot lacks capacity — nothing in the group
    moves if any one of them can't.

    Deadlock-safety: every slot row involved (old and new, across every pair) is
    locked in one deterministic, sorted-by-id order — the same precedent
    `reschedule_active_booking` already established for its own two slots, generalized
    to however many are involved here.
    """
    pairs = [(str(booking_id), str(new_slot_id)) for booking_id, new_slot_id in booking_new_slot_pairs]

    with transaction.atomic():
        bookings = {}
        for booking_id, _new_slot_id in pairs:
            booking = Booking.objects.select_for_update().get(pk=booking_id)
            if booking.status != Booking.Status.ACTIVE:
                raise ValidationError(
                    f"cannot reschedule booking {booking_id}: not currently ACTIVE "
                    f"(status={booking.status!r})."
                )
            bookings[booking_id] = booking

        old_slot_ids = {str(b.slot_id) for b in bookings.values()}
        for booking_id, new_slot_id in pairs:
            if new_slot_id == str(bookings[booking_id].slot_id):
                raise ValidationError("new slot is the same as the booking's current slot")

        all_ids = sorted(old_slot_ids | {new_slot_id for _, new_slot_id in pairs})
        locked_slots = {}
        for slot_id in all_ids:
            with Slot.objects.lock_for_mutation(slot_id) as slot:
                locked_slots[slot_id] = slot

        for booking_id, new_slot_id in pairs:
            new_slot = locked_slots[new_slot_id]
            if new_slot.capacity_remaining < bookings[booking_id].quantity:
                raise ValidationError(f"slot {new_slot_id} does not have enough capacity")

        old_slot_id_by_booking = {bid: str(b.slot_id) for bid, b in bookings.items()}
        for booking_id, new_slot_id in pairs:
            booking = bookings[booking_id]
            new_slot = locked_slots[new_slot_id]
            new_slot.capacity_remaining -= booking.quantity
            if new_slot.capacity_remaining == 0:
                new_slot.status = Slot.Status.HELD
            new_slot.save(update_fields=["capacity_remaining", "status", "updated_at"])
            _restore_capacity(locked_slots[old_slot_id_by_booking[booking_id]], booking.quantity)
            booking.slot_id = new_slot.id
            booking.save(update_fields=["slot", "updated_at"])

    result = []
    for booking_id, new_slot_id in pairs:
        booking = bookings[booking_id]
        booking.refresh_from_db()
        result.append(booking)
        if event_bus is not None:
            old_slot_id = old_slot_id_by_booking[booking_id]
            publish_event(event_bus, SlotEvent.RELEASED, slot_id=old_slot_id)
            publish_event(event_bus, SlotEvent.RESCHEDULED, slot_id=new_slot_id)
            publish_event(event_bus, BookingEvent.RESCHEDULED, booking_id=booking_id)
            log_booking_audit_event(
                booking=booking,
                booking_id=booking_id,
                event_type=BookingEvent.RESCHEDULED,
                detail={"old_slot_id": old_slot_id, "new_slot_id": new_slot_id},
                correlation_id=correlation_id,
            )

    return result
