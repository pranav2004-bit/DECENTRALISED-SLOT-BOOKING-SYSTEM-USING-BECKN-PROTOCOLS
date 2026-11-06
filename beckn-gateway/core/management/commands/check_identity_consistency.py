from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core import onboarding_state, participant_keys, registry_client


class Command(BaseCommand):
    help = (
        "Verifies Gateway's on-disk signing key actually matches what Registry has on "
        "file for every domain Gateway believes itself SUBSCRIBED for. Exists because "
        "of a real, live incident (2026-10-21, found on BAP but the same bug class this "
        "project's own RUNBOOK.md already documented hitting Gateway, BPP, and BAP "
        "independently): a secrets volume recreated empty makes "
        "participant_keys._load_or_generate() silently generate a brand-new signing key "
        "with no check against Registry's existing record for this subscriber_id, and "
        "every outbound signed call then fails with a cryptic 401 — for however long it "
        "takes a human to manually trace it back. Silent generation itself is "
        "intentionally still allowed in dev/test (needed for a genuinely fresh "
        "environment's first boot) — this command is the other half: catching the "
        "moment that generation diverges from what the network already believes this "
        "participant's identity is. "
        "Exits non-zero on any mismatch or on a lookup failure — intended to run both at "
        "container start (entrypoint.sh, non-fatal there — logged loud, doesn't block "
        "boot) and on the same daily cron sidecar that already runs "
        "onboarding_rotate_keys, so drift surfaces within a day instead of silently "
        "breaking search until someone notices."
    )

    def handle(self, *args, **options):
        domains = onboarding_state.list_subscribed_domains()
        if not domains:
            self.stdout.write("No SUBSCRIBED domains on file — nothing to verify.")
            return

        current_signing_pub, _ = participant_keys.get_signing_keys()

        try:
            results = registry_client.lookup({"subscriber_id": settings.SUBSCRIBER_ID})
        except Exception as exc:
            raise CommandError(
                f"Could not verify identity with Registry — Gateway's own signed "
                f"/lookup call failed: {exc}. This can itself be a symptom of the same "
                "drift this command exists to catch (Registry verifies that call "
                "against whichever of this subscriber_id's domain rows it finds first, "
                "so a mismatched key can make even this check fail closed rather than "
                "report cleanly) — treat a failure here as seriously as a reported "
                "mismatch."
            ) from exc

        by_domain = {r["domain"]: r for r in results}
        mismatches = []
        for domain in domains:
            record = by_domain.get(domain)
            if record is None:
                mismatches.append(f"{domain}: SUBSCRIBED locally but no Registry record found")
                continue
            if record.get("signing_public_key") != current_signing_pub:
                mismatches.append(
                    f"{domain}: Registry has a different signing key on file than "
                    "Gateway's current on-disk key — stale identity, needs re-sync "
                    "(RUNBOOK.md)"
                )

        if mismatches:
            raise CommandError(
                "Identity drift detected between Gateway's on-disk key and Registry's "
                "records:\n  " + "\n  ".join(mismatches)
            )

        self.stdout.write(
            self.style.SUCCESS(f"Identity consistent with Registry for: {', '.join(domains)}")
        )
