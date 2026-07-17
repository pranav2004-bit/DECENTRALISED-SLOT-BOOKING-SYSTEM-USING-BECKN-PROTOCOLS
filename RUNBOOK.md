# Runbook

**Status: stub.** This fills in with real, battle-tested procedures as Phase 2–4 of [livetracker1.md](livetracker1.md) produce actual incidents, onboarding runs, and operational experience. An early, honest stub beats a fabricated "complete" runbook nobody has actually exercised — false confidence in an incident is worse than an acknowledged gap.

## Where to Look

- **Logs:** structured JSON per [OBSERVABILITY.md](OBSERVABILITY.md), searchable by `correlation_id` or Beckn `transaction_id`.
- **Health/readiness:** `GET /health` and `GET /ready` on every backend app.
- **Metrics:** `GET /metrics` (Prometheus format) on every backend app.

## Known Operational Facts (filled in as Phase 2–4 progress)

- The Registry is a single point of trust for the whole network. **Confirmed live in Phase 4.2** (not just theorized): stopping the Registry container and hitting Gateway's `/search` returns a clean `500 INTERNAL_ERROR` (fails closed, no crash) — but takes **~19 seconds** to fail, because `resilient_http`'s retry policy re-attempts DNS resolution on a now-unreachable hostname several times before giving up. If Registry-outage symptoms include slow (not fast) failures elsewhere, this is why — it is not a hang or a deadlock.
- **Fixed and confirmed live (2026-07-16):** the circuit breaker previously did not trip across gunicorn worker processes — 5 consecutive real failures against a stopped Registry, sent to Gateway's 2-worker container, never failed fast, each still took the full ~19s retry-then-DNS-fail path. Root cause was the same as the per-worker metrics/rate-limit undercounting below, applied to a new component: `ResilientHttpClient`'s circuit breaker state was a module-level singleton, so each gunicorn worker kept its own independent breaker. **Fix:** `RedisCircuitBreaker` (`shared/resilient_http/circuit_breaker.py`) shares state via Redis. Reverified live: attempts 1–5 against a stopped Registry each took ~19s, attempt 6 onward failed in ~0.3s with `CircuitOpenError`. Active unconditionally for BAP/BPP (required Redis); active for Gateway **only when `CACHE_ENABLED=true`** (the `gateway-cache` service stays an optional `[BETA]`-profile service, `docker compose --profile with-gateway-cache up`) — with the cache disabled (the default), Gateway's breaker falls back to the old per-worker in-memory behavior above. Registry's own outbound client (site-verification fetch, on_subscribe dispatch) was intentionally left on in-memory-only — that path wasn't part of the tested finding and Registry has no Redis dependency today.
- Recovery is fast and automatic: once Registry comes back, the very next request through Gateway succeeds in under 1 second — no manual restart or cache-clear needed anywhere.
- Rate limits: Subscribe 10 req/min, Lookup 7,600 req/min (per [protocol_compliance_notes_v1.1.md](protocol_compliance_notes_v1.1.md) §B.6) — a sudden spike in `RATE_LIMITED` errors against the Registry is expected behavior under abuse/misconfiguration, not a Registry bug.
- Key rotation has no dedicated endpoint — it's a re-`/subscribe` call with a new `key_pair` (§B.4). If a rotation appears "stuck," check whether the new Subscribe call actually completed the on_subscribe challenge, not just whether it was sent. **As of Phase 4.3, rotation must be signed with the CURRENTLY REGISTERED key, not the new one being submitted** — a re-Subscribe that gets `401 UNAUTHORIZED` is very likely signed with the wrong (new) key; this is by design (see Security below), not a bug.
- **Registry's `/subscribe` and `/lookup` now require a real signed `Authorization` header (Phase 4.3)** — an unauthenticated or wrongly-signed call to either gets `401 UNAUTHORIZED`. Confirmed live. First-time Subscribe is verified against the key being submitted (proof-of-possession); re-Subscribe is verified against the key already on file.
- **Known, protocol-consistent limitation, not a bug:** a validly-signed request can be replayed as-is within its signature's `created`→`expires` window (confirmed live — the same signed Lookup call succeeded twice in a row). The confirmed Authorization scheme has no nonce field, only a time window, so this is a characteristic of the real protocol design, not a gap introduced here. Mitigated by the short (~30s) window used throughout this codebase, not eliminated.

