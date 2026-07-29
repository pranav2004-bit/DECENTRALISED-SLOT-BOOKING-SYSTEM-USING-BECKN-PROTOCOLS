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
- [livetracker1.md](livetracker1.md) — foundation & trust layer phased implementation tracker. **Closed.**
- [livetracker2.md](livetracker2.md) — business workflow & inventory tracker (search/select/init/confirm, real-time inventory, multi-domain, rating/support), builds on livetracker1.md. **Closed.**
- [livetracker3.md](livetracker3.md) — functional/UX completeness tracker (search relevance, rating aggregation, missing UI, notifications), builds on livetracker2.md. **Designed, not yet implemented — start here to see current progress.**
- [livetracker4.md](livetracker4.md) — infrastructure & scale-readiness tracker (Gateway-routing correction, event-driven maturity, secret management, horizontal scale), the same audit's other half. **Designed, not yet implemented.**
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

> **Status note (updated 2026-07-29):** `livetracker1.md` (foundation & trust layer) and `livetracker2.md` (business workflow — search/select/init/confirm, real-time inventory, Beauty/Healthcare/Automotive, rating/support, production-readiness review) are both fully **closed** and merged to `master`. A full-system audit at close produced `livetracker3.md` (functional/UX gaps) and `livetracker4.md` (infrastructure/scale-readiness) — both designed, neither implemented yet; see those trackers for current status. Payment gateway integration remains deferred to a future `livetracker5.md`, not yet designed.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).
