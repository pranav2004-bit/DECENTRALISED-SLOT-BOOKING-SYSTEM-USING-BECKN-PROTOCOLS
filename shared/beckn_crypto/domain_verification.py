"""Generates the ondc-site-verification.html content required for ONDC domain-ownership
verification (protocol_compliance_notes_v1.1.md §B.2). This module builds the file
content only — actually hosting it at https://<your-domain>/ondc-site-verification.html
so the Registry can fetch and validate it is a real deployment step that requires an
actual, DNS-controlled domain, which doesn't exist in this development context. Do not
treat "file content generated" as "domain verification complete" — those are different
things; the second one is genuinely a user/ops action, not something this code can do.
"""

import base64
import re

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .crypto import SignatureVerificationError, sign_domain_verification_request_id

_TEMPLATE = """<!--Contents of ondc-site-verification.html.
Signed Unique Request ID: {signed_request_id}
-->
"""

_SIGNATURE_LINE_RE = re.compile(r"Signed Unique Request ID:\s*(\S+)")


def build_verification_file_content(*, request_id: str, signing_private_key_b64: str) -> str:
    """Returns the exact file content to place at
    https://<your-domain>/ondc-site-verification.html — signs request_id (Ed25519,
    unhashed, per the confirmed spec) and embeds it in ONDC's documented HTML-comment
    format."""
    signature = sign_domain_verification_request_id(
        request_id=request_id, signing_private_key_b64=signing_private_key_b64
    )
    return _TEMPLATE.format(signed_request_id=signature)


def verify_domain_ownership_file(
    *, file_content: str, request_id: str, signing_public_key_b64: str
) -> bool:
    """Registry-side counterpart to build_verification_file_content: parses the signed
    request_id out of a fetched ondc-site-verification.html and verifies it against the
    submitting participant's signing_public_key (protocol_compliance_notes_v1.1.md §B.2
    — "the registry fetches and validates it"). Raises SignatureVerificationError with a
    clear reason on any failure (missing/malformed content, wrong signature) rather than
    returning a bare False, so callers can surface a real NACK reason."""
    match = _SIGNATURE_LINE_RE.search(file_content)
    if not match:
        raise SignatureVerificationError(
            "ondc-site-verification.html does not contain a 'Signed Unique Request ID:' line"
        )
    signature_b64 = match.group(1)
    try:
        public_key = Ed25519PublicKey.from_public_bytes(base64.b64decode(signing_public_key_b64))
        public_key.verify(base64.b64decode(signature_b64), request_id.encode())
    except (InvalidSignature, ValueError) as exc:
        raise SignatureVerificationError(
            "Domain-ownership verification signature is invalid"
        ) from exc
    return True
