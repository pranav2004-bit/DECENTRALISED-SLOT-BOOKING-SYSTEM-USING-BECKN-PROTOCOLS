"""BAP onboarding state — per-domain progress toward SUBSCRIBED
(livetracker1.md Phase 3.1). One row per domain this BAP participates in (a BAP serving
Healthcare/Automotive/Beauty onboards into each domain separately — see
onboarding_service.py for why one Subscribe call per domain, not a combined array).
"""

from django.db import models


class OnboardingStatus(models.Model):
    class Status(models.TextChoices):
        NOT_STARTED = "NOT_STARTED", "Not Started"
        AWAITING_APPROVAL = "AWAITING_APPROVAL", "Awaiting Approval"
        UNDER_SUBSCRIPTION = "UNDER_SUBSCRIPTION", "Under Subscription"
        SUBSCRIBED = "SUBSCRIBED", "Subscribed"
        FAILED = "FAILED", "Failed"

    domain = models.CharField(max_length=100, unique=True)

    # Simulates the ONDC Network Participant Portal's human-reviewed whitelisting gate
    # (livetracker1.md 3.1: "expect a manual review gate ... don't auto-approve").
    # Only settable via the onboarding_approve management command.
    approved_for_subscribe = models.BooleanField(default=False)

    status = models.CharField(max_length=40, choices=Status.choices, default=Status.NOT_STARTED)
    last_error = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"OnboardingStatus({self.domain}) [{self.status}]"


class SiteVerification(models.Model):
    """The request_id currently signed and served at /ondc-site-verification.html.
    Domain-ownership verification (protocol_compliance_notes_v1.1.md §B.2) proves
    control of the subscriber's FQDN — it is NOT per ONDC domain-category (healthcare/
    automotive/beauty); Registry fetches exactly `{subscriber_url}/ondc-site-verification.html`
    with no query parameter, so there is one active value at a time, updated immediately
    before each Subscribe call so the served file's signed request_id always matches the
    request_id in that call's payload. Singleton by convention (pk=1), not enforced by a
    DB constraint — see onboarding_service.get_current().
    """

    request_id = models.CharField(max_length=255)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"SiteVerification(request_id={self.request_id})"
