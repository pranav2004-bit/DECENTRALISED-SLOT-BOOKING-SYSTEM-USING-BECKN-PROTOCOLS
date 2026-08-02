from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"

    def ready(self):
        # livetracker4.md §2.4: starts BAP's own real periodic reconciliation
        # loop (stale-confirmation resync) — mirrors BPP's own core/apps.py
        # ready() hook. No-ops under settings.TESTING — see reconciliation.py's
        # own docstring for why.
        from .reconciliation import start_reconciliation_loop

        start_reconciliation_loop()
