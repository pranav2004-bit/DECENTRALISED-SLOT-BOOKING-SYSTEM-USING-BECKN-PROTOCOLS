"""Phase 2.3 Test Gate (livetracker2.md §2.3) for BPP's internal catalog representation,
widened per-domain in §4.1.

FUNC: the internal catalog representation round-trips correctly against the confirmed
real schema shape (Beauty's shape is what the fixed schema file below actually checks;
Healthcare uses the same generic `Catalog`/`Provider`/`Item` shape, proven directly by
assertion rather than a second schema file, since the shape itself is domain-agnostic —
only the data inside it differs).

§4.1: also proves domain isolation — a Healthcare business's resources must never appear
in a Beauty catalog build, and vice versa. Referenced by name from `catalog.py`'s own
`build_catalog()` docstring.
"""

import json
from pathlib import Path

import jsonschema
import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from inventory_core.models import Resource

from core.catalog import build_catalog, filter_catalog

BusinessAccount = get_user_model()

# Test fixture value, not a real credential.
TEST_PASSWORD = "unused-in-this-test"  # pragma: allowlist secret

BEAUTY = settings.DOMAIN_BEAUTY
HEALTHCARE = settings.DOMAIN_HEALTHCARE

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
    catalog = build_catalog(BEAUTY)

    assert catalog == {"descriptor": {"name": "Beauty Catalog"}, "providers": []}
    jsonschema.validate(instance=catalog, schema=_schema())


@pytest.mark.django_db
def test_catalog_round_trips_real_business_and_resource_data():
    business = BusinessAccount.objects.create_user(
        contact="salon@example.com",
        business_name="Glow Salon",
        password=TEST_PASSWORD,
        domain_code=BEAUTY,
    )
    Resource.objects.create(
        owner_ref=str(business.id),
        name="Stylist A",
        code="STY-A",
        short_desc="Senior stylist",
        category_id="ONDC:RET13",
        price_currency="INR",
        price_value="750.00",
    )

    catalog = build_catalog(BEAUTY)
    jsonschema.validate(instance=catalog, schema=_schema())

    assert len(catalog["providers"]) == 1
    provider = catalog["providers"][0]
    assert provider["id"] == str(business.id)
    assert provider["descriptor"]["name"] == "Glow Salon"
    assert provider["category_id"] == BEAUTY

    assert len(provider["items"]) == 1
    item = provider["items"][0]
    assert item["descriptor"]["name"] == "Stylist A"
    assert item["descriptor"]["code"] == "STY-A"
    assert item["category_ids"] == ["ONDC:RET13"]
    assert item["rateable"] is True
    assert item["price"] == {"currency": "INR", "value": "750.00"}


@pytest.mark.django_db
def test_catalog_item_uses_the_default_price_when_none_set():
    business = BusinessAccount.objects.create_user(
        contact="salon2@example.com",
        business_name="Default Price Salon",
        password=TEST_PASSWORD,
        domain_code=BEAUTY,
    )
    Resource.objects.create(owner_ref=str(business.id), name="Stylist B")

    catalog = build_catalog(BEAUTY)
    jsonschema.validate(instance=catalog, schema=_schema())

    item = catalog["providers"][0]["items"][0]
    assert item["price"] == {"currency": "INR", "value": "0.00"}


@pytest.mark.django_db
def test_inactive_business_is_excluded_from_the_catalog():
    business = BusinessAccount.objects.create_user(
        contact="salon@example.com",
        business_name="Glow Salon",
        password=TEST_PASSWORD,
        domain_code=BEAUTY,
    )
    Resource.objects.create(owner_ref=str(business.id), name="Stylist A")
    BusinessAccount.objects.filter(id=business.id).update(is_active=False)

    catalog = build_catalog(BEAUTY)

    assert catalog["providers"] == []


@pytest.mark.django_db
def test_business_with_no_resources_is_excluded_from_the_catalog():
    BusinessAccount.objects.create_user(
        contact="salon@example.com",
        business_name="Glow Salon",
        password=TEST_PASSWORD,
        domain_code=BEAUTY,
    )

    catalog = build_catalog(BEAUTY)

    assert catalog["providers"] == []


@pytest.mark.django_db
def test_consecutive_builds_against_unchanged_data_return_identically_ordered_results():
    """A real ordering-determinism regression test — before this fix, `build_catalog()`'s
    two queries (businesses, and each business's own resources) had no explicit
    `.order_by()`, so Postgres didn't guarantee the same row order across repeated
    identical queries: two consecutive calls against genuinely unchanged data could
    return the same providers/items in a different list order and compare unequal by
    `==`. Found live via §3.11's catalog-cache reconciliation sweep logging a false
    "corrected" on almost every tick. Multiple businesses/resources here to give any
    real nondeterminism room to actually surface, not just one of each."""
    for i in range(5):
        business = BusinessAccount.objects.create_user(
            contact=f"salon{i}@example.com",
            business_name=f"Salon {i}",
            password=TEST_PASSWORD,
            domain_code=BEAUTY,
        )
        for j in range(3):
            Resource.objects.create(owner_ref=str(business.id), name=f"Stylist {j}")

    first = build_catalog(BEAUTY)
    second = build_catalog(BEAUTY)
    third = build_catalog(BEAUTY)

    assert first == second == third


