"""Operation trace log: one JSONL line per verb invocation.

Every CLI command and MCP tool call appends
{ts, surface, verb, action, manuscript, duration_ms, ok, error} to
<workspace>/.authorlm/logs/trace.jsonl (default workspace: ~/.authorlm).
Zero-dependency and cheap enough to be always on. The log exists to be
*read by agents*: a scheduled reviewer scans it for slow operations and
errors and proposes optimizations (see README "Logging & traces").

Never raises — a broken trace must not break the operation it measures.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

MAX_BYTES = 5 * 1024 * 1024  # rotate to trace.jsonl.1 beyond this


def log_dir(workspace: str | None = None) -> Path:
    base = Path(workspace).resolve() if workspace else Path.home()
    return base / ".authorlm" / "logs"


def record(verb: str, *, surface: str, workspace: str | None = None,
           action: str | None = None, manuscript: str | None = None,
           duration_ms: int | None = None, ok: bool = True,
           error: str | None = None, client: dict | None = None) -> None:
    try:
        directory = log_dir(workspace)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "trace.jsonl"
        if path.exists() and path.stat().st_size > MAX_BYTES:
            path.replace(path.with_suffix(".jsonl.1"))
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "surface": surface,
            "verb": verb,
        }
        if action:
            entry["action"] = action
        if manuscript:
            entry["manuscript"] = manuscript
        if duration_ms is not None:
            entry["duration_ms"] = duration_ms
        entry["ok"] = ok
        if error:
            entry["error"] = error[:500]
        # The trace log is the TIMELINE; row metadata is the join key.
        # Together they answer "which client did what, when, to this
        # object" with no schema change and no index. Resolved here so
        # every caller gets it from one edit; omitted entirely when
        # unknown, so old lines and unattributed ones read alike.
        if client is None:
            try:
                from . import clients

                found = clients.current()
                client = found.key() if found.precision != "none" else None
            except Exception:
                client = None
        if client:
            entry["client"] = client
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")
    except Exception:
        pass  # tracing is best-effort by design


class Timer:
    def __enter__(self):
        self._t0 = time.monotonic()
        return self

    def __exit__(self, *exc):
        self.ms = int((time.monotonic() - self._t0) * 1000)
        return False
