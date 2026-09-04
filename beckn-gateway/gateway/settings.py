"""Django settings for the Beckn Gateway application. Stateless — no database
(per beckn_gateway_details_v1.1.md §4). Config via django-environ, fail-fast on
missing required vars, same pattern as Registry (registry/registry/settings.py).
"""

import sys
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR.parent / "shared"))

env = environ.Env(
    DJANGO_DEBUG=(bool, False),
)
env_file = BASE_DIR / ".env"
if env_file.exists():
    environ.Env.read_env(str(env_file))

SECRET_KEY = env("DJANGO_SECRET_KEY")

DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])
LOG_LEVEL = env("LOG_LEVEL", default="INFO")

REGISTRY_BASE_URL = env("REGISTRY_BASE_URL")
REGISTRY_LOOKUP_TIMEOUT_MS = env.int("REGISTRY_LOOKUP_TIMEOUT_MS", default=3000)
# livetracker8.md §2.1: required, no default, no opt-out — the Redis-backed circuit
# breaker is now mandatory, matching BAP's/BPP's own unconditional Redis dependency
# instead of Gateway being the one component where a stopped Registry could silently
# degrade to ~19s-per-request fail-slow (the old CACHE_ENABLED=false path, removed).
REDIS_URL = env("REDIS_URL")

# --- Gateway's own network identity (Phase 3.3 onboarding) ---
SUBSCRIBER_ID = env("SUBSCRIBER_ID", default="")
UNIQUE_KEY_ID = env("UNIQUE_KEY_ID", default="")
SUBSCRIBER_URL = env("SUBSCRIBER_URL", default="")
SIGNING_PRIVATE_KEY_PATH = env("GATEWAY_SIGNING_PRIVATE_KEY_PATH")
ENCRYPTION_PRIVATE_KEY_PATH = env("GATEWAY_ENCRYPTION_PRIVATE_KEY_PATH")
ON_SUBSCRIBE_CALLBACK_PATH = env("ON_SUBSCRIBE_CALLBACK_PATH", default="/on_subscribe")
# File-backed onboarding progress — Gateway has no DB (see module docstrings in
# core/onboarding_state.py for why this can't be a Django model like BAP/BPP's).
ONBOARDING_STATE_PATH = env("ONBOARDING_STATE_PATH", default="/app/data/onboarding_state.json")

# livetracker8.md §2.2: same defined cadence as Registry's own REGISTRY_KEY_ROTATION_DAYS
# (§1.2) — 90 days, a standard industry baseline for API/signing key rotation.
KEY_ROTATION_DAYS = env.int("KEY_ROTATION_DAYS", default=90)

# Django's test runner forces DEBUG=False regardless of .env — TESTING is the correct
# signal for "is this a local/test run" checks that must hold true even though DEBUG is
# off, matching registry/registry/settings.py's established fix for the same issue.
TESTING = "pytest" in sys.modules

# livetracker8.md §2.2: a real, previously-hidden bug found live 2026-09-04 — Gateway
# never had this block, so `django.core.cache.cache` (used by
# onboarding_service.py's pending-rotation-key hand-off) silently defaulted to Django's
# in-memory `LocMemCache`, per-gunicorn-worker, exactly the class of bug this whole
# tracker exists to close elsewhere (§1.1's rate limiter, §1.2's Registry key cache).
# `settings.REDIS_URL` was already mandatory for the circuit breaker (§2.1) but was
# never wired into Django's generic cache framework — confirmed live: a key set from a
# separate `manage.py shell` process was invisible to the gunicorn worker serving the
# real HTTP request, causing a genuine, reproducible rotation failure. Same pattern as
# Registry's/BAP's/BPP's own CACHES config for consistency.
CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": f"{REDIS_URL.rsplit('/', 1)[0]}/15" if TESTING else REDIS_URL,
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
            "CONNECTION_POOL_KWARGS": {"socket_connect_timeout": 0.5, "socket_timeout": 0.5},
        },
    }
}

SERVICE_NAME = "beckn-gateway"

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.staticfiles",
    "rest_framework",
    "django_observability",
    "core",
]

MIDDLEWARE = [
    "django_observability.middleware.CorrelationIdMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django_observability.middleware.ExceptionHandlingMiddleware",
]

ROOT_URLCONF = "gateway.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": ["django.template.context_processors.request"]},
    },
]

WSGI_APPLICATION = "gateway.wsgi.application"

# No DATABASES entry — deliberately stateless. Django itself is fine without one
# as long as no INSTALLED_APPS requires the ORM (admin/auth/sessions excluded above).

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True

# --- Observability ---
OBSERVABILITY_READINESS_CHECKS = []  # no hard dependencies to check — Gateway has no DB;
# cache is explicitly optional ([BETA]), so its absence must not make /ready report unavailable

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "correlation_id": {"()": "django_observability.logging_filter.CorrelationIdLogFilter"},
    },
    "formatters": {
        "json": {
            "()": "django_observability.logging_formatter.JsonFormatter",
            "service_name": SERVICE_NAME,
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json",
            "filters": ["correlation_id"],
        },
    },
    "root": {"handlers": ["console"], "level": LOG_LEVEL},
    "loggers": {
        "django": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
        "django_observability": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
        "gateway": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
    },
}
