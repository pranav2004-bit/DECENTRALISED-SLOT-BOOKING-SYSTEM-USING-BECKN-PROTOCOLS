"""Catalog visibility (livetracker2.md §2.2/§4.1) and the real internal catalog
representation (§2.3), both built on `inventory_core`'s `Resource`. Not the real Beckn
`search`/`on_search` wiring — that's Phase 3's job, explicitly deferred by §2.3 itself.

`Resource.owner_ref` is an opaque string, not a foreign key (inventory_core is deliberately
decoupled from any one consuming app's account model — see `shared/inventory_core/models.py`),
so "is this resource's owner active" is answered here, in BPP's own code, not inside the
shared library.

Phase 4.1 real gap found via audit before implementing: this module (and `catalog_cache.py`/
`search_service.py`) was hardcoded single-domain (Beauty) throughout — every business's
resources were pooled into one catalog regardless of what domain they actually served, which
would have silently mixed a Healthcare clinic's doctors into a Beauty search's results the
moment a second domain existed. Fixed by keying every catalog operation off
`BusinessAccount.domain_code` (§4.1's own new field) — one catalog per domain, not one
global catalog, matching how a real BPP genuinely serving multiple ONDC domains registers a
separate Participant row per domain (confirmed directly, `registry/core/models.py`'s own
`unique_subscriber_domain_type` constraint) rather than blending them.
"""

from django.conf import settings
from inventory_core.models import Resource

from .models import BusinessAccount

# Display name per domain — purely cosmetic (the `Catalog.descriptor.name` field), not a
# protocol-significant value. Falls back to a generic label for any domain_code not listed
# here rather than raising, since a genuinely unknown domain_code would already have failed
# earlier (DomainAdapter registration/lookup), not here.
_DOMAIN_DISPLAY_NAMES = {
    settings.DOMAIN_BEAUTY: "Beauty Catalog",
    settings.DOMAIN_HEALTHCARE: "Healthcare Catalog",
    settings.DOMAIN_AUTOMOTIVE: "Automotive Catalog",
}


def visible_resources(domain_code: str):
    """`Resource`s whose owning `BusinessAccount` is currently `ACTIVE` *and* serves
    `domain_code` — the set any search/catalog surface for that domain should draw from.
    A deactivated business's resources, or a different domain's resources, are excluded
    here, at the query level, not filtered ad hoc per caller.
    """
    active_owner_refs = BusinessAccount.objects.filter(
        is_active=True, domain_code=domain_code
    ).values_list("id", flat=True)
    return Resource.objects.filter(owner_ref__in=[str(pk) for pk in active_owner_refs])


def _resource_to_item(resource: Resource) -> dict:
    """A real `Item` (confirmed shape, protocol_compliance_notes_v1.1.md §F) built from a
    `Resource` — `descriptor` fields map directly since `Resource`'s own descriptive fields
    were already grounded in the same real `Descriptor.yaml` shape in Phase 1.1.

    livetracker3.md §2.2: `rating` is only included once a real aggregate-eligible rating
    exists (`resource.average_rating is not None`) — an honestly absent field, matching
    `build_catalog()`'s own established "a real schema field left out is honest; a guessed
    one wouldn't be" precedent for `fulfillments`/`payments`/`offers`, rather than a
    fabricated `0`/`"0"`. The real confirmed schema shape for `Item.rating` is the single
    scalar `Rating.yaml#/properties/value` (protocol_compliance_notes_v1.1.md §O/§F) — a
    string, so `average_rating` is serialized as one here. `rating_count` alongside it is
    this project's own additive, non-protocol field (same "project-defined, not
    protocol-confirmed" territory already established elsewhere in this file), needed
    because the real schema's single scalar can't distinguish "5.0 from one rating" from
    "5.0 from a hundred."
    """
    item = {
        "id": str(resource.id),
        "descriptor": {
            "name": resource.name,
            "code": resource.code,
            "short_desc": resource.short_desc,
            "long_desc": resource.long_desc,
        },
        "category_ids": [resource.category_id] if resource.category_id else [],
        "rateable": resource.rateable,
        "price": {
            "currency": resource.price_currency,
            "value": str(resource.price_value),
        },
    }
    if resource.average_rating is not None:
        item["rating"] = str(resource.average_rating)
        item["rating_count"] = resource.rating_count
    return item


