from beckn_crypto import generate_encryption_key_pair, generate_signing_key_pair
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from key_rotation import atomic_write_key_file, backup_key_file, is_rotation_due

from core import onboarding_service, participant_keys
from core.models import OnboardingStatus


class Command(BaseCommand):
    help = (
        "Rotates BAP's signing+encryption key pairs and re-Subscribes EVERY "
        "currently-SUBSCRIBED domain with the new keys — domain-agnostic: the domain "
        "list is read from BAP's own OnboardingStatus table, never passed on the "
        "command line. Per livetracker1.md 3.4 ('re-Subscribe with new key_pair before "
        "valid_until'). "
        "Mirrors the identical fix applied to beckn-gateway/core/management/commands/"
        "onboarding_rotate_keys.py (2026-09-30): this command used to take one domain "
        "as its argument and only re-Subscribe that one. BAP has exactly one signing "
        "key shared across every domain it serves (there is no per-domain key) — the "
        "moment it rotates, it starts signing ALL traffic with the new key, including "
        "for domains the command was never told about. Registry's record for those "
        "other domains still expects the old key, so the very next signature check for "
        "their traffic fails. This specific project's BAP instances only actually "
        "Subscribe to one domain each today (confirmed against Registry, 2026-09-30), "
        "so this bug is currently latent here — but the architecture explicitly "
        "supports a BAP serving all 3 domains (RUNBOOK.md), and the original command "
        "would have broken the moment that became true, the same way Gateway's did the "
        "day it gained a second domain. Fixed proactively rather than waiting to hit it "
        "live. "
        "Generates the new keys in memory and submits them to Registry BEFORE ever "
        "writing them to disk — the old key stays live on disk throughout every "
        "Subscribe attempt (Registry requires the re-Subscribe's Authorization header "
        "to be signed with the CURRENTLY REGISTERED key, not the new one). Only once "
        "Registry has confirmed the new identity for EVERY subscribed domain are the "
        "new keys persisted. "
        "Known, accepted limitation, not solved here: if one domain's re-Subscribe "
        "succeeds and a later one in the same run fails, Registry now disagrees with "
        "what's on disk for the domain(s) that already succeeded — recovered by hand "
        "(RUNBOOK.md's existing key-mismatch recovery), not automatically. Rotation is "
        "infrequent (KEY_ROTATION_DAYS, default 90) and this project already accepts an "
        "equivalent manual-recovery trade-off for similar key-sync incidents, so real "
        "multi-call atomicity across N domains isn't built here."
    )

    def add_arguments(self, parser):
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

        domains = list(
            OnboardingStatus.objects.filter(status=OnboardingStatus.Status.SUBSCRIBED).values_list(
                "domain", flat=True
            )
        )
        if not domains:
            raise CommandError("No SUBSCRIBED domains on file — nothing to rotate against.")

        old_signing_pub, _ = participant_keys.get_signing_keys()
        new_signing_pub, new_signing_priv = generate_signing_key_pair()
        new_encryption_pub, new_encryption_priv = generate_encryption_key_pair()

        # Registry's own synchronous handling of POST /subscribe calls back to BAP TWICE
        # before it ever returns a response: once to fetch the domain-ownership
        # verification file (must be signed with the NEW signing key), and once to
        # dispatch the on_subscribe challenge (must be decrypted with the NEW encryption
        # key) — both pending keys need to be live for the whole loop below, not just
        # one call.
        onboarding_service.set_pending_rotation_signing_key(new_signing_priv)
        onboarding_service.set_pending_rotation_encryption_key(new_encryption_priv)
        confirmed = []
        try:
            for domain in domains:
                status = onboarding_service.submit_subscribe(
                    domain,
                    signing_public_key=new_signing_pub,
                    encryption_public_key=new_encryption_pub,
                )
                confirmed.append((domain, status.status))
        except onboarding_service.OnboardingError as exc:
            raise CommandError(
                f"Re-Subscribe with rotated keys failed after {len(confirmed)} of "
                f"{len(domains)} domain(s) succeeded ({[d for d, _ in confirmed]}): {exc}. "
                "Nothing was written to disk — BAP keeps signing with its OLD key. Any "
                "domain listed above as already-confirmed now expects the NEW key at "
                "Registry but BAP is still signing with the old one, until this is retried "
                "and fully succeeds, or fixed by hand (see RUNBOOK.md)."
            ) from exc
        finally:
            onboarding_service.clear_pending_rotation_signing_key()
            onboarding_service.clear_pending_rotation_encryption_key()

        # Registry confirmed the new identity for every subscribed domain — now, and
        # only now, make it real locally.
        for path_str, generate_result in (
            (settings.SIGNING_PRIVATE_KEY_PATH, (new_signing_pub, new_signing_priv)),
            (settings.ENCRYPTION_PRIVATE_KEY_PATH, (new_encryption_pub, new_encryption_priv)),
        ):
            backup_key_file(path_str)
            atomic_write_key_file(path_str, *generate_result)

        self.stdout.write(
            f"Rotated signing key: {old_signing_pub[:12]}... -> {new_signing_pub[:12]}..."
        )
        for domain, status in confirmed:
            self.stdout.write(
                self.style.SUCCESS(f"Re-Subscribed {domain} with rotated keys: {status}")
            )
