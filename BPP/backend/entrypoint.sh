#!/bin/sh
# Real gap found live (2026-07-31): nothing in docker-compose.yml, this Dockerfile, or
# CI ever ran `manage.py migrate` — every prior "it works" run this project has ever
# had was against a Docker named volume that got migrated once, by hand, and never
# actually dropped since. A genuinely fresh volume (which is what CI, and any real
# first-time deployment, always starts with) boots with zero tables — /health still
# reports "ok" (deliberately DB-agnostic liveness only), so this was never caught
# until the first real fresh-volume CI run. `migrate --noinput` before exec'ing the
# real process is the standard fix for exactly this class of bug.
#
# Second real gap found live (livetracker7.md Phase 2, 2026-08-22): plain `migrate
# --noinput` is unsafe when more than one container from this same image starts
# against a genuinely fresh, empty database at once (bpp-backend + bpp-worker; newly
# exposed by Phase 2's bpp-medical-backend + bpp-medical-worker and bpp-automotive-
# backend + bpp-automotive-worker, the first genuinely-fresh multi-container DB pairs
# this project has created) — both processes race to create the same brand-new
# schema, and the loser crashes with a real "relation already exists" error, not a
# harmless no-op. `migrate_locked` (core/management/commands/migrate_locked.py) wraps
# the same migrate call in a Postgres advisory lock so concurrent starts serialize
# instead of racing.
set -e
python manage.py migrate_locked
exec "$@"
