"""Gateway's own signing/encryption identity for Beckn network participation
(protocol_compliance_notes_v1.1.md §A.4). Loads persisted keys from the configured
secret path if present; generates and persists on first use otherwise. Mirrors
BAP/backend/core/participant_keys.py — see that module's docstring for the full
rationale (identical here).
"""

import json
import logging
import threading
from functools import lru_cache
from pathlib import Path

from beckn_crypto import generate_encryption_key_pair, generate_signing_key_pair
from django.conf import settings

logger = logging.getLogger("gateway")

# Guards the check-then-generate-then-write critical section below, for the ENTIRE
# function body including the read path — `lru_cache` alone does NOT guarantee only one
# thread runs the wrapped function on a cache miss, and `path.write_text()` isn't atomic
# (open/truncate, write, close), so a check-and-read *outside* the lock is still a real
# race: it can observe another thread's file mid-write (created but not yet fully
# written), raising `json.JSONDecodeError`. A double-checked-locking fast path (checking
# `path.exists()` before acquiring the lock) was tried first and still had exactly this
# gap — the unprotected outer check could read a file another thread was still writing
# under the lock. Simplified to lock unconditionally instead: this function is called at
# most once per process in practice (`get_signing_keys`/`get_encryption_keys` are
# `lru_cache`-wrapped), so the lock's cost is irrelevant — there is no hot path here worth
# optimizing around.
_generate_lock = threading.Lock()


def _load_or_generate(path_str: str, generate_fn, label: str) -> tuple[str, str]:
    path = Path(path_str)
    with _generate_lock:
        if path.exists():
            data = json.loads(path.read_text())
            return data["public_key"], data["private_key"]

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


@lru_cache(maxsize=1)
def get_signing_keys() -> tuple[str, str]:
    """Returns (public_key_b64, private_key_b64)."""
    return _load_or_generate(
        settings.SIGNING_PRIVATE_KEY_PATH, generate_signing_key_pair, "signing"
    )


@lru_cache(maxsize=1)
def get_encryption_keys() -> tuple[str, str]:
    """Returns (public_key_b64_der, private_key_b64)."""
    return _load_or_generate(
        settings.ENCRYPTION_PRIVATE_KEY_PATH, generate_encryption_key_pair, "encryption"
    )


def rotate_signing_key() -> tuple[str, str]:
    """Forcibly regenerates and persists a new signing key pair, overwriting whatever
    was there — used by the onboarding_rotate_keys management command."""
    public_key, private_key = generate_signing_key_pair()
    path = Path(settings.SIGNING_PRIVATE_KEY_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"public_key": public_key, "private_key": private_key}))
    get_signing_keys.cache_clear()
    return public_key, private_key


def rotate_encryption_key() -> tuple[str, str]:
    """Same as rotate_signing_key but for the encryption key pair."""
    public_key, private_key = generate_encryption_key_pair()
    path = Path(settings.ENCRYPTION_PRIVATE_KEY_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"public_key": public_key, "private_key": private_key}))
    get_encryption_keys.cache_clear()
    return public_key, private_key
