from django.core.management.base import BaseCommand, CommandError

from core import onboarding_service, participant_keys


class Command(BaseCommand):
    help = (
        "Rotates Gateway's signing+encryption key pairs and re-Subscribes an already-"
        "SUBSCRIBED domain with the new keys, per livetracker1.md 3.4 "
        "('re-Subscribe with new key_pair before valid_until')."
    )

    def add_arguments(self, parser):
        parser.add_argument("domain", help="Domain to re-Subscribe with the rotated keys.")

    def handle(self, *args, **options):
        old_signing_pub, _ = participant_keys.get_signing_keys()
        new_signing_pub, _ = participant_keys.rotate_signing_key()
        new_encryption_pub, _ = participant_keys.rotate_encryption_key()
        self.stdout.write(
            f"Rotated signing key: {old_signing_pub[:12]}... -> {new_signing_pub[:12]}..."
        )

        try:
            entry = onboarding_service.submit_subscribe(options["domain"])
        except onboarding_service.OnboardingError as exc:
            raise CommandError(f"Re-Subscribe with rotated keys failed: {exc}") from exc
        self.stdout.write(
            self.style.SUCCESS(
                f"Re-Subscribed {options['domain']} with rotated keys: {entry['status']}"
            )
        )
