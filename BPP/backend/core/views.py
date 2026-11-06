import datetime as dt
import json
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib.auth import (
    authenticate,
    get_user_model,
    login,
    logout,
    password_validation,
    update_session_auth_hash,
)
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import validate_email
from django.db import models, transaction
from django.db.models.functions import TruncDate
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie
from django.views.decorators.http import require_http_methods
from django_observability.errors import error_response
from django_observability.rate_limit import by_authenticated_account, rate_limit
from inventory_core.domain_adapter import get_adapter
from inventory_core.models import AvailabilityCalendar, Booking, Resource, Slot
from postal_directory.models import Circle, Division, Pincode

from . import (
    availability_service,
    cancel_service,
    confirm_service,
    init_service,
    onboarding_service,
    pagination,
    payment_status_service,
    rating_service,
    search_service,
    select_service,
    status_service,
    support_service,
    track_service,
    update_service,
)
from .catalog import visible_resources
from .catalog_cache import invalidate_catalog_cache
from .models import Location
from .realtime import broadcast_slot_update

# §3.7: caps the real number of Slot rows one availability-creation call can
# materialize — an honest, deliberately-generous ceiling (just over a year), not a
# number derived from real production capacity data (none exists yet).
MAX_AVAILABILITY_RANGE_DAYS = 366

# Profile page's Status toggle — a business can't go "Live" with a half-filled
# listing, so every one of these BusinessAccount fields must be non-empty before
# is_accepting_bookings can be set True (business_profile_update_view). Mirrored
# in BPP/web/app/profile/page.tsx's own REQUIRED_BUSINESS_INFO_FIELDS.
REQUIRED_FOR_LIVE_PROFILE_FIELDS = (
    "business_name",
    "description",
    "public_phone",
    "public_email",
    "address",
    "state",
    "division",
    "pincode",
)

# Profile page's State dropdown — the full list of India's states/UTs, shown
# regardless of whether shared.postal_directory has any Circle/Division/Pincode
# data for that state yet (currently only Andhra Pradesh does). Mirrored in
# BPP/web/lib/constants.ts's own ALL_INDIA_STATES — duplicated, not shared across
# the frontend/backend language boundary, same convention as SPECIALISATIONS
# (ADR-0004, see core/healthcare_adapter.py's own comment on that).
ALL_INDIA_STATES = (
    "Andaman and Nicobar Islands",
    "Andhra Pradesh",
    "Arunachal Pradesh",
    "Assam",
    "Bihar",
    "Chandigarh",
    "Chhattisgarh",
    "Dadra and Nagar Haveli and Daman and Diu",
    "Delhi",
    "Goa",
    "Gujarat",
    "Haryana",
    "Himachal Pradesh",
    "Jammu and Kashmir",
    "Jharkhand",
    "Karnataka",
    "Kerala",
    "Ladakh",
    "Lakshadweep",
    "Madhya Pradesh",
    "Maharashtra",
    "Manipur",
    "Meghalaya",
    "Mizoram",
    "Nagaland",
    "Odisha",
    "Puducherry",
    "Punjab",
    "Rajasthan",
    "Sikkim",
    "Tamil Nadu",
    "Telangana",
    "Tripura",
    "Uttar Pradesh",
    "Uttarakhand",
    "West Bengal",
)


@require_http_methods(["GET"])
def ondc_site_verification_view(request):
    """Serves the signed domain-ownership verification file at the exact path ONDC's
    Registry fetches — see BAP/backend/core/views.py's equivalent for the full rationale."""
    try:
        content = onboarding_service.get_verification_file_content()
    except onboarding_service.OnboardingError as exc:
        return HttpResponse(str(exc), status=404)
    return HttpResponse(content, content_type="text/html")


@csrf_exempt
@require_http_methods(["POST"])
def on_subscribe_view(request):
    """Registry-initiated callback — decrypts the challenge and returns the answer."""
    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Request body is not valid JSON"}, status=400)

    try:
        result = onboarding_service.handle_on_subscribe(payload)
    except Exception as exc:
        return JsonResponse({"error": f"Challenge decryption failed: {exc}"}, status=400)

    return JsonResponse(result, status=200)


def _business_account_json(account) -> dict:
    data = {
        "id": str(account.id),
        "business_name": account.business_name,
        "contact": account.contact,
        "domain_code": account.domain_code,
        "role": account.role,
        "managed_by": str(account.managed_by_id) if account.managed_by_id else None,
        "is_accepting_bookings": account.is_accepting_bookings,
        "description": account.description,
        "public_phone": account.public_phone,
        "public_email": account.public_email,
        "address": account.address,
        "state": account.state,
        "division": account.division,
        "pincode": account.pincode,
        # "Date of joining" — bounds how far back the Orders page's history
        # browser (year → month → date) goes.
        "created_at": account.created_at.isoformat(),
    }
    # §4.3: a staff account's own dashboard needs to know which Resource(s) it's
    # actually allowed to self-manage — cheaper to include here than a second round trip.
    if account.role == account.Role.STAFF:
        data["assigned_resource_ids"] = list(
            Resource.objects.filter(domain_data__assigned_staff_id=str(account.id)).values_list(
                "id", flat=True
            )
        )
        data["assigned_resource_ids"] = [str(rid) for rid in data["assigned_resource_ids"]]
    # livetracker3.md §7.1: an owner's own dashboard needs to know which Resource(s) it
    # owns, the same "cheaper here than a second round trip" reasoning as the staff-side
    # field above — an owner previously got nothing back from /api/v1/auth/me listing
    # its own resources at all, a real gap found while designing this section.
    elif account.role == account.Role.OWNER:
        data["owned_resource_ids"] = [
            str(rid)
            for rid in Resource.objects.filter(owner_ref=str(account.id)).values_list(
                "id", flat=True
            )
        ]
    return data


@csrf_exempt
@require_http_methods(["POST"])
@rate_limit(limit_per_minute=30, scope="business-profile-update", key_func=by_authenticated_account)
def business_profile_update_view(request):
    """Profile page (business name edit + Live/Offline toggle) — owner-only, partial
    update: only the fields actually present in the payload are changed. Toggling
    `is_accepting_bookings` off must take effect in search immediately, same as any
    other real catalog content change, so this invalidates the cache exactly like
    `resource_create_view` does — not left to the TTL safety net."""
    if not request.user.is_authenticated:
        return error_response("UNAUTHORIZED", "not logged in", 401)
    if request.user.role != get_user_model().Role.OWNER:
        return error_response(
            "FORBIDDEN", "only a business owner account can update the business profile", 403
        )

    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return error_response("VALIDATION_ERROR", "Request body is not valid JSON", 400)

    changed = False

    if "business_name" in payload:
        business_name = (payload.get("business_name") or "").strip()
        if not business_name:
            return error_response(
                "VALIDATION_ERROR", "business_name is required", 400, field="business_name"
            )
        if len(business_name) > 255:
            return error_response(
                "VALIDATION_ERROR", "business_name is too long (max 255)", 400,
                field="business_name",
            )
        request.user.business_name = business_name
        changed = True

    if "is_accepting_bookings" in payload:
        is_accepting_bookings = payload.get("is_accepting_bookings")
        if not isinstance(is_accepting_bookings, bool):
            return error_response(
                "VALIDATION_ERROR", "is_accepting_bookings must be a boolean", 400,
                field="is_accepting_bookings",
            )
        request.user.is_accepting_bookings = is_accepting_bookings
        changed = True

    # Public profile fields — all optional (an empty string clears the field, same as
    # never having set it), so no "is required" check like business_name gets above.
    if "description" in payload:
        description = (payload.get("description") or "").strip()
        if len(description) > 1000:
            return error_response(
                "VALIDATION_ERROR", "description is too long (max 1000)", 400,
                field="description",
            )
        request.user.description = description
        changed = True

    if "public_phone" in payload:
        public_phone = (payload.get("public_phone") or "").strip()
        if len(public_phone) > 30:
            return error_response(
                "VALIDATION_ERROR", "public_phone is too long (max 30)", 400,
                field="public_phone",
            )
        request.user.public_phone = public_phone
        changed = True

    if "public_email" in payload:
        public_email = (payload.get("public_email") or "").strip()
        if public_email:
            try:
                validate_email(public_email)
            except DjangoValidationError:
                return error_response(
                    "VALIDATION_ERROR", "public_email is not a valid email address", 400,
                    field="public_email",
                )
        request.user.public_email = public_email
        changed = True

    if "address" in payload:
        address = (payload.get("address") or "").strip()
        if len(address) > 500:
            return error_response(
                "VALIDATION_ERROR", "address is too long (max 500)", 400, field="address"
            )
        request.user.address = address
        changed = True

    # State -> Division -> Pincode cascade: each is validated against the fixed
    # list/real DB rows it was picked from, same "must be real, not free text"
    # reasoning as pincode below. Cleared together when state changes on the
    # frontend, but this view doesn't assume that — each field is only checked
    # against what's actually present in this same payload.
    if "state" in payload:
        state = (payload.get("state") or "").strip()
        if state and state not in ALL_INDIA_STATES:
            return error_response(
                "VALIDATION_ERROR", "not a recognised state", 400, field="state"
            )
        request.user.state = state
        changed = True

    if "division" in payload:
        division = (payload.get("division") or "").strip()
        if division and not Division.objects.filter(name=division).exists():
            return error_response(
                "VALIDATION_ERROR", "not a recognised division", 400, field="division"
            )
        request.user.division = division
        changed = True

    if "pincode" in payload:
        pincode = (payload.get("pincode") or "").strip()
        # Must be a real code from the fixed reference list, not free text — an
        # empty string still clears the field, same as every other optional
        # profile field above.
        if pincode and not Pincode.objects.filter(code=pincode).exists():
            return error_response(
                "VALIDATION_ERROR", "not a recognised pincode", 400, field="pincode"
            )
        request.user.pincode = pincode
        changed = True

    # Enforces "Live" only ever means a complete listing, not just "toggled on" —
    # evaluated against the fully-updated in-memory account (every field block
    # above has already run), so this also catches turning is_accepting_bookings
    # on and clearing a required field in the very same request, not just the
    # simple case of toggling on with everything already filled in. Runs on any
    # change that leaves the account live, not only when is_accepting_bookings
    # itself was in this payload — an already-live account can't be edited into
    # an incomplete state either, so the invariant holds at every save, not just
    # the moment of turning on.
    if changed and request.user.is_accepting_bookings:
        missing = [
            field
            for field in REQUIRED_FOR_LIVE_PROFILE_FIELDS
            if not getattr(request.user, field).strip()
        ]
        if missing:
            return error_response(
                "VALIDATION_ERROR",
                "complete every business info field before going live",
                400,
                field="is_accepting_bookings",
            )

    if changed:
        request.user.save()
        invalidate_catalog_cache(request.user.domain_code)

    return JsonResponse(_business_account_json(request.user), status=200)


