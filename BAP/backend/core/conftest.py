"""Shared pytest fixtures for BAP's core app tests."""

import pytest
from django.core.cache import cache


@pytest.fixture(autouse=True)
def _clear_rate_limit_cache():
    """§3.7's rate limiting (search/select/init/confirm/status/cancel/update/track,
    signup/login) and the reservation-hold abuse cap are real and Redis-backed —
    the same shared cache across this whole test session, not reset by Django's
    own per-test DB rollback. Without this, tests calling any rate-limited
    trigger endpoint repeatedly (several files already do, within the same
    60-second window) would eventually start failing with a real 429 that has
    nothing to do with the thing actually under test. Autouse + conftest-level
    so every test file gets this for free, including ones written later."""
    cache.clear()
    yield
    cache.clear()