@pytest.mark.django_db
def test_a_malformed_catalog_fails_contract_validation():
    """Proves the schema actually catches non-conformance, not just passes trivially —
    the same NEG discipline as shared/testing/test_contract_reference.py's reference
    pattern."""
    catalog = build_catalog(BEAUTY)
    del catalog["descriptor"]

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=catalog, schema=_schema())


@pytest.mark.django_db
def test_two_domains_never_leak_into_each_others_catalog():
    """§4.1's own domain-isolation requirement, referenced by name from `catalog.py`'s
    `build_catalog()` docstring: a Healthcare clinic's resources must never appear in a
    Beauty catalog build, and vice versa, even though both businesses coexist in the
    same `BusinessAccount`/`Resource` tables."""
    salon = BusinessAccount.objects.create_user(
        contact="salon@example.com",
        business_name="Glow Salon",
        password=TEST_PASSWORD,
        domain_code=BEAUTY,
    )
    Resource.objects.create(
        owner_ref=str(salon.id), name="Stylist A", domain_data={"resource_type": "stylist"}
    )

    clinic = BusinessAccount.objects.create_user(
        contact="clinic@example.com",
        business_name="City Clinic",
        password=TEST_PASSWORD,
        domain_code=HEALTHCARE,
    )
    Resource.objects.create(
        owner_ref=str(clinic.id), name="Dr. Rao", domain_data={"resource_type": "doctor"}
    )

    beauty_catalog = build_catalog(BEAUTY)
    healthcare_catalog = build_catalog(HEALTHCARE)

    assert beauty_catalog["descriptor"]["name"] == "Beauty Catalog"
    assert [p["descriptor"]["name"] for p in beauty_catalog["providers"]] == ["Glow Salon"]

    assert healthcare_catalog["descriptor"]["name"] == "Healthcare Catalog"
    assert [p["descriptor"]["name"] for p in healthcare_catalog["providers"]] == ["City Clinic"]


@pytest.mark.django_db
def test_catalog_omits_rating_for_an_unrated_resource_and_provider():
    """livetracker3.md §2.2: an honestly absent field (no `rating` key at all), not a
    fabricated `0`/`"0"` — matching build_catalog()'s own established precedent for
    fulfillments/payments/offers."""
    business = BusinessAccount.objects.create_user(
        contact="salon@example.com",
        business_name="Glow Salon",
        password=TEST_PASSWORD,
        domain_code=BEAUTY,
    )
    Resource.objects.create(owner_ref=str(business.id), name="Stylist A")

    catalog = build_catalog(BEAUTY)
    jsonschema.validate(instance=catalog, schema=_schema())

    provider = catalog["providers"][0]
    assert "rating" not in provider
    assert "rating_count" not in provider
    assert "rating" not in provider["items"][0]
    assert "rating_count" not in provider["items"][0]


@pytest.mark.django_db
def test_catalog_surfaces_a_resources_real_rating_aggregate():
    """livetracker3.md §2.2 Test Gate: a real aggregate is reflected in the next
    catalog build — Item.rating is the real Rating.yaml#/properties/value scalar
    (a string), rating_count is this project's own additive field alongside it."""
    business = BusinessAccount.objects.create_user(
        contact="salon@example.com",
        business_name="Glow Salon",
        password=TEST_PASSWORD,
        domain_code=BEAUTY,
    )
    resource = Resource.objects.create(owner_ref=str(business.id), name="Stylist A")
    resource.average_rating = "4.33"
    resource.rating_count = 3
    resource.save(update_fields=["average_rating", "rating_count"])

    catalog = build_catalog(BEAUTY)
    jsonschema.validate(instance=catalog, schema=_schema())

    provider = catalog["providers"][0]
    item = provider["items"][0]
    assert item["rating"] == "4.33"
    assert item["rating_count"] == 3
    # Provider rollup: one rated resource, so the weighted rollup equals its own average.
    assert provider["rating"] == "4.33"
    assert provider["rating_count"] == 3


