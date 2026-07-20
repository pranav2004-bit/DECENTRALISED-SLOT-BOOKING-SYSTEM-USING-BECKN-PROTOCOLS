import json

from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from . import onboarding_service, routing


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


@csrf_exempt
@require_http_methods(["POST"])
def search_view(request):
    """Real /search business logic (livetracker2.md Phase 3.1) — see
    core/routing.py's module docstring for the async-mandate reasoning. Validates and
    ACKs the calling BAP synchronously, then fires the actual forwarding to every
    SUBSCRIBED BPP in the background — the ACK returns without waiting on any BPP."""
    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Request body is not valid JSON"}, status=400)

    authorization_header = request.headers.get("Authorization", "")
    response_body, status_code = routing.validate_and_ack_search(
        payload=payload, authorization_header=authorization_header, body=request.body
    )
    if status_code == 200:
        routing.dispatch_search_in_background(
            payload=payload, authorization_header=authorization_header
        )
    return JsonResponse(response_body, status=status_code)


@csrf_exempt
@require_http_methods(["POST"])
def on_search_view(request):
    """Receives a BPP's /on_search callback and relays it on to the originating BAP
    (livetracker2.md Phase 3.1) — Gateway is on the critical path for both directions
    of the search/on_search pair (protocol_compliance_notes_v1.1.md §H.4), not just
    routing the initial /search."""
    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Request body is not valid JSON"}, status=400)

    authorization_header = request.headers.get("Authorization", "")
    response_body, status_code = routing.validate_and_ack_on_search(
        payload=payload, authorization_header=authorization_header, body=request.body
    )
    if status_code == 200:
        routing.relay_on_search_in_background(
            payload=payload, authorization_header=authorization_header
        )
    return JsonResponse(response_body, status=status_code)


@csrf_exempt
@require_http_methods(["POST"])
def select_view(request):
    """Real /select business logic (livetracker2.md Phase 3.2) — validates and ACKs
    the calling BAP synchronously, then forwards to the one specific, already-known
    BPP the customer chose from search results (after a fresh SUBSCRIBED re-check —
    see core/routing.py's dispatch_select docstring) in the background."""
    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Request body is not valid JSON"}, status=400)

    authorization_header = request.headers.get("Authorization", "")
    response_body, status_code = routing.validate_and_ack_select(
        payload=payload, authorization_header=authorization_header, body=request.body
    )
    if status_code == 200:
        routing.dispatch_select_in_background(
            payload=payload, authorization_header=authorization_header
        )
    return JsonResponse(response_body, status=status_code)


@csrf_exempt
@require_http_methods(["POST"])
def on_select_view(request):
    """Receives a BPP's /on_select callback and relays it on to the originating BAP
    (livetracker2.md Phase 3.2) — same routes-back-through-Gateway pattern as
    on_search."""
    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Request body is not valid JSON"}, status=400)

    authorization_header = request.headers.get("Authorization", "")
    response_body, status_code = routing.validate_and_ack_on_select(
        payload=payload, authorization_header=authorization_header, body=request.body
    )
    if status_code == 200:
        routing.relay_on_select_in_background(
            payload=payload, authorization_header=authorization_header
        )
    return JsonResponse(response_body, status=status_code)


@csrf_exempt
@require_http_methods(["POST"])
def init_view(request):
    """Real /init business logic (livetracker2.md Phase 3.3) — validates and ACKs the
    calling BAP synchronously, then forwards to the one specific, already-known BPP
    (after a fresh SUBSCRIBED re-check — see core/routing.py's dispatch_init
    docstring) in the background."""
    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Request body is not valid JSON"}, status=400)

    authorization_header = request.headers.get("Authorization", "")
    response_body, status_code = routing.validate_and_ack_init(
        payload=payload, authorization_header=authorization_header, body=request.body
    )
    if status_code == 200:
        routing.dispatch_init_in_background(
            payload=payload, authorization_header=authorization_header
        )
    return JsonResponse(response_body, status=status_code)


