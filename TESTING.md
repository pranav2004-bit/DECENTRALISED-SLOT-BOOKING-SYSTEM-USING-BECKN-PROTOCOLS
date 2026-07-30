# Testing

## Framework Choices

| Stack | Framework | Notes |
|---|---|---|
| Python (registry, beckn-gateway, BAP/backend, BPP/backend) | `pytest` + `pytest-django` | Config in each app's `pyproject.toml` (`[tool.pytest.ini_options]`) |
| Python (`shared/beckn_crypto`, `shared/beckn_transaction`, `shared/event_bus`, `shared/resilient_http`) | plain `pytest`, no Django | Each is framework-free by design (any of the four apps can use it identically) and has its own `tests.py`, run directly with `pytest tests.py` from inside that library's own directory — a dedicated CI job (`test-shared-python` in `.github/workflows/ci.yml`) runs all four, since none of them live inside any single app's own working directory and were found live (`livetracker2.md`'s Phase 3.11 follow-up fixes) to never be discovered by any per-app `pytest` run otherwise |
| TypeScript/Next.js (BAP/web, BPP/web) | `Vitest` | Faster than Jest for this project's scale; swap is low-cost later if needed |

## Frontend Component Testing

Introduced in `livetracker2.md` Phase 2.4 — before this, `BAP/web`/`BPP/web`'s Vitest config ran in a `node` environment with zero DOM, since the only prior test target (`lib/api-client.ts`) was pure logic. Phase 2.4 added the first real React components (`AppShell`, the base component library, `useRealtimeConnection()`), so the test infra had to grow with them, not stay behind:

- **`vitest.config.ts` switched to `environment: "jsdom"`** (from `node`) plus a `@vitejs/plugin-react` plugin and a `resolve.alias` for the `@/*` path (Vitest doesn't read `tsconfig.json`'s `paths` automatically — Next.js does, but the test runner needed the alias declared explicitly or every `@/...` import in a test file fails to resolve).
- **`@testing-library/react`** for rendering components and `renderHook` for the `useRealtimeConnection()` hook; **`@testing-library/user-event`** for realistic click/keyboard interaction simulation over raw DOM event dispatch; **`@testing-library/jest-dom`** for the `toBeInTheDocument()`/`toHaveAttribute()`/`toHaveTextContent()` style of DOM assertion, wired in via a `vitest.setup.ts` that also calls `cleanup()` after every test (React Testing Library doesn't auto-unmount between tests the way Jest's default environment does).
- **WebSocket in tests**: `jsdom` doesn't implement `WebSocket`, so `useRealtimeConnection.test.ts` defines a small `MockWebSocket` class (tracks its own listeners, exposes an `emit()` helper to fire `open`/`message`/`close`/`error` from the test) and installs it via `vi.stubGlobal('WebSocket', MockWebSocket)` — a real handshake is exercised separately, live, in a browser (see Phase 2.4's Test Gate in `livetracker2.md`); the unit test's job is only the hook's own state-machine logic (`connecting`/`open`/`closed`/`error`, reconnect-on-close, manual `reconnect()`).
- Same pattern applied identically to both `BAP/web` and `BPP/web`, matching [ADR-0004](docs/adr/0004-web-ui-duplicated-not-shared-package.md)'s duplicated-not-shared decision for the UI code itself.

## Local Testing Gotcha (Windows/Docker Desktop/WSL2)

Found in Phase 1.4: when connecting from the Windows host to a Docker-published port, `localhost` can silently hit a stale `wslrelay.exe` binding on the IPv6 loopback (`[::1]`) instead of Docker's actual port-forward, causing connection resets and multi-minute timeouts (standalone containers) or an immediate `curl` failure (exit 56/7) that look like flakiness but aren't — `netstat -ano | findstr :<port>` will show two different PIDs bound to the same port on `0.0.0.0` vs `[::1]`. **Fix:** connect via `127.0.0.1` explicitly instead of `localhost`, in local test `.env` files and in any host-side `curl`/browser access.

