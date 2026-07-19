"""Phase 2.3 Test Gate (livetracker2.md §2.3) for BPP's internal Beauty catalog
representation.

FUNC: the internal catalog representation round-trips correctly against the confirmed
real schema field names.
"""

import json
from pathlib import Path

import jsonschema
import pytest
from django.contrib.auth import get_user_model
from inventory_core.models import Resource

from core.catalog import build_beauty_catalog

BusinessAccount = get_user_model()

SCHEMA_PATH = (
    Path(__file__).resolve().parents[3]
    / "shared"
    / "testing"
    / "contract_schemas"
    / "beauty_catalog.schema.json"
)


def _schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text())


@pytest.mark.django_db
def test_empty_catalog_matches_the_confirmed_schema():
    catalog = build_beauty_catalog()

    assert catalog == {"descriptor": {"name": "Beauty Catalog"}, "providers": []}
    jsonschema.validate(instance=catalog, schema=_schema())


@pytest.mark.django_db
def test_catalog_round_trips_real_business_and_resource_data():
    business = BusinessAccount.objects.create_user(
        contact="salon@example.com", business_name="Glow Salon", password="unused-in-this-test"
    )
    Resource.objects.create(
        owner_ref=str(business.id),
        name="Stylist A",
        code="STY-A",
        short_desc="Senior stylist",
        category_id="ONDC:RET13",
    )

    catalog = build_beauty_catalog()
    jsonschema.validate(instance=catalog, schema=_schema())

    assert len(catalog["providers"]) == 1
    provider = catalog["providers"][0]
    assert provider["id"] == str(business.id)
    assert provider["descriptor"]["name"] == "Glow Salon"
    assert provider["category_id"] == "ONDC:RET13"

    assert len(provider["items"]) == 1
    item = provider["items"][0]
    assert item["descriptor"]["name"] == "Stylist A"
    assert item["descriptor"]["code"] == "STY-A"
    assert item["category_ids"] == ["ONDC:RET13"]
    assert item["rateable"] is True


@pytest.mark.django_db
def test_inactive_business_is_excluded_from_the_catalog():
    business = BusinessAccount.objects.create_user(
        contact="salon@example.com", business_name="Glow Salon", password="unused-in-this-test"
    )
    Resource.objects.create(owner_ref=str(business.id), name="Stylist A")
    BusinessAccount.objects.filter(id=business.id).update(is_active=False)

    catalog = build_beauty_catalog()

    assert catalog["providers"] == []


@pytest.mark.django_db
def test_business_with_no_resources_is_excluded_from_the_catalog():
    BusinessAccount.objects.create_user(
        contact="salon@example.com", business_name="Glow Salon", password="unused-in-this-test"
    )

    catalog = build_beauty_catalog()

    assert catalog["providers"] == []


@pytest.mark.django_db
def test_a_malformed_catalog_fails_contract_validation():
    """Proves the schema actually catches non-conformance, not just passes trivially —
    the same NEG discipline as shared/testing/test_contract_reference.py's reference
    pattern."""
    catalog = build_beauty_catalog()
    del catalog["descriptor"]

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=catalog, schema=_schema())