@csrf_exempt
@require_http_methods(["POST"])
def on_init_view(request):
    """Receives a BPP's /on_init callback and relays it on to the originating BAP
    (livetracker2.md Phase 3.3) — same routes-back-through-Gateway pattern as
    on_select."""
    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Request body is not valid JSON"}, status=400)

    authorization_header = request.headers.get("Authorization", "")
    response_body, status_code = routing.validate_and_ack_on_init(
        payload=payload, authorization_header=authorization_header, body=request.body
    )
    if status_code == 200:
        routing.relay_on_init_in_background(
            payload=payload, authorization_header=authorization_header
        )
    return JsonResponse(response_body, status=status_code)


@csrf_exempt
@require_http_methods(["POST"])
def confirm_view(request):
    """Real /confirm business logic (livetracker2.md Phase 3.4) — validates and ACKs
    the calling BAP synchronously, then forwards to the one specific, already-known
    BPP (after a fresh SUBSCRIBED re-check — see core/routing.py's dispatch_confirm
    docstring) in the background."""
    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Request body is not valid JSON"}, status=400)

    authorization_header = request.headers.get("Authorization", "")
    response_body, status_code = routing.validate_and_ack_confirm(
        payload=payload, authorization_header=authorization_header, body=request.body
    )
    if status_code == 200:
        routing.dispatch_confirm_in_background(
            payload=payload, authorization_header=authorization_header
        )
    return JsonResponse(response_body, status=status_code)


@csrf_exempt
@require_http_methods(["POST"])
def on_confirm_view(request):
    """Receives a BPP's /on_confirm callback and relays it on to the originating
    BAP (livetracker2.md Phase 3.4) — same routes-back-through-Gateway pattern as
    on_init."""
    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Request body is not valid JSON"}, status=400)

    authorization_header = request.headers.get("Authorization", "")
    response_body, status_code = routing.validate_and_ack_on_confirm(
        payload=payload, authorization_header=authorization_header, body=request.body
    )
    if status_code == 200:
        routing.relay_on_confirm_in_background(
            payload=payload, authorization_header=authorization_header
        )
    return JsonResponse(response_body, status=status_code)


@csrf_exempt
@require_http_methods(["POST"])
def status_view(request):
    """Real /status business logic (livetracker2.md Phase 3.5) — validates and
    ACKs the calling BAP synchronously, then forwards to the one specific,
    already-known BPP (after a fresh SUBSCRIBED re-check) in the background."""
    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Request body is not valid JSON"}, status=400)

    authorization_header = request.headers.get("Authorization", "")
    response_body, status_code = routing.validate_and_ack_status(
        payload=payload, authorization_header=authorization_header, body=request.body
    )
    if status_code == 200:
        routing.dispatch_status_in_background(
            payload=payload, authorization_header=authorization_header
        )
    return JsonResponse(response_body, status=status_code)


@csrf_exempt
@require_http_methods(["POST"])
def on_status_view(request):
    """Receives a BPP's /on_status callback and relays it on to the originating
    BAP (livetracker2.md Phase 3.5) — same routes-back-through-Gateway pattern
    as on_confirm."""
    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Request body is not valid JSON"}, status=400)

    authorization_header = request.headers.get("Authorization", "")
    response_body, status_code = routing.validate_and_ack_on_status(
        payload=payload, authorization_header=authorization_header, body=request.body
    )
    if status_code == 200:
        routing.relay_on_status_in_background(
            payload=payload, authorization_header=authorization_header
        )
    return JsonResponse(response_body, status=status_code)