@csrf_exempt
@require_http_methods(["POST"])
@rate_limit(limit_per_minute=5, scope="business-password-change", key_func=by_authenticated_account)
def business_password_change_view(request):
    """Profile page's Password section — any authenticated account (owner or staff)
    may change its own password; unlike business_profile_update_view this is NOT
    owner-only, since a password belongs to the individual login, not the business
    identity.

    `update_session_auth_hash()` after `set_password()` is the one easy-to-miss piece:
    Django ties a session's validity to a hash derived from the password, so without
    this call the user would be silently logged out of their own current session the
    instant they changed their own password — the current tab included. Calling it
    keeps this exact session (and therefore every other open tab/request using the
    same session cookie) authenticated against the new password immediately, with no
    re-login required anywhere."""
    if not request.user.is_authenticated:
        return error_response("UNAUTHORIZED", "not logged in", 401)

    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return error_response("VALIDATION_ERROR", "Request body is not valid JSON", 400)

    current_password = payload.get("current_password") or ""
    new_password = payload.get("new_password") or ""

    if not current_password:
        return error_response(
            "VALIDATION_ERROR", "current_password is required", 400, field="current_password"
        )
    if not new_password:
        return error_response(
            "VALIDATION_ERROR", "new_password is required", 400, field="new_password"
        )
    if not request.user.check_password(current_password):
        return error_response(
            "VALIDATION_ERROR", "current password is incorrect", 400, field="current_password"
        )
    if new_password == current_password:
        return error_response(
            "VALIDATION_ERROR",
            "new password must be different from your current password",
            400,
            field="new_password",
        )
    try:
        password_validation.validate_password(new_password, user=request.user)
    except DjangoValidationError as exc:
        return error_response(
            "VALIDATION_ERROR", " ".join(exc.messages), 400, field="new_password"
        )

    request.user.set_password(new_password)
    request.user.save()
    update_session_auth_hash(request, request.user)

    return JsonResponse({"status": "ok"}, status=200)


@require_http_methods(["GET"])
@ensure_csrf_cookie
def csrf_token_view(request):
    """Mirrors BAP/backend/core/views.py's `csrf_token_view` exactly — see its
    docstring for the full rationale (§3.7)."""
    return JsonResponse({"status": "ok"}, status=200)


@require_http_methods(["POST"])
@rate_limit(limit_per_minute=5, scope="business-signup")
def business_signup_view(request):
    """One business-account login (livetracker2.md §2.2) — same shape as BAP's customer
    signup (§2.1), for the same reasons (see BAP/backend/core/views.py's `signup_view`).
    Rate-limited (§3.7) for the same reason as BAP's signup/login — a real gap found
    via audit: this bullet's own CSRF fix already treated this as an equally real
    auth-form surface, rate limiting was the one piece left inconsistent.

    §4.1: accepts an optional `domain_code`, defaulting to `settings.DOMAIN_BEAUTY` so
    every existing signup caller that doesn't send it keeps working unchanged. Validated
    against `inventory_core.domain_adapter`'s real registry (whichever domains actually
    have a registered `DomainAdapter` — Beauty/Healthcare so far) rather than accepted
    as an arbitrary string, since an unregistered domain_code would silently orphan a
    business's resources from ever appearing in any catalog."""
    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return error_response("VALIDATION_ERROR", "Request body is not valid JSON", 400)

    business_name = (payload.get("business_name") or "").strip()
    contact = (payload.get("contact") or "").strip()
    password = payload.get("password") or ""
    domain_code = (payload.get("domain_code") or settings.DOMAIN_BEAUTY).strip()

    if not business_name:
        return error_response(
            "VALIDATION_ERROR", "business_name is required", 400, field="business_name"
        )
    if not contact:
        return error_response("VALIDATION_ERROR", "contact is required", 400, field="contact")
    if not password:
        return error_response("VALIDATION_ERROR", "password is required", 400, field="password")
    if len(business_name) > 255:
        return error_response(
            "VALIDATION_ERROR", "business_name is too long (max 255)", 400, field="business_name"
        )
    if len(contact) > 255:
        return error_response(
            "VALIDATION_ERROR", "contact is too long (max 255)", 400, field="contact"
        )
    try:
        get_adapter(domain_code)
    except LookupError:
        return error_response(
            "VALIDATION_ERROR",
            f"unsupported domain_code: {domain_code!r}",
            400,
            field="domain_code",
        )

    BusinessAccount = get_user_model()
    if BusinessAccount.objects.filter(contact=contact).exists():
        return error_response(
            "VALIDATION_ERROR", "an account with this contact already exists", 409, field="contact"
        )

    try:
        password_validation.validate_password(password)
    except DjangoValidationError as exc:
        return error_response("VALIDATION_ERROR", " ".join(exc.messages), 400, field="password")

    account = BusinessAccount.objects.create_user(
        contact=contact, business_name=business_name, password=password, domain_code=domain_code
    )
    return JsonResponse(_business_account_json(account), status=201)


@require_http_methods(["POST"])
@rate_limit(limit_per_minute=5, scope="business-login")
def business_login_view(request):
    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return error_response("VALIDATION_ERROR", "Request body is not valid JSON", 400)

    contact = (payload.get("contact") or "").strip()
    password = payload.get("password") or ""

    if len(contact) > 255:
        return error_response("UNAUTHORIZED", "invalid contact or password", 401)

    account = authenticate(request, username=contact, password=password)
    if account is None:
        return error_response("UNAUTHORIZED", "invalid contact or password", 401)

    login(request, account)
    return JsonResponse(_business_account_json(account), status=200)


@require_http_methods(["POST"])
def business_logout_view(request):
    logout(request)
    return JsonResponse({"status": "ok"}, status=200)


@require_http_methods(["GET"])
def business_me_view(request):
    if not request.user.is_authenticated:
        return error_response("UNAUTHORIZED", "not logged in", 401)
    return JsonResponse(_business_account_json(request.user), status=200)


@csrf_exempt
@require_http_methods(["POST"])
@rate_limit(limit_per_minute=30, scope="resource-create", key_func=by_authenticated_account)
def resource_create_view(request):
    """The logged-in business account creates a real `Resource` it owns (§2.2) —
    `owner_ref` is always the authenticated account's own id, never client-supplied, so a
    business can only ever create resources under itself.

    §4.3: owner-only. A staff account's `owner_ref` would be its own id, not the
    business's, silently splitting inventory ownership between the owner and its staff
    sub-accounts — resource/calendar creation stays an owner action; staff only
    self-manage the availability of a Resource already assigned to them (see
    `resource_availability_block_view`)."""
    if not request.user.is_authenticated:
        return error_response("UNAUTHORIZED", "not logged in", 401)
    if request.user.role != get_user_model().Role.OWNER:
        return error_response(
            "FORBIDDEN", "only a business owner account can create resources", 403
        )

    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return error_response("VALIDATION_ERROR", "Request body is not valid JSON", 400)

    name = (payload.get("name") or "").strip()
    if not name:
        return error_response("VALIDATION_ERROR", "name is required", 400, field="name")
    if len(name) > 255:
        return error_response("VALIDATION_ERROR", "name is too long (max 255)", 400, field="name")

    domain_data = payload.get("domain_data") or {}
    # §4.1: the authenticated business's own domain, not a hardcoded Beauty adapter — a
    # Healthcare business must have its domain_data validated by HealthcareDomainAdapter,
    # not BeautyDomainAdapter's stylist/chair rules.
    adapter = get_adapter(request.user.domain_code)
    try:
        adapter.validate_resource_domain_data(domain_data)
    except DjangoValidationError as exc:
        return error_response(
            "VALIDATION_ERROR", " ".join(exc.messages), 400, field="domain_data"
        )

    price_value = Decimal("0.00")
    if "price_value" in payload:
        try:
            price_value = Decimal(str(payload["price_value"]))
        except InvalidOperation:
            return error_response(
                "VALIDATION_ERROR", "price_value must be a valid decimal", 400, field="price_value"
            )
        if price_value < 0:
            return error_response(
                "VALIDATION_ERROR", "price_value must not be negative", 400, field="price_value"
            )

    # §4.3: optional multi-location tagging — validated against this business's own
    # locations only, same "no such X for this account" IDOR posture as everywhere else.
    location_id = payload.get("location_id")
    if location_id:
        if not Location.objects.filter(id=location_id, business=request.user).exists():
            return error_response(
                "VALIDATION_ERROR", "no such location for this business account", 400,
                field="location_id",
            )
        domain_data = {**domain_data, "location_id": str(location_id)}

    # Doctors tab: every doctor gets a server-generated, owner-scoped, immutable
    # "DOC-0001"-style id — never client-supplied (mirrors owner_ref above), and
    # never editable afterwards (resource_update_view rejects it outright). The
    # increment happens under a row lock on the owning account so two concurrent
    # creates can't race to the same number.
    if domain_data.get("resource_type") == "doctor":
        with transaction.atomic():
            account = get_user_model().objects.select_for_update().get(pk=request.user.pk)
            sequence = account.next_doctor_sequence
            account.next_doctor_sequence = sequence + 1
            account.save(update_fields=["next_doctor_sequence"])
        domain_data = {**domain_data, "doctor_id": f"DOC-{sequence:04d}"}

    resource = Resource.objects.create(
        owner_ref=str(request.user.id),
        name=name,
        code=payload.get("code", ""),
        short_desc=payload.get("short_desc", ""),
        long_desc=payload.get("long_desc", ""),
        domain_data=domain_data,
        price_currency=payload.get("price_currency", "INR"),
        price_value=price_value,
    )
    # §3.8: a new Resource is real, visible catalog content — invalidate the
    # cached search catalog so it doesn't stay stale until the TTL safety net.
    # §4.1: only this business's own domain's cache, not every domain's.
    invalidate_catalog_cache(request.user.domain_code)
    return JsonResponse(
        {
            "id": str(resource.id),
            "name": resource.name,
            "domain_data": resource.domain_data,
            "is_active": resource.is_active,
            "price_currency": resource.price_currency,
            "price_value": str(resource.price_value),
            "created_at": resource.created_at.isoformat(),
        },
        status=201,
    )


