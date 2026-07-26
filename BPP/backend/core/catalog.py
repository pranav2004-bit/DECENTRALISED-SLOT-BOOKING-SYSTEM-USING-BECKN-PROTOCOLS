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
    were already grounded in the same real `Descriptor.yaml` shape in Phase 1.1."""
    return {
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


def _business_to_provider(business: BusinessAccount) -> dict:
    """A real `Provider` (confirmed shape, §F) built from a `BusinessAccount` and its
    currently-visible `Resource`s. Only ever built for `ACTIVE` businesses with at least
    one `Resource` — an inactive or empty business simply isn't represented, the same
    "stops appearing" behavior as §2.2's `visible_resources()`, expressed at the Provider
    level here instead of a flat Resource list."""
    items = [
        _resource_to_item(r)
        for r in Resource.objects.filter(owner_ref=str(business.id)).order_by("name", "id")
    ]
    return {
        "id": str(business.id),
        "descriptor": {"name": business.business_name},
        "category_id": business.domain_code,
        "items": items,
    }


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
