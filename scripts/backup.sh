#!/usr/bin/env bash
# Nightly backup for Book Corners: database dump + media files → Borg archive.
# Intended to run as a cron job under the deploy user on the VPS.
#
# Prerequisites:
#   - /home/deploy/.env.backup with BORG_REPO and BORG_PASSPHRASE
#   - borg installed (apt-get install borgbackup)
#   - SSH key authorized at BorgBase
#   - deploy user has passwordless sudo for dokku commands

set -euo pipefail

# ── Configuration ────────────────────────────────────────────────────────────

ENV_FILE="/home/deploy/.env.backup"
MEDIA_DIR="/var/lib/dokku/data/storage/book-corners/media"
DB_SERVICE="book-corners-db"
WORK_DIR="/tmp/book-corners-backup"
ARCHIVE_PREFIX="book-corners"

# Retention policy
KEEP_DAILY=7
KEEP_WEEKLY=4
KEEP_MONTHLY=6

# ── Load environment ─────────────────────────────────────────────────────────

if [[ ! -f "$ENV_FILE" ]]; then
    echo "ERROR: $ENV_FILE not found. Create it with BORG_REPO and BORG_PASSPHRASE." >&2
    exit 1
fi

# shellcheck source=/dev/null
source "$ENV_FILE"

if [[ -z "${BORG_REPO:-}" ]]; then
    echo "ERROR: BORG_REPO is not set in $ENV_FILE." >&2
    exit 1
fi

if [[ -z "${BORG_PASSPHRASE:-}" ]]; then
    echo "ERROR: BORG_PASSPHRASE is not set in $ENV_FILE." >&2
    exit 1
fi

export BORG_REPO
export BORG_PASSPHRASE

# ── Cleanup trap ─────────────────────────────────────────────────────────────

cleanup() {
    rm -rf "$WORK_DIR"
}
trap cleanup EXIT

# ── Main ─────────────────────────────────────────────────────────────────────

echo "=== Book Corners backup started at $(date -Iseconds) ==="

# Prepare working directory
rm -rf "$WORK_DIR"
mkdir -p "$WORK_DIR"

# Export database
echo "Exporting database..."
sudo dokku postgres:export "$DB_SERVICE" | cat > "$WORK_DIR/db.dump"
echo "Database export complete ($(du -h "$WORK_DIR/db.dump" | cut -f1))."

# Create borg archive
ARCHIVE_NAME="${ARCHIVE_PREFIX}-$(date +%Y-%m-%dT%H:%M:%S)"
echo "Creating archive: $ARCHIVE_NAME"

borg create \
    --verbose \
    --stats \
    --compression zstd,3 \
    "::${ARCHIVE_NAME}" \
    "$WORK_DIR/db.dump" \
    "$MEDIA_DIR"

echo "Archive created successfully."

# Prune old archives
echo "Pruning old archives..."
borg prune \
    --verbose \
    --stats \
    --keep-daily="$KEEP_DAILY" \
    --keep-weekly="$KEEP_WEEKLY" \
    --keep-monthly="$KEEP_MONTHLY" \
    --glob-archives "${ARCHIVE_PREFIX}-*"

# Compact repository
echo "Compacting repository..."
borg compact

echo "=== Book Corners backup finished at $(date -Iseconds) ==="
