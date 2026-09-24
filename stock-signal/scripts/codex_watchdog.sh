#!/usr/bin/env bash
# Persistent watchdog: wake codex_tick.py every ~5 minutes.
# Use on hosts without system cron; survives only while this process lives.
set -eu

export STOCK_DATA_DIR="${STOCK_DATA_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
export TZ=Asia/Shanghai

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TICK="${TICK:-${SCRIPT_DIR}/codex_tick.py}"
PYTHON="${PYTHON:-python3}"
LOG_DIR="${STOCK_DATA_DIR}/logs"

mkdir -p "$LOG_DIR"
PIDFILE="$LOG_DIR/codex-watchdog.pid"
FLOCK="$LOG_DIR/codex-watchdog.flock"
LOG="$LOG_DIR/codex-watchdog.log"

echo $$ > "$PIDFILE"
echo "$(date '+%Y-%m-%d %H:%M:%S %Z') watchdog start pid=$$" >> "$LOG"

while true; do
  # Align to :00/:05/:10/... roughly by sleeping until next 5-min boundary + 3s
  now=$(date +%s)
  rem=$(( now % 300 ))
  if [ "$rem" -eq 0 ]; then
    sleep 3
  else
    sleep $(( 300 - rem + 3 ))
  fi
  
  # Run tick with flock to prevent overlapping executions
  /usr/bin/flock -n "$FLOCK" "$PYTHON" "$TICK" >> "$LOG" 2>&1 || true
done
