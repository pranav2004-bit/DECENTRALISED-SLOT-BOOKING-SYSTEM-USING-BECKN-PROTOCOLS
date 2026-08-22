"""livetracker7.md §2.3 Test Gate piece: `deactivate_participant_domain` (and the
`deactivate_participant` management command wrapping it) — used to narrow BPP-Beauty's
Registry subscription down from all 3 domains to Beauty-only once BPP-Medical/
BPP-Automotive exist as their own separate participants.
"""

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from core import registry_service
from core.models import AuditLogEntry, Participant


def _create_participant(*, subscriber_id="bpp-backend.local", domain="BECKN:AUTO01"):
    now = timezone.now()
    return Participant.objects.create(
        subscriber_id=subscriber_id,
        subscriber_url="https://bpp.example.com",
        participant_type="sellerApp",
        domain=domain,
        unique_key_id="key-1",
        signing_public_key="fake-signing-pub",
        encryption_public_key="fake-encryption-pub",
        key_valid_from=now,
        key_valid_until=now + timezone.timedelta(days=365),
        callback_url="/on_subscribe",
        status=Participant.Status.SUBSCRIBED,
    )


@pytest.mark.django_db
def test_deactivate_participant_domain_sets_status_unsubscribed():
    participant = _create_participant()

    result = registry_service.deactivate_participant_domain(
        subscriber_id="bpp-backend.local", domain="BECKN:AUTO01", participant_type="sellerApp"
    )

    participant.refresh_from_db()
    assert participant.status == Participant.Status.UNSUBSCRIBED
    assert result.id == participant.id


@pytest.mark.django_db
def test_deactivate_participant_domain_writes_an_audit_log_entry():
    _create_participant()

    registry_service.deactivate_participant_domain(
        subscriber_id="bpp-backend.local", domain="BECKN:AUTO01", participant_type="sellerApp"
    )

    entry = AuditLogEntry.objects.get(
        subscriber_id="bpp-backend.local", event_type="UNSUBSCRIBED"
    )
    assert entry.detail["domain"] == "BECKN:AUTO01"


@pytest.mark.django_db
def test_deactivate_participant_domain_does_not_touch_other_domains():
    """A real isolation check: deactivating Automotive must never affect this same
    subscriber's still-genuinely-served Beauty subscription."""
    _create_participant(domain="BECKN:AUTO01")
    beauty = _create_participant(domain="ONDC:RET13")

    registry_service.deactivate_participant_domain(
        subscriber_id="bpp-backend.local", domain="BECKN:AUTO01", participant_type="sellerApp"
    )

    beauty.refresh_from_db()
    assert beauty.status == Participant.Status.SUBSCRIBED


@pytest.mark.django_db
def test_deactivate_participant_domain_raises_for_no_matching_row():
    with pytest.raises(registry_service.ParticipantNotFoundError):
        registry_service.deactivate_participant_domain(
            subscriber_id="no-such-participant.local",
            domain="ONDC:RET13",
            participant_type="sellerApp",
        )


@pytest.mark.django_db
def test_deactivate_participant_management_command_succeeds():
    _create_participant()

    call_command("deactivate_participant", "bpp-backend.local", "BECKN:AUTO01")

    participant = Participant.objects.get(subscriber_id="bpp-backend.local", domain="BECKN:AUTO01")
    assert participant.status == Participant.Status.UNSUBSCRIBED


@pytest.mark.django_db
def test_deactivate_participant_management_command_raises_command_error_for_no_match():
    with pytest.raises(CommandError):
        call_command("deactivate_participant", "no-such-participant.local", "ONDC:RET13")
