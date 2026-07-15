import json

from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

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
