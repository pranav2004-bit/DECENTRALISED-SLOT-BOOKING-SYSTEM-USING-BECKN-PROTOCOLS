"""Shared Beckn/ONDC cryptography — Ed25519 signing, X25519 challenge encryption,
BLAKE-512 digests, and domain-ownership verification, per the confirmed scheme in
protocol_compliance_notes_v1.1.md §A.4/§B/§C.

Moved here from registry/core/crypto.py during the Phase 3 pre-work gap-closure pass:
Registry, BAP, BPP, and Gateway all need the identical signing/encryption capability
(they're all Beckn participants with their own key pairs), so this lives once in
shared/, not duplicated four times.

CONFIRMED (2026-07-17) against ONDC's own reference implementation —
`cryptic_utils.py` in ONDC-Official/reference-implementations
(utilities/signing_and_verification/python/cryptic_utils.py) — after this was
previously flagged as unconfirmed: the challenge-encryption scheme is the RAW X25519
shared secret used directly as an AES-256 key (no KDF/HKDF step at all), with
**AES-ECB mode** and PKCS7 padding — not AES-GCM. This module previously used
HKDF-SHA256 -> AES-256-GCM, a more modern and more secure construction, but one that
is NOT what the real network speaks — a participant using the old scheme here could
not decrypt a real Registry's on_subscribe challenge, or vice versa. Now matches
ONDC's real scheme exactly. ECB is a genuinely weaker mode (deterministic per block,
leaks plaintext-equality patterns) — noted honestly, not silently upgraded, because
matching the real, confirmed protocol takes priority over a locally-preferred
"more secure" alternative the network doesn't actually use. The practical exposure is
limited here (challenge values are single-use random strings, not repeated
structured data), but this is a real security tradeoff, not a non-issue.
"""

import base64
import hashlib
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import padding, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


class SignatureVerificationError(Exception):
    pass


class ChallengeDecryptionError(Exception):
    pass


# --- Key generation ---


def generate_signing_key_pair() -> tuple[str, str]:
    """Ed25519 key pair. Returns (public_key_b64, private_key_b64)."""
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    private_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    return (
        base64.b64encode(public_bytes).decode(),
        base64.b64encode(private_bytes).decode(),
    )


def generate_encryption_key_pair() -> tuple[str, str]:
    """X25519 key pair. Returns (public_key_b64_der, private_key_b64_raw) — public key
    in ASN.1 DER (SubjectPublicKeyInfo) format, matching the confirmed real ONDC
    Subscribe payload shape (protocol_compliance_notes_v1.1.md §B.3)."""
    private_key = X25519PrivateKey.generate()
    public_key = private_key.public_key()
    private_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_der = public_key.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return (
        base64.b64encode(public_der).decode(),
        base64.b64encode(private_bytes).decode(),
    )


# --- Digest & signing string ---


def compute_blake512_digest(body: bytes) -> str:
    """BLAKE-512, i.e. BLAKE2b with a 64-byte (512-bit) digest, base64-encoded."""
    digest = hashlib.blake2b(body, digest_size=64).digest()
    return base64.b64encode(digest).decode()


def build_signing_string(*, created: int, expires: int, digest_b64: str) -> str:
    return f"(created): {created}\n(expires): {expires}\ndigest: BLAKE-512={digest_b64}"


def build_authorization_header(
    *,
    subscriber_id: str,
    unique_key_id: str,
    algorithm: str,
    created: int,
    expires: int,
    signature_b64: str,
) -> str:
    return (
        f'Signature keyId="{subscriber_id}|{unique_key_id}|{algorithm}",'
        f'algorithm="{algorithm}",created="{created}",expires="{expires}",'
        f'headers="(created) (expires) digest",signature="{signature_b64}"'
    )


def parse_authorization_header(header: str) -> dict:
    """Parses `Signature keyId="...",algorithm="...",...` into a dict. Raises
    SignatureVerificationError if the header is malformed."""
    if not header.startswith("Signature "):
        raise SignatureVerificationError("Authorization header must start with 'Signature '")
    params: dict[str, str] = {}
    body = header[len("Signature ") :]
    for part in body.split(","):
        if "=" not in part:
            raise SignatureVerificationError(f"Malformed header segment: {part!r}")
        key, _, value = part.partition("=")
        params[key.strip()] = value.strip().strip('"')
    required = {"keyId", "algorithm", "created", "expires", "signature"}
    missing = required - params.keys()
    if missing:
        raise SignatureVerificationError(f"Authorization header missing fields: {missing}")
    key_parts = params["keyId"].split("|")
    if len(key_parts) != 3:
        raise SignatureVerificationError(
            f"keyId must be 'subscriber_id|unique_key_id|algorithm', got {params['keyId']!r}"
        )
    params["subscriber_id"], params["unique_key_id"], params["key_algorithm"] = key_parts
    return params


# --- Signing / verification ---


