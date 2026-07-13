"""Helper for constructing the standardized error response shape from API_CONVENTIONS.md.
Use this for handled validation/business errors; ExceptionHandlingMiddleware in
middleware.py covers unhandled exceptions automatically using the same shape."""

from django.http import JsonResponse

from .context import correlation_id_var


def error_response(code: str, message: str, status: int, field: str | None = None) -> JsonResponse:
    error = {
        "code": code,
        "message": message,
        "correlation_id": correlation_id_var.get(),
    }
    if field:
        error["field"] = field
    return JsonResponse({"error": error}, status=status)
