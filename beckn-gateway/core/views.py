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
    """Phase 4.1 trust-chain plumbing endpoint — see core/routing.py's module docstring.
    Verifies the caller's signature and returns which SUBSCRIBED BPPs Gateway would
    route to; does not forward the request or implement /on_search."""
    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Request body is not valid JSON"}, status=400)

    try:
        result = routing.route_search(
            payload=payload,
            authorization_header=request.headers.get("Authorization", ""),
            body=request.body,
        )
    except routing.RoutingError as exc:
        return JsonResponse({"error": exc.message}, status=exc.status_code)

    return JsonResponse(result, status=200)
