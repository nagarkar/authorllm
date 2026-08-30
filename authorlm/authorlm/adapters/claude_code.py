"""Claude Code session hooks → marker files.

On the measured versions (2.1.246 / 2.1.247) the adapter is already
`exact` without any hook, because `CLAUDE_CODE_SESSION_ID` travels in the
environment of both Bash-tool subprocesses and stdio MCP servers. **The
hook is enrichment, not correctness**: it supplies the label, the start
time and the transcript path, which the adapter picks up from a marker
*keyed by the session id it already knows*.

It is also the Branch B insurance. If that undocumented variable ever
goes away, the marker — which records the Claude host pid alongside the
session id — becomes the mapping, and the adapter stays exact, because
the host pid reaches both surfaces through
`CLAUDE_CODE_MESSAGING_SOCKET`.

The hook payload fields used here (`session_id`, `transcript_path`,
`cwd`, `source`) are all documented at
<https://code.claude.com/docs/en/hooks>. Nothing here parses a label.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

from .. import clients

ENGINE = "claude-code"

SNIPPET_FILENAME = "claude-code-settings-snippet.json"


def snippet_path() -> Path:
    return Path(__file__).resolve().parent / SNIPPET_FILENAME


def setup_snippet() -> str:
    try:
        return snippet_path().read_text(encoding="utf-8").rstrip("\n")
    except Exception:
        return ""


def build_label(env: Mapping[str, str]) -> str | None:
    """`AI_AGENT` is a decent label with no hook at all, and it records
    the client version — the thing most likely to explain a future
    behaviour change. Stored verbatim, never parsed."""
    agent = env.get("AI_AGENT")
    entrypoint = env.get("CLAUDE_CODE_ENTRYPOINT")
    if agent and entrypoint:
        return f"{agent} ({entrypoint})"
    return agent or entrypoint or None


def _host_pid(env: Mapping[str, str]) -> int | None:
    raw = env.get("CLAUDE_PID")
    if raw:
        try:
            return int(raw)
        except ValueError:
            pass
    socket = env.get("CLAUDE_CODE_MESSAGING_SOCKET") or ""
    stem = Path(socket).stem if socket else ""
    if stem.isdigit():
        return int(stem)
    try:
        return os.getppid()
    except Exception:
        return None


def write_marker(payload: dict, env: Mapping[str, str],
                 workspace: str | Path | None = None,
                 now: str | None = None) -> Path | None:
    """Handle a `SessionStart` payload. Returns the marker path, or
    `None` when the payload carries no session id."""
    session_id = payload.get("session_id")
    if not session_id:
        return None
    ws = Path(workspace) if workspace is not None else clients.default_workspace()
    stamp = now or clients._now()
    existing = clients.read_marker(ws, ENGINE, session_id) or {}
    data = {
        "engine": ENGINE,
        "session_id": str(session_id),
        "label": build_label(env),
        # A resumed session keeps the moment it actually began.
        "started_at": existing.get("started_at") or stamp,
        "transcript_hint": payload.get("transcript_path"),
        "host_pid": _host_pid(env),
        "cwd": payload.get("cwd") or env.get("CLAUDE_PROJECT_DIR"),
        "source": payload.get("source"),
        "updated_at": stamp,
    }
    path = clients.write_marker_file(ws, data)
    clients.sweep_markers(ws, stamp)
    return path


# ------------------------------------------- the transcript contract

# Measured structurally on 2026-08-30 from this machine's own 49
# transcripts (57,535 lines): key names, types and integers only. **No
# message text was read, and this parser reads none either.** It touches
# exactly six keys — `type`, `isSidechain`, `message.id`, `message.model`,
# `message.usage`, and the four integers inside it — and never `content`,
# `toolUseResult`, `lastPrompt`, `customTitle` or `aiTitle`.
#
# Three facts shaped it:
#   - usage lives ONLY on `type == "assistant"`, at `message.usage`;
#   - `iterations` restates the same numbers for the turn's internal
#     steps, so summing it double-counts — the top-level counters are
#     read and `iterations` is ignored;
#   - the same `message.id` is written up to SIX times with a
#     byte-identical usage object (21,709 usage-bearing lines carrying
#     10,571 distinct ids: a 2.05x overcount for a parser that sums
#     lines), which is why the shared sweeper dedups on it.
#
# `<synthetic>` rows are locally generated placeholder turns, not API
# calls. All 32 of them carry zeros, and they are skipped BY NAME so that
# a future synthetic row with non-zero counts cannot leak in.
CLAUDE_CODE_USAGE = clients.JsonlTranscriptUsage(
    is_turn=lambda d: (d.get("type") == "assistant"
                       and isinstance((d.get("message") or {}).get("usage"),
                                      dict)),
    turn_id=lambda d: (d.get("message") or {}).get("id"),
    model_of=lambda d: (
        None if (m := (d.get("message") or {}).get("model")) == "<synthetic>"
        else m),
    counters=lambda d: clients._four((d.get("message") or {})["usage"]),
    roots=("~/.claude/projects/*",),
)


def clear_marker(session_id: str | None,
                 workspace: str | Path | None = None) -> bool:
    """Handle a `SessionEnd` payload — one `unlink`, comfortably inside
    the documented 1.5 s shared SessionEnd budget."""
    if not session_id:
        return False
    ws = Path(workspace) if workspace is not None else clients.default_workspace()
    return clients.clear_marker_file(ws, ENGINE, str(session_id))
