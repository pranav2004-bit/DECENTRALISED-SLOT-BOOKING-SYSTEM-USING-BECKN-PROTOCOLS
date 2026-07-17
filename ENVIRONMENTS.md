# Environments

> **Scope note (2026-07-17):** this project builds a **private, self-contained Beckn network** — see [livetracker1.md](livetracker1.md)'s top-of-file scope declaration. Every environment below points at **this project's own Registry**, deployed to progressively more real infrastructure. The Staging/Pre-Production/Production naming and the "independently whitelisted per environment" pattern is modeled on how the real ONDC network structures its environments (per [protocol_compliance_notes_v1.1.md](protocol_compliance_notes_v1.1.md) §B.1) — a reasonable pattern to copy for a private network too — but no environment here connects to an actual `*.registry.ondc.org` endpoint, and no ONDC Network Participant Portal whitelisting is required at any stage.

## Environment List

| Environment | Purpose | Registry Target |
|---|---|---|
| **Local** | Individual developer machine, `docker compose up` | This project's local Registry container |
| **Dev** | Shared integration environment for the team | This project's dev-deployed Registry instance |
| **Staging** | Pre-production validation | This project's own Registry, deployed to a real, publicly reachable staging environment |
| **Pre-Production** | Final validation before go-live | This project's own Registry, deployed to a real pre-production environment |
| **Production** | Live traffic | This project's own Registry, deployed to production infrastructure |

**Important — this is not "one registry, five configs."** Modeled on the real ONDC network's pattern (per [protocol_compliance_notes_v1.1.md](protocol_compliance_notes_v1.1.md) §B.1) of Staging, Pre-Production, and Production being **three independently deployed environments**, this project's own Staging/Pre-Prod/Production Registry deployments are likewise kept independent — reaching a healthy state in Staging does not carry over to Pre-Prod or Production. There is no real-ONDC Network Participant Portal approval involved at any stage; the only onboarding that happens is participants (BAP/BPP) subscribing to *this project's own* Registry (Phase 3 of [livetracker1.md](livetracker1.md)).

## Parity Rules

- **Local and Dev** run all four applications + 3 Postgres databases + Redis via `docker-compose.yml`, with a local Registry.
- **Staging/Pre-Prod/Production** point at this project's own Registry, deployed to progressively more real infrastructure. Application code must be identical across these three — only configuration (registry endpoint, keys, subscriber_id) differs.
- No environment-specific code branches (`if env == "prod"` scattered through business logic). Environment differences are confined to configuration, per the 12-factor approach in [ARCHITECTURE.md](ARCHITECTURE.md).

## Configuration Strategy

All configuration is environment-variable-driven (12-factor). Each app ships a `.env.example` documenting every required variable with a placeholder value — never a real one. Real values live in `.env` (git-ignored) locally, and in the deployment platform's secrets store in Dev/Staging/Prod (see [SECURITY.md](SECURITY.md) for the secrets management approach).

Every app validates its configuration on startup and fails fast — with a clear error naming the missing/invalid variable — rather than starting in a partially-configured state. Verified for real in Phase 1.1 (Registry): removing `DJANGO_SECRET_KEY` from `.env` correctly raised `django.core.exceptions.ImproperlyConfigured: Set the DJANGO_SECRET_KEY environment variable` on startup.

**Gotcha (found the hard way in Phase 1.1, worth keeping in mind for every future `.env.example`):** do not put inline `# comment` text after a value on the same line, e.g. `DJANGO_DEBUG=true   # must be false outside local/dev`. `django-environ`'s parser doesn't strip trailing comments — the whole remainder of the line becomes part of the value. For a boolean this silently evaluates to `False` instead of raising an error, which is worse than a loud failure; for `DATABASE_URL` it produces a corrupted connection string that fails at connect time (also caught for real in Phase 1.1/1.3 — every one of Registry, BAP, and BPP's `.env.example` had this exact bug on their `DATABASE_URL` line). Put explanatory comments on their own line above the variable instead — verified that this doesn't break `django-environ` parsing.

**Related gotcha, same root cause:** if that variable's placeholder value also needs a `detect-secrets` allowlist pragma (see [SECURITY.md](SECURITY.md)), you cannot use `# pragma: allowlist secret` inline for the same reason — and moving it to its own line above doesn't work either, because `detect-secrets` only honors the pragma on the *same line* as the flagged content. The correct fix for `.env.example` files (as opposed to YAML files like `docker-compose.yml`, where inline pragma comments work fine) is to let `detect-secrets scan` flag it and then run `detect-secrets audit .secrets.baseline`, answering "yes, safe to commit" for known placeholder credentials — this records `is_secret: false` in the baseline, which is what actually suppresses the finding, not the comment.

## Environment Promotion Gates

| Transition | Gate |
|---|---|
| Local → Dev | CI green on the PR (lint, unit tests, SCA, SAST, secrets scan — see [.github/workflows/ci.yml](.github/workflows/ci.yml)); merge to `main` auto-deploys to Dev. |
| Dev → Staging | Manual approval by the project maintainer, after Phase 2/3 test gates relevant to the change are confirmed green in Dev. No real-ONDC whitelisting is involved — the only prerequisite is this project's own participants (BAP/BPP) successfully subscribing to this project's own Staging Registry deployment. |
| Staging → Pre-Production | Manual approval, plus the specific `livetracker1.md` phase being promoted must have completed its own Phase Exit — Testing & Sign-off. |
| Pre-Production → Production | Manual approval **and** the general-good-practice items in `livetracker1.md` Phase 4.4 (DPDP-style data-handling review). The real-ONDC-specific items in that checklist (Pramaan certification, Network Participant Agreement, GRO/IGM readiness) are marked `[N/A]` there — they only become relevant if real ONDC integration is pursued later — and do not block production traffic for this project. |

No automatic promotion beyond Local → Dev exists at `[MVP]`/`[PILOT]` stage — every later transition is a deliberate, manual decision, consistent with the non-technical gates documented in [protocol_compliance_notes_v1.1.md](protocol_compliance_notes_v1.1.md) §E.

## Per-App `.env.example` Locations

- [registry/.env.example](registry/.env.example)
- [beckn-gateway/.env.example](beckn-gateway/.env.example)
- [BAP/backend/.env.example](BAP/backend/.env.example)
- [BAP/web/.env.example](BAP/web/.env.example)
- [BPP/backend/.env.example](BPP/backend/.env.example)
- [BPP/web/.env.example](BPP/web/.env.example)
