"""BAP's own signing/encryption identity for Beckn network participation
(protocol_compliance_notes_v1.1.md §A.4). Loads persisted keys from the configured
secret path if present; generates and persists on first use otherwise — real
deployments mount a real secret at that path ahead of time, so it's already there and
never (re)generated. Mirrors registry/core/registry_keys.py's ephemeral-in-dev pattern,
but actually implements the load-from-path side: Phase 3 onboarding requires the same
key pair to survive process restarts (Registry stores the public half; decrypting future
on_subscribe challenges needs the matching private half), so "ephemeral" isn't viable here.
"""

import json
import logging
from functools import lru_cache
from pathlib import Path

from beckn_crypto import generate_encryption_key_pair, generate_signing_key_pair
from django.conf import settings

logger = logging.getLogger("bap")


def _load_or_generate(path_str: str, generate_fn, label: str) -> tuple[str, str]:
    path = Path(path_str)
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
        "Generated and persisted a new %s key pair at %s (dev/test mode only).", label, path_str
    )
    return public_key, private_key


@lru_cache(maxsize=1)
def get_signing_keys() -> tuple[str, str]:
    """Returns (public_key_b64, private_key_b64)."""
    return _load_or_generate(settings.SIGNING_PRIVATE_KEY_PATH, generate_signing_key_pair, "signing")


@lru_cache(maxsize=1)
def get_encryption_keys() -> tuple[str, str]:
    """Returns (public_key_b64_der, private_key_b64)."""
    return _load_or_generate(
        settings.ENCRYPTION_PRIVATE_KEY_PATH, generate_encryption_key_pair, "encryption"
    )


def rotate_signing_key() -> tuple[str, str]:
    """Forcibly regenerates and persists a new signing key pair, overwriting whatever
    was there — used by the onboarding_rotate_keys management command
    (livetracker1.md 3.4: 're-Subscribe with new key_pair before valid_until')."""
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
