#!/bin/bash
# Staged startup — brings up the full stack in waves (DBs/caches -> trust layer ->
# backends -> workers -> frontends) instead of docker-compose's default of starting
# all 24 containers at once. Fixes Problem #3 from the local root-cause investigation:
# starting everything simultaneously creates the worst-case CPU/RAM spike every time,
# which is what pushed a resource-constrained machine into swapping and crashing.
#
# Usage: ./staged-up.sh
set -e

WAIT_TIMEOUT=480 # later stages need more margin — by the last backend, everything
# earlier is already running and competing for CPU, confirmed live 2026-09-01
# (bap-y-backend took just over 5 minutes; genuinely started fine, just slowly)

echo "== Stage 1/5: Caches (Postgres moved to Neon 2026-09-02 — no local DB containers) =="
docker compose up -d --wait --wait-timeout $WAIT_TIMEOUT \
  bap-cache bpp-cache gateway-cache registry-cache

echo "== Stage 2/5: Trust layer (Registry + Gateway) =="
docker compose up -d --wait --wait-timeout $WAIT_TIMEOUT \
  registry beckn-gateway key-rotation-scheduler

echo "== Stage 3/5: Backends (one at a time — 5 backends starting together still exceeds"
echo "   the healthcheck probe's 3s timeout under CPU contention, confirmed live 2026-09-01) =="
for svc in bap-backend bpp-backend bpp-medical-backend bpp-automotive-backend bap-y-backend; do
  echo "  -> $svc"
  docker compose up -d --wait --wait-timeout $WAIT_TIMEOUT "$svc"
done

echo "== Stage 4/5: Background workers =="
docker compose up -d --wait --wait-timeout $WAIT_TIMEOUT \
  bpp-worker bpp-medical-worker bpp-automotive-worker

echo "== Stage 5/5: Frontends =="
docker compose up -d --wait --wait-timeout $WAIT_TIMEOUT \
  bap-web bpp-web bpp-medical-web bpp-automotive-web bap-y-web

echo ""
echo "All 5 stages complete. Run 'docker compose ps' to confirm status."
