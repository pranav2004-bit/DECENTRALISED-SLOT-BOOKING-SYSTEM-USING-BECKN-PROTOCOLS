from beckn_crypto import generate_encryption_key_pair, generate_signing_key_pair
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from key_rotation import atomic_write_key_file, backup_key_file, is_rotation_due

from core import onboarding_service, onboarding_state, participant_keys


class Command(BaseCommand):
    help = (
        "Rotates Gateway's signing+encryption key pairs and re-Subscribes EVERY "
        "currently-SUBSCRIBED domain with the new keys — domain-agnostic: the domain "
        "list is read from Gateway's own local onboarding state (onboarding_state."
        "list_subscribed_domains()), never passed on the command line. "
        "Real bug found and fixed 2026-09-30: this command used to take one domain as "
        "its argument and only re-Subscribe that one. Gateway has exactly one signing "
        "key shared across every domain it serves (there is no per-domain key) — the "
        "moment it rotates, it starts signing ALL traffic with the new key, including "
        "for domains the command was never told about. Registry's record for those "
        "other domains still expects the old key, so the very next signature check for "
        "their traffic fails. Found while onboarding Gateway for a second domain "
        "(ONDC:SRV13) alongside its pre-existing ONDC:RET13 subscription — a single-"
        "domain rotation call would have silently broken the other one the next time "
        "rotation actually fired. "
        "Known, accepted limitation of this fix, not solved here: if one domain's "
        "re-Subscribe succeeds and a later one in the same run fails, Registry now "
        "disagrees with what's on disk for the domain(s) that already succeeded — "
        "nothing is written to disk until every domain succeeds, so Gateway keeps "
        "signing with the OLD key while Registry expects the NEW one for that domain. "
        "Recovered the same way this project already recovers a Registry/disk key "
        "mismatch elsewhere (see RUNBOOK.md) — a direct, manual fix, not an automatic "
        "one. Rotation is infrequent (KEY_ROTATION_DAYS, default 90) and this project "
        "already accepts an equivalent manual-recovery trade-off for similar key-sync "
        "incidents, so real multi-call atomicity across N domains isn't built here — "
        "it would be more engineering than this rare a failure mode justifies. "
        "livetracker8.md §2.2's original 'write to disk only after Registry confirms' "
        "design is otherwise unchanged."
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

        domains = onboarding_state.list_subscribed_domains()
        if not domains:
            raise CommandError("No SUBSCRIBED domains on file — nothing to rotate against.")

        old_signing_pub, _ = participant_keys.get_signing_keys()
        new_signing_pub, new_signing_priv = generate_signing_key_pair()
        new_encryption_pub, new_encryption_priv = generate_encryption_key_pair()

        # Same reasoning as before (§2.2): both pending keys must be live for the whole
        # multi-domain loop below, not just one Subscribe call — Registry calls back
        # TWICE per domain (verification file fetch signed with the new signing key,
        # on_subscribe challenge decrypted with the new encryption key) before any of
        # them return.
        onboarding_service.set_pending_rotation_signing_key(new_signing_priv)
        onboarding_service.set_pending_rotation_encryption_key(new_encryption_priv)
        confirmed = []
        try:
            for domain in domains:
                entry = onboarding_service.submit_subscribe(
                    domain,
                    signing_public_key=new_signing_pub,
                    encryption_public_key=new_encryption_pub,
                )
                confirmed.append((domain, entry["status"]))
        except onboarding_service.OnboardingError as exc:
            raise CommandError(
                f"Re-Subscribe with rotated keys failed after {len(confirmed)} of "
                f"{len(domains)} domain(s) succeeded ({[d for d, _ in confirmed]}): {exc}. "
                "Nothing was written to disk — Gateway keeps signing with its OLD key. "
                "Any domain listed above as already-confirmed now expects the NEW key at "
                "Registry but Gateway is still signing with the old one, until this command "
                "is retried and fully succeeds, or the mismatch is fixed by hand "
                "(see RUNBOOK.md)."
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