@csrf_exempt
@require_http_methods(["POST"])
@rate_limit(limit_per_minute=30, scope="resource-update", key_func=by_authenticated_account)
def resource_update_view(request, resource_id):
    """Edit action for the Resources/Doctors table — owner-only, partial update of
    `name` and/or `domain_data`. `domain_data` is merged, not replaced wholesale: it
    also carries `resource_type`/`assigned_staff_id`/`location_id`, set by other
    flows (creation, staff assignment) — overwriting the whole dict here would
    silently unassign staff or lose the resource's own type. The merged result is
    re-validated through this business's own domain adapter, so an edit that clears
    a Healthcare doctor's required `specialisation` is rejected the same way
    creating one without it already is."""
    if not request.user.is_authenticated:
        return error_response("UNAUTHORIZED", "not logged in", 401)
    if request.user.role != get_user_model().Role.OWNER:
        return error_response(
            "FORBIDDEN", "only a business owner account can edit resources", 403
        )

    try:
        resource = Resource.objects.get(id=resource_id, owner_ref=str(request.user.id))
    except (Resource.DoesNotExist, DjangoValidationError, ValueError):
        return error_response("NOT_FOUND", "no such resource for this business account", 404)

    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return error_response("VALIDATION_ERROR", "Request body is not valid JSON", 400)

    changed = False

    if "name" in payload:
        name = (payload.get("name") or "").strip()
        if not name:
            return error_response("VALIDATION_ERROR", "name is required", 400, field="name")
        if len(name) > 255:
            return error_response(
                "VALIDATION_ERROR", "name is too long (max 255)", 400, field="name"
            )
        resource.name = name
        changed = True

    if "domain_data" in payload:
        incoming = payload.get("domain_data") or {}
        if not isinstance(incoming, dict):
            return error_response(
                "VALIDATION_ERROR", "domain_data must be an object", 400, field="domain_data"
            )
        # Doctors tab: doctor_id is server-generated at creation and permanent —
        # never accepted from a client here, regardless of what the frontend form
        # sends, so a direct API call can't reassign it either.
        if "doctor_id" in incoming and incoming["doctor_id"] != resource.domain_data.get(
            "doctor_id"
        ):
            return error_response(
                "VALIDATION_ERROR", "doctor_id cannot be changed", 400, field="doctor_id"
            )
        merged_domain_data = {**resource.domain_data, **incoming}
        adapter = get_adapter(request.user.domain_code)
        try:
            adapter.validate_resource_domain_data(merged_domain_data)
        except DjangoValidationError as exc:
            return error_response(
                "VALIDATION_ERROR", " ".join(exc.messages), 400, field="domain_data"
            )
        resource.domain_data = merged_domain_data
        changed = True

    if "price_value" in payload:
        try:
            price_value = Decimal(str(payload["price_value"]))
        except InvalidOperation:
            return error_response(
                "VALIDATION_ERROR", "price_value must be a valid decimal", 400, field="price_value"
            )
        if price_value < 0:
            return error_response(
                "VALIDATION_ERROR", "price_value must not be negative", 400, field="price_value"
            )
        resource.price_value = price_value
        changed = True

    if "price_currency" in payload:
        resource.price_currency = payload.get("price_currency") or "INR"
        changed = True

    if changed:
        resource.save()
        invalidate_catalog_cache(request.user.domain_code)

    return JsonResponse(
        {
            "id": str(resource.id),
            "name": resource.name,
            "domain_data": resource.domain_data,
            "is_active": resource.is_active,
            "price_currency": resource.price_currency,
            "price_value": str(resource.price_value),
        },
        status=200,
    )


@csrf_exempt
@require_http_methods(["POST"])
@rate_limit(limit_per_minute=30, scope="resource-status", key_func=by_authenticated_account)
def resource_status_view(request, resource_id):
    """Active/Inactive toggle for the Resources/Doctors table — owner-only. Unlike
    the business-level Profile Status toggle, this pauses just this one resource;
    the rest of the business's roster is unaffected. Takes effect in search
    immediately (cache invalidated here), same as every other real catalog change."""
    if not request.user.is_authenticated:
        return error_response("UNAUTHORIZED", "not logged in", 401)
    if request.user.role != get_user_model().Role.OWNER:
        return error_response(
            "FORBIDDEN", "only a business owner account can change a resource's status", 403
        )

    try:
        resource = Resource.objects.get(id=resource_id, owner_ref=str(request.user.id))
    except (Resource.DoesNotExist, DjangoValidationError, ValueError):
        return error_response("NOT_FOUND", "no such resource for this business account", 404)

    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return error_response("VALIDATION_ERROR", "Request body is not valid JSON", 400)

    is_active = payload.get("is_active")
    if not isinstance(is_active, bool):
        return error_response(
            "VALIDATION_ERROR", "is_active must be a boolean", 400, field="is_active"
        )

    resource.is_active = is_active
    resource.save(update_fields=["is_active", "updated_at"])
    invalidate_catalog_cache(request.user.domain_code)

    return JsonResponse({"id": str(resource.id), "is_active": resource.is_active}, status=200)


@csrf_exempt
@require_http_methods(["POST"])
@rate_limit(limit_per_minute=30, scope="resource-delete", key_func=by_authenticated_account)
def resource_delete_view(request, resource_id):
    """Delete action for the Resources/Doctors table — owner-only, and deliberately
    refuses when real booking history exists: `Slot.resource` is `on_delete=CASCADE`
    and `Booking.slot` chains off that, so a hard delete here would silently destroy
    a customer's real booking record, not just an inventory listing. A resource
    that's ever had a booking (any status — even a cancelled one is still real
    history) must be paused via the Active/Inactive toggle instead; only a genuinely
    never-booked resource (added by mistake, or with zero slots) can be hard-deleted."""
    if not request.user.is_authenticated:
        return error_response("UNAUTHORIZED", "not logged in", 401)
    if request.user.role != get_user_model().Role.OWNER:
        return error_response(
            "FORBIDDEN", "only a business owner account can delete resources", 403
        )

    try:
        resource = Resource.objects.get(id=resource_id, owner_ref=str(request.user.id))
    except (Resource.DoesNotExist, DjangoValidationError, ValueError):
        return error_response("NOT_FOUND", "no such resource for this business account", 404)

    if Booking.objects.filter(slot__resource=resource).exists():
        return error_response(
            "VALIDATION_ERROR",
            "this resource has real booking history and can't be deleted — "
            "use the Active/Inactive toggle to hide it from search instead",
            409,
        )

    resource.delete()
    invalidate_catalog_cache(request.user.domain_code)
    return JsonResponse({"status": "ok"}, status=200)


@csrf_exempt
@require_http_methods(["POST"])
@rate_limit(
    limit_per_minute=30, scope="resource-availability-delete", key_func=by_authenticated_account
)
def resource_availability_delete_view(request, resource_id):
    """Delete action for the Doctors table's "Recent" slots view — owner-only, and
    only ever deletes `Slot`s still in AVAILABLE status. A HELD/BOOKED slot has a
    real (in-progress or confirmed) booking on the other side, and `Booking.slot`
    chains off `Slot` via CASCADE, so hard-deleting those would silently destroy
    real booking history — same reasoning as `resource_delete_view` above. A
    CANCELLED slot is also left alone, since it's already real history, not a live
    offer to clear."""
    if not request.user.is_authenticated:
        return error_response("UNAUTHORIZED", "not logged in", 401)
    if request.user.role != get_user_model().Role.OWNER:
        return error_response(
            "FORBIDDEN", "only a business owner account can delete availability", 403
        )

    try:
        resource = Resource.objects.get(id=resource_id, owner_ref=str(request.user.id))
    except (Resource.DoesNotExist, DjangoValidationError, ValueError):
        return error_response("NOT_FOUND", "no such resource for this business account", 404)

    deletable = Slot.objects.filter(resource=resource, status=Slot.Status.AVAILABLE)
    deleted_count = deletable.count()
    deletable.delete()
    kept_count = (
        Slot.objects.filter(resource=resource).exclude(status=Slot.Status.AVAILABLE).count()
    )

    return JsonResponse({"deleted_count": deleted_count, "kept_count": kept_count}, status=200)


def _resource_availability_get(request, resource_id):
    """§4.4: `GET` lists the resource's current slots — owner or assigned staff, the
    same access model as the block view — feeding a live availability dashboard's
    initial render before its WebSocket connection takes over, and the Doctors
    table's "Recent" view.

    livetracker3.md §2.2: also returns the resource's own real rating aggregate
    (`average_rating`/`rating_count`) alongside the slot list — one wire call, not a
    second endpoint, since the dashboard already fetches this resource by id here.
    `average_rating` is `None` (not a fabricated `0`) when no aggregate-eligible
    rating exists yet — the business side seeing its own honest, real rating is the
    whole point of this addition (§2.2 bullet 2's own reasoning)."""
    try:
        resource = Resource.objects.get(id=resource_id)
    except (Resource.DoesNotExist, DjangoValidationError, ValueError):
        return error_response("NOT_FOUND", "no such resource for this account", 404)
    if not _resource_accessible_to(request.user, resource):
        return error_response("NOT_FOUND", "no such resource for this account", 404)
    slots = Slot.objects.filter(resource=resource).order_by("start_time")
    return JsonResponse(
        {
            "resource": {
                "id": str(resource.id),
                "name": resource.name,
                "average_rating": (
                    str(resource.average_rating) if resource.average_rating is not None else None
                ),
                "rating_count": resource.rating_count,
                # livetracker3.md §8.1: lets the owner-side resource list show
                # each resource's current staff assignment (or lack of one)
                # without a second endpoint — same resource this dashboard
                # already fetches via `getSlots()`.
                "assigned_staff_id": resource.domain_data.get("assigned_staff_id"),
                "domain_data": resource.domain_data,
                "is_active": resource.is_active,
                "price_currency": resource.price_currency,
                "price_value": str(resource.price_value),
                # The doctor's own "joining date" — bounds how far back the
                # Doctors table's slot-history browser goes.
                "created_at": resource.created_at.isoformat(),
            },
            "slots": [
                {
                    "id": str(s.id),
                    "start_time": s.start_time.isoformat(),
                    "end_time": s.end_time.isoformat(),
                    "status": s.status,
                    "capacity_remaining": s.capacity_remaining,
                    "capacity_total": s.capacity_total,
                }
                for s in slots
            ],
        },
        status=200,
    )


