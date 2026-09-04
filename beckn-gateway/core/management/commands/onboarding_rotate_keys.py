from beckn_crypto import generate_encryption_key_pair, generate_signing_key_pair
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from key_rotation import atomic_write_key_file, backup_key_file, is_rotation_due

from core import onboarding_service, participant_keys


class Command(BaseCommand):
    help = (
        "Rotates Gateway's signing+encryption key pairs and re-Subscribes an already-"
        "SUBSCRIBED domain with the new keys, per livetracker1.md 3.4 "
        "('re-Subscribe with new key_pair before valid_until'). "
        "livetracker8.md §2.2: generates the new keys in memory and submits them to "
        "Registry BEFORE ever writing them to disk — the old key stays live on disk "
        "throughout the Subscribe attempt (Registry requires the re-Subscribe's "
        "Authorization header to be signed with the CURRENTLY REGISTERED key, not the "
        "new one, see onboarding_service.submit_subscribe's own docstring). Only once "
        "Registry actually confirms the new identity are the new keys persisted. If "
        "Subscribe fails, disk is never touched — no rollback needed, because nothing "
        "was ever written. Safe to invoke on any schedule — by default this only "
        "actually rotates once the signing key is at least settings.KEY_ROTATION_DAYS "
        "old; pass --force to rotate immediately regardless of age."
    )

    def add_arguments(self, parser):
        parser.add_argument("domain", help="Domain to re-Subscribe with the rotated keys.")
        parser.add_argument(
            "--force",
            action="store_true",
            help="Rotate immediately regardless of the current key's age.",
        )

    def handle(self, *args, **options):
        if not options["force"] and not is_rotation_due(
            settings.SIGNING_PRIVATE_KEY_PATH, settings.KEY_ROTATION_DAYS
        ):
            self.stdout.write(
                f"Keys not due for rotation yet (cadence: {settings.KEY_ROTATION_DAYS} days) "
                "— skipping. Use --force to rotate anyway."
            )
            return

        old_signing_pub, _ = participant_keys.get_signing_keys()
        new_signing_pub, new_signing_priv = generate_signing_key_pair()
        new_encryption_pub, new_encryption_priv = generate_encryption_key_pair()

        # Registry's own synchronous handling of POST /subscribe calls back to Gateway
        # TWICE before it ever returns a response: once to fetch the domain-ownership
        # verification file (must be signed with the NEW signing key), and once to
        # dispatch the on_subscribe challenge (must be decrypted with the NEW encryption
        # key) — both pending keys need to be live for the whole submit_subscribe call,
        # not just one. See onboarding_service's own module-level docstring for why
        # this can't just be an in-process variable.
        onboarding_service.set_pending_rotation_signing_key(new_signing_priv)
        onboarding_service.set_pending_rotation_encryption_key(new_encryption_priv)
        try:
            entry = onboarding_service.submit_subscribe(
                options["domain"],
                signing_public_key=new_signing_pub,
                encryption_public_key=new_encryption_pub,
            )
        except onboarding_service.OnboardingError as exc:
            raise CommandError(f"Re-Subscribe with rotated keys failed: {exc}") from exc
        finally:
            onboarding_service.clear_pending_rotation_signing_key()
            onboarding_service.clear_pending_rotation_encryption_key()

        # Registry confirmed the new identity — now, and only now, make it real locally.
        # A backup here isn't a rollback path (the old key was never at risk — nothing
        # failed) — it's the same defense-in-depth every other key overwrite in this
        # project gets, kept for audit/recovery, not because this write can fail unsafely.
        for path_str, generate_result in (
            (settings.SIGNING_PRIVATE_KEY_PATH, (new_signing_pub, new_signing_priv)),
            (settings.ENCRYPTION_PRIVATE_KEY_PATH, (new_encryption_pub, new_encryption_priv)),
        ):
            backup_key_file(path_str)
            atomic_write_key_file(path_str, *generate_result)

        self.stdout.write(
            f"Rotated signing key: {old_signing_pub[:12]}... -> {new_signing_pub[:12]}..."
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Re-Subscribed {options['domain']} with rotated keys: {entry['status']}"
            )
        )
