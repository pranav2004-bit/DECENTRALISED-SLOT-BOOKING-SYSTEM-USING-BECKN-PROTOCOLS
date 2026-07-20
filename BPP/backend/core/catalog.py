"""Catalog visibility (livetracker2.md §2.2) and the real internal Beauty catalog
representation (§2.3), both built on `inventory_core`'s `Resource`. Not the real Beckn
`search`/`on_search` wiring — that's Phase 3's job, explicitly deferred by §2.3 itself.

`Resource.owner_ref` is an opaque string, not a foreign key (inventory_core is deliberately
decoupled from any one consuming app's account model — see `shared/inventory_core/models.py`),
so "is this resource's owner active" is answered here, in BPP's own code, not inside the
shared library.
"""

from django.conf import settings
from inventory_core.models import Resource

from .models import BusinessAccount


def visible_resources():
    """`Resource`s whose owning `BusinessAccount` is currently `ACTIVE` — the set any
    future real search/catalog surface should draw from. A deactivated business's
    resources are excluded here, at the query level, not filtered ad hoc per caller.
    """
    active_owner_refs = BusinessAccount.objects.filter(is_active=True).values_list(
        "id", flat=True
    )
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
        for r in Resource.objects.filter(owner_ref=str(business.id)).order_by("name")
    ]
    return {
        "id": str(business.id),
        "descriptor": {"name": business.business_name},
        "category_id": settings.DOMAIN_BEAUTY,
        "items": items,
    }


def build_beauty_catalog() -> dict:
    """BPP's Beauty catalog, represented internally using the confirmed real `Catalog`/
    `Provider`/`Item` schema shapes (protocol_compliance_notes_v1.1.md §F/§G) — livetracker2.md
    §2.3. Not yet wired to `search`/`on_search` — that's Phase 3's job; this is purely the
    internal representation, proven correct against the real schema by
    `shared/testing/contract_schemas/beauty_catalog.schema.json`.

    `fulfillments`/`payments`/`offers` (real, optional `Catalog` fields) are deliberately
    omitted — no fulfillment/payment/offer data exists yet to populate them with, and a
    real schema field left out is honest; a guessed one wouldn't be.
    """
    providers = [
        _business_to_provider(business)
        for business in BusinessAccount.objects.filter(is_active=True)
        if Resource.objects.filter(owner_ref=str(business.id)).exists()
    ]
    return {
        "descriptor": {"name": "Beauty Catalog"},
        "providers": providers,
    }
