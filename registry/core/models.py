"""Registry data model — per registry_details_v1.1.md §8 (Data Ownership) and the
confirmed Subscription schema (protocol_compliance_notes_v1.1.md §A.2, §B.3).

Deliberately excludes GST/PAN/business-KYC fields for now: those are an ONDC onboarding
portal concern (protocol_compliance_notes_v1.1.md §B.3), not part of the core Beckn
Subscription object this Registry implementation targets — adding them now would be
scope creep ahead of Phase 3 actually needing them. Registry never stores catalogs,
inventory, orders, payments, or customer information (registry_details_v1.1.md §8).
"""

import uuid

from django.db import models


class Participant(models.Model):
    """Mirrors the confirmed core `Subscription` object
    (protocol_compliance_notes_v1.1.md §A.2), with ONDC's real nested Subscribe fields
    (§B.3) flattened onto it. One row per (subscriber_id, domain, type) registration."""

    class Status(models.TextChoices):
        INITIATED = "INITIATED", "Initiated"
        UNDER_SUBSCRIPTION = "UNDER_SUBSCRIPTION", "Under Subscription"
        SUBSCRIBED = "SUBSCRIBED", "Subscribed"
        INVALID_SSL = "INVALID_SSL", "Invalid SSL"
        UNSUBSCRIBED = "UNSUBSCRIBED", "Unsubscribed"

    class ParticipantType(models.TextChoices):
        BUYER_APP = "buyerApp", "Buyer App"
        SELLER_APP = "sellerApp", "Seller App"
        GATEWAY = "gateway", "Gateway"

    subscriber_id = models.CharField(max_length=255, db_index=True)
    subscriber_url = models.URLField(max_length=500)
    participant_type = models.CharField(max_length=20, choices=ParticipantType.choices)
    domain = models.CharField(max_length=100)
    country = models.CharField(max_length=10, default="IND")
    city_code = models.JSONField(default=list, blank=True)

    unique_key_id = models.CharField(max_length=255)
    signing_public_key = models.TextField()
    encryption_public_key = models.TextField()
    key_valid_from = models.DateTimeField()
    key_valid_until = models.DateTimeField()

    callback_url = models.CharField(max_length=500)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.INITIATED)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["subscriber_id", "domain", "participant_type"],
                name="unique_subscriber_domain_type",
            )
        ]
        indexes = [models.Index(fields=["subscriber_id"]), models.Index(fields=["domain"])]

    def __str__(self) -> str:
        return f"{self.subscriber_id} ({self.domain}, {self.participant_type}) [{self.status}]"


class Challenge(models.Model):
    """A single-use, time-bound on_subscribe challenge issued to a Participant
    (protocol_compliance_notes_v1.1.md §A.1/§B.5). Verified once, then marked used —
    real replay-attack protection, not just a design intention."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    participant = models.ForeignKey(
        Participant, on_delete=models.CASCADE, related_name="challenges"
    )
    plaintext_challenge = models.CharField(max_length=255)
    encrypted_challenge = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return f"Challenge({self.id}) for {self.participant.subscriber_id}"

    def is_expired(self) -> bool:
        from django.utils import timezone

        return timezone.now() > self.expires_at

    def is_used(self) -> bool:
        return self.used_at is not None


class AuditLogEntry(models.Model):
    """Append-only audit trail of every registration/verification/status-change event
    (registry_details_v1.1.md §8, per protocol_compliance_notes_v1.1.md §A note on
    audit logging). Never updated or deleted after creation — enforced by convention
    (no update/delete call sites) and by admin readonly config, see admin.py."""

    participant = models.ForeignKey(
        Participant, on_delete=models.SET_NULL, null=True, related_name="audit_entries"
    )
    subscriber_id = models.CharField(
        max_length=255, db_index=True
    )  # kept even if participant is deleted
    event_type = models.CharField(max_length=50)
    detail = models.JSONField(default=dict, blank=True)
    correlation_id = models.CharField(max_length=64, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.created_at} {self.subscriber_id} {self.event_type}"