@rate_limit(
    limit_per_minute=30, scope="resource-availability-create", key_func=by_authenticated_account
)
def _resource_availability_post(request, resource_id):
    """§4.3: `POST` (create) is owner-only, same reasoning as `resource_create_view` —
    bulk calendar generation is inventory *creation*, distinct from a staff account
    merely blocking off already-generated slots (`resource_availability_block_view`).
    Creates a real `AvailabilityCalendar` for one of the logged-in business's own
    `Resource`s and generates real `Slot` rows from it (§2.2's own Test Gate wording)."""
    if request.user.role != get_user_model().Role.OWNER:
        return error_response(
            "FORBIDDEN", "only a business owner account can create availability", 403
        )

    try:
        resource = Resource.objects.get(id=resource_id, owner_ref=str(request.user.id))
    except Resource.DoesNotExist:
        return error_response("NOT_FOUND", "no such resource for this business account", 404)

    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return error_response("VALIDATION_ERROR", "Request body is not valid JSON", 400)

    # `parse_datetime` explicitly, rather than passing the raw JSON strings straight to
    # `.create()`: a DateTimeField only auto-converts a string to `datetime` at DB-storage
    # time (`get_prep_value`) or on a fresh fetch — the in-memory object `.create()` returns
    # still holds the raw string, which `generate_slots()` (called immediately after, before
    # any DB round-trip) would then crash on calling `.date()` against a `str`.
    range_start = parse_datetime(payload.get("range_start", ""))
    range_end = parse_datetime(payload.get("range_end", ""))
    if range_start is None or range_end is None:
        return error_response(
            "VALIDATION_ERROR", "range_start/range_end must be valid ISO 8601 datetimes", 400
        )
    if range_end <= range_start:
        return error_response("VALIDATION_ERROR", "range_end must be after range_start", 400)
    # §3.7: bound the span — an unbounded range would make generate_slots() below
    # attempt to materialize an unbounded number of real Slot rows, a genuine
    # resource-exhaustion DoS, not just "malformed" input.
    if (range_end - range_start) > dt.timedelta(days=MAX_AVAILABILITY_RANGE_DAYS):
        return error_response(
            "VALIDATION_ERROR",
            f"range_start/range_end span must not exceed {MAX_AVAILABILITY_RANGE_DAYS} days",
            400,
        )

    frequency_days = payload.get("frequency_days", 1)
    slot_duration_minutes = payload.get("slot_duration_minutes", 30)
    slot_capacity = payload.get("slot_capacity", 1)
    if not isinstance(frequency_days, int) or frequency_days <= 0:
        return error_response(
            "VALIDATION_ERROR", "frequency_days must be a positive integer", 400
        )
    if not isinstance(slot_duration_minutes, int) or slot_duration_minutes <= 0:
        return error_response(
            "VALIDATION_ERROR", "slot_duration_minutes must be a positive integer", 400
        )
    if not isinstance(slot_capacity, int) or slot_capacity <= 0:
        return error_response("VALIDATION_ERROR", "slot_capacity must be a positive integer", 400)

    try:
        calendar = AvailabilityCalendar.objects.create(
            resource=resource,
            frequency=dt.timedelta(days=frequency_days),
            range_start=range_start,
            range_end=range_end,
            days=payload.get("days", ""),
            times=payload.get("times", []),
            holidays=payload.get("holidays", []),
            slot_duration=dt.timedelta(minutes=slot_duration_minutes),
            slot_capacity=slot_capacity,
        )
        slots = calendar.generate_slots()
    except DjangoValidationError as exc:
        return error_response("VALIDATION_ERROR", str(exc), 400)

    return JsonResponse(
        {"calendar_id": str(calendar.id), "slots_created": len(slots)}, status=201
    )


@csrf_exempt
@require_http_methods(["GET", "POST"])
def resource_availability_create_view(request, resource_id):
    """Dispatches to `_resource_availability_get`/`_resource_availability_post` —
    kept as one URL (unchanged for every existing caller) but each method now
    carries its own rate-limit scope, so heavy read traffic (the "Recent" view,
    the live dashboard) can't exhaust the write-side budget bulk calendar
    generation uses, or vice versa."""
    if not request.user.is_authenticated:
        return error_response("UNAUTHORIZED", "not logged in", 401)
    if request.method == "GET":
        return _resource_availability_get(request, resource_id)
    return _resource_availability_post(request, resource_id)


@require_http_methods(["GET"])
def resources_list_view(request):
    """Lists currently-visible `Resource`s for BPP's own business dashboard — a
    deactivated business's resources are excluded (§2.2's own Test Gate wording: "a
    deactivated business account's inventory stops appearing in search"). Distinct
    from the real Beckn `/search`/`/on_search` wire endpoints below (§3.1) — this is a
    non-protocol, web-facing convenience endpoint, not part of the Beckn transaction
    flow.

    §4.1: `visible_resources()` is now domain-scoped (BPP can serve more than one
    domain) — accepts an optional `?domain=` query param, defaulting to
    `settings.DOMAIN_BEAUTY` so every caller that existed before this field did keeps
    seeing exactly what it saw before, unchanged."""
    domain_code = request.GET.get("domain") or settings.DOMAIN_BEAUTY
    resources = visible_resources(domain_code).values("id", "name", "owner_ref")
    return JsonResponse({"resources": list(resources)}, status=200)


def _accessible_order_resource_ids(user):
    """Same resource-set resolution `core/consumers.py`'s `BusinessOrdersConsumer`
    uses for its own WebSocket group membership (livetracker6.md §2.2) — one
    scoping rule, not two independently-written ones that could drift apart."""
    BusinessAccount = get_user_model()
    if user.role == BusinessAccount.Role.OWNER:
        return Resource.objects.filter(owner_ref=str(user.id)).values_list("id", flat=True)
    return Resource.objects.filter(
        owner_ref=str(user.managed_by_id), domain_data__assigned_staff_id=str(user.id)
    ).values_list("id", flat=True)


@require_http_methods(["GET"])
def orders_list_view(request):
    """The logged-in business's own real Orders list (livetracker6.md §2.2) — the
    initial page load the `BusinessOrdersConsumer` WebSocket then keeps live.
    Scoped to exactly the same resource set as that consumer (an owner sees every
    owned resource's orders, staff sees only their one assigned resource's own).

    Cursor-paginated the same way as BAP's own `booking_history_service` — with
    one real difference, not an oversight: `Booking.id` is a random UUID, not a
    monotonically-increasing integer PK, so `id` alone can't serve as a stable
    newest-first cursor the way it does there. Ordered by `created_at` (a real
    `DateTimeField`) with `id` as an explicit tiebreaker for two bookings created
    in the same instant, avoiding the correctness bug a bare `id`-based cursor
    would silently have here."""
    if not request.user.is_authenticated:
        return error_response("UNAUTHORIZED", "not logged in", 401)

    resource_ids = list(_accessible_order_resource_ids(request.user))
    limit = pagination.parse_limit(request.GET.get("limit"))
    queryset = (
        Booking.objects.filter(slot__resource_id__in=resource_ids)
        .exclude(status=Booking.Status.HELD)
        .select_related("slot__resource")
        .order_by("-created_at", "-id")
    )

    cursor = request.GET.get("cursor")
    if cursor:
        try:
            cursor_created_at, cursor_id = cursor.split("|", 1)
            cursor_dt = dt.datetime.fromisoformat(cursor_created_at)
        except ValueError:
            pass
        else:
            queryset = queryset.filter(
                models.Q(created_at__lt=cursor_dt)
                | (models.Q(created_at=cursor_dt) & models.Q(id__lt=cursor_id))
            )

    page = list(queryset[: limit + 1])
    has_more = len(page) > limit
    page = page[:limit]

    def _cursor_for(booking):
        return f"{booking.created_at.isoformat()}|{booking.id}"

    return JsonResponse(
        {
            "orders": [
                {
                    "booking_id": str(booking.id),
                    "transaction_id": booking.holder_ref,
                    "resource_id": str(booking.slot.resource_id),
                    "resource_name": booking.slot.resource.name,
                    "slot_time": booking.slot.start_time.isoformat(),
                    "slot_end_time": booking.slot.end_time.isoformat(),
                    "status": booking.status,
                    "fulfillment_status": booking.fulfillment_status,
                    "payment_status": booking.payment_status,
                    "amount": str(booking.confirmed_total_value)
                    if booking.confirmed_total_value is not None
                    else None,
                    "currency": booking.confirmed_total_currency,
                    # Same project-defined customer fields as realtime.py's
                    # broadcast_order_confirmed — one shape, not two drifting ones.
                    "customer_id": booking.domain_data.get("customer_id", ""),
                    "customer_name": booking.domain_data.get("customer_name", ""),
                    "customer_email": booking.domain_data.get("customer_email", ""),
                    "customer_phone": booking.domain_data.get("customer_phone", ""),
                }
                for booking in page
            ],
            "next_cursor": _cursor_for(page[-1]) if has_more and page else None,
        },
        status=200,
    )


