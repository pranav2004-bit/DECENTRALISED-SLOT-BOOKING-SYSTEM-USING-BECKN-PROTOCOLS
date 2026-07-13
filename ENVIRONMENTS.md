# Environments

## Environment List

| Environment | Purpose | Registry Target |
|---|---|---|
| **Local** | Individual developer machine, `docker compose up` | Local Registry container (no real ONDC network) |
| **Dev** | Shared integration environment for the team | Local/dev Registry container, or ONDC staging once Phase 3 onboarding begins |
| **Staging** | Pre-production validation | ONDC **staging** registry (`https://staging.registry.ondc.org`) |
| **Pre-Production** | Final validation before go-live | ONDC **pre-prod** registry (`https://preprod.registry.ondc.org`) |
| **Production** | Live traffic | ONDC **production** registry (`https://prod.registry.ondc.org`) |

**Important — this is not "one registry, five configs."** Per [protocol_compliance_notes_v1.1.md](protocol_compliance_notes_v1.1.md) §B.1, ONDC's Staging, Pre-Production, and Production registries are **three independently whitelisted deployments**. Each requires its own Network Participant Portal approval and its own onboarding pass (Phase 3 of [livetracker1.md](livetracker1.md)) — reaching `SUBSCRIBED` in Staging does not carry over to Pre-Prod or Production.

## Parity Rules

- **Local and Dev** run all four applications + 3 Postgres databases + Redis via `docker-compose.yml`, with a local Registry (no real ONDC dependency) so development doesn't require network whitelisting.
- **Staging/Pre-Prod/Production** point at the real corresponding ONDC registry environment. Application code must be identical across these three — only configuration (registry endpoint, keys, subscriber_id) differs.
- No environment-specific code branches (`if env == "prod"` scattered through business logic). Environment differences are confined to configuration, per the 12-factor approach in [ARCHITECTURE.md](ARCHITECTURE.md).

## Configuration Strategy

All configuration is environment-variable-driven (12-factor). Each app ships a `.env.example` documenting every required variable with a placeholder value — never a real one. Real values live in `.env` (git-ignored) locally, and in the deployment platform's secrets store in Dev/Staging/Prod (see [SECURITY.md](SECURITY.md) for the secrets management approach).

Every app validates its configuration on startup and fails fast — with a clear error naming the missing/invalid variable — rather than starting in a partially-configured state.

## Environment Promotion Gates

| Transition | Gate |
|---|---|
| Local → Dev | CI green on the PR (lint, unit tests, SCA, SAST, secrets scan — see [.github/workflows/ci.yml](.github/workflows/ci.yml)); merge to `main` auto-deploys to Dev. |
| Dev → Staging | Manual approval by the project maintainer, after Phase 2/3 test gates relevant to the change are confirmed green in Dev. Staging onboarding additionally requires its own ONDC Network Participant Portal whitelisting (§B.2 of protocol_compliance_notes_v1.1.md) — this is a one-time per-environment prerequisite, not a per-deploy gate. |
| Staging → Pre-Production | Manual approval, plus the specific `livetracker1.md` phase being promoted must have completed its own Phase Exit — Testing & Sign-off. |
| Pre-Production → Production | Manual approval **and** the full compliance/certification checklist in `livetracker1.md` Phase 4.4 (Pramaan certification, Network Participant Agreement, GRO/IGM readiness, DPDP review) — production traffic is blocked on this regardless of CI status. |

No automatic promotion beyond Local → Dev exists at `[MVP]`/`[PILOT]` stage — every later transition is a deliberate, manual decision, consistent with the non-technical gates documented in [protocol_compliance_notes_v1.1.md](protocol_compliance_notes_v1.1.md) §E.

## Per-App `.env.example` Locations

- [registry/.env.example](registry/.env.example)
- [beckn-gateway/.env.example](beckn-gateway/.env.example)
- [BAP/backend/.env.example](BAP/backend/.env.example)
- [BAP/web/.env.example](BAP/web/.env.example)
- [BPP/backend/.env.example](BPP/backend/.env.example)
- [BPP/web/.env.example](BPP/web/.env.example)
