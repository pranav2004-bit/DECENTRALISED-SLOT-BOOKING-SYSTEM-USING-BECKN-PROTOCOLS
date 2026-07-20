# ADR-0004: BAP/web and BPP/web's shared UI foundation is duplicated code, not a shared npm package

**Status:** Accepted
**Date:** 2026-07-19

## Context

`livetracker2.md` §2.4 asks for "a single, shared, mobile-first responsive layout shell for both BAP web and BPP web." Before implementing, the actual state of both apps was surveyed: they are two independent Next.js 16 (App Router) projects, structurally identical today (same dependencies, same `layout.tsx`/`page.tsx`/`error.tsx` shapes), each with its own self-contained Docker build context (`build: ./BAP/web`, `build: ./BPP/web` in `docker-compose.yml`) — unlike the four Python backends, which already build from the repo root specifically to import `shared/` as a sibling directory. No JS monorepo tooling (npm/pnpm workspaces, Turborepo, Nx) exists anywhere in this repo today; introducing one would be new infrastructure, not an extension of an existing pattern.

## Decision

Build the shell, the base component library (loading/empty/error state, form-input pattern), and the WebSocket client utility as **identical, independently-maintained code in both `BAP/web` and `BPP/web`** — not a shared npm package imported by both. "Single, shared" is satisfied at the *design* level (the same layout/component pattern, built once and copied, not invented twice) rather than via a physical shared package.

## Alternatives Considered

- **A real shared npm package** (e.g. `shared/web-ui/`, imported via npm/pnpm workspaces) — the more "correct" long-term answer, and the same reasoning ADR-0003 used for `shared/inventory_core` (duplicated critical logic is a correctness risk) could apply here too. Rejected for now: it requires introducing monorepo tooling that doesn't exist yet, changing both web apps' Docker build context to match the backend pattern (`context: .` + copy the shared package), and making the Node lint/test CI jobs workspace-aware — a real, multi-file infrastructure change that goes well beyond what a "minimal... foundation" `[MVP]` layout shell needs. The backend's `shared/` libraries hold genuinely correctness-critical logic (crypto, concurrency-safe capacity, event ordering); a loading spinner and an empty-state banner are not in the same risk class.
- **Just build it once in BAP/web and let BPP/web diverge over time** — rejected outright: this is exactly the drift risk the tracker's word "shared" is guarding against, and would silently violate the tracker's own requirement.

## Consequences

- Any future change to the shell/component library must be applied to both apps by hand. Acceptable at this project's current scale (one team, two small apps, low change frequency on foundational UI) — the same "revisit when team/scale grows" reasoning ADR-0001 already uses for the monorepo-vs-polyrepo call.
- If this drifts in practice (the two copies actually diverge, or enough shared UI complexity accumulates that duplication becomes a real, observed problem — not a hypothetical one), that's the trigger to revisit this ADR and introduce real package sharing, not a reason to guess at it now.
- The WebSocket *server-side* piece (Django Channels routing + a minimal foundation consumer) is **not** duplicated the same way — it lives in `shared/realtime/`, since backend `shared/` package-sharing is already an established, working pattern (unlike the frontend), and the two Django backends already both import from it via their existing Docker build context.

## Related

[livetracker2.md](../../livetracker2.md) §2.4 · [ADR-0001](0001-monorepo.md) (revisit-when-it-actually-hurts reasoning) · [ADR-0003](0003-generic-inventory-core.md) (the opposite call, for genuinely correctness-critical backend logic)
