# ADR-0003: Generic domain-agnostic inventory core, built once and shared

**Status:** Accepted
**Date:** 2026-07-17

## Context

`livetracker2.md` needs to support real-time inventory/slot booking across three service categories (Healthcare, Automotive, Beauty) per `project_details.md`'s "3+ service categories" KPI. Two approaches were considered before any code was written: build three separate, domain-specific inventory systems (one per category), or build one generic Resource/Slot/Availability/Capacity/Booking model shared across all three, with domain-specific differences handled through a thin adapter interface.

This mirrors the same shared-vs-duplicated tradeoff already resolved for `shared/beckn_crypto` and `shared/event_bus` in `livetracker1.md`'s Phase 1 — but for the business layer instead of the trust layer.

## Decision

Build one generic, domain-agnostic inventory/booking core (`shared/inventory_core/`) used by all three categories, with domain-specific fields (consultation type, combo services, multi-resource requirements) plugged in through a thin adapter interface rather than forked per domain. Prove it against one category (Beauty) end-to-end first, then widen to Healthcare and Automotive using the same core — a vertical-slice delivery order, not build-all-three-in-parallel.

This is also the pattern the real Beckn protocol itself already uses: the confirmed real `Item`/`Fulfillment` schemas (pulled directly from `beckn/protocol-specifications`) are themselves domain-agnostic, with domain specifics pushed into free-text `type`/`tags` fields rather than a different schema per category — this decision follows the protocol's own design philosophy, not an invented one.

## Alternatives Considered

- **Three separate inventory systems (one per domain)** — would let each domain's model fit its category perfectly with zero abstraction overhead, but means the same correctness-critical logic (concurrency-safe capacity, double-booking prevention, reservation TTLs) gets implemented three times, three times to maintain, and three times for bugs to diverge between. Rejected for the same reason `shared/beckn_crypto` wasn't forked four ways in Phase 1: duplicated critical logic is a correctness risk, not just an efficiency one.
- **Build all three domains in parallel from day one** — rejected as a delivery-order decision (not an architecture one): none of the three domains would get proven end-to-end before all three needed fixing, violating the walking-skeleton principle already validated by `livetracker1.md`'s own history (Phase 1's all-four-apps-together integration test, Phase 4.1's live trust-chain verification before wider rollout).

## Consequences

- A real query pattern and real concurrency behavior gets proven once (Beauty, `livetracker2.md` Phase 1–3) before being trusted for Healthcare/Automotive (Phase 4), rather than three unproven implementations shipped simultaneously.
- The domain adapter interface (Phase 1.5) is a real constraint on future domain additions — a fourth category later must fit through the same interface, not bypass it. Acceptable: `project_details.md` names exactly 3 categories, not "as many as needed" — no over-engineering for a domain #4 that isn't requested.
- If Healthcare or Automotive turns out to need something the generic core genuinely can't express (not just something inconvenient), that's a real signal to revisit this ADR, not a reason to quietly fork around it.

## Related

[livetracker2.md](../../livetracker2.md) Phase 1 · [ARCHITECTURE.md](../../ARCHITECTURE.md) §System Overview (`shared/` library pattern) · confirmed real schema research citing `beckn/protocol-specifications`
