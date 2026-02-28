#!/usr/bin/env bash
# Restore Book Corners data from a Borg archive.
# Supports selective restore (database only, media only, or both).
#
# Usage:
#   restore.sh --list              List available archives
#   restore.sh --dry-run [ARCHIVE] Show what would be restored
#   restore.sh --db-only [ARCHIVE] Restore only the database
#   restore.sh --media-only [ARCHIVE] Restore only media files
#   restore.sh [ARCHIVE]           Restore both database and media
#
# If ARCHIVE is omitted, the latest archive is used.
#
# IMPORTANT: Run as the deploy user, NOT with sudo. Borg needs the deploy
# user's SSH key to authenticate with BorgBase. The script uses sudo only
# for the dokku commands that require it.

set -euo pipefail

# ── Configuration ────────────────────────────────────────────────────────────

ENV_FILE="/home/deploy/.env.backup"
MEDIA_DIR="/var/lib/dokku/data/storage/book-corners/media"
DB_SERVICE="book-corners-db"
WORK_DIR="/tmp/book-corners-restore"

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

# ── Helpers ──────────────────────────────────────────────────────────────────

usage() {
    echo "Usage: $(basename "$0") [OPTIONS] [ARCHIVE]"
    echo ""
    echo "Options:"
    echo "  --list         List available archives"
    echo "  --dry-run      Show what would be restored (no changes)"
    echo "  --db-only      Restore only the database"
    echo "  --media-only   Restore only media files"
    echo "  -h, --help     Show this help message"
    echo ""
    echo "If ARCHIVE is omitted, the latest archive is used."
}

confirm() {
    local prompt="$1"
    read -r -p "$prompt [y/N] " response
    [[ "$response" =~ ^[Yy]$ ]]
}

get_latest_archive() {
    borg list --short --sort-by timestamp --last 1 | tr -d '[:space:]'
}

cleanup() {
    rm -rf "$WORK_DIR"
}
trap cleanup EXIT

# ── Parse arguments ──────────────────────────────────────────────────────────

MODE="both"
DRY_RUN=false
ARCHIVE=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --list)
            echo "Available archives:"
            echo ""
            borg list --sort-by timestamp
            exit 0
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --db-only)
            MODE="db"
            shift
            ;;
        --media-only)
            MODE="media"
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        -*)
            echo "Unknown option: $1" >&2
            usage >&2
            exit 1
            ;;
        *)
            ARCHIVE="$1"
            shift
            ;;
    esac
done

# Resolve archive name
if [[ -z "$ARCHIVE" ]]; then
    ARCHIVE=$(get_latest_archive)
    if [[ -z "$ARCHIVE" ]]; then
        echo "ERROR: No archives found in the repository." >&2
        exit 1
    fi
    echo "Using latest archive: $ARCHIVE"
fi

# Verify archive exists
if ! borg info "::${ARCHIVE}" > /dev/null 2>&1; then
    echo "ERROR: Archive '$ARCHIVE' not found." >&2
    echo "Use --list to see available archives." >&2
    exit 1
fi

# ── Dry run ──────────────────────────────────────────────────────────────────

if [[ "$DRY_RUN" == true ]]; then
    echo ""
    echo "=== Dry run: contents of archive '$ARCHIVE' ==="
    echo ""
    echo "--- Database dump ---"
    borg list "::${ARCHIVE}" | grep "db\.dump" || echo "(no database dump found)"
    echo ""
    echo "--- Media files ---"
    borg list "::${ARCHIVE}" | grep "media/" | head -20
    MEDIA_COUNT=$(borg list "::${ARCHIVE}" | grep -c "media/" || true)
    if [[ "$MEDIA_COUNT" -gt 20 ]]; then
        echo "... and $((MEDIA_COUNT - 20)) more media files"
    fi
    echo ""
    echo "Mode: $MODE"
    echo "No changes were made."
    exit 0
fi

# ── Restore ──────────────────────────────────────────────────────────────────

echo ""
echo "=== Restore from archive: $ARCHIVE ==="
echo "Mode: $MODE"
echo ""

# Prepare working directory
rm -rf "$WORK_DIR"
mkdir -p "$WORK_DIR"

# ── Database restore ─────────────────────────────────────────────────────────

if [[ "$MODE" == "both" || "$MODE" == "db" ]]; then
    echo "--- Database restore ---"
    echo ""
    echo "TIP: Consider restoring to a test database first:"
    echo "  sudo dokku postgres:create ${DB_SERVICE}-test"
    echo "  sudo dokku postgres:import ${DB_SERVICE}-test < db.dump"
    echo "  sudo dokku postgres:connect ${DB_SERVICE}-test"
    echo "  # Verify data, then: sudo dokku postgres:destroy ${DB_SERVICE}-test --force"
    echo ""

    if ! confirm "Restore database to PRODUCTION ($DB_SERVICE)?"; then
        echo "Database restore skipped."
    else
        echo "Extracting database dump..."
        cd "$WORK_DIR"
        borg extract "::${ARCHIVE}" --pattern "*/db.dump"

        # Find the extracted dump (path includes the work dir prefix)
        DB_DUMP=$(find "$WORK_DIR" -name "db.dump" -type f | head -1)

        if [[ -z "$DB_DUMP" ]]; then
            echo "ERROR: No database dump found in archive." >&2
            exit 1
        fi

        echo "Importing database dump..."
        cat "$DB_DUMP" | sudo dokku postgres:import "$DB_SERVICE"
        echo "Database restore complete."
    fi
    echo ""
fi

# ── Media restore ────────────────────────────────────────────────────────────

if [[ "$MODE" == "both" || "$MODE" == "media" ]]; then
    echo "--- Media restore ---"
    echo ""

    MEDIA_COUNT=$(borg list "::${ARCHIVE}" | grep -c "media/" || true)
    echo "Archive contains $MEDIA_COUNT media files."
    echo "Target: $MEDIA_DIR"
    echo ""

    if ! confirm "Restore media files to $MEDIA_DIR?"; then
        echo "Media restore skipped."
    else
        echo "Extracting media files..."
        cd "$WORK_DIR"
        borg extract "::${ARCHIVE}" --pattern "*/media/*"

        # Find the extracted media root
        EXTRACTED_MEDIA=$(find "$WORK_DIR" -type d -name "media" | head -1)

        if [[ -z "$EXTRACTED_MEDIA" ]]; then
            echo "ERROR: No media files found in archive." >&2
            exit 1
        fi

        echo "Copying media files to $MEDIA_DIR..."
        sudo cp -a "$EXTRACTED_MEDIA"/. "$MEDIA_DIR"/
        sudo chown -R 32767:32767 "$MEDIA_DIR"
        echo "Media restore complete (ownership set to 32767:32767)."
    fi
    echo ""
fi

echo "=== Restore finished at $(date -Iseconds) ==="
