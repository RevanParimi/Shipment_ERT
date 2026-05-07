#!/usr/bin/env bash
# setup_db.sh — create the SQLite database from schema.sql
# Usage: bash scripts/setup_db.sh [optional: path/to/db]
set -e

DB_FILE="${1:-${DB_PATH:-data/supply_chain.db}}"
SCHEMA="$(dirname "$0")/schema.sql"

mkdir -p "$(dirname "$DB_FILE")"

if [ ! -f "$SCHEMA" ]; then
    echo "[setup_db] ERROR: schema not found at $SCHEMA" >&2
    exit 1
fi

echo "[setup_db] Creating database at $DB_FILE"
sqlite3 "$DB_FILE" < "$SCHEMA"
echo "[setup_db] Schema applied. Tables:"
sqlite3 "$DB_FILE" ".tables"
