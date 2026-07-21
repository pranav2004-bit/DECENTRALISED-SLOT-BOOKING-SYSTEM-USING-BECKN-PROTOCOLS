"""Idempotency-Key support (API_CONVENTIONS.md: "server stores key->response for 24h,
replays on repeat"). Built for the browser-facing "confirm" trigger endpoint
(livetracker2.md §3.6): a flaky mobile connection causing the browser to retry the
confirm POST must not create two real bookings at the web layer — a real gap the
event-level idempotency in shared/inventory_core (§1.4) doesn't cover, since that
dedupes internal events by event_id, not web-layer retries of the original trigger. A
thin, reusable decorator, not a one-off hack, so any other web-facing mutating endpoint
can adopt it the same way later.
"""

import hashlib
import json
import logging
from functools import wraps

from django.core.cache import cache
from django.http import JsonResponse

from .errors import _RETRYABLE_STATUSES, error_response

logger = logging.getLogger("django_observability")

IDEMPOTENCY_HEADER = "Idempotency-Key"
DEFAULT_TIMEOUT_SECONDS = 24 * 60 * 60
_LOCK_TIMEOUT_SECONDS = 30


def idempotent_view(timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS):
    """Requests without the header pass through untouched — the header is opt-in per
    API_CONVENTIONS.md's own framing, not mandatory. A cached response is replayed
    verbatim without re-running the view at all, so the real underlying action (e.g.
    the Gateway /confirm call) never fires a second time for the same key. A short-
    lived Redis lock (`cache.add`, atomic SET-NX under django-redis) rejects a second
    request for the same key that arrives while the first is still in flight, rather
    than letting both run concurrently and race.

    Responses classified `retryable` (§3.6's own retryable/non-retryable
    classification — 502/503/504/408/429, `errors.error_response`) are deliberately
    NOT cached: `retryable: true` means "this exact request is safe and expected to
    eventually succeed if retried", so a genuine retry must actually re-attempt the
    call, not be stuck replaying a stale transient failure for up to 24h.
    """

    def decorator(view_func):
        @wraps(view_func)
        def wrapped(request, *args, **kwargs):
            key = request.headers.get(IDEMPOTENCY_HEADER)
            if not key:
                return view_func(request, *args, **kwargs)

            cache_key = _cache_key(request, key)
            lock_key = f"{cache_key}:lock"

            cached = cache.get(cache_key)
            if cached is not None:
                return JsonResponse(cached["body"], status=cached["status"])

            if not cache.add(lock_key, "1", timeout=_LOCK_TIMEOUT_SECONDS):
                return error_response(
                    "IDEMPOTENCY_KEY_IN_PROGRESS",
                    "A request with this Idempotency-Key is already being processed",
                    409,
                    retryable=True,
                )

            try:
                response = view_func(request, *args, **kwargs)
            finally:
                cache.delete(lock_key)

            _maybe_cache_response(cache_key, response, timeout_seconds)
            return response

        return wrapped

    return decorator


def _maybe_cache_response(cache_key: str, response, timeout_seconds: int) -> None:
    if not isinstance(response, JsonResponse) or response.status_code in _RETRYABLE_STATUSES:
        return
    try:
        body = json.loads(response.content)
    except (ValueError, TypeError):
        logger.warning("idempotent_view: response body was not valid JSON, not caching")
        return
    cache.set(cache_key, {"body": body, "status": response.status_code}, timeout=timeout_seconds)


def _cache_key(request, key: str) -> str:
    """Scoped to the exact view path plus a hash of the request body, not just the
    raw key — the same Idempotency-Key value reused for a genuinely different request
    body (a real client bug, not a retry) must not silently replay the wrong cached
    response."""
    body_hash = hashlib.sha256(request.body or b"").hexdigest()
    return f"idempotency:{request.path}:{key}:{body_hash}"
