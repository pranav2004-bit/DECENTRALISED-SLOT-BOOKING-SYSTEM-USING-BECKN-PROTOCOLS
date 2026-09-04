from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core import registry_service
from core.registry_keys import NoExistingKeyError, is_rotation_due

_KEY_PATH_SETTING = {
    "signing": "SIGNING_PRIVATE_KEY_PATH",
    "encryption": "ENCRYPTION_PRIVATE_KEY_PATH",
}


class Command(BaseCommand):
    help = (
        "livetracker8.md §1.2: rotates Registry's own signing and/or encryption identity "
        "key, replacing the manual 'nothing, this never happens' process. Backs up the "
        "previous key before overwriting, writes an AuditLogEntry, and — since "
        "registry_keys.py no longer caches keys in-process (2026-09-03) — takes effect "
        "immediately for every already-running gunicorn worker, no restart needed. "
        "Safe to invoke on any schedule (e.g. daily, via an external scheduler like the "
        "ofelia sidecar in docker-compose.yml) — by default this only actually rotates a "
        "key once it's at least settings.REGISTRY_KEY_ROTATION_DAYS old; pass --force to "
        "rotate immediately regardless of age (e.g. responding to a suspected compromise)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--key-type",
            dest="key_type",
            default="both",
            choices=["signing", "encryption", "both"],
            help="Which identity key to rotate (default: both).",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Rotate immediately regardless of the key's current age.",
        )

    def handle(self, *args, **options):
        key_types = ["signing", "encryption"] if options["key_type"] == "both" else [options["key_type"]]
        for key_type in key_types:
            path_str = getattr(settings, _KEY_PATH_SETTING[key_type])
            if not options["force"] and not is_rotation_due(
                path_str, settings.REGISTRY_KEY_ROTATION_DAYS
            ):
                self.stdout.write(
                    f"{key_type} key not due for rotation yet "
                    f"(cadence: {settings.REGISTRY_KEY_ROTATION_DAYS} days) — skipping. "
                    "Use --force to rotate anyway."
                )
                continue
            try:
                new_public_key = registry_service.rotate_registry_key(key_type=key_type)
            except NoExistingKeyError as exc:
                raise CommandError(str(exc)) from exc
            self.stdout.write(
                self.style.SUCCESS(
                    f"Rotated Registry's {key_type} key. New public key: {new_public_key[:24]}..."
                )
            )
