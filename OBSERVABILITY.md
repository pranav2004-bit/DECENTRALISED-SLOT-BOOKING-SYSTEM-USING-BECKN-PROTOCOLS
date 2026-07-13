# Observability

Shared pattern all four applications implement identically in Phase 1, so logs/metrics/traces are comparable across the whole system instead of drifting per-app. See [livetracker1.md](livetracker1.md) Phase 0.7 (this document) and Phase 1.x (per-app instantiation).

## Structured Logging

Every log line is a single JSON object with these fields, minimum:

| Field | Type | Notes |
|---|---|---|
| `timestamp` | string, ISO 8601 UTC | e.g. `2026-07-13T09:00:00.000Z` |
| `service` | string | `registry` \| `beckn-gateway` \| `bap-backend` \| `bap-web` \| `bpp-backend` \| `bpp-web` |
| `level` | string | `DEBUG` \| `INFO` \| `WARNING` \| `ERROR` \| `CRITICAL` |
| `correlation_id` | string | propagated per below; `null` only for startup/shutdown lines with no request context |
| `message` | string | human-readable |

Additional fields (e.g. `subscriber_id`, `transaction_id`, `duration_ms`) may be added per log line as structured extras — never string-interpolated into `message`.

**Reference implementation:** [shared/observability/logging_reference.py](shared/observability/logging_reference.py) — a minimal, runnable example of the exact JSON shape every Django app's logging config must produce. Phase 1 per-app logging setup should match this shape, not reinvent it.

## Correlation ID Propagation

- Header name: `X-Correlation-Id`.
- If absent on an inbound request, the receiving service generates one (UUID v4) and includes it in its own logs and in the header of any downstream call it makes.
- If present, it is passed through unchanged to downstream calls and included in all logs for that request.
- This is distinct from Beckn's own `transaction_id`/`message_id` (protocol-level, in the `context` object per [protocol_compliance_notes_v1.1.md](protocol_compliance_notes_v1.1.md) §D.3) — `X-Correlation-Id` is our internal debugging aid across service boundaries; log both when both are present on a request.

## Health & Readiness Endpoints

Every backend app (Registry, Gateway, BAP backend, BPP backend) exposes:

- **`GET /health`** — liveness. Returns `200 {"status": "ok", "service": "<name>"}` if the process is running. No dependency checks — this must stay fast and never fail due to a downstream outage (a dead downstream is a `/ready` concern, not a reason to restart this process).
- **`GET /ready`** — readiness. Returns `200 {"status": "ok", "service": "<name>", "checks": {"database": "ok", "cache": "ok"}}` only if all of this app's hard dependencies (DB, cache, where applicable) are reachable; otherwise `503` with the failing check(s) named.

Frontend apps (BAP/web, BPP/web) expose the same `/health` (liveness only — no backend dependency check, to avoid cascading false-unhealthy states when the backend is merely slow).

## Metrics

**`GET /metrics`** — Prometheus text exposition format, on every backend app. Minimum metrics: request count and latency histogram per route, error count per route, and (where applicable) DB connection pool utilization.

## Distributed Tracing

`[MVP]`/`[PILOT]`: correlation-ID-based log correlation (above) is sufficient — searching logs by `correlation_id` across services reconstructs a request's path.

`[BETA]`+: adopt W3C Trace Context (`traceparent` header) and OpenTelemetry SDK once request volume/complexity justifies dedicated tracing infrastructure (Jaeger/Tempo/etc.). Not built now — this is a deliberate no-over-engineering call, not an oversight; revisit when Phase 1–4 foundation work is done and business-workflow trackers begin generating real multi-hop traffic worth visualizing.

## Alerting (forward reference)

Alert thresholds are defined per-service once each app exists (Phase 1) and wired to dashboards in Phase 2.6 (Registry Observability & Ops) and Phase 4.4 (Production Readiness Review). Not applicable to Phase 0, which only defines the pattern.
