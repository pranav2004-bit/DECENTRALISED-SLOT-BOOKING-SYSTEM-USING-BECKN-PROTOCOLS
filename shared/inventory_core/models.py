"""Generic, domain-agnostic Resource/Slot/AvailabilityCalendar data model — the shared
inventory/booking core used by Beauty now and Healthcare/Automotive later (livetracker2.md
Phase 1.1, ADR-0003). Field shapes are grounded in the confirmed real `Descriptor`/`Provider`/
`Item`/`Time`/`Schedule` schemas from `beckn/protocol-specifications` — see
protocol_compliance_notes_v1.1.md §F for the sourced field-by-field mapping. Domain-specific
fields (consultation type, combo services, ...) do NOT belong here — they plug in later through
the Phase 1.5 adapter interface.

Deliberately a plain Django app (not tied to any one project's business-account model): `Resource`
references its owning business via an opaque `owner_ref` string, not a foreign key, so this
library stays importable identically by any consuming Django project (currently BPP only, per
Phase 2.2), matching the decoupling already established for shared/beckn_crypto and
shared/event_bus.
"""

import uuid
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

_WEEKDAY_TOKENS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")


class Resource(models.Model):
    """The bookable person/thing (doctor, stylist, bay+mechanic). Descriptive fields mirror the
    real `Descriptor.yaml` shape (`name`/`code`/`short_desc`/`long_desc`); `category_id` and
    `rateable` mirror the real `Provider.yaml`/`Item.yaml` fields of the same name — not invented
    independently, per protocol_compliance_notes_v1.1.md §F."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Opaque reference to the owning business account (Phase 2.2) — a plain indexed string, not a
    # FK, so this shared library never depends on a specific consuming app's account model.
    owner_ref = models.CharField(max_length=255, db_index=True)

    name = models.CharField(max_length=255)
    code = models.CharField(max_length=100, blank=True)
    short_desc = models.CharField(max_length=255, blank=True)
    long_desc = models.TextField(blank=True)
    category_id = models.CharField(max_length=100, blank=True)
    rateable = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"Resource({self.name})"


class SlotManager(models.Manager):
    def try_reserve(self, slot_id, *, quantity: int = 1) -> bool:
        """Atomic, race-safe capacity decrement at the database level (livetracker2.md §1.2) —
        a single conditional `UPDATE ... SET capacity_remaining = capacity_remaining - %s WHERE
        id = %s AND capacity_remaining >= %s`, not a check-then-write (`SELECT` then separate
        `UPDATE`). Postgres serializes concurrent `UPDATE`s to the same row, so under N
        concurrent callers against a capacity-1 slot, exactly one `UPDATE` matches the `WHERE`
        clause and succeeds; every other caller's `WHERE` no longer matches by the time its
        `UPDATE` runs and it affects zero rows — no separate lock needed for this single-field
        case. Returns `True` if the reservation succeeded, `False` if there wasn't enough
        capacity (or the slot doesn't exist) — never raises for the ordinary "not enough
        capacity" outcome.

        This is deliberately *not* the same thing as `lock_for_mutation` below: this method
        only ever touches `capacity_remaining` in one statement. A caller that needs to change
        multiple related fields (or rows) consistently around one slot mutation should use
        `lock_for_mutation` instead.
        """
        updated = self.filter(pk=slot_id, capacity_remaining__gte=quantity).update(
            capacity_remaining=models.F("capacity_remaining") - quantity
        )
        return updated == 1

    @contextmanager
    def lock_for_mutation(self, slot_id):
        """Short-lived technical lock during slot mutation (livetracker2.md §1.2) — a real
        `SELECT ... FOR UPDATE` row lock, held only for the lifetime of this `with` block, so a
        caller can safely read-then-write more than one field on a single `Slot` as one
        consistent unit (e.g. Phase 1.3's booking creation touching both `capacity_remaining`
        and `status` together), where `try_reserve`'s single-statement conditional `UPDATE`
        isn't expressive enough on its own.

        Deliberately distinct from the business-level reservation/`HELD` state (§1.3): that's a
        customer-facing hold that persists across requests (Redis-backed, TTL'd); this is a
        purely internal, sub-second DB mutual-exclusion primitive that never outlives one
        request's transaction. Must be called inside `transaction.atomic()` — Django raises if
        `select_for_update()` is used outside one, which is the correct behavior here too: a
        lock held outside a transaction isn't "short-lived," it's a bug.
        """
        slot = self.select_for_update().get(pk=slot_id)
        yield slot


class Slot(models.Model):
    """A bookable time window on a `Resource`. Has no direct protocol-schema counterpart — the
    real protocol only models availability at the Time/Schedule level and leaves per-slot
    generation to the provider's own inventory system, so this shape is this project's own
    design (protocol_compliance_notes_v1.1.md §F), same "project-defined, not protocol-confirmed"
    territory as the Fulfillment state machine (livetracker2.md §1.3).

    `capacity_total` is not in the tracker's literal Slot field list (`start_time`, `end_time`,
    `capacity_remaining`, `status`) but is added here: without it, `capacity_remaining` alone
    can't be audited or reset ("3 of how many remaining?") — a cheap, low-risk companion field
    needed to make the requested field meaningful, not scope creep.
    """

    class Status(models.TextChoices):
        AVAILABLE = "AVAILABLE", "Available"
        HELD = "HELD", "Held"
        BOOKED = "BOOKED", "Booked"
        CANCELLED = "CANCELLED", "Cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    resource = models.ForeignKey(Resource, on_delete=models.CASCADE, related_name="slots")

    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    capacity_total = models.PositiveIntegerField()
    capacity_remaining = models.PositiveIntegerField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.AVAILABLE)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = SlotManager()

    class Meta:
        indexes = [
            # Serves the "resource + time-range lookup" query path (1.1's own stated requirement).
            models.Index(
                fields=["resource", "start_time", "end_time"], name="slot_resource_time_idx"
            ),
            # Serves availability-status filtering (e.g. "only AVAILABLE slots").
            models.Index(fields=["status"], name="slot_status_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_time__gt=models.F("start_time")),
                name="slot_end_after_start",
            ),
            models.CheckConstraint(
                condition=models.Q(capacity_remaining__lte=models.F("capacity_total")),
                name="slot_capacity_remaining_lte_total",
            ),
            # Defense-in-depth for §1.2's atomicity guarantee: `PositiveIntegerField` only
            # validates at the Python/`full_clean()` level, not at the database level, so a raw
            # `UPDATE` that bypassed `try_reserve`'s own `WHERE capacity_remaining >= quantity`
            # guard could otherwise drive this negative. A real `CHECK` closes that gap for good,
            # not just for the one code path that's careful about it today.
            models.CheckConstraint(
                condition=models.Q(capacity_remaining__gte=0),
                name="slot_capacity_remaining_gte_zero",
            ),
        ]

    def __str__(self) -> str:
        return f"Slot({self.resource_id}, {self.start_time.isoformat()})"


class AvailabilityCalendar(models.Model):
    """A recurring schedule + exceptions/holidays that generates real `Slot` rows for a
    `Resource`. Field shapes are the confirmed real `Time`/`Schedule` schemas
    (protocol_compliance_notes_v1.1.md §F): `frequency` (recurrence interval, ISO 8601 duration,
    real `Schedule.frequency`), `range_start`/`range_end` (real `Time.range`), `days` (real
    `Time.days` — comma-separated weekday tokens), `times` (real `Schedule.times` — interpreted
    here as time-of-day anchors, the practical real-world usage of that field despite its loose
    `date-time` schema typing), `holidays` (real `Schedule.holidays`, dates to exclude).

    `slot_duration` and `slot_capacity` are this project's own fields (no protocol counterpart) —
    needed to turn a `times` anchor into a concrete `Slot.start_time`/`end_time`/`capacity_total`,
    same "project-defined" territory as `Slot` itself.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    resource = models.ForeignKey(
        Resource, on_delete=models.CASCADE, related_name="availability_calendars"
    )

    frequency = models.DurationField(help_text="Recurrence interval, e.g. 1 day.")
    range_start = models.DateTimeField()
    range_end = models.DateTimeField()
    days = models.CharField(
        max_length=50,
        blank=True,
        help_text="Comma-separated weekday tokens (MON..SUN). Blank means every day.",
    )
    times = models.JSONField(
        default=list, help_text='Time-of-day anchors, e.g. ["09:00", "14:00"].'
    )
    holidays = models.JSONField(default=list, help_text='Excluded dates, e.g. ["2026-12-25"].')

    slot_duration = models.DurationField(help_text="Length of each generated slot.")
    slot_capacity = models.PositiveIntegerField(default=1)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return (
            f"AvailabilityCalendar({self.resource_id}, "
            f"{self.range_start.date()}..{self.range_end.date()})"
        )

    def clean(self):
        errors = {}

        if self.frequency is not None and self.frequency <= timedelta(0):
            errors["frequency"] = "frequency must be a positive duration."
        if self.slot_duration is not None and self.slot_duration <= timedelta(0):
            errors["slot_duration"] = "slot_duration must be a positive duration."
        if self.range_start and self.range_end and self.range_end <= self.range_start:
            errors["range_end"] = "range_end must be after range_start."

        for token in self._day_tokens():
            if token not in _WEEKDAY_TOKENS:
                errors["days"] = (
                    f"invalid weekday token: {token!r}. Expected one of {_WEEKDAY_TOKENS}."
                )
                break

        if not self.times:
            errors["times"] = "at least one time-of-day anchor is required."
        else:
            for raw in self.times:
                if self._parse_time_of_day(raw) is None:
                    errors["times"] = (
                        f"invalid time-of-day value: {raw!r}. Expected HH:MM or HH:MM:SS."
                    )
                    break

        for raw in self.holidays:
            if self._parse_holiday(raw) is None:
                errors["holidays"] = f"invalid holiday date: {raw!r}. Expected YYYY-MM-DD."
                break

        if errors:
            raise ValidationError(errors)

    def _day_tokens(self) -> list[str]:
        return [t.strip().upper() for t in self.days.split(",") if t.strip()]

    @staticmethod
    def _parse_time_of_day(raw: str) -> time | None:
        for fmt in ("%H:%M:%S", "%H:%M"):
            try:
                return datetime.strptime(raw, fmt).time()
            except (TypeError, ValueError):
                continue
        return None

    @staticmethod
    def _parse_holiday(raw: str) -> date | None:
        try:
            return date.fromisoformat(raw)
        except (TypeError, ValueError):
            return None

    def generate_slots(self) -> list[Slot]:
        """Materializes concrete `Slot` rows for this calendar's recurring rule, honoring the
        `days` weekday filter and skipping `holidays`. Raises `ValidationError` on a malformed
        rule instead of silently miscalculating (1.1's EDGE test-gate requirement) — validation
        runs first, before any slot is built.
        """
        self.clean()

        day_tokens = set(self._day_tokens())
        holiday_dates = {self._parse_holiday(h) for h in self.holidays}
        times_of_day = [self._parse_time_of_day(t) for t in self.times]

        # `range_start`/`range_end` bound which *calendar days* this rule applies to, at day
        # granularity — not a sub-day cutoff on individual slot timestamps. A per-slot
        # `start <= range_end` check would silently drop a valid last day's slots whenever
        # `range_end`'s own time-of-day falls before that day's `times` anchors (e.g. a midnight
        # `range_end` would cut out every slot on the final day) — caught by 1.1's own EDGE gate.
        slots: list[Slot] = []
        current = self.range_start
        end_date = self.range_end.date()
        while current.date() <= end_date:
            weekday_token = _WEEKDAY_TOKENS[current.weekday()]
            day_allowed = not day_tokens or weekday_token in day_tokens
            if day_allowed and current.date() not in holiday_dates:
                for tod in times_of_day:
                    naive_start = datetime.combine(current.date(), tod)
                    start = (
                        timezone.make_aware(naive_start, current.tzinfo)
                        if timezone.is_naive(current)
                        else naive_start.replace(tzinfo=current.tzinfo)
                    )
                    end = start + self.slot_duration
                    slots.append(
                        Slot(
                            resource=self.resource,
                            start_time=start,
                            end_time=end,
                            capacity_total=self.slot_capacity,
                            capacity_remaining=self.slot_capacity,
                            status=Slot.Status.AVAILABLE,
                        )
                    )
            current += self.frequency

        return Slot.objects.bulk_create(slots)
