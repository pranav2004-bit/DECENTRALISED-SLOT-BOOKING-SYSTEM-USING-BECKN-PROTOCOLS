"""Registry business logic: Subscribe, on_subscribe challenge issuance/verification,
Lookup. Kept separate from views.py so it's directly unit-testable without going
through the HTTP/DRF layer.
"""

import logging
import secrets as secrets_module
from datetime import timedelta

from beckn_crypto import (
    SignatureVerificationError,
    encrypt_challenge,
    parse_authorization_header,
    verify_domain_ownership_file,
    verify_request_signature,
)
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from resilient_http import ResilientHttpClient

from . import metrics
from .models import AuditLogEntry, Challenge, Participant
from .registry_keys import get_registry_encryption_keys

logger = logging.getLogger("registry")


class AuthorizationError(Exception):
    """Raised when a request's Authorization header is missing or fails signature
    verification (protocol_compliance_notes_v1.1.md §C.4: signing is required on every
    registry.yaml path, including /lookup — not previously enforced server-side, closed
    in Phase 4.3)."""

CHALLENGE_TTL_SECONDS = 60

_http_client: ResilientHttpClient | None = None


class DomainVerificationError(Exception):
    """Raised when a participant's ondc-site-verification.html can't be fetched or
    doesn't validate against their submitted signing_public_key. Distinct from ValueError
    (payload-shape problems) — this is a real trust-boundary rejection
    (protocol_compliance_notes_v1.1.md §B.2: 'do not treat as optional')."""


def _get_http_client() -> ResilientHttpClient:
    global _http_client
    if _http_client is None:
        _http_client = ResilientHttpClient(timeout_seconds=5.0)
    return _http_client


def _log_audit(
    *,
    participant: Participant | None,
    subscriber_id: str,
    event_type: str,
    detail: dict,
    correlation_id: str | None,
):
    AuditLogEntry.objects.create(
        participant=participant,
        subscriber_id=subscriber_id,
        event_type=event_type,
        detail=detail,
        correlation_id=correlation_id,
    )


def verify_subscribe_authorization(
    *, payload: dict, authorization_header: str, body: bytes
) -> None:
    """Verifies the Authorization header on a Subscribe request. Resolves the
    first-time-vs-rotation bootstrapping question flagged as unresolved at Phase 3 exit
    with a defensible, documented design — NOT confirmed from an official ONDC source
    about exactly how this case is handled, a sound proof-of-possession scheme instead:

    - First-time Subscribe (no existing Participant row for this subscriber_id/domain/
      type): verify against the NEW signing_public_key submitted in THIS payload —
      proves the caller controls the private key for the identity they're registering.
    - Re-Subscribe / key rotation (a row already exists): verify against the CURRENTLY
      REGISTERED signing_public_key, not the new one being submitted — proves the
      caller is the legitimate current key holder initiating the rotation, not a third
      party who could otherwise "steal" an existing subscriber_id by generating their
      own new key pair and re-Subscribing over it.
    """
    if not authorization_header:
        raise AuthorizationError("Missing Authorization header")

    entity = payload["message"]["entity"]
    network_participant = payload["message"]["network_participant"][0]
    existing = Participant.objects.filter(
        subscriber_id=entity["subscriber_id"],
        domain=network_participant["domain"],
        participant_type=network_participant["type"],
    ).first()
    verification_key = (
        existing.signing_public_key if existing else entity["key_pair"]["signing_public_key"]
    )
    try:
        verify_request_signature(
            authorization_header=authorization_header, body=body, public_key_b64=verification_key
        )
    except SignatureVerificationError as exc:
        raise AuthorizationError(str(exc)) from exc


def verify_lookup_authorization(*, authorization_header: str, body: bytes) -> None:
    """Verifies the Authorization header on a Lookup request against the caller's OWN
    registered signing_public_key (protocol_compliance_notes_v1.1.md §C.4: every
    registry.yaml path requires SubscriberAuth, Lookup included) — only a known,
    registered network participant can enumerate the registry, not an anonymous caller.
    If a subscriber_id has multiple Participant rows (one per domain), any one is used
    for verification — in practice a real participant uses the same signing key across
    its domains, this doesn't attempt to reconcile a case where it doesn't."""
    if not authorization_header:
        raise AuthorizationError("Missing Authorization header")
    try:
        params = parse_authorization_header(authorization_header)
    except SignatureVerificationError as exc:
        raise AuthorizationError(str(exc)) from exc

    participant = Participant.objects.filter(subscriber_id=params["subscriber_id"]).first()
    if participant is None:
        raise AuthorizationError(f"Unknown subscriber_id: {params['subscriber_id']!r}")

    try:
        verify_request_signature(
            authorization_header=authorization_header,
            body=body,
            public_key_b64=participant.signing_public_key,
        )
    except SignatureVerificationError as exc:
        raise AuthorizationError(str(exc)) from exc


