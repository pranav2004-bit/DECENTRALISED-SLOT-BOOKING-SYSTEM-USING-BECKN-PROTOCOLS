"""Minimal catalog-visibility query (livetracker2.md §2.2's "a deactivated business
account's inventory stops appearing in search" Test Gate requirement). Not the real
Beckn `search`/`on_search` wiring — that's Phase 3's job (§2.3 explicitly defers it).

`Resource.owner_ref` is an opaque string, not a foreign key (inventory_core is deliberately
decoupled from any one consuming app's account model — see `shared/inventory_core/models.py`),
so "is this resource's owner active" is answered here, in BPP's own code, not inside the
shared library.
"""

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
