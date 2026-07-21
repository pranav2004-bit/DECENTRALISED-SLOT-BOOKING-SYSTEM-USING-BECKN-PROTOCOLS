import json

from django.contrib.auth import authenticate, get_user_model, login, logout, password_validation
from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie
from django.views.decorators.http import require_http_methods
from django_observability.errors import error_response
from django_observability.idempotency import idempotent_view
from django_observability.rate_limit import rate_limit

from . import (
    booking_history_service,
    cancel_service,
    confirm_service,
    init_service,
    onboarding_service,
    pagination,
    search_service,
    select_service,
    status_service,
    track_service,
    update_service,
)


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


@require_http_methods(["GET"])
@ensure_csrf_cookie
def csrf_token_view(request):
    """The real, standard Django mechanism for a cross-origin SPA to obtain a
    CSRF cookie before its first mutating call (§3.7) — needed now that
    `signup_view`/`login_view` are no longer `@csrf_exempt`: `ensure_csrf_cookie`
    forces Django to set the `csrftoken` cookie on this response even though
    nothing here renders a template calling `get_token()` itself. The frontend
    reads that cookie and echoes it back as `X-CSRFToken` on its next POST, the
    documented Django AJAX-CSRF pattern. No real frontend calls this yet (only
    the Phase 2.4 shell exists) — ready for §3.9 to consume."""
    return JsonResponse({"status": "ok"}, status=200)


@require_http_methods(["POST"])
@rate_limit(limit_per_minute=5, scope="signup")
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
    # §3.7: reject oversized input before it reaches the DB layer, where a real
    # Postgres varchar(255) overflow would otherwise surface as an uncaught
    # DataError -> a generic 500, not a clean 400.
    if len(name) > 255:
        return error_response("VALIDATION_ERROR", "name is too long (max 255)", 400, field="name")
    if len(contact) > 255:
        return error_response(
            "VALIDATION_ERROR", "contact is too long (max 255)", 400, field="contact"
        )

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


@require_http_methods(["POST"])
@rate_limit(limit_per_minute=5, scope="login")
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

    if len(contact) > 255:
        return error_response("UNAUTHORIZED", "invalid contact or password", 401)

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


@require_http_methods(["GET"])
def bookings_list_view(request):
    """The logged-in customer's own booking history (§3.6, livetracker2.md) — a
    genuinely new endpoint, not an existing one this phase merely paginates (see
    booking_history_service's module docstring). IDOR-safe by construction: always
    scoped to request.user, never a customer id read from the request. Cursor-
    paginated the same way as search_results_view."""
    if not request.user.is_authenticated:
        return error_response("UNAUTHORIZED", "not logged in", 401)
    limit = pagination.parse_limit(request.GET.get("limit"))
    result = booking_history_service.get_customer_bookings(
        customer=request.user, cursor=request.GET.get("cursor"), limit=limit
    )
    return JsonResponse(result, status=200)


@csrf_exempt
@require_http_methods(["POST"])
@rate_limit(limit_per_minute=20, scope="search")
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
    # §3.7: reject oversized input before it reaches business logic / the DB.
    if len(query) > 500:
        return error_response("VALIDATION_ERROR", "query is too long (max 500)", 400, field="query")
    if len(domain) > 100:
        return error_response(
            "VALIDATION_ERROR", "domain is too long (max 100)", 400, field="domain"
        )

    customer = request.user if request.user.is_authenticated else None
    try:
        transaction_id = search_service.trigger_search(
            query=query,
            domain=domain,
            customer=customer,
            client_ip=request.META.get("REMOTE_ADDR"),
        )
    except search_service.SearchError as exc:
        return error_response("SEARCH_UNAVAILABLE", exc.message, exc.status_code)

    return JsonResponse({"transaction_id": transaction_id}, status=202)


