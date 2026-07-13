"""Resilient HTTP client: timeout + retry-with-backoff + circuit-breaker defaults,
per livetracker1.md Phase 1.2/1.3/1.4 "HTTP Client Service" requirement. One shared,
real implementation — not duplicated per app.
"""

import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

from .circuit_breaker import CircuitBreaker, CircuitOpenError

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
    ):
        self.timeout_seconds = timeout_seconds
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
