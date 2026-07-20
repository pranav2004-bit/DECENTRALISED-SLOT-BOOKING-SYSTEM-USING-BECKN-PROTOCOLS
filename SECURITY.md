# Security

## Reporting

If you find a security issue in this codebase, do not open a public issue. Report it privately to the project maintainer first.

## Threat Model Summary

This system's trust boundary is the Beckn/ONDC Registry (see [protocol_compliance_notes_v1.1.md](protocol_compliance_notes_v1.1.md) §A.5 — the Registry is a Public Key Infrastructure). The primary risks in scope for the foundation/trust-layer build ([livetracker1.md](livetracker1.md) Phase 0–4):

- **Participant impersonation** — mitigated by mandatory Ed25519 request signing on every endpoint (§C of protocol_compliance_notes_v1.1.md), verified against Registry-sourced public keys.
- **Private key compromise** — mitigated by never transmitting or logging private keys (§A.5), secrets-manager-only storage (below), and a documented rotation path (re-`/subscribe` with a new `key_pair`, per §B.4).
- **Registry/Gateway abuse** — mitigated by rate limiting at the real ONDC thresholds (Subscribe 10/min, Lookup 7,600/min — §B.6) and input validation on every API surface.
- **Replay attacks** — mitigated by time-bound, single-use on_subscribe challenges and `created`/`expires` bounds on every signed request.
- **Data exposure** — the Registry stores only minimum participant metadata (no catalogs/orders/payments — see [registry_details_v1.1.md](registry/registry_details_v1.1.md) §8); customer personal data handled by BAP/BPP is subject to DPDP Act obligations (protocol_compliance_notes_v1.1.md §E.4).

## Secrets & Key Management

**Strategy for `[MVP]`/`[PILOT]`:** secrets (Django secret keys, database credentials, signing/encryption private keys, payment gateway API keys) are injected via environment variables sourced from the deployment platform's secrets store (e.g., Docker secrets locally, a managed secrets manager in Dev/Staging/Prod). They are **never** committed to source control and never inlined in `.env.example` — only placeholder values live there.

**Deferred to `[ENT]`:** HSM/KMS-backed key custody for signing/encryption private keys. Evaluated but not required at foundation stage; revisit when transaction volume or compliance requirements (see protocol_compliance_notes_v1.1.md §E) justify the added operational complexity.

**Key rotation policy:** manual at `[MVP]` — re-run the Subscribe flow with a new `key_pair` before the current one's `valid_until` expires (protocol_compliance_notes_v1.1.md §B.4). No dedicated rotation endpoint exists in the protocol; this is the confirmed mechanism, not a placeholder.

**Enforcement:** a pre-commit secrets-scanning hook ([.pre-commit-config.yaml](.pre-commit-config.yaml), using `detect-secrets`) blocks commits containing anything that looks like a real key, token, or credential. This runs locally via pre-commit and again in CI ([.github/workflows/ci.yml](.github/workflows/ci.yml)) as a non-bypassable gate.

## Signing

Every inter-participant request is signed — see [protocol_compliance_notes_v1.1.md](protocol_compliance_notes_v1.1.md) §C for the exact `Authorization`/`X-Gateway-Authorization` header format, Ed25519 algorithm, and BLAKE-512 digest scheme. No endpoint (including `/lookup`) is unauthenticated.

## Dependency & Container Scanning

CI runs SCA (dependency vulnerability scan) and container image scanning on every PR — see [.github/workflows/ci.yml](.github/workflows/ci.yml). Findings block merge unless explicitly accepted with a documented reason.

## Compliance

This project is a private, self-contained Beckn network (see [livetracker1.md](livetracker1.md)'s scope declaration) and does not pursue real-ONDC certification. [protocol_compliance_notes_v1.1.md](protocol_compliance_notes_v1.1.md) §E documents the full real-ONDC compliance/certification layer (Pramaan certification, ONDC Network Participant Agreement, IGM/GRO designation) as reference facts about the real network only — these are marked `[N/A]` in [livetracker1.md](livetracker1.md) Phase 4.4 and would only become relevant if real ONDC integration is pursued later. DPDP Act obligations around customer personal data are the one item in that checklist kept as general good practice, independent of ONDC.
