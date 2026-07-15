from core.models import OnboardingStatus
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = (
        "Resets a domain's local onboarding state back to NOT_STARTED — the rollback "
        "path for a failed or abandoned mid-onboarding attempt (livetracker1.md 3.4). "
        "Registry has no server-side deregistration endpoint (Phase 2 scope); this only "
        "clears local state so a fresh onboarding_approve -> onboarding_subscribe retry "
        "starts clean, with no orphaned local status."
    )

    def add_arguments(self, parser):
        parser.add_argument("domain", help="ONDC domain code, e.g. ONDC:RET13")

    def handle(self, *args, **options):
        try:
            status = OnboardingStatus.objects.get(domain=options["domain"])
        except OnboardingStatus.DoesNotExist as exc:
            raise CommandError(f"No onboarding state exists for domain {options['domain']!r}") from exc

        status.approved_for_subscribe = False
        status.status = OnboardingStatus.Status.NOT_STARTED
        status.last_error = ""
        status.save(update_fields=["approved_for_subscribe", "status", "last_error", "updated_at"])
        self.stdout.write(self.style.SUCCESS(f"Reset {status.domain} to NOT_STARTED."))
