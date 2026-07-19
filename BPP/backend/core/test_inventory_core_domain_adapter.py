"""Phase 1.5 Test Gate (livetracker2.md §1.5) for shared/inventory_core's Domain Adapter
Interface — extension point only, no real domain adapter built here (that's Phase 2's Beauty
adapter). Exercised here for the same reason as Phase 1.1-1.4's tests — a Django app's tests
need a real settings module + database, and BPP is its only current consumer.

FUNC: a stub/mock domain adapter exercises the interface end-to-end.
"""

import pytest
from django.core.exceptions import ValidationError
from inventory_core import domain_adapter as domain_adapter_module
from inventory_core.domain_adapter import DomainAdapter, get_adapter, register_adapter
from inventory_core.models import Booking, Resource, Slot

TEST_DOMAIN = "TEST:STUB"


class StubDomainAdapter(DomainAdapter):
    """A minimal stub used only to prove the interface itself works end-to-end — not a real
    domain implementation. Real ones (Beauty in Phase 2, Healthcare/Automotive in Phase 4) live
    in their own consuming app's code, not in shared/inventory_core."""

    domain_code = TEST_DOMAIN

    def validate_resource_domain_data(self, domain_data: dict) -> None:
        if "consultation_type" not in domain_data:
            raise ValidationError("consultation_type is required for this stub domain.")

    def validate_booking_domain_data(self, domain_data: dict) -> None:
        if domain_data.get("combo") and "steps" not in domain_data:
            raise ValidationError("a combo booking must list its steps.")

    def required_resource_count(self, booking_domain_data: dict) -> int:
        if not booking_domain_data.get("combo"):
            return 1
        return len(booking_domain_data.get("steps", []))

    def fulfillment_type(self, booking_domain_data: dict) -> str:
        return "COMBO_SERVICE" if booking_domain_data.get("combo") else "STANDARD"


@pytest.fixture
def stub_adapter():
    adapter = StubDomainAdapter()
    register_adapter(TEST_DOMAIN, adapter)
    yield adapter
    domain_adapter_module._REGISTRY.pop(TEST_DOMAIN, None)


@pytest.fixture
def resource(db):
    return Resource.objects.create(owner_ref="biz-1", name="Stylist A")


def test_domain_adapter_is_abstract_and_cannot_be_instantiated_directly():
    with pytest.raises(TypeError):
        DomainAdapter()


def test_get_adapter_raises_for_unregistered_domain_code():
    with pytest.raises(LookupError):
        get_adapter("TEST:NEVER_REGISTERED")


@pytest.mark.django_db
def test_registered_stub_adapter_is_retrievable_by_domain_code(stub_adapter):
    assert get_adapter(TEST_DOMAIN) is stub_adapter


@pytest.mark.django_db
def test_resource_domain_data_validated_by_the_active_adapter(resource, stub_adapter):
    resource.domain_data = {"consultation_type": "in_person"}
    stub_adapter.validate_resource_domain_data(resource.domain_data)  # does not raise

    resource.domain_data = {}
    with pytest.raises(ValidationError):
        stub_adapter.validate_resource_domain_data(resource.domain_data)


@pytest.mark.django_db
def test_booking_domain_data_and_multi_resource_hook_for_a_combo_service(resource, stub_adapter):
    slot = Slot.objects.create(
        resource=resource,
        start_time="2026-08-01T09:00:00Z",
        end_time="2026-08-01T09:30:00Z",
        capacity_total=1,
        capacity_remaining=1,
    )
    booking = Booking.objects.create(
        slot=slot,
        holder_ref="cust-1",
        domain_data={"combo": True, "steps": ["haircut", "coloring"]},
    )

    stub_adapter.validate_booking_domain_data(booking.domain_data)  # does not raise
    assert stub_adapter.required_resource_count(booking.domain_data) == 2
    assert stub_adapter.fulfillment_type(booking.domain_data) == "COMBO_SERVICE"


@pytest.mark.django_db
def test_booking_domain_data_rejects_a_combo_booking_missing_its_steps(resource, stub_adapter):
    slot = Slot.objects.create(
        resource=resource,
        start_time="2026-08-01T09:00:00Z",
        end_time="2026-08-01T09:30:00Z",
        capacity_total=1,
        capacity_remaining=1,
    )
    booking = Booking.objects.create(slot=slot, holder_ref="cust-1", domain_data={"combo": True})

    with pytest.raises(ValidationError):
        stub_adapter.validate_booking_domain_data(booking.domain_data)


@pytest.mark.django_db
def test_standard_non_combo_booking_needs_exactly_one_resource(resource, stub_adapter):
    slot = Slot.objects.create(
        resource=resource,
        start_time="2026-08-01T09:00:00Z",
        end_time="2026-08-01T09:30:00Z",
        capacity_total=1,
        capacity_remaining=1,
    )
    booking = Booking.objects.create(slot=slot, holder_ref="cust-1")

    assert stub_adapter.required_resource_count(booking.domain_data) == 1
    assert stub_adapter.fulfillment_type(booking.domain_data) == "STANDARD"
