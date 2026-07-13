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
