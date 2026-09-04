"""Gateway's own signing/encryption identity for Beckn network participation
(protocol_compliance_notes_v1.1.md §A.4). Loads persisted keys from the configured
secret path if present; generates and persists on first use otherwise. Mirrors
BAP/backend/core/participant_keys.py — see that module's docstring for the full
rationale (identical here).

livetracker8.md §2.2: no `@lru_cache` on `get_signing_keys`/`get_encryption_keys`
(removed 2026-09-04, was present since this module's original design) — the exact same
bug found and fixed in Registry's own `registry_keys.py` (§1.2): Gateway runs multiple
gunicorn workers, and an in-process cache means a real rotation writes the new key to
disk but every already-running worker keeps serving the *old* key from memory until
restarted. The old `rotate_signing_key()`/`rotate_encryption_key()` called
`get_signing_keys.cache_clear()` after writing, but that only clears the cache in
whatever process called it (the `onboarding_rotate_keys` management command — a
separate, short-lived process, not any of the live gunicorn workers actually serving
traffic) — it never touched the workers' own stale caches. Not a hot path here either
(participant-key reads happen at Subscribe/dispatch time, not every request), so
re-reading a small JSON file per call is cheap and correctness-first.
"""

import json
import logging
import threading
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
    restore_key_file,
)

logger = logging.getLogger("gateway")

__all__ = [
    "NoExistingKeyError",
    "get_signing_keys",
    "get_encryption_keys",
    "rotate_signing_key",
    "rotate_encryption_key",
    "backup_current_keys",
    "restore_keys_from_backup",
    "key_age_days",
    "is_rotation_due",
]

# Guards the check-then-generate-then-write critical section in `_load_or_generate`
# below, for the ENTIRE function body including the read path — see the original,
# still-accurate reasoning in this docstring's own history: `path.write_text()` isn't
# atomic (open/truncate, write, close), so a check-and-read *outside* the lock is a
# real race, observing another thread's file mid-write. This function is called at
# most once per process in practice, so the lock's cost is irrelevant.
_generate_lock = threading.Lock()


def _load_or_generate(path_str: str, generate_fn, label: str) -> tuple[str, str]:
    path = Path(path_str)
    with _generate_lock:
        if path.exists():
            return read_key_file(path_str)

        if not (getattr(settings, "TESTING", False) or settings.DEBUG):
            raise NotImplementedError(
                f"No {label} key found at {path_str} and DEBUG=False — a production identity "
                "must be provisioned out-of-band (mounted secret), not silently generated."
            )

        public_key, private_key = generate_fn()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"public_key": public_key, "private_key": private_key}))
        logger.warning(
            "Generated and persisted a new %s key pair at %s (dev/test mode only).",
            label,
            path_str,
        )
        return public_key, private_key


def get_signing_keys() -> tuple[str, str]:
    """Returns (public_key_b64, private_key_b64)."""
    return _load_or_generate(
        settings.SIGNING_PRIVATE_KEY_PATH, generate_signing_key_pair, "signing"
    )


def get_encryption_keys() -> tuple[str, str]:
    """Returns (public_key_b64_der, private_key_b64)."""
    return _load_or_generate(
        settings.ENCRYPTION_PRIVATE_KEY_PATH, generate_encryption_key_pair, "encryption"
    )


def rotate_signing_key() -> tuple[str, str]:
    """Forcibly regenerates and persists a new signing key pair, backing up the old one
    first. Raises `NoExistingKeyError` if no signing key has been provisioned yet."""
    backup_path = backup_key_file(settings.SIGNING_PRIVATE_KEY_PATH)
    public_key, private_key = generate_signing_key_pair()
    atomic_write_key_file(settings.SIGNING_PRIVATE_KEY_PATH, public_key, private_key)
    logger.warning("Rotated Gateway signing key (previous key backed up to %s).", backup_path)
    return public_key, private_key


def rotate_encryption_key() -> tuple[str, str]:
    """Same as `rotate_signing_key` but for the encryption key pair."""
    backup_path = backup_key_file(settings.ENCRYPTION_PRIVATE_KEY_PATH)
    public_key, private_key = generate_encryption_key_pair()
    atomic_write_key_file(settings.ENCRYPTION_PRIVATE_KEY_PATH, public_key, private_key)
    logger.warning("Rotated Gateway encryption key (previous key backed up to %s).", backup_path)
    return public_key, private_key


def backup_current_keys() -> dict[str, str]:
    """Backs up both current keys without rotating them — used by
    `onboarding_rotate_keys` *before* generating new ones, so a failed re-Subscribe can
    restore exactly what was live a moment ago. Returns {"signing": backup_path,
    "encryption": backup_path}."""
    return {
        "signing": backup_key_file(settings.SIGNING_PRIVATE_KEY_PATH),
        "encryption": backup_key_file(settings.ENCRYPTION_PRIVATE_KEY_PATH),
    }


def restore_keys_from_backup(backup_paths: dict[str, str]) -> None:
    """Rolls Gateway's own identity back to what a prior `backup_current_keys()` call
    saved — used when a rotation's re-Subscribe step fails, so Gateway doesn't end up
    signing with a key Registry never actually learned about."""
    restore_key_file(settings.SIGNING_PRIVATE_KEY_PATH, backup_paths["signing"])
    restore_key_file(settings.ENCRYPTION_PRIVATE_KEY_PATH, backup_paths["encryption"])
    logger.warning("Restored Gateway's signing+encryption keys from backup after a failed rotation.")
