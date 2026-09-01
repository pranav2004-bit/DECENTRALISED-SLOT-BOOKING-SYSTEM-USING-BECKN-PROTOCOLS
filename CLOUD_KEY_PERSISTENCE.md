# Cloud Deployment: Private Key Persistence

## Scope

Registry, Gateway, and every BAP/BPP instance generate their own signing/encryption private key on first startup (see [SECURITY.md](SECURITY.md) "Local/dev key persistence" for the original bug and the local-dev fix). This document covers the same requirement for the **cloud production server** specifically — 3 prerequisites, all must hold, or a redeploy silently generates new keys and breaks every participant's established trust relationship with Registry.

**Participants requiring their own key (7 total):** Registry, Gateway, BAP-X, BAP-Y, BPP-Beauty, BPP-Medical, BPP-Automotive. Frontends (`*-web`) hold no key of their own — they are not separate participants.

Each key is stored as a file on disk (a Docker named volume), never in a database — private keys must never be centrally stored or shared across participants.

## 1. Docker volume creation

Each of the 7 participants needs its own persistent Docker volume, created once on the cloud server, matching the existing `docker-compose.yml` pattern (`registry-secrets`, `gateway-secrets`, `bap-secrets`, `bpp-secrets`, `bpp-medical-secrets`, `bpp-automotive-secrets`, `bap-y-secrets`). No shared/central volume — one volume per participant.

## 2. CI/CD script safety

The redeploy pipeline must only stop and recreate **containers**, never remove **volumes**. Any step that runs a "clean slate" command touching volumes (e.g. `docker compose down -v`, `docker volume rm`, `docker volume prune`) destroys the saved keys and reproduces the original bug. Audit the deploy script explicitly for this before first production use.

## 3. Backup and restore plan for the volume

Volumes protect against redeploys, not against loss of the server itself (crash, accidental deletion, disk failure). Required:

- **Backup:** copy each of the 7 key files to a private, encrypted cloud storage bucket, separate from the server, once after each key is first generated. Keys are static once created — this is not a recurring nightly job, just confirmed periodically (e.g. monthly).
- **Restore:** a documented, tested procedure to put a backed-up key back into its volume on a new/replacement server. A backup with no known restore procedure is not a working safety net.
- **Access:** the backup bucket must be as locked-down as the keys themselves — private access only, never a public or shared location.
