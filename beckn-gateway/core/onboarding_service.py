"""Orchestrates Gateway's Phase 3.3 onboarding sequence (livetracker1.md): key
generation (participant_keys) -> domain verification -> manual approval gate ->
Subscribe -> on_subscribe challenge response -> SUBSCRIBED. Mirrors
BAP/backend/core/onboarding_service.py, with one structural difference: Gateway is
stateless (no DB — beckn_gateway_details_v1.1.md §4), so onboarding progress is tracked
via core.onboarding_state (file-backed) instead of a Django model.

participant_type is "gateway" (confirmed real ONDC enum value alongside buyerApp/
sellerApp). ops_no=4 is used for the Subscribe context, but this is a documented
inference, not confirmed from official sources specifically for the gateway type — the
only confirmed ops_no mapping (protocol_compliance_notes_v1.1.md §B.3) covers
1=BAP/2=BPP/4=both, without addressing a pure-gateway registration explicitly. 4 is used
as the least-wrong available value (the alternative deprecated codes 3/5 are ruled out
outright); do not treat this as a confirmed protocol fact.
"""

import logging
import uuid

import requests
from beckn_crypto import build_verification_file_content, decrypt_challenge
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from . import onboarding_state, registry_client
from .participant_keys import get_encryption_keys, get_signing_keys

# livetracker8.md §2.2: short-TTL, Redis-backed hand-off for the *new* signing AND
# encryption private keys during a rotation's re-Subscribe flow. Real reason this
# exists, not a convenience — see submit_subscribe's own docstring: Registry requires a
# re-Subscribe's Authorization header to be signed with the CURRENTLY REGISTERED (old)
# signing key, but its domain-ownership verification file must be signed with the NEW
# signing key being submitted in THAT SAME request's payload. The identical split exists
# for encryption: Registry's on_subscribe challenge dispatch (also mid-flow, part of the
# same synchronous Subscribe handling) encrypts the challenge using the NEW encryption
# public key just submitted — found live 2026-09-04 when the first version of this fix
# only handled the signing-key half, and Gateway's own `/on_subscribe` callback
# (`handle_on_subscribe` below) correctly reached Registry but returned a genuine 400 —
# it was still decrypting with the OLD on-disk encryption key. Gateway can't tell any of
# this apart via `get_signing_keys()`/`get_encryption_keys()` alone (those always mean
# "whatever's on disk right now"), and both of Registry's callbacks (verification file
# fetch, challenge dispatch) arrive as genuinely separate HTTP requests — possibly a
# different gunicorn worker than the one running the rotation command — so this can't be
# a plain in-process variable either. A short TTL (comfortably longer than one Subscribe
# round trip, per handle_subscribe's own "synchronously... before returning" design
# elsewhere in this project) means an interrupted/crashed rotation attempt can't leave
# either lingering.
_PENDING_ROTATION_SIGNING_CACHE_KEY = "gateway:pending_rotation_signing_key"
_PENDING_ROTATION_ENCRYPTION_CACHE_KEY = "gateway:pending_rotation_encryption_key"
_PENDING_ROTATION_TTL_SECONDS = 60


def set_pending_rotation_signing_key(signing_private_key_b64: str) -> None:
    cache.set(
        _PENDING_ROTATION_SIGNING_CACHE_KEY,
        signing_private_key_b64,
        timeout=_PENDING_ROTATION_TTL_SECONDS,
    )


def get_pending_rotation_signing_key() -> str | None:
    return cache.get(_PENDING_ROTATION_SIGNING_CACHE_KEY)


def clear_pending_rotation_signing_key() -> None:
    cache.delete(_PENDING_ROTATION_SIGNING_CACHE_KEY)


def set_pending_rotation_encryption_key(encryption_private_key_b64: str) -> None:
    cache.set(
        _PENDING_ROTATION_ENCRYPTION_CACHE_KEY,
        encryption_private_key_b64,
        timeout=_PENDING_ROTATION_TTL_SECONDS,
    )


def get_pending_rotation_encryption_key() -> str | None:
    return cache.get(_PENDING_ROTATION_ENCRYPTION_CACHE_KEY)


