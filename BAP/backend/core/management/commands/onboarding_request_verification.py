from core import onboarding_service
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Sets/refreshes the request_id signed and served at /ondc-site-verification.html."

    def add_arguments(self, parser):
        parser.add_argument("--request-id", default=None, help="Use a specific request_id.")

    def handle(self, *args, **options):
        request_id = onboarding_service.request_domain_verification(
            request_id=options["request_id"]
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Now serving domain-verification content for request_id={request_id!r} "
                "at /ondc-site-verification.html"
            )
        )
