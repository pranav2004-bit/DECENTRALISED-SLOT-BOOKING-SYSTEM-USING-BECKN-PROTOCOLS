# BECKN — Decentralized Slot Booking System

A decentralized, Beckn-protocol-compliant slot booking platform spanning healthcare, automotive, and beauty service categories. See [project_details.md](project_details.md) for the full brief.

**Scope:** this is a **private, self-contained Beckn network** — our own Registry, Gateway, BAP, and BPP — built strictly to the Beckn Protocol Specification and Beckn-ONDC Implementation Guidelines for correctness and interop-readiness. It does **not** connect to, register with, or integrate with the real, live ONDC network — see [livetracker1.md](livetracker1.md)'s top-of-file scope declaration for the full reasoning against `project_details.md`.

## Components

| Component | Path | Role |
|---|---|---|
| Registry | [registry/](registry) | Trust & identity (PKI) |
| Beckn Gateway | [beckn-gateway/](beckn-gateway) | Discovery routing between BAP and BPP |
| BAP | [BAP/](BAP) | Buyer App Platform |
| BPP | [BPP/](BPP) | Beckn Provider Platform |

## Key Documents

- [ARCHITECTURE.md](ARCHITECTURE.md) — system-level architectural decisions
- [livetracker1.md](livetracker1.md) — phased implementation tracker (start here to see current progress)
- [protocol_compliance_notes_v1.1.md](protocol_compliance_notes_v1.1.md) — verified Beckn/ONDC protocol facts, sourced from official specs
- [SECURITY.md](SECURITY.md) · [API_CONVENTIONS.md](API_CONVENTIONS.md) · [ENVIRONMENTS.md](ENVIRONMENTS.md) · [OBSERVABILITY.md](OBSERVABILITY.md) · [TESTING.md](TESTING.md) · [INFRASTRUCTURE.md](INFRASTRUCTURE.md)
- [CONTRIBUTING.md](CONTRIBUTING.md) — branching, commit, and PR conventions

## Local Setup

Prerequisites: Docker + Docker Compose, Git.

```bash
git clone <repo-url>
cd BECKN
cp registry/.env.example registry/.env
cp beckn-gateway/.env.example beckn-gateway/.env
cp BAP/backend/.env.example BAP/backend/.env
cp BAP/web/.env.example BAP/web/.env
cp BPP/backend/.env.example BPP/backend/.env
cp BPP/web/.env.example BPP/web/.env
docker compose up
```

Each app exposes `/health` and `/ready` once running (see [OBSERVABILITY.md](OBSERVABILITY.md)).

> **Status note:** as of Phase 0, application source code doesn't exist yet — Dockerfiles and `docker-compose.yml` are scaffolded ahead of Phase 1 (Application Foundation), which adds the actual Django/Next.js project code each Dockerfile expects. `docker compose up` will not yet produce healthy containers until Phase 1 lands; this is expected sequencing, not a bug. See [livetracker1.md](livetracker1.md) Phase 0 vs Phase 1.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).
