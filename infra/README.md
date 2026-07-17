# Infrastructure (Terraform) — Placeholder

See [INFRASTRUCTURE.md](../INFRASTRUCTURE.md) for the full strategy and why this is intentionally minimal right now.

**Activation trigger:** when `livetracker1.md` Phase 3 (Participant Onboarding) needs a real, publicly reachable Staging HTTPS endpoint to complete this project's own domain-ownership verification and on_subscribe callback delivery (design modeled on the real ONDC network's approach — see [livetracker1.md](../livetracker1.md)'s scope declaration; this project's own Registry, not the real ONDC network, is what's being verified against) — neither of which `docker compose` on a laptop can provide.

**What exists now:** shared variable/version conventions only (`variables.tf`, `versions.tf`) — no provider block, no resources. This lets tagging/naming conventions get agreed and reused once a cloud provider is chosen, without provisioning anything (and incurring cost) before there's real application code to host.
