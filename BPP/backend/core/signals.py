"""Catalog cache invalidation signal handlers (livetracker2.md §3.8, widened per-domain in
§4.1) — catches `BusinessAccount` changes made outside application code (Django admin, per
§2.2's own established `is_active` toggle mechanism), which no view function ever sees.
Connected in `apps.py`'s `ready()`, not imported at module scope elsewhere, matching
Django's own documented signal-registration convention (avoids duplicate connections
under autoreload).

§4.1: invalidates only the saved/deleted `instance`'s own `domain_code` cache, not every
domain's — an admin edit to a Healthcare business must never touch Beauty's cached catalog.
"""

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .catalog_cache import invalidate_catalog_cache
from .models import BusinessAccount


@receiver(post_save, sender=BusinessAccount)
def _invalidate_catalog_cache_on_business_account_save(sender, instance, **kwargs):
    invalidate_catalog_cache(instance.domain_code)


@receiver(post_delete, sender=BusinessAccount)
def _invalidate_catalog_cache_on_business_account_delete(sender, instance, **kwargs):
    invalidate_catalog_cache(instance.domain_code)
