"""Tests for the 2026-09-30 domain-agnostic fix to `onboarding_rotate_keys`
(mirrors `beckn-gateway/core/test_key_rotation.py`'s identical tests for Gateway's own
copy of this same bug — see that command's docstring for the full incident writeup).
BAP had zero automated coverage for this command before this file existed (only the
lower-level `participant_keys.rotate_signing_key()` was tested — RUNBOOK.md's "live-
verified end-to-end" claim was from a real rotation run against the real stack, not an
automated test)."""

import json
from io import StringIO

import pytest
import responses
from django.core.management import call_command
from django.core.management.base import CommandError

from core import onboarding_service, participant_keys
from core.models import OnboardingStatus


@pytest.fixture
def rotation_settings(settings, tmp_path):
    settings.SIGNING_PRIVATE_KEY_PATH = str(tmp_path / "signing.json")
    settings.ENCRYPTION_PRIVATE_KEY_PATH = str(tmp_path / "encryption.json")
    settings.SUBSCRIBER_ID = "bap.example.com"
    settings.UNIQUE_KEY_ID = "key-1"
    settings.SUBSCRIBER_URL = "https://bap.example.com"
    yield settings


def _mark_subscribed(domain: str) -> None:
    onboarding_service.approve(domain)
    status = OnboardingStatus.objects.get(domain=domain)
    status.status = OnboardingStatus.Status.SUBSCRIBED
    status.save(update_fields=["status"])


@pytest.mark.django_db
def test_command_raises_when_no_domain_is_subscribed_yet(rotation_settings):
    with pytest.raises(CommandError, match="No SUBSCRIBED domains"):
        call_command("onboarding_rotate_keys", "--force")


@pytest.mark.django_db
def test_command_reconfirms_every_subscribed_domain_with_the_same_rotated_key(
    rotation_settings,
):
    """The actual regression test for the bug: a BAP subscribed to more than one
    domain (the architecture explicitly supports up to 3 — RUNBOOK.md) must have EVERY
    one of them re-Subscribed with the one new key from a single rotation run, not just
    whichever domain used to be hardcoded into the command/cron label."""
    for domain in ("ONDC:RET13", "ONDC:SRV13"):
        _mark_subscribed(domain)
    participant_keys.get_encryption_keys()

    seen_domains = []
    seen_keys = []

    def subscribe_callback(request):
        body = json.loads(request.body)
        seen_domains.append(body["message"]["network_participant"][0]["domain"])
        seen_keys.append(body["message"]["entity"]["key_pair"]["signing_public_key"])
        return (200, {}, json.dumps({"status": "UNDER_SUBSCRIPTION"}))

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST, "http://registry:8000/subscribe", callback=subscribe_callback
        )
        out = StringIO()
        call_command("onboarding_rotate_keys", "--force", stdout=out)

    assert sorted(seen_domains) == ["ONDC:RET13", "ONDC:SRV13"]
    assert seen_keys[0] == seen_keys[1]  # both domains confirmed with the identical new key
    assert "Re-Subscribed ONDC:RET13" in out.getvalue()
    assert "Re-Subscribed ONDC:SRV13" in out.getvalue()


@pytest.mark.django_db
def test_command_persists_nothing_if_one_of_several_domains_fails(rotation_settings):
    """The documented, accepted edge case: if domain 1 of 2 succeeds and domain 2
    fails, disk must stay on the OLD key — even though Registry itself now disagrees
    for the domain that did succeed. That residual mismatch is recovered by hand
    (RUNBOOK.md), not by this command."""
    for domain in ("ONDC:RET13", "ONDC:SRV13"):
        _mark_subscribed(domain)
    original_signing_pub, _ = participant_keys.get_signing_keys()
    participant_keys.get_encryption_keys()

    def subscribe_callback(request):
        body = json.loads(request.body)
        if body["message"]["network_participant"][0]["domain"] == "ONDC:RET13":
            return (200, {}, json.dumps({"status": "UNDER_SUBSCRIPTION"}))
        return (422, {}, json.dumps({"error": {"code": "X", "message": "rejected"}}))

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST, "http://registry:8000/subscribe", callback=subscribe_callback
        )
        with pytest.raises(CommandError, match="1 of 2"):
            call_command("onboarding_rotate_keys", "--force")

    assert participant_keys.get_signing_keys()[0] == original_signing_pub


@pytest.mark.django_db
def test_command_skips_when_not_due_without_force(rotation_settings):
    original_pub, _ = participant_keys.get_signing_keys()
    out = StringIO()
    call_command("onboarding_rotate_keys", stdout=out)
    assert "not due" in out.getvalue()
    assert participant_keys.get_signing_keys()[0] == original_pub