def _business_to_provider(business: BusinessAccount) -> dict:
    """A real `Provider` (confirmed shape, §F) built from a `BusinessAccount` and its
    currently-visible `Resource`s. Only ever built for `ACTIVE` businesses with at least
    one `Resource` — an inactive or empty business simply isn't represented, the same
    "stops appearing" behavior as §2.2's `visible_resources()`, expressed at the Provider
    level here instead of a flat Resource list.

    livetracker3.md §2.2: `Provider.rating`/`rating_count` are a weighted rollup across
    this business's own already-fetched `Resource` rows (no extra query — the same rows
    already used to build `items` above) — a rating-count-weighted mean of each rated
    `Resource`'s own `average_rating`, not a separate persisted field of its own. Omitted
    entirely when none of this business's resources have any aggregate-eligible rating
    yet, same "honestly absent, not fabricated" rule as `Item.rating`."""
    resources = list(
        Resource.objects.filter(owner_ref=str(business.id)).order_by("name", "id")
    )
    items = [_resource_to_item(r) for r in resources]
    provider = {
        "id": str(business.id),
        "descriptor": {"name": business.business_name},
        "category_id": business.domain_code,
        "items": items,
    }
    rated_resources = [r for r in resources if r.average_rating is not None]
    if rated_resources:
        total_count = sum(r.rating_count for r in rated_resources)
        weighted_sum = sum(float(r.average_rating) * r.rating_count for r in rated_resources)
        provider["rating"] = f"{weighted_sum / total_count:.2f}"
        provider["rating_count"] = total_count
    return provider


def _tokenize(query_text: str) -> list[str]:
    return [word for word in query_text.strip().lower().split() if word]


def filter_catalog(catalog: dict, query_text: str | None) -> dict:
    """livetracker3.md §1.1: narrows an already-built catalog (as returned by
    `build_catalog()`/`get_cached_catalog()`) down to providers/items matching
    `query_text`, applied *after* the domain-only cache read rather than as a
    cache-key dimension — see `catalog_cache.py`'s own docstring for why per-query
    caching would explode cache cardinality for no real benefit.

    Word-tokenized, case-insensitive AND-match: every word in `query_text` must
    appear as a substring somewhere in the combined `item.descriptor.name` +
    `provider.descriptor.name` text for that item. Combining both fields (rather
    than matching each independently) is deliberate — it's what lets a query like
    "hair salon" match a business named "Salon Hair Care" even though the words
    aren't in the same order and neither field alone contains both words. Not
    fuzzy/typo-tolerant (§1.2, explicitly out of scope here).

    A missing/empty `query_text` returns `catalog` unchanged — the defined
    "browse everything" behavior for a customer who hasn't typed anything, not
    a regression from the prior always-unfiltered behavior.
    """
    if not query_text:
        return catalog
    words = _tokenize(query_text)
    if not words:
        return catalog

    filtered_providers = []
    for provider in catalog.get("providers", []):
        provider_name = provider.get("descriptor", {}).get("name", "")
        matching_items = [
            item
            for item in provider.get("items", [])
            if all(
                word in f"{item.get('descriptor', {}).get('name', '')} {provider_name}".lower()
                for word in words
            )
        ]
        if matching_items:
            filtered_providers.append({**provider, "items": matching_items})

    return {"descriptor": catalog.get("descriptor", {}), "providers": filtered_providers}


def build_catalog(domain_code: str) -> dict:
    """BPP's catalog for one domain, represented internally using the confirmed real
    `Catalog`/`Provider`/`Item` schema shapes (protocol_compliance_notes_v1.1.md §F/§G) —
    livetracker2.md §2.3, widened to be per-domain in §4.1. Not yet wired to `search`/
    `on_search` directly — `search_service.py` calls this via `catalog_cache`; this is
    purely the internal representation, proven correct against the real schema by
    `shared/testing/contract_schemas/beauty_catalog.schema.json`.

    `fulfillments`/`payments`/`offers` (real, optional `Catalog` fields) are deliberately
    omitted — no fulfillment/payment/offer data exists yet to populate them with, and a
    real schema field left out is honest; a guessed one wouldn't be.

    Deterministically ordered (`.order_by("id")` here, `.order_by("name", "id")` on each
    provider's own items in `_business_to_provider`) — without it, Postgres doesn't
    guarantee row order across repeated identical queries, so two consecutive calls with
    genuinely unchanged data could return the same providers/items in a different list
    order and compare unequal by `==`. Found live via §3.11's own catalog-cache
    reconciliation sweep, which rebuilds this fresh on a real schedule and compares it to
    the cached version — the ordering nondeterminism was making it log a "correction" on
    almost every tick even though nothing had actually changed. Harmless (the cache always
    ended up matching current reality either way) but a noisy signal, fixed by explicit
    ordering rather than left as-is.

    Only ever returns *this domain's own* businesses — a Healthcare search calling
    `build_catalog(settings.DOMAIN_HEALTHCARE)` never sees a Beauty salon's resources, and
    vice versa (§4.1's own domain-isolation requirement, proven by
    `test_catalog.py::test_two_domains_never_leak_into_each_others_catalog`).
    """
    providers = [
        _business_to_provider(business)
        for business in BusinessAccount.objects.filter(
            is_active=True, domain_code=domain_code
        ).order_by("id")
        if Resource.objects.filter(owner_ref=str(business.id)).exists()
    ]
    return {
        "descriptor": {"name": _DOMAIN_DISPLAY_NAMES.get(domain_code, "Catalog")},
        "providers": providers,
    }
