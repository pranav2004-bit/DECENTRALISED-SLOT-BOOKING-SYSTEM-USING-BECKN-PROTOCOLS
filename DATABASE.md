# Shared Database Layer

Covers the three PostgreSQL databases (Registry, BAP, BPP — Gateway is stateless, per [beckn_gateway_details_v1.1.md](beckn-gateway/beckn_gateway_details_v1.1.md) §4) at foundation stage, per [livetracker1.md](livetracker1.md) Phase 1.5, plus Redis (added below, Phase 3 Exit — a real, previously-unaddressed gap: this document's own title and scope line never covered it, and no other repo document did either, despite BAP/BPP/Gateway all depending on Redis for real functionality since Phase 1.3).

## Migrations

Each app uses Django's built-in migration framework (`python manage.py migrate`), with its own isolated migration history under `<app>/core/migrations/`. Verified for real during Phase 1.1/1.3/1.4: fresh migrations applied cleanly against real PostgreSQL 16 containers for all three databases (Registry, BAP, BPP), with zero errors.

No custom migration tooling on top of Django's own — sufficient at this scale, revisit only if multi-service coordinated migrations become a real need at `[BETA]`+.

## Backup Strategy

**Update (2026-09-02):** Local/dev's 6 databases moved from local `docker compose` Postgres containers to Neon (managed Postgres) — see RUNBOOK.md's "Postgres moved to Neon" note. This does give Local/Dev a real cloud footprint now, ahead of the "once a real Staging environment exists" framing below — Neon provides its own point-in-time recovery and branch-based restore independent of the `pg_dump`/`pg_restore` procedure documented here, which remains the correct approach for whatever hosts Staging/Production once provisioned (not necessarily Neon itself — an open decision, not yet made).

**`[MVP]`/`[PILOT]`:** daily automated `pg_dump` (custom format, `-F c`) per database, retained for 7 days locally / in object storage once a real Staging environment exists (per [INFRASTRUCTURE.md](INFRASTRUCTURE.md)). Scheduling mechanism (cron / CI scheduled job) gets wired in when Staging is provisioned, matching the same "activation trigger" pattern already used for Terraform in `infra/`.

**`[BETA]`+:** continuous WAL archiving / point-in-time recovery, once transaction volume justifies the added operational complexity. Not built now — deliberate scope discipline, not an oversight.

## Restore Procedure — Verified For Real, Not Just Documented

Dry-run performed in Phase 1.5 against the Registry database:

1. Started a fresh PostgreSQL 16 container, ran real Django migrations against it.
2. Inserted a real test record (a Django `User` row) via the ORM.
3. Took a real backup: `pg_dump -F c -f registry_backup.dump`.
4. Started a **second, completely fresh** PostgreSQL container (simulating total loss of the original).
5. Restored: `pg_restore --no-owner --role=registry registry_backup.dump`.
6. Queried the restored database via the Django ORM and confirmed the test record was present and correct.

This is a genuine, executed proof that backup → restore → data-integrity-intact works end to end for this stack — not an assumption. BPP's own schema was independently re-verified the same way in Phase 1.5 Exit (`livetracker2.md`), after its tables had actually changed from Registry's. **BAP's was independently re-verified too, only at Phase 3 Exit (`livetracker2.md`, 2026-07-24)** — a real gap found by direct audit: this section previously claimed the Registry procedure "applies to" BAP "identically" without ever having actually run it there, an unproven-by-analogy claim in a document that otherwise prides itself on "verified for real, not just documented." Closed for real: `pg_dump -F c` of BAP's live database (17 real customer rows, including two accounts created live during that session's own end-to-end walkthrough), restored into a completely fresh, independent `postgres:16-alpine` container via `pg_restore --no-owner --role=bap`, queried directly and confirmed both named accounts present with correct data. Same procedure, same tooling, genuinely re-run per app now — not asserted by extension.

## Seed / Fixture Data Strategy

- **Local/Dev:** Django fixtures (`manage.py loaddata`) or `factory_boy` factories for anything beyond what a fresh migration provides — matches the approach already established in [TESTING.md](TESTING.md) for test data. No production data is ever used to seed local/dev.
- **Staging/Pre-Prod:** minimal, deliberately-fake seed data for manual QA — never copied from Production (which doesn't exist yet at foundation stage, and wouldn't be permissible under DPDP obligations per [protocol_compliance_notes_v1.1.md](protocol_compliance_notes_v1.1.md) §E.4 even once it does).

## Baseline Indexing Strategy

`[MVP]`/`[PILOT]`: rely on Django's automatic indexing (primary keys, `unique=True`, `db_index=True` on foreign keys) — no custom composite indexes yet, since no real query patterns exist until Phase 2+ introduces actual business models (Registry's `Subscription` model, BAP/BPP's domain models). Adding indexes ahead of real query patterns would be premature optimization — the over-engineering this project explicitly avoids.

**`[BETA]`+:** revisit with `EXPLAIN ANALYZE` against real query patterns once Phase 2+ models and real traffic exist. No premature read replicas or partitioning below `[BETA]`, consistent with [INFRASTRUCTURE.md](INFRASTRUCTURE.md).

## Connection Pooling

`CONN_MAX_AGE` configured per app (default 60s, via `DB_CONN_MAX_AGE` env var) — verified working in Registry, BAP, and BPP's `settings.py`. No external pooler (PgBouncer) yet — Django's built-in persistent connections are sufficient at this scale; revisit if real concurrent load demands it.

## Secrets

Database credentials are sourced from environment variables (`DATABASE_URL`), never hardcoded — per [SECURITY.md](SECURITY.md). Local-dev placeholder passwords (`registry:registry`, `bap:bap`, `bpp:bpp`) are intentionally simple and are not real secrets — reviewed and confirmed in `.secrets.baseline` (see [ENVIRONMENTS.md](ENVIRONMENTS.md) for the `detect-secrets` audit process that established this).

## Redis Persistence (real gap found and closed, Phase 3 Exit, 2026-07-24)

Three Redis instances (`bap-cache`, `bpp-cache`, `gateway-cache`, plain `redis:7-alpine`, no `docker-compose.yml` volume mount for any of them) hold real data with genuinely different loss tolerances, never previously distinguished in writing anywhere in this repo:

- **Sessions, `django-redis` cache, rate-limit counters, business metrics counters (`shared/django_observability`), circuit-breaker state (`shared/resilient_http`):** fine to lose. Sessions force a re-login; cache/counters/breaker state all regenerate from the next real request. No fix needed, by design.
- **`ReservationHold` TTL keys (`shared/inventory_core/reservation.py`, BPP's `bpp-cache`):** losing these on a crash just means active holds vanish early — the existing reconciliation sweep (`livetracker2.md` §3.11) and the slot's own real `capacity_remaining` in Postgres are the source of truth either way; a lost hold key is indistinguishable from a naturally-expired one. Acceptable, not a gap.
- **`event_bus` queue + DLQ (`shared/event_bus`, BAP's/BPP's `bap-cache`/`bpp-cache`):** genuinely different — a Redis crash here loses real, not-yet-processed internal events (audit-log writes, metrics increments already fired but their downstream consumers not yet run). This is a real, accepted-at-`[MVP]` reliability gap, not a "fine to lose" one, and had never been written down anywhere before this entry.

**Decision, not a fix:** stock `redis:7-alpine`'s own default RDB snapshotting is already active inside each container, but writes to `/data`, which isn't a persisted volume here either — so today a container recreate loses it regardless. Deliberately **not** adding persistent volumes + AOF for these Redis instances now: doing so would mean deciding on an AOF fsync policy, testing real crash-recovery behavior, and reasoning about replay-on-restart semantics for `event_bus` specifically — a real, non-trivial reliability project of its own, not a documentation fix, and not justified by this project's current dev/single-session traffic (the same "no real concurrent traffic yet" reasoning `OBSERVABILITY.md` already uses to defer distributed tracing). Revisit at `[BETA]`, when real concurrent pilot traffic makes an actual lost-event incident possible rather than theoretical.

**Note — this section is about data loss, not availability.** A separate, distinct question (can callers keep working *while* Redis is unreachable, regardless of what it loses) was found to have its own real gap — a Redis-outage hang that could take 10+ seconds and force-kill the request — root-caused and fixed at Phase 3 Exit (2026-07-25) via `shared/redis_safe.py`'s hard wall-clock deadline. See `RUNBOOK.md`'s "Beauty Business-Layer Resilience & Failure Injection" section for the full investigation and fix.
