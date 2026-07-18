"""Phase 1.1 Test Gate (livetracker2.md) for shared/inventory_core's Resource/Slot/
AvailabilityCalendar model, exercised here (not in shared/inventory_core/tests.py) because it's
a Django app whose tests need a real settings module + database — BPP is its first, and
currently only, consumer (Phase 2.2). Matches the existing project convention of exercising
other shared/ libraries (event_bus) from within a consuming app's own test suite rather than
assuming a standalone shared/ test file gets collected by CI.

SMOKE/FUNC: module imports and migrates cleanly (proven implicitly by every test below via the
django_db fixture, which builds the real ephemeral test database from migrations).
"""

import datetime as dt

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from inventory_core.models import AvailabilityCalendar, Resource, Slot

_WEEKDAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]


@pytest.fixture
def resource(db):
    return Resource.objects.create(owner_ref="biz-1", name="Stylist A")


@pytest.mark.django_db
def test_resource_slot_calendar_import_and_migrate_cleanly(resource):
    assert Resource.objects.count() == 1
    assert Slot.objects.count() == 0
    assert AvailabilityCalendar.objects.count() == 0


@pytest.mark.django_db
def test_generate_slots_recurring_rule_with_holiday_produces_correct_set(resource):
    today = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    range_start = today + dt.timedelta(days=1)
    range_end = range_start + dt.timedelta(days=2)  # 3 consecutive days: start, +1, +2
    holiday = (range_start + dt.timedelta(days=1)).date().isoformat()  # exclude the middle day

    calendar = AvailabilityCalendar.objects.create(
        resource=resource,
        frequency=dt.timedelta(days=1),
        range_start=range_start,
        range_end=range_end,
        days="",  # no weekday filter — isolates this test to holiday-exclusion behavior
        times=["09:00", "14:00"],
        holidays=[holiday],
        slot_duration=dt.timedelta(minutes=30),
        slot_capacity=2,
    )

    slots = calendar.generate_slots()

    # 3 days - 1 holiday = 2 days * 2 times/day = 4 slots.
    assert len(slots) == 4
    assert Slot.objects.filter(resource=resource).count() == 4
    assert {s.start_time.date().isoformat() for s in slots} == {
        range_start.date().isoformat(),
        range_end.date().isoformat(),
    }
    for slot in slots:
        assert slot.status == Slot.Status.AVAILABLE
        assert slot.capacity_total == 2
        assert slot.capacity_remaining == 2
        assert slot.end_time - slot.start_time == dt.timedelta(minutes=30)


@pytest.mark.django_db
def test_generate_slots_respects_weekday_filter(resource):
    today = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    range_start = today + dt.timedelta(days=1)
    range_end = range_start + dt.timedelta(days=6)  # 7 consecutive days, each a distinct weekday
    only_weekday = _WEEKDAYS[range_start.weekday()]

    calendar = AvailabilityCalendar.objects.create(
        resource=resource,
        frequency=dt.timedelta(days=1),
        range_start=range_start,
        range_end=range_end,
        days=only_weekday,
        times=["10:00"],
        holidays=[],
        slot_duration=dt.timedelta(minutes=30),
        slot_capacity=1,
    )

    slots = calendar.generate_slots()

    assert len(slots) == 1
    assert slots[0].start_time.date() == range_start.date()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"frequency": dt.timedelta(0)}, id="zero-frequency"),
        pytest.param({"frequency": dt.timedelta(days=-1)}, id="negative-frequency"),
        pytest.param({"slot_duration": dt.timedelta(0)}, id="zero-slot-duration"),
        pytest.param({"range_end_before_start": True}, id="range-end-before-start"),
        pytest.param({"times": ["25:99"]}, id="malformed-time-of-day"),
        pytest.param({"days": "FUNDAY"}, id="invalid-weekday-token"),
        pytest.param({"holidays": ["not-a-date"]}, id="malformed-holiday-date"),
    ],
)
def test_generate_slots_rejects_malformed_recurrence_rule(resource, overrides):
    today = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    range_start = today + dt.timedelta(days=1)
    range_end = range_start + dt.timedelta(days=1)
    if overrides.pop("range_end_before_start", False):
        range_start, range_end = range_end, range_start

    kwargs = dict(
        resource=resource,
        frequency=dt.timedelta(days=1),
        range_start=range_start,
        range_end=range_end,
        days="",
        times=["09:00"],
        holidays=[],
        slot_duration=dt.timedelta(minutes=30),
        slot_capacity=1,
    )
    kwargs.update(overrides)
    calendar = AvailabilityCalendar(**kwargs)

    with pytest.raises(ValidationError):
        calendar.generate_slots()

    # Rejected outright, not partially/silently miscalculated — no slot should have been created.
    assert Slot.objects.filter(resource=resource).count() == 0


@pytest.mark.django_db
def test_slot_end_after_start_constraint_enforced_at_db_level(resource):
    start = timezone.now()
    with pytest.raises(IntegrityError), transaction.atomic():
        Slot.objects.create(
            resource=resource,
            start_time=start,
            end_time=start - dt.timedelta(minutes=1),
            capacity_total=1,
            capacity_remaining=1,
        )


@pytest.mark.django_db
def test_slot_capacity_remaining_lte_total_constraint_enforced_at_db_level(resource):
    start = timezone.now()
    with pytest.raises(IntegrityError), transaction.atomic():
        Slot.objects.create(
            resource=resource,
            start_time=start,
            end_time=start + dt.timedelta(minutes=30),
            capacity_total=1,
            capacity_remaining=2,
        )
