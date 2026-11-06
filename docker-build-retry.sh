#!/bin/bash
# Wraps `docker compose build` with retries. Covers residual DNS/registry blips that
# the static-DNS fix (daemon.json "dns", Docker Desktop Settings > Resources > Network
# > DNS) reduces but can't fully eliminate — Docker Hub auth/registry lookups happen
# inside the Docker Desktop WSL2 VM, a layer static-DNS config doesn't fully reach.
# Confirmed live 2026-11-05: identical build succeeded on retry #2 after failing #1
# with a transient "lookup auth.docker.io: no such host" error.
#
# Usage: ./docker-build-retry.sh <service> [service...]
set -u

if [ "$#" -eq 0 ]; then
  echo "Usage: ./docker-build-retry.sh <service> [service...]" >&2
  exit 1
fi

MAX_ATTEMPTS=6
SLEEP_SECS=30

for i in $(seq 1 $MAX_ATTEMPTS); do
  echo "== Build attempt $i/$MAX_ATTEMPTS: docker compose build $* =="
  if docker compose build "$@"; then
    echo "== Build succeeded on attempt $i =="
    exit 0
  fi
  if [ "$i" -lt "$MAX_ATTEMPTS" ]; then
    echo "== Attempt $i failed, retrying in ${SLEEP_SECS}s (transient DNS/registry errors are the expected cause — see header) =="
    sleep $SLEEP_SECS
  fi
done

echo "== All $MAX_ATTEMPTS attempts failed. This is no longer transient — check Docker Desktop status, network connectivity, and the actual build error above (not just DNS) before retrying further. ==" >&2
exit 1
