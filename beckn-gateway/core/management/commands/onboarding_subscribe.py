from core import onboarding_service
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Submits Subscribe to the Registry for a domain (requires prior onboarding_approve)."

    def add_arguments(self, parser):
        parser.add_argument("domain", help="ONDC domain code, e.g. ONDC:RET13")

    def handle(self, *args, **options):
        try:
            entry = onboarding_service.submit_subscribe(options["domain"])
        except onboarding_service.OnboardingError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f"{options['domain']}: status={entry['status']}"))
