#!/usr/bin/env bash
set -e

DB_FILE="${DB_PATH:-data/supply_chain.db}"

# Seed the database on first boot if it is absent.
# On subsequent starts the existing DB is used as-is (data persists via volume mount).
if [ ! -f "$DB_FILE" ]; then
    echo "[entrypoint] Database not found at $DB_FILE — seeding..."
    python scripts/seed_data.py
    echo "[entrypoint] Seeding complete."
else
    echo "[entrypoint] Database found at $DB_FILE — skipping seed."
fi

echo "[entrypoint] Starting Supply Chain AI API on port 8000..."
exec python main.py
