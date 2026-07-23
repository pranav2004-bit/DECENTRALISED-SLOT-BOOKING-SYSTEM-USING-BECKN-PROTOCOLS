"""Django settings for the BPP (Beckn Provider Platform) backend. Config via
django-environ, fail-fast on missing required vars — same pattern as
Registry/Gateway/BAP.
"""

import sys
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR.parent.parent / "shared"))

env = environ.Env(DJANGO_DEBUG=(bool, False))
env_file = BASE_DIR / ".env"
if env_file.exists():
    environ.Env.read_env(str(env_file))

SECRET_KEY = env("DJANGO_SECRET_KEY")
DATABASE_URL = env("DATABASE_URL")
REDIS_URL = env("REDIS_URL")

# Reservation Window / TTL-based HELD state (livetracker2.md §1.3), first actually used by
# a real transaction flow in §3.2's select/on_select. 600s (10 minutes) is a conventional
# e-commerce checkout window — no real-traffic baseline exists yet to tune this against
# (same honesty already applied elsewhere in this project rather than inventing a
# precision this stage doesn't have data to support).
RESERVATION_HOLD_TTL_SECONDS = env.int("RESERVATION_HOLD_TTL_SECONDS", default=600)

# livetracker2.md §3.11: how often the real background reconciliation loop (expired-hold
# sweep + catalog-cache drift check, core/reconciliation.py) runs. 60s is a conventional
# starting point for a correctness safety net, not a real-traffic-tuned value — same
# honesty already applied to RESERVATION_HOLD_TTL_SECONDS above.
RECONCILIATION_INTERVAL_SECONDS = env.int("RECONCILIATION_INTERVAL_SECONDS", default=60)

DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])
CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=[])
LOG_LEVEL = env("LOG_LEVEL", default="INFO")

REGISTRY_BASE_URL = env("REGISTRY_BASE_URL")
GATEWAY_BASE_URL = env("GATEWAY_BASE_URL")
SUBSCRIBER_ID = env("SUBSCRIBER_ID", default="")
UNIQUE_KEY_ID = env("UNIQUE_KEY_ID", default="")
SUBSCRIBER_URL = env("SUBSCRIBER_URL", default="")
SIGNING_PRIVATE_KEY_PATH = env("BPP_SIGNING_PRIVATE_KEY_PATH")
ENCRYPTION_PRIVATE_KEY_PATH = env("BPP_ENCRYPTION_PRIVATE_KEY_PATH")
ON_SUBSCRIBE_CALLBACK_PATH = env("ON_SUBSCRIBE_CALLBACK_PATH", default="/on_subscribe")
EVENT_BUS_URL = env("EVENT_BUS_URL", default=REDIS_URL)
EVENT_BUS_QUEUE_NAME = "bpp-internal-events"
EVENT_BUS_DLQ_NAME = env("EVENT_BUS_DLQ_NAME", default="bpp-internal-dlq")

HTTP_CLIENT_TIMEOUT_MS = env.int("HTTP_CLIENT_TIMEOUT_MS", default=5000)
HTTP_CLIENT_MAX_RETRIES = env.int("HTTP_CLIENT_MAX_RETRIES", default=3)
HTTP_CLIENT_CIRCUIT_BREAKER_THRESHOLD = env.int("HTTP_CLIENT_CIRCUIT_BREAKER_THRESHOLD", default=5)

# Domain codes — Healthcare/Automotive pending confirmation, per
# protocol_compliance_notes_v1.1.md "Remaining Open Items" and livetracker1.md Phase 3.2.
DOMAIN_HEALTHCARE = env("DOMAIN_HEALTHCARE", default="CONFIRM_BEFORE_USE")
DOMAIN_AUTOMOTIVE = env("DOMAIN_AUTOMOTIVE", default="CONFIRM_BEFORE_USE")
DOMAIN_BEAUTY = env("DOMAIN_BEAUTY", default="ONDC:RET13")

# Django's test runner forces DEBUG=False regardless of .env — TESTING is the correct
# signal for "is this a local/test run" checks that must hold true even though DEBUG is
# off, matching registry/registry/settings.py's established fix for the same issue.
TESTING = "pytest" in sys.modules

SERVICE_NAME = "bpp-backend"

INSTALLED_APPS = [
    "daphne",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "channels",
    "django_observability",
    "inventory_core",
    "core",
]

# WebSocket channel between Web App and Backend (livetracker2.md §2.4) — foundation transport
# only, see shared/realtime/consumers.py. "daphne" must be first in INSTALLED_APPS per
# Channels' own documented setup.
ASGI_APPLICATION = "bpp.asgi.application"

MIDDLEWARE = [
    "django_observability.middleware.CorrelationIdMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_observability.middleware.ExceptionHandlingMiddleware",
]

ROOT_URLCONF = "bpp.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "bpp.wsgi.application"

DATABASES = {"default": env.db_url_config(DATABASE_URL)}
DATABASES["default"]["CONN_MAX_AGE"] = env.int("DB_CONN_MAX_AGE", default=60)

CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": REDIS_URL,
        "OPTIONS": {"CLIENT_CLASS": "django_redis.client.DefaultClient"},
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

AUTH_USER_MODEL = "core.BusinessAccount"

# Argon2 first — same standard applied to BAP's Customer accounts (livetracker2.md §2.1);
# a business account's password deserves the same modern hasher, not a weaker one just
# because §2.2 doesn't repeat the requirement verbatim.
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
    "django.contrib.auth.hashers.BCryptSHA256PasswordHasher",
]

# Business-account sessions in Redis, not the DB — same reasoning as BAP §2.1.
SESSION_ENGINE = "django.contrib.sessions.backends.cache"
SESSION_CACHE_ALIAS = "default"

# §3.7: same real gap and fix as BAP's settings.py — see its comment for the
# full rationale.
SESSION_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_SECURE = not DEBUG

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

OBSERVABILITY_READINESS_CHECKS = [
    ("database", "django_observability.checks.database_check"),
    ("cache", "django_observability.checks.cache_check"),
]

# §3.10: real booking-lifecycle counters, Redis-backed — see core/metrics.py.
EXTRA_METRICS_PROVIDERS = ["core.metrics.render_metrics"]

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
        "bpp": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
    },
}
