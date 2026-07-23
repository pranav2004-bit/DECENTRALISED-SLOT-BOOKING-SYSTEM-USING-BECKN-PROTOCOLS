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

**Real gap found and closed for `confirm`/`cancel`/`update` (livetracker2.md §3.10):** this document specified downstream propagation from Phase 0, but a direct audit found BAP's outbound calls to Gateway and Gateway's relay to BPP never actually set the header — each hop silently minted its own disconnected id. Now genuinely forwarded end-to-end for these three actions (the ones whose dispatch also writes to the new booking-lifecycle audit log, see `RUNBOOK.md`), and threaded from BPP's request-handling thread into its background dispatch and the event layer (`ContextVar`s don't cross a manually-created `threading.Thread`, so the id is captured and passed explicitly, not re-read from context inside the spawned thread). `search`/`select`/`init` still mint a fresh id per hop as before — genuinely lower-value to fix, since neither writes to the audit log this correlates with.

## Health & Readiness Endpoints

Every backend app (Registry, Gateway, BAP backend, BPP backend) exposes:

- **`GET /health`** — liveness. Returns `200 {"status": "ok", "service": "<name>"}` if the process is running. No dependency checks — this must stay fast and never fail due to a downstream outage (a dead downstream is a `/ready` concern, not a reason to restart this process).
- **`GET /ready`** — readiness. Returns `200 {"status": "ok", "service": "<name>", "checks": {"database": "ok", "cache": "ok"}}` only if all of this app's hard dependencies (DB, cache, where applicable) are reachable; otherwise `503` with the failing check(s) named.

Frontend apps (BAP/web, BPP/web) expose the same `/health` (liveness only — no backend dependency check, to avoid cascading false-unhealthy states when the backend is merely slow).

## Metrics

**`GET /metrics`** — Prometheus text exposition format, on every backend app. Minimum metrics: request count and latency histogram per route, error count per route, and (where applicable) DB connection pool utilization.

**Business metrics (livetracker2.md §3.10):** BAP exposes `bap_booking_funnel_total{stage=...}` (search-to-confirm conversion funnel) and BPP exposes `bpp_booking_lifecycle_total{event=...}` (confirmed/cancelled/hold-created/hold-expired) — real, Redis-backed counters (`core/metrics.py` in each app), not the in-process pattern Registry's own `core/metrics.py` uses. See `RUNBOOK.md`'s "Beauty Booking Business Metrics & Alerting Thresholds" table for the full metric list and suggested alert thresholds.

## Distributed Tracing

`[MVP]`/`[PILOT]`: correlation-ID-based log correlation (above) is sufficient — searching logs by `correlation_id` across services reconstructs a request's path.

`[BETA]`+: adopt W3C Trace Context (`traceparent` header) and OpenTelemetry SDK once request volume/complexity justifies dedicated tracing infrastructure (Jaeger/Tempo/etc.). Not built now — this is a deliberate no-over-engineering call, not an oversight; revisit when Phase 1–4 foundation work is done and business-workflow trackers begin generating real multi-hop traffic worth visualizing.

**Evaluated for real, not left as a silent forward reference (livetracker2.md §3.10):** this tracker's Phase 3 does satisfy the *literal* trigger condition above — `search`→`select`→`init`→`confirm` each genuinely hop BAP→Gateway→BPP→Gateway→BAP, real multi-hop traffic, not trust-layer plumbing. **Decision: re-deferred, but for a concrete, traffic-volume-tied reason, not a repeat of the original text.** The trigger condition's own qualifier is "worth visualizing" — and at this project's current actual traffic (manual/dev-session testing, effectively one transaction in flight at a time, never real concurrent pilot users), correlation-ID log-stitching genuinely stays tractable: §3.10 closed the one real gap that would have undermined it (the id wasn't forwarded across the BAP→Gateway→BPP hop chain at all before this phase — see `BAP/backend/core/confirm_service.py`/`beckn-gateway/core/routing.py`), and a human can `grep` one `correlation_id` across 3 services' logs by hand at this volume without needing a trace visualizer. Dedicated tracing infrastructure (Jaeger/Tempo, SDK instrumentation, a 5th service to operate) earns its real complexity budget specifically once there's enough *concurrent* overlapping multi-hop traffic that manual log-correlation stops being tractable — real pilot users placing bookings simultaneously, not a single tester's session. That hasn't happened yet. Revisit at that point, not before.

## Alerting (forward reference)

Alert thresholds are defined per-service once each app exists (Phase 1) and wired to dashboards in Phase 2.6 (Registry Observability & Ops) and Phase 4.4 (Production Readiness Review). Not applicable to Phase 0, which only defines the pattern.
