import logging

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django_observability.context import correlation_id_var

from . import metrics, registry_service
from .rate_limit import rate_limit
from .registry_keys import get_registry_encryption_keys, get_registry_signing_keys
from .validation import PayloadValidationError, validate_against_schema

logger = logging.getLogger("registry")


def _json_error(code: str, message: str, status: int, field: str | None = None) -> JsonResponse:
    error = {"code": code, "message": message, "correlation_id": correlation_id_var.get()}
    if field:
        error["field"] = field
    return JsonResponse({"error": error}, status=status)


@csrf_exempt
@require_http_methods(["POST"])
@rate_limit(limit_per_minute=10, scope="subscribe")
def subscribe_view(request):
    import json

    with metrics.timed("subscribe"):
        metrics.increment("subscribe_requests_total")
        try:
            payload = json.loads(request.body)
        except json.JSONDecodeError:
            metrics.increment("subscribe_errors_total")
            return _json_error("VALIDATION_ERROR", "Request body is not valid JSON", 400)

        try:
            validate_against_schema(payload, "subscribe_request.schema.json")
        except PayloadValidationError as exc:
            metrics.increment("subscribe_errors_total")
            return _json_error("VALIDATION_ERROR", exc.message, 400, field=exc.field)

        try:
            registry_service.verify_subscribe_authorization(
                payload=payload,
                authorization_header=request.headers.get("Authorization", ""),
                body=request.body,
            )
        except registry_service.AuthorizationError as exc:
            metrics.increment("subscribe_errors_total")
            return _json_error("UNAUTHORIZED", str(exc), 401)

        try:
            result = registry_service.handle_subscribe(
                payload, correlation_id=correlation_id_var.get()
            )
        except ValueError as exc:
            metrics.increment("subscribe_errors_total")
            return _json_error("VALIDATION_ERROR", str(exc), 400)
        except registry_service.DomainVerificationError as exc:
            metrics.increment("subscribe_errors_total")
            return _json_error("DOMAIN_VERIFICATION_FAILED", str(exc), 422)

        return JsonResponse(result, status=200)


@csrf_exempt
@require_http_methods(["POST"])
@rate_limit(limit_per_minute=7600, scope="lookup")
def lookup_view(request):
    import json

    with metrics.timed("lookup"):
        metrics.increment("lookup_requests_total")
        try:
            filters = json.loads(request.body) if request.body else {}
        except json.JSONDecodeError:
            metrics.increment("lookup_errors_total")
            return _json_error("VALIDATION_ERROR", "Request body is not valid JSON", 400)

        try:
            registry_service.verify_lookup_authorization(
                authorization_header=request.headers.get("Authorization", ""),
                body=request.body,
            )
        except registry_service.AuthorizationError as exc:
            metrics.increment("lookup_errors_total")
            return _json_error("UNAUTHORIZED", str(exc), 401)

        results = registry_service.handle_lookup(filters)
        return JsonResponse(results, safe=False, status=200)


@require_http_methods(["GET"])
def identity_view(request):
    """Exposes the Registry's own public keys. Real ONDC publishes its registry public
    keys out-of-band (via the Network Participant Portal / onboarding docs), not through
    Subscribe/Lookup — those APIs are for participants, not the registry itself. Our
    network needs *some* real mechanism for participants to fetch the registry's
    encryption public key to decrypt on_subscribe challenges, so this endpoint fills that
    role for this implementation (protocol_compliance_notes_v1.1.md §A.5)."""
    signing_pub, _ = get_registry_signing_keys()
    encryption_pub, _ = get_registry_encryption_keys()
    return JsonResponse(
        {"signing_public_key": signing_pub, "encryption_public_key": encryption_pub}
    )
