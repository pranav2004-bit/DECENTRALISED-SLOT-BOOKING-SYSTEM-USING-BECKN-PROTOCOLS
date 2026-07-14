# Runbook

**Status: stub.** This fills in with real, battle-tested procedures as Phase 2–4 of [livetracker1.md](livetracker1.md) produce actual incidents, onboarding runs, and operational experience. An early, honest stub beats a fabricated "complete" runbook nobody has actually exercised — false confidence in an incident is worse than an acknowledged gap.

## Where to Look

- **Logs:** structured JSON per [OBSERVABILITY.md](OBSERVABILITY.md), searchable by `correlation_id` or Beckn `transaction_id`.
- **Health/readiness:** `GET /health` and `GET /ready` on every backend app.
- **Metrics:** `GET /metrics` (Prometheus format) on every backend app.

## Known Operational Facts (filled in as Phase 2–4 progress)

- The Registry is a single point of trust for the whole network — its unavailability degrades, but per [livetracker1.md](livetracker1.md) Phase 4.2, should not crash BAP/BPP/Gateway (graceful degradation via cached trusted data where the architecture allows).
- Rate limits: Subscribe 10 req/min, Lookup 7,600 req/min (per [protocol_compliance_notes_v1.1.md](protocol_compliance_notes_v1.1.md) §B.6) — a sudden spike in `RATE_LIMITED` errors against the Registry is expected behavior under abuse/misconfiguration, not a Registry bug.
- Key rotation has no dedicated endpoint — it's a re-`/subscribe` call with a new `key_pair` (§B.4). If a rotation appears "stuck," check whether the new Subscribe call actually completed the on_subscribe challenge, not just whether it was sent.

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

No real dashboard exists yet (no monitoring stack stood up — foundation stage). This table is the threshold *design*, ready to wire into Prometheus/Grafana alerting rules once Phase 4.4 stands up real infrastructure to scrape `/metrics` continuously.

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
