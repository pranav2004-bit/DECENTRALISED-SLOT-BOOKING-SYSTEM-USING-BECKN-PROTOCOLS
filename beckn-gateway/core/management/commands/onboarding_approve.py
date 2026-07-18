from django.core.management.base import BaseCommand

from core import onboarding_service


class Command(BaseCommand):
    help = (
        "Simulates the ONDC Network Participant Portal's human-reviewed whitelisting "
        "gate for a domain — must be run before onboarding_subscribe will proceed."
    )

    def add_arguments(self, parser):
        parser.add_argument("domain", help="ONDC domain code, e.g. ONDC:RET13")

    def handle(self, *args, **options):
        onboarding_service.approve(options["domain"])
        self.stdout.write(self.style.SUCCESS(f"Approved {options['domain']} for Subscribe."))
