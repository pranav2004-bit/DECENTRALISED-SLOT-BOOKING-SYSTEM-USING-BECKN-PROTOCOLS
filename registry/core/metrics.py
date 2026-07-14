"""Registry-specific metrics (Phase 2.6), per livetracker1.md: "subscribe/lookup/verify
rates, latency, error rates". In-process counters — same documented per-worker
limitation as rate_limit.py; a real Prometheus scrape target aggregates across workers
regardless (each worker's /metrics is scraped independently in a typical setup), so
this is a real, useful metric now, not a placeholder, even with that caveat.
"""

import threading
import time
from collections import defaultdict

_lock = threading.Lock()
_counters: dict[str, int] = defaultdict(int)
_latency_sum: dict[str, float] = defaultdict(float)
_latency_count: dict[str, int] = defaultdict(int)


def increment(counter_name: str, amount: int = 1) -> None:
    with _lock:
        _counters[counter_name] += amount


def observe_latency(metric_name: str, seconds: float) -> None:
    with _lock:
        _latency_sum[metric_name] += seconds
        _latency_count[metric_name] += 1


class timed:
    """Context manager: `with timed("subscribe"): ...` records request latency."""

    def __init__(self, metric_name: str):
        self.metric_name = metric_name

    def __enter__(self):
        self._start = time.monotonic()
        return self

    def __exit__(self, *exc_info):
        observe_latency(self.metric_name, time.monotonic() - self._start)


def render_metrics() -> list[str]:
    """Called by shared.django_observability.views.metrics_view via
    settings.EXTRA_METRICS_PROVIDERS."""
    with _lock:
        counters = dict(_counters)
        latency_sum = dict(_latency_sum)
        latency_count = dict(_latency_count)

    lines = [
        "# HELP registry_requests_total Total requests per endpoint/outcome",
        "# TYPE registry_requests_total counter",
    ]
    for name, value in sorted(counters.items()):
        lines.append(f'registry_requests_total{{metric="{name}"}} {value}')

    lines += [
        "# HELP registry_request_latency_seconds_sum Sum of observed request latencies",
        "# TYPE registry_request_latency_seconds_sum counter",
    ]
    for name, value in sorted(latency_sum.items()):
        lines.append(f'registry_request_latency_seconds_sum{{metric="{name}"}} {value:.6f}')

    lines += [
        "# HELP registry_request_latency_seconds_count Count of observed request latencies",
        "# TYPE registry_request_latency_seconds_count counter",
    ]
    for name, value in sorted(latency_count.items()):
        lines.append(f'registry_request_latency_seconds_count{{metric="{name}"}} {value}')

    return lines
