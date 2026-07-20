# BPP Backend — Django backend. Multi-stage build, non-root runtime user.
# BUILD CONTEXT MUST BE THE REPO ROOT — this app imports shared/django_observability,
# shared/resilient_http, shared/event_bus, shared/beckn_crypto, shared/inventory_core, and
# shared/realtime as siblings of BPP/backend/, matching local dev's sys.path setup in
# BPP/backend/bpp/settings.py.

FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

FROM base AS deps
COPY BPP/backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

FROM deps AS runtime
RUN addgroup --system app && adduser --system --ingroup app app
COPY shared/django_observability ./shared/django_observability
COPY shared/resilient_http ./shared/resilient_http
COPY shared/event_bus ./shared/event_bus
COPY shared/beckn_crypto ./shared/beckn_crypto
COPY shared/beckn_transaction ./shared/beckn_transaction
COPY shared/inventory_core ./shared/inventory_core
COPY shared/realtime ./shared/realtime
COPY BPP/backend/ ./BPP/backend/
RUN chown -R app:app /app
USER app
WORKDIR /app/BPP/backend

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health').read()" || exit 1

# daphne (not gunicorn) — serves ordinary HTTP through Channels' ProtocolTypeRouter
# unchanged (see bpp/asgi.py) while also handling the real WebSocket channel (livetracker2.md
# §2.4). A real topology change from the WSGI-only setup Phase 1 shipped with, not a
# cosmetic one — revisit worker/scaling config once real concurrent load exists.
CMD ["daphne", "-b", "0.0.0.0", "-p", "8000", "bpp.asgi:application"]
