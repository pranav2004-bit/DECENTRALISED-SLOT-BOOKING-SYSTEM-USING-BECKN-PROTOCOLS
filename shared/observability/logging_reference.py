"""Reference implementation of the structured JSON logging shape defined in OBSERVABILITY.md.

Not a shared library imported by the four apps (each Django app configures its own logging
via LOGGING in settings.py in Phase 1) — this is a runnable, minimal example proving the
documented shape is achievable, and a template each app's real config should match.

Run directly to see sample output: python shared/observability/logging_reference.py
"""

import json
import logging
import sys
import uuid
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    def __init__(self, service_name: str):
        super().__init__()
        self.service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "service": self.service_name,
            "level": record.levelname,
            "correlation_id": getattr(record, "correlation_id", None),
            "message": record.getMessage(),
        }
        extras = getattr(record, "extra_fields", None)
        if extras:
            payload.update(extras)
        return json.dumps(payload)


def get_logger(service_name: str) -> logging.Logger:
    logger = logging.getLogger(service_name)
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service_name))
    logger.handlers = [handler]
    logger.propagate = False
    return logger


if __name__ == "__main__":
    logger = get_logger("registry")
    correlation_id = str(uuid.uuid4())

    logger.info("Service starting up", extra={"correlation_id": None})
    logger.info(
        "Subscribe request received",
        extra={"correlation_id": correlation_id, "extra_fields": {"subscriber_id": "example.bpp.test"}},
    )
    logger.error(
        "Subscribe request failed validation",
        extra={"correlation_id": correlation_id, "extra_fields": {"reason": "missing signing_public_key"}},
    )
