#!/bin/bash
# Run the hermetic suite: every tests/test_*.py except test_live_llm.py
# (which sends unrecorded requests to the live, billed model).
#
# usage: tools/run_tests.sh [DIR]
#   DIR  the directory holding tests/ (default: this repo's authorlm/)
#
# Prints "ok   <file>" or "FAIL <file>" per file, with the last lines of
# a failing file's output, and exits 1 if any file failed.
#
# Note: tests/test_audio.py needs the web frontend built (pip install -e
# ".[all]" builds it), so it fails in a fresh worktree for that reason.

set -u
DIR="${1:-$(cd "$(dirname "$0")/.." && pwd)}"
cd "$DIR" || { echo "error: no such directory: $DIR" >&2; exit 1; }

failed=0
log="$(mktemp -t authorlm-tests)"
trap 'rm -f "$log"' EXIT
for f in tests/test_*.py; do
  [ -e "$f" ] || continue
  [ "$f" = tests/test_live_llm.py ] && continue
  if python3 "$f" >"$log" 2>&1; then
    echo "ok   $f"
  else
    echo "FAIL $f"
    tail -n 20 "$log" | sed 's/^/    /'
    failed=$((failed + 1))
  fi
done
if [ "$failed" -gt 0 ]; then
  echo "$failed file(s) failed"
  exit 1
fi
echo "all passed"
