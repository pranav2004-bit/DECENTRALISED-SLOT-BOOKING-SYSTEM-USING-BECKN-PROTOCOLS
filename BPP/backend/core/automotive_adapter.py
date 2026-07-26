"""The real Automotive domain adapter (livetracker2.md §4.2) — `Resource` = bay +
mechanic, genuinely two separate resources needed *simultaneously* for one real
booking (the multi-resource case flagged early in this tracker's own planning, and
the reason `shared/inventory_core/reservation.py` gained
`hold_multi_resource_booking()`/`find_group_bookings()` this phase, not Beauty's own
`combo_group_id` sequential-steps pattern — bay+mechanic overlap in *time*, they
don't chain one after another).

Registered with `inventory_core.domain_adapter`'s registry at app startup
(`core/apps.py`'s `ready()`), keyed by this project's own Automotive domain code
(`settings.DOMAIN_AUTOMOTIVE`, `BECKN:AUTO01` — see
`protocol_compliance_notes_v1.1.md`'s "Remaining Open Items" for why no genuine
ONDC code was claimed here, unlike Healthcare's real `ONDC:SRV13`).
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from inventory_core.domain_adapter import DomainAdapter
from inventory_core.models import Resource, Slot

RESOURCE_TYPES = ("bay", "mechanic")
_COMPLEMENT = {"bay": "mechanic", "mechanic": "bay"}


class AutomotiveDomainAdapter(DomainAdapter):
    domain_code = settings.DOMAIN_AUTOMOTIVE

    def validate_resource_domain_data(self, domain_data: dict) -> None:
        resource_type = domain_data.get("resource_type")
        if resource_type not in RESOURCE_TYPES:
            raise ValidationError(
                f"resource_type must be one of {RESOURCE_TYPES}, got {resource_type!r}"
            )

    def validate_booking_domain_data(self, domain_data: dict) -> None:
        # No customer-supplied domain_data to validate here — unlike Beauty's combo
        # flag or Healthcare's consultation_type, the bay+mechanic pairing is a
        # server-side reservation-layer concern (`select_service.py` resolves the
        # pair, `hold_multi_resource_booking()` holds them together), not something
        # the customer expresses on the wire.
        pass

    def required_resource_count(self, booking_domain_data: dict) -> int:
        # Always 2 — a real Automotive service genuinely needs a bay *and* a
        # mechanic simultaneously available, unlike Beauty's combo services
        # (optional, sequential, single-resource) or Healthcare (always 1).
        return 2

    def fulfillment_type(self, booking_domain_data: dict) -> str:
        # Automotive is where fulfillment *tracking* is actually meaningful
        # (§4.2's own tracker wording) — a single real fulfillment type reflecting
        # that a technician is dispatched to work on the vehicle, unlike Beauty's
        # combo/standard split or Healthcare's in-person/tele-consultation split.
        return "TECHNICIAN_DISPATCH"


def find_paired_resource(resource: Resource, requested_time) -> tuple[Resource, Slot] | None:
    """§4.2's pairing logic behind "genuine multi-resource booking support": given the
    `Resource` + time a customer selected (a bay or a mechanic), finds the
    complementary `resource_type` (mechanic<->bay) from the *same* business
    (`owner_ref`) with an available `Slot` at that exact same time.

    Returns `(paired_resource, paired_slot)` on success, or `None` if no compatible
    resource+slot exists — the real "only one of the two required resources is
    available" failure case §4.2's own Test Gate names by name.
    `select_service.py` treats this identically to any other `SLOT_UNAVAILABLE`,
    not a special case — a customer never needs to know *why* nothing was
    available, only that it wasn't."""
    resource_type = (resource.domain_data or {}).get("resource_type")
    complement = _COMPLEMENT.get(resource_type)
    if complement is None:
        return None

    candidates = (
        Resource.objects.filter(owner_ref=resource.owner_ref).exclude(id=resource.id).order_by("id")
    )
    for candidate in candidates:
        if (candidate.domain_data or {}).get("resource_type") != complement:
            continue
        slot = (
            Slot.objects.filter(
                resource=candidate,
                start_time=requested_time,
                status__in=[Slot.Status.AVAILABLE, Slot.Status.HELD],
                capacity_remaining__gte=1,
            )
            .order_by("id")
            .first()
        )
        if slot is not None:
            return candidate, slot
    return None
