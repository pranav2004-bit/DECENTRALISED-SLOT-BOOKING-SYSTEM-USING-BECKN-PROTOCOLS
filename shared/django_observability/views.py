"""Health, readiness, and metrics endpoints per OBSERVABILITY.md contract.

Readiness checks are pluggable per-project via settings.OBSERVABILITY_READINESS_CHECKS,
a list of dotted import paths to zero-arg callables returning True/False. This keeps
this shared app dependency-agnostic — Registry/BAP/BPP check DB (+Redis for BAP/BPP),
Gateway checks neither, per its statelessness (beckn_gateway_details_v1.1.md §4).
"""

import time

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.utils.module_loading import import_string


def health_view(request):
    """Liveness — process is up. No dependency checks, must stay fast and never
    fail due to a downstream outage (that's a /ready concern, not /health)."""
    return JsonResponse({"status": "ok", "service": getattr(settings, "SERVICE_NAME", "unknown")})


def ready_view(request):
    """Readiness — all hard dependencies reachable, or 503 naming what failed."""
    checks = {}
    all_ok = True
    for check_path in getattr(settings, "OBSERVABILITY_READINESS_CHECKS", []):
        name, fn_path = check_path
        try:
            fn = import_string(fn_path)
            ok = bool(fn())
        except Exception:
            ok = False
        checks[name] = "ok" if ok else "unreachable"
        all_ok = all_ok and ok

    body = {
        "status": "ok" if all_ok else "unavailable",
        "service": getattr(settings, "SERVICE_NAME", "unknown"),
        "checks": checks,
    }
    return JsonResponse(body, status=200 if all_ok else 503)


_START_TIME = time.time()


def metrics_view(request):
    """Prometheus text-exposition format. Real per-route request/latency counters get
    wired in as each project adds real endpoints in Phase 2+ — this establishes the
    contract and a real, scrapeable metric now (uptime), not a stub returning nothing.

    Project-specific metrics plug in via settings.EXTRA_METRICS_PROVIDERS, a list of
    dotted paths to zero-arg callables returning a list of Prometheus-format lines —
    keeps this shared app free of any one project's route names (e.g. Registry's
    subscribe/lookup counters, per Phase 2.6), while still emitting them all here."""
    uptime_seconds = time.time() - _START_TIME
    service = getattr(settings, "SERVICE_NAME", "unknown")
    lines = [
        "# HELP app_uptime_seconds Seconds since process start",
        "# TYPE app_uptime_seconds counter",
        f'app_uptime_seconds{{service="{service}"}} {uptime_seconds:.3f}',
    ]
    for provider_path in getattr(settings, "EXTRA_METRICS_PROVIDERS", []):
        try:
            provider = import_string(provider_path)
            lines.extend(provider())
        except Exception:
            continue
    lines.append("")
    return HttpResponse("\n".join(lines), content_type="text/plain; version=0.0.4")
