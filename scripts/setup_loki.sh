#!/usr/bin/env bash
# Setup Dokku Vector log shipping to Grafana Cloud Loki.
# Run this on the VPS as a user with sudo/dokku access.
#
# Usage:
#   bash setup_loki.sh <LOKI_WRITE_TOKEN>

set -euo pipefail

APP_NAME="book-corners"
LOKI_URL="https://logs-prod-012.grafana.net"
LOKI_USER="1502192"
LOKI_WRITE_TOKEN="${1:?Usage: bash setup_loki.sh <LOKI_WRITE_TOKEN>}"

TOKEN_ENC="$(python3 - "$LOKI_WRITE_TOKEN" <<'PY'
import sys
import urllib.parse

print(urllib.parse.quote(sys.argv[1], safe=""))
PY
)"

echo "==> Validating Loki write token..."
TS="$(date +%s%N)"
HTTP_CODE="$(curl -sS -o /tmp/loki_setup_check.txt -w "%{http_code}" \
  -u "${LOKI_USER}:${LOKI_WRITE_TOKEN}" \
  -H "Content-Type: application/json" \
  -X POST "${LOKI_URL}/loki/api/v1/push" \
  --data-raw "{\"streams\":[{\"stream\":{\"app\":\"${APP_NAME}\",\"source\":\"setup-script\"},\"values\":[[\"${TS}\",\"SETUP_LOKI_CHECK ${TS}\"]]}]}")"

if [ "${HTTP_CODE}" != "204" ]; then
  echo "ERROR: Loki token validation failed (HTTP ${HTTP_CODE})."
  cat /tmp/loki_setup_check.txt
  exit 1
fi
rm -f /tmp/loki_setup_check.txt

# Build the Dokku DSN for the Loki sink.
# Dokku overwrites vector.json on every start, so the only reliable
# way to configure Vector is through the DSN-based vector-sink setting.
# The endpoint must be URL-encoded inside the query string, and auth
# fields must stay quoted so Vector parses them as strings.
LOKI_DSN="loki://?endpoint=https%3A%2F%2Flogs-prod-012.grafana.net&path=%2Floki%2Fapi%2Fv1%2Fpush&auth[strategy]=basic&auth[user]=%221502192%22&auth[password]=%22${TOKEN_ENC}%22&encoding[codec]=json&labels[app]=book-corners"

echo "==> Clearing previous Vector sink config..."
sudo dokku logs:set --global vector-sink ""
sudo dokku logs:set "${APP_NAME}" vector-sink ""

echo "==> Setting Dokku Vector sink for Loki..."
sudo dokku logs:set "${APP_NAME}" vector-sink "${LOKI_DSN}"

echo "==> Restarting Vector..."
sudo dokku logs:vector-stop 2>/dev/null || true
sudo dokku logs:vector-start

echo "==> Checking Vector status..."
sudo dokku logs:report "${APP_NAME}"
sudo dokku logs:vector-logs 2>&1 | tail -40

echo ""
echo "==> Done."
echo "    If Vector starts without auth/config errors, shipping is configured."