def handle_subscribe(payload: dict, *, correlation_id: str | None = None) -> dict:
    """Implements the confirmed /subscribe flow (protocol_compliance_notes_v1.1.md §A.1,
    §B.3, §B.5): upsert the Participant at UNDER_SUBSCRIPTION, then synchronously
    dispatch the on_subscribe challenge and attempt verification before returning.
    Returns the confirmed response shape: {"status": "UNDER_SUBSCRIPTION"}.
    """
    entity = payload["message"]["entity"]
    network_participant = payload["message"]["network_participant"][0]
    ops_no = payload["context"]["operation"]["ops_no"]

    if ops_no in (3, 5):
        raise ValueError(f"ops_no {ops_no} is deprecated (Seller-On-Record) — not supported")

    participant_type = network_participant["type"]
    subscriber_id = entity["subscriber_id"]
    domain = network_participant["domain"]

    _verify_domain_ownership(
        subscriber_url=network_participant["subscriber_url"],
        signing_public_key=entity["key_pair"]["signing_public_key"],
        request_id=payload["message"]["request_id"],
        subscriber_id=subscriber_id,
        correlation_id=correlation_id,
    )

    participant, created = Participant.objects.update_or_create(
        subscriber_id=subscriber_id,
        domain=domain,
        participant_type=participant_type,
        defaults={
            "subscriber_url": network_participant["subscriber_url"],
            "country": entity.get("country", "IND"),
            "city_code": network_participant.get("city_code", []),
            "unique_key_id": entity["unique_key_id"],
            "signing_public_key": entity["key_pair"]["signing_public_key"],
            "encryption_public_key": entity["key_pair"]["encryption_public_key"],
            "key_valid_from": parse_datetime(entity["key_pair"]["valid_from"]),
            "key_valid_until": parse_datetime(entity["key_pair"]["valid_until"]),
            "callback_url": entity["callback_url"],
            "status": Participant.Status.UNDER_SUBSCRIPTION,
        },
    )

    _log_audit(
        participant=participant,
        subscriber_id=subscriber_id,
        event_type="SUBSCRIBE_RECEIVED" if created else "SUBSCRIBE_UPDATED_KEY_ROTATION",
        detail={"domain": domain, "participant_type": participant_type},
        correlation_id=correlation_id,
    )

    _dispatch_on_subscribe_challenge(participant, correlation_id=correlation_id)

    return {"status": Participant.Status.UNDER_SUBSCRIPTION.value}


def _verify_domain_ownership(
    *,
    subscriber_url: str,
    signing_public_key: str,
    request_id: str,
    subscriber_id: str,
    correlation_id: str | None,
) -> None:
    """Fetches and validates ondc-site-verification.html from the participant's own
    domain (protocol_compliance_notes_v1.1.md §B.2). Raises DomainVerificationError with
    a clear, distinct reason on any failure — unreachable domain, missing file, or a
    signature that doesn't match the submitted signing_public_key — so Subscribe is
    rejected before a participant row is ever created for an unverified domain."""
    verification_url = subscriber_url.rstrip("/") + "/ondc-site-verification.html"
    try:
        response = _get_http_client().get(verification_url)
    except Exception as exc:
        _log_audit(
            participant=None,
            subscriber_id=subscriber_id,
            event_type="DOMAIN_VERIFICATION_UNREACHABLE",
            detail={"url": verification_url, "error": str(exc)},
            correlation_id=correlation_id,
        )
        raise DomainVerificationError(
            f"Could not fetch domain-ownership verification file at {verification_url}: {exc}"
        ) from exc

    if response.status_code != 200:
        _log_audit(
            participant=None,
            subscriber_id=subscriber_id,
            event_type="DOMAIN_VERIFICATION_NOT_FOUND",
            detail={"url": verification_url, "status_code": response.status_code},
            correlation_id=correlation_id,
        )
        raise DomainVerificationError(
            f"Domain-ownership verification file at {verification_url} "
            f"returned status {response.status_code}"
        )

    try:
        verify_domain_ownership_file(
            file_content=response.text,
            request_id=request_id,
            signing_public_key_b64=signing_public_key,
        )
    except SignatureVerificationError as exc:
        _log_audit(
            participant=None,
            subscriber_id=subscriber_id,
            event_type="DOMAIN_VERIFICATION_SIGNATURE_INVALID",
            detail={"url": verification_url, "error": str(exc)},
            correlation_id=correlation_id,
        )
        raise DomainVerificationError(str(exc)) from exc

    _log_audit(
        participant=None,
        subscriber_id=subscriber_id,
        event_type="DOMAIN_VERIFICATION_SUCCEEDED",
        detail={"url": verification_url},
        correlation_id=correlation_id,
    )


