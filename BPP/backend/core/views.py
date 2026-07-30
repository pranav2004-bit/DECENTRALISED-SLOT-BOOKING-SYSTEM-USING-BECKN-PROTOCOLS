import datetime as dt
import json
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib.auth import authenticate, get_user_model, login, logout, password_validation
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie
from django.views.decorators.http import require_http_methods
from django_observability.errors import error_response
from django_observability.rate_limit import by_authenticated_account, rate_limit
from inventory_core.domain_adapter import get_adapter
from inventory_core.models import AvailabilityCalendar, Resource, Slot

from . import (
    cancel_service,
    confirm_service,
    init_service,
    onboarding_service,
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
    return data


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
    return JsonResponse({"id": str(resource.id), "name": resource.name}, status=201)


@csrf_exempt
@require_http_methods(["GET", "POST"])
@rate_limit(
    limit_per_minute=30, scope="resource-availability-create", key_func=by_authenticated_account
)
def resource_availability_create_view(request, resource_id):
    """Creates a real `AvailabilityCalendar` for one of the logged-in business's own
    `Resource`s and generates real `Slot` rows from it (§2.2's own Test Gate wording).

    §4.3: `POST` (create) is owner-only, same reasoning as `resource_create_view` —
    bulk calendar generation is inventory *creation*, distinct from a staff account
    merely blocking off already-generated slots (`resource_availability_block_view`).

    §4.4: `GET` lists the resource's current slots — owner or assigned staff, the
    same access model as the block view — feeding a live availability dashboard's
    initial render before its WebSocket connection takes over.

    livetracker3.md §2.2: also returns the resource's own real rating aggregate
    (`average_rating`/`rating_count`) alongside the slot list — one wire call, not a
    second endpoint, since the dashboard already fetches this resource by id here.
    `average_rating` is `None` (not a fabricated `0`) when no aggregate-eligible
    rating exists yet — the business side seeing its own honest, real rating is the
    whole point of this addition (§2.2 bullet 2's own reasoning)."""
    if not request.user.is_authenticated:
        return error_response("UNAUTHORIZED", "not logged in", 401)

    if request.method == "GET":
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
                        str(resource.average_rating)
                        if resource.average_rating is not None
                        else None
                    ),
                    "rating_count": resource.rating_count,
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
                ]
            },
            status=200,
        )

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
        return JsonResponse({"staff": list({**s, "id": str(s["id"])} for s in staff)}, status=200)

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
@rate_limit(limit_per_minute=30, scope="resource-assign-staff", key_func=by_authenticated_account)
def resource_assign_staff_view(request, resource_id):
    """§4.3: the owner assigns one of its own `Resource`s to one of its own staff
    accounts — `Resource.domain_data["assigned_staff_id"]` is the opaque link the
    availability-block view below trusts for staff-scoped authorization. Owner-only,
    same IDOR posture as `resource_availability_create_view` (404, not 403, on
    mismatch, so a caller can't distinguish "not yours" from "doesn't exist")."""
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

    staff_id = payload.get("staff_id")
    if not staff_id:
        return error_response("VALIDATION_ERROR", "staff_id is required", 400, field="staff_id")
    if not BusinessAccount.objects.filter(
        id=staff_id, role=BusinessAccount.Role.STAFF, managed_by=request.user
    ).exists():
        return error_response(
            "VALIDATION_ERROR", "no such staff account for this business", 400, field="staff_id"
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
