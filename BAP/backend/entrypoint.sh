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
exec "$@"
