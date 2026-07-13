# ADR-0001: Monorepo for all four applications

**Status:** Accepted
**Date:** 2026-07-13

## Context

Four applications (Registry, Beckn Gateway, BAP, BPP) need a repository strategy before Phase 0 (Repository & Version Control Strategy) can proceed. They are tightly coupled by protocol version and by the trust layer they jointly implement — Phase 2–3 of `livetracker1.md` requires all four to move in lockstep during participant onboarding.

## Decision

One repository containing all four applications as top-level folders, plus shared root-level tooling (CI, Docker Compose, docs).

## Alternatives Considered

- **Polyrepo (one repo per app)** — cleaner independent release trains and access control per team, but at this project's current single-team, foundation-stage scale, it multiplies coordination overhead (four PRs to keep in sync for any protocol-layer change) for a benefit (independent teams/releases) that doesn't exist yet.

## Consequences

- Simpler cross-app changes during Phase 0–4 (one PR can touch Registry + BAP + BPP consistently).
- CI must be structured to scope per-app (see `.github/workflows/ci.yml` matrix jobs) so one app's failure doesn't block unrelated apps' status reporting.
- Revisit if/when any component gets an independently-scaled team with its own release cadence — polyrepo becomes more attractive then, not before.

## Related

[ARCHITECTURE.md](../../ARCHITECTURE.md) §Repository Strategy
