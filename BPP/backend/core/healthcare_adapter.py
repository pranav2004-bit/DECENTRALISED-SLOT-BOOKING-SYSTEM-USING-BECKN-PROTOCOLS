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


class HealthcareDomainAdapter(DomainAdapter):
    domain_code = settings.DOMAIN_HEALTHCARE

    def validate_resource_domain_data(self, domain_data: dict) -> None:
        resource_type = domain_data.get("resource_type")
        if resource_type not in RESOURCE_TYPES:
            raise ValidationError(
                f"resource_type must be one of {RESOURCE_TYPES}, got {resource_type!r}"
            )

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
