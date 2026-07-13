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

## Incident Procedure (placeholder — to be replaced with real experience)

1. Check `/health` and `/ready` on the affected service(s).
2. Search logs by `correlation_id` from the reported failure.
3. Check whether the Registry itself is reachable (root-cause many trust-layer failures trace back here).
4. Escalate per the team's current on-call arrangement (not yet formalized — foundation stage).

## To Be Added

- Real incident postmortems, once any occur.
- Specific dashboard links, once Phase 2.6/4.4 stand up real monitoring dashboards.
- On-call rotation and escalation policy, once the team formalizes one.
