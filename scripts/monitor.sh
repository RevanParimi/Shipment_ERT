#!/usr/bin/env bash
# monitor.sh — health check with alerting.
# Recommended: run every 5 minutes via cron.
#   */5 * * * *  /path/to/supply_chain_ai/scripts/monitor.sh
#
# Set ALERT_EMAIL or SLACK_WEBHOOK_URL to enable alerting.
set -e

API_URL="${SUPPLY_CHAIN_API_URL:-http://localhost:8000}"
ALERT_EMAIL="${ALERT_EMAIL:-}"
SLACK_WEBHOOK_URL="${SLACK_WEBHOOK_URL:-}"
TIMEOUT=10
LOG_DIR="${LOG_DIR:-/var/log/supply_chain}"

mkdir -p "$LOG_DIR"

TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

HTTP_CODE=$(curl -o /dev/null -sf \
    --max-time "$TIMEOUT" \
    -w "%{http_code}" \
    "$API_URL/health" 2>/dev/null || echo "000")

if [ "$HTTP_CODE" = "200" ]; then
    echo "[$TIMESTAMP] UP — $API_URL/health returned 200" >> "$LOG_DIR/monitor.log"
    exit 0
fi

# --- API is DOWN ---
MSG="[ALERT] Supply Chain AI API is DOWN at $TIMESTAMP (HTTP $HTTP_CODE) — $API_URL"
echo "$MSG" >> "$LOG_DIR/monitor.log"

# Email alert
if [ -n "$ALERT_EMAIL" ]; then
    echo "$MSG" | mail -s "ALERT: Supply Chain AI down" "$ALERT_EMAIL" 2>/dev/null || true
fi

# Slack alert
if [ -n "$SLACK_WEBHOOK_URL" ]; then
    curl -sf -X POST "$SLACK_WEBHOOK_URL" \
        -H "Content-Type: application/json" \
        -d "{\"text\": \"$MSG\"}" 2>/dev/null || true
fi

exit 1
