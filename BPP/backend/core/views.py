import datetime as dt
import json
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib.auth import authenticate, get_user_model, login, logout, password_validation
from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import HttpResponse, JsonResponse
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django_observability.errors import error_response
from inventory_core.domain_adapter import get_adapter
from inventory_core.models import AvailabilityCalendar, Resource

from . import init_service, onboarding_service, search_service, select_service
from .catalog import visible_resources


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
    return {
        "id": str(account.id),
        "business_name": account.business_name,
        "contact": account.contact,
    }


@csrf_exempt
@require_http_methods(["POST"])
def business_signup_view(request):
    """One business-account login (livetracker2.md §2.2) — same shape as BAP's customer
    signup (§2.1), for the same reasons (see BAP/backend/core/views.py's `signup_view`)."""
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
        contact=contact, business_name=business_name, password=password
    )
    return JsonResponse(_business_account_json(account), status=201)


@csrf_exempt
@require_http_methods(["POST"])
def business_login_view(request):
    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return error_response("VALIDATION_ERROR", "Request body is not valid JSON", 400)

    contact = (payload.get("contact") or "").strip()
    password = payload.get("password") or ""

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
def resource_create_view(request):
    """The logged-in business account creates a real `Resource` it owns (§2.2) —
    `owner_ref` is always the authenticated account's own id, never client-supplied, so a
    business can only ever create resources under itself."""
    if not request.user.is_authenticated:
        return error_response("UNAUTHORIZED", "not logged in", 401)

    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return error_response("VALIDATION_ERROR", "Request body is not valid JSON", 400)

    name = (payload.get("name") or "").strip()
    if not name:
        return error_response("VALIDATION_ERROR", "name is required", 400, field="name")

    domain_data = payload.get("domain_data") or {}
    adapter = get_adapter(settings.DOMAIN_BEAUTY)
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
    return JsonResponse({"id": str(resource.id), "name": resource.name}, status=201)


@csrf_exempt
@require_http_methods(["POST"])
def resource_availability_create_view(request, resource_id):
    """Creates a real `AvailabilityCalendar` for one of the logged-in business's own
    `Resource`s and generates real `Slot` rows from it (§2.2's own Test Gate wording)."""
    if not request.user.is_authenticated:
        return error_response("UNAUTHORIZED", "not logged in", 401)

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

    try:
        calendar = AvailabilityCalendar.objects.create(
            resource=resource,
            frequency=dt.timedelta(days=payload.get("frequency_days", 1)),
            range_start=range_start,
            range_end=range_end,
            days=payload.get("days", ""),
            times=payload.get("times", []),
            holidays=payload.get("holidays", []),
            slot_duration=dt.timedelta(minutes=payload.get("slot_duration_minutes", 30)),
            slot_capacity=payload.get("slot_capacity", 1),
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
    flow."""
    resources = visible_resources().values("id", "name", "owner_ref")
    return JsonResponse({"resources": list(resources)}, status=200)


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
