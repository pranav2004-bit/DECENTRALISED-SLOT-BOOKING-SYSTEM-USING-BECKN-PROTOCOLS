"""The real Healthcare domain adapter (livetracker2.md §4.1), built on the same
`inventory_core` extension-point interface as Beauty (§2.2) — `Resource` = doctor, with
`Fulfillment.type` variants for in-person vs. tele-consultation, per `BPP_details_v1.1.md`'s
"Supported Service Domains" note.

Registered with `inventory_core.domain_adapter`'s registry at app startup (`core/apps.py`'s
`ready()`), keyed by this project's own Healthcare domain code (`settings.DOMAIN_HEALTHCARE`,
`ONDC:SRV13` — see `protocol_compliance_notes_v1.1.md`'s "Remaining Open Items" for how that
value was confirmed).

No multi-resource booking here — that concept is Automotive's (§4.2: bay *and* mechanic
simultaneously), not a real Healthcare requirement, so `required_resource_count` is always 1,
matching Beauty's own reasoning for the same field.
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from inventory_core.domain_adapter import DomainAdapter

RESOURCE_TYPES = ("doctor",)
CONSULTATION_TYPES = ("in_person", "tele_consultation")
GENDER_OPTIONS = ("male", "female", "other")
# Mirrored in BPP/web/app/resources/page.tsx's own SPECIALISATIONS constant
# (ADR-0004: duplicated, not shared across the frontend/backend language boundary,
# same as GENDER_OPTIONS/RESOURCE_TYPES already are). A fixed list, not free text —
# real ONDC health listings need comparable, filterable specialisation values across
# different clinics, not each business inventing its own spelling.
SPECIALISATIONS = (
    "General Medicine / General Physician",
    "Cardiology",
    "Orthopedics",
    "Gynecology & Obstetrics",
    "Pediatrics",
    "Oncology",
    "Neurology & Neurosurgery",
    "Gastroenterology",
    "Nephrology",
    "Urology",
    "Pulmonology",
    "Dermatology",
    "Ophthalmology",
    "ENT (Otolaryngology)",
    "Endocrinology",
    "Rheumatology",
    "Hematology",
    "Psychiatry",
    "General Surgery",
    "Emergency Medicine",
)


class HealthcareDomainAdapter(DomainAdapter):
    domain_code = settings.DOMAIN_HEALTHCARE

    def validate_resource_domain_data(self, domain_data: dict) -> None:
        resource_type = domain_data.get("resource_type")
        if resource_type not in RESOURCE_TYPES:
            raise ValidationError(
                f"resource_type must be one of {RESOURCE_TYPES}, got {resource_type!r}"
            )

        # Doctor profile fields — required (not just validated-if-present): a doctor
        # listing without these is the exact "not enough information to book"
        # complaint real ONDC health listings have been flagged for, so this domain
        # doesn't accept a half-filled one the way the generic fields elsewhere do.
        specialisation = domain_data.get("specialisation")
        if specialisation not in SPECIALISATIONS:
            raise ValidationError(
                f"specialisation must be one of {SPECIALISATIONS}, got {specialisation!r}"
            )

        education = (domain_data.get("education") or "").strip()
        if not education:
            raise ValidationError("education is required")
        if len(education) > 255:
            raise ValidationError("education is too long (max 255)")

        years_of_experience = domain_data.get("years_of_experience")
        if not isinstance(years_of_experience, int) or isinstance(years_of_experience, bool):
            raise ValidationError("years_of_experience must be a whole number")
        if years_of_experience < 0 or years_of_experience > 80:
            raise ValidationError("years_of_experience must be between 0 and 80")

        gender = domain_data.get("gender")
        if gender not in GENDER_OPTIONS:
            raise ValidationError(f"gender must be one of {GENDER_OPTIONS}, got {gender!r}")

    def validate_booking_domain_data(self, domain_data: dict) -> None:
        consultation_type = domain_data.get("consultation_type", "in_person")
        if consultation_type not in CONSULTATION_TYPES:
            raise ValidationError(
                f"consultation_type must be one of {CONSULTATION_TYPES}, "
                f"got {consultation_type!r}"
            )

    def required_resource_count(self, booking_domain_data: dict) -> int:
        return 1

    def fulfillment_type(self, booking_domain_data: dict) -> str:
        consultation_type = booking_domain_data.get("consultation_type", "in_person")
        if consultation_type == "tele_consultation":
            return "TELE_CONSULTATION"
        return "IN_PERSON_CONSULTATION"
