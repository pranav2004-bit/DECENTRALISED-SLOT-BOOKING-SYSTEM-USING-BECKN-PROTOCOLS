"""BAP onboarding state — per-domain progress toward SUBSCRIBED
(livetracker1.md Phase 3.1). One row per domain this BAP participates in (a BAP serving
Healthcare/Automotive/Beauty onboards into each domain separately — see
onboarding_service.py for why one Subscribe call per domain, not a combined array).
"""

import uuid

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.db import models


class CustomerManager(BaseUserManager):
    """Standard Django custom-user-model manager — `create_user`/`create_superuser` are
    the two entry points Django's own auth machinery (admin, `createsuperuser`) expects."""

    def create_user(self, contact: str, name: str, password: str | None = None, **extra_fields):
        if not contact:
            raise ValueError("Customers must have a contact (email or phone).")
        customer = self.model(contact=contact, name=name, **extra_fields)
        customer.set_password(password)
        customer.save(using=self._db)
        return customer

    def create_superuser(
        self, contact: str, name: str, password: str | None = None, **extra_fields
    ):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        return self.create_user(contact, name, password, **extra_fields)


class Customer(AbstractBaseUser, PermissionsMixin):
    """Minimal customer identity (livetracker2.md §2.1) — an ordinary web-app auth layer,
    entirely separate from the Ed25519/Registry participant-level trust in `livetracker1.md`.
    A custom user model (`AbstractBaseUser`, not `AbstractUser`) because the login identifier
    is `contact` (email or phone), not Django's default `username`.

    **Buyer Lifecycle Management** (`ACTIVE`/`INACTIVE`) is deliberately just Django's own
    `is_active` flag, not a separate custom enum: Django's `authenticate()` already refuses to
    return a user where `is_active=False`, which is exactly the "a deactivated account cannot
    log in" behavior this phase's Test Gate requires — reusing that battle-tested gate instead
    of adding a second, potentially-drifting field for the same concept. Toggled via Django
    admin (`core/admin.py`), the standard real mechanism for this, not a bespoke command.

    **Buyer Configuration Management** (basic notification preference) is `notify_by_email`.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    contact = models.CharField(max_length=255, unique=True)
    name = models.CharField(max_length=255)

    notify_by_email = models.BooleanField(default=True)

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = CustomerManager()

    USERNAME_FIELD = "contact"
    REQUIRED_FIELDS = ["name"]

    def __str__(self) -> str:
        return f"Customer({self.contact})"


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


class SearchSession(models.Model):
    """One real Beckn search transaction (livetracker2.md §3.1) — created the moment a
    customer triggers a search, accumulates real `on_search` catalog results as they
    arrive asynchronously (protocol_compliance_notes_v1.1.md §H.1: results are never
    available synchronously, only via a later callback). A transaction can accumulate
    results from more than one `SUBSCRIBED` BPP over its lifetime — `results` is a
    list appended to, never overwritten. Anonymous search is allowed (`customer` is
    nullable) — matching ordinary e-commerce UX where browsing doesn't require login,
    the same reasoning this project already applied by not gating `resources_list_view`
    on BPP's side behind auth."""

    transaction_id = models.CharField(max_length=255, unique=True)
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, null=True, blank=True)
    query = models.CharField(max_length=500, blank=True)
    domain = models.CharField(max_length=100)
    results = models.JSONField(default=list)

    # §3.2 Selection — one active selection per transaction, sharing the same
    # transaction_id per real Beckn semantics (search -> select -> init -> confirm is
    # one continuous transaction, not separate ones). `selected_order` holds the real
    # Order (with its quote) once on_select arrives successfully; `selected_error`
    # holds the real error instead when the BPP rejects the selection (e.g. the slot
    # was taken microseconds earlier) — the two are mutually exclusive, never both set.
    selected_order = models.JSONField(null=True, blank=True)
    selected_error = models.JSONField(null=True, blank=True)

    # The specific BPP a successful selection actually resolved to — needed to target
    # /init at the same BPP again (§3.3), since `selected_order.provider.id` alone
    # isn't reliably enough to re-derive it (provider ids are each BPP's own choice,
    # not guaranteed unique across different BPPs). Set alongside selected_order,
    # blank while no successful selection exists yet.
    selected_bpp_id = models.CharField(max_length=255, blank=True)
    selected_bpp_uri = models.CharField(max_length=500, blank=True)

    # §3.3 Initialization — same mutually-exclusive success/error pattern as
    # selected_order/selected_error, one step further along the same continuous
    # transaction. `init_order` holds the real Order (with its real Quotation,
    # price+breakup[]+ttl) once on_init arrives successfully.
    init_order = models.JSONField(null=True, blank=True)
    init_error = models.JSONField(null=True, blank=True)

    # §3.4 Confirmation — same mutually-exclusive success/error pattern, the final
    # step of the same continuous transaction. `confirmed_order` holds the real,
    # permanently-confirmed Order (status ACTIVE, a real payments[] stub) once
    # on_confirm arrives successfully.
    confirmed_order = models.JSONField(null=True, blank=True)
    confirmed_error = models.JSONField(null=True, blank=True)

    # §3.5 Post-Booking — same mutually-exclusive success/error pattern, all
    # operating on the already-confirmed booking above. `status_order` holds
    # the real, live current Order state; `cancelled_order` the real cancelled
    # Order; `updated_order` the real rescheduled Order; `tracking` the real
    # (always-inactive, for this domain) Tracking object, per
    # protocol_compliance_notes_v1.1.md §L.
    status_order = models.JSONField(null=True, blank=True)
    status_error = models.JSONField(null=True, blank=True)
    cancelled_order = models.JSONField(null=True, blank=True)
    cancelled_error = models.JSONField(null=True, blank=True)
    updated_order = models.JSONField(null=True, blank=True)
    updated_error = models.JSONField(null=True, blank=True)
    tracking = models.JSONField(null=True, blank=True)
    tracking_error = models.JSONField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"SearchSession({self.transaction_id})"
