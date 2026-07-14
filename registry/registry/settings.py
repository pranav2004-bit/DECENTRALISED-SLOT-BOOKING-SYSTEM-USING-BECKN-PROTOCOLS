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
# HTTPS-only headers are gated on DEBUG so local/dev over plain HTTP keeps working;
# real deployments terminate TLS in front of gunicorn and must set DJANGO_DEBUG=false.
SECURE_SSL_REDIRECT = not DEBUG
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG

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
