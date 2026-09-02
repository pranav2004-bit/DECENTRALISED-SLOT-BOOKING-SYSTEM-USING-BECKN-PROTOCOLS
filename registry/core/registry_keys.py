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
"""

import json
import logging
from functools import lru_cache
from pathlib import Path

from beckn_crypto import generate_encryption_key_pair, generate_signing_key_pair
from django.conf import settings

logger = logging.getLogger("registry")


def _read_key_file(path: Path) -> tuple[str, str]:
    data = json.loads(path.read_text())
    return data["public_key"], data["private_key"]


def _load_or_generate(path_str: str, generate_fn, label: str) -> tuple[str, str]:
    path = Path(path_str)
    if path.exists():
        return _read_key_file(path)

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
    return _read_key_file(path)


@lru_cache(maxsize=1)
def get_registry_signing_keys() -> tuple[str, str]:
    """Returns (public_key_b64, private_key_b64)."""
    return _load_or_generate(
        settings.SIGNING_PRIVATE_KEY_PATH, generate_signing_key_pair, "signing"
    )


@lru_cache(maxsize=1)
def get_registry_encryption_keys() -> tuple[str, str]:
    """Returns (public_key_b64_der, private_key_b64)."""
    return _load_or_generate(
        settings.ENCRYPTION_PRIVATE_KEY_PATH, generate_encryption_key_pair, "encryption"
    )
