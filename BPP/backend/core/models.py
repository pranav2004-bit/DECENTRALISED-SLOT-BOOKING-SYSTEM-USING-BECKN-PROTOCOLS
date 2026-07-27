"""BPP onboarding state — per-domain progress toward SUBSCRIBED
(livetracker1.md Phase 3.2). One row per ONDC domain-category this BPP serves
(healthcare/automotive/beauty — see BPP_details_v1.1.md, DOMAIN_* settings).
"""

import uuid

from django.conf import settings
from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.db import models


class BusinessAccountManager(BaseUserManager):
    """Standard Django custom-user-model manager, mirroring BAP's `CustomerManager`."""

    def create_user(
        self,
        contact: str,
        business_name: str,
        password: str | None = None,
        domain_code: str | None = None,
        **extra_fields,
    ):
        if not contact:
            raise ValueError("Business accounts must have a contact (email or phone).")
        account = self.model(
            contact=contact,
            business_name=business_name,
            domain_code=domain_code or settings.DOMAIN_BEAUTY,
            **extra_fields,
        )
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
    """One business-account login (salon owner/admin) — livetracker2.md §2.2.

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

    Phase 4.3 (livetracker2.md §4.3): individual staff-level logins, deferred from §2.2.
    Rather than a second `AUTH_USER_MODEL` (Django only supports one per project) or a
    custom auth backend, a staff account is just another `BusinessAccount` row with
    `role=STAFF` and `managed_by` pointing at the owner account — it reuses 100% of the
    existing session/auth/rate-limit/URL plumbing (same login endpoint, same Redis-backed
    session, same `authenticate()`/`login()` calls) with zero new auth machinery. A staff
    account cannot itself create other staff, resources, or locations (owner-only actions
    in `views.py`); it can only self-manage the availability of whichever `Resource` the
    owner has explicitly assigned to it (`Resource.domain_data["assigned_staff_id"]`).
    """

    class Role(models.TextChoices):
        OWNER = "OWNER", "Owner"
        STAFF = "STAFF", "Staff"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    contact = models.CharField(max_length=255, unique=True)
    business_name = models.CharField(max_length=255)

    role = models.CharField(max_length=20, choices=Role.choices, default=Role.OWNER)
    # Set only for STAFF accounts — the owning BusinessAccount that created this staff
    # login. CASCADE: a staff login has no meaning once the business itself is gone.
    managed_by = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="staff_members",
    )

    # Phase 4.1 (livetracker2.md) — real gap found via audit before implementing: BPP was
    # single-domain-only everywhere (catalog/search hardcoded to Beauty), with no way to
    # even represent "this business is a Healthcare provider, not a salon." A business
    # realistically operates in one domain (a clinic isn't also a salon), so this lives on
    # the account, not on individual Resources. Defaults to Beauty for backward
    # compatibility with every business created before this field existed.
    domain_code = models.CharField(max_length=100, default=settings.DOMAIN_BEAUTY)

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = BusinessAccountManager()

    USERNAME_FIELD = "contact"
    REQUIRED_FIELDS = ["business_name"]

    class Meta:
        constraints = [
            # Defense in depth: an OWNER must never have a manager, and a STAFF account
            # must always have one — enforced at the DB level, not just in view code.
            models.CheckConstraint(
                condition=(
                    models.Q(role="OWNER", managed_by__isnull=True)
                    | models.Q(role="STAFF", managed_by__isnull=False)
                ),
                name="business_account_role_managed_by_consistency",
            ),
        ]

    def __str__(self) -> str:
        return f"BusinessAccount({self.contact})"


class Location(models.Model):
    """Phase 4.3 (livetracker2.md §4.3) — "richer business profile management
    (multi-location support)". A business owns one or more physical locations;
    `Resource.domain_data["location_id"]` (the shared library's generic no-fork
    extension point, per its own docstring) opaquely links a Resource to one, the same
    pattern already used for staff assignment above — no schema change to the shared
    `shared/inventory_core` app needed.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        BusinessAccount, on_delete=models.CASCADE, related_name="locations"
    )
    name = models.CharField(max_length=255)
    address = models.CharField(max_length=500, blank=True)
    city = models.CharField(max_length=255, blank=True)
    is_primary = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"Location({self.name}, business={self.business_id})"


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