**Correction (Phase 2 Exit):** an earlier version of this note claimed `docker compose` itself was unaffected, reasoning that services resolve each other by service name inside the Docker network. That's true for *container-to-container* traffic, but wrong for *host-to-container* traffic: hitting a `docker compose`-published port from the host (e.g. `curl http://localhost:8000/health` after `docker compose up`) hits the exact same `wslrelay.exe` conflict — confirmed for real during Phase 2 Exit's full-stack integration test. Always use `127.0.0.1` from the host, for both standalone containers and `docker compose`.

**Docker Desktop full crash + recovery (Phase 3 Exit, 2026-07-25/26) — a multi-hour live incident, recorded so a repeat doesn't cost the same time again.**

*Root causes, compounding:*
1. Repeated full image rebuilds across one long session (8+ times, no incremental deploy) filled Docker's build cache with ~48GB of reclaimable bloat, driving host disk free space down to **~4.6GB** — critical for a system that constantly needs to write new layers.
2. A second, redundant build accidentally launched on top of an already-running one (misjudged timing) doubled the disk/CPU contention at the worst possible moment.
3. Docker Desktop's backend crashed under that pressure — its internal API started returning `502 Bad Gateway` (`docker ps`/`docker stats` hanging or erroring, not just slow).
4. Separately found: the host machine was down to **0.53GB free RAM out of 7.65GB** at one point — likely a contributing factor to the instability, not just a side effect of it.

*Diagnostic checks, in order of what actually revealed the real problem:*
- `docker system df` / `df -h` — confirms disk-space exhaustion as a first-order cause.
- `docker ps` / `docker stats --no-stream` with a short timeout — if even these trivial commands hang or return `502`, the daemon itself is down, not just slow.
- `wsl -l -v` — the check that found the actual root cause this time: Docker Desktop's own Windows-side app processes (`Docker Desktop.exe`, `com.docker.backend`) can show `Running` and "Responding: True" at the OS level while the `docker-desktop` WSL distro that actually runs the engine is stuck `Stopped` underneath. A normal "Restart" from the tray icon does not reliably fix this.

*Recovery sequence that actually worked, in order (each step alone was insufficient):*
1. Restart Docker Desktop via the tray icon — clears `502` errors, but often leaves containers in a broken state (zombie/dead/duplicate hash-prefixed containers, some stuck "visible in `docker ps -a` but `docker rm` reports `No such container`" — a genuine daemon-internal metadata inconsistency, not user error).
2. `wsl --shutdown` (PowerShell) — resets the Linux VM. Sometimes not enough on its own; corrupted container references can survive it.
3. Fully kill and relaunch the Docker Desktop application itself (`Stop-Process -Name "Docker Desktop","com.docker*"` then relaunch the exe) — a more thorough reset than the tray icon's "Restart."
4. If the daemon is still unresponsive after that: check `wsl -l -v`. If `docker-desktop` shows `Stopped`, start it directly (`wsl -d docker-desktop -e echo test`) — this is what actually restored a responsive daemon when the first three steps hadn't.
5. Rebuild any images that got wiped during the crash (no source code is ever at risk — only compiled images).
6. For containers stuck with ugly hash-prefixed names after a broken recreate cycle: **don't** keep fighting `docker compose up`/`rm -f` against the same corrupted reference — use `docker rename <hash-prefixed-name> <proper-name>` directly on the already-running container instead. Zero downtime, sidesteps the corruption entirely.
7. Verify nothing was actually lost: check Postgres data survived (e.g. Registry's `Participant` rows still `SUBSCRIBED`), re-run full test suites, and do one live E2E walkthrough with a database-level cross-check — Docker/WSL2-level chaos never touches named volumes, only the containers/images sitting on top of them.

**Preventive note for next time:** if a rebuild is taking noticeably longer than its own established baseline, check `docker system df` and free disk space *before* assuming the build itself is slow or launching a second one — that single check would have caught this incident at its actual root cause, hours earlier.

## Test Database Strategy

Django's test runner creates an isolated, ephemeral `test_<dbname>` per test run against the same Postgres instance defined in `docker-compose.yml` — never against a shared/persistent database. Fixtures/factories via `factory_boy`, not hand-rolled JSON fixtures, so test data stays close to real model shape as models evolve.

## Mocking / Stubbing for Integration Tests

Inter-service calls (BAP/BPP → Registry, → Gateway, → each other) are mocked at the HTTP boundary using `responses` (Python) in unit/integration tests — real network calls are never made in the standard test suite. A separate, explicitly-labeled contract/E2E suite (see below) makes real calls only against this project's own deployed services (never the real ONDC network — see [livetracker1.md](livetracker1.md)'s scope declaration), and only when deliberately run (not part of the default CI gate).

