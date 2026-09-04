"""Registry's own signing/encryption identity (protocol_compliance_notes_v1.1.md §A.5 —
Registry acts as a PKI and has its own identity too). Loads persisted keys from the
configured secret path if present; generates and persists on first use otherwise —
real deployments mount a real secret at that path ahead of time, so it's already there
and never (re)generated.

Real gap found and fixed live (2026-09-02): this previously always wrote to a hardcoded
/tmp path regardless of settings.SIGNING_PRIVATE_KEY_PATH/ENCRYPTION_PRIVATE_KEY_PATH —
the persisted `registry-secrets` Docker volume (docker-compose.yml) was mounted but never
actually read from or written to, so Registry silently got a brand-new identity on every
container restart even locally, contradicting SECURITY.md's "keys now survive an ordinary
container recreate" claim. BAP/BPP/Gateway's own core/participant_keys.py never had this
gap — they already loaded from their real configured path. Fixed by doing the same here.

Registry runs multiple gunicorn workers, each its own process — loading/generating must be
safe across processes, not just threads, so this uses `path.open("x")` (atomic exclusive
create) with a FileExistsError fallback to re-read, not just an in-process lock.

livetracker8.md §1.2: no `@lru_cache` here (removed 2026-09-03, was present since this
module's original design) — an in-process cache would mean a real key rotation (see
`rotate_signing_keys`/`rotate_encryption_keys` below) is invisible to every already-running
gunicorn worker until it's restarted, exactly the "zero window" the rotation Test Gate
requires *not* having. This isn't a hot path (`/identity` and Subscribe/Lookup's own
signature verification, not every request), so re-reading a small JSON file from disk on
each call is cheap and correctness-first, not a premature optimization removed later.

livetracker8.md §2.2: the rotate/backup/due-check machinery moved to `shared/key_rotation`
once Gateway needed the identical logic — "ideally shared tooling ... rather than two
separate implementations" (the tracker's own words). First-provisioning (`_load_or_generate`
below) stays local: it's a genuinely different concern from rotation (the atomic-exclusive-
create race-safety it needs only matters for a *first* key, never a rotation of an existing
one), so it wasn't worth generalizing into the shared module too.
"""

import json
import logging
from pathlib import Path

from beckn_crypto import generate_encryption_key_pair, generate_signing_key_pair
from django.conf import settings
from key_rotation import (
    NoExistingKeyError,
    atomic_write_key_file,
    backup_key_file,
    is_rotation_due,
    key_age_days,
    read_key_file,
)

logger = logging.getLogger("registry")

# Re-exported for backward compatibility — existing callers (the management command,
# tests) import these from here, not from shared/key_rotation directly.
__all__ = [
    "NoExistingKeyError",
    "get_registry_signing_keys",
    "get_registry_encryption_keys",
    "rotate_signing_keys",
    "rotate_encryption_keys",
    "key_age_days",
    "is_rotation_due",
]


def _load_or_generate(path_str: str, generate_fn, label: str) -> tuple[str, str]:
    path = Path(path_str)
    if path.exists():
        return read_key_file(path_str)

    if not (getattr(settings, "TESTING", False) or settings.DEBUG):
        raise NotImplementedError(
            f"No {label} key found at {path_str} and DEBUG=False — a production identity "
            "must be provisioned out-of-band (mounted secret), not silently generated."
        )

    public_key, private_key = generate_fn()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x") as f:
            json.dump({"public_key": public_key, "private_key": private_key}, f)
    except FileExistsError:
        pass  # another worker process won the race to generate first — use theirs instead
    logger.warning(
        "Generated and persisted a new Registry %s key pair at %s.", label, path_str
    )
    return read_key_file(path_str)


def get_registry_signing_keys() -> tuple[str, str]:
    """Returns (public_key_b64, private_key_b64)."""
    return _load_or_generate(
        settings.SIGNING_PRIVATE_KEY_PATH, generate_signing_key_pair, "signing"
    )


def get_registry_encryption_keys() -> tuple[str, str]:
    """Returns (public_key_b64_der, private_key_b64)."""
    return _load_or_generate(
        settings.ENCRYPTION_PRIVATE_KEY_PATH, generate_encryption_key_pair, "encryption"
    )


def _rotate(path_str: str, generate_fn, label: str) -> tuple[str, str]:
    backup_path = backup_key_file(path_str)  # raises NoExistingKeyError if nothing to rotate
    public_key, private_key = generate_fn()
    atomic_write_key_file(path_str, public_key, private_key)
    logger.warning(
        "Rotated Registry %s key at %s (previous key backed up to %s).",
        label,
        path_str,
        backup_path,
    )
    return public_key, private_key


def rotate_signing_keys() -> tuple[str, str]:
    """Returns the new (public_key_b64, private_key_b64). Raises `NoExistingKeyError`
    if no signing key has been provisioned yet."""
    return _rotate(settings.SIGNING_PRIVATE_KEY_PATH, generate_signing_key_pair, "signing")


def rotate_encryption_keys() -> tuple[str, str]:
    """Returns the new (public_key_b64_der, private_key_b64). Raises `NoExistingKeyError`
    if no encryption key has been provisioned yet."""
    return _rotate(
        settings.ENCRYPTION_PRIVATE_KEY_PATH, generate_encryption_key_pair, "encryption"
    )