def clear_pending_rotation_encryption_key() -> None:
    cache.delete(_PENDING_ROTATION_ENCRYPTION_CACHE_KEY)

logger = logging.getLogger("gateway")


class OnboardingError(Exception):
    pass


UNCONFIRMED_DOMAIN_SENTINEL = "CONFIRM_BEFORE_USE"


def get_verification_file_content() -> str:
    request_id = onboarding_state.get_verification_request_id()
    if not request_id:
        raise OnboardingError("No domain-verification request_id has been set yet")
    # A rotation in progress signs this with the NEW key being submitted (see
    # set_pending_rotation_signing_key's own docstring for why) — falls back to the
    # normal on-disk key for an ordinary first-time Subscribe.
    signing_priv = get_pending_rotation_signing_key()
    if signing_priv is None:
        _, signing_priv = get_signing_keys()
    return build_verification_file_content(
        request_id=request_id, signing_private_key_b64=signing_priv
    )


def request_domain_verification(*, request_id: str | None = None) -> str:
    request_id = request_id or str(uuid.uuid4())
    onboarding_state.set_verification_request_id(request_id)
    return request_id


def approve(domain: str) -> dict:
    """Simulates the ONDC Network Participant Portal's human review gate — never called
    automatically, only from the onboarding_approve management command."""
    return onboarding_state.approve(domain)


def _build_subscribe_payload(
    *,
    domain: str,
    request_id: str,
    signing_public_key: str | None = None,
    encryption_public_key: str | None = None,
) -> dict:
    """`signing_public_key`/`encryption_public_key` override what's declared in the
    payload's `entity.key_pair` — used by a rotation to submit the *new* public keys
    while `submit_subscribe`'s own Authorization-header signing (via
    `registry_client.subscribe` -> `get_signing_keys()`) still uses whatever's on disk
    (the old key, not yet rotated). Defaults to the current on-disk keys for an ordinary
    first-time Subscribe, unchanged from before."""
    signing_pub = signing_public_key or get_signing_keys()[0]
    encryption_pub = encryption_public_key or get_encryption_keys()[0]
    now = timezone.now()
    later = now + timezone.timedelta(days=365)
    return {
        "context": {"operation": {"ops_no": 4}},  # documented inference — see module docstring
        "message": {
            "request_id": request_id,
            "timestamp": now.isoformat(),
            "entity": {
                "subscriber_id": settings.SUBSCRIBER_ID,
                "unique_key_id": settings.UNIQUE_KEY_ID,
                "callback_url": settings.ON_SUBSCRIBE_CALLBACK_PATH,
                "country": "IND",
                "key_pair": {
                    "signing_public_key": signing_pub,
                    "encryption_public_key": encryption_pub,
                    "valid_from": now.isoformat(),
                    "valid_until": later.isoformat(),
                },
            },
            "network_participant": [
                {
                    "subscriber_url": settings.SUBSCRIBER_URL,
                    "domain": domain,
                    "type": "gateway",
                }
            ],
        },
    }


