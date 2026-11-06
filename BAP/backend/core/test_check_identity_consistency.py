"""2026-10-21 incident fix: check_identity_consistency catches BAP's on-disk signing
key drifting away from what Registry has on file for a domain BAP believes itself
SUBSCRIBED for — the exact class of bug that caused every real `/search` call to
fail with a cryptic 401 until someone manually traced it back (RUNBOOK.md).
"""

from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from core.models import OnboardingStatus


@pytest.mark.django_db
def test_passes_when_every_subscribed_domains_key_matches_registry():
    OnboardingStatus.objects.create(domain="ONDC:RET13", status=OnboardingStatus.Status.SUBSCRIBED)
    with patch("core.participant_keys.get_signing_keys", return_value=("current-pub-key", "priv")):
        with patch(
            "core.registry_client.lookup",
            return_value=[{"domain": "ONDC:RET13", "signing_public_key": "current-pub-key"}],
        ):
            out = StringIO()
            call_command("check_identity_consistency", stdout=out)
    assert "Identity consistent" in out.getvalue()


@pytest.mark.django_db
def test_fails_loud_when_registrys_key_does_not_match_the_current_on_disk_key():
    """The exact real incident: Registry still has a stale key on file (e.g. from a
    secrets volume that was recreated empty, silently generating a new local key) —
    must be reported as an error, not silently ignored."""
    OnboardingStatus.objects.create(domain="ONDC:RET13", status=OnboardingStatus.Status.SUBSCRIBED)
    with patch("core.participant_keys.get_signing_keys", return_value=("new-pub-key", "priv")):
        with patch(
            "core.registry_client.lookup",
            return_value=[{"domain": "ONDC:RET13", "signing_public_key": "stale-pub-key"}],
        ):
            with pytest.raises(CommandError, match="Identity drift detected"):
                call_command("check_identity_consistency")


@pytest.mark.django_db
def test_fails_loud_when_a_subscribed_domain_has_no_registry_record_at_all():
    OnboardingStatus.objects.create(domain="BECKN:AUTO01", status=OnboardingStatus.Status.SUBSCRIBED)
    with patch("core.participant_keys.get_signing_keys", return_value=("current-pub-key", "priv")):
        with patch("core.registry_client.lookup", return_value=[]):
            with pytest.raises(CommandError, match="no Registry record found"):
                call_command("check_identity_consistency")


@pytest.mark.django_db
def test_treats_its_own_failed_registry_lookup_as_seriously_as_a_mismatch():
    """A failed self-lookup can itself be a symptom of the same drift (Registry
    verifies the call against whichever domain row it finds first) — must not be
    swallowed as a silent pass."""
    OnboardingStatus.objects.create(domain="ONDC:RET13", status=OnboardingStatus.Status.SUBSCRIBED)
    with patch("core.participant_keys.get_signing_keys", return_value=("current-pub-key", "priv")):
        with patch("core.registry_client.lookup", side_effect=Exception("401 Unauthorized")):
            with pytest.raises(CommandError, match="Could not verify identity"):
                call_command("check_identity_consistency")


@pytest.mark.django_db
def test_no_subscribed_domains_is_a_clean_no_op_not_an_error():
    out = StringIO()
    call_command("check_identity_consistency", stdout=out)
    assert "nothing to verify" in out.getvalue()