## Contract Testing

Beckn/ONDC JSON payload shapes are confirmed in [protocol_compliance_notes_v1.1.md](protocol_compliance_notes_v1.1.md). Contract tests validate that:
1. Every outbound request this codebase constructs (Subscribe, Lookup, on_subscribe answer, etc.) matches the confirmed schema exactly.
2. Every inbound payload this codebase parses is validated against that same schema before use — malformed input from a counterparty must be rejected with a clean error, not crash the handler.

Schemas live as JSON Schema documents derived directly from the confirmed shapes in `protocol_compliance_notes_v1.1.md`, kept in sync by hand until/unless the project adopts the official Beckn OpenAPI specs as a generation source. See [shared/testing/contract_schemas/](shared/testing/contract_schemas/) and the reference test in [shared/testing/test_contract_reference.py](shared/testing/test_contract_reference.py).

## Concurrency & Race-Condition Testing

Introduced in `livetracker2.md` Phase 1.2 for `shared/inventory_core`'s atomic capacity decrement, and reused in Phase 1.3 for the Redis-backed TTL reservation window — the real pattern, not a theoretical description:

- **Real concurrent writes, not a single-threaded simulation.** `pytest.mark.django_db(transaction=True)` (not the default `django_db` marker) — the default wraps a test in pytest-django's own outer transaction, which serializes everything through one connection and would hide the exact race being tested. `transaction=True` gives each thread a genuine, independently-committing Postgres connection.
- **`concurrent.futures.ThreadPoolExecutor`**, one worker per attempt, all racing the same DB row (e.g. a capacity-1 `Slot`). Django's connection handling is thread-local, so each thread lazily opens its own real connection on first use — no manual connection-pool wiring needed, but each thread function must call `django.db.connection.close()` when done to avoid leaking connections across the test run.
- **Assert the aggregate outcome, not a single call.** For an atomic conditional `UPDATE` (`shared/inventory_core.models.SlotManager.try_reserve`), assert exactly one success and N-1 clean rejections against a capacity-1 row — a corrupted/over-counted result is the actual bug this test exists to catch, not just "did it run without an exception."
- **Real timing for TTL behavior**, not a mocked clock: Phase 1.3's expiry tests use a short (1-second) real Redis TTL and a real `time.sleep()` past it, then assert the reconciliation function (`release_expired_hold`) does the right thing — genuinely exercises Redis's own eviction, not an assumption about how TTLs behave.
- **Re-run before trusting.** Both the concurrent-write and TTL-timing tests were re-run multiple times (5x and 3x respectively) during development specifically to rule out flakiness before being counted as passing — a single green run of a timing/concurrency test is weaker evidence than for ordinary deterministic tests, and treated that way.

See `BPP/backend/core/test_inventory_core_concurrency.py` and `test_inventory_core_booking.py` for the reference implementation of this pattern.