@csrf_exempt
@require_http_methods(["POST"])
def cancel_view(request):
    """Real /cancel business logic (livetracker2.md Phase 3.5) — validates and
    ACKs the calling BAP synchronously, then forwards to the one specific,
    already-known BPP (after a fresh SUBSCRIBED re-check) in the background."""
    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Request body is not valid JSON"}, status=400)

    authorization_header = request.headers.get("Authorization", "")
    response_body, status_code = routing.validate_and_ack_cancel(
        payload=payload, authorization_header=authorization_header, body=request.body
    )
    if status_code == 200:
        routing.dispatch_cancel_in_background(
            payload=payload, authorization_header=authorization_header
        )
    return JsonResponse(response_body, status=status_code)


@csrf_exempt
@require_http_methods(["POST"])
def on_cancel_view(request):
    """Receives a BPP's /on_cancel callback and relays it on to the originating
    BAP (livetracker2.md Phase 3.5) — same routes-back-through-Gateway pattern
    as on_confirm."""
    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Request body is not valid JSON"}, status=400)

    authorization_header = request.headers.get("Authorization", "")
    response_body, status_code = routing.validate_and_ack_on_cancel(
        payload=payload, authorization_header=authorization_header, body=request.body
    )
    if status_code == 200:
        routing.relay_on_cancel_in_background(
            payload=payload, authorization_header=authorization_header
        )
    return JsonResponse(response_body, status=status_code)


@csrf_exempt
@require_http_methods(["POST"])
def update_view(request):
    """Real /update business logic (livetracker2.md Phase 3.5) — validates and
    ACKs the calling BAP synchronously, then forwards to the one specific,
    already-known BPP (after a fresh SUBSCRIBED re-check) in the background."""
    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Request body is not valid JSON"}, status=400)

    authorization_header = request.headers.get("Authorization", "")
    response_body, status_code = routing.validate_and_ack_update(
        payload=payload, authorization_header=authorization_header, body=request.body
    )
    if status_code == 200:
        routing.dispatch_update_in_background(
            payload=payload, authorization_header=authorization_header
        )
    return JsonResponse(response_body, status=status_code)


@csrf_exempt
@require_http_methods(["POST"])
def on_update_view(request):
    """Receives a BPP's /on_update callback and relays it on to the originating
    BAP (livetracker2.md Phase 3.5) — same routes-back-through-Gateway pattern
    as on_confirm."""
    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Request body is not valid JSON"}, status=400)

    authorization_header = request.headers.get("Authorization", "")
    response_body, status_code = routing.validate_and_ack_on_update(
        payload=payload, authorization_header=authorization_header, body=request.body
    )
    if status_code == 200:
        routing.relay_on_update_in_background(
            payload=payload, authorization_header=authorization_header
        )
    return JsonResponse(response_body, status=status_code)


@csrf_exempt
@require_http_methods(["POST"])
def track_view(request):
    """Real /track business logic (livetracker2.md Phase 3.5) — validates and
    ACKs the calling BAP synchronously, then forwards to the one specific,
    already-known BPP (after a fresh SUBSCRIBED re-check) in the background."""
    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Request body is not valid JSON"}, status=400)

    authorization_header = request.headers.get("Authorization", "")
    response_body, status_code = routing.validate_and_ack_track(
        payload=payload, authorization_header=authorization_header, body=request.body
    )
    if status_code == 200:
        routing.dispatch_track_in_background(
            payload=payload, authorization_header=authorization_header
        )
    return JsonResponse(response_body, status=status_code)


@csrf_exempt
@require_http_methods(["POST"])
def on_track_view(request):
    """Receives a BPP's /on_track callback and relays it on to the originating
    BAP (livetracker2.md Phase 3.5) — same routes-back-through-Gateway pattern
    as on_confirm."""
    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Request body is not valid JSON"}, status=400)

    authorization_header = request.headers.get("Authorization", "")
    response_body, status_code = routing.validate_and_ack_on_track(
        payload=payload, authorization_header=authorization_header, body=request.body
    )
    if status_code == 200:
        routing.relay_on_track_in_background(
            payload=payload, authorization_header=authorization_header
        )
    return JsonResponse(response_body, status=status_code)
