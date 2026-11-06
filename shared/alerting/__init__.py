"""Audit finding closed live 2026-11-06: payment-critical failures (a refund that
raised, a webhook that NACKed, a vendor adapter missing) were only ever written to
the container's own stdout/log stream — nobody is paged. `logger.exception`/
`logger.error` calls at those sites now also call `send_alert()`, a thin, best-
effort webhook ping (Slack incoming-webhook shape: `{"text": "..."}"`) that never
raises and never blocks the caller on its own failure — same fail-open posture as
`shared/resilient_http`'s circuit breaker and `RedisCircuitBreaker`: an alert being
undeliverable must never become a second reason the original request fails.

`ALERT_WEBHOOK_URL` is optional. Unset (the honest default for local/dev and for
any environment that hasn't configured one yet) makes `send_alert()` a no-op —
this always stays a *supplement* to the real log line already written by the
caller's own `logger.exception`/`logger.error`, never a replacement for it."""

import logging

import requests
from django.conf import settings

logger = logging.getLogger("alerting")

_TIMEOUT_SECONDS = 3.0


def send_alert(summary: str, **context) -> None:
    """Fires a best-effort webhook notification. Safe to call unconditionally from
    any exception handler — swallows every failure (missing/unreachable webhook,
    bad URL, timeout) rather than let an alert-delivery problem compound the
    original error it's trying to report."""
    webhook_url = getattr(settings, "ALERT_WEBHOOK_URL", "")
    if not webhook_url:
        return

    lines = [f"🚨 {summary}"]
    lines.extend(f"{key}: {value!r}" for key, value in context.items())
    try:
        requests.post(webhook_url, json={"text": "\n".join(lines)}, timeout=_TIMEOUT_SECONDS)
    except Exception:
        logger.warning("send_alert: webhook delivery failed for %r", summary, exc_info=True)
