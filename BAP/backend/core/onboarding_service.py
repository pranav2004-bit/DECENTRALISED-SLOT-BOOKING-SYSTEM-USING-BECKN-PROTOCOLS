"""Orchestrates BAP's Phase 3.1 onboarding sequence (livetracker1.md): key generation
(participant_keys) -> domain verification -> manual approval gate -> Subscribe ->
on_subscribe challenge response -> SUBSCRIBED. Kept separate from views.py/management
commands so the logic is directly unit-testable, matching Registry's registry_service.py
convention.
"""

import logging
import uuid

import requests
from beckn_crypto import build_verification_file_content, decrypt_challenge
from django.conf import settings
from django.utils import timezone

from . import registry_client
from .models import OnboardingStatus, SiteVerification
from .participant_keys import get_encryption_keys, get_signing_keys

logger = logging.getLogger("bap")


class OnboardingError(Exception):
    pass


def get_or_create_status(domain: str) -> OnboardingStatus:
    status, _ = OnboardingStatus.objects.get_or_create(domain=domain)
    return status


def _set_current_verification_request_id(request_id: str) -> None:
    SiteVerification.objects.update_or_create(pk=1, defaults={"request_id": request_id})


def get_verification_file_content() -> str:
    """Serves the signed content for whatever request_id is currently on record.
    Raises OnboardingError if no Subscribe attempt (or explicit
    request_domain_verification call) has set one yet."""
    site_verification = SiteVerification.objects.filter(pk=1).first()
    if site_verification is None:
        raise OnboardingError("No domain-verification request_id has been set yet")
    _, signing_priv = get_signing_keys()
    return build_verification_file_content(
        request_id=site_verification.request_id, signing_private_key_b64=signing_priv
    )


def request_domain_verification(*, request_id: str | None = None) -> str:
    """Sets the request_id to sign and serve at /ondc-site-verification.html, for manual
    testing of the file-serving endpoint ahead of a real Subscribe call. submit_subscribe
    also does this automatically immediately before calling Registry, so this is not a
    required precondition — it exists for observability/manual verification."""
    request_id = request_id or str(uuid.uuid4())
    _set_current_verification_request_id(request_id)
    return request_id


def approve(domain: str) -> OnboardingStatus:
    """Simulates the ONDC Network Participant Portal's human review gate — never called
    automatically, only from the onboarding_approve management command."""
    status = get_or_create_status(domain)
    status.approved_for_subscribe = True
    if status.status == OnboardingStatus.Status.NOT_STARTED:
        status.status = OnboardingStatus.Status.AWAITING_APPROVAL
    status.save(update_fields=["approved_for_subscribe", "status", "updated_at"])
    return status


def _build_subscribe_payload(*, domain: str, request_id: str) -> dict:
    signing_pub, _ = get_signing_keys()
    encryption_pub, _ = get_encryption_keys()
    now = timezone.now()
    later = now + timezone.timedelta(days=365)
    return {
        "context": {"operation": {"ops_no": 1}},  # 1 = BAP registration (confirmed mapping)
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
                    "type": "buyerApp",
                }
            ],
        },
    }


def submit_subscribe(domain: str) -> OnboardingStatus:
    """Submits Subscribe to the Registry for this domain. Refuses to proceed unless the
    manual approval gate has been passed (3.1: 'don't auto-approve'). Generates a fresh
    request_id, publishes it as the current domain-verification content, then uses that
    SAME request_id in the Subscribe payload — guaranteeing Registry's synchronous fetch
    of /ondc-site-verification.html (triggered by this call) sees a file that matches."""
    status = get_or_create_status(domain)
    if not status.approved_for_subscribe:
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
    payload = _build_subscribe_payload(domain=domain, request_id=request_id)
    try:
        result = registry_client.subscribe(payload)
    except requests.HTTPError as exc:
        detail = exc.response.text if exc.response is not None else str(exc)
        status.status = OnboardingStatus.Status.FAILED
        status.last_error = detail
        status.save(update_fields=["status", "last_error", "updated_at"])
        raise OnboardingError(f"Subscribe rejected by Registry: {detail}") from exc

    status.status = OnboardingStatus.Status(result["status"])
    status.last_error = ""
    status.save(update_fields=["status", "last_error", "updated_at"])
    return status


def handle_on_subscribe(payload: dict) -> dict:
    """Handles Registry's inbound POST /on_subscribe callback
    (protocol_compliance_notes_v1.1.md §A.1/§B.5): decrypt the challenge using the shared
    key derived from this BAP's encryption private key + Registry's encryption public
    key, respond with the decrypted answer. Also opportunistically marks in-flight
    OnboardingStatus rows SUBSCRIBED — this BAP can't be told directly which domain's
    challenge just resolved (Registry's callback payload carries subscriber_id only, not
    domain), but responding correctly is the strongest local signal available short of
    polling Lookup (which Phase 3.4's trust-establishment utilities do for the
    authoritative check)."""
    _, encryption_priv = get_encryption_keys()
    registry_identity = registry_client.get_registry_identity()
    answer = decrypt_challenge(
        encrypted_challenge=payload["challenge"],
        own_private_key_b64=encryption_priv,
        peer_public_key_b64_der=registry_identity["encryption_public_key"],
    )
    OnboardingStatus.objects.filter(
        status=OnboardingStatus.Status.UNDER_SUBSCRIPTION
    ).update(status=OnboardingStatus.Status.SUBSCRIBED)
    return {"answer": answer}