@require_http_methods(["GET"])
def search_results_view(request, transaction_id):
    """Customer-facing results poll — real results only exist once BPP(s) actually
    call back via /on_search, so an empty `results` list here is a normal in-progress
    state, not an error (protocol_compliance_notes_v1.1.md §H.1: async is mandatory,
    there is no synchronous alternative to poll around).

    Cursor-paginated (§3.6, API_CONVENTIONS.md `?cursor=&limit=`) — see
    search_service.get_search_results's docstring for why the cursor is a bpp_id,
    not a raw offset."""
    limit = pagination.parse_limit(request.GET.get("limit"))
    customer = request.user if request.user.is_authenticated else None
    try:
        result = search_service.get_search_results(
            transaction_id=transaction_id, cursor=request.GET.get("cursor"), limit=limit,
            customer=customer,
        )
    except search_service.SearchError as exc:
        return error_response("SEARCH_UNAVAILABLE", exc.message, exc.status_code)
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
@rate_limit(limit_per_minute=10, scope="select")
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

    customer = request.user if request.user.is_authenticated else None
    try:
        select_service.trigger_select(
            transaction_id=transaction_id,
            item_id=item_id,
            requested_timestamp=requested_timestamp,
            customer=customer,
        )
    except select_service.SelectError as exc:
        return error_response("SELECT_UNAVAILABLE", exc.message, exc.status_code)

    return JsonResponse({"transaction_id": transaction_id}, status=202)


@require_http_methods(["GET"])
def select_result_view(request, transaction_id):
    """Customer-facing selection result poll — real results only exist once the BPP
    actually calls back via /on_select, so both fields being null here is a normal
    in-progress state, not an error."""
    customer = request.user if request.user.is_authenticated else None
    try:
        result = select_service.get_selection_result(
            transaction_id=transaction_id, customer=customer
        )
    except select_service.SelectError as exc:
        return error_response("SELECT_UNAVAILABLE", exc.message, exc.status_code)
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
@rate_limit(limit_per_minute=10, scope="init")
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

    customer = request.user if request.user.is_authenticated else None
    try:
        init_service.trigger_init(transaction_id=transaction_id, customer=customer)
    except init_service.InitError as exc:
        return error_response("INIT_UNAVAILABLE", exc.message, exc.status_code)

    return JsonResponse({"transaction_id": transaction_id}, status=202)


@require_http_methods(["GET"])
def init_result_view(request, transaction_id):
    """Customer-facing initialization result poll — real results only exist once
    the BPP actually calls back via /on_init, so both fields being null here is a
    normal in-progress state, not an error."""
    customer = request.user if request.user.is_authenticated else None
    try:
        result = init_service.get_init_result(transaction_id=transaction_id, customer=customer)
    except init_service.InitError as exc:
        return error_response("INIT_UNAVAILABLE", exc.message, exc.status_code)
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


@csrf_exempt
@require_http_methods(["POST"])
@rate_limit(limit_per_minute=10, scope="confirm")
@idempotent_view()
def confirm_trigger_view(request):
    """Customer-facing confirmation trigger (livetracker2.md §3.4) — deliberately
    NOT the Beckn wire shape, same convention as init_trigger_view. Builds and
    sends the real signed Beckn /confirm to Gateway, targeting the same BPP a prior
    successful /init already resolved to. No new fields needed from the customer —
    everything required already lives on the session from Initialization.

    `@idempotent_view()` (§3.6): a client sending a real `Idempotency-Key` header gets
    the exact same recorded response replayed on a retry, instead of a second real
    Beckn /confirm firing at Gateway/BPP — the specific web-layer double-booking risk
    this bullet asked for, independent of and in addition to event-level idempotency
    (§1.4)."""
    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return error_response("VALIDATION_ERROR", "Request body is not valid JSON", 400)

    transaction_id = (payload.get("transaction_id") or "").strip()
    if not transaction_id:
        return error_response("VALIDATION_ERROR", "transaction_id is required", 400)

    customer = request.user if request.user.is_authenticated else None
    try:
        confirm_service.trigger_confirm(transaction_id=transaction_id, customer=customer)
    except confirm_service.ConfirmError as exc:
        return error_response("CONFIRM_UNAVAILABLE", exc.message, exc.status_code)

    return JsonResponse({"transaction_id": transaction_id}, status=202)