@require_http_methods(["GET"])
def dashboard_stats_view(request):
    """The business owner's/staff's own Dashboard home — aggregate counts and
    revenue totals, scoped to exactly the same resource set as
    `orders_list_view`/`BusinessOrdersConsumer` (one scoping rule, not three
    independently-written ones).

    "Today"/"this month" are computed in the *caller's own local timezone*,
    not the server's (`TIME_ZONE = "UTC"`, `bpp/settings.py`) — a hospital
    admin's "today" is their own wall-clock day, and the server's UTC day
    boundary can be off by hours for an IST browser. `tz_offset_minutes` is
    the exact value `Date.prototype.getTimezoneOffset()` already returns in
    the browser (IST: -330) — reusing a value JS hands us for free instead of
    guessing the business's timezone or adding a new per-account setting for
    it. An absent/malformed value falls back to UTC (`0`), the same as
    treating the caller as the server's own timezone — a safe, non-crashing
    default, not a wrong-but-silent one (the boundary is simply UTC instead
    of local, same as before this endpoint existed).

    Revenue/booking-count figures are scoped to a booking's own *appointment*
    date (`Slot.start_time`), not when the row was created or paid — "today's
    earnings" means "revenue from today's appointments", the same meaning a
    clinic's own front-desk staff would use, and the one every comparable
    booking-platform business dashboard (Zocdoc/Practo for Business) reports.
    `payment_status == "SUCCEEDED"` is required for every revenue figure (not
    just `status == ACTIVE`/`COMPLETE`) — an appointment that happened but was
    never actually paid for is real activity, not real revenue; see
    `Booking.payment_status`'s own docstring for why this field is a
    best-effort mirror, not authoritative, which is why it's never the sole
    signal for "is this booking real" (`status` already is) — only for "was
    it paid"."""
    if not request.user.is_authenticated:
        return error_response("UNAUTHORIZED", "not logged in", 401)

    try:
        tz_offset_minutes = int(request.GET.get("tz_offset_minutes", "0"))
    except (TypeError, ValueError):
        tz_offset_minutes = 0
    local_tz = dt.timezone(dt.timedelta(minutes=-tz_offset_minutes))

    now_local = timezone.now().astimezone(local_tz)
    today_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + dt.timedelta(days=1)
    month_start = today_start.replace(day=1)
    next_month_start = (month_start + dt.timedelta(days=32)).replace(day=1)
    trend_start = today_start - dt.timedelta(days=6)  # 7-day window, inclusive of today

    resource_ids = list(_accessible_order_resource_ids(request.user))

    resources = Resource.objects.filter(id__in=resource_ids)
    total_doctors = resources.count()
    doctors_available_today = (
        resources.filter(
            is_active=True,
            slots__status=Slot.Status.AVAILABLE,
            slots__capacity_remaining__gt=0,
            slots__start_time__gte=today_start,
            slots__start_time__lt=today_end,
        )
        .distinct()
        .count()
    )
    # Deliberately the simple complement, not a second independently-filtered
    # query: an inactive doctor and an active-but-fully-booked one are both
    # genuinely "not available today" from the same front-desk perspective,
    # and a doctor is always in exactly one of these two buckets.
    doctors_unavailable_today = total_doctors - doctors_available_today

    # Overall rating (every real booking-platform business dashboard — Practo,
    # Zocdoc, Urban Company — leads with this): a review-weighted average
    # across every owned doctor's own `average_rating`, not a plain mean of
    # each doctor's average — a doctor with 200 reviews should move this
    # number more than one with 2, same reasoning a 5-star product listing
    # with one review doesn't outrank one with a thousand.
    rating_totals = resources.filter(rating_count__gt=0).aggregate(
        weighted_sum=models.Sum(models.F("average_rating") * models.F("rating_count")),
        total_count=models.Sum("rating_count"),
    )
    total_rating_count = rating_totals["total_count"] or 0
    overall_rating = (
        str((rating_totals["weighted_sum"] / total_rating_count).quantize(Decimal("0.1")))
        if total_rating_count > 0
        else None
    )

    yesterday_start = today_start - dt.timedelta(days=1)
    # Last-month-to-date: the same number of calendar days into last month as
    # "today" is into this month, so a still-incomplete current month is
    # compared against an equally-incomplete prior one — comparing it against
    # a *full* prior month would always read as a decline, a biased
    # comparison every real dashboard (Stripe, Razorpay) avoids the same way.
    prev_month_start = (month_start - dt.timedelta(days=1)).replace(day=1)
    # Capped at `month_start`: if today is e.g. the 31st and the previous
    # month only had 28/29/30 days, `prev_month_start + timedelta(days=31)`
    # would spill past the end of that month and into the first few days of
    # the *current* one — silently double-counting this month's own early
    # bookings as "last month's" and corrupting the % change. Capping means
    # the comparison simply covers the whole prior month instead on those
    # dates, never bleeds forward.
    prev_month_mtd_end = min(prev_month_start + dt.timedelta(days=now_local.day), month_start)

    def _pct_change(current: Decimal, previous: Decimal) -> float | None:
        """`None` (not 0, not an undefined/inflated value) when there's no
        real prior-period baseline — a brand-new business's first day has
        nothing honest to compare against yet."""
        if previous == 0:
            return None
        return float(((current - previous) / previous * 100).quantize(Decimal("0.1")))

    confirmed_bookings = Booking.objects.filter(
        slot__resource_id__in=resource_ids,
        status__in=[Booking.Status.ACTIVE, Booking.Status.COMPLETE],
    )
    paid_bookings = confirmed_bookings.filter(payment_status="SUCCEEDED")

    def _sum_revenue(queryset) -> Decimal:
        return queryset.aggregate(total=models.Sum("confirmed_total_value"))["total"] or Decimal(
            "0.00"
        )

    earnings_today = _sum_revenue(
        paid_bookings.filter(slot__start_time__gte=today_start, slot__start_time__lt=today_end)
    )
    earnings_yesterday = _sum_revenue(
        paid_bookings.filter(
            slot__start_time__gte=yesterday_start, slot__start_time__lt=today_start
        )
    )
    earnings_this_month = _sum_revenue(
        paid_bookings.filter(
            slot__start_time__gte=month_start, slot__start_time__lt=next_month_start
        )
    )
    earnings_last_month_mtd = _sum_revenue(
        paid_bookings.filter(
            slot__start_time__gte=prev_month_start, slot__start_time__lt=prev_month_mtd_end
        )
    )

    total_bookings = confirmed_bookings.count()
    bookings_today = confirmed_bookings.filter(
        slot__start_time__gte=today_start, slot__start_time__lt=today_end
    ).count()
    bookings_yesterday = confirmed_bookings.filter(
        slot__start_time__gte=yesterday_start, slot__start_time__lt=today_start
    ).count()
    bookings_this_month = confirmed_bookings.filter(
        slot__start_time__gte=month_start, slot__start_time__lt=next_month_start
    ).count()

    # A confirmed appointment nobody has actually paid for yet is real
    # follow-up work, not just a number — see `Booking.payment_status`'s own
    # "best-effort mirror, not authoritative" contract above. Never date-
    # scoped: this is a worklist, not a daily metric.
    pending_payment_count = confirmed_bookings.filter(payment_status="").count()

    avg_booking_value = (
        str((earnings_this_month / bookings_this_month).quantize(Decimal("0.01")))
        if bookings_this_month > 0
        else None
    )

    # Cancellation/no-show rate (this month) — HELD rows are excluded from
    # both sides of the ratio (never a real customer commitment to begin
    # with, see select_service.py's own module docstring), so the
    # denominator is only bookings a customer actually confirmed.
    month_bookings_all = Booking.objects.filter(
        slot__resource_id__in=resource_ids,
        slot__start_time__gte=month_start,
        slot__start_time__lt=next_month_start,
    ).exclude(status=Booking.Status.HELD)
    month_bookings_total = month_bookings_all.count()
    month_bookings_lost = month_bookings_all.filter(
        models.Q(status=Booking.Status.CANCELLED)
        | models.Q(fulfillment_status=Booking.FulfillmentStatus.NO_SHOW)
    ).count()
    cancellation_rate = (
        str((Decimal(month_bookings_lost) / month_bookings_total * 100).quantize(Decimal("0.1")))
        if month_bookings_total > 0
        else None
    )

    # Per-doctor performance this month — every owned doctor, not just the
    # ones with bookings (a doctor with zero bookings this month is exactly
    # the kind of roster gap this panel should surface, not silently omit).
    # Sorted by real paid revenue, not booking count — a tie-break on volume
    # would reward activity over money actually brought in, the wrong
    # incentive for a "performance" ranking on a revenue dashboard.
    doctor_stats_by_resource = {
        row["slot__resource_id"]: row
        for row in (
            confirmed_bookings.filter(
                slot__start_time__gte=month_start, slot__start_time__lt=next_month_start
            )
            .values("slot__resource_id")
            .annotate(
                bookings=models.Count("id"),
                revenue=models.Sum(
                    "confirmed_total_value", filter=models.Q(payment_status="SUCCEEDED")
                ),
            )
        )
    }
    doctor_performance = sorted(
        (
            {
                "name": resource.name,
                "specialisation": resource.domain_data.get("specialisation", ""),
                "fee": str(resource.price_value),
                "bookings": doctor_stats_by_resource.get(resource.id, {}).get("bookings", 0),
                "revenue": str(
                    doctor_stats_by_resource.get(resource.id, {}).get("revenue") or Decimal("0.00")
                ),
            }
            for resource in resources
        ),
        key=lambda row: Decimal(row["revenue"]),
        reverse=True,
    )
    top_doctor = (
        doctor_performance[0]
        if doctor_performance and Decimal(doctor_performance[0]["revenue"]) > 0
        else None
    )

    trend_rows = {
        row["day"].isoformat(): row
        for row in (
            confirmed_bookings.filter(
                slot__start_time__gte=trend_start, slot__start_time__lt=today_end
            )
            .annotate(day=TruncDate("slot__start_time", tzinfo=local_tz))
            .values("day")
            .annotate(
                bookings=models.Count("id"),
                revenue=models.Sum(
                    "confirmed_total_value", filter=models.Q(payment_status="SUCCEEDED")
                ),
            )
        )
    }
    trend = []
    for offset in range(7):
        day_key = (trend_start.date() + dt.timedelta(days=offset)).isoformat()
        row = trend_rows.get(day_key)
        trend.append(
            {
                "date": day_key,
                "bookings": row["bookings"] if row else 0,
                "revenue": str(row["revenue"] or Decimal("0.00")) if row else "0.00",
            }
        )

    response = JsonResponse(
        {
            "total_doctors": total_doctors,
            "doctors_available_today": doctors_available_today,
            "doctors_unavailable_today": doctors_unavailable_today,
            "overall_rating": overall_rating,
            "rating_count": total_rating_count,
            "earnings_today": str(earnings_today),
            "earnings_today_change_pct": _pct_change(earnings_today, earnings_yesterday),
            "earnings_this_month": str(earnings_this_month),
            "earnings_this_month_change_pct": _pct_change(
                earnings_this_month, earnings_last_month_mtd
            ),
            "total_bookings": total_bookings,
            "bookings_today": bookings_today,
            "bookings_today_change_pct": _pct_change(
                Decimal(bookings_today), Decimal(bookings_yesterday)
            ),
            "pending_payment_count": pending_payment_count,
            "avg_booking_value": avg_booking_value,
            "cancellation_rate": cancellation_rate,
            "top_doctor": top_doctor,
            "doctor_performance": doctor_performance,
            "currency": "INR",
            "trend": trend,
        },
        status=200,
    )
    # Defense in depth alongside the frontend's own `cache: 'no-store'`
    # (api-client.ts) — every figure here is live operational data computed
    # fresh on every request; nothing between the browser and this view
    # (a corporate proxy, a CDN, the browser's own heuristic cache) should
    # ever be allowed to serve a stale copy instead of hitting this view.
    response["Cache-Control"] = "no-store"
    return response


@csrf_exempt
@require_http_methods(["POST"])
@rate_limit(
    limit_per_minute=30, scope="order-fulfillment-status", key_func=by_authenticated_account
)
def order_fulfillment_status_update_view(request, booking_id):
    """Persists the Orders table's own Pending/Completed dropdown (BPP/web/app/
    orders/page.tsx) against the real `Booking.fulfillment_status` state machine —
    previously local-only UI state that reverted on refresh.

    The table's own two-value model (`PENDING`/`COMPLETED`) is a deliberate
    simplification of the real four-state machine (`SCHEDULED`/`IN_PROGRESS`/
    `COMPLETED`/`NO_SHOW`, `inventory_core.models.Booking._FULFILLMENT_TRANSITIONS`)
    for exactly the one action an owner/staff genuinely takes from this table:
    mark an appointment done. Requesting `COMPLETED` drives the two-step real
    transition (`SCHEDULED` -> `IN_PROGRESS` -> `COMPLETED`) when starting from
    `SCHEDULED`, or the single real step when already `IN_PROGRESS`; already
    `COMPLETED` is accepted idempotently (a second click/retry must not error).
    Requesting `PENDING` is **not** a real reverse transition the state machine
    allows (`COMPLETED`/`NO_SHOW` are terminal) and is rejected with a clear
    message rather than silently faking a state the backend doesn't actually
    hold — this table never offers it as a target from a terminal booking in
    the first place, so this path is a defensive rejection of a replayed/
    forged request, not a real user-facing flow to support."""
    if not request.user.is_authenticated:
        return error_response("UNAUTHORIZED", "not logged in", 401)

    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return error_response("VALIDATION_ERROR", "Request body is not valid JSON", 400)

    target = payload.get("fulfillment_status")
    if target not in ("PENDING", "COMPLETED"):
        return error_response(
            "VALIDATION_ERROR", "fulfillment_status must be PENDING or COMPLETED", 400
        )

    accessible_ids = set(str(rid) for rid in _accessible_order_resource_ids(request.user))
    try:
        booking = Booking.objects.select_related("slot__resource").get(pk=booking_id)
    except (Booking.DoesNotExist, ValueError):
        return error_response("NOT_FOUND", "No such booking", 404)
    # Same IDOR posture as every other owner/staff-scoped view in this file:
    # "not found" for a real booking outside this account's own resources,
    # never a distinguishable "forbidden" that would confirm its existence.
    if str(booking.slot.resource_id) not in accessible_ids:
        return error_response("NOT_FOUND", "No such booking", 404)

    if target == "PENDING":
        if booking.fulfillment_status == Booking.FulfillmentStatus.SCHEDULED:
            pass  # already the state PENDING maps to — idempotent no-op
        else:
            return error_response(
                "INVALID_TRANSITION",
                f"Cannot revert a {booking.fulfillment_status.lower()} booking back to pending",
                409,
            )
    else:  # COMPLETED
        try:
            if booking.fulfillment_status == Booking.FulfillmentStatus.SCHEDULED:
                booking.transition_fulfillment_status(Booking.FulfillmentStatus.IN_PROGRESS)
                booking.transition_fulfillment_status(Booking.FulfillmentStatus.COMPLETED)
            elif booking.fulfillment_status == Booking.FulfillmentStatus.IN_PROGRESS:
                booking.transition_fulfillment_status(Booking.FulfillmentStatus.COMPLETED)
            elif booking.fulfillment_status == Booking.FulfillmentStatus.COMPLETED:
                pass  # idempotent — a retried/duplicate request must not error
            else:
                return error_response(
                    "INVALID_TRANSITION", "Cannot mark a no-show booking as completed", 409
                )
        except DjangoValidationError as exc:
            return error_response("INVALID_TRANSITION", str(exc), 409)

    return JsonResponse(
        {"booking_id": str(booking.id), "fulfillment_status": booking.fulfillment_status},
        status=200,
    )


