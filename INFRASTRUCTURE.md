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
| Production | Full topology, sized per real traffic once Pramaan certification (protocol_compliance_notes_v1.1.md §E.1) clears it for Go-Live |

## `infra/` Structure (skeleton)

```
infra/
  README.md          — how to use this module, current placeholder status
  variables.tf        — shared variable definitions (project, component, environment, owner, lifecycle_stage tags)
  versions.tf          — Terraform + provider version constraints
```

No provider blocks or real resources are defined yet — see `infra/README.md` for the activation trigger.
