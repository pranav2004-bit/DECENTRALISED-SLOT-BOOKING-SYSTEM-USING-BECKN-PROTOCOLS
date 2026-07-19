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
(`release_expired_hold`) just needs to run before the result is trusted, which every real caller
of this module already does on the hot path.
"""

from django.core.exceptions import ValidationError
from django.db import transaction

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

    def clear(self, booking_id) -> None:
        self._redis.delete(self._key(booking_id))


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


def release_expired_hold(booking_id, *, redis_client, event_bus=None) -> bool:
    """If `booking_id`'s Redis TTL hold has expired (or was never active) and the `Booking` is
    still `HELD`, atomically cancels the `Booking` and restores the `Slot`'s held capacity —
    the "HELD slot with an expired TTL auto-returns to AVAILABLE" behavior §1.3's Test Gate asks
    for. Returns `True` if it performed a release, `False` if the hold is still active or the
    booking was already resolved (never raises for either ordinary outcome).

    `event_bus` is optional (`None` by default) — pass a real `EventBus` to also publish
    `SlotEvent.RELEASED` + `BookingEvent.CANCELLED` (§1.4) when a release actually happens.
    """
    try:
        booking = Booking.objects.select_related("slot").get(pk=booking_id)
    except Booking.DoesNotExist:
        return False

    if booking.status != Booking.Status.HELD:
        return False

    if ReservationHold(redis_client=redis_client).is_active(booking_id):
        return False

    with transaction.atomic():
        booking.transition_status(Booking.Status.CANCELLED)
        with Slot.objects.lock_for_mutation(booking.slot_id) as slot:
            slot.capacity_remaining = min(
                slot.capacity_remaining + booking.quantity, slot.capacity_total
            )
            if slot.status == Slot.Status.HELD and slot.capacity_remaining > 0:
                slot.status = Slot.Status.AVAILABLE
            slot.save(update_fields=["capacity_remaining", "status", "updated_at"])

    if event_bus is not None:
        publish_event(event_bus, SlotEvent.RELEASED, slot_id=str(booking.slot_id))
        publish_event(event_bus, BookingEvent.CANCELLED, booking_id=str(booking_id))

    return True


def confirm_hold(booking_id, *, redis_client, event_bus=None) -> Booking:
    """Transitions a `HELD` booking to `ACTIVE` (the real confirm business-flow itself is
    Phase 3's job — this is just the state-machine + Redis-cleanup half of it) and clears its
    Redis TTL key, since an `ACTIVE` booking is no longer time-limited. Raises `ValidationError`
    via `transition_status` if the booking isn't currently `HELD` (e.g. its hold already
    expired) — never silently confirms a booking that shouldn't be confirmable anymore.

    `event_bus` is optional (`None` by default) — pass a real `EventBus` to also publish
    `SlotEvent.CONFIRMED` + `BookingEvent.CONFIRMED` (§1.4) on success.
    """
    booking = Booking.objects.get(pk=booking_id)
    if not ReservationHold(redis_client=redis_client).is_active(booking_id):
        raise ValidationError(
            f"cannot confirm booking {booking_id}: its reservation hold has already expired."
        )
    booking.transition_status(Booking.Status.ACTIVE)
    ReservationHold(redis_client=redis_client).clear(booking_id)

    if event_bus is not None:
        publish_event(event_bus, SlotEvent.CONFIRMED, slot_id=str(booking.slot_id))
        publish_event(event_bus, BookingEvent.CONFIRMED, booking_id=str(booking_id))

    return booking
