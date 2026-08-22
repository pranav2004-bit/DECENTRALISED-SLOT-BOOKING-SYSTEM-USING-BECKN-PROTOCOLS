# Architecture

System-level index for the BECKN project. Component-level detail lives in each `*_details_v1.1.md` file; this document covers decisions that span all four applications.

**Related documents:** [project_details.md](project_details.md) · [registry_details_v1.1.md](registry/registry_details_v1.1.md) · [beckn_gateway_details_v1.1.md](beckn-gateway/beckn_gateway_details_v1.1.md) · [BAP_details_v1.1.md](BAP/BAP_details_v1.1.md) · [BPP_details_v1.1.md](BPP/BPP_details_v1.1.md) · [protocol_compliance_notes_v1.1.md](protocol_compliance_notes_v1.1.md) · [livetracker1.md](livetracker1.md) (trust layer, closed) · [livetracker2.md](livetracker2.md) (business workflow, closed) · [livetracker3.md](livetracker3.md) (functional/UX gaps, designed) · [livetracker4.md](livetracker4.md) (infrastructure/scale readiness, designed) · [livetracker7.md](livetracker7.md) (multi-participant decentralization, closed)

## System Overview

Four independent **application codebases** form a Beckn-compliant, private decentralized slot booking network, built to Beckn-ONDC Implementation Guidelines but not connected to the real ONDC network (see [livetracker1.md](livetracker1.md)'s scope declaration):

- **Registry** — trust & identity (PKI). Stateless of business data; Python/Django; PostgreSQL. One real deployment.
- **Beckn Gateway** — discovery routing (search → on_search) between any BAP and any BPP. Stateless; Python/Django; no database, optional cache. One real deployment.
- **BAP** (Buyer App Platform) — buyer-side participant. Python/Django backend + Next.js/TypeScript web app; PostgreSQL + Redis. **Two independent real deployments** of this one codebase — see "Multi-Participant Network Topology" below.
- **BPP** (Beckn Provider Platform) — provider-side participant. Python/Django backend + Next.js/TypeScript web app; PostgreSQL + Redis. **Three independent real deployments** of this one codebase, each scoped to exactly one domain (healthcare/automotive/beauty) — see below.

All communicate over signed HTTP/JSON per the Beckn protocol (see [protocol_compliance_notes_v1.1.md](protocol_compliance_notes_v1.1.md) for the verified wire contracts). No participant trusts another directly — trust is mediated through the Registry.

## Multi-Participant Network Topology

**Real requirement, closed by [livetracker7.md](livetracker7.md) (2026-08-22):** a single BAP and a single BPP instance — however genuinely multi-domain internally — cannot demonstrate the network's actual *decentralization* property (many independent participants, freely discoverable and interoperable, no gatekeeper). This network now runs **5 real, independently-identified participants**, each with its own Registry subscription, signing identity, database, and branded web UI — not 5 forked codebases, 5 *deployments* of the 4 codebases above, differentiated purely by environment configuration.

| Participant | Codebase | Brand | Domain(s) served | Subscriber ID | Backend port | Web port |
|---|---|---|---|---|---|---|
| BPP-Beauty | BPP | StyleNest | Beauty (`ONDC:RET13`) only | `bpp-backend.local` | 8002 | 3001 |
| BPP-Medical | BPP | CareNest | Healthcare (`ONDC:SRV13`) only | `bpp-medical.local` | 8004 | 3003 |
| BPP-Automotive | BPP | AutoCare | Automotive (`BECKN:AUTO01`) only | `bpp-automotive.local` | 8005 | 3004 |
| BAP-X | BAP | OnSlot | All 3 domains | `bap-backend.local` | 8001 | 3000 |
| BAP-Y | BAP | GoFetch | All 3 domains | `bap-y.local` | 8006 | 3005 |

**How "no forked code" actually works:** every row above builds from the exact same `BPP/backend`+`BPP/web` or `BAP/backend`+`BAP/web` source. What differs per row is entirely environment configuration, layered at two points:
- **Backend**: its own `.env` file (`SUBSCRIBER_ID`, signing/encryption key paths → its own named Docker secret volume, database connection string, and — the real second enforcement layer `livetracker7.md` Phase 1 added — `SUPPORTED_DOMAINS`, checked at the request boundary independently of Registry/Gateway's own domain filtering).
- **Web**: a `NEXT_PUBLIC_BRAND_ID` Docker build arg selecting one entry from that app's own `lib/brand.ts` (name, color, tagline, icon set — `livetracker7.md` Phase 4).

**Domain-scoping is enforced at three independent layers**, not one, live-verified end-to-end in Phase 5: Registry's own per-domain `Participant` subscription rows (a BPP with no `SUBSCRIBED` row for a domain is never looked up for it), Gateway's `dispatch_search` domain filter (never routes a search to a BPP not subscribed for that domain), and each BPP's own `SUPPORTED_DOMAINS` check (rejects an out-of-domain request that reaches it directly, bypassing Gateway entirely — confirmed live: a direct `/search` for `BECKN:AUTO01` sent straight to BPP-Medical, no Gateway hop, returns a real `400 NACK`, not a routed-around empty result).

**Shared infrastructure, a recorded demo-scale simplification:** all 3 BPP-family instances share one `bpp-cache` Redis container (each on its own DB index), and both BAP-family instances share one `bap-cache` container likewise — not 5 separate Redis containers. Revisit before any real multi-operator production deployment (`livetracker7.md` §2.2's own caveat).

**Proven live, not just configured** (`livetracker7.md` Phase 5): both BAP-X and BAP-Y run the identical domain-scoped discovery against all 3 BPPs (6 total search checks, each returning only the correctly-scoped BPP); a full search→select→init→confirm booking completed end-to-end through BAP-X against BPP-Medical, and a second through BAP-Y against BPP-Automotive (a genuine multi-resource Bay+Mechanic pairing, resolved automatically) — a different BAP paired with a different BPP than the first, proving any-to-any interoperability rather than one hardcoded working pair.

## Shared Libraries (`shared/`)

Framework/business logic reused across apps rather than duplicated per-app, imported via each consuming app's `sys.path` insertion of `shared/` (see e.g. `BPP/backend/bpp/settings.py`). Plain-Python libraries are importable standalone by any app with zero framework dependency; Django-app libraries are installed into a consuming app's own `INSTALLED_APPS` and own their own migrations.

| Library | Kind | Used by | Purpose |
|---|---|---|---|
| `beckn_crypto` | Plain Python | Registry, Gateway, BAP, BPP | Ed25519/X25519 signing, verification, encryption, domain-ownership verification (`livetracker1.md` Phase 1) |
| `event_bus` | Plain Python | BAP, BPP | Redis-backed internal EDA bus with a Dead Letter Queue (`livetracker1.md` Phase 1) |
| `resilient_http` | Plain Python | BAP, BPP, Gateway | HTTP client resilience: retries, timeouts, circuit breaker (`livetracker1.md` Phase 4.2) |
| `django_observability` | Django app | Registry, Gateway, BAP, BPP | Correlation IDs, structured JSON logging, `/health`/`/ready`/`/metrics`, standardized error responses |
| `observability` | Plain Python | all | Shared logging/metrics reference conventions |
| `testing` | Plain Python | all | Shared contract-schema test fixtures (`shared/testing/contract_schemas/`) |
| `inventory_core` | Django app | BPP (Phase 2.2) | Generic, domain-agnostic `Resource`/`Slot`/`Booking`/`AvailabilityCalendar` booking core — concurrency-safe capacity, a two-machine Booking/Fulfillment state model, a Redis-backed TTL hold window, event-bus wiring, and a Domain Adapter extension point — built once and shared across Beauty/Healthcare/Automotive (`livetracker2.md` Phase 1, ADR-0003) |
| `realtime` | Django app (Channels consumer) | BAP, BPP | `FoundationConsumer` — the WebSocket transport foundation (`livetracker2.md` Phase 2.4): accepts a connection, sends a `connected` ack, echoes `pong` on `ping`. Routed via each app's `asgi.py` `ProtocolTypeRouter` (served by `daphne`, replacing the previous WSGI/gunicorn-only setup which had no WebSocket capability). The full live-inventory-push feature built on top of this transport is Phase 4.4's job, not this module's |

## Repository Strategy

**Decision: monorepo.** One repository containing all four applications (`registry/`, `beckn-gateway/`, `BAP/`, `BPP/`) plus shared root-level tooling (CI, docs, Docker Compose).

**Why:** the four applications are tightly coupled by protocol version and by the trust layer they jointly implement (Phase 2–3 of [livetracker1.md](livetracker1.md) requires all four to move in lockstep during onboarding). At this project's current scale (foundation stage, single team), a monorepo avoids the coordination overhead of four separate repos with four separate release trains, while still keeping each app's code physically separated by top-level folder. Revisit if/when each component gets an independently-scaled team — polyrepo becomes more attractive at that point, not before.

## Branching Strategy

**Decision: trunk-based development.** A single long-lived `main` branch. Short-lived feature branches (`feat/…`, `fix/…`, `chore/…`) merged via pull request after CI passes. No long-lived `develop` branch — added process overhead isn't justified at this project's current lifecycle stage ([MVP]/[PILOT]).

- All work happens on a branch; direct pushes to `main` are not the norm.
- A PR must pass the CI pipeline (see [.github/workflows/ci.yml](.github/workflows/ci.yml)) before merge.
- Squash-merge preferred, to keep `main` history one commit per logical change.

## Environment Promotion

Local → Dev → Staging → Production, all pointing at **this project's own Registry** deployed to progressively more real infrastructure — not the real ONDC registries. The naming/staging pattern is modeled on the three independently-whitelisted ONDC registry environments described in [protocol_compliance_notes_v1.1.md](protocol_compliance_notes_v1.1.md) §B.1 (a reasonable pattern to copy for a private network too), but no environment here connects to an actual ONDC endpoint. See [ENVIRONMENTS.md](ENVIRONMENTS.md) for parity rules and [INFRASTRUCTURE.md](INFRASTRUCTURE.md) for how each environment is provisioned.

## Architectural Decisions Log

Significant decisions get an ADR in [docs/adr/](docs/adr/) rather than being buried in chat history or commit messages. See [docs/adr/0000-adr-template.md](docs/adr/0000-adr-template.md) for the format.

| ADR | Decision |
|---|---|
| [0001](docs/adr/0001-monorepo.md) | Monorepo for all four applications |
| [0002](docs/adr/0002-trunk-based-development.md) | Trunk-based development, no long-lived `develop` branch |
| [0003](docs/adr/0003-generic-inventory-core.md) | Generic domain-agnostic inventory core, shared across Healthcare/Automotive/Beauty, proven on one category before widening |
| [0004](docs/adr/0004-web-ui-duplicated-not-shared-package.md) | BAP/web and BPP/web's shared UI foundation is duplicated code, not a shared npm package — no JS monorepo tooling exists yet, revisit if drift becomes a real problem |
| [0005](docs/adr/0005-gateway-search-only-routing.md) | Gateway routes only /search; the other 9 actions dispatch directly BAP<->BPP, trading network-wide Gateway visibility for lower latency/blast-radius, per the real protocol's own scope |