**This pattern also caught a real, previously-latent bug outside `inventory_core`** (`livetracker2.md`'s Phase 3.11 follow-up fixes): `participant_keys.py`'s lazy signing-key generation (BPP/BAP/beckn-gateway) raced under genuine concurrent first callers — `functools.lru_cache` doesn't guarantee only one thread runs the wrapped function on a cache miss, and the file-write itself wasn't atomic. A dedicated `ThreadPoolExecutor`-based test (25 real threads racing `_load_or_generate()` against the same fresh key path, `test_participant_keys.py` in all three apps) reproduced the race deterministically and, on a first fix attempt, caught that the fix's own double-checked-locking approach still had the identical gap — the same "assert the aggregate outcome across every thread, not just that it ran" discipline this section already establishes, applied to a bug this pattern wasn't originally written for.

**Another confirmed instance, same "read-old/write-new" race class as `participant_keys` above** (`livetracker3.md`'s Phase 2, `BPP/backend/core/test_rating.py`): `dispatch_on_rating`'s original implementation read a `Resource`'s current `rating_count`/`average_rating` via a separate pre-fetch before calling `update_or_create()` on the `Rating` row — two genuinely concurrent submissions for the same `(booking, rating_category)` (a double-click, a client retry) could both read the same stale aggregate and both write, overcounting relative to the single `Rating` row actually stored. `test_dispatch_on_rating_concurrent_resubmission_never_overcounts` reproduced this live (two raw `threading.Thread`s, not `ThreadPoolExecutor` — only 2 concurrent callers needed, same reasoning as the deadlock test below) before the fix wrapped the read+write in one `transaction.atomic()` block using `select_for_update().get_or_create()`; the test asserts exactly one `Rating` row exists and the aggregate matches whichever value actually won, not just that the code ran.

**A second, distinct concern this pattern is also used for: proving *no deadlock*, not just a correct aggregate outcome** (`livetracker2.md`'s Phase 4.2, `hold_multi_resource_booking`'s multi-slot locking): `test_hold_multi_resource_booking_locks_slots_in_a_deterministic_order_to_avoid_deadlock` (`test_inventory_core_booking.py`) uses raw `threading.Thread` (not `ThreadPoolExecutor` — only 2 real concurrent callers are needed here, not N racing the same row) to submit the *same pair* of slot ids in opposite order from two threads at once, then asserts `not t1.is_alive() and not t2.is_alive()` after a bounded `join(timeout=...)` — liveness, not correctness, is the property under test. A real Postgres deadlock would otherwise leave one or both threads permanently blocked (or one killed by Postgres's own deadlock detector after a multi-second delay) rather than exiting cleanly; a passing "aggregate outcome" assertion alone (e.g. "exactly one succeeded") would not by itself prove neither thread hung getting there. Same `transaction=True` requirement as the rest of this section, for the same reason (real independent connections, real row locks).

## Load Testing

**Tool: k6.** Scriptable, lightweight, good fit for HTTP API load testing without a heavyweight setup. Scaffolded now (`[MVP]`), not exercised at real scale until Phase 4.2 (Network Resilience & Failure Injection) and beyond — running load tests against nothing but empty Phase 0 scaffolding would produce meaningless numbers.

## Security Testing

- **SCA** (dependency vulnerabilities) and **SAST** (static code analysis) run in CI on every PR — see [.github/workflows/ci.yml](.github/workflows/ci.yml) and [SECURITY.md](SECURITY.md).
- **DAST baseline**: OWASP ZAP baseline scan against a running instance, introduced once Phase 1 apps actually serve HTTP (not meaningful against no running service). Tracked for Phase 2.5 (Registry Security Hardening) and Phase 4.3 (Security Penetration Pass).
- **Live manual verification of business-layer security controls** (`livetracker2.md` §3.7, re-run at Phase 3 Exit): the automated SEC test suite (`test_session_authz.py`, `test_rate_limit.py`, `test_customer_auth.py`) proves the logic in isolation; it's supplemented by real `curl` attempts against the running Docker stack — two genuinely distinct logged-in customers attempting cross-access on a real `transaction_id` (expect 403), an unauthenticated request against an owned session (expect 401), 6 rapid real login attempts against a 5/min limit (expect 429 on the 6th), and a real CSRF-token-less POST (expect Django's own 403 "CSRF cookie not set"). Unit tests can assert the function returns the right status code; only a live run proves the middleware chain, session cookie, and Redis-backed limiter are actually wired together correctly end-to-end in the deployed app, not just in a test client.

## Coverage Policy

No fixed blanket coverage percentage gate at `[MVP]` — coverage is reported (`pytest --cov`) but not yet blocking, since Phase 0/1 code is mostly scaffolding. A real coverage threshold gate gets set once Phase 2 (Registry business logic) exists, where correctness actually matters most.

## What NOT to Over-Build Here

Per the project's no-over-engineering principle: no dedicated test-data-management service, no separate test orchestration platform, no parallel test-environment-per-branch infrastructure at this stage. Docker Compose + pytest/Vitest + CI is sufficient for `[MVP]`/`[PILOT]`; revisit only if team size or test suite runtime actually demands it.
