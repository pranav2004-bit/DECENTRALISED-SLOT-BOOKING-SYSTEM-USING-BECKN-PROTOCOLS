"""A minimal, real (not stubbed) circuit breaker — classic 3-state design
(CLOSED → OPEN → HALF_OPEN → CLOSED), thread-safe. Used by ResilientHttpClient
to stop hammering a downstream that's already failing, per livetracker1.md's
resilience requirements (Phase 1.2/1.3/1.4 HTTP Client Service).
"""

import threading
import time


class CircuitOpenError(Exception):
    """Raised when a call is attempted while the circuit is open (failing fast,
    not even trying the downstream call)."""


class CircuitBreaker:
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

    def __init__(self, *, failure_threshold: int = 5, reset_timeout_seconds: float = 30.0):
        self.failure_threshold = failure_threshold
        self.reset_timeout_seconds = reset_timeout_seconds
        self._state = self.CLOSED
        self._failure_count = 0
        self._opened_at: float | None = None
        self._lock = threading.Lock()

    @property
    def state(self) -> str:
        with self._lock:
            self._maybe_transition_to_half_open()
            return self._state

    def _maybe_transition_to_half_open(self) -> None:
        if self._state == self.OPEN and self._opened_at is not None:
            if time.monotonic() - self._opened_at >= self.reset_timeout_seconds:
                self._state = self.HALF_OPEN

    def before_call(self) -> None:
        """Raises CircuitOpenError if the circuit is open and not yet ready to retry."""
        with self._lock:
            self._maybe_transition_to_half_open()
            if self._state == self.OPEN:
                raise CircuitOpenError(
                    f"Circuit is open — failing fast without attempting the call "
                    f"(will retry after {self.reset_timeout_seconds}s cooldown)"
                )

    def record_success(self) -> None:
        with self._lock:
            self._failure_count = 0
            self._state = self.CLOSED
            self._opened_at = None

    def record_failure(self) -> None:
        with self._lock:
            self._failure_count += 1
            if self._state == self.HALF_OPEN or self._failure_count >= self.failure_threshold:
                self._state = self.OPEN
                self._opened_at = time.monotonic()


class RedisCircuitBreaker:
    """Same CLOSED -> OPEN -> HALF_OPEN -> CLOSED semantics as CircuitBreaker, but
    state lives in Redis instead of process memory — same public interface
    (state, before_call, record_success, record_failure), so ResilientHttpClient
    doesn't need to know which one it holds.

    Fixes a real limitation found in Phase 4.2 (livetracker1.md): gunicorn runs
    multiple worker processes, each with its own independent in-memory
    CircuitBreaker. A downstream that was genuinely down never tripped the breaker
    network-wide — every worker had to independently accumulate its own
    failure_threshold failures, and even then only that one worker failed fast
    while the others kept retrying into the full timeout. Sharing state via Redis
    means one worker's failures count toward every worker's decision.
    """

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

    def __init__(
        self,
        *,
        redis_client,
        key_prefix: str,
        failure_threshold: int = 5,
        reset_timeout_seconds: float = 30.0,
    ):
        self._redis = redis_client
        self._failures_key = f"{key_prefix}:cb:failures"
        self._opened_at_key = f"{key_prefix}:cb:opened_at"
        self.failure_threshold = failure_threshold
        self.reset_timeout_seconds = reset_timeout_seconds
        # Keys shouldn't outlive many idle reset windows if a process dies mid-open.
        self._key_ttl_seconds = max(int(reset_timeout_seconds * 10), 60)

    @property
    def state(self) -> str:
        opened_at = self._redis.get(self._opened_at_key)
        if opened_at is None:
            return self.CLOSED
        if time.time() - float(opened_at) >= self.reset_timeout_seconds:
            return self.HALF_OPEN
        return self.OPEN

    def before_call(self) -> None:
        if self.state == self.OPEN:
            raise CircuitOpenError(
                f"Circuit is open — failing fast without attempting the call "
                f"(will retry after {self.reset_timeout_seconds}s cooldown)"
            )

    def record_success(self) -> None:
        self._redis.delete(self._failures_key, self._opened_at_key)

    def record_failure(self) -> None:
        current_state = self.state
        count = self._redis.incr(self._failures_key)
        self._redis.expire(self._failures_key, self._key_ttl_seconds)
        if current_state == self.HALF_OPEN or count >= self.failure_threshold:
            self._redis.set(self._opened_at_key, time.time(), ex=self._key_ttl_seconds)
