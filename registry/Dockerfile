# Registry — Django backend. Multi-stage build, non-root runtime user.
# NOTE: expects Phase 1.1's Django project layout (manage.py at app root, requirements.txt).
# This Dockerfile is scaffolded ahead of that code landing — see README.md "Status note".

FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

FROM base AS deps
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

FROM deps AS runtime
RUN addgroup --system app && adduser --system --ingroup app app
COPY . .
RUN chown -R app:app /app
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health').read()" || exit 1

CMD ["gunicorn", "registry.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2"]
