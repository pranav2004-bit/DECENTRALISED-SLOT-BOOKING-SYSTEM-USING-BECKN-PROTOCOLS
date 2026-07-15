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
from django.utils import timezone

from . import onboarding_state, registry_client
from .participant_keys import get_encryption_keys, get_signing_keys

logger = logging.getLogger("gateway")


class OnboardingError(Exception):
    pass


UNCONFIRMED_DOMAIN_SENTINEL = "CONFIRM_BEFORE_USE"


def get_verification_file_content() -> str:
    request_id = onboarding_state.get_verification_request_id()
    if not request_id:
        raise OnboardingError("No domain-verification request_id has been set yet")
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


def _build_subscribe_payload(*, domain: str, request_id: str) -> dict:
    signing_pub, _ = get_signing_keys()
    encryption_pub, _ = get_encryption_keys()
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


def submit_subscribe(domain: str) -> dict:
    """Submits Subscribe to the Registry for this domain. Refuses to proceed unless the
    manual approval gate has been passed."""
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
    payload = _build_subscribe_payload(domain=domain, request_id=request_id)
    try:
        result = registry_client.subscribe(payload)
    except requests.HTTPError as exc:
        detail = exc.response.text if exc.response is not None else str(exc)
        onboarding_state.set_status(domain, "FAILED", last_error=detail)
        raise OnboardingError(f"Subscribe rejected by Registry: {detail}") from exc

    return onboarding_state.set_status(domain, result["status"])


def handle_on_subscribe(payload: dict) -> dict:
    """Handles Registry's inbound POST /on_subscribe callback — see
    BAP/backend/core/onboarding_service.py's handle_on_subscribe docstring for the full
    design rationale (identical here, modulo file-backed state instead of a DB row)."""
    _, encryption_priv = get_encryption_keys()
    registry_identity = registry_client.get_registry_identity()
    answer = decrypt_challenge(
        encrypted_challenge=payload["challenge"],
        own_private_key_b64=encryption_priv,
        peer_public_key_b64_der=registry_identity["encryption_public_key"],
    )
    onboarding_state.mark_all_under_subscription_as_subscribed()
    return {"answer": answer}
