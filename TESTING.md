# Testing

## Framework Choices

| Stack | Framework | Notes |
|---|---|---|
| Python (registry, beckn-gateway, BAP/backend, BPP/backend) | `pytest` + `pytest-django` | Config in each app's `pyproject.toml` (`[tool.pytest.ini_options]`) |
| TypeScript/Next.js (BAP/web, BPP/web) | `Vitest` | Faster than Jest for this project's scale; swap is low-cost later if needed |

## Test Database Strategy

Django's test runner creates an isolated, ephemeral `test_<dbname>` per test run against the same Postgres instance defined in `docker-compose.yml` — never against a shared/persistent database. Fixtures/factories via `factory_boy`, not hand-rolled JSON fixtures, so test data stays close to real model shape as models evolve.

## Mocking / Stubbing for Integration Tests

Inter-service calls (BAP/BPP → Registry, → Gateway, → each other) are mocked at the HTTP boundary using `responses` (Python) in unit/integration tests — real network calls are never made in the standard test suite. A separate, explicitly-labeled contract/E2E suite (see below) is the only place real calls to the ONDC staging sandbox happen, and only when deliberately run (not part of the default CI gate), consistent with the Phase 2.0 sandbox spike already defined in [livetracker1.md](livetracker1.md).

## Contract Testing

Beckn/ONDC JSON payload shapes are confirmed in [protocol_compliance_notes_v1.1.md](protocol_compliance_notes_v1.1.md). Contract tests validate that:
1. Every outbound request this codebase constructs (Subscribe, Lookup, on_subscribe answer, etc.) matches the confirmed schema exactly.
2. Every inbound payload this codebase parses is validated against that same schema before use — malformed input from a counterparty must be rejected with a clean error, not crash the handler.

Schemas live as JSON Schema documents derived directly from the confirmed shapes in `protocol_compliance_notes_v1.1.md`, kept in sync by hand until/unless the project adopts the official Beckn OpenAPI specs as a generation source. See [shared/testing/contract_schemas/](shared/testing/contract_schemas/) and the reference test in [shared/testing/test_contract_reference.py](shared/testing/test_contract_reference.py).

## Load Testing

**Tool: k6.** Scriptable, lightweight, good fit for HTTP API load testing without a heavyweight setup. Scaffolded now (`[MVP]`), not exercised at real scale until Phase 4.2 (Network Resilience & Failure Injection) and beyond — running load tests against nothing but empty Phase 0 scaffolding would produce meaningless numbers.

## Security Testing

- **SCA** (dependency vulnerabilities) and **SAST** (static code analysis) run in CI on every PR — see [.github/workflows/ci.yml](.github/workflows/ci.yml) and [SECURITY.md](SECURITY.md).
- **DAST baseline**: OWASP ZAP baseline scan against a running instance, introduced once Phase 1 apps actually serve HTTP (not meaningful against no running service). Tracked for Phase 2.5 (Registry Security Hardening) and Phase 4.3 (Security Penetration Pass).

## Coverage Policy

No fixed blanket coverage percentage gate at `[MVP]` — coverage is reported (`pytest --cov`) but not yet blocking, since Phase 0/1 code is mostly scaffolding. A real coverage threshold gate gets set once Phase 2 (Registry business logic) exists, where correctness actually matters most.

## What NOT to Over-Build Here

Per the project's no-over-engineering principle: no dedicated test-data-management service, no separate test orchestration platform, no parallel test-environment-per-branch infrastructure at this stage. Docker Compose + pytest/Vitest + CI is sufficient for `[MVP]`/`[PILOT]`; revisit only if team size or test suite runtime actually demands it.
