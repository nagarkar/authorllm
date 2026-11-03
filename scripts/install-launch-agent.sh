#!/usr/bin/env bash
# Writes this Mac's copy of the launch agent (metalm operations.md, launch agents) from the
# template beside it, filling in this checkout's path, the home folder, the authorlm on PATH and
# the manuscript the page opens on, then prints the command the owner runs to load it. Loads
# nothing itself. Safe to run again: it overwrites the installed copy; restart afterwards with
# launchctl kickstart -k.
set -euo pipefail
if [ $# -ne 1 ]; then
  echo "usage: $0 <manuscript the page opens on>" >&2
  exit 2
fi
manuscript="$1"
repo="$(cd "$(dirname "$0")/.." && pwd)"
label=com.nagarkar.authorlm.serve
target="$HOME/Library/LaunchAgents/$label.plist"
authorlm="$(command -v authorlm)"
if ! (cd "$repo/authorlm" && "$authorlm" _manuscripts) | grep -Fxq "$manuscript"; then
  echo "no manuscript named: $manuscript" >&2
  exit 2
fi
ffmpeg_dir="$(dirname "$(command -v ffmpeg)")"
path="$(dirname "$authorlm"):$ffmpeg_dir:/usr/bin:/bin:/usr/sbin:/sbin"
mkdir -p "$HOME/Library/LaunchAgents" "$HOME/.authorlm/logs"
sed -e "s#__AUTHORLM__#$authorlm#g" -e "s#__PATH__#$path#g" -e "s#__REPO__#$repo#g" \
  -e "s#__HOME__#$HOME#g" -e "s#__MANUSCRIPT__#$manuscript#g" \
  "$repo/scripts/$label.plist" > "$target"
echo "wrote $target"
echo "load it (once):   launchctl bootstrap gui/$(id -u) $target"
echo "restart onto new code: launchctl kickstart -k gui/$(id -u)/$label"
