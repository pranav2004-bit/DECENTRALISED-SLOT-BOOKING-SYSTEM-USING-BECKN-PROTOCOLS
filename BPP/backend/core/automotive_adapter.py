"""The real Automotive domain adapter (livetracker2.md §4.2) — `Resource` = bay, the
real physical capacity constraint a garage books against. A mechanic is staff, not a
Resource: a real customer books a time slot at a garage for a service, not a named
mechanic (unlike Beauty, where a customer often does pick their stylist) — the
existing Staff feature (StaffAccount + assigned_resource_ids, already used by
CareNest/StyleNest) already covers "a mechanic is staff assigned to a bay" with no
automotive-specific code needed.

Registered with `inventory_core.domain_adapter`'s registry at app startup
(`core/apps.py`'s `ready()`), keyed by this project's own Automotive domain code
(`settings.DOMAIN_AUTOMOTIVE`, `BECKN:AUTO01` — see
`protocol_compliance_notes_v1.1.md`'s "Remaining Open Items" for why no genuine
ONDC code was claimed here, unlike Healthcare's real `ONDC:SRV13`).
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from inventory_core.domain_adapter import DomainAdapter

RESOURCE_TYPES = ("bay",)


class AutomotiveDomainAdapter(DomainAdapter):
    domain_code = settings.DOMAIN_AUTOMOTIVE

    def validate_resource_domain_data(self, domain_data: dict) -> None:
        resource_type = domain_data.get("resource_type")
        if resource_type not in RESOURCE_TYPES:
            raise ValidationError(
                f"resource_type must be one of {RESOURCE_TYPES}, got {resource_type!r}"
            )

    def validate_booking_domain_data(self, domain_data: dict) -> None:
        # No customer-supplied domain_data to validate here — a bay booking carries
        # no Automotive-specific fields, same as Healthcare's plain doctor visit.
        pass

    def required_resource_count(self, booking_domain_data: dict) -> int:
        # Always 1 — a bay is the only Resource Automotive books against now (a
        # mechanic is staff, not a Resource); same reasoning as Beauty's/
        # Healthcare's own always-1, not the simultaneous bay+mechanic pairing
        # this adapter used to require.
        return 1

    def fulfillment_type(self, booking_domain_data: dict) -> str:
        # Automotive is where fulfillment *tracking* is actually meaningful
        # (§4.2's own tracker wording) — a single real fulfillment type reflecting
        # that a technician is dispatched to work on the vehicle, unlike Beauty's
        # combo/standard split or Healthcare's in-person/tele-consultation split.
        return "TECHNICIAN_DISPATCH"
