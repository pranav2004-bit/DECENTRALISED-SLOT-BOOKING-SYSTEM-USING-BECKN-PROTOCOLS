# ADR-0005: Gateway routes only /search; the other 9 actions dispatch directly BAP<->BPP

**Status:** Accepted
**Date:** 2026-07-31

## Context

`livetracker2.md` originally implemented all 10 action pairs (search, select, init, confirm, status, cancel, update, track, rating, support — each with its `on_X` callback) as routing through the Beckn Gateway, mirroring `dispatch_search`/`relay_on_search`'s validate-ACK/forward split for every action. A later re-read of the real protocol (`protocol_compliance_notes_v1.1.md` §P) found this was broader than the spec requires: only `/search` is meant to route through a Gateway for BPP discovery. Once a BAP has a specific `bpp_id`/`bpp_uri` from a BPP's own `on_search` response, every subsequent action in that transaction is meant to go directly BAP<->BPP — the Gateway has no further role once discovery is complete.

## Decision

`livetracker4.md` §1.1-§1.4 moved the 9 discovery-complete actions off Gateway:

- BAP dispatches directly to the BPP it already knows about, via a fresh `resolve_subscribed_bpp()` staleness re-check (§1.1) and a per-counterparty circuit-breaker client (`get_bpp_client`).
- BPP receives directly (§1.2) and sends its own `on_X` callback straight back to `context["bap_uri"]` via `get_bap_client`, instead of relaying through Gateway.
- Gateway's own dead code for the 9 actions (`routing.py`'s `validate_and_ack_X`/`dispatch_X`/`relay_on_X` trio, `views.py`'s view functions, `urls.py`'s routes, and the corresponding ~91 tests) was deleted in §1.4, once BAP's and BPP's own test suites were confirmed to carry equivalent coverage — including scenarios (a stale-SUBSCRIBED BPP, an unreachable counterparty) that used to be Gateway's own responsibility to test.

`/search` and `/on_search` are unchanged — Gateway is still the only path for initial discovery.

## Alternatives Considered

- **Keep Gateway on the critical path for all 10 actions** — was the original (over-broad) implementation. Rejected once re-verified against the real protocol: it added an unnecessary hop's worth of latency and a single shared point of failure to 9 actions that don't need it, in exchange for nothing the protocol actually asks for.
- **Keep Gateway's routing code in place but unused, in case network-wide visibility is wanted later** — rejected per this project's own no-speculative-code discipline (`CLAUDE.md`/session convention: don't build for hypothetical future requirements). Dead code that mirrors 9 actions is a real maintenance and audit-surface cost; if network-wide visibility is genuinely needed later, it can be added back deliberately with its own test suite, not left half-alive.

## Consequences

- **Reduced network-wide visibility.** Gateway previously saw every action in every transaction across the whole network; now it only sees `/search`/`/on_search`. Any future network-wide audit trail, fraud-detection, or cross-participant monitoring feature that wants to observe select/init/confirm/etc. traffic can no longer read it off Gateway — it would need its own reporting path (e.g., BAP/BPP emitting events, or Registry-side aggregation), not a Gateway tap. Accepted as this project's private, single-Gateway-instance network has no such requirement stated in `project_details.md`; the real ONDC protocol places this responsibility on Registry-side reporting APIs, not Gateway, which is consistent with this decision.
- **Genuine latency and blast-radius reduction.** 9 actions now make one fewer network hop and no longer share Gateway's single circuit-breaker/availability as a dependency — a Gateway outage after discovery no longer blocks an in-flight booking's select/init/confirm/etc.
- **Gateway's test suite shrank from 125 to 34 tests** (the 91 removed instances of dead-code-covering tests), with equivalent coverage now living in BAP's/BPP's own suites (verified live: BAP 224/224, BPP 323/323, Gateway 34/34, all 9 retired routes confirmed 404, `/search` confirmed still functioning — `livetracker4.md` §1.4's Test Gate).

## Related

[livetracker4.md](../../livetracker4.md) §1.1-§1.4 · [protocol_compliance_notes_v1.1.md](../../protocol_compliance_notes_v1.1.md) §P · [ARCHITECTURE.md](../../ARCHITECTURE.md) §System Overview (Gateway's "discovery routing (search → on_search)" description, already consistent with this decision) · [beckn_gateway_details_v1.1.md](../../beckn-gateway/beckn_gateway_details_v1.1.md)
