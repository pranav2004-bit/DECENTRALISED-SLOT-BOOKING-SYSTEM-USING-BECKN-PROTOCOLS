# Registry — Django backend. Multi-stage build, non-root runtime user.
# BUILD CONTEXT MUST BE THE REPO ROOT (not this folder) — this app imports the shared/
# django_observability package as a sibling of registry/, matching local dev's
# sys.path setup in registry/settings.py. See docker-compose.yml's `build.context: .`
# with `dockerfile: registry/Dockerfile` for how this is invoked correctly.

FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

FROM base AS deps
COPY registry/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

FROM deps AS runtime
RUN addgroup --system app && adduser --system --ingroup app app
COPY shared/django_observability ./shared/django_observability
COPY shared/resilient_http ./shared/resilient_http
COPY shared/beckn_crypto ./shared/beckn_crypto
COPY shared/testing/contract_schemas ./shared/testing/contract_schemas
COPY shared/redis_safe ./shared/redis_safe
COPY registry/ ./registry/
# Phase 3 Exit fix (livetracker2.md, 2026-07-24) — see BAP/backend/Dockerfile's
# identical comment. Registry's own key path is /run/secrets (outside /app, unlike
# the other three apps' /app/data/secrets), so it needs its own explicit mkdir+chown
# rather than being covered by the `chown -R app:app /app` below.
RUN mkdir -p /run/secrets && chown -R app:app /run/secrets
RUN chown -R app:app /app
RUN chmod +x /app/registry/entrypoint.sh
USER app
WORKDIR /app/registry

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health').read()" || exit 1

# Real gap found live (2026-07-31): nothing ever ran `manage.py migrate` against a
# genuinely fresh database — see entrypoint.sh's own comment for the full story.
ENTRYPOINT ["./entrypoint.sh"]
CMD ["gunicorn", "registry.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2"]
