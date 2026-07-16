"""Resilient HTTP client: timeout + retry-with-backoff + circuit-breaker defaults,
per livetracker1.md Phase 1.2/1.3/1.4 "HTTP Client Service" requirement. One shared,
real implementation — not duplicated per app.
"""

import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

from .circuit_breaker import CircuitBreaker, CircuitOpenError, RedisCircuitBreaker

__all__ = ["ResilientHttpClient", "CircuitOpenError"]


class ResilientHttpClient:
    def __init__(
        self,
        *,
        timeout_seconds: float = 5.0,
        max_retries: int = 3,
        backoff_factor: float = 0.5,
        circuit_breaker_threshold: int = 5,
        circuit_breaker_reset_seconds: float = 30.0,
        redis_client=None,
        circuit_breaker_key: str = "resilient_http",
    ):
        """redis_client (optional): a raw redis-py client. When provided, the
        circuit breaker's state is shared across processes via Redis instead of
        held in this process's memory — needed for correctness under gunicorn's
        multiple workers (see circuit_breaker.RedisCircuitBreaker docstring for
        the real bug this fixes). circuit_breaker_key scopes the shared state to
        this client's target (e.g. "registry") so unrelated clients in the same
        Redis don't share a breaker."""
        self.timeout_seconds = timeout_seconds
        if redis_client is not None:
            self._circuit_breaker = RedisCircuitBreaker(
                redis_client=redis_client,
                key_prefix=circuit_breaker_key,
                failure_threshold=circuit_breaker_threshold,
                reset_timeout_seconds=circuit_breaker_reset_seconds,
            )
        else:
            self._circuit_breaker = CircuitBreaker(
                failure_threshold=circuit_breaker_threshold,
                reset_timeout_seconds=circuit_breaker_reset_seconds,
            )
        self._session = requests.Session()
        retry = Retry(
            total=max_retries,
            backoff_factor=backoff_factor,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["GET", "POST", "PUT", "DELETE"],
        )
        adapter = HTTPAdapter(max_retries=retry)
        self._session.mount("http://", adapter)
        self._session.mount("https://", adapter)

    @property
    def circuit_state(self) -> str:
        return self._circuit_breaker.state

    def request(self, method: str, url: str, **kwargs) -> requests.Response:
        self._circuit_breaker.before_call()
        kwargs.setdefault("timeout", self.timeout_seconds)
        try:
            response = self._session.request(method, url, **kwargs)
        except requests.exceptions.RequestException:
            self._circuit_breaker.record_failure()
            raise
        if response.status_code >= 500:
            self._circuit_breaker.record_failure()
        else:
            self._circuit_breaker.record_success()
        return response

    def get(self, url: str, **kwargs) -> requests.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs) -> requests.Response:
        return self.request("POST", url, **kwargs)
