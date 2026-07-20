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
COPY beckn-gateway/ ./beckn-gateway/
RUN chown -R app:app /app
USER app
WORKDIR /app/beckn-gateway

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health').read()" || exit 1

CMD ["gunicorn", "gateway.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2"]
