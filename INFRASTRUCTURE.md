# Infrastructure & Cost Governance

## IaC Tool

**Decision: Terraform.** Widely supported, provider-agnostic (keeps the cloud choice reversible), and matches the team's existing familiarity assumption. Module skeleton lives in [infra/](infra/), currently a placeholder — see "Current Status" below for why it isn't fully built out yet.

## Current Status — Deliberately Minimal, Not an Oversight

Local and Dev environments run entirely via `docker-compose.yml` — no cloud infrastructure is needed for either. The first genuine infrastructure need arises at **Staging**, because domain-ownership verification for this project's own Registry (per [protocol_compliance_notes_v1.1.md](protocol_compliance_notes_v1.1.md) §B.2, whose OCSP-validation approach this project follows as a design reference — see [livetracker1.md](livetracker1.md)'s scope declaration) requires a real, publicly reachable HTTPS domain with a valid SSL certificate — something `docker compose` on a laptop cannot provide.

Building out full Terraform modules against a cloud provider with no account yet chosen and no Staging-onboarding-ready application code (Phase 1–3 not yet complete) would be premature — provisioning real cloud spend for infrastructure nothing uses yet is the over-engineering this project explicitly avoids. `infra/` is scaffolded with the *convention* (structure, tagging, variables) now, and gets filled in with real provider resources when Phase 3 (Participant Onboarding) needs a real Staging endpoint to onboard against.

## Resource Tagging Convention

Every provisioned cloud resource (once `infra/` is filled in) must carry these tags, enforced via Terraform variables, not left to convention alone:

| Tag | Example | Purpose |
|---|---|---|
| `project` | `beckn-slot-booking` | Cost rollup across all resources |
| `component` | `registry` \| `beckn-gateway` \| `bap` \| `bpp` | Cost attribution per application |
| `environment` | `dev` \| `staging` \| `preprod` \| `production` | Cost attribution per environment |
| `owner` | team/contact identifier | Who to page, who approved the spend |
| `lifecycle_stage` | `mvp` \| `pilot` \| `beta` \| `ent` | Matches the lifecycle tags used throughout [livetracker1.md](livetracker1.md) — makes it visible which spend belongs to which maturity stage |

## Non-Prod Cost Governance

- **Non-prod environments (Dev, Staging) get scheduled teardown/scale-to-zero outside working hours** once real cloud resources exist for them — not run 24/7 by default. Implemented as a scheduled Terraform/CI job once `infra/` is real; not needed while Local/Dev run on `docker compose` alone.
- **Right-sizing baseline:** smallest viable instance/tier for Dev and Staging (this is validation infrastructure, not load-bearing production traffic). No autoscaling configured below Production — autoscaling non-prod is cost without benefit at this stage.
- **No premature read replicas, multi-region, or reserved capacity** below `[BETA]` — all deferred, consistent with the lifecycle tags already used across `livetracker1.md`.

## Environment → Infrastructure Mapping

| Environment | Infrastructure |
|---|---|
| Local | `docker-compose.yml` on a developer machine — no cloud cost |
| Dev | `docker-compose.yml`, possibly a shared always-on host later — no cloud cost yet |
| Staging | First real cloud footprint — minimal single-instance-per-app, scheduled teardown |
| Pre-Production | Mirrors Production topology at smaller scale, for final validation |
| Production | Full topology, sized per real traffic once the production-promotion gates in [ENVIRONMENTS.md](ENVIRONMENTS.md) (general-good-practice data-handling review — not real-ONDC Pramaan certification, which is `[N/A]` for this private network per `livetracker1.md`'s scope declaration) clear it for Go-Live |

## `infra/` Structure (skeleton)

```
infra/
  README.md          — how to use this module, current placeholder status
  variables.tf        — shared variable definitions (project, component, environment, owner, lifecycle_stage tags)
  versions.tf          — Terraform + provider version constraints
```

No provider blocks or real resources are defined yet — see `infra/README.md` for the activation trigger.

## Scale-Readiness Design (documented, not built) `[ENT]`

Same "skeleton now, fill in when real traffic demands it" discipline as `infra/` itself (see "Current Status" above) — this section is a design, not a migration plan to execute today. Today's actual traffic is one dev session at a time; nothing below is activated until real multi-provider load makes it necessary, and every claim here is checked against the real, already-built data model in `shared/inventory_core/models.py`, not aspirational.

### Horizontal scaling — BPP/BAP

Both apps' web tier (Daphne, per each Dockerfile's `HEALTHCHECK` comment: "daphne is single-process for this project's Dockerfile, confirmed directly, no `--workers` flag") is stateless per-request and horizontally scalable behind a load balancer — sessions live in Redis (`SESSION_ENGINE = "...backends.cache"`), not in-process, so any replica can serve any request.