def sign_request(*, signing_string: str, private_key_b64: str) -> str:
    """Ed25519-signs the signing string. Returns base64 signature."""
    private_bytes = base64.b64decode(private_key_b64)
    private_key = Ed25519PrivateKey.from_private_bytes(private_bytes)
    signature = private_key.sign(signing_string.encode())
    return base64.b64encode(signature).decode()


def verify_request_signature(
    *, authorization_header: str, body: bytes, public_key_b64: str
) -> bool:
    """Verify an inbound request's Authorization header against the confirmed scheme
    (protocol_compliance_notes_v1.1.md §C.1-2). Also enforces the created/expires
    window — a validly-signed but expired request is rejected (replay-window control)."""
    params = parse_authorization_header(authorization_header)

    try:
        created = int(params["created"])
        expires = int(params["expires"])
    except ValueError as exc:
        raise SignatureVerificationError("created/expires must be integers") from exc

    now = int(time.time())
    if now > expires:
        raise SignatureVerificationError(f"Signature expired at {expires}, now {now}")
    if created > now + 60:  # allow small clock skew
        raise SignatureVerificationError(f"Signature created in the future: {created}")

    digest_b64 = compute_blake512_digest(body)
    expected_signing_string = build_signing_string(
        created=created, expires=expires, digest_b64=digest_b64
    )

    try:
        public_bytes = base64.b64decode(public_key_b64)
        public_key = Ed25519PublicKey.from_public_bytes(public_bytes)
        signature = base64.b64decode(params["signature"])
        public_key.verify(signature, expected_signing_string.encode())
    except (InvalidSignature, ValueError) as exc:
        raise SignatureVerificationError("Signature verification failed") from exc

    return True


# --- Challenge encryption (on_subscribe) ---


def _derive_shared_key(own_private_key_b64: str, peer_public_key_b64_der: str) -> bytes:
    """Returns the RAW X25519 shared secret, used directly as the AES-256 key — no
    KDF step, confirmed against ONDC's own reference implementation (see module
    docstring). The function name/shape is kept for callers, but there is no
    "derivation" beyond the ECDH exchange itself."""
    own_private = X25519PrivateKey.from_private_bytes(base64.b64decode(own_private_key_b64))
    peer_public = serialization.load_der_public_key(base64.b64decode(peer_public_key_b64_der))
    if not isinstance(peer_public, X25519PublicKey):
        raise ChallengeDecryptionError("Peer public key is not a valid X25519 key")
    return own_private.exchange(peer_public)


def encrypt_challenge(
    *, challenge: str, own_private_key_b64: str, peer_public_key_b64_der: str
) -> str:
    """Encrypts a challenge string with AES-256-ECB (PKCS7-padded) using the raw
    X25519(own_private, peer_public) shared secret as the key directly — confirmed
    against ONDC's own reference implementation (see module docstring). Returns
    base64(ciphertext); ECB has no IV/nonce to prepend."""
    key = _derive_shared_key(own_private_key_b64, peer_public_key_b64_der)
    padder = padding.PKCS7(algorithms.AES.block_size).padder()
    padded = padder.update(challenge.encode()) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()
    return base64.b64encode(ciphertext).decode()


def decrypt_challenge(
    *, encrypted_challenge: str, own_private_key_b64: str, peer_public_key_b64_der: str
) -> str:
    """Decrypt an on_subscribe challenge using the raw shared secret derived from this
    participant's encryption private key and the peer's public key
    (protocol_compliance_notes_v1.1.md §A.1/§B.5) — AES-256-ECB, PKCS7-padded,
    confirmed against ONDC's own reference implementation (see module docstring)."""
    key = _derive_shared_key(own_private_key_b64, peer_public_key_b64_der)
    try:
        ciphertext = base64.b64decode(encrypted_challenge)
        decryptor = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
        padded = decryptor.update(ciphertext) + decryptor.finalize()
        unpadder = padding.PKCS7(algorithms.AES.block_size).unpadder()
        plaintext = unpadder.update(padded) + unpadder.finalize()
    except Exception as exc:
        raise ChallengeDecryptionError("Failed to decrypt challenge") from exc
    return plaintext.decode()


# --- Domain-ownership verification (protocol_compliance_notes_v1.1.md §B.2) ---


def sign_domain_verification_request_id(*, request_id: str, signing_private_key_b64: str) -> str:
    """Signs a request_id for ONDC domain-ownership verification. Per the confirmed
    spec this is Ed25519 over the RAW request_id string, UNHASHED — deliberately not
    routed through compute_blake512_digest/build_signing_string, which are for request
    signing, a different operation. Returns base64 signature, ready to place in
    ondc-site-verification.html (see beckn_crypto.domain_verification)."""
    private_bytes = base64.b64decode(signing_private_key_b64)
    private_key = Ed25519PrivateKey.from_private_bytes(private_bytes)
    signature = private_key.sign(request_id.encode())
    return base64.b64encode(signature).decode()
