from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = "core"

    def ready(self):
        # Registers the real Beauty domain adapter (livetracker2.md §2.2) with
        # inventory_core's registry at startup — matching Django's own convention for
        # app-startup registration hooks (e.g. signal handlers).
        from django.conf import settings
        from inventory_core.domain_adapter import register_adapter

        from .beauty_adapter import BeautyDomainAdapter

        register_adapter(settings.DOMAIN_BEAUTY, BeautyDomainAdapter())

        # §3.8: connects the catalog-cache invalidation signal handlers (imported
        # for its side effect of running the module-level @receiver decorators).
        from . import signals  # noqa: F401

        # §3.11: starts the real periodic reconciliation loop (expired-hold sweep +
        # catalog-cache drift check). No-ops under settings.TESTING — see
        # reconciliation.py's own docstring for why.
        from .reconciliation import start_reconciliation_loop

        start_reconciliation_loop()
