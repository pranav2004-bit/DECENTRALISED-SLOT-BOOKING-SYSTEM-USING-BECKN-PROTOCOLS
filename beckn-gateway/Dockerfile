# Beckn Gateway — Django backend, stateless. Multi-stage build, non-root runtime user.
# BUILD CONTEXT MUST BE THE REPO ROOT — this app imports shared/django_observability
# and shared/resilient_http as siblings of beckn-gateway/, matching local dev's
# sys.path setup in beckn-gateway/gateway/settings.py.

FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

FROM base AS deps
COPY beckn-gateway/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

FROM deps AS runtime
RUN addgroup --system app && adduser --system --ingroup app app
COPY shared/django_observability ./shared/django_observability
COPY shared/resilient_http ./shared/resilient_http
COPY shared/beckn_crypto ./shared/beckn_crypto
COPY shared/beckn_transaction ./shared/beckn_transaction
COPY shared/redis_safe ./shared/redis_safe
COPY shared/key_rotation ./shared/key_rotation
COPY beckn-gateway/ ./beckn-gateway/
# Phase 3 Exit fix (livetracker2.md, 2026-07-24) — see BAP/backend/Dockerfile's
# identical comment: /app/data/secrets must already exist, app-owned, in the image
# before it's first mounted as a named volume, or the non-root runtime user can't
# write its own key file into the daemon-created, root-owned empty volume.
RUN mkdir -p /app/data/secrets && chown -R app:app /app
USER app
WORKDIR /app/beckn-gateway

EXPOSE 8000
# timeout bumped 3s -> 10s (2026-09-01): confirmed live that the app itself was healthy
# and answering /health correctly, but the probe process alone missed a 3s timeout under
# real CPU contention on a resource-constrained host — false "unhealthy" flags, not a real
# failure. 10s still catches a genuinely dead container well within interval*retries=90s.
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health').read()" || exit 1

CMD ["gunicorn", "gateway.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2"]
