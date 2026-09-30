#!/usr/bin/env bash
# check-dev.sh — start the audiostation dev server (--no-watch, so Rust
# compiles once and holds), wait for compilation, exit reporting success
# or failure. The app window stays open after this script exits.
#
# Usage (from the audiostation/ directory):
#   ./check-dev.sh
#
# Log file: /tmp/audiostation-dev.log
# PID file: /tmp/audiostation-dev.pid

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG=/tmp/audiostation-dev.log
CLEAN_LOG=/tmp/audiostation-dev-clean.log
PID_FILE=/tmp/audiostation-dev.pid
TIMEOUT=300

_refresh_clean() {
  sed $'s/\x1b\\[[0-9;]*[mGKHF]//g' "$LOG" > "$CLEAN_LOG" 2>/dev/null || true
}

if [[ -f "$PID_FILE" ]]; then
  OLD_PID=$(cat "$PID_FILE")
  if kill -0 "$OLD_PID" 2>/dev/null; then
    echo "Stopping previous dev server (PID $OLD_PID)..."
    kill "$OLD_PID" 2>/dev/null || true
    sleep 1
  fi
  rm -f "$PID_FILE"
fi

cd "$REPO_DIR"
: > "$LOG"
npx tauri dev --no-watch > "$LOG" 2>&1 &
echo $! > "$PID_FILE"
echo "Dev server started (PID $(cat "$PID_FILE")); waiting for compilation..."

for ((i = 0; i < TIMEOUT; i++)); do
  _refresh_clean
  if grep -qE "error\[|error: could not compile" "$CLEAN_LOG"; then
    echo "COMPILATION FAILED — see $LOG"
    grep -E "error" "$CLEAN_LOG" | head -20
    exit 1
  fi
  if grep -qE "Finished .*dev|Running .*audiostation" "$CLEAN_LOG"; then
    echo "Compiled and running."
    exit 0
  fi
  sleep 1
done

echo "Timed out after ${TIMEOUT}s — see $LOG"
exit 1
