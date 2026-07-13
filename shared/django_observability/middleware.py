import logging
import uuid

from django.conf import settings
from django.http import JsonResponse

from .context import correlation_id_var

logger = logging.getLogger("django_observability")

CORRELATION_HEADER = "X-Correlation-Id"


class CorrelationIdMiddleware:
    """Reads X-Correlation-Id from the inbound request, or generates one, per
    OBSERVABILITY.md. Makes it available to logging via a ContextVar and echoes
    it back on the response so callers can correlate."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        incoming = request.headers.get(CORRELATION_HEADER)
        correlation_id = incoming or str(uuid.uuid4())
        token = correlation_id_var.set(correlation_id)
        request.correlation_id = correlation_id
        try:
            response = self.get_response(request)
        finally:
            correlation_id_var.reset(token)
        response[CORRELATION_HEADER] = correlation_id
        return response


class ExceptionHandlingMiddleware:
    """Global exception handler. Maps every unhandled exception to the standardized
    error schema in API_CONVENTIONS.md — never a bare 500 with no body, never a raw
    stack trace leaked to the caller (full traceback still goes to the server log).
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exception):
        correlation_id = getattr(request, "correlation_id", None) or correlation_id_var.get()
        logger.error(
            "Unhandled exception",
            exc_info=exception,
            extra={"correlation_id": correlation_id, "extra_fields": {"path": request.path}},
        )
        message = "Internal server error"
        if getattr(settings, "DEBUG", False):
            message = f"{type(exception).__name__}: {exception}"
        return JsonResponse(
            {
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": message,
                    "correlation_id": correlation_id,
                }
            },
            status=500,
        )
