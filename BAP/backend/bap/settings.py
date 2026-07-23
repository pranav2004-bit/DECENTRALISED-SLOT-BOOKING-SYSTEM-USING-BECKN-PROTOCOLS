"""Django settings for the BAP (Buyer App Platform) backend. Config via django-environ,
fail-fast on missing required vars — same pattern as Registry/Gateway.
"""

import sys
from pathlib import Path

import environ
from corsheaders.defaults import default_headers as CORS_DEFAULT_HEADERS

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR.parent.parent / "shared"))

env = environ.Env(DJANGO_DEBUG=(bool, False))
env_file = BASE_DIR / ".env"
if env_file.exists():
    environ.Env.read_env(str(env_file))

SECRET_KEY = env("DJANGO_SECRET_KEY")
DATABASE_URL = env("DATABASE_URL")
REDIS_URL = env("REDIS_URL")

DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])
CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=[])
# django-cors-headers' own DEFAULT_HEADERS (accept/authorization/content-type/etc.) doesn't
# include this project's own custom `Idempotency-Key` request header (§3.6's
# django_observability.idempotency.IDEMPOTENCY_HEADER, read by confirm_trigger_view) — a
# real gap found live in §3.9's own browser verification: the browser's CORS preflight
# correctly refused to allow the actual POST /api/v1/confirm through once BAP/web started
# sending that header, since it was never granted here.
CORS_ALLOW_HEADERS = [*CORS_DEFAULT_HEADERS, "idempotency-key"]
LOG_LEVEL = env("LOG_LEVEL", default="INFO")

REGISTRY_BASE_URL = env("REGISTRY_BASE_URL")
GATEWAY_BASE_URL = env("GATEWAY_BASE_URL")
SUBSCRIBER_ID = env("SUBSCRIBER_ID", default="")
UNIQUE_KEY_ID = env("UNIQUE_KEY_ID", default="")
SUBSCRIBER_URL = env("SUBSCRIBER_URL", default="")
SIGNING_PRIVATE_KEY_PATH = env("BAP_SIGNING_PRIVATE_KEY_PATH")
ENCRYPTION_PRIVATE_KEY_PATH = env("BAP_ENCRYPTION_PRIVATE_KEY_PATH")
ON_SUBSCRIBE_CALLBACK_PATH = env("ON_SUBSCRIBE_CALLBACK_PATH", default="/on_subscribe")
EVENT_BUS_URL = env("EVENT_BUS_URL", default=REDIS_URL)
EVENT_BUS_QUEUE_NAME = "bap-internal-events"
EVENT_BUS_DLQ_NAME = env("EVENT_BUS_DLQ_NAME", default="bap-internal-dlq")

HTTP_CLIENT_TIMEOUT_MS = env.int("HTTP_CLIENT_TIMEOUT_MS", default=5000)
HTTP_CLIENT_MAX_RETRIES = env.int("HTTP_CLIENT_MAX_RETRIES", default=3)
HTTP_CLIENT_CIRCUIT_BREAKER_THRESHOLD = env.int("HTTP_CLIENT_CIRCUIT_BREAKER_THRESHOLD", default=5)

# Django's test runner forces DEBUG=False regardless of .env — TESTING is the correct
# signal for "is this a local/test run" checks that must hold true even though DEBUG is
# off, matching registry/registry/settings.py's established fix for the same issue.
TESTING = "pytest" in sys.modules

SERVICE_NAME = "bap-backend"

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
    "corsheaders",
    "django_observability",
    "core",
]

# WebSocket channel between Web App and Backend (livetracker2.md §2.4) — foundation transport
# only, see shared/realtime/consumers.py. "daphne" must be first in INSTALLED_APPS per
# Channels' own documented setup.
ASGI_APPLICATION = "bap.asgi.application"

MIDDLEWARE = [
    "django_observability.middleware.CorrelationIdMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_observability.middleware.ExceptionHandlingMiddleware",
]

ROOT_URLCONF = "bap.urls"

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

WSGI_APPLICATION = "bap.wsgi.application"

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

AUTH_USER_MODEL = "core.Customer"

# Argon2 first (livetracker2.md §2.1: "argon2/bcrypt via Django's built-in password hashers") —
# Django's own default (PBKDF2) is not what was asked for here. The rest are fallback-only, so
# existing hashes using them still verify; new hashes always use Argon2.
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
    "django.contrib.auth.hashers.BCryptSHA256PasswordHasher",
]

# Customer sessions live in Redis, not the DB (livetracker2.md §2.1, project_details.md's
# explicit "Redis for caching and session management" prerequisite) — "cache" (not
# "cached_db") is a pure-Redis backend with no DB fallback/table at all.
SESSION_ENGINE = "django.contrib.sessions.backends.cache"
SESSION_CACHE_ALIAS = "default"

# §3.7: real gap found and closed — neither was set at all, meaning both defaulted
# to Django's own `False`, so session/CSRF cookies would be sent over plain HTTP
# even in a real, non-DEBUG deployment. Standard Django pattern: only require
# HTTPS-only cookies once actually deployed non-DEBUG; local/dev over plain HTTP
# still works. `SESSION_COOKIE_HTTPONLY` is already Django's own default (True) —
# set explicitly here to match this project's convention of not leaving
# security-relevant settings implicit.
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

# §3.10: real search-to-confirm funnel counters, Redis-backed — see core/metrics.py.
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
        "bap": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
    },
}
