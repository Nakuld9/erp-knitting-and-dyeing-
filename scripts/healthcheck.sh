#!/usr/bin/env bash
#
# Probe a running Textile ERP instance. Exit 0 only when it is READY (process up
# AND database reachable). Suitable for cron/monitoring, a Docker HEALTHCHECK, or
# a load-balancer health probe.
#
# Env:  BASE_URL (default http://127.0.0.1:8000)
# Usage:  BASE_URL=https://erp.example.com ./scripts/healthcheck.sh
set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
code="$(curl -fsS -o /dev/null -w '%{http_code}' "$BASE_URL/health/ready" 2>/dev/null || echo 000)"

if [ "$code" = "200" ]; then
  echo "OK: $BASE_URL is ready"
  exit 0
fi
echo "UNHEALTHY: $BASE_URL/health/ready returned $code" >&2
exit 1
