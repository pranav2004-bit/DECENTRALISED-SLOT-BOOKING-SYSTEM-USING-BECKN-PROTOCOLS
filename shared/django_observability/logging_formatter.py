"""JSON logging formatter matching the shape defined in OBSERVABILITY.md and proven
runnable in shared/observability/logging_reference.py. Used via LOGGING['formatters']
in each Django project's settings.py.
"""

import json
import logging
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    def __init__(self, service_name: str):
        super().__init__()
        self.service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "service": self.service_name,
            "level": record.levelname,
            "correlation_id": getattr(record, "correlation_id", None),
            "message": record.getMessage(),
        }
        extra_fields = getattr(record, "extra_fields", None)
        if extra_fields:
            payload.update(extra_fields)
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload)
