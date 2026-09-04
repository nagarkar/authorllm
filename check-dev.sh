#!/usr/bin/env bash
# check-dev.sh
# Starts the EditorLLM dev server (--no-watch, so Rust compiles once and holds),
# waits for compilation to finish, then exits reporting success or failure.
# The app window stays open after this script exits.
#
# Usage (from the desktop/ directory):
#   ./check-dev.sh
#
# Log file: /tmp/editorllm-dev.log
# PID file:  /tmp/editorllm-dev.pid

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG=/tmp/editorllm-dev.log
CLEAN_LOG=/tmp/editorllm-dev-clean.log   # ANSI-stripped version for grepping
PID_FILE=/tmp/editorllm-dev.pid
TIMEOUT=300   # seconds to wait for compilation

# Strip ANSI escape codes from $LOG into $CLEAN_LOG so grep patterns work.
_refresh_clean() {
  sed $'s/\x1b\\[[0-9;]*[mGKHF]//g' "$LOG" > "$CLEAN_LOG" 2>/dev/null || true
}

# ── Kill any previous instance ───────────────────────────────────────────────
if [[ -f "$PID_FILE" ]]; then
  OLD_PID=$(cat "$PID_FILE")
  if kill -0 "$OLD_PID" 2>/dev/null; then
    echo "Stopping previous dev server (PID $OLD_PID)..."
    kill "$OLD_PID" 2>/dev/null || true
    sleep 2
  fi
  rm -f "$PID_FILE"
fi
# Also catch stray processes by name.
pkill -f "tauri dev" 2>/dev/null || true
pkill -f "editorllm-desktop" 2>/dev/null || true
# Kill any Vite server holding port 5173 (orphaned subprocess).
lsof -ti:5173 | xargs kill -9 2>/dev/null || true
sleep 1

# ── Start fresh, log everything ──────────────────────────────────────────────
echo "=== EditorLLM dev start $(date) ===" > "$LOG"
echo "Starting: npm run dev:log"

cd "$REPO_DIR"
# Run in background; npm and the Tauri process inherit the shell's session.
npm run dev:log >> "$LOG" 2>&1 &
DEV_PID=$!
echo "$DEV_PID" > "$PID_FILE"
echo "Dev server PID $DEV_PID — log: $LOG"

# ── Wait for compile result ───────────────────────────────────────────────────
echo "Waiting up to ${TIMEOUT}s for compilation..."
ELAPSED=0
while (( ELAPSED < TIMEOUT )); do
  _refresh_clean

  # Hard failure patterns (checked against ANSI-stripped log)
  if grep -qE '^error(\[E[0-9]+\])?:|process didn.t exit successfully|Permission .* not found' "$CLEAN_LOG" 2>/dev/null; then
    echo ""
    echo "FAIL ─ compilation error"
    echo "──────────────────────────────────────"
    grep -E '^error(\[E[0-9]+\])?:|process didn.t exit|Permission .* not found|^ *--> ' "$CLEAN_LOG" | head -40
    echo "──────────────────────────────────────"
    echo "Full log: $LOG"
    exit 1
  fi

  if grep -qE 'panicked at|thread .* panicked' "$CLEAN_LOG" 2>/dev/null; then
    echo ""
    echo "FAIL ─ runtime panic"
    grep -A5 'panicked at' "$CLEAN_LOG" | head -20
    exit 1
  fi

  # Success: Rust finished building
  if grep -q 'Finished `dev` profile' "$CLEAN_LOG" 2>/dev/null; then
    echo ""
    echo "OK ─ Rust compiled successfully"
    # Show last few meaningful lines
    grep -E 'Finished|Compiling editorllm|Running|Listening|http://' "$CLEAN_LOG" | tail -8
    echo "──────────────────────────────────────"
    echo "App running (PID $DEV_PID). Log: $LOG"
    exit 0
  fi

  printf '.'
  sleep 3
  (( ELAPSED += 3 ))
done

echo ""
echo "TIMEOUT after ${TIMEOUT}s — last 20 lines of log (ANSI stripped):"
_refresh_clean
tail -20 "$CLEAN_LOG"
exit 2
