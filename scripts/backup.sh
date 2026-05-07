#!/usr/bin/env bash
# backup.sh — SQLite hot backup before each pipeline run.
# SQLite's .backup command is safe under concurrent reads/writes.
# Keeps last 7 days of backups; older files are deleted automatically.
set -e

DB_FILE="${DB_PATH:-data/supply_chain.db}"
BACKUP_DIR="${BACKUP_DIR:-data/backups}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-7}"

mkdir -p "$BACKUP_DIR"

if [ ! -f "$DB_FILE" ]; then
    echo "[backup] Database not found at $DB_FILE — skipping." >&2
    exit 0
fi

TIMESTAMP=$(date '+%Y%m%d_%H%M%S')
DEST="$BACKUP_DIR/supply_chain_$TIMESTAMP.db"

sqlite3 "$DB_FILE" ".backup '$DEST'"
echo "[backup] Saved: $DEST ($(du -h "$DEST" | cut -f1))"

# Rotate: delete backups older than RETENTION_DAYS
find "$BACKUP_DIR" -name "supply_chain_*.db" -mtime +"$RETENTION_DAYS" -delete
REMAINING=$(find "$BACKUP_DIR" -name "supply_chain_*.db" | wc -l | tr -d ' ')
echo "[backup] Retained $REMAINING backup(s) (policy: ${RETENTION_DAYS}d)"
