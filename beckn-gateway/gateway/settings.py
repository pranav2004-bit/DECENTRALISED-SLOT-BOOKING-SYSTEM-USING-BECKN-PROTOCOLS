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
    CACHE_ENABLED=(bool, False),
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
CACHE_ENABLED = env.bool("CACHE_ENABLED", default=False)
# Only meaningful when CACHE_ENABLED — see core/registry_client.py for why the
# Redis-backed circuit breaker fix is opt-in here but not for BAP/BPP.
REDIS_URL = env("REDIS_URL", default="")

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

# Django's test runner forces DEBUG=False regardless of .env — TESTING is the correct
# signal for "is this a local/test run" checks that must hold true even though DEBUG is
# off, matching registry/registry/settings.py's established fix for the same issue.
TESTING = "pytest" in sys.modules

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
