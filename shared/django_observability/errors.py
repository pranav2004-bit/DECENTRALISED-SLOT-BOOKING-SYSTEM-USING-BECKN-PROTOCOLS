"""Helper for constructing the standardized error response shape from API_CONVENTIONS.md.
Use this for handled validation/business errors; ExceptionHandlingMiddleware in
middleware.py covers unhandled exceptions automatically using the same shape."""

from django.http import JsonResponse

from .context import correlation_id_var

# HTTP statuses where retrying the same request later has a real chance of succeeding
# without the caller changing anything (§3.6, livetracker2.md) — a temporarily
# unreachable/overloaded downstream, not a problem with the request itself. Every other
# status (4xx business/validation errors, 500) is not retryable by default: retrying an
# invalid request or an unavailable slot changes nothing.
_RETRYABLE_STATUSES = frozenset({408, 429, 502, 503, 504})


def error_response(
    code: str, message: str, status: int, field: str | None = None, retryable: bool | None = None
) -> JsonResponse:
    """`retryable` is auto-classified from `status` when not explicitly overridden —
    callers with a better-informed answer (e.g. a business rule that a specific 400
    is actually safe to retry) can still pass it explicitly."""
    if retryable is None:
        retryable = status in _RETRYABLE_STATUSES
    error = {
        "code": code,
        "message": message,
        "correlation_id": correlation_id_var.get(),
        "retryable": retryable,
    }
    if field:
        error["field"] = field
    return JsonResponse({"error": error}, status=status)
