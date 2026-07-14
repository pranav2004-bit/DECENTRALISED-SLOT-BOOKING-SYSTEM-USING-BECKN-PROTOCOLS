"""Rate limiting — real ONDC thresholds (protocol_compliance_notes_v1.1.md §B.6):
Subscribe 10 req/min, Lookup 7,600 req/min. Uses Django's cache framework (LocMemCache
by default) as a fixed-window counter keyed by client IP.

KNOWN LIMITATION, documented not hidden: LocMemCache is per-process, so with multiple
gunicorn workers the effective limit is (configured_limit * worker_count), not exact.
Acceptable at `[MVP]`/`[PILOT]` scale (single worker or low worker count locally);
revisit with a shared Redis-backed cache before `[BETA]` multi-worker production
deployment — this is a real, tracked gap, not an oversight.
"""

from functools import wraps

from django.core.cache import cache
from django.http import JsonResponse
from django_observability.context import correlation_id_var


def _client_key(request) -> str:
    return request.META.get("REMOTE_ADDR", "unknown")


def rate_limit(*, limit_per_minute: int, scope: str):
    def decorator(view_func):
        @wraps(view_func)
        def wrapped(request, *args, **kwargs):
            key = f"ratelimit:{scope}:{_client_key(request)}"
            try:
                count = cache.incr(key)
            except ValueError:
                cache.set(key, 1, timeout=60)
                count = 1
            if count > limit_per_minute:
                return JsonResponse(
                    {
                        "error": {
                            "code": "RATE_LIMITED",
                            "message": f"{scope} rate limit of {limit_per_minute}/min exceeded",
                            "correlation_id": correlation_id_var.get(),
                        }
                    },
                    status=429,
                )
            return view_func(request, *args, **kwargs)

        return wrapped

    return decorator