@csrf_exempt
@require_http_methods(["GET", "POST"])
@rate_limit(limit_per_minute=20, scope="staff", key_func=by_authenticated_account)
def staff_view(request):
    """§4.3: owner-only staff-account management. `POST` creates a new staff login under
    the calling business (`role=STAFF`, `managed_by=<the owner>`); `GET` lists the
    calling business's own staff. A staff account cannot call either — it has no staff
    of its own, mirroring `resource_create_view`'s owner-only IDOR posture."""
    if not request.user.is_authenticated:
        return error_response("UNAUTHORIZED", "not logged in", 401)
    BusinessAccount = get_user_model()
    if request.user.role != BusinessAccount.Role.OWNER:
        return error_response("FORBIDDEN", "only a business owner account can manage staff", 403)

    if request.method == "GET":
        staff = request.user.staff_members.values("id", "business_name", "contact", "is_active")
        # livetracker3.md §8.1: the staff-management UI needs to show each staff
        # account's current resource assignment (or lack of one) without a second,
        # per-row round trip — cheaper to compute here, same "one wire call" reasoning
        # as `_business_account_json`'s own owned/assigned id fields.
        assigned_by_staff: dict[str, list[str]] = {}
        for resource in Resource.objects.filter(owner_ref=str(request.user.id)).only(
            "id", "domain_data"
        ):
            staff_id = resource.domain_data.get("assigned_staff_id")
            if staff_id:
                assigned_by_staff.setdefault(staff_id, []).append(str(resource.id))
        return JsonResponse(
            {
                "staff": [
                    {
                        **{**s, "id": str(s["id"])},
                        "assigned_resource_ids": assigned_by_staff.get(str(s["id"]), []),
                    }
                    for s in staff
                ]
            },
            status=200,
        )

    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return error_response("VALIDATION_ERROR", "Request body is not valid JSON", 400)

    business_name = (payload.get("business_name") or "").strip()
    contact = (payload.get("contact") or "").strip()
    password = payload.get("password") or ""

    if not business_name:
        return error_response(
            "VALIDATION_ERROR", "business_name is required", 400, field="business_name"
        )
    if not contact:
        return error_response("VALIDATION_ERROR", "contact is required", 400, field="contact")
    if not password:
        return error_response("VALIDATION_ERROR", "password is required", 400, field="password")
    if len(business_name) > 255:
        return error_response(
            "VALIDATION_ERROR", "business_name is too long (max 255)", 400, field="business_name"
        )
    if len(contact) > 255:
        return error_response(
            "VALIDATION_ERROR", "contact is too long (max 255)", 400, field="contact"
        )
    if BusinessAccount.objects.filter(contact=contact).exists():
        return error_response(
            "VALIDATION_ERROR", "an account with this contact already exists", 409, field="contact"
        )
    try:
        password_validation.validate_password(password)
    except DjangoValidationError as exc:
        return error_response("VALIDATION_ERROR", " ".join(exc.messages), 400, field="password")

    staff = BusinessAccount.objects.create_user(
        contact=contact,
        business_name=business_name,
        password=password,
        domain_code=request.user.domain_code,
        role=BusinessAccount.Role.STAFF,
        managed_by=request.user,
    )
    return JsonResponse(_business_account_json(staff), status=201)


@csrf_exempt
@require_http_methods(["POST"])
@rate_limit(limit_per_minute=30, scope="staff-status", key_func=by_authenticated_account)
def staff_status_view(request, staff_id):
    """livetracker3.md §8.1's own seventh post-close audit: the real gap flagged and now
    closed — deactivating/reactivating a staff account was previously only possible via
    Django admin (`models.py`'s own documented "Provider Lifecycle Management"
    mechanism, `admin.py`'s own explicit "the real deactivation mechanism, not a
    placeholder"), with no real path for an owner to do it themselves. Reuses the same
    `is_active` flag `authenticate()` already refuses a login for (`models.py`'s own
    docstring) — no new auth machinery, matching this project's own established
    `role=STAFF` precedent of reusing existing plumbing rather than inventing a second.
    Deactivating also clears any resource this staff account was assigned to — the same
    real gap the earlier unassign work closed one level up applies here too: a
    deactivated-but-still-assigned staff account would otherwise leave that resource
    silently unreachable (nobody could log in and manage it) until an owner happened to
    notice and manually unassign it separately."""
    if not request.user.is_authenticated:
        return error_response("UNAUTHORIZED", "not logged in", 401)
    BusinessAccount = get_user_model()
    if request.user.role != BusinessAccount.Role.OWNER:
        return error_response("FORBIDDEN", "only a business owner account can manage staff", 403)

    try:
        staff = BusinessAccount.objects.get(
            id=staff_id, role=BusinessAccount.Role.STAFF, managed_by=request.user
        )
    except (BusinessAccount.DoesNotExist, DjangoValidationError, ValueError):
        return error_response("NOT_FOUND", "no such staff account for this business", 404)

    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return error_response("VALIDATION_ERROR", "Request body is not valid JSON", 400)

    is_active = payload.get("is_active")
    if not isinstance(is_active, bool):
        return error_response(
            "VALIDATION_ERROR", "is_active (boolean) is required", 400, field="is_active"
        )

    staff.is_active = is_active
    staff.save(update_fields=["is_active", "updated_at"])

    if not staff.is_active:
        affected = Resource.objects.filter(
            owner_ref=str(request.user.id), domain_data__assigned_staff_id=str(staff.id)
        )
        for resource in affected:
            resource.domain_data = {
                k: v for k, v in resource.domain_data.items() if k != "assigned_staff_id"
            }
            resource.save(update_fields=["domain_data", "updated_at"])

    return JsonResponse({"id": str(staff.id), "is_active": staff.is_active}, status=200)


@csrf_exempt
@require_http_methods(["POST"])
@rate_limit(limit_per_minute=10, scope="staff-password-reset", key_func=by_authenticated_account)
def staff_password_reset_view(request, staff_id):
    """livetracker3.md §8.1's own ninth post-close audit: a real gap found and closed
    — `set_password()` is called exactly once anywhere in this entire codebase (BAP or
    BPP), at account creation, confirmed by direct grep; there was no way for a staff
    account's password to ever change again short of the owner deleting and re-creating
    the whole account. No self-service "forgot password" flow exists anywhere in this
    project (no email/token infrastructure at all), and building one would be a much
    larger, cross-cutting feature spanning BAP customers and BPP owners too — genuinely
    out of this phase's own scope. The proportionate fix matching the trust model
    already established here: the owner, who already chooses the staff account's
    password once at creation (`staff_view`'s own `POST`), can reset it again later —
    the same real, direct control they already had over `is_active`, not a new
    boundary. Same `10/min` tier as `business-signup`/`business-login` (the closest
    existing precedent for a credential-setting action), tighter than the other
    staff-management endpoints' `30/min`."""
    if not request.user.is_authenticated:
        return error_response("UNAUTHORIZED", "not logged in", 401)
    BusinessAccount = get_user_model()
    if request.user.role != BusinessAccount.Role.OWNER:
        return error_response("FORBIDDEN", "only a business owner account can manage staff", 403)

    try:
        staff = BusinessAccount.objects.get(
            id=staff_id, role=BusinessAccount.Role.STAFF, managed_by=request.user
        )
    except (BusinessAccount.DoesNotExist, DjangoValidationError, ValueError):
        return error_response("NOT_FOUND", "no such staff account for this business", 404)

    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return error_response("VALIDATION_ERROR", "Request body is not valid JSON", 400)

    password = payload.get("password") or ""
    if not password:
        return error_response("VALIDATION_ERROR", "password is required", 400, field="password")
    try:
        password_validation.validate_password(password, user=staff)
    except DjangoValidationError as exc:
        return error_response("VALIDATION_ERROR", " ".join(exc.messages), 400, field="password")

    staff.set_password(password)
    staff.save(update_fields=["password", "updated_at"])
    return JsonResponse({"id": str(staff.id)}, status=200)


