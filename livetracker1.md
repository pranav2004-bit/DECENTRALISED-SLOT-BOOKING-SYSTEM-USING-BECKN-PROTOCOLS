# BECKN Platform — Foundation & Trust Layer Live Tracker

**Scope of this tracker:** Foundation setup for all four applications (Registry, Gateway, BAP, BPP) through full participant onboarding and a proven, tested trust layer. It stops at the point where the network can trust itself — it does **not** cover Beckn business workflows (search/select/init/confirm/fulfillment). Those get their own live trackers later, per component and per integration, once this foundation is signed off.

**Related documents:** [project_details.md](../project_details.md) · [registry/registry_details_v1.1.md](registry/registry_details_v1.1.md) · [beckn-gateway/beckn_gateway_details_v1.1.md](beckn-gateway/beckn_gateway_details_v1.1.md) · [BAP/BAP_details_v1.1.md](BAP/BAP_details_v1.1.md) · [BPP/BPP_details_v1.1.md](BPP/BPP_details_v1.1.md) · [protocol_compliance_notes_v1.1.md](protocol_compliance_notes_v1.1.md)

**Protocol grounding:** Phases 2 and 3 involve real Beckn trust-layer mechanics (registration, cryptographic challenge-response, signing). Several specifics were verified against official sources ([beckn/registry](https://github.com/beckn/registry), [beckn-onix](https://github.com/beckn/beckn-onix), Beckn signing docs) — see [protocol_compliance_notes_v1.1.md](protocol_compliance_notes_v1.1.md) for what's confirmed vs. still unverified. Anything marked UNVERIFIED there must be resolved against beckn-onix / ONDC reference code before the corresponding task below is implemented — do not implement from inference.

---

## How to Use This Tracker

1. Work phases **top to bottom**. Within a phase, work components in the listed order only where a real dependency exists (e.g., Registry must exist before onboarding can begin); otherwise components may proceed in parallel.
2. A task/subtask is checked `[x]` **only** after its implementation is complete **and** its Testing & Validation Gate passes. Never check a box on implementation alone.
3. A phase is closed **only** after its "Phase Exit — Testing & Sign-off" section is fully checked.
4. To resume after a pause: find the first unchecked box, top to bottom. Everything above it is assumed done and trustworthy — do not re-verify unless you have reason to suspect drift (in which case, re-run that item's test gate before continuing).
5. If implementation reveals a decision not captured in a component's `*_details.md`, update that file in the same work session (see §0.10). This tracker records *progress*; the details files record *architecture truth*.
6. Lifecycle tags on each item show when it's required. Do not build ahead of the tagged stage — that's over-engineering. Do not skip a tag at its stage — that's under-engineering.

**Lifecycle tags:** `[MVP]` build now · `[PILOT]` small real-batch of participants · `[BETA]` broader external participants · `[ENT]` enterprise scale. An item with no tag is `[MVP]` by default.

**Test type legend:** `SMOKE` basic runs · `SANITY` narrow correctness check after a change · `FUNC` functional correctness · `POS`/`NEG`/`EDGE` case coverage · `INTEG` cross-service · `E2E` full flow · `REGR` regression · `SEC` security/abuse · `LOAD` concurrency/throughput · `DR` failure-injection/disaster-recovery.

---

## Phase 0 — Program & Engineering Enablement (Cross-Cutting)

**Objective:** Establish the shared engineering substrate once, so all four applications inherit it identically instead of drifting.

### 0.1 Repository & Version Control Strategy
- [x] Decide monorepo vs. per-app repos; document rationale in `ARCHITECTURE.md` — see [ADR-0001](docs/adr/0001-monorepo.md)
- [x] Branching strategy (trunk-based / gitflow) and merge/PR rules defined — see [ADR-0002](docs/adr/0002-trunk-based-development.md)
- [x] Commit conventions + PR template — [CONTRIBUTING.md](CONTRIBUTING.md), [.github/PULL_REQUEST_TEMPLATE.md](.github/PULL_REQUEST_TEMPLATE.md)
- [x] `.gitignore` per app covering secrets, build artifacts, env files — single root `.gitignore` (monorepo) covers all six app paths
- **Test Gate:** `SMOKE` — fresh clone builds per README with no undocumented steps. **PASSED** — `git init` run, `README.md` setup steps followed exactly (`.env.example` → `.env` copy, `docker compose config` validated) with no gaps found.

### 0.2 Environment & Configuration Strategy
- [x] 12-factor config approach: env vars / config service, no hardcoded values — documented in [ENVIRONMENTS.md](ENVIRONMENTS.md)
- [x] `ENVIRONMENTS.md` created: local, dev, staging, prod — parity rules documented; treats staging/preprod/production as three *independently whitelisted* registries per [protocol_compliance_notes_v1.1.md](protocol_compliance_notes_v1.1.md) §7
- [x] `.env.example` template per app (Registry, Gateway, BAP backend+web, BPP backend+web) — all 6 created
- [ ] Config schema validation on startup (fail fast on missing/invalid config) — **not yet implementable**: this is real application behavior (Django settings.py), which doesn't exist until Phase 1. Carried forward to Phase 1.1–1.4 "Configuration management wired to Phase 0.2 strategy."
- **Test Gate:** `SANITY`/`POS` — **partially passed.** Structure and templates verified; the fail-fast runtime behavior itself requires Phase 1 app code and is not yet testable. Not silently assumed done.

### 0.3 Secrets & Key Management Strategy
- [x] Secrets manager selected for MVP (environment-injected + secrets store) `[MVP]` — [SECURITY.md](SECURITY.md)
- [x] HSM/KMS-backed key custody evaluated and documented as future path `[ENT]` — [SECURITY.md](SECURITY.md)
- [x] Key rotation policy documented (manual at MVP; re-`/subscribe` mechanism) — [SECURITY.md](SECURITY.md)
- [x] Secrets-scanning pre-commit/CI hook active on all repos — [.pre-commit-config.yaml](.pre-commit-config.yaml), CI `secrets-scan` job
- **Test Gate:** `SEC` — **PASSED, genuinely verified**: `detect-secrets` tested live against a dummy PEM private key and AWS-style credentials placed in-repo; both correctly detected and would block a commit. Real repo baseline (`.secrets.baseline`) confirmed clean (zero findings). `SANITY` (app reads secret from vault/env, never source) is inherently a Phase 1 concern — no app exists yet to read anything; the never-commit-real-values discipline is structurally enforced now via the hook.

### 0.4 Containerization & Local Orchestration
- [x] Dockerfile per app (Registry, Gateway, BAP backend, BAP web, BPP backend, BPP web) — all 6 created, multi-stage, non-root
- [x] `docker-compose.yml` for full local stack (4 apps + 3 DBs + Redis) — created and syntax-validated (`docker compose config` passes clean)
- [x] Non-root container users, minimal base images — `python:3.12-slim` / `node:20-alpine`, dedicated `app` user in every Dockerfile
- **Test Gate:** `SMOKE` — **NOT YET PASSED, and not claimed as passed.** `docker compose up` cannot bring services to healthy yet — there is no Django/Next.js source code for any app (`manage.py`, `package.json`, etc. don't exist), so `docker build` itself would fail. This is expected sequencing (see `README.md` "Status note"), not a defect in this task's own deliverables, which are otherwise complete and validated. **This Test Gate is explicitly deferred to Phase 1 Exit**, where real app code will let `docker compose up` actually succeed — do not mark this SMOKE gate passed until then.

### 0.5 CI/CD Pipeline Skeleton
- [x] Lint + format check stage — `lint-python`, `lint-node` jobs
- [x] Unit test stage with coverage threshold gate — `test-python`, `test-node` jobs; threshold intentionally not yet blocking (see [TESTING.md](TESTING.md) "Coverage Policy" — deliberate `[MVP]` scope decision, not an oversight)
- [x] Dependency vulnerability scan (SCA) stage — `sca-dependency-scan` job (`pip-audit`, `npm audit`)
- [x] Static code analysis (SAST) stage — `sast-static-analysis` job (`bandit`)
- [x] Container image scan stage — `container-scan` job (Trivy)
- [x] Build/artifact stage — `build` job
- [x] Environment promotion gates documented — [ENVIRONMENTS.md](ENVIRONMENTS.md) "Environment Promotion Gates"
- **Test Gate:** `SMOKE`/`NEG` — **partially verified.** `.github/workflows/ci.yml` parsed and confirmed structurally valid YAML with all 9 jobs present; the secrets-scan job's actual detection logic was verified locally (see 0.3). **Not yet execution-verified on real GitHub Actions infrastructure** — this repo has no remote configured yet, so no real pipeline run has occurred. Confirm on first real push before treating this gate as fully closed.

### 0.6 Coding Standards & Static Quality Gates
- [x] Python/Django style guide + linter config (Registry, Gateway, BAP, BPP backends) — `pyproject.toml` per app, `ruff` + `black`
- [x] TypeScript/Next.js style guide + linter config (BAP web, BPP web) — `.eslintrc.json` + `.prettierrc.json` per app
- [x] Pre-commit hooks wired to linters — [.pre-commit-config.yaml](.pre-commit-config.yaml)
- **Test Gate:** `SANITY` — **PASSED for Python, genuinely verified**: a deliberately malformed file (unused imports, unused variable) was run through `ruff check` and correctly failed with exit code 1 and specific, accurate diagnostics; file removed after the test. **Not yet execution-verified for TypeScript** — no `package.json`/`node_modules` exist yet (Phase 1.3/1.4), so `eslint` can't actually run against real `.ts`/`.tsx` files today; config is written and JSON-valid, runtime behavior confirmed once those apps exist.

### 0.7 Observability Primitives (shared pattern, instantiated per app in Phase 1)
- [x] Structured JSON logging format standardized (fields: timestamp, service, level, correlation_id, message) — [OBSERVABILITY.md](OBSERVABILITY.md)
- [x] Correlation/transaction ID generation + propagation convention defined (`X-Correlation-Id` header) — [OBSERVABILITY.md](OBSERVABILITY.md)
- [x] `/health` (liveness) and `/ready` (readiness) endpoint contract defined — [OBSERVABILITY.md](OBSERVABILITY.md)
- [x] `/metrics` endpoint contract defined (Prometheus-style) — [OBSERVABILITY.md](OBSERVABILITY.md)
- [x] Distributed tracing approach selected — correlation-ID-based at `[MVP]`/`[PILOT]`, W3C Trace Context + OpenTelemetry deferred to `[BETA]` (deliberate, documented scope decision)
- **Test Gate:** `SANITY` — **PASSED, genuinely verified**: [shared/observability/logging_reference.py](shared/observability/logging_reference.py) actually runs and was programmatically checked — every emitted log line is valid JSON containing all five required fields, correlation ID correctly threaded through and correctly `null` for non-request-scoped lines.

### 0.8 Testing Infrastructure Baseline
- [x] Unit test framework per stack (pytest for Django apps, Vitest for Next.js apps — see [TESTING.md](TESTING.md) for the Jest-vs-Vitest decision) — chosen and documented
- [x] Test database strategy (isolated, ephemeral, fixture/factory-based via `factory_boy`) — documented in [TESTING.md](TESTING.md)
- [x] Inter-service mocking/stubbing approach for integration tests (`responses` library at the HTTP boundary) — documented and verified working
- [x] Contract-testing approach defined for Beckn/JSON schema conformance — [shared/testing/contract_schemas/](shared/testing/contract_schemas/), verified working
- [x] Load-testing tool selected (k6) — scaffolded choice only, not yet exercised at scale (correct for `[MVP]`)
- [x] Baseline security testing tool selected (SCA/SAST in CI now; OWASP ZAP DAST baseline deferred to Phase 2.5/4.3 once a real service exists to scan) — documented in [TESTING.md](TESTING.md)
- **Test Gate:** `SMOKE` — **PASSED, genuinely verified**: [shared/testing/test_contract_reference.py](shared/testing/test_contract_reference.py) runs 4 real tests covering all three types (unit, contract via JSON Schema, integration via mocked HTTP) — all pass. One real bug was caught and fixed during this verification: the integration-test example initially used raw `urllib` instead of `requests`, which `responses` doesn't intercept — corrected and re-verified.

### 0.9 Infrastructure-as-Code & Cost Governance
- [x] IaC tool selected (Terraform) for reproducible dev/staging environments `[MVP]` — [INFRASTRUCTURE.md](INFRASTRUCTURE.md)
- [x] Resource tagging convention (project, component, environment, owner, lifecycle_stage) — [INFRASTRUCTURE.md](INFRASTRUCTURE.md), enforced as validated Terraform variables in [infra/variables.tf](infra/variables.tf)
- [ ] Non-prod environment teardown/scheduling automation to control idle cost — **strategy documented, automation not built**: deliberately deferred, since no real cloud resources exist yet to tear down (Local/Dev run entirely on `docker compose`). See [INFRASTRUCTURE.md](INFRASTRUCTURE.md) "Current Status" for the activation trigger (Phase 3 Staging onboarding).
- [x] Right-sizing baseline for dev/staging tiers documented (no prod-grade sizing pre-Beta) — [INFRASTRUCTURE.md](INFRASTRUCTURE.md)
- **Test Gate:** `SANITY` — **partially passed.** `infra/variables.tf` and `infra/versions.tf` confirmed as syntactically valid HCL (parsed successfully). A real `terraform plan` cannot run — there is no provider block or cloud account configured yet, by deliberate design (see INFRASTRUCTURE.md). Not claimed as fully passed; revisit when `infra/` is activated.

### 0.10 Documentation Baseline
- [x] `ARCHITECTURE.md` (system-level, links all four component detail docs) — created
- [x] `SECURITY.md` (threat model summary, reporting process) — created
- [x] `API_CONVENTIONS.md` (error schema, idempotency-key convention, versioning rule) — created
- [x] `RUNBOOK.md` stub (on-call basics, where logs/metrics live — filled in as ops muscle grows) — created, honestly labeled as a stub
- [x] ADR (Architecture Decision Record) folder + template created — [docs/adr/](docs/adr/), template + 2 real ADRs (0001, 0002) already using it
- **Test Gate:** `SANITY` — self-reviewed carefully (every cross-reference checked against actual file paths); a human peer review is still recommended before treating this as fully closed, since no second reviewer has looked at it yet.

### Phase 0 Exit — Testing & Sign-off
- [x] All Phase 0 test gates above pass **or are explicitly, honestly marked as partial/deferred with a stated reason** — none silently skipped. Fully closed: 0.1, 0.3 (SEC half), 0.6 (Python half), 0.7, 0.8, 0.10. Partial/deferred with explicit carry-forward: 0.2 (runtime config validation → Phase 1), 0.4 (compose-up-healthy → Phase 1 Exit), 0.5 (real CI execution → first push), 0.6 (TS lint runtime → Phase 1.3/1.4), 0.9 (terraform plan → Phase 3 Staging trigger).
- [ ] `INTEG` — full local stack (`docker compose up`) runs all four apps + DBs + cache with health checks green — **blocked on Phase 1 app code, as documented above. Not yet attempted for real; will only genuinely pass once Phase 1 lands.**
- [x] Sign-off recorded — reviewed by Claude (AI pair engineer) 2026-07-13; human review of this Phase 0 sign-off still recommended before treating Phase 0 as fully closed, consistent with the 0.10 note above.

---

## Phase 1 — Application Foundation

**Objective:** Every application stands up independently, wired to the Phase 0 primitives, with no Beckn network communication or trust yet.

### 1.0 Shared Django Observability App (not originally a separate line item — added because Registry/Gateway/BAP/BPP all needed identical health/ready/metrics/logging/exception-handling code; built once in `shared/django_observability/` instead of duplicated four times)
- [x] `/health`, `/ready`, `/metrics` views + JSON logging formatter + correlation-ID middleware + global exception-handling middleware — all genuinely tested (11 unit tests, plus live curl verification against real running containers in every app below)

### 1.1 Registry Foundation
- [x] Project structure (Django project/apps layout) — `registry/registry/` (project) + `registry/core/` (app)
- [x] Application skeleton boots with Phase 0.7 logging/health/metrics wired in — verified live: real container reports Docker `healthy`, `/health` and `/ready` curled successfully
- [x] Configuration management wired to Phase 0.2 strategy — `django-environ`, fail-fast verified for real (removing `DJANGO_SECRET_KEY` raises `ImproperlyConfigured` with the exact missing variable named)
- [x] Shared utility service stubs: Cryptography, Validation, Configuration, Logging (per [registry_details_v1.1.md](registry/registry_details_v1.1.md) §12) — Validation is a real working JSON-Schema validator (not a stub) against the confirmed Subscribe schema; Cryptography intentionally raises `NotImplementedError` pointing to Phase 2.2/2.3, per the tracker's own scoping
- [x] Database connectivity (PostgreSQL) with connection pooling configured — `CONN_MAX_AGE`, verified via real migrations + `/ready` database check passing
- [x] Basic REST API framework (routing, standardized error-response schema from 0.10, request-ID middleware) — DRF installed, `django_observability.errors.error_response()` helper matches [API_CONVENTIONS.md](API_CONVENTIONS.md) exactly
- [x] Global exception handling → maps to standardized error schema, no stack traces leaked — verified live in **both** `DEBUG=True` (shows exception detail) and `DEBUG=False` (generic message only) branches, via a real deliberately-broken view
- [x] Signature verification middleware scaffolded (capability only; exercised in Phase 2/3) — `core/crypto.py` stub functions matching the confirmed header syntax, correctly raise `NotImplementedError` until Phase 2.3
- **Test Gate:** **PASSED, genuinely verified.** 11 automated regression tests (95% coverage) + live container run against real Postgres: `/health` 200, `/ready` 200 with `database: ok`, exception handling correct in both debug states, Docker's own HEALTHCHECK reports `healthy`.

### 1.2 Gateway Foundation
- [x] Project structure
- [x] Application skeleton boots with logging/health/metrics wired in — verified live, container `healthy`
- [x] Configuration management
- [x] Shared utility service stubs: Cryptography, Validation, Registry Client, HTTP Client (with timeout+retry+circuit-breaker defaults), Configuration, Logging, Cache `[BETA]` (per [beckn_gateway_details_v1.1.md](beckn-gateway/beckn_gateway_details_v1.1.md) §9) — HTTP Client is **real, working infrastructure**, not a stub: [shared/resilient_http/](shared/resilient_http/) implements a genuine 3-state circuit breaker (closed/open/half-open) + retry-with-backoff, 8 tests passing against real simulated failures
- [x] Note for later signing middleware: Gateway signs outbound calls via `Proxy-Authorization`, not `Authorization` — a distinct header from every other participant-to-participant call ([protocol_compliance_notes_v1.1.md](protocol_compliance_notes_v1.1.md) §C.3). Don't reuse BAP/BPP/Registry signing middleware unmodified.
- [x] Basic REST API framework + standardized error schema
- [x] Global exception handling
- [x] No database — confirm statelessness holds (no accidental persistence introduced) — verified: no `DATABASES` setting exists, `/ready` correctly reports an empty `checks: {}` (nothing to check, not a false failure)
- **Test Gate:** **PASSED, genuinely verified.** 8 automated tests + live container run: `/health` 200, `/ready` 200 with empty checks (correct statelessness), Docker HEALTHCHECK reports `healthy`.

### 1.3 BAP Foundation
- [x] Project structure (backend + `BAP/web` Next.js app)
- [x] Backend application skeleton boots with logging/health/metrics — verified live, container `healthy`
- [x] Configuration management
- [x] Shared utility service stubs: Cryptography, Validation, Registry Client, HTTP Client (resilience defaults), Configuration, Logging, Auth, Cache (per [BAP_details_v1.1.md](BAP/BAP_details_v1.1.md) §10)
- [x] Database connectivity (PostgreSQL) with pooling — verified via real migrations + `/ready` check
- [x] Cache connectivity (Redis) — verified via `/ready` cache check passing against a real Redis container
- [x] Internal event infrastructure (EDA bus) with a Dead Letter Queue for undeliverable/failed internal events — **real, working infrastructure**: [shared/event_bus/](shared/event_bus/), Redis-list-backed, genuinely tested against a live Redis (publish/consume round trip, DLQ receives a deliberately-failed event, queue-length tracking) — 5 tests passing
- [x] Basic buyer web application skeleton (Next.js + TypeScript + Tailwind): routing shell, environment config, API client with timeout/retry, custom 404/500 error pages, mobile-first responsive baseline layout — all verified live: real build, real container, `/health` + homepage + custom-404 all curled successfully
- [x] Basic backend framework: REST routing, standardized error schema, idempotency-key support in request pipeline
- **Test Gate:** **PASSED, genuinely verified.** Backend: 9 tests (95%→82% coverage incl. real event-bus/DLQ exercise) + live container against real Postgres+Redis (`/ready` shows `database: ok, cache: ok`). Web: real `npm run build` + 5 Vitest tests for the API client's timeout/retry logic + live standalone container serving `/health`, `/`, and a verified custom 404 page.

### 1.4 BPP Foundation
- [x] Project structure (backend + `BPP/web` Next.js app)
- [x] Backend application skeleton boots with logging/health/metrics — verified live, container `healthy`
- [x] Configuration management
- [x] Shared utility service stubs: Authentication, Authorization, Validation, Cryptography, Registry Client, HTTP Client (resilience defaults), Configuration, Logging (per [BPP_details_v1.1.md](BPP/BPP_details_v1.1.md) §10) — Authentication and Authorization built as two distinct stub services (matching BPP's spec exactly, unlike BAP's combined service)
- [x] Database connectivity (PostgreSQL) with pooling — verified
- [x] Cache connectivity (Redis) — verified
- [x] Internal event infrastructure (EDA bus) with Dead Letter Queue — same shared, tested `event_bus` module as BAP; DLQ-on-failure verified again for BPP specifically
- [x] Basic provider web application skeleton (Next.js + TypeScript + Tailwind): routing shell, environment config, API client with timeout/retry, custom 404/500 error pages, mobile-first responsive baseline layout — verified live
- [x] Basic backend framework: REST routing, standardized error schema, idempotency-key support
- **Test Gate:** **PASSED, genuinely verified.** Backend: 10 tests + live container against real Postgres+Redis. Web: real build + 5 Vitest tests + live standalone container, `/health`/homepage/404 all confirmed. One real environment bug found and fixed along the way — see Change Log (Windows/WSL2 port-conflict false failure, root-caused, not just retried).

### 1.5 Shared Database Layer
- [x] Registry DB provisioned; migration tool configured (Django migrations); baseline schema versioned — real migrations applied cleanly against live PostgreSQL 16
- [x] BAP DB provisioned; migration tool configured; baseline schema versioned — same
- [x] BPP DB provisioned; migration tool configured; baseline schema versioned — same
- [x] Backup strategy defined and scheduled for all three (even at MVP: daily automated snapshot minimum) — documented in [DATABASE.md](DATABASE.md); actual scheduling automation deferred to Staging provisioning (consistent with [INFRASTRUCTURE.md](INFRASTRUCTURE.md)'s existing activation-trigger pattern — no cloud footprint exists yet to schedule against)
- [x] Restore procedure documented and dry-run tested once — **genuinely executed, not just written**: real `pg_dump` of a database with real test data, restored into a completely fresh PostgreSQL container, data integrity confirmed via Django ORM query. Full account in [DATABASE.md](DATABASE.md).
- [x] Seed/fixture data strategy for local & staging environments — documented in [DATABASE.md](DATABASE.md)
- [x] Baseline indexing strategy documented (no premature read replicas — deferred `[BETA]`) — documented in [DATABASE.md](DATABASE.md); deliberately relies on Django's automatic indexing until real Phase 2+ query patterns exist
- **Test Gate:** **PASSED, genuinely verified.** `SANITY` migrations apply cleanly — confirmed for all three DBs. `DR` restore-from-backup — actually performed once against Registry, full data-integrity proof. `SEC` DB credentials sourced from `DATABASE_URL` env var, never hardcoded — confirmed via `.secrets.baseline` audit (see Change Log).

### Phase 1 Exit — Testing & Sign-off
- [x] All four applications run independently via `docker compose up` with green health checks — **all 11 containers (4 apps + 3 DBs + 2 caches + implicit) reported `healthy` in a real full-stack `docker compose up`.** This is the exact gate Phase 0.4 could not pass (no app code existed then) — now genuinely closed.
- [x] All three databases connected, migrated, seeded, backed up once — migrations verified per-app in 1.1/1.3/1.4; backup/restore genuinely proven in 1.5 (Registry); BAP/BPP use the identical mechanism
- [x] `INTEG` — no cross-service calls exist yet; confirmed no accidental coupling introduced — verified: each app's `/ready` only checks its own direct dependencies (DB/cache), no app calls another app's API yet, matching Phase 1's explicit no-Beckn-communication scope
- [x] `REGR` — Phase 0 gates re-verified still green after Phase 1 changes — `ruff check` clean across all 4 Python apps + `shared/`; `detect-secrets` baseline re-verified clean (all findings are the already-audited local-dev placeholder credentials, `is_secret: false`)
- [x] `SEC` — dependency + container scans clean (or exceptions documented/accepted) — Python: no new findings. Node (BAP/web, BPP/web): `npm audit` shows 7 moderate/high/critical findings, all in **dev-tooling transitive dependencies** (`esbuild` via `vitest`, `postcss` via `next`) — not runtime code paths. A fix was attempted post-Phase-1 and deliberately not kept: `npm audit fix --force` "resolved" it by silently downgrading `next` from `16.2.10` to `^9.3.3` (a 2019-era release) — caught via `git diff` before it shipped, reverted immediately. A safer targeted upgrade (`vitest`/`@vitejs/plugin-react` only, leaving `next` untouched) then hit an unresolvable peer-dependency conflict (`vite@^8.0.0` vs. the installed `vite@8.1.4`). Decision: leave the 7 findings as accepted, documented, dev-tooling-only risk rather than force either unsafe path — verified the app still builds and all tests pass after reverting. Revisit if/when Next.js or the vitest/vite ecosystem ships a compatible non-breaking patch.
- [x] Sign-off recorded — reviewed by Claude (AI pair engineer), 2026-07-13. Four real, non-trivial bugs were found and fixed during this phase (not glossed over) — see Change Log below for the full list. Human review of this Phase 1 sign-off is still recommended before treating it as fully closed, same standing note as Phase 0.

---

## Phase 2 — Registry Implementation (Trust & Identity Service)

**Objective:** Build the complete Trust & Identity service every participant depends on.

### 2.0 Protocol Conformance Confirmation Run (validation, not discovery — core schemas already sourced from the actual OpenAPI spec)
- [ ] Clone/review [ONDC-Official/developer-docs](https://github.com/ONDC-Official/developer-docs) Python crypto reference toolkit (signing, verification, encryption, decryption, on_subscribe) — use as the implementation reference, don't build from scratch
- [ ] Register one throwaway test participant against the real ONDC **staging** registry sandbox using the confirmed Subscribe payload shape ([protocol_compliance_notes_v1.1.md](protocol_compliance_notes_v1.1.md) §B.3); confirm live response matches `{"status":"UNDER_SUBSCRIPTION"}` (§A.1)
- [ ] Confirm a live Lookup response matches the `Subscription[]` shape (§A.2); note any field-naming drift (e.g. `encr_public_key` vs. `encryption_public_key`, §A.2)
- [ ] Re-submit Subscribe for that same test participant with a new `key_pair` to confirm rotation behavior matches §B.4 (no dedicated rotation endpoint)
- [ ] Record any drift from `protocol_compliance_notes_v1.1.md` before proceeding; adjust 2.1–2.3 tasks below only if live behavior contradicts the documented spec
- **Test Gate:** `SANITY` — a teammate can read `protocol_compliance_notes_v1.1.md` and implement Subscribe/Lookup/on_subscribe/rotation without guessing any field name, endpoint, or flow step; this confirmation run only needs to catch drift between spec and live behavior, not discover unknowns

### 2.1 Participant Registration
- [ ] Subscribe API — implement against the **confirmed nested payload shape** (`context.operation.ops_no`, `message.request_id/timestamp/entity.{gst, pan, signatory, contact, key_pair}`, `message.network_participant[]`), per [protocol_compliance_notes_v1.1.md](protocol_compliance_notes_v1.1.md) §11 — do not implement a flat field list, the real payload is nested
- [ ] `ops_no` handling: `1` = BAP registration, `2` = BPP registration, `4` = both; `3`/`5` explicitly rejected as deprecated (Seller-On-Record, obsolete)
- [ ] Registrar approval step modeled (manual/governance gate before `INITIATED`, even if the "Registrar" is a simple admin action at `[MVP]`) — do not treat Subscribe as fully self-service
- [ ] Lookup API (participant/network search — required; Gateway depends on this to discover BPPs) — request shape confirmed (filter object on `country/domain/type/city/subscriber_id`, notes §15); response shape confirmed via Phase 2.0 sandbox spike before finalizing the parser
- [ ] Update / Lifecycle-status API (`create → update → activate → deactivate`, per [registry_details_v1.1.md](registry/registry_details_v1.1.md) §3.2)
- [ ] Participant status model implemented (confirmed **core Beckn protocol** enum, directly from `registry.yaml` — per [protocol_compliance_notes_v1.1.md](protocol_compliance_notes_v1.1.md) §A.3): `INITIATED → UNDER_SUBSCRIPTION → SUBSCRIBED`, with `INVALID_SSL` and `UNSUBSCRIBED` as terminal/error states
- [ ] Environment-scoped registries: staging / pre-prod / production modeled as separate registry deployments with independent whitelisting, not one shared instance (per protocol_compliance_notes_v1.1.md §7) — reflect this in Phase 0.2 environment strategy
- [ ] Rate limiting on Subscribe (10 req/min) and Lookup (7,600 req/min) — concrete ONDC limits, not placeholders (per protocol_compliance_notes_v1.1.md §12)
- [ ] Idempotency on Subscribe (duplicate calls with same payload don't create duplicate participants)
- **Test Gate:** `FUNC` full CRUD lifecycle · `POS` valid subscribe → lookup returns it · `NEG` duplicate subscribe handled idempotently, invalid payload rejected cleanly; exercise real NACK reasons from protocol_compliance_notes_v1.1.md §13 (unwhitelisted subscriber_id, duplicate subscriber_id, malformed timestamp) · `EDGE` lookup for non-existent participant, deactivate-then-lookup · `SEC` rate limit triggers on flood attempt at the real thresholds above · `LOAD` light concurrency smoke on Subscribe/Lookup at those thresholds

### 2.2 Participant Verification
- [ ] Challenge generation (on_subscribe encrypted-challenge mechanism, implemented per 2.0 findings — not from inference)
- [ ] Challenge verification (decrypted/signed response validated against stored public keys)
- [ ] Participant validation status transition wired to verification outcome (`INITIATED` → `SUBSCRIBED`)
- [ ] Replay-attack protection (challenge single-use, time-bound)
- **Test Gate:** `FUNC` full challenge/response round trip · `POS` valid signature accepted · `NEG` invalid/expired/reused challenge rejected · `SEC` replay attempt blocked, tampered payload rejected

### 2.3 Cryptography
- [ ] Registry's own key pair generation (for signing Registry responses / TLS identity)
- [ ] Dual public-key management per participant: `signing_public_key` (Ed25519) and `encryption_public_key` (X25519) — store, retrieve, associate with participant record
- [ ] Participant public key storage (encrypted at rest)
- [ ] Signature verification support exposed as reusable service: Ed25519 signature over signing string, BLAKE-512 body digest, per [protocol_compliance_notes_v1.1.md](protocol_compliance_notes_v1.1.md) §4
- [ ] Key rotation procedure exercised at least once (manual acceptable at `[MVP]`)
- **Test Gate:** `FUNC` sign/verify round trip using the confirmed Ed25519+BLAKE-512 scheme · `NEG` verification fails on wrong key/tampered data · `SEC` private key material never appears in logs/responses; encryption-at-rest verified

### 2.4 Registry Data Management
- [ ] Participant identity schema finalized (per §8 of registry_details_v1.1.md — identity, subscriber info, network identifiers, domain, public keys, verification status, metadata)
- [ ] Confirm no business data fields introduced (no catalogs/orders/payments — scope discipline check)
- [ ] Audit log of all registration/verification/status-change events (who/what/when, immutable)
- **Test Gate:** `SANITY` schema matches documented data ownership boundaries exactly · `SEC` audit log entries are tamper-evident (append-only) and cover every state transition

### 2.5 Registry Security Hardening
- [ ] Network governance model for who may call Subscribe (staging vs. prod network policy documented)
- [ ] Input validation on all API surfaces (schema-level rejection of malformed Beckn payloads)
- [ ] Standard security headers, CORS policy applied
- [ ] Basic bot/abuse detection on public-facing endpoints
- **Test Gate:** `SEC` — basic pentest pass: injection attempts, oversized payloads, malformed JSON, header spoofing all rejected safely · `NEG` unauthorized network attempts to subscribe are rejected per governance policy

### 2.6 Registry Observability & Ops
- [ ] Structured logs + correlation IDs wired per Phase 0.7 pattern
- [ ] Metrics: subscribe/lookup/verify rates, latency, error rates
- [ ] Alerting thresholds defined for Registry unavailability (it's a single point of trust for the whole network)
- **Test Gate:** `SANITY` dashboards show live traffic from test gate 2.1–2.5 runs · `DR` simulate Registry restart mid-traffic, confirm graceful recovery and no data loss

### Phase 2 Exit — Testing & Sign-off
- [ ] `E2E` — register a test participant, verify it, activate it, look it up, deactivate it, confirm each transition logged
- [ ] `REGR` — Phase 0/1 gates re-verified green
- [ ] `LOAD` — Registry sustains expected onboarding-burst concurrency without error-rate spike (light, MVP-scale target, not enterprise scale)
- [ ] `SEC` — hardening pass (2.5) fully green
- [ ] Sign-off recorded

---

## Phase 3 — Participant Onboarding & Trust Establishment

**Objective:** Onboard BAP, BPP, and Gateway into the network as trusted, cryptographically verified participants.

Each onboarding flow below follows the confirmed ONDC sequence (protocol_compliance_notes_v1.1.md §9–10): key generation → domain verification → portal whitelisting → subscribe → on_subscribe challenge → `SUBSCRIBED`. Skipping the domain-verification step is a common real-world onboarding failure mode — do not treat it as optional.

### 3.1 BAP Onboarding
- [ ] Generate **signing key pair** (Ed25519) and **encryption key pair** (X25519) — two distinct pairs, not one
- [ ] Store both private keys securely (per Phase 0.3 secrets strategy — never in source/config)
- [ ] Provision valid FQDN + SSL certificate for the BAP's subscriber domain
- [ ] Sign a `request_id` with the signing private key and host it at `ondc-site-verification.html` on the BAP's domain (domain-ownership proof)
- [ ] Request environment whitelisting via the Network Participant Portal per environment (staging/preprod/prod) — expect a manual review gate (`[MVP]`: even a simple internal approval step is acceptable, don't auto-approve)
- [ ] Register (Subscribe) with the target environment's Registry, submitting `subscriber_id, callback_url, signing_public_key, encryption_public_key, unique_key_id` plus generic-Beckn fields
- [ ] Reach `INITIATED` on successful whitelisting + payload validation
- [ ] Implement `/on_subscribe` endpoint: decrypt incoming challenge using the shared key derived from the BAP's encryption private key + Registry's public key, respond with the decrypted answer
- [ ] Reach `UNDER_SUBSCRIPTION` → `SUBSCRIBED` on successful challenge response
- **Test Gate:** `E2E` full onboarding flow through all six real stages above · `NEG` onboarding with malformed/missing key, missing domain-verification file, or wrong challenge answer fails with the correct NACK reason (protocol_compliance_notes_v1.1.md §13) and is retryable · `SEC` neither private key is ever transmitted to Registry or logged

### 3.2 BPP Onboarding
- [ ] Confirm exact ONDC `domain` code(s) to use for healthcare and automotive service-booking before submitting Subscribe — **not yet confirmed** ([protocol_compliance_notes_v1.1.md](protocol_compliance_notes_v1.1.md) "Remaining Open Items"); beauty maps reasonably to an existing Beauty & Personal Care domain, but healthcare and automotive may need an adjacent-domain mapping or ONDC's domain-onboarding process. Do not guess a domain code and submit it.
- [ ] Same sequence as 3.1, applied to BPP: dual key pairs, domain verification, portal whitelisting, subscribe, on_subscribe challenge handling, reach `SUBSCRIBED`
- **Test Gate:** same as 3.1, applied to BPP, plus `SANITY` — confirmed domain code used matches an ONDC-recognized value, not an assumed/guessed one

### 3.3 Gateway Onboarding
- [ ] Same sequence as 3.1, applied to Gateway: dual key pairs, domain verification, portal whitelisting, subscribe, on_subscribe challenge handling, reach `SUBSCRIBED`
- **Test Gate:** same as 3.1, applied to Gateway

### 3.4 Trust Establishment & Network Governance
- [ ] Public key registration confirmed retrievable by all participants via Registry Lookup
- [ ] Cross-participant verification: Gateway can fetch and validate BAP's and BPP's public keys; BAP/BPP can validate Registry's identity
- [ ] Deregistration/rollback procedure exercised (what happens if onboarding fails partway)
- [ ] Key rotation exercised once for a live onboarded participant, without breaking trust — implemented per the Phase 2.0 sandbox-confirmed rotation behavior (re-Subscribe with new `key_pair` before `valid_until`, per [protocol_compliance_notes_v1.1.md](protocol_compliance_notes_v1.1.md) §12)
- **Test Gate:** `INTEG` cross-participant key fetch + signature validation succeeds for all three participants · `NEG` rollback of a failed mid-onboarding participant leaves no orphaned/partial state · `SEC` impersonation attempt (wrong key presented) is rejected at every participant boundary

### Phase 3 Exit — Testing & Sign-off
- [ ] All three participants (BAP, BPP, Gateway) hold `SUBSCRIBED` status in Registry
- [ ] Each participant possesses and correctly uses its own key pair
- [ ] `REGR` — Phase 0/1/2 gates re-verified green
- [ ] **Scope note recorded, not yet actioned:** reaching `SUBSCRIBED` establishes network trust but is **not** sufficient for production traffic — Pramaan certification, the ONDC Network Participant Agreement, and GRO/IGM designation are separate non-technical gates, tracked explicitly in Phase 4.4, not assumed complete here (per [protocol_compliance_notes_v1.1.md](protocol_compliance_notes_v1.1.md) §E)
- [ ] Sign-off recorded

---

## Phase 4 — Cross-Participant Integration & Network Readiness

**Objective:** Prove the trust layer works as a *network*, not just as four individually-correct services, and that it survives realistic failure modes before business workflows are layered on top.

### 4.1 End-to-End Trust Chain Verification
- [ ] BAP → Registry lookup → discovers Gateway's public identity
- [ ] Gateway → Registry lookup → discovers BPP's public identity
- [ ] Full chain dry run: BAP signs a request, Gateway verifies BAP's signature via Registry-sourced key, confirms it can route toward BPP — use the confirmed `/search` → `/on_search` context/envelope shape ([protocol_compliance_notes_v1.1.md](protocol_compliance_notes_v1.1.md) §D) as the plumbing test payload, without implementing real `intent`/`catalog` business logic yet
- **Test Gate:** `E2E` full chain trust verification succeeds · `NEG` tampered signature anywhere in the chain is caught at the first verification point, not downstream

### 4.2 Network Resilience & Failure Injection
- [ ] Registry unavailable mid-flow: Gateway/BAP/BPP degrade gracefully using cached trusted data where architecture allows, and fail closed (not open) where it doesn't
- [ ] Timeout/retry behavior verified on all inter-service HTTP clients (per Phase 0.7/1.x resilience defaults)
- [ ] Circuit breaker trips correctly under sustained downstream failure and recovers on restoration
- [ ] DLQ inspected after induced internal event failures in BAP/BPP — no silent event loss
- **Test Gate:** `DR` kill Registry, verify defined degradation behavior, not crashes · `DR` kill Gateway, verify BAP/BPP handle it per resilience contract · `LOAD` light concurrent onboarding-burst simulation, confirm no trust-state corruption

### 4.3 Security Penetration Pass (Trust Layer)
- [ ] Attempted participant impersonation across all boundaries (Registry, Gateway, BAP, BPP)
- [ ] Attempted replay of old signed requests
- [ ] Attempted unauthorized Registry writes (Subscribe/Update without valid credentials)
- [ ] Dependency/container/SAST scans re-run clean across all four apps
- **Test Gate:** `SEC` — all attack attempts above are rejected with correct audit trail entries; no attempt succeeds or goes unlogged

### 4.4 Production Readiness Review & Sign-off
- [ ] Runbook (`RUNBOOK.md`) updated with real onboarding/incident procedures observed during Phase 2–4
- [ ] All `*_details.md` component files reviewed and updated to reflect any implementation-driven decisions
- [ ] Monitoring dashboards + alert thresholds confirmed live for all four services
- [ ] Cost/resource review against Phase 0.9 governance (no untagged or oversized resources)
- [ ] Full regression pass: Phases 0 through 4 test gates re-run green in one continuous run
- [ ] **Compliance & certification checklist** (per [protocol_compliance_notes_v1.1.md](protocol_compliance_notes_v1.1.md) §E — organizational/legal, not engineering tasks, but blocking for real production traffic):
  - [ ] Pramaan certification: mandatory flows completed, required tests passed, Integration Report obtained, per applicable domain test suite (confirm which domain suite applies to healthcare/automotive/beauty, or whether a custom-domain path is needed)
  - [ ] Production-environment Probationary Period requirement understood and scheduled (Pramaan pass alone is not Go-Live)
  - [ ] ONDC Network Participant Agreement executed (legal/contractual, not code)
  - [ ] Grievance Redressal Officer (GRO) designated and details shared with ONDC
  - [ ] IGM readiness acknowledged as a known future requirement (`/report`/`/on_issue` implementation is business-workflow scope, deliberately deferred to the future business-capability tracker — not silently forgotten)
  - [ ] DPDP Act data-handling review completed for whatever personal data this system will store (consent management, access/correction/grievance rights, safeguards proportionate to data sensitivity)
- [ ] Final sign-off recorded — **foundation and trust layer declared production-ready; system is now ready for business-capability live trackers (Beckn workflows) to begin**

---

## Change Log

| Date | Change | By |
|---|---|---|
| 2026-07-13 | Initial tracker created from refined foundation roadmap (Phases 0–4) | — |
| 2026-07-13 | Corrected Phase 2/3 against verified official Beckn sources: dual key-pair model (signing + encryption), confirmed Subscribe API fields, Registrar approval gate, Ed25519+BLAKE-512 signing scheme, removed unverified status enum, added Phase 2.0 protocol conformance gate. See [protocol_compliance_notes_v1.1.md](protocol_compliance_notes_v1.1.md) | — |
| 2026-07-13 | Verified ONDC-specific onboarding mechanics against ONDC's own developer docs: added missing domain-ownership verification step (`ondc-site-verification.html`), three independently-whitelisted registry environments, confirmed on_subscribe challenge mechanism, confirmed ONDC status enum, concrete rate limits (Subscribe 10/min, Lookup 7600/min) | — |
| 2026-07-13 | Closed remaining protocol gaps: confirmed real nested Subscribe payload shape (GST/PAN entity fields, `ops_no` table, `key_pair` validity windows), confirmed Lookup request shape. Converted Phase 2.0 from open-ended source review into a bounded live-sandbox spike against ONDC staging registry to close the last two narrow gaps (Lookup response field names, key-rotation operation). Tracker is now implementation-ready end to end. | — |
| 2026-07-13 | Pulled the actual core OpenAPI spec files (`registry.yaml`, `transaction.yaml`) from beckn/protocol-specifications — highest-confidence source used yet. Corrected status enum from "ONDC-specific" to confirmed core protocol; confirmed Lookup response schema fully (no sandbox spike needed for it anymore); confirmed no dedicated key-rotation endpoint exists; confirmed full Authorization header syntax and the Gateway-specific `Proxy-Authorization` distinction; confirmed the 18-path transaction API contract Gateway routes between BAP/BPP. Phase 2.0 downgraded from discovery spike to a confirmation/drift-check run. See protocol_compliance_notes_v1.1.md §A–D. | — |
| 2026-07-13 | Identified and closed a previously-missing dimension: compliance/certification (distinct from API conformance). Confirmed Pramaan certification as a mandatory 4-stage gate before ONDC production Go-Live (with a Probationary Period beyond certification), the ONDC Network Participant Agreement as a legal prerequisite, IGM (Issue & Grievance Management) as a legally-mandated protocol extension with a designated GRO requirement, and DPDP Act data-handling obligations. Added an explicit compliance checklist to Phase 4.4 and a scope note to Phase 3 exit clarifying that `SUBSCRIBED` status alone is not sufficient for production traffic. See protocol_compliance_notes_v1.1.md §E. | — |
| 2026-07-13 | **Phase 0 implemented and closed.** Git repo initialized; all 10 tasks (0.1–0.10) completed with genuine verification, not just written config: `detect-secrets` tested live against real dummy credentials, `ruff` tested against real malformed code, the observability logging reference actually runs and was programmatically checked, all three test types (unit/contract/integration) in the testing baseline actually run green (catching and fixing one real bug in the integration-test example along the way), Docker Compose and Terraform HCL syntax-validated, CI workflow YAML structurally validated. Two ADRs recorded (monorepo, trunk-based dev). Honestly flagged as partial/deferred rather than falsely checked: runtime config validation, `docker compose up` healthy, real GitHub Actions execution, TypeScript lint runtime, and `terraform plan` — all structurally blocked on Phase 1 app code or deliberately deferred real infrastructure, each with an explicit carry-forward note rather than a silently skipped gate. New root-level artifacts: `README.md`, `ARCHITECTURE.md`, `CONTRIBUTING.md`, `SECURITY.md`, `ENVIRONMENTS.md`, `OBSERVABILITY.md`, `TESTING.md`, `INFRASTRUCTURE.md`, `API_CONVENTIONS.md`, `RUNBOOK.md`, `docs/adr/`, `.github/`, `.pre-commit-config.yaml`, `.secrets.baseline`, `docker-compose.yml`, `infra/`, `shared/`, plus per-app `.env.example`, `Dockerfile`, `.dockerignore`, and lint configs for all six apps. | Claude (AI pair engineer) |
| 2026-07-13 | **Phase 1 implemented and closed.** All four applications built and genuinely verified — real Django/Next.js code, real Docker builds, real containers run against real Postgres/Redis, real test suites, culminating in a full `docker compose up` where all 11 containers (4 apps + 3 DBs + 2 caches) reported `healthy` — the exact gate Phase 0.4 couldn't pass because no app code existed yet. Built shared, reusable, tested infrastructure instead of duplicating code four times: `shared/django_observability` (health/ready/metrics/logging/exception-handling, 11 tests), `shared/resilient_http` (real 3-state circuit breaker + retry-with-backoff, 8 tests), `shared/event_bus` (Redis-backed with DLQ, 5 tests). Four real bugs found and fixed during this phase, not glossed over: **(1)** inline `#` comments in `.env.example` files silently corrupted `DATABASE_URL` and booleans because `django-environ` doesn't strip trailing comments — affected Registry, BAP, and BPP identically, found via genuine end-to-end testing, fixed by moving comments to their own line, documented as a gotcha in `ENVIRONMENTS.md`; **(2)** `shared/event_bus/__init__.py` didn't export `process_with_dlq`, caught by a real `ImportError` when BAP's test suite ran; **(3)** a stale `wslrelay.exe` process was double-bound to a test Redis port on Windows/WSL2/Docker Desktop, causing 3-minute connection-reset failures that looked like flakiness — root-caused via `netstat`, not just retried, documented in `TESTING.md`; **(4)** Next.js standalone-mode servers bind to the container's specific Docker network IP by default, not `0.0.0.0`, so the in-container `HEALTHCHECK` failed even though external requests worked fine — fixed with an explicit `ENV HOSTNAME=0.0.0.0` in both web Dockerfiles, only caught because the full `docker compose up` verification was actually run instead of assumed. Also fixed a `detect-secrets` baseline self-scan issue (its own hashed values look like secrets) in both `.pre-commit-config.yaml` and CI. New artifacts: `DATABASE.md` (with a genuinely-executed backup/restore dry run, not just documented), real Django projects for Registry/Gateway/BAP/BPP with `core` apps, real Next.js apps for BAP/web and BPP/web with custom error pages and a resilient API client (Vitest-tested). | Claude (AI pair engineer) |
| 2026-07-14 | Attempted to close the npm audit findings flagged in Phase 1 Exit. `npm audit fix --force` silently downgraded `next` to a 2019-era `^9.3.3` to satisfy the advisory graph — caught via `git diff` before it shipped, reverted immediately. A safer targeted `vitest`-only upgrade then hit an unresolvable peer-dependency conflict. Decision: leave the 7 findings as accepted, dev-tooling-only risk rather than force either unsafe path. App reverted, rebuilt, and all tests reconfirmed passing — no net code changes from this attempt, only the tracker note. | Claude (AI pair engineer) |
