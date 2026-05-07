#!/usr/bin/env bash
# schedule.sh — trigger the supply chain pipeline via cron.
# Add to crontab:  */15 8-18 * * 1-5  /path/to/supply_chain_ai/scripts/schedule.sh
#
# Runs every 15 minutes on weekdays 08:00–18:00.
# Logs to /var/log/supply_chain/pipeline.log (rotate with logrotate).
set -e

API_URL="${SUPPLY_CHAIN_API_URL:-http://localhost:8000}"
LOG_DIR="${LOG_DIR:-/var/log/supply_chain}"
LOG_FILE="$LOG_DIR/pipeline.log"
TIMEOUT=120   # seconds before giving up on a /run call

mkdir -p "$LOG_DIR"

TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
echo "[$TIMESTAMP] Triggering pipeline at $API_URL/run" >> "$LOG_FILE"

RESPONSE=$(curl -sf \
    --max-time "$TIMEOUT" \
    -X POST \
    -H "Content-Type: application/json" \
    "$API_URL/run" 2>&1)

EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    AT_RISK=$(echo "$RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('at_risk_count',0))" 2>/dev/null || echo "?")
    ESCALATIONS=$(echo "$RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('escalations',0))" 2>/dev/null || echo "?")
    echo "[$TIMESTAMP] OK — at_risk=$AT_RISK escalations=$ESCALATIONS" >> "$LOG_FILE"
else
    echo "[$TIMESTAMP] FAILED (exit=$EXIT_CODE) — $RESPONSE" >> "$LOG_FILE"
    exit 1
fi
