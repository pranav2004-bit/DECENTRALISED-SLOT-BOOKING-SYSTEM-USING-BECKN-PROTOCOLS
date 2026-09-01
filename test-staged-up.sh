#!/bin/bash
# Validates staged-up.sh, not the full stack itself. Two real failure modes checked
# (no Docker/no running containers required for either):
#   1. Syntax errors in the script.
#   2. Service drift — every service docker-compose.yml actually defines must appear in
#      staged-up.sh's stages exactly once (a service added later and never added to a
#      stage would silently never start; a typo'd name would silently no-op).
set -e

FAIL=0

echo "Check 1/2: script syntax"
if bash -n staged-up.sh; then
  echo "  OK"
else
  echo "  FAILED — syntax error in staged-up.sh"
  FAIL=1
fi

echo "Check 2/2: every compose service is staged exactly once"
real_services=$(docker compose config --services | sort)
staged_services=$(grep -v '^#' staged-up.sh | grep -v 'echo\|set -e\|WAIT_TIMEOUT=' \
  | tr -s ' \t\\;' '\n' \
  | grep -E '^[a-z0-9-]+$' \
  | grep -vE '^(docker|compose|up|-d|--wait|--wait-timeout|for|in|do|done|svc)$' \
  | sort -u)

missing=$(comm -23 <(echo "$real_services") <(echo "$staged_services"))
extra=$(comm -13 <(echo "$real_services") <(echo "$staged_services"))
dupes=$(echo "$staged_services" | sort | uniq -d)

if [ -n "$missing" ]; then
  echo "  FAILED — services in docker-compose.yml but missing from staged-up.sh:"
  echo "$missing" | sed 's/^/    /'
  FAIL=1
fi
if [ -n "$extra" ]; then
  echo "  FAILED — services in staged-up.sh that don't exist in docker-compose.yml:"
  echo "$extra" | sed 's/^/    /'
  FAIL=1
fi
if [ -n "$dupes" ]; then
  echo "  FAILED — services staged more than once:"
  echo "$dupes" | sed 's/^/    /'
  FAIL=1
fi
if [ "$FAIL" -eq 0 ]; then
  echo "  OK — $(echo "$real_services" | wc -l) services, each staged exactly once"
fi

exit $FAIL