@require_http_methods(["GET"])
def confirm_result_view(request, transaction_id):
    """Customer-facing confirmation result poll — real results only exist once the
    BPP actually calls back via /on_confirm, so both fields being null here is a
    normal in-progress state, not an error."""
    customer = request.user if request.user.is_authenticated else None
    try:
        result = confirm_service.get_confirm_result(
            transaction_id=transaction_id, customer=customer
        )
    except confirm_service.ConfirmError as exc:
        return error_response("CONFIRM_UNAVAILABLE", exc.message, exc.status_code)
    if result is None:
        return error_response("NOT_FOUND", "no such search transaction", 404)
    return JsonResponse(result, status=200)


@csrf_exempt
@require_http_methods(["POST"])
def on_confirm_view(request):
    """Real /on_confirm wire endpoint — receives Gateway's relayed callback from a
    BPP, verifies both the BPP's and Gateway's signatures, ACKs synchronously, then
    records the real confirmed Order (or error) against the matching SearchSession."""
    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Request body is not valid JSON"}, status=400)

    response_body, status_code = confirm_service.validate_and_ack_on_confirm(
        payload=payload,
        authorization_header=request.headers.get("Authorization", ""),
        gateway_authorization_header=request.headers.get("X-Gateway-Authorization", ""),
        body=request.body,
    )
    if status_code == 200:
        confirm_service.record_on_confirm_result(payload=payload)
    return JsonResponse(response_body, status=status_code)


@csrf_exempt
@require_http_methods(["POST"])
@rate_limit(limit_per_minute=10, scope="status")
def status_trigger_view(request):
    """Customer-facing status-check trigger (livetracker2.md §3.5) — deliberately
    NOT the Beckn wire shape. Sends the real signed Beckn /status to Gateway,
    targeting the same BPP this transaction was confirmed with."""
    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return error_response("VALIDATION_ERROR", "Request body is not valid JSON", 400)

    transaction_id = (payload.get("transaction_id") or "").strip()
    if not transaction_id:
        return error_response("VALIDATION_ERROR", "transaction_id is required", 400)

    customer = request.user if request.user.is_authenticated else None
    try:
        status_service.trigger_status(transaction_id=transaction_id, customer=customer)
    except status_service.StatusError as exc:
        return error_response("STATUS_UNAVAILABLE", exc.message, exc.status_code)

    return JsonResponse({"transaction_id": transaction_id}, status=202)


@require_http_methods(["GET"])
def status_result_view(request, transaction_id):
    """Customer-facing status result poll — real results only exist once the BPP
    actually calls back via /on_status, so both fields being null here is a
    normal in-progress state, not an error."""
    customer = request.user if request.user.is_authenticated else None
    try:
        result = status_service.get_status_result(transaction_id=transaction_id, customer=customer)
    except status_service.StatusError as exc:
        return error_response("STATUS_UNAVAILABLE", exc.message, exc.status_code)
    if result is None:
        return error_response("NOT_FOUND", "no such search transaction", 404)
    return JsonResponse(result, status=200)


@csrf_exempt
@require_http_methods(["POST"])
def on_status_view(request):
    """Real /on_status wire endpoint — receives Gateway's relayed callback from a
    BPP, verifies both the BPP's and Gateway's signatures, ACKs synchronously,
    then records the real Order (or error) against the matching SearchSession."""
    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Request body is not valid JSON"}, status=400)

    response_body, status_code = status_service.validate_and_ack_on_status(
        payload=payload,
        authorization_header=request.headers.get("Authorization", ""),
        gateway_authorization_header=request.headers.get("X-Gateway-Authorization", ""),
        body=request.body,
    )
    if status_code == 200:
        status_service.record_on_status_result(payload=payload)
    return JsonResponse(response_body, status=status_code)