def _dispatch_on_subscribe_challenge(
    participant: Participant, *, correlation_id: str | None
) -> None:
    """Registry-initiated callback into the participant (protocol_compliance_notes_v1.1.md
    §A.1) — Registry calls OUT to the participant's callback_url, not the reverse."""
    _, registry_priv = get_registry_encryption_keys()

    plaintext_challenge = secrets_module.token_urlsafe(32)
    encrypted = encrypt_challenge(
        challenge=plaintext_challenge,
        own_private_key_b64=registry_priv,
        peer_public_key_b64_der=participant.encryption_public_key,
    )
    challenge = Challenge.objects.create(
        participant=participant,
        plaintext_challenge=plaintext_challenge,
        encrypted_challenge=encrypted,
        expires_at=timezone.now() + timedelta(seconds=CHALLENGE_TTL_SECONDS),
    )

    callback_full_url = participant.subscriber_url.rstrip("/") + participant.callback_url
    try:
        response = _get_http_client().post(
            callback_full_url,
            json={"subscriber_id": participant.subscriber_id, "challenge": encrypted},
        )
    except Exception as exc:
        logger.warning("on_subscribe dispatch to %s failed: %s", callback_full_url, exc)
        _log_audit(
            participant=participant,
            subscriber_id=participant.subscriber_id,
            event_type="ON_SUBSCRIBE_DISPATCH_FAILED",
            detail={"error": str(exc)},
            correlation_id=correlation_id,
        )
        return

    if response.status_code != 200:
        _log_audit(
            participant=participant,
            subscriber_id=participant.subscriber_id,
            event_type="ON_SUBSCRIBE_DISPATCH_REJECTED",
            detail={"status_code": response.status_code},
            correlation_id=correlation_id,
        )
        return

    answer = response.json().get("answer")
    verify_challenge_answer(challenge, answer, correlation_id=correlation_id)


def verify_challenge_answer(
    challenge: Challenge, answer: str | None, *, correlation_id: str | None = None
) -> bool:
    """Verifies a participant's on_subscribe answer against the issued challenge.
    Single-use and time-bound — real replay-attack protection (protocol_compliance_notes_v1.1.md
    §B.7 NACK reason: 'challenge decryption failure')."""
    participant = challenge.participant

    if challenge.is_used():
        metrics.increment("verify_failures_total")
        _log_audit(
            participant=participant,
            subscriber_id=participant.subscriber_id,
            event_type="CHALLENGE_REPLAY_REJECTED",
            detail={"challenge_id": str(challenge.id)},
            correlation_id=correlation_id,
        )
        return False

    if challenge.is_expired():
        metrics.increment("verify_failures_total")
        _log_audit(
            participant=participant,
            subscriber_id=participant.subscriber_id,
            event_type="CHALLENGE_EXPIRED",
            detail={"challenge_id": str(challenge.id)},
            correlation_id=correlation_id,
        )
        return False

    challenge.used_at = timezone.now()
    challenge.save(update_fields=["used_at"])

    if answer != challenge.plaintext_challenge:
        metrics.increment("verify_failures_total")
        _log_audit(
            participant=participant,
            subscriber_id=participant.subscriber_id,
            event_type="CHALLENGE_ANSWER_MISMATCH",
            detail={"challenge_id": str(challenge.id)},
            correlation_id=correlation_id,
        )
        return False

    participant.status = Participant.Status.SUBSCRIBED
    participant.save(update_fields=["status", "updated_at"])
    metrics.increment("verify_successes_total")
    _log_audit(
        participant=participant,
        subscriber_id=participant.subscriber_id,
        event_type="SUBSCRIBED",
        detail={"challenge_id": str(challenge.id)},
        correlation_id=correlation_id,
    )
    return True


def handle_lookup(filters: dict) -> list[dict]:
    """POST /lookup — filter object on a subset of Subscription fields
    (protocol_compliance_notes_v1.1.md §A.1, §B.1). Returns an array of matching
    Subscription-shaped dicts."""
    qs = Participant.objects.all()
    field_map = {
        "subscriber_id": "subscriber_id",
        "domain": "domain",
        "country": "country",
        "type": "participant_type",
    }
    for key, value in filters.items():
        if key in field_map and value:
            qs = qs.filter(**{field_map[key]: value})
    return [_participant_to_subscription_dict(p) for p in qs]


def _participant_to_subscription_dict(p: Participant) -> dict:
    return {
        "subscriber_id": p.subscriber_id,
        "url": p.subscriber_url,
        "type": p.participant_type,
        "domain": p.domain,
        "country": p.country,
        "city_code": p.city_code,
        "key_id": p.unique_key_id,
        "signing_public_key": p.signing_public_key,
        "encr_public_key": p.encryption_public_key,
        "valid_from": p.key_valid_from.isoformat(),
        "valid_until": p.key_valid_until.isoformat(),
        "status": p.status,
        "created": p.created_at.isoformat(),
        "updated": p.updated_at.isoformat(),
    }
