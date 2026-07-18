from django.core.management.base import BaseCommand

from core import onboarding_state


class Command(BaseCommand):
    help = (
        "Resets a domain's local onboarding state back to NOT_STARTED — the rollback "
        "path for a failed or abandoned mid-onboarding attempt (livetracker1.md 3.4)."
    )

    def add_arguments(self, parser):
        parser.add_argument("domain", help="ONDC domain code, e.g. ONDC:RET13")

    def handle(self, *args, **options):
        onboarding_state.reset(options["domain"])
        self.stdout.write(self.style.SUCCESS(f"Reset {options['domain']} to NOT_STARTED."))