@csrf_exempt
@require_http_methods(["POST"])
@rate_limit(limit_per_minute=10, scope="cancel")
def cancel_trigger_view(request):
    """Customer-facing cancellation trigger (livetracker2.md §3.5) — deliberately
    NOT the Beckn wire shape. Sends the real signed Beckn /cancel to Gateway,
    targeting the same BPP this transaction was confirmed with.
    `cancellation_reason_id` is optional free-form text from the customer (no
    real cancellation-reason catalog exists in this project)."""
    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return error_response("VALIDATION_ERROR", "Request body is not valid JSON", 400)

    transaction_id = (payload.get("transaction_id") or "").strip()
    if not transaction_id:
        return error_response("VALIDATION_ERROR", "transaction_id is required", 400)

    cancellation_reason_id = (payload.get("cancellation_reason_id") or "").strip()

    customer = request.user if request.user.is_authenticated else None
    try:
        cancel_service.trigger_cancel(
            transaction_id=transaction_id,
            cancellation_reason_id=cancellation_reason_id,
            customer=customer,
        )
    except cancel_service.CancelError as exc:
        return error_response("CANCEL_UNAVAILABLE", exc.message, exc.status_code)

    return JsonResponse({"transaction_id": transaction_id}, status=202)


@require_http_methods(["GET"])
def cancel_result_view(request, transaction_id):
    """Customer-facing cancellation result poll — real results only exist once
    the BPP actually calls back via /on_cancel, so both fields being null here
    is a normal in-progress state, not an error."""
    customer = request.user if request.user.is_authenticated else None
    try:
        result = cancel_service.get_cancel_result(transaction_id=transaction_id, customer=customer)
    except cancel_service.CancelError as exc:
        return error_response("CANCEL_UNAVAILABLE", exc.message, exc.status_code)
    if result is None:
        return error_response("NOT_FOUND", "no such search transaction", 404)
    return JsonResponse(result, status=200)


@csrf_exempt
@require_http_methods(["POST"])
def on_cancel_view(request):
    """Real /on_cancel wire endpoint — receives Gateway's relayed callback from a
    BPP, verifies both the BPP's and Gateway's signatures, ACKs synchronously,
    then records the real cancelled Order (or error) against the matching
    SearchSession."""
    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Request body is not valid JSON"}, status=400)

    response_body, status_code = cancel_service.validate_and_ack_on_cancel(
        payload=payload,
        authorization_header=request.headers.get("Authorization", ""),
        gateway_authorization_header=request.headers.get("X-Gateway-Authorization", ""),
        body=request.body,
    )
    if status_code == 200:
        cancel_service.record_on_cancel_result(payload=payload)
    return JsonResponse(response_body, status=status_code)


@csrf_exempt
@require_http_methods(["POST"])
@rate_limit(limit_per_minute=10, scope="update")
def update_trigger_view(request):
    """Customer-facing reschedule trigger (livetracker2.md §3.5) — deliberately
    NOT the Beckn wire shape. Sends the real signed Beckn /update to Gateway,
    targeting the same BPP this transaction was confirmed with, requesting the
    booking be moved to `requested_timestamp`."""
    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return error_response("VALIDATION_ERROR", "Request body is not valid JSON", 400)

    transaction_id = (payload.get("transaction_id") or "").strip()
    requested_timestamp = (payload.get("requested_timestamp") or "").strip()
    if not transaction_id or not requested_timestamp:
        return error_response(
            "VALIDATION_ERROR", "transaction_id and requested_timestamp are required", 400
        )

    customer = request.user if request.user.is_authenticated else None
    try:
        update_service.trigger_update(
            transaction_id=transaction_id,
            requested_timestamp=requested_timestamp,
            customer=customer,
        )
    except update_service.UpdateError as exc:
        return error_response("UPDATE_UNAVAILABLE", exc.message, exc.status_code)

    return JsonResponse({"transaction_id": transaction_id}, status=202)


