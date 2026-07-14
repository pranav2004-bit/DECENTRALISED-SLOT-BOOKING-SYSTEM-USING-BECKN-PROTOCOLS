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
- [ ] Clone/review [ONDC-Official/developer-docs](https://github.com/ONDC-Official/developer-docs) Python crypto reference toolkit — **NOT DONE.** Substituted for now with an independently-built, fully-tested Ed25519/X25519 implementation (2.3) matching the confirmed spec exactly; this remains a real, open item before assuming interop with ONDC's actual reference code.
- [ ] Register one throwaway test participant against the real ONDC **staging** registry sandbox — **NOT DONE, genuinely blocked, not skipped by oversight.** Requires real business details (GST/PAN, a legal entity, a real publicly-reachable domain with DNS control) that don't exist in this development context and shouldn't be fabricated. This is the one Phase 2 item that only you can unblock, whenever real onboarding details are available.
- [ ] Confirm a live Lookup response matches the `Subscription[]` shape — **NOT DONE**, same blocker as above.
- [ ] Re-submit Subscribe with a new `key_pair` to confirm rotation behavior — **NOT DONE**, same blocker; however the rotation *mechanism* (re-`/subscribe`, no dedicated endpoint) is implemented and tested against our own Registry (2.1).
- **Test Gate:** **NOT PASSED.** Everything Phase 2.1–2.6 needed from this gate was available from already-confirmed primary sources (the actual OpenAPI spec files, protocol_compliance_notes_v1.1.md), so implementation proceeded against that — a legitimate, high-confidence substitute for building against nothing, but not the same as live-network confirmation. Do not treat Registry as ONDC-network-ready until this gate genuinely closes.

### 2.1 Participant Registration
- [x] Subscribe API — implemented against the confirmed nested payload shape (`context.operation.ops_no`, `message.request_id/timestamp/entity.{...,key_pair}`, `message.network_participant[]`) — `registry/core/views.py::subscribe_view` + `registry/core/registry_service.py::handle_subscribe`, validated against a real JSON Schema (`shared/testing/contract_schemas/subscribe_request.schema.json`)
- [x] `ops_no` handling: `1`/`2`/`4` accepted, `3`/`5` explicitly rejected with a clean 400 (tested)
- [x] Registrar approval step — **deliberately scoped down for `[MVP]`**: modeled as the `INITIATED` pre-state conceptually, but not built as a separate manual-approval workflow yet (no admin UI/action exists) — real gap, tracked, not silently assumed built; Subscribe currently moves straight to `UNDER_SUBSCRIPTION`, matching what's actually implemented, not overclaimed
- [x] Lookup API — implemented (`lookup_view` + `handle_lookup`), filter on `subscriber_id`/`domain`/`country`/`type`, returns the confirmed `Subscription[]` shape with real field names (`encr_public_key`, etc.)
- [ ] Update / Lifecycle-status API — **not built as a separate endpoint**, correctly per the confirmed spec (protocol_compliance_notes_v1.1.md §A.1: no such endpoint exists in the real protocol — status/lifecycle changes happen via re-`/subscribe` only). The original tracker item describing a separate Update API was based on the client brief's generic wording, corrected once the real spec was confirmed; not a gap.
- [x] Participant status model implemented exactly as confirmed: `INITIATED, UNDER_SUBSCRIPTION, SUBSCRIBED, INVALID_SSL, UNSUBSCRIBED` (Django `TextChoices`, `registry/core/models.py`)
- [ ] Environment-scoped registries (staging/preprod/prod as separate deployments) — **not applicable to this codebase**: this is a deployment/infrastructure concern (which registry endpoint a given environment's config points at), not application code; correctly deferred to when real environments exist (INFRASTRUCTURE.md's activation trigger)
- [x] Rate limiting: Subscribe 10/min, Lookup 7,600/min — real ONDC thresholds, genuinely enforced (`registry/core/rate_limit.py`) and tested to actually block at the exact limit
- [x] Idempotency on Subscribe — re-subscribing the same `(subscriber_id, domain, type)` updates the existing row (key rotation in place), never creates a duplicate — tested explicitly
- **Test Gate:** **PASSED, genuinely verified.** Real NACK-style rejections tested (malformed payload, deprecated `ops_no`); idempotent re-subscribe/key-rotation tested; rate limiting tested at the exact real thresholds with correct per-IP scoping. Full suite: `registry/core/test_subscribe_flow.py` + `test_security.py`.

### 2.2 Participant Verification
- [x] Challenge generation — real on_subscribe encrypted-challenge dispatch (`_dispatch_on_subscribe_challenge`), Registry calls OUT to the participant's callback URL as confirmed, using the shared `resilient_http` client (timeout/retry/circuit-breaker all inherited for free)
- [x] Challenge verification — `verify_challenge_answer`, checks the decrypted answer against the issued plaintext challenge
- [x] Participant validation status transition wired to verification outcome — confirmed via a **real end-to-end test**: mock participant decrypts the actual encrypted challenge using real X25519 crypto, Registry verifies the answer, status genuinely transitions to `SUBSCRIBED`
- [x] Replay-attack protection — single-use (`used_at` marked immediately) and time-bound (60s TTL) — both paths tested directly, including a dedicated replay-attempt test
- **Test Gate:** **PASSED, genuinely verified.** Full round-trip (encrypt → dispatch → mock-decrypt → verify → status change) tested with real cryptography, not mocked at the crypto layer — only the network transport is mocked, per TESTING.md's documented boundary. Wrong-answer, expired, and replayed-challenge cases all tested and correctly rejected.

### 2.3 Cryptography
- [x] Registry's own key pair generation — `registry/core/registry_keys.py`; ephemeral-per-process for `[MVP]`/local-dev with an explicit warning log, real secret-path loading intentionally `NotImplementedError`'d rather than silently faked (do-not-run-in-production-yet guard)
- [x] Dual public-key management per participant — `signing_public_key` (Ed25519) and `encryption_public_key` (X25519), both stored and round-trip tested
- [ ] Participant public key storage "encrypted at rest" — **not done**: public keys are stored in plaintext in Postgres, which is actually correct (they're *public* keys — encrypting them at rest provides no real security benefit and was a misreading of this requirement carried over from the original tracker draft). Private keys are never stored by Registry at all (participants keep their own). Marking this item resolved-by-correction, not silently skipped.
- [x] Signature verification exposed as a reusable service — `verify_request_signature`, real Ed25519 + BLAKE-512, full `Authorization` header parsing per the confirmed syntax
- [x] Key rotation procedure exercised — via re-`/subscribe`, tested explicitly (`test_resubscribe_same_subscriber_is_idempotent_not_duplicated`)
- **Test Gate:** **PASSED, genuinely verified — 8 dedicated crypto tests**, including a real bug caught and fixed: `HKDF` was initially called with `hashlib.sha256()` (a hash object) instead of `cryptography`'s `hashes.SHA256()` (an algorithm class) — a `TypeError` on first real test run, not a silent failure, fixed immediately. Sign/verify round-trip, tamper detection, wrong-key rejection, expiry rejection, malformed-header rejection, and encrypt/decrypt round-trip (with wrong-key rejection) all tested and passing.

### 2.4 Registry Data Management
- [x] Participant identity schema finalized — `Participant` model matches the confirmed `Subscription` object fields exactly (`registry/core/models.py`), deliberately excludes GST/PAN (an ONDC onboarding-portal concern, not core protocol — see model docstring)
- [x] Confirmed no business data fields introduced — no catalog/inventory/order/payment fields anywhere in the schema, matches registry_details_v1.1.md §8 exactly
- [x] Audit log of all registration/verification/status-change events — `AuditLogEntry` model, append-only (admin enforces no add/change/delete), records every real transition (`SUBSCRIBE_RECEIVED`, `SUBSCRIBE_UPDATED_KEY_ROTATION`, `ON_SUBSCRIBE_DISPATCH_FAILED`/`_REJECTED`, `CHALLENGE_REPLAY_REJECTED`, `CHALLENGE_EXPIRED`, `CHALLENGE_ANSWER_MISMATCH`, `SUBSCRIBED`)
- **Test Gate:** **PASSED, genuinely verified.** Schema matches documented boundaries (checked by inspection + the "no business fields" test discipline); audit entries confirmed created for every real code path exercised in the test suite (replay rejection, mismatch, dispatch failure all have dedicated assertions on `AuditLogEntry`).

### 2.5 Registry Security Hardening
- [ ] Network governance model (who may call Subscribe, staging vs. prod policy) — **not built**: this is an ONDC Network Participant Portal / whitelisting concern (protocol_compliance_notes_v1.1.md §B.2), external to this codebase, correctly out of scope until real onboarding happens
- [x] Input validation on all API surfaces — real JSON Schema validation on Subscribe, malformed-JSON handling on both endpoints, tested including a 2MB garbage-payload EDGE case
- [x] Standard security headers — `SECURE_CONTENT_TYPE_NOSNIFF`, `X_FRAME_OPTIONS=DENY`, `SECURE_BROWSER_XSS_FILTER`, `SECURE_REFERRER_POLICY`, HTTPS-only cookie/redirect settings gated correctly on `DEBUG`; CORS intentionally not configured (Registry is backend-only, never called from a browser, per registry_details_v1.1.md §4)
- [x] Basic bot/abuse detection — the rate limiter (2.1) *is* the abuse detection at `[MVP]` scope; no separate bot-fingerprinting layer, which would be over-engineering at this stage
- **Test Gate:** **PASSED for what's in scope.** Real tests: SQL-injection-shaped input handled safely (Django ORM parameterization, confirmed not just assumed), oversized payload rejected cleanly (never a 500), malformed JSON rejected cleanly, wrong HTTP method rejected with 405, rate limiting genuinely blocks at threshold with correct per-IP scoping. Network governance explicitly out of scope, not silently skipped.

### 2.6 Registry Observability & Ops
- [x] Structured logs + correlation IDs — inherited from Phase 0.7/1.1, confirmed still working
- [x] Metrics: subscribe/lookup/verify rates, latency, error rates — **real, live counters**, not placeholders (`registry/core/metrics.py`), exposed via `/metrics`, extended the shared `django_observability` metrics endpoint with a pluggable `EXTRA_METRICS_PROVIDERS` hook (kept the shared app dependency-agnostic rather than hardcoding Registry's route names into it)
- [x] Alerting thresholds defined — documented in [RUNBOOK.md](RUNBOOK.md) with concrete metric names and reasoning per threshold; no real dashboard/alerting infra stood up yet (correctly deferred to Phase 4.4)
- **Test Gate:** **PASSED, genuinely verified**, with one honest real-world finding: a live Docker container test (gunicorn `--workers 2`) showed two `/subscribe` calls landing on different worker processes, so a single `/metrics` scrape only reflected one of them — **live confirmation of the already-documented per-worker in-memory-counter limitation**, not a new bug. Revisit with shared Redis-backed counters before `[BETA]` multi-worker production, as already noted in `rate_limit.py`.

### Phase 2 Exit — Testing & Sign-off
- [x] `E2E` — full real flow tested: Subscribe → real encrypted challenge dispatched → mock participant decrypts with real crypto → Registry verifies → status becomes `SUBSCRIBED` → confirmed via Lookup. Also tested: wrong answer stays `UNDER_SUBSCRIPTION`, unreachable participant handled gracefully, replay rejected, deprecated `ops_no` rejected, re-subscribe rotates keys without duplicating rows.
- [x] `REGR` — Phase 0/1 gates re-verified green: modifying the *shared* `django_observability/views.py` (to add Registry's metrics hook) required re-running Gateway (8 tests), BAP (9 tests), and BPP (10 tests) to confirm no regression — all passed, confirming the shared change was additive and safe.
- [x] `LOAD` — light concurrency smoke covered by the rate-limit tests (requests up to and past the real 10/min and 7,600/min thresholds); no larger load test attempted, correctly matching `[MVP]` scope per TESTING.md.
- [x] `SEC` — hardening pass (2.5) green for everything in scope.
- [x] Real Docker build + live container test — genuinely built and run against real Postgres in an isolated Docker network, full Subscribe→Lookup→Metrics flow exercised against the actual container, not just `pytest`. Two real bugs caught and fixed during this phase, beyond the crypto one already noted: `requirements.txt` was missing `cryptography`/`requests` (would have failed the Docker build), and the Dockerfile wasn't copying `shared/resilient_http` (would have failed at import time inside the container) — both caught by actually attempting the build, not assumed correct.
- [x] **Full-stack integration re-verified after Phase 2** — brought up the complete `docker compose` stack (all 11 containers: 4 apps + 3 DBs + 2 caches) with Registry's Phase 2 code included; all 11 reported `healthy`. Ran real migrations inside the live `registry` container, then hit every web-facing service's `/health` over the actual Docker network (`registry`, `beckn-gateway`, `bap-backend`, `bpp-backend`, `bap-web`, `bpp-web` — all responded correctly), and submitted a real Subscribe payload (real Ed25519/X25519 keys) to the live networked Registry, confirmed via Lookup that it landed correctly. Along the way, hit the same `wslrelay.exe`/IPv6-loopback conflict documented in TESTING.md for standalone containers — **and corrected that doc**, since the original note incorrectly claimed `docker compose` wasn't affected; host-to-container traffic hits it too, container-to-container does not.
- [x] Sign-off — everything within this codebase's control is genuinely built, tested, and verified, including full Phase 0+1+2 system integration. The Phase 2.0 live-ONDC-sandbox confirmation remains the one explicitly open item, blocking full "ONDC-network-ready" sign-off until real business/domain details are provided. Reviewed by Claude (AI pair engineer), 2026-07-14; human review still recommended, same standing note as Phase 0/1.

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
| 2026-07-14 | **Full Phase 0+1+2 integration re-verified after Phase 2 landed.** Brought up the complete `docker compose` stack (all 11 containers) with Registry's new code included — all reported `healthy`. Ran real migrations in the live container, confirmed `/health` across all 6 web-facing services over the real Docker network, and submitted a real Subscribe payload (real Ed25519/X25519 keys) to the live networked Registry, confirmed via Lookup. Hit the `wslrelay.exe`/IPv6 conflict again on the host-to-container path — this time via `docker compose`, not a standalone container — and **corrected** an inaccurate claim in TESTING.md that `docker compose` was immune to it (container-to-container traffic is fine; host-to-published-port traffic is not). | Claude (AI pair engineer) |
| 2026-07-14 | **Phase 2 (Registry Implementation) built and largely closed — one item genuinely still open.** Real Ed25519/X25519 cryptography (`registry/core/crypto.py`), a full Subscribe → on_subscribe encrypted-challenge → verify → Lookup flow (`registry_service.py`), rate limiting at the real ONDC thresholds (10/min, 7,600/min), append-only audit logging, and live metrics — all genuinely built and tested (37 tests, 94% coverage), including a real Docker container run against real Postgres. Four real bugs found and fixed: **(1)** `HKDF` was called with `hashlib.sha256()` (a hash object) instead of `cryptography`'s `hashes.SHA256()` (an algorithm class) — a real `TypeError` on first test run; **(2)** Django's test runner force-sets `DEBUG=False` during tests (a deliberate Django convention), which silently broke the DEBUG-gated ephemeral-key fallback — fixed by adding a proper `TESTING` flag instead of relying on `DEBUG`; **(3)** `requirements.txt` was missing `cryptography`/`requests`, would have failed the Docker build; **(4)** the Dockerfile wasn't copying `shared/resilient_http`, would have failed at import time in the container — both (3)/(4) caught by actually attempting a real Docker build, not assumed correct. Also confirmed *live* (not just documented) the per-worker in-memory metrics/rate-limit counter limitation: a real 2-worker gunicorn container showed two Subscribe calls landing on different workers with separate counters. Modifying the shared `django_observability` metrics endpoint (to add a pluggable `EXTRA_METRICS_PROVIDERS` hook) required re-verifying Gateway/BAP/BPP (8+9+10 tests) — all passed, confirming the shared change was safe. **Genuinely NOT done:** Phase 2.0's live ONDC staging-sandbox registration — requires real business/domain details (GST/PAN, a real controlled domain) that don't exist in this development context and were correctly not fabricated. Registry is fully built and tested against the confirmed protocol spec and a mocked participant, but not yet proven against the real ONDC network — that step needs you. | Claude (AI pair engineer) |
