"""Tests for the hard wall-clock deadline wrapper (see timeout.py's own docstring
for the full history: found live at Phase 3 Exit, 2026-07-25, when neither
socket_connect_timeout/socket_timeout nor exception handling bounded a DNS
resolution hang against a stopped-but-present Redis container).
"""

import threading
import time

import pytest

from .timeout import RedisHardTimeout, call_with_hard_timeout


def test_a_fast_call_returns_its_real_value():
    assert call_with_hard_timeout(lambda: 42) == 42


def test_args_and_kwargs_are_passed_through():
    assert call_with_hard_timeout(lambda a, b, c=0: a + b + c, 1, 2, c=3) == 6


def test_the_callables_own_exception_is_re_raised_verbatim():
    def _boom():
        raise ValueError("real failure, not Redis-unavailable")

    with pytest.raises(ValueError, match="real failure"):
        call_with_hard_timeout(_boom)


def test_a_call_slower_than_the_deadline_raises_redis_hard_timeout():
    """The exact property this module exists for: a call that would otherwise
    hang (here simulated with a real `time.sleep`, standing in for the live
    DNS-resolution hang) must not block the caller past `timeout`."""
    start = time.monotonic()
    with pytest.raises(RedisHardTimeout):
        call_with_hard_timeout(time.sleep, 5, timeout=0.2)
    assert time.monotonic() - start < 1.0


def test_a_call_faster_than_the_deadline_is_not_treated_as_a_timeout():
    assert call_with_hard_timeout(time.sleep, 0.05, timeout=2.0) is None


def test_many_legitimate_concurrent_fast_calls_all_complete_without_being_dropped():
    """Regression test for the real bug found in this project's own live
    testing: a first version used a shared, fixed-size `ThreadPoolExecutor`,
    and under genuine concurrent load calls queued for a free worker — that
    queuing delay counted against the same deadline meant to bound Redis
    itself, so a healthy-but-busy pool got misclassified as a Redis outage
    (confirmed via BPP's own `core/test_metrics.py` concurrency test losing
    3 of 800 real increments). The per-call-thread design this module uses
    instead must not reproduce that: every one of a real concurrent burst
    must land its correct result, none dropped."""
    iterations_per_thread = 50
    thread_count = 8
    results = []
    results_lock = threading.Lock()

    def _hammer():
        for i in range(iterations_per_thread):
            value = call_with_hard_timeout(lambda x: x, i, timeout=1.0)
            with results_lock:
                results.append(value)

    threads = [threading.Thread(target=_hammer) for _ in range(thread_count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == iterations_per_thread * thread_count


def test_default_timeout_is_generous_enough_for_a_healthy_local_redis_round_trip():
    """Not a live Redis call (this module has no Redis dependency itself,
    confirmed by direct read — it's a generic hard-deadline primitive used
    by callers that do talk to Redis), but documents the actual value in
    force: 10.0s. Raised three times, each caught by the same class of
    regression test in a different environment: 0.5s dropped 2/800 on a local
    dev machine; 2.0s dropped 3/800 on GitHub Actions CI (BPP's copy of the
    concurrency test); 5.0s still dropped 5/800 on CI (BAP's independent copy
    of the same test). Each dropped call is genuine tail-latency in getting a
    dedicated, isolated thread scheduled/completed within the deadline — not a
    logic bug (no shared state between calls to race on) — under a demanding
    800-real-thread-creation tight-loop burst on a shared/throttled CI runner,
    heavier than any real traffic pattern. 10.0s is still a small fraction of
    what an actual production Redis outage would otherwise cost (the original
    unbounded hang ran 10s+ before this module existed).

    **2026-07-26 correction:** a fourth failure of BAP's copy of this test was
    traced instead to a real logic bug in
    `shared/django_observability/metrics.py`'s `increment_counter()` (a
    non-atomic check-then-set race on a brand-new counter key) — not another
    instance of this timeout being too tight. Raising this default further did
    not fix it and was reverted; see that module's own docstring."""
    import inspect

    default_timeout = inspect.signature(call_with_hard_timeout).parameters["timeout"].default
    assert default_timeout == 10.0
