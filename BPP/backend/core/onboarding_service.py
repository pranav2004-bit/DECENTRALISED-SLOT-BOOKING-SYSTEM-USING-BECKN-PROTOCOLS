"""Orchestrates BPP's Phase 3.2 onboarding sequence (livetracker1.md): key generation
(participant_keys) -> domain verification -> manual approval gate -> Subscribe ->
on_subscribe challenge response -> SUBSCRIBED. Mirrors BAP/backend/core/onboarding_service.py
— see that module for the full design rationale (FQDN-global verification file, etc.).

One real difference from BAP: BPP's domain codes for Healthcare/Automotive are not yet
confirmed (protocol_compliance_notes_v1.1.md "Remaining Open Items") — submit_subscribe
refuses outright rather than submitting a guessed code to Registry.
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

logger = logging.getLogger("bpp")

UNCONFIRMED_DOMAIN_SENTINEL = "CONFIRM_BEFORE_USE"


class OnboardingError(Exception):
    pass


def get_or_create_status(domain: str) -> OnboardingStatus:
    status, _ = OnboardingStatus.objects.get_or_create(domain=domain)
    return status


def _set_current_verification_request_id(request_id: str) -> None:
    SiteVerification.objects.update_or_create(pk=1, defaults={"request_id": request_id})


def get_verification_file_content() -> str:
    site_verification = SiteVerification.objects.filter(pk=1).first()
    if site_verification is None:
        raise OnboardingError("No domain-verification request_id has been set yet")
    _, signing_priv = get_signing_keys()
    return build_verification_file_content(
        request_id=site_verification.request_id, signing_private_key_b64=signing_priv
    )


def request_domain_verification(*, request_id: str | None = None) -> str:
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
        "context": {"operation": {"ops_no": 2}},  # 2 = BPP registration (confirmed mapping)
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
                    "type": "sellerApp",
                }
            ],
        },
    }


def submit_subscribe(domain: str) -> OnboardingStatus:
    """Submits Subscribe to the Registry for this domain. Refuses to proceed if: the
    domain code is the unconfirmed placeholder (do not guess a code and submit it — per
    livetracker1.md 3.2), the manual approval gate hasn't been passed, or SUBSCRIBER_URL
    isn't configured."""
    if domain == UNCONFIRMED_DOMAIN_SENTINEL or not domain:
        raise OnboardingError(
            f"Domain code is unconfirmed ({domain!r}) — see protocol_compliance_notes_v1.1.md "
            "'Remaining Open Items'. Confirm the real ONDC:SRV## code before subscribing; "
            "do not guess and submit."
        )
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

    # Real, deeper race found live (2026-07-26, root-caused after the first fix below
    # turned out insufficient on its own): Registry's own `handle_subscribe()` dispatches
    # the on_subscribe challenge *synchronously, before responding* — meaning
    # `handle_on_subscribe()` (this row's own only path to SUBSCRIBED) can run and
    # complete *nested inside* the `registry_client.subscribe()` call below, before it
    # ever returns. `handle_on_subscribe()`'s own bulk update only matches rows *already*
    # `UNDER_SUBSCRIPTION` — but this function used to only write that status *after*
    # `registry_client.subscribe()` returned, so at the exact moment the nested challenge
    # fired, this row was still whatever `approve()` had left it at (`AWAITING_APPROVAL`),
    # never matching the filter at all. The challenge would still succeed and return 200
    # to Registry, but silently confirm nothing on BPP's own side — confirmed live by
    # directly cross-checking Registry's own authoritative `Participant.status`
    # (`SUBSCRIBED`) against this row staying stuck, with `handle_on_subscribe`'s bulk
    # `.update()` provably finding zero matching rows at dispatch time. Fixed by marking
    # this row `UNDER_SUBSCRIPTION` *before* calling Registry, not after — so by the time
    # the nested challenge fires, `handle_on_subscribe()` has a real row to match.
    status.status = OnboardingStatus.Status.UNDER_SUBSCRIPTION
    status.save(update_fields=["status", "updated_at"])

    try:
        result = registry_client.subscribe(payload)
    except requests.HTTPError as exc:
        detail = exc.response.text if exc.response is not None else str(exc)
        status.status = OnboardingStatus.Status.FAILED
        status.last_error = detail
        status.save(update_fields=["status", "last_error", "updated_at"])
        raise OnboardingError(f"Subscribe rejected by Registry: {detail}") from exc

    # First-found half of this same race (still needed on its own): even with the row
    # correctly marked before the call above, `handle_on_subscribe()` may have already
    # flipped it to SUBSCRIBED by the time control returns here. Registry's /subscribe
    # response body always literally says `{"status": "UNDER_SUBSCRIPTION"}` regardless
    # of the challenge outcome it just ran — so blindly writing `result["status"]` here
    # would stomp a genuine SUBSCRIBED back down using this function's own stale,
    # pre-HTTP-call `status` object. `handle_on_subscribe` is the one authoritative path
    # to SUBSCRIBED; never downgrade a row it already moved there.
    status.refresh_from_db()
    if status.status != OnboardingStatus.Status.SUBSCRIBED:
        status.status = OnboardingStatus.Status(result["status"])
    status.last_error = ""
    status.save(update_fields=["status", "last_error", "updated_at"])
    return status


def handle_on_subscribe(payload: dict) -> dict:
    """Handles Registry's inbound POST /on_subscribe callback — see
    BAP/backend/core/onboarding_service.py's handle_on_subscribe docstring for the full
    design rationale (identical here)."""
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