@require_http_methods(["GET"])
def update_result_view(request, transaction_id):
    """Customer-facing reschedule result poll — real results only exist once the
    BPP actually calls back via /on_update, so both fields being null here is a
    normal in-progress state, not an error."""
    customer = request.user if request.user.is_authenticated else None
    try:
        result = update_service.get_update_result(transaction_id=transaction_id, customer=customer)
    except update_service.UpdateError as exc:
        return error_response("UPDATE_UNAVAILABLE", exc.message, exc.status_code)
    if result is None:
        return error_response("NOT_FOUND", "no such search transaction", 404)
    return JsonResponse(result, status=200)


@csrf_exempt
@require_http_methods(["POST"])
def on_update_view(request):
    """Real /on_update wire endpoint — receives Gateway's relayed callback from a
    BPP, verifies both the BPP's and Gateway's signatures, ACKs synchronously,
    then records the real rescheduled Order (or error) against the matching
    SearchSession."""
    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Request body is not valid JSON"}, status=400)

    response_body, status_code = update_service.validate_and_ack_on_update(
        payload=payload,
        authorization_header=request.headers.get("Authorization", ""),
        gateway_authorization_header=request.headers.get("X-Gateway-Authorization", ""),
        body=request.body,
    )
    if status_code == 200:
        update_service.record_on_update_result(payload=payload)
    return JsonResponse(response_body, status=status_code)


@csrf_exempt
@require_http_methods(["POST"])
@rate_limit(limit_per_minute=10, scope="track")
def track_trigger_view(request):
    """Customer-facing tracking trigger (livetracker2.md §3.5) — deliberately NOT
    the Beckn wire shape. Sends the real signed Beckn /track to Gateway,
    targeting the same BPP this transaction was confirmed with."""
    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return error_response("VALIDATION_ERROR", "Request body is not valid JSON", 400)

    transaction_id = (payload.get("transaction_id") or "").strip()
    if not transaction_id:
        return error_response("VALIDATION_ERROR", "transaction_id is required", 400)

    customer = request.user if request.user.is_authenticated else None
    try:
        track_service.trigger_track(transaction_id=transaction_id, customer=customer)
    except track_service.TrackError as exc:
        return error_response("TRACK_UNAVAILABLE", exc.message, exc.status_code)

    return JsonResponse({"transaction_id": transaction_id}, status=202)


@require_http_methods(["GET"])
def track_result_view(request, transaction_id):
    """Customer-facing tracking result poll — real results only exist once the
    BPP actually calls back via /on_track, so both fields being null here is a
    normal in-progress state, not an error."""
    customer = request.user if request.user.is_authenticated else None
    try:
        result = track_service.get_track_result(transaction_id=transaction_id, customer=customer)
    except track_service.TrackError as exc:
        return error_response("TRACK_UNAVAILABLE", exc.message, exc.status_code)
    if result is None:
        return error_response("NOT_FOUND", "no such search transaction", 404)
    return JsonResponse(result, status=200)


@csrf_exempt
@require_http_methods(["POST"])
def on_track_view(request):
    """Real /on_track wire endpoint — receives Gateway's relayed callback from a
    BPP, verifies both the BPP's and Gateway's signatures, ACKs synchronously,
    then records the real (always-inactive, for this domain) Tracking object
    (or error) against the matching SearchSession."""
    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Request body is not valid JSON"}, status=400)

    response_body, status_code = track_service.validate_and_ack_on_track(
        payload=payload,
        authorization_header=request.headers.get("Authorization", ""),
        gateway_authorization_header=request.headers.get("X-Gateway-Authorization", ""),
        body=request.body,
    )
    if status_code == 200:
        track_service.record_on_track_result(payload=payload)
    return JsonResponse(response_body, status=status_code)
