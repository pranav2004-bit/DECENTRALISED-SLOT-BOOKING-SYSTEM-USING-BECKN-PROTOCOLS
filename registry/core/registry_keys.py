"""Registry's own signing/encryption identity (protocol_compliance_notes_v1.1.md §A.5 —
Registry acts as a PKI and has its own identity too). Reads from configured secret paths
in production; falls back to ephemeral in-process keys for local/test use only, with an
explicit log warning so this is never silently mistaken for a real persistent identity.
"""

import logging
from functools import lru_cache

from beckn_crypto import generate_encryption_key_pair, generate_signing_key_pair
from django.conf import settings

logger = logging.getLogger("registry")


@lru_cache(maxsize=1)
def get_registry_signing_keys() -> tuple[str, str]:
    """Returns (public_key_b64, private_key_b64). Ephemeral per-process in local/test —
    real deployments must set REGISTRY_SIGNING_PRIVATE_KEY_PATH to a real mounted secret
    (see SECURITY.md); loading from that path is not yet wired in here, tracked as a
    production-readiness item, not silently assumed done."""
    if getattr(settings, "TESTING", False) or settings.DEBUG:
        logger.warning(
            "Using ephemeral Registry signing key pair — not persisted, this process only."
        )
        return generate_signing_key_pair()
    raise NotImplementedError(
        "Production Registry signing key loading from REGISTRY_SIGNING_PRIVATE_KEY_PATH "
        "is not yet implemented — do not run with DEBUG=False until this is wired in."
    )


@lru_cache(maxsize=1)
def get_registry_encryption_keys() -> tuple[str, str]:
    """Returns (public_key_b64_der, private_key_b64). Same ephemeral-in-dev caveat as above."""
    if getattr(settings, "TESTING", False) or settings.DEBUG:
        logger.warning(
            "Using ephemeral Registry encryption key pair — not persisted, this process only."
        )
        return generate_encryption_key_pair()
    raise NotImplementedError(
        "Production Registry encryption key loading from REGISTRY_ENCRYPTION_PRIVATE_KEY_PATH "
        "is not yet implemented — do not run with DEBUG=False until this is wired in."
    )
