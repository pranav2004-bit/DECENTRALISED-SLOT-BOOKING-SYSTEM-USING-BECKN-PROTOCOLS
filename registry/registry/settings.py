"""Django settings for the Registry application.

Config is environment-variable-driven (12-factor, per ENVIRONMENTS.md) via django-environ,
which fails fast with a clear ImproperlyConfigured error on missing/invalid required vars —
this is the runtime behavior Phase 0.2 could only document, not implement, before this app existed.
"""

import sys
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent

# Make the shared/ monorepo folder importable (django_observability app, etc.) —
# same relative layout locally (registry/../shared) and in the Docker image (see Dockerfile).
sys.path.insert(0, str(BASE_DIR.parent / "shared"))

env = environ.Env(
    DJANGO_DEBUG=(bool, False),
)
env_file = BASE_DIR / ".env"
if env_file.exists():
    environ.Env.read_env(str(env_file))

# --- Required, fail-fast if missing (per ENVIRONMENTS.md "Configuration Strategy") ---
SECRET_KEY = env("DJANGO_SECRET_KEY")  # raises ImproperlyConfigured if absent — no insecure default
DATABASE_URL = env("DATABASE_URL")
SIGNING_PRIVATE_KEY_PATH = env("REGISTRY_SIGNING_PRIVATE_KEY_PATH")
ENCRYPTION_PRIVATE_KEY_PATH = env("REGISTRY_ENCRYPTION_PRIVATE_KEY_PATH")
REDIS_URL = env("REDIS_URL")

# livetracker8.md §1.2: the actual defined rotation cadence — 90 days, a standard
# industry baseline for API/signing key rotation (not a number derived from traffic
# data, since a rotation cadence is a security posture decision, not a load measurement
# — different from this project's own "no invented SLA" discipline, which is about not
# faking a *performance* number). Env-overridable per deployment.
REGISTRY_KEY_ROTATION_DAYS = env.int("REGISTRY_KEY_ROTATION_DAYS", default=90)

# --- Optional with sane defaults ---
DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])
CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=[])
LOG_LEVEL = env("LOG_LEVEL", default="INFO")

SERVICE_NAME = "registry"

# Django's test runner (via django.test.utils.setup_test_environment, invoked by
# pytest-django) force-sets DEBUG=False during tests regardless of .env — a deliberate
# Django convention so tests don't accidentally depend on DEBUG-only behavior. Found for
# real in Phase 2.1 when registry_keys.py's DEBUG-gated ephemeral-key fallback broke
# under pytest. TESTING is the correct signal to use instead of DEBUG for "is this a
# local/test run" checks that must hold true even though DEBUG gets forced off.
TESTING = "pytest" in sys.modules

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "django_observability",
    "core",
]

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

ROOT_URLCONF = "registry.urls"

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

WSGI_APPLICATION = "registry.wsgi.application"

DATABASES = {"default": env.db_url_config(DATABASE_URL)}
DATABASES["default"]["CONN_MAX_AGE"] = env.int("DB_CONN_MAX_AGE", default=60)
# resilient_db retries a database's first connection attempt after a short backoff —
# works around Neon free-tier's "scale to zero when inactive" cold-start behavior
# (confirmed live, RUNBOOK.md's "Postgres moved to Neon" note). See shared/resilient_db/
# base.py for the full reasoning; not affordable to switch to a paid always-on plan yet.
DATABASES["default"]["ENGINE"] = "resilient_db"  # Django appends ".base" itself

# livetracker8.md §1.1: replaces the old LocMemCache-backed rate limiter (per-process,
# so the effective limit was configured_limit * worker_count under multiple gunicorn
# workers — a real, previously-tracked gap, RUNBOOK.md's own "Postgres moved to Neon"
# era notes). Same pattern as BAP's/BPP's own CACHES config (bap/settings.py,
# bpp/settings.py) for consistency, including the TESTING DB-15 isolation.
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

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Security headers (Phase 2.5 hardening) ---
# Registry is backend-only, called by other backends per registry_details_v1.1.md §4 —
# never by a browser — so no CORS/CSRF-for-forms concerns; these are the standard
# transport/content-sniffing protections that apply regardless.
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SECURE_BROWSER_XSS_FILTER = True
SECURE_REFERRER_POLICY = "no-referrer"
# Real gap found and corrected live deploying behind Caddy (livetracker5.md's
# client-presentation deployment, 2026-09-25): SECURE_SSL_REDIRECT was previously
# `not DEBUG and not TESTING`, on the assumption a real deployment might be reached
# directly over plain HTTP by an untrusted caller needing an app-level redirect to
# HTTPS. That assumption doesn't hold for this app — Registry is "backend-only,
# called by other backends... never by a browser" (see this section's own comment
# above) — and in this deployment topology, the overwhelming majority of Registry's
# real traffic is *internal*, container-to-container plain HTTP on port 8000, which
# has no TLS listener at all (only Caddy, in front of the whole stack, terminates
# real TLS, on separate public ports). SECURE_SSL_REDIRECT=True made every one of
# those internal calls (BAP/BPP/Gateway's REGISTRY_BASE_URL=http://registry:8000)
# get 301'd to an unreachable https://registry:8000 — confirmed live: every non-
# /health endpoint hung for a full 30s gunicorn WORKER_TIMEOUT and was SIGKILLed,
# reproduced identically via loopback, 127.0.0.1, and cross-container calls, with
# the client-side symptom being an SSL handshake timeout against a plain-HTTP-only
# port (the client dutifully following the 301 to an https:// URL nothing serves).
# Real TLS enforcement for genuine external traffic is Caddy's job at the true edge,
# not this app's — matching the other 3 apps in this codebase, none of which sets
# SECURE_SSL_REDIRECT at all. SESSION_COOKIE_SECURE/CSRF_COOKIE_SECURE stay as they
# were (harmless — this app issues neither to a browser per the comment above).
SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = not DEBUG and not TESTING
CSRF_COOKIE_SECURE = not DEBUG and not TESTING

# --- Observability (per OBSERVABILITY.md) ---
OBSERVABILITY_READINESS_CHECKS = [
    ("database", "django_observability.checks.database_check"),
]
# Phase 2.6: subscribe/lookup/verify rate, latency, and error-rate metrics.
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
        "registry": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
    },
}
