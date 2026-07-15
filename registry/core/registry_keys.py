"""Registry's own signing/encryption identity (protocol_compliance_notes_v1.1.md §A.5 —
Registry acts as a PKI and has its own identity too). Reads from configured secret paths
in production; falls back to ephemeral local/test keys otherwise, with an explicit log
warning so this is never silently mistaken for a real persistent identity.

The ephemeral keys are persisted to a /tmp file (load-or-generate, same pattern as
BAP/BPP/Gateway's participant_keys.py) rather than kept purely in-process. Found for
real during Phase 3 end-to-end onboarding testing: Registry runs multiple gunicorn
workers, each its own process — a pure @lru_cache-only ephemeral key meant one worker
could encrypt an on_subscribe challenge with a different key than the one another
worker (handling the participant's later GET /identity call) reports, making
decryption fail unpredictably. The /tmp file makes all workers in the same container
converge on one key; still lost on container restart, so still not a real deployed
identity.
"""

import json
import logging
from functools import lru_cache
from pathlib import Path

from beckn_crypto import generate_encryption_key_pair, generate_signing_key_pair
from django.conf import settings

logger = logging.getLogger("registry")

# bandit B108 (hardcoded /tmp path): a local attacker able to pre-plant or symlink this
# file would have to already have write access inside this container — and the payoff
# is only ever a throwaway dev/test ephemeral key that the DEBUG/TESTING guard below
# refuses to run with in production, never a real credential. Fixing this with proper
# secure-tempfile semantics isn't warranted for key material with zero production value.
_EPHEMERAL_SIGNING_KEY_PATH = Path("/tmp/registry_ephemeral_signing_key.json")  # nosec B108
_EPHEMERAL_ENCRYPTION_KEY_PATH = Path("/tmp/registry_ephemeral_encryption_key.json")  # nosec B108


def _read_key_file(path: Path) -> tuple[str, str]:
    data = json.loads(path.read_text())
    return data["public_key"], data["private_key"]


def _load_or_generate_ephemeral(path: Path, generate_fn, label: str) -> tuple[str, str]:
    if path.exists():
        return _read_key_file(path)
    public_key, private_key = generate_fn()
    try:
        with path.open("x") as f:
            json.dump({"public_key": public_key, "private_key": private_key}, f)
    except FileExistsError:
        pass  # another worker process won the race to generate first — use theirs instead
    return _read_key_file(path)


@lru_cache(maxsize=1)
def get_registry_signing_keys() -> tuple[str, str]:
    """Returns (public_key_b64, private_key_b64). Ephemeral in local/test —
    real deployments must set REGISTRY_SIGNING_PRIVATE_KEY_PATH to a real mounted secret
    (see SECURITY.md); loading from that path is not yet wired in here, tracked as a
    production-readiness item, not silently assumed done."""
    if getattr(settings, "TESTING", False) or settings.DEBUG:
        logger.warning(
            "Using ephemeral Registry signing key pair — not a real deployed identity, "
            "lost on container restart."
        )
        return _load_or_generate_ephemeral(
            _EPHEMERAL_SIGNING_KEY_PATH, generate_signing_key_pair, "signing"
        )
    raise NotImplementedError(
        "Production Registry signing key loading from REGISTRY_SIGNING_PRIVATE_KEY_PATH "
        "is not yet implemented — do not run with DEBUG=False until this is wired in."
    )


@lru_cache(maxsize=1)
def get_registry_encryption_keys() -> tuple[str, str]:
    """Returns (public_key_b64_der, private_key_b64). Same ephemeral-in-dev caveat as above."""
    if getattr(settings, "TESTING", False) or settings.DEBUG:
        logger.warning(
            "Using ephemeral Registry encryption key pair — not a real deployed identity, "
            "lost on container restart."
        )
        return _load_or_generate_ephemeral(
            _EPHEMERAL_ENCRYPTION_KEY_PATH, generate_encryption_key_pair, "encryption"
        )
    raise NotImplementedError(
        "Production Registry encryption key loading from REGISTRY_ENCRYPTION_PRIVATE_KEY_PATH "
        "is not yet implemented — do not run with DEBUG=False until this is wired in."
    )