**One real, already-identified blocker, not a hypothetical one:** `BPP/backend/core/reconciliation.py`'s `start_reconciliation_loop()` guards itself with an in-process `threading.Lock` + module-level `_started` flag — correct for today's single-process deployment, but naively running the same guard in N horizontally-scaled replicas would start N independent reconciliation loops. Each loop's own primitives (`sweep_expired_holds`, `sweep_completed_bookings`, `reconcile_catalog_cache`) are individually safe under that — every mutation goes through `select_for_update()`-guarded row locks, so concurrent sweeps can't double-release or double-complete the same row — but it's still N-times redundant DB/Redis load doing the same real work, not a genuine scaling win. Documented next step, not built: move the reconciliation loop to a single dedicated worker process/replica (a `RECONCILIATION_WORKER=true` env-gated entrypoint, or a separate deployment with `replicas: 1`), not something every web replica runs.

### Inventory partitioning — by provider and region

The real, already-built key for provider-level partitioning is `Resource.owner_ref` (the opaque business-account id every `Slot` and `Booking` transitively hangs off via `Slot.resource`) — every table in the hot path (`Slot`, `Booking`) already carries a path back to one `Resource`, so a provider's entire inventory co-locates naturally under this key. The existing `slot_resource_time_idx` (`Slot`'s own compound index on `(resource, start_time, end_time)`, built in Phase 1.1) already matches this access pattern — a real artifact this design points to, not one invented for this document.

Region-level partitioning is honestly weaker: the only region-adjacent field that exists is `Resource.domain_data["location_id"]` → `Location.city` (Phase 4.3's multi-location support), and it's optional — a `Resource` created before Phase 4.3, or one whose owner never set a location, has no region key at all. Region-based sharding is documented here as a second-tier strategy layered on top of provider-based partitioning once `location_id` adoption is high enough to be a reliable key, not a today-ready one.

### Hotspot management — a small number of highly-demanded slots

The correctness primitive already exists and doesn't need new design: `Slot.capacity_remaining`'s atomic, `select_for_update()`-locked decrement (Phase 1.2) already prevents overselling a single hot slot under concurrent writers. The real scaling question is read amplification (many customers repeatedly searching/polling the same popular resource), not correctness — and BPP's read-through catalog cache (§3.8, `catalog_cache.py`) is the already-built mitigation for that. If write contention on one specific hot slot's row lock ever becomes the bottleneck (not observed at today's traffic), the documented next step is switching that slot's confirm path from the current pessimistic `select_for_update()` to optimistic concurrency (a version column + retry-on-conflict) — scoped to genuinely hot rows only, not a blanket replacement of a locking scheme that's correct and unproblematic everywhere else.

### Backpressure

`shared/event_bus`'s own module docstring already states the real, current architectural fact plainly: "no multi-worker consumer pool exists for this bus anywhere in the codebase." That single-consumer design is what gives today's event bus its per-entity ordering guarantee (`events.py`'s own docstring) — but it also means there is currently no backpressure at all: if a consumer ever fell behind, the underlying Redis list would grow unboundedly with no shedding or slow-down signal back to producers. Documented design, not built (no real consumer lag has ever been observed to justify building it — the same "no invented baseline" discipline already applied to §3.8's latency panel): bound the queue (`LTRIM` to a max depth, or migrate to a capped Redis Stream) paired with an explicit shed policy for what happens past that bound, and have `publish_event()` check queue depth and apply backpressure to the publisher (block briefly or return a real, surfaced error) rather than let an unbounded queue silently mask a stuck consumer.

### Explicitly not built

No premature horizontal scaling, no speculative sharding, no infrastructure spend without real traffic to justify it — consistent with `infra/`'s own "Current Status" section above and every lifecycle-tagged deferral already made throughout this document. Activation trigger for all four items above: real multi-provider production traffic (per the Environment → Infrastructure Mapping table's own Production row), not a milestone on a calendar.
