import logging

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django_observability.context import correlation_id_var

from . import metrics, registry_service
from .rate_limit import rate_limit
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
            result = registry_service.handle_subscribe(
                payload, correlation_id=correlation_id_var.get()
            )
        except ValueError as exc:
            metrics.increment("subscribe_errors_total")
            return _json_error("VALIDATION_ERROR", str(exc), 400)

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

        results = registry_service.handle_lookup(filters)
        return JsonResponse(results, safe=False, status=200)