@csrf_exempt
@require_http_methods(["POST"])
@rate_limit(limit_per_minute=30, scope="resource-assign-staff", key_func=by_authenticated_account)
def resource_assign_staff_view(request, resource_id):
    """§4.3: the owner assigns one of its own `Resource`s to one of its own staff
    accounts — `Resource.domain_data["assigned_staff_id"]` is the opaque link the
    availability-block view below trusts for staff-scoped authorization. Owner-only,
    same IDOR posture as `resource_availability_create_view` (404, not 403, on
    mismatch, so a caller can't distinguish "not yours" from "doesn't exist").

    livetracker3.md §8.1's own third post-close audit, two real gaps closed together
    (unassign has to exist before the second one is safe to add, or a staff account
    assigned once would be permanently stuck there with no way to move):
    `staff_id: null` (the key present, explicitly `null` — distinct from the key being
    entirely absent, which stays the pre-existing validation error) now clears the
    resource's own assignment. And: a staff account already assigned to a *different*
    resource of this same owner is now rejected up front — `SECURITY.md`'s own
    IDOR-boundary doc already frames this system as one-Resource-per-staff by design
    (`_resource_accessible_to()`'s own comment: "the *one* Resource its owner
    explicitly assigned to it"), but nothing here previously enforced it; a second
    assignment would have silently left that second resource unreachable via login
    (`assigned_resource_ids[0]` is all any client ever reads)."""
    if not request.user.is_authenticated:
        return error_response("UNAUTHORIZED", "not logged in", 401)
    BusinessAccount = get_user_model()
    if request.user.role != BusinessAccount.Role.OWNER:
        return error_response("FORBIDDEN", "only a business owner account can assign staff", 403)

    try:
        resource = Resource.objects.get(id=resource_id, owner_ref=str(request.user.id))
    except (Resource.DoesNotExist, DjangoValidationError, ValueError):
        return error_response("NOT_FOUND", "no such resource for this business account", 404)

    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return error_response("VALIDATION_ERROR", "Request body is not valid JSON", 400)

    if "staff_id" not in payload:
        return error_response("VALIDATION_ERROR", "staff_id is required", 400, field="staff_id")

    staff_id = payload["staff_id"]
    if staff_id is None:
        resource.domain_data = {
            k: v for k, v in resource.domain_data.items() if k != "assigned_staff_id"
        }
        resource.save(update_fields=["domain_data", "updated_at"])
        return JsonResponse({"id": str(resource.id), "assigned_staff_id": None}, status=200)

    if not staff_id:
        return error_response("VALIDATION_ERROR", "staff_id is required", 400, field="staff_id")
    if not BusinessAccount.objects.filter(
        id=staff_id, role=BusinessAccount.Role.STAFF, managed_by=request.user
    ).exists():
        return error_response(
            "VALIDATION_ERROR", "no such staff account for this business", 400, field="staff_id"
        )
    if (
        Resource.objects.filter(
            owner_ref=str(request.user.id), domain_data__assigned_staff_id=str(staff_id)
        )
        .exclude(id=resource.id)
        .exists()
    ):
        return error_response(
            "VALIDATION_ERROR",
            "this staff account is already assigned to a different resource",
            400,
            field="staff_id",
        )

    resource.domain_data = {**resource.domain_data, "assigned_staff_id": str(staff_id)}
    resource.save(update_fields=["domain_data", "updated_at"])
    return JsonResponse({"id": str(resource.id), "assigned_staff_id": str(staff_id)}, status=200)


def _resource_accessible_to(user, resource) -> bool:
    """§4.3: shared ownership check between the owner's full-access path and a staff
    account's narrower one-resource path — the owner always owns everything under its
    own `owner_ref`; a staff account only ever owns the one `Resource` its owner
    explicitly assigned to it via `resource_assign_staff_view` above."""
    BusinessAccount = get_user_model()
    if user.role == BusinessAccount.Role.OWNER:
        return resource.owner_ref == str(user.id)
    return (
        resource.owner_ref == str(user.managed_by_id)
        and resource.domain_data.get("assigned_staff_id") == str(user.id)
    )


def block_slots(resource, slot_ids: list) -> tuple[list, list]:
    """Shared core of §4.3's self-service blocking, factored out so Phase 4.4's
    `ResourceAvailabilityConsumer` (`core/consumers.py`) can offer the exact same action
    over the live WebSocket channel, not a parallel reimplementation — a real
    client → server signal on that channel (livetracker2.md §4.4's own wording: "not
    just a one-way dashboard feed"), not merely `ping`/`pong`. Broadcasts each
    successfully-blocked slot via `realtime.broadcast_slot_update` so every other
    browser watching this resource's dashboard updates live too, including the REST
    caller's own other open tabs.

    Blocking only ever targets currently-`AVAILABLE` slots (a `HELD`/`BOOKED` slot has
    an active customer transaction against it that this has no business unwinding) and
    reuses the existing `CANCELLED` status rather than inventing a new one, matching
    `Slot.Status`'s already-confirmed "not bookable" terminal meaning. Best-effort per
    slot_id, not all-or-nothing: one already-booked slot in a batch shouldn't block the
    rest from being blocked."""
    blocked, skipped = [], []
    for raw_id in slot_ids:
        try:
            with transaction.atomic(), Slot.objects.lock_for_mutation(raw_id) as slot:
                if slot.resource_id != resource.id:
                    skipped.append({"slot_id": str(raw_id), "reason": "not found"})
                    continue
                if slot.status != Slot.Status.AVAILABLE:
                    skipped.append(
                        {"slot_id": str(raw_id), "reason": f"not blockable (status={slot.status})"}
                    )
                    continue
                slot.status = Slot.Status.CANCELLED
                slot.save(update_fields=["status", "updated_at"])
                blocked.append(str(slot.id))
        except (Slot.DoesNotExist, DjangoValidationError, ValueError):
            skipped.append({"slot_id": str(raw_id), "reason": "not found"})
            continue
        broadcast_slot_update(resource.id, slot)
    return blocked, skipped


@csrf_exempt
@require_http_methods(["POST"])
@rate_limit(
    limit_per_minute=30, scope="resource-availability-block", key_func=by_authenticated_account
)
def resource_availability_block_view(request, resource_id):
    """§4.3's own Test Gate: "a staff member logs in independently of the business
    owner account and blocks off their own availability." Self-service — a staff
    account calls this on its one assigned `Resource` with no owner involvement, using
    the exact same login/session mechanism the owner uses (§2.2), just a different
    account. The owner can also call this on any of its own resources."""
    if not request.user.is_authenticated:
        return error_response("UNAUTHORIZED", "not logged in", 401)

    try:
        resource = Resource.objects.get(id=resource_id)
    except (Resource.DoesNotExist, DjangoValidationError, ValueError):
        return error_response("NOT_FOUND", "no such resource for this account", 404)
    if not _resource_accessible_to(request.user, resource):
        return error_response("NOT_FOUND", "no such resource for this account", 404)

    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return error_response("VALIDATION_ERROR", "Request body is not valid JSON", 400)

    slot_ids = payload.get("slot_ids")
    if not isinstance(slot_ids, list) or not slot_ids:
        return error_response(
            "VALIDATION_ERROR", "slot_ids must be a non-empty list", 400, field="slot_ids"
        )
    # §3.7-style cap: bound the batch so this can't be used to hold an unbounded number
    # of row locks in one request.
    if len(slot_ids) > 500:
        return error_response(
            "VALIDATION_ERROR", "slot_ids must not exceed 500 items", 400, field="slot_ids"
        )

    blocked, skipped = block_slots(resource, slot_ids)
    return JsonResponse({"blocked": blocked, "skipped": skipped}, status=200)


def _circle_for_state(state: str) -> Circle | None:
    """shared.postal_directory names its Circle rows "<State> Circle" — see that
    app's own seed_postal_directory docstring. Not every state has a Circle seeded
    yet (only Andhra Pradesh does), so this is expected to return None often."""
    return Circle.objects.filter(name=f"{state} Circle").first()


@require_http_methods(["GET"])
@rate_limit(limit_per_minute=30, scope="postal-directory-read", key_func=by_authenticated_account)
def postal_divisions_view(request):
    """Step 2 of the Profile page's State -> Division -> Pincode picker: the real
    Divisions under the chosen state's Circle, or an empty list when that state has
    no postal_directory data yet — the frontend shows "Coming soon" for an empty
    list rather than treating it as an error, since most states are genuinely
    unseeded right now, not a real failure."""
    if not request.user.is_authenticated:
        return error_response("UNAUTHORIZED", "not logged in", 401)
    state = (request.GET.get("state") or "").strip()
    if state not in ALL_INDIA_STATES:
        return error_response("VALIDATION_ERROR", "not a recognised state", 400, field="state")
    circle = _circle_for_state(state)
    divisions = (
        [{"id": d.id, "name": d.name} for d in circle.divisions.order_by("name")]
        if circle
        else []
    )
    return JsonResponse({"divisions": divisions}, status=200)


@require_http_methods(["GET"])
@rate_limit(limit_per_minute=30, scope="postal-directory-read", key_func=by_authenticated_account)
def postal_pincodes_view(request):
    """Step 3 — the real Pincodes under one specific Division, identified by id
    (not name: division names aren't guaranteed unique once more than one state's
    data is seeded, so a name-only lookup would be ambiguous the moment a second
    circle is added)."""
    if not request.user.is_authenticated:
        return error_response("UNAUTHORIZED", "not logged in", 401)
    try:
        division = Division.objects.get(id=request.GET.get("division_id"))
    except (Division.DoesNotExist, ValueError, TypeError):
        return error_response("NOT_FOUND", "no such division", 404)
    codes = list(division.pincodes.order_by("code").values_list("code", flat=True))
    return JsonResponse({"pincodes": codes}, status=200)


@csrf_exempt
@require_http_methods(["GET", "POST"])
@rate_limit(limit_per_minute=20, scope="locations", key_func=by_authenticated_account)
def locations_view(request):
    """§4.3: "richer business profile management (multi-location support)". Owner-only,
    same collection-endpoint shape as `staff_view` above (`GET` lists, `POST` creates).
    A `Resource` opts into a location via `domain_data["location_id"]` at creation time
    (see `resource_create_view`) — this endpoint only manages the `Location` rows
    themselves."""
    if not request.user.is_authenticated:
        return error_response("UNAUTHORIZED", "not logged in", 401)
    BusinessAccount = get_user_model()
    if request.user.role != BusinessAccount.Role.OWNER:
        return error_response(
            "FORBIDDEN", "only a business owner account can manage locations", 403
        )

    if request.method == "GET":
        locations = request.user.locations.values(
            "id", "name", "address", "city", "is_primary"
        )
        return JsonResponse(
            {"locations": list({**loc, "id": str(loc["id"])} for loc in locations)}, status=200
        )

    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return error_response("VALIDATION_ERROR", "Request body is not valid JSON", 400)

    name = (payload.get("name") or "").strip()
    if not name:
        return error_response("VALIDATION_ERROR", "name is required", 400, field="name")
    if len(name) > 255:
        return error_response("VALIDATION_ERROR", "name is too long (max 255)", 400, field="name")

    location = Location.objects.create(
        business=request.user,
        name=name,
        address=(payload.get("address") or "").strip(),
        city=(payload.get("city") or "").strip(),
        is_primary=bool(payload.get("is_primary", False)),
    )
    return JsonResponse({"id": str(location.id), "name": location.name}, status=201)


@csrf_exempt
@require_http_methods(["POST"])
def search_view(request):
    """Real /search business logic (livetracker2.md Phase 3.1) — receives Gateway's
    forwarded search intent, ACKs the calling Gateway/BAP pair synchronously, then
    builds and sends the real Beauty catalog as a signed /on_search callback in the
    background (see core/search_service.py's module docstring for the full
    async-mandate reasoning)."""
    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Request body is not valid JSON"}, status=400)

    response_body, status_code = search_service.validate_and_ack_search(
        payload=payload,
        authorization_header=request.headers.get("Authorization", ""),
        gateway_authorization_header=request.headers.get("X-Gateway-Authorization", ""),
        body=request.body,
    )
    if status_code == 200:
        search_service.dispatch_on_search_in_background(payload=payload)
    return JsonResponse(response_body, status=status_code)