@pytest.mark.django_db
def test_catalog_provider_rating_is_a_count_weighted_rollup_across_its_resources():
    """A business with two rated resources of different weight gets a real
    count-weighted mean, not a naive average of the two averages."""
    business = BusinessAccount.objects.create_user(
        contact="salon@example.com",
        business_name="Glow Salon",
        password=TEST_PASSWORD,
        domain_code=BEAUTY,
    )
    r1 = Resource.objects.create(owner_ref=str(business.id), name="Stylist A")
    r1.average_rating, r1.rating_count = "5.00", 1
    r1.save(update_fields=["average_rating", "rating_count"])
    r2 = Resource.objects.create(owner_ref=str(business.id), name="Stylist B")
    r2.average_rating, r2.rating_count = "3.00", 9
    r2.save(update_fields=["average_rating", "rating_count"])
    # An unrated third resource must not pull the rollup toward zero.
    Resource.objects.create(owner_ref=str(business.id), name="Stylist C")

    catalog = build_catalog(BEAUTY)

    provider = catalog["providers"][0]
    # (5*1 + 3*9) / 10 = 3.20
    assert provider["rating"] == "3.20"
    assert provider["rating_count"] == 10


def _seed_two_stylists():
    """A real catalog with one provider ("Glow Salon") offering two named resources —
    one that should match a "Priya" query, one that shouldn't."""
    business = BusinessAccount.objects.create_user(
        contact="salon@example.com",
        business_name="Glow Salon",
        password=TEST_PASSWORD,
        domain_code=BEAUTY,
    )
    Resource.objects.create(owner_ref=str(business.id), name="Senior Stylist - Priya")
    Resource.objects.create(owner_ref=str(business.id), name="Junior Stylist - Anjali")
    return business


@pytest.mark.django_db
def test_filter_catalog_matches_only_the_resource_whose_name_contains_the_query():
    """livetracker3.md §1.1 Test Gate: a real search for an existing stylist's
    descriptor name returns only that resource, not the full provider catalog."""
    _seed_two_stylists()
    catalog = build_catalog(BEAUTY)

    filtered = filter_catalog(catalog, "Priya")

    assert len(filtered["providers"]) == 1
    items = filtered["providers"][0]["items"]
    assert [item["descriptor"]["name"] for item in items] == ["Senior Stylist - Priya"]


@pytest.mark.django_db
def test_filter_catalog_match_is_case_insensitive():
    _seed_two_stylists()
    catalog = build_catalog(BEAUTY)

    filtered = filter_catalog(catalog, "priya")

    assert [item["descriptor"]["name"] for item in filtered["providers"][0]["items"]] == [
        "Senior Stylist - Priya"
    ]


@pytest.mark.django_db
def test_filter_catalog_with_nonsense_query_returns_zero_providers_not_an_error():
    _seed_two_stylists()
    catalog = build_catalog(BEAUTY)

    filtered = filter_catalog(catalog, "xyzzy-no-such-thing")

    assert filtered["providers"] == []
    assert filtered["descriptor"] == catalog["descriptor"]


@pytest.mark.django_db
def test_filter_catalog_with_empty_query_returns_the_full_catalog_unchanged():
    _seed_two_stylists()
    catalog = build_catalog(BEAUTY)

    assert filter_catalog(catalog, "") == catalog
    assert filter_catalog(catalog, None) == catalog
    assert filter_catalog(catalog, "   ") == catalog


@pytest.mark.django_db
def test_filter_catalog_multiword_query_matches_regardless_of_word_order():
    """The exact case §1.1 was written to fix: a literal-substring match on the whole
    query string would miss this (words in a different order), but a word-tokenized
    AND-match against the combined item+provider descriptor text finds it."""
    business = BusinessAccount.objects.create_user(
        contact="salon@example.com",
        business_name="Salon Hair Care",
        password=TEST_PASSWORD,
        domain_code=BEAUTY,
    )
    Resource.objects.create(owner_ref=str(business.id), name="Basic Cut")
    catalog = build_catalog(BEAUTY)

    filtered = filter_catalog(catalog, "hair salon")

    assert len(filtered["providers"]) == 1
    assert filtered["providers"][0]["descriptor"]["name"] == "Salon Hair Care"


@pytest.mark.django_db
def test_filter_catalog_excludes_a_provider_whose_no_items_match():
    salon = BusinessAccount.objects.create_user(
        contact="salon@example.com",
        business_name="Glow Salon",
        password=TEST_PASSWORD,
        domain_code=BEAUTY,
    )
    Resource.objects.create(owner_ref=str(salon.id), name="Stylist A")

    clinic = BusinessAccount.objects.create_user(
        contact="clinic@example.com",
        business_name="City Clinic",
        password=TEST_PASSWORD,
        domain_code=BEAUTY,
    )
    Resource.objects.create(owner_ref=str(clinic.id), name="Dr. Rao")
    catalog = build_catalog(BEAUTY)

    filtered = filter_catalog(catalog, "Stylist")

    assert [p["descriptor"]["name"] for p in filtered["providers"]] == ["Glow Salon"]
