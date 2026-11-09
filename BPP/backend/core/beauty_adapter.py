"""The real Beauty domain adapter (livetracker2.md §2.2), built on `inventory_core`'s
Phase 1.5 extension-point interface — `Resource` = stylist/chair, with combo-service
support (sequential slot chaining for multi-step services, e.g. haircut then colour).

Registered with `inventory_core.domain_adapter`'s registry at app startup
(`core/apps.py`'s `ready()`), keyed by the real confirmed `ONDC:RET13` Beauty domain code
(`settings.DOMAIN_BEAUTY`), so any code holding just that string can look up the right
adapter without importing this module directly.
"""

import datetime as dt
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from inventory_core.domain_adapter import DomainAdapter
from inventory_core.models import Slot
from inventory_core.reservation import hold_slot

RESOURCE_TYPES = ("stylist", "chair")
GENDER_OPTIONS = ("male", "female", "other")
# Mirrored in BPP/web/app/resources/page.tsx's own BEAUTY_SERVICES constant
# (ADR-0004: duplicated, not shared across the frontend/backend language boundary,
# same convention HealthcareDomainAdapter's SPECIALISATIONS already uses) — a fixed
# list, not free text, for the same reason: comparable, filterable service values
# across different salons, not each business inventing its own spelling.
SPECIALISATIONS = (
    "Hair Styling",
    "Hair Cutting",
    "Hair Coloring",
    "Hair Spa & Treatment",
    "Bridal Makeup",
    "Party Makeup",
    "Facial & Skin Care",
    "Manicure",
    "Pedicure",
    "Waxing",
    "Threading",
    "Mehendi (Henna)",
    "Massage & Spa",
    "Nail Art",
)


class BeautyDomainAdapter(DomainAdapter):
    domain_code = settings.DOMAIN_BEAUTY

    def validate_resource_domain_data(self, domain_data: dict) -> None:
        resource_type = domain_data.get("resource_type")
        if resource_type not in RESOURCE_TYPES:
            raise ValidationError(
                f"resource_type must be one of {RESOURCE_TYPES}, got {resource_type!r}"
            )

        # Stylist profile fields — required, same "not enough information to book"
        # reasoning as HealthcareDomainAdapter's doctor fields. A "chair" is an
        # inanimate bookable resource, not a person, so it never carries these.
        if resource_type != "stylist":
            return

        specialisation = domain_data.get("specialisation")
        if specialisation not in SPECIALISATIONS:
            raise ValidationError(
                f"specialisation must be one of {SPECIALISATIONS}, got {specialisation!r}"
            )

        years_of_experience = domain_data.get("years_of_experience")
        if not isinstance(years_of_experience, int) or isinstance(years_of_experience, bool):
            raise ValidationError("years_of_experience must be a whole number")
        if years_of_experience < 0 or years_of_experience > 80:
            raise ValidationError("years_of_experience must be between 0 and 80")

        gender = domain_data.get("gender")
        if gender not in GENDER_OPTIONS:
            raise ValidationError(f"gender must be one of {GENDER_OPTIONS}, got {gender!r}")

    def validate_booking_domain_data(self, domain_data: dict) -> None:
        if not domain_data.get("combo"):
            return
        steps = domain_data.get("steps")
        if not steps or not isinstance(steps, list):
            raise ValidationError("a combo booking must list its steps")
        for step in steps:
            if not step.get("service") or not step.get("duration_minutes"):
                raise ValidationError("each combo step needs a service and duration_minutes")

    def required_resource_count(self, booking_domain_data: dict) -> int:
        # Beauty's combo services chain sequentially on ONE stylist/chair (see
        # create_combo_booking below) — a simultaneous multi-resource requirement isn't a
        # real Beauty concept, so this is always 1.
        return 1

    def fulfillment_type(self, booking_domain_data: dict) -> str:
        return "COMBO_SERVICE" if booking_domain_data.get("combo") else "STANDARD_SERVICE"


def create_combo_booking(resource, *, holder_ref: str, steps: list[dict], start_time, redis_client):
    """Sequential slot chaining for multi-step Beauty services — books back-to-back
    `Slot`s on `resource`, one per step, all sharing a `combo_group_id` in `domain_data`
    so they're recognizable as one logical combo booking.

    Deliberately NOT a schema change to `Booking` (still one `Slot` per `Booking`, per
    Phase 1.3) — the combo relationship is a Beauty-specific concept, expressed through
    `domain_data`, not a fork of the generic core.
    """
    adapter = BeautyDomainAdapter()
    combo_domain_data = {"combo": True, "steps": steps}
    adapter.validate_booking_domain_data(combo_domain_data)

    combo_group_id = str(uuid.uuid4())
    bookings = []
    current_start = start_time

    with transaction.atomic():
        for index, step in enumerate(steps):
            duration = dt.timedelta(minutes=step["duration_minutes"])
            slot = Slot.objects.create(
                resource=resource,
                start_time=current_start,
                end_time=current_start + duration,
                capacity_total=1,
                capacity_remaining=1,
            )
            booking = hold_slot(
                slot.id, holder_ref=holder_ref, redis_client=redis_client, ttl_seconds=900
            )
            booking.domain_data = {
                "combo": True,
                "combo_group_id": combo_group_id,
                "step_index": index,
                "service": step["service"],
            }
            booking.save(update_fields=["domain_data"])
            bookings.append(booking)
            current_start = slot.end_time

    return bookings
