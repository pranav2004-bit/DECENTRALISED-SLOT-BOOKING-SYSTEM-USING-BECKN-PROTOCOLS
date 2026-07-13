"""Reusable readiness-check functions, referenced by settings.OBSERVABILITY_READINESS_CHECKS."""

from django.db import connections
from django.db.utils import OperationalError


def database_check() -> bool:
    try:
        conn = connections["default"]
        conn.cursor()
        return True
    except OperationalError:
        return False


def cache_check() -> bool:
    from django.core.cache import cache

    try:
        cache.set("_readiness_probe", "1", timeout=5)
        return cache.get("_readiness_probe") == "1"
    except Exception:
        return False