## Registry Metrics & Alerting Thresholds (Phase 2.6)

`GET /metrics` on the Registry exposes real, live counters (not placeholders — see `core/metrics.py`):

| Metric | What it means | Suggested alert threshold |
|---|---|---|
| `registry_requests_total{metric="subscribe_errors_total"}` | Malformed/invalid Subscribe attempts | Sudden spike → possible misbehaving client or attack; investigate before assuming abuse |
| `registry_requests_total{metric="verify_failures_total"}` | Challenge replay/expiry/mismatch on on_subscribe | Sustained rate > a few per minute from one participant → likely a broken integration on their end, not ours |
| `registry_requests_total{metric="verify_successes_total"}` vs `subscribe_requests_total` | Conversion rate from Subscribe to actually reaching `SUBSCRIBED` | A large, persistent gap suggests participants are failing onboarding — worth proactive outreach, not just a technical alert |
| `registry_request_latency_seconds_sum / _count` (per `subscribe`/`lookup`) | Average latency per endpoint | No fixed number yet — no real traffic baseline exists at foundation stage; set a threshold once Phase 3 onboarding generates real traffic to baseline against, not before (avoids alerting on a made-up number) |
| Rate of HTTP `429` responses | Rate limiting engaging | Expected occasionally under legitimate retry storms; sustained high rate from one IP → likely abuse, correlates with `rate_limit.py`'s per-IP counters |

**Honest limitation carried from `rate_limit.py`/`metrics.py`:** both are in-process (LocMemCache/module-level), so with multiple gunicorn workers the true totals are undercounted per worker unless each worker's `/metrics` is scraped and summed independently. Acceptable at `[MVP]`/`[PILOT]` single/low-worker scale; revisit with shared Redis-backed counters before `[BETA]` multi-worker production.

**A real local dashboard exists as of 2026-07-17**: `docker compose --profile with-monitoring up -d` starts Prometheus (`monitoring/prometheus.yml`, scrapes all four apps' `/metrics` every 15s) and Grafana (`localhost:3002`, anonymous viewer access enabled for local dev, admin/admin for the provisioned account) with a pre-loaded 5-panel dashboard (`monitoring/grafana/dashboards/beckn-overview.json`) built from exactly this table. Confirmed live: all 4 scrape targets `up`, real Registry traffic visible in the dashboard within one scrape interval. This was previously described as blocked on "real cloud infrastructure" — that was incorrect; nothing here needs anything beyond `docker-compose`. What's still genuinely not set is the *alert threshold* for latency specifically (row above) — dashboards existing doesn't manufacture the missing real-traffic baseline.

**Confirmed live, not just theorized:** during Phase 2 Exit's real Docker container test (gunicorn `--workers 2`), two `/subscribe` calls landed on different worker processes, and a single `/metrics` scrape only showed one of them — live proof of the documented per-worker in-memory counter limitation above. Not a bug to fix at `[MVP]`; a real data point confirming the `[BETA]` Redis-backed-counters item is worth prioritizing once multi-worker production traffic is real.

## Incident Procedure (placeholder — to be replaced with real experience)

1. Check `/health` and `/ready` on the affected service(s).
2. Search logs by `correlation_id` from the reported failure.
3. Check whether the Registry itself is reachable (root-cause many trust-layer failures trace back here).
4. Escalate per the team's current on-call arrangement (not yet formalized — foundation stage).

## To Be Added

- Real incident postmortems, once any occur.
- Specific dashboard links, once Phase 2.6/4.4 stand up real monitoring dashboards.
- On-call rotation and escalation policy, once the team formalizes one.
