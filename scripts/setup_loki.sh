#!/usr/bin/env bash
# Setup Dokku Vector log shipping to Grafana Cloud Loki.
# Run this on the VPS as a user with sudo/dokku access.
#
# Usage:
#   bash setup_loki.sh <LOKI_WRITE_TOKEN>

set -euo pipefail

LOKI_URL="https://logs-prod-012.grafana.net"
LOKI_USER="1502192"
LOKI_WRITE_TOKEN="${1:?Usage: bash setup_loki.sh <LOKI_WRITE_TOKEN>}"

VECTOR_CONFIG="/var/lib/dokku/data/vector/vector.json"

echo "==> Configuring Dokku Vector to ship logs to Grafana Cloud Loki..."

# Try the DSN-based sink first (simplest approach)
LOKI_DSN="https://${LOKI_USER}:${LOKI_WRITE_TOKEN}@${LOKI_URL#https://}/loki/api/v1/push"

echo "==> Attempting DSN sink: dokku logs:set --global vector-sink"
if sudo dokku logs:set --global vector-sink "$LOKI_DSN" 2>/dev/null; then
    echo "==> DSN sink configured. Starting Vector..."
    sudo dokku logs:vector-start || true
    echo "==> Checking Vector logs..."
    sudo dokku logs:vector-logs 2>&1 | tail -20

    echo ""
    echo "==> DSN sink setup complete."
    echo "    If you see errors above, re-run with the fallback config below."
    exit 0
fi

echo "==> DSN sink not supported for Loki. Falling back to custom Vector config..."

# Fallback: write a full Vector config with a Loki sink
sudo mkdir -p "$(dirname "$VECTOR_CONFIG")"

sudo tee "$VECTOR_CONFIG" > /dev/null <<VECTORJSON
{
  "sources": {
    "dokku_logs": {
      "type": "docker_logs"
    }
  },
  "transforms": {
    "add_labels": {
      "type": "remap",
      "inputs": ["dokku_logs"],
      "source": ".app = replace(string!(.container_name), r'^/', \"\")"
    }
  },
  "sinks": {
    "grafana_loki": {
      "type": "loki",
      "inputs": ["add_labels"],
      "endpoint": "${LOKI_URL}",
      "auth": {
        "strategy": "basic",
        "user": "${LOKI_USER}",
        "password": "${LOKI_WRITE_TOKEN}"
      },
      "labels": {
        "app": "{{ app }}",
        "host": "{{ host }}"
      },
      "encoding": {
        "codec": "json"
      }
    }
  }
}
VECTORJSON

echo "==> Custom Vector config written to ${VECTOR_CONFIG}"

# Clear any previous DSN sink so Vector uses the config file
sudo dokku logs:set --global vector-sink "" 2>/dev/null || true

echo "==> Starting Vector..."
sudo dokku logs:vector-start || sudo dokku logs:vector-start --force

echo "==> Checking Vector logs..."
sudo dokku logs:vector-logs 2>&1 | tail -20

echo ""
echo "==> Setup complete. Verify with:"
echo "    sudo dokku logs:vector-logs"
echo ""
echo "    From your local machine:"
echo "    logcli query '{app=\"book-corners\"}' --limit 5"
