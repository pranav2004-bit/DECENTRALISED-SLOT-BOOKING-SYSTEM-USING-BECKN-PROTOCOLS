import json

from django.contrib.auth import authenticate, get_user_model, login, logout, password_validation
from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django_observability.errors import error_response

from . import onboarding_service


@require_http_methods(["GET"])
def ondc_site_verification_view(request):
    """Serves the signed domain-ownership verification file
    (protocol_compliance_notes_v1.1.md §B.2) at the exact path ONDC's Registry fetches:
    /ondc-site-verification.html — no query parameters, since Registry's fetch is a bare
    GET against the participant's subscriber_url. One FQDN, one active verification at a
    time (see core.models.SiteVerification)."""
    try:
        content = onboarding_service.get_verification_file_content()
    except onboarding_service.OnboardingError as exc:
        return HttpResponse(str(exc), status=404)
    return HttpResponse(content, content_type="text/html")


@csrf_exempt
@require_http_methods(["POST"])
def on_subscribe_view(request):
    """Registry-initiated callback (protocol_compliance_notes_v1.1.md §A.1) — decrypts
    the challenge and returns the answer. Not authenticated by an Authorization header:
    the challenge-response itself IS the authentication (only the real key holder can
    decrypt it), matching the confirmed protocol design."""
    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Request body is not valid JSON"}, status=400)

    try:
        result = onboarding_service.handle_on_subscribe(payload)
    except Exception as exc:
        return JsonResponse({"error": f"Challenge decryption failed: {exc}"}, status=400)

    return JsonResponse(result, status=200)


def _customer_json(customer) -> dict:
    return {
        "id": str(customer.id),
        "name": customer.name,
        "contact": customer.contact,
        "notify_by_email": customer.notify_by_email,
    }


@csrf_exempt
@require_http_methods(["POST"])
def signup_view(request):
    """Customer signup (livetracker2.md §2.1) — an ordinary web-app auth layer, unrelated
    to the Ed25519/Registry participant trust in livetracker1.md. Not wrapped in
    Idempotency-Key replay logic (API_CONVENTIONS.md's general rule for mutating
    endpoints): `contact`'s DB-level unique constraint already prevents the one dangerous
    outcome (a duplicate account) on a retried request, so full response-replay storage is
    deferred until a real endpoint needs it (deliberate scope decision, not an oversight).
    """
    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return error_response("VALIDATION_ERROR", "Request body is not valid JSON", 400)

    name = (payload.get("name") or "").strip()
    contact = (payload.get("contact") or "").strip()
    password = payload.get("password") or ""

    if not name:
        return error_response("VALIDATION_ERROR", "name is required", 400, field="name")
    if not contact:
        return error_response("VALIDATION_ERROR", "contact is required", 400, field="contact")
    if not password:
        return error_response("VALIDATION_ERROR", "password is required", 400, field="password")

    Customer = get_user_model()
    if Customer.objects.filter(contact=contact).exists():
        return error_response(
            "VALIDATION_ERROR", "an account with this contact already exists", 409, field="contact"
        )

    try:
        password_validation.validate_password(password)
    except DjangoValidationError as exc:
        return error_response("VALIDATION_ERROR", " ".join(exc.messages), 400, field="password")

    customer = Customer.objects.create_user(contact=contact, name=name, password=password)
    return JsonResponse(_customer_json(customer), status=201)


@csrf_exempt
@require_http_methods(["POST"])
def login_view(request):
    """Logs a customer in and establishes a real session (Redis-backed, per
    `SESSION_ENGINE`). `authenticate()` already refuses an `is_active=False` customer —
    the Buyer Lifecycle Management gate, satisfied by Django's own auth machinery."""
    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return error_response("VALIDATION_ERROR", "Request body is not valid JSON", 400)

    contact = (payload.get("contact") or "").strip()
    password = payload.get("password") or ""

    customer = authenticate(request, username=contact, password=password)
    if customer is None:
        return error_response("UNAUTHORIZED", "invalid contact or password", 401)

    login(request, customer)
    return JsonResponse(_customer_json(customer), status=200)


@require_http_methods(["POST"])
def logout_view(request):
    logout(request)
    return JsonResponse({"status": "ok"}, status=200)


@require_http_methods(["GET"])
def me_view(request):
    """Proves a customer is identified consistently across a session (§2.1's own Test
    Gate wording) — the same session cookie returns the same customer on every call."""
    if not request.user.is_authenticated:
        return error_response("UNAUTHORIZED", "not logged in", 401)
    return JsonResponse(_customer_json(request.user), status=200)
