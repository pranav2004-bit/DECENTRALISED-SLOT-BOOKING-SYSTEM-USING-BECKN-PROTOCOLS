"""Cryptography Service — real implementation for Phase 2.2/2.3, per the confirmed
scheme in protocol_compliance_notes_v1.1.md §A.4/§C:
  - Signing: Ed25519, body digest via BLAKE-512 (BLAKE2b-512), signing string
    `(created): {v}\n(expires): {v}\ndigest: BLAKE-512={digest}`, delivered via
    `Authorization: Signature keyId="{subscriber_id}|{unique_key_id}|{algorithm}",...`
  - Encryption (on_subscribe challenge): X25519 key exchange.

IMPORTANT — one detail genuinely NOT confirmed from official sources (see
protocol_compliance_notes_v1.1.md "Remaining Open Items"): the exact KDF/cipher used
to turn the X25519 shared secret into a symmetric key for challenge encryption. This
module uses a standard, secure construction (HKDF-SHA256 -> AES-256-GCM) that is
internally consistent (Registry can encrypt a challenge and a participant using this
same module can decrypt it, and vice versa) and is what the Phase 2.0 live-sandbox
spike against ONDC staging exists to confirm or correct before this is used against
the real network — do not assume interop with ONDC's real registry without that
confirmation step.
"""

import base64
import hashlib
import os
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


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
    return (base64.b64encode(public_bytes).decode(), base64.b64encode(private_bytes).decode())


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
        encoding=serialization.Encoding.DER, format=serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return (base64.b64encode(public_der).decode(), base64.b64encode(private_bytes).decode())


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
    own_private = X25519PrivateKey.from_private_bytes(base64.b64decode(own_private_key_b64))
    peer_public = serialization.load_der_public_key(base64.b64decode(peer_public_key_b64_der))
    if not isinstance(peer_public, X25519PublicKey):
        raise ChallengeDecryptionError("Peer public key is not a valid X25519 key")
    shared_secret = own_private.exchange(peer_public)
    return HKDF(
        algorithm=hashes.SHA256(), length=32, salt=None, info=b"beckn-on_subscribe-challenge"
    ).derive(shared_secret)


def encrypt_challenge(
    *, challenge: str, own_private_key_b64: str, peer_public_key_b64_der: str
) -> str:
    """Encrypts a challenge string using a key derived from X25519(own_private, peer_public).
    Returns base64(nonce || ciphertext). See module docstring re: KDF/cipher confirmation status.
    """
    key = _derive_shared_key(own_private_key_b64, peer_public_key_b64_der)
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    ciphertext = aesgcm.encrypt(nonce, challenge.encode(), None)
    return base64.b64encode(nonce + ciphertext).decode()


def decrypt_challenge(
    *, encrypted_challenge: str, own_private_key_b64: str, peer_public_key_b64_der: str
) -> str:
    """Decrypt an on_subscribe challenge using the shared key derived from this
    participant's encryption private key and the peer's public key
    (protocol_compliance_notes_v1.1.md §A.1/§B.5)."""
    key = _derive_shared_key(own_private_key_b64, peer_public_key_b64_der)
    aesgcm = AESGCM(key)
    raw = base64.b64decode(encrypted_challenge)
    nonce, ciphertext = raw[:12], raw[12:]
    try:
        plaintext = aesgcm.decrypt(nonce, ciphertext, None)
    except Exception as exc:
        raise ChallengeDecryptionError("Failed to decrypt challenge") from exc
    return plaintext.decode()
