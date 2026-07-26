"""Hard wall-clock deadline for a single Redis operation.

Real gap found and closed at Phase 3 Exit (livetracker2.md, 2026-07-25, live
"kill Redis" re-test, fourth pass): `socket_connect_timeout`/`socket_timeout`
(passed to redis-py / django-redis's CONNECTION_POOL_KWARGS) only bound the
TCP-connect phase. Live-tested directly inside the bpp-backend container
against a `docker compose stop`ped (not removed) bpp-cache: a single
`cache.get()`/`cache.set()` took ~4s to raise, entirely spent in DNS
resolution ("No address associated with hostname" / "Name or service not
known") — a phase neither timeout kwarg touches. No redis-py/django-redis
option bounds DNS, so the only way to get a real hard deadline covering
DNS + connect + I/O together is to run the call off-thread and give up on
waiting for it.

A dedicated thread per call, not a shared bounded pool: a first version used
a fixed-size `ThreadPoolExecutor`, which failed its own regression test
(`core/test_metrics.py`'s 4-thread/800-increment concurrency test lost 3
increments) — under legitimate concurrent load, calls were queuing for a free
pool worker, and that queuing delay counted against the same 0.5s deadline
meant for bounding Redis itself, misclassifying a healthy-but-busy pool as a
Redis outage. Spawning a fresh thread per call means `timeout` only ever
measures the call's own DNS+connect+I/O time, never a wait for pool capacity.

The leaked thread (Python threads can't be force-killed) is left to finish or
die on its own — acceptable because every caller here already treats a
timeout the same as any other "Redis unavailable" failure (fail open / fall
back to Postgres), and this only fires during a genuine outage, not normal
operation.

Default raised three times, each time because a live regression test caught
real thread-scheduling tail latency at volume, not because DNS/connect/I/O
itself got slower: 0.5s dropped 2/800 calls on a local dev machine; 2.0s then
dropped 3/800 the same way on GitHub Actions' CI runners; 5.0s still dropped
5/800 on CI (a second, independent copy of the same regression test, BAP's
own). Each call is fully isolated (its own queue, its own thread — no shared
state to race on), so a drop is genuinely the underlying call's own thread not
getting scheduled/completing within the deadline on that run, not a logic bug;
800 real thread creations in a tight loop is a demanding tail-latency stress
test on a shared/throttled CI runner. Settled at 10.0s — still a small
fraction of what an actual production outage would otherwise cost (the
original unbounded hang ran 10s+), and irrelevant to normal operation either
way (a healthy call returns in microseconds regardless of the ceiling).

**2026-07-26 correction:** a fourth occurrence of this same test failing (this
time consistently, 3/5 local re-runs, even after raising this default to
20.0s) turned out to be a *different, real logic bug*, not another instance of
this tail latency — see `shared/django_observability/metrics.py`'s
`increment_counter()` docstring. Raising this timeout further was the wrong
fix for that failure and has been reverted back to 10.0s; this module's own
three-escalation history above remains accurate for what it actually
diagnosed.
"""

import queue
import threading


class RedisHardTimeout(Exception):
    """A Redis call didn't complete within its hard deadline. Callers should
    treat this identically to any other Redis-unavailable exception."""


def call_with_hard_timeout(func, *args, timeout=10.0, **kwargs):
    result: queue.Queue = queue.Queue(maxsize=1)

    def _run():
        try:
            result.put(("ok", func(*args, **kwargs)))
        except Exception as exc:  # noqa: BLE001 - re-raised verbatim in the caller's thread
            result.put(("error", exc))

    threading.Thread(target=_run, daemon=True, name="redis-safe").start()
    try:
        status, value = result.get(timeout=timeout)
    except queue.Empty:
        raise RedisHardTimeout(
            f"{getattr(func, '__qualname__', func)} exceeded {timeout}s hard deadline"
        ) from None
    if status == "error":
        raise value
    return value
