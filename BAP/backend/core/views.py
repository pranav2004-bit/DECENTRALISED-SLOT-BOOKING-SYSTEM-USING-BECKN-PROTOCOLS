import json

from django.contrib.auth import authenticate, get_user_model, login, logout, password_validation
from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django_observability.errors import error_response

from . import init_service, onboarding_service, search_service, select_service


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


@csrf_exempt
@require_http_methods(["POST"])
def search_trigger_view(request):
    """Customer-facing search trigger (livetracker2.md §3.1) — deliberately NOT the
    Beckn wire shape (API_CONVENTIONS.md §3.6's scope line: web-to-backend calls use
    this project's own simple JSON convention, never the protocol shape). Builds and
    sends the real signed Beckn `/search` to Gateway, returns a transaction_id the
    browser polls for results with — search itself doesn't require login, matching
    ordinary browse-before-signup e-commerce UX."""
    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return error_response("VALIDATION_ERROR", "Request body is not valid JSON", 400)

    query = (payload.get("query") or "").strip()
    domain = (payload.get("domain") or "").strip()
    if not query or not domain:
        return error_response("VALIDATION_ERROR", "query and domain are required", 400)

    customer = request.user if request.user.is_authenticated else None
    try:
        transaction_id = search_service.trigger_search(
            query=query, domain=domain, customer=customer
        )
    except search_service.SearchError as exc:
        return error_response("SEARCH_UNAVAILABLE", exc.message, exc.status_code)

    return JsonResponse({"transaction_id": transaction_id}, status=202)


@require_http_methods(["GET"])
def search_results_view(request, transaction_id):
    """Customer-facing results poll — real results only exist once BPP(s) actually
    call back via /on_search, so an empty `results` list here is a normal in-progress
    state, not an error (protocol_compliance_notes_v1.1.md §H.1: async is mandatory,
    there is no synchronous alternative to poll around)."""
    result = search_service.get_search_results(transaction_id=transaction_id)
    if result is None:
        return error_response("NOT_FOUND", "no such search transaction", 404)
    return JsonResponse(result, status=200)


@csrf_exempt
@require_http_methods(["POST"])
def on_search_view(request):
    """Real /on_search wire endpoint — receives Gateway's relayed callback from a BPP,
    verifies both the BPP's and Gateway's signatures, ACKs synchronously, then records
    the real catalog against the matching SearchSession in the background (same
    async-mandate discipline as Gateway/BPP's own routing/search_service)."""
    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Request body is not valid JSON"}, status=400)

    response_body, status_code = search_service.validate_and_ack_on_search(
        payload=payload,
        authorization_header=request.headers.get("Authorization", ""),
        gateway_authorization_header=request.headers.get("X-Gateway-Authorization", ""),
        body=request.body,
    )
    if status_code == 200:
        search_service.record_on_search_result(payload=payload)
    return JsonResponse(response_body, status=status_code)


@csrf_exempt
@require_http_methods(["POST"])
def select_trigger_view(request):
    """Customer-facing selection trigger (livetracker2.md §3.2) — deliberately NOT the
    Beckn wire shape, same convention as search_trigger_view. Builds and sends the
    real signed Beckn /select to Gateway, targeting the specific BPP that offered the
    chosen item in an earlier real search."""
    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return error_response("VALIDATION_ERROR", "Request body is not valid JSON", 400)

    transaction_id = (payload.get("transaction_id") or "").strip()
    item_id = (payload.get("item_id") or "").strip()
    requested_timestamp = (payload.get("requested_timestamp") or "").strip()
    if not transaction_id or not item_id or not requested_timestamp:
        return error_response(
            "VALIDATION_ERROR",
            "transaction_id, item_id, and requested_timestamp are required",
            400,
        )

    try:
        select_service.trigger_select(
            transaction_id=transaction_id,
            item_id=item_id,
            requested_timestamp=requested_timestamp,
        )
    except select_service.SelectError as exc:
        return error_response("SELECT_UNAVAILABLE", exc.message, exc.status_code)

    return JsonResponse({"transaction_id": transaction_id}, status=202)


@require_http_methods(["GET"])
def select_result_view(request, transaction_id):
    """Customer-facing selection result poll — real results only exist once the BPP
    actually calls back via /on_select, so both fields being null here is a normal
    in-progress state, not an error."""
    result = select_service.get_selection_result(transaction_id=transaction_id)
    if result is None:
        return error_response("NOT_FOUND", "no such search transaction", 404)
    return JsonResponse(result, status=200)


@csrf_exempt
@require_http_methods(["POST"])
def on_select_view(request):
    """Real /on_select wire endpoint — receives Gateway's relayed callback from a BPP,
    verifies both the BPP's and Gateway's signatures, ACKs synchronously, then records
    the real Order+quote (or error) against the matching SearchSession."""
    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Request body is not valid JSON"}, status=400)

    response_body, status_code = select_service.validate_and_ack_on_select(
        payload=payload,
        authorization_header=request.headers.get("Authorization", ""),
        gateway_authorization_header=request.headers.get("X-Gateway-Authorization", ""),
        body=request.body,
    )
    if status_code == 200:
        select_service.record_on_select_result(payload=payload)
    return JsonResponse(response_body, status=status_code)


@csrf_exempt
@require_http_methods(["POST"])
def init_trigger_view(request):
    """Customer-facing initialization trigger (livetracker2.md §3.3) — deliberately
    NOT the Beckn wire shape, same convention as select_trigger_view. Builds and
    sends the real signed Beckn /init to Gateway, targeting the same BPP a prior
    successful /select already resolved to. No new fields needed from the customer —
    everything required already lives on the session from Selection."""
    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return error_response("VALIDATION_ERROR", "Request body is not valid JSON", 400)

    transaction_id = (payload.get("transaction_id") or "").strip()
    if not transaction_id:
        return error_response("VALIDATION_ERROR", "transaction_id is required", 400)

    try:
        init_service.trigger_init(transaction_id=transaction_id)
    except init_service.InitError as exc:
        return error_response("INIT_UNAVAILABLE", exc.message, exc.status_code)

    return JsonResponse({"transaction_id": transaction_id}, status=202)


@require_http_methods(["GET"])
def init_result_view(request, transaction_id):
    """Customer-facing initialization result poll — real results only exist once
    the BPP actually calls back via /on_init, so both fields being null here is a
    normal in-progress state, not an error."""
    result = init_service.get_init_result(transaction_id=transaction_id)
    if result is None:
        return error_response("NOT_FOUND", "no such search transaction", 404)
    return JsonResponse(result, status=200)


@csrf_exempt
@require_http_methods(["POST"])
def on_init_view(request):
    """Real /on_init wire endpoint — receives Gateway's relayed callback from a BPP,
    verifies both the BPP's and Gateway's signatures, ACKs synchronously, then
    records the real Order+Quotation (or error) against the matching SearchSession."""
    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Request body is not valid JSON"}, status=400)

    response_body, status_code = init_service.validate_and_ack_on_init(
        payload=payload,
        authorization_header=request.headers.get("Authorization", ""),
        gateway_authorization_header=request.headers.get("X-Gateway-Authorization", ""),
        body=request.body,
    )
    if status_code == 200:
        init_service.record_on_init_result(payload=payload)
    return JsonResponse(response_body, status=status_code)