def submit_subscribe(
    domain: str,
    *,
    signing_public_key: str | None = None,
    encryption_public_key: str | None = None,
) -> dict:
    """Submits Subscribe to the Registry for this domain. Refuses to proceed unless the
    manual approval gate has been passed.

    `signing_public_key`/`encryption_public_key`: only passed by a key rotation —
    submits these as the *new* key_pair in the payload while the actual HTTP request is
    still signed with whatever's currently on disk. Registry's own
    `verify_subscribe_authorization` requires exactly this split for a re-Subscribe: the
    Authorization header must be signed with the CURRENTLY REGISTERED key (proves the
    caller is the legitimate current key holder), while the domain-ownership
    verification file it fetches via callback must be signed with the NEW key (proves
    the caller controls the identity being rotated *to*) — RUNBOOK.md's own "Known
    Operational Facts" already documents the Authorization-header half of this rule.
    Get this wrong and Registry returns a clean, expected `401 UNAUTHORIZED` — not a
    bug, by design (found live, 2026-09-04, when the original `onboarding_rotate_keys`
    rotated the on-disk key *before* calling this, signing with the new key instead)."""
    if domain == UNCONFIRMED_DOMAIN_SENTINEL or not domain:
        raise OnboardingError(
            f"Domain code is unconfirmed ({domain!r}) — see protocol_compliance_notes_v1.1.md "
            "'Remaining Open Items'. Confirm the real ONDC:SRV## code before subscribing; "
            "do not guess and submit."
        )
    entry = onboarding_state.get_domain_status(domain)
    if not entry["approved_for_subscribe"]:
        raise OnboardingError(
            f"Domain {domain!r} is not approved for Subscribe — run onboarding_approve first "
            "(simulates the ONDC Network Participant Portal review gate)."
        )
    if not settings.SUBSCRIBER_URL:
        raise OnboardingError(
            "SUBSCRIBER_URL is not configured — Registry needs a real reachable URL to "
            "dispatch the on_subscribe challenge and fetch the verification file."
        )

    request_id = request_domain_verification()
    payload = _build_subscribe_payload(
        domain=domain,
        request_id=request_id,
        signing_public_key=signing_public_key,
        encryption_public_key=encryption_public_key,
    )
    # Same fix BAP/backend/core/onboarding_service.py already has, first half: mark
    # UNDER_SUBSCRIPTION *before* calling Registry, not after — Registry's synchronous
    # handling of POST /subscribe calls back to handle_on_subscribe() below before it
    # ever returns, so by the time that nested challenge fires, there needs to be a
    # real UNDER_SUBSCRIPTION row for mark_all_under_subscription_as_subscribed() to
    # actually flip. Without this, a genuinely successful rotation still left Gateway's
    # own local state wrong (confirmed live 2026-09-04 — Registry's own DB correctly
    # showed SUBSCRIBED, Gateway's local file still said UNDER_SUBSCRIPTION).
    onboarding_state.set_status(domain, "UNDER_SUBSCRIPTION")
    try:
        result = registry_client.subscribe(payload)
    except requests.HTTPError as exc:
        detail = exc.response.text if exc.response is not None else str(exc)
        onboarding_state.set_status(domain, "FAILED", last_error=detail)
        raise OnboardingError(f"Subscribe rejected by Registry: {detail}") from exc

    # Same race BAP/backend/core/onboarding_service.py already documents and guards
    # against — found live here too, 2026-09-04, via this project's own rotation Test
    # Gate: Registry's synchronous handling of POST /subscribe calls back to
    # handle_on_subscribe() (below) *before* it returns, and that callback may already
    # have marked this domain SUBSCRIBED for real. Registry's /subscribe response body
    # always literally says {"status": "UNDER_SUBSCRIPTION"} regardless of the actual
    # challenge outcome — blindly writing result["status"] here would stomp a genuine
    # SUBSCRIBED back down. handle_on_subscribe is the one authoritative path to
    # SUBSCRIBED; never downgrade a status it already moved there.
    current = onboarding_state.get_domain_status(domain)
    if current["status"] == "SUBSCRIBED":
        return current
    return onboarding_state.set_status(domain, result["status"])


def handle_on_subscribe(payload: dict) -> dict:
    """Handles Registry's inbound POST /on_subscribe callback — see
    BAP/backend/core/onboarding_service.py's handle_on_subscribe docstring for the full
    design rationale (identical here, modulo file-backed state instead of a DB row).

    A rotation in progress decrypts this with the NEW encryption key being submitted
    (see set_pending_rotation_encryption_key's own docstring for why) — falls back to
    the normal on-disk key for an ordinary first-time Subscribe."""
    encryption_priv = get_pending_rotation_encryption_key()
    if encryption_priv is None:
        _, encryption_priv = get_encryption_keys()
    registry_identity = registry_client.get_registry_identity()
    answer = decrypt_challenge(
        encrypted_challenge=payload["challenge"],
        own_private_key_b64=encryption_priv,
        peer_public_key_b64_der=registry_identity["encryption_public_key"],
    )
    onboarding_state.mark_all_under_subscription_as_subscribed()
    return {"answer": answer}
