# BAP Backend — Django backend. Multi-stage build, non-root runtime user.
# BUILD CONTEXT MUST BE THE REPO ROOT — this app imports shared/django_observability,
# shared/resilient_http, shared/event_bus, shared/beckn_crypto, and shared/realtime as
# siblings of BAP/backend/, matching local dev's sys.path setup in
# BAP/backend/bap/settings.py.

FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

FROM base AS deps
COPY BAP/backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

FROM deps AS runtime
RUN addgroup --system app && adduser --system --ingroup app app
COPY shared/django_observability ./shared/django_observability
COPY shared/resilient_http ./shared/resilient_http
COPY shared/event_bus ./shared/event_bus
COPY shared/beckn_crypto ./shared/beckn_crypto
COPY shared/beckn_transaction ./shared/beckn_transaction
COPY shared/realtime ./shared/realtime
COPY shared/redis_safe ./shared/redis_safe
COPY BAP/backend/ ./BAP/backend/
# Phase 3 Exit fix (livetracker2.md, 2026-07-24): /app/data/secrets is now a named
# Docker volume (persisted signing/encryption keys, see docker-compose.yml). A fresh
# named volume is created root-owned by the Docker daemon on first mount — Docker
# only copies the *image's* existing ownership/content into it, so this directory
# must already exist in the image, owned by `app`, before that first mount, or the
# non-root runtime user gets a real PermissionError writing its own key file.
RUN mkdir -p /app/data/secrets && chown -R app:app /app
USER app
WORKDIR /app/BAP/backend

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health').read()" || exit 1

# daphne (not gunicorn) — serves ordinary HTTP through Channels' ProtocolTypeRouter
# unchanged (see bap/asgi.py) while also handling the real WebSocket channel (livetracker2.md
# §2.4). A real topology change from the WSGI-only setup Phase 1 shipped with, not a
# cosmetic one — revisit worker/scaling config once real concurrent load exists.
CMD ["daphne", "-b", "0.0.0.0", "-p", "8000", "bap.asgi:application"]
