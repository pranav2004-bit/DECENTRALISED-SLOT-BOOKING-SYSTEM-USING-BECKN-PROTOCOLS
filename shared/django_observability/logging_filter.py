import logging

from .context import correlation_id_var


class CorrelationIdLogFilter(logging.Filter):
    """Injects the current request's correlation_id into every log record automatically,
    so call sites never need to pass extra={"correlation_id": ...} by hand."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "correlation_id"):
            record.correlation_id = correlation_id_var.get()
        return True
