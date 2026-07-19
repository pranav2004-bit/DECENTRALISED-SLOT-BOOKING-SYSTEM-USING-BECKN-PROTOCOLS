"""BPP onboarding state — per-domain progress toward SUBSCRIBED
(livetracker1.md Phase 3.2). One row per ONDC domain-category this BPP serves
(healthcare/automotive/beauty — see BPP_details_v1.1.md, DOMAIN_* settings).
"""

import uuid

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.db import models


class BusinessAccountManager(BaseUserManager):
    """Standard Django custom-user-model manager, mirroring BAP's `CustomerManager`."""

    def create_user(
        self, contact: str, business_name: str, password: str | None = None, **extra_fields
    ):
        if not contact:
            raise ValueError("Business accounts must have a contact (email or phone).")
        account = self.model(contact=contact, business_name=business_name, **extra_fields)
        account.set_password(password)
        account.save(using=self._db)
        return account

    def create_superuser(
        self, contact: str, business_name: str, password: str | None = None, **extra_fields
    ):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        return self.create_user(contact, business_name, password, **extra_fields)


class BusinessAccount(AbstractBaseUser, PermissionsMixin):
    """One business-account login (salon owner/admin) — livetracker2.md §2.2. Deliberately
    **not** individual staff logins (deferred to Phase 4, tagged `[PILOT]` there): one
    account per business, not one per employee.

    **Provider Lifecycle Management** (`ACTIVE`/`INACTIVE`) reuses Django's own `is_active`
    flag, same reasoning as BAP's `Customer.is_active` (§2.1) — `authenticate()` already
    refuses an inactive account, and `visible_resources()` in `catalog.py` filters a
    deactivated business's `Resource`s out of what "search" can see, satisfying this
    phase's other Test Gate requirement with the same real flag, not a second one.

    **Provider Configuration Management** (the Provider Management Module's other named
    peer) is satisfied by the `AvailabilityCalendar` itself (§1.1/§2.2) — a business's
    operating-hours/schedule configuration *is* its configuration management here, not a
    separate settings screen. Documented explicitly as the mapping, not silently
    unaddressed.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    contact = models.CharField(max_length=255, unique=True)
    business_name = models.CharField(max_length=255)

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = BusinessAccountManager()

    USERNAME_FIELD = "contact"
    REQUIRED_FIELDS = ["business_name"]

    def __str__(self) -> str:
        return f"BusinessAccount({self.contact})"


class OnboardingStatus(models.Model):
    class Status(models.TextChoices):
        NOT_STARTED = "NOT_STARTED", "Not Started"
        AWAITING_APPROVAL = "AWAITING_APPROVAL", "Awaiting Approval"
        UNDER_SUBSCRIPTION = "UNDER_SUBSCRIPTION", "Under Subscription"
        SUBSCRIBED = "SUBSCRIBED", "Subscribed"
        FAILED = "FAILED", "Failed"

    domain = models.CharField(max_length=100, unique=True)

    # Simulates the ONDC Network Participant Portal's human-reviewed whitelisting gate
    # (livetracker1.md 3.2: "expect a manual review gate ... don't auto-approve").
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
    Singleton by convention (pk=1) — see BAP/backend/core/models.py's SiteVerification
    docstring for the full rationale (identical here)."""

    request_id = models.CharField(max_length=255)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"SiteVerification(request_id={self.request_id})"