@csrf_exempt
@require_http_methods(["POST"])
def select_view(request):
    """Real /select business logic (livetracker2.md Phase 3.2) — receives Gateway's
    forwarded selection, ACKs the calling Gateway/BAP pair synchronously, then resolves
    the requested item+time against live availability and attempts the real atomic
    hold in the background (see core/select_service.py's module docstring)."""
    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Request body is not valid JSON"}, status=400)

    response_body, status_code = select_service.validate_and_ack_select(
        payload=payload,
        authorization_header=request.headers.get("Authorization", ""),
        gateway_authorization_header=request.headers.get("X-Gateway-Authorization", ""),
        body=request.body,
    )
    if status_code == 200:
        select_service.dispatch_on_select_in_background(payload=payload)
    return JsonResponse(response_body, status=status_code)


@csrf_exempt
@require_http_methods(["POST"])
@rate_limit(limit_per_minute=60, scope="bpp-check-availability")
def check_availability_view(request):
    """Direct, signed BAP->BPP call (not a real Beckn action — see
    core/availability_service.py's module docstring) answering, per `resource_id`,
    whether it has a real open slot on a given date. IP-keyed defense-in-depth
    ceiling, matching `rating_view`'s/`support_view`'s own tier reasoning — a bit
    more generous here since picking a date is a natural single-tap gesture a real
    browsing customer can do several times a minute, not a one-off action."""
    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Request body is not valid JSON"}, status=400)

    response_body, status_code = availability_service.check_availability(
        payload=payload,
        authorization_header=request.headers.get("Authorization", ""),
        gateway_authorization_header=request.headers.get("X-Gateway-Authorization", ""),
        body=request.body,
    )
    return JsonResponse(response_body, status=status_code)


@csrf_exempt
@require_http_methods(["POST"])
@rate_limit(limit_per_minute=60, scope="bpp-list-available-slots")
def list_available_slots_view(request):
    """Direct, signed BAP->BPP call — the real bookable time windows for one
    resource on one date (see core/availability_service.py's own
    list_available_slots docstring). Same tier/reasoning as
    check_availability_view above."""
    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Request body is not valid JSON"}, status=400)

    response_body, status_code = availability_service.list_available_slots(
        payload=payload,
        authorization_header=request.headers.get("Authorization", ""),
        gateway_authorization_header=request.headers.get("X-Gateway-Authorization", ""),
        body=request.body,
    )
    return JsonResponse(response_body, status=status_code)


@csrf_exempt
@require_http_methods(["POST"])
def init_view(request):
    """Real /init business logic (livetracker2.md Phase 3.3) — receives Gateway's
    forwarded initialization, ACKs the calling Gateway/BAP pair synchronously, then
    revalidates the referenced booking against live state and returns a real
    Quotation in the background (see core/init_service.py's module docstring)."""
    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Request body is not valid JSON"}, status=400)

    response_body, status_code = init_service.validate_and_ack_init(
        payload=payload,
        authorization_header=request.headers.get("Authorization", ""),
        gateway_authorization_header=request.headers.get("X-Gateway-Authorization", ""),
        body=request.body,
    )
    if status_code == 200:
        init_service.dispatch_on_init_in_background(payload=payload)
    return JsonResponse(response_body, status=status_code)


@csrf_exempt
@require_http_methods(["POST"])
def confirm_view(request):
    """Real /confirm business logic (livetracker2.md Phase 3.4) — receives Gateway's
    forwarded confirmation, ACKs the calling Gateway/BAP pair synchronously, then
    performs the real HELD -> ACTIVE transition and returns the confirmed Order in
    the background (see core/confirm_service.py's module docstring)."""
    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Request body is not valid JSON"}, status=400)

    response_body, status_code = confirm_service.validate_and_ack_confirm(
        payload=payload,
        authorization_header=request.headers.get("Authorization", ""),
        gateway_authorization_header=request.headers.get("X-Gateway-Authorization", ""),
        body=request.body,
    )
    if status_code == 200:
        confirm_service.dispatch_on_confirm_in_background(payload=payload)
    return JsonResponse(response_body, status=status_code)


@csrf_exempt
@require_http_methods(["POST"])
def payment_status_view(request):
    """Real /payment_status receipt (livetracker5.md Phase 4.1) — a signed, direct
    BAP->BPP notification (not a real Beckn protocol action; see
    core/payment_status_service.py's module docstring for why it's still shaped
    and verified exactly like one). ACKs synchronously, then records the real
    status change directly (not backgrounded — a cheap DB write + local event
    publish, no external network call, same discipline as record_on_cancel_result)."""
    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Request body is not valid JSON"}, status=400)

    response_body, status_code = payment_status_service.validate_and_ack_payment_status(
        payload=payload,
        authorization_header=request.headers.get("Authorization", ""),
        gateway_authorization_header=request.headers.get("X-Gateway-Authorization", ""),
        body=request.body,
    )
    if status_code == 200:
        payment_status_service.record_payment_status(payload=payload)
    return JsonResponse(response_body, status=status_code)


@csrf_exempt
@require_http_methods(["POST"])
def status_view(request):
    """Real /status business logic (livetracker2.md Phase 3.5) — receives Gateway's
    forwarded status request, ACKs the calling Gateway/BAP pair synchronously, then
    returns the booking's real, live current state in the background (see
    core/status_service.py's module docstring)."""
    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Request body is not valid JSON"}, status=400)

    response_body, status_code = status_service.validate_and_ack_status(
        payload=payload,
        authorization_header=request.headers.get("Authorization", ""),
        gateway_authorization_header=request.headers.get("X-Gateway-Authorization", ""),
        body=request.body,
    )
    if status_code == 200:
        status_service.dispatch_on_status_in_background(payload=payload)
    return JsonResponse(response_body, status=status_code)


@csrf_exempt
@require_http_methods(["POST"])
def cancel_view(request):
    """Real /cancel business logic (livetracker2.md Phase 3.5) — receives Gateway's
    forwarded cancellation, ACKs the calling Gateway/BAP pair synchronously, then
    performs the real ACTIVE -> CANCELLED transition in the background (see
    core/cancel_service.py's module docstring)."""
    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Request body is not valid JSON"}, status=400)

    response_body, status_code = cancel_service.validate_and_ack_cancel(
        payload=payload,
        authorization_header=request.headers.get("Authorization", ""),
        gateway_authorization_header=request.headers.get("X-Gateway-Authorization", ""),
        body=request.body,
    )
    if status_code == 200:
        cancel_service.dispatch_on_cancel_in_background(payload=payload)
    return JsonResponse(response_body, status=status_code)


@csrf_exempt
@require_http_methods(["POST"])
def update_view(request):
    """Real /update business logic (livetracker2.md Phase 3.5) — receives Gateway's
    forwarded reschedule request, ACKs the calling Gateway/BAP pair synchronously,
    then moves the booking to the newly-requested slot in the background (see
    core/update_service.py's module docstring)."""
    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Request body is not valid JSON"}, status=400)

    response_body, status_code = update_service.validate_and_ack_update(
        payload=payload,
        authorization_header=request.headers.get("Authorization", ""),
        gateway_authorization_header=request.headers.get("X-Gateway-Authorization", ""),
        body=request.body,
    )
    if status_code == 200:
        update_service.dispatch_on_update_in_background(payload=payload)
    return JsonResponse(response_body, status=status_code)


@csrf_exempt
@require_http_methods(["POST"])
def track_view(request):
    """Real /track business logic (livetracker2.md Phase 3.5) — receives Gateway's
    forwarded tracking request, ACKs the calling Gateway/BAP pair synchronously,
    then returns a real (always-inactive, for this domain) Tracking object in the
    background (see core/track_service.py's module docstring)."""
    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Request body is not valid JSON"}, status=400)

    response_body, status_code = track_service.validate_and_ack_track(
        payload=payload,
        authorization_header=request.headers.get("Authorization", ""),
        gateway_authorization_header=request.headers.get("X-Gateway-Authorization", ""),
        body=request.body,
    )
    if status_code == 200:
        track_service.dispatch_on_track_in_background(payload=payload)
    return JsonResponse(response_body, status=status_code)


@csrf_exempt
@require_http_methods(["POST"])
@rate_limit(limit_per_minute=30, scope="bpp-rating")
def rating_view(request):
    """Real /rating business logic (livetracker2.md Phase 4.5) — receives Gateway's
    forwarded rating submission, ACKs the calling Gateway/BAP pair synchronously,
    then records every submitted rating in the background (see
    core/rating_service.py's module docstring).

    livetracker3.md §2.1 bullet 4: BAP's own customer-facing `rating_trigger_view`
    already carries `@rate_limit(limit_per_minute=10, scope="rating")` — this is a
    real, live gap that was distinct from that: BPP's own Gateway-receiving endpoint
    had no rate limiting at all, unlike this project's audit note assumed. A generous
    30/min defense-in-depth ceiling (matching `resource_create_view`'s own tier), not
    the primary customer-facing limiter — that's BAP's job, keyed per real customer IP;
    this is IP-keyed against Gateway's own relaying connection, protecting BPP itself
    from being flooded regardless of which BAP the traffic originated from."""
    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Request body is not valid JSON"}, status=400)

    response_body, status_code = rating_service.validate_and_ack_rating(
        payload=payload,
        authorization_header=request.headers.get("Authorization", ""),
        gateway_authorization_header=request.headers.get("X-Gateway-Authorization", ""),
        body=request.body,
    )
    if status_code == 200:
        rating_service.dispatch_on_rating_in_background(payload=payload)
    return JsonResponse(response_body, status=status_code)


@csrf_exempt
@require_http_methods(["POST"])
@rate_limit(limit_per_minute=30, scope="bpp-support")
def support_view(request):
    """Real /support business logic (livetracker2.md Phase 4.5) — receives Gateway's
    forwarded support lookup, ACKs the calling Gateway/BAP pair synchronously, then
    returns the owning business's real contact info in the background (see
    core/support_service.py's module docstring).

    livetracker3.md §2.1 bullet 4: same real, live BPP-side gap and same fix as
    `rating_view` above — see that view's docstring for the full reasoning."""
    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Request body is not valid JSON"}, status=400)

    response_body, status_code = support_service.validate_and_ack_support(
        payload=payload,
        authorization_header=request.headers.get("Authorization", ""),
        gateway_authorization_header=request.headers.get("X-Gateway-Authorization", ""),
        body=request.body,
    )
    if status_code == 200:
        support_service.dispatch_on_support_in_background(payload=payload)
    return JsonResponse(response_body, status=status_code)
