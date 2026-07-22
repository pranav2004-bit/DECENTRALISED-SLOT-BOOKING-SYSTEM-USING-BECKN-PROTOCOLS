"""Shared pytest fixtures for BPP's core app tests."""

import pytest
from django.core.cache import cache


@pytest.fixture(autouse=True)
def _clear_rate_limit_cache():
    """§3.7's rate limiting (business-signup/business-login/resource-create/
    resource-availability-create) is real and Redis-backed — the same shared
    cache across this whole test session, not reset by Django's own per-test DB
    rollback. Without this, tests calling any rate-limited endpoint repeatedly
    (test_business_account.py alone calls business-signup/login 16 times) would
    eventually start failing with a real 429 unrelated to what's under test.
    Autouse + conftest-level so every test file gets this for free."""
    cache.clear()
    yield
    cache.clear()
