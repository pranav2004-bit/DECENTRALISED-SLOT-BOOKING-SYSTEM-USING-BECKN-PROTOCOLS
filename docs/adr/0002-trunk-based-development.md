# ADR-0002: Trunk-based development, no long-lived develop branch

**Status:** Accepted
**Date:** 2026-07-13

## Context

Branching strategy needed before Phase 0.1 could close. Team is small and at foundation/MVP lifecycle stage per `livetracker1.md`.

## Decision

Single long-lived `main` branch. Short-lived feature branches (`feat/…`, `fix/…`, `chore/…`) merged via PR after CI passes. No `develop` branch.

## Alternatives Considered

- **Gitflow (`develop` + release branches)** — better suited to teams with scheduled release trains and multiple in-flight release versions simultaneously. Adds merge/branch-management overhead not justified at `[MVP]`/`[PILOT]` scale, where there is effectively one in-flight version at a time.

## Consequences

- Every merge to `main` must be deployable (CI-gated) — no "broken on develop is fine" safety net.
- Simpler mental model for a small team; revisit if the project reaches `[BETA]`/`[ENT]` scale with multiple concurrent release trains.

## Related

[ARCHITECTURE.md](../../ARCHITECTURE.md) §Branching Strategy, [CONTRIBUTING.md](../../CONTRIBUTING.md)
