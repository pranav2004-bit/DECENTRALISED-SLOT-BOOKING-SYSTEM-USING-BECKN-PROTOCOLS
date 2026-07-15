"""Generates the ondc-site-verification.html content required for ONDC domain-ownership
verification (protocol_compliance_notes_v1.1.md §B.2). This module builds the file
content only — actually hosting it at https://<your-domain>/ondc-site-verification.html
so the Registry can fetch and validate it is a real deployment step that requires an
actual, DNS-controlled domain, which doesn't exist in this development context. Do not
treat "file content generated" as "domain verification complete" — those are different
things; the second one is genuinely a user/ops action, not something this code can do.
"""

from .crypto import sign_domain_verification_request_id

_TEMPLATE = """<!--Contents of ondc-site-verification.html.
Signed Unique Request ID: {signed_request_id}
-->
"""


def build_verification_file_content(*, request_id: str, signing_private_key_b64: str) -> str:
    """Returns the exact file content to place at
    https://<your-domain>/ondc-site-verification.html — signs request_id (Ed25519,
    unhashed, per the confirmed spec) and embeds it in ONDC's documented HTML-comment
    format."""
    signature = sign_domain_verification_request_id(
        request_id=request_id, signing_private_key_b64=signing_private_key_b64
    )
    return _TEMPLATE.format(signed_request_id=signature)
