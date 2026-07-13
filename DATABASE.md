# Shared Database Layer

Covers the three PostgreSQL databases (Registry, BAP, BPP — Gateway is stateless, per [beckn_gateway_details_v1.1.md](beckn-gateway/beckn_gateway_details_v1.1.md) §4) at foundation stage, per [livetracker1.md](livetracker1.md) Phase 1.5.

## Migrations

Each app uses Django's built-in migration framework (`python manage.py migrate`), with its own isolated migration history under `<app>/core/migrations/`. Verified for real during Phase 1.1/1.3/1.4: fresh migrations applied cleanly against real PostgreSQL 16 containers for all three databases (Registry, BAP, BPP), with zero errors.

No custom migration tooling on top of Django's own — sufficient at this scale, revisit only if multi-service coordinated migrations become a real need at `[BETA]`+.

## Backup Strategy

**`[MVP]`/`[PILOT]`:** daily automated `pg_dump` (custom format, `-F c`) per database, retained for 7 days locally / in object storage once a real Staging environment exists (per [INFRASTRUCTURE.md](INFRASTRUCTURE.md) — no cloud footprint exists yet for Local/Dev, which run entirely on `docker compose`). Scheduling mechanism (cron / CI scheduled job) gets wired in when Staging is provisioned, matching the same "activation trigger" pattern already used for Terraform in `infra/`.

**`[BETA]`+:** continuous WAL archiving / point-in-time recovery, once transaction volume justifies the added operational complexity. Not built now — deliberate scope discipline, not an oversight.

## Restore Procedure — Verified For Real, Not Just Documented

Dry-run performed in Phase 1.5 against the Registry database:

1. Started a fresh PostgreSQL 16 container, ran real Django migrations against it.
2. Inserted a real test record (a Django `User` row) via the ORM.
3. Took a real backup: `pg_dump -F c -f registry_backup.dump`.
4. Started a **second, completely fresh** PostgreSQL container (simulating total loss of the original).
5. Restored: `pg_restore --no-owner --role=registry registry_backup.dump`.
6. Queried the restored database via the Django ORM and confirmed the test record was present and correct.

This is a genuine, executed proof that backup → restore → data-integrity-intact works end to end for this stack — not an assumption. The same procedure applies to BAP and BPP's databases (identical PostgreSQL setup, same Django migration framework).

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
