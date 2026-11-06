#!/bin/sh
# Real gap found live (2026-07-31): nothing in docker-compose.yml, this Dockerfile, or
# CI ever ran `manage.py migrate` — every prior "it works" run this project has ever
# had was against a Docker named volume that got migrated once, by hand, and never
# actually dropped since. A genuinely fresh volume (which is what CI, and any real
# first-time deployment, always starts with) boots with zero tables — /health still
# reports "ok" (deliberately DB-agnostic liveness only), so this was never caught
# until the first real fresh-volume CI run. `migrate --noinput` before exec'ing the
# real process is the standard fix for exactly this class of bug.
set -e
python manage.py migrate --noinput
# Non-fatal by design (no `set -e` trip here) — a transient Registry outage at boot
# shouldn't block this service from starting, but real identity drift (this
# container's on-disk signing key no longer matching what Registry has on file,
# e.g. after a recreated/empty secrets volume) must be loud, not silently
# discovered later as a wall of 401s on search. See check_identity_consistency's
# own docstring for the real incident this exists to catch.
#
# Hard-bounded with `timeout` (2026-10-25): a cold multi-container restart once
# took this ~90s (Registry itself was still starting, so the HTTP client's own
# retry/backoff stacked up) — harmless that time since it still finished and
# started the server, but nothing actually bounded how long it *could* stall
# startup. 15s is generous for a healthy network; a genuinely slow/unreachable
# Registry now fails this check fast instead of stretching container startup.
timeout 15 python manage.py check_identity_consistency || echo "WARNING: identity consistency check failed or timed out at startup (see above) — service is still starting, but this needs investigating before it causes real request failures. See RUNBOOK.md." >&2
exec "$@"
