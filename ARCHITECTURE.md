# Architecture

System-level index for the BECKN project. Component-level detail lives in each `*_details_v1.1.md` file; this document covers decisions that span all four applications.

**Related documents:** [project_details.md](project_details.md) · [registry_details_v1.1.md](registry/registry_details_v1.1.md) · [beckn_gateway_details_v1.1.md](beckn-gateway/beckn_gateway_details_v1.1.md) · [BAP_details_v1.1.md](BAP/BAP_details_v1.1.md) · [BPP_details_v1.1.md](BPP/BPP_details_v1.1.md) · [protocol_compliance_notes_v1.1.md](protocol_compliance_notes_v1.1.md) · [livetracker1.md](livetracker1.md)

## System Overview

Four independent applications form a Beckn-compliant, private decentralized slot booking network, built to Beckn-ONDC Implementation Guidelines but not connected to the real ONDC network (see [livetracker1.md](livetracker1.md)'s scope declaration):

- **Registry** — trust & identity (PKI). Stateless of business data; Python/Django; PostgreSQL.
- **Beckn Gateway** — discovery routing (search → on_search) between BAP and BPP. Stateless; Python/Django; no database, optional cache.
- **BAP** (Buyer App Platform) — buyer-side participant. Python/Django backend + Next.js/TypeScript web app; PostgreSQL + Redis.
- **BPP** (Beckn Provider Platform) — provider-side participant, serving healthcare/automotive/beauty domains. Python/Django backend + Next.js/TypeScript web app; PostgreSQL + Redis.

All four communicate over signed HTTP/JSON per the Beckn protocol (see [protocol_compliance_notes_v1.1.md](protocol_compliance_notes_v1.1.md) for the verified wire contracts). No participant trusts another directly — trust is mediated through the Registry.

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
