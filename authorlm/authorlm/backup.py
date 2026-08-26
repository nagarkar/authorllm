"""Session-start backups of the SQLite store (OPS-4).

`~/.authorlm/authorlm.db` (or wherever `--workspace` points) holds the
author's concept graph, evidence, beliefs, and years of triage verdicts —
none of it derivable from anything else. `paths.py` documents a
`backups` directory for workspace state; this is what writes it.

The database runs in WAL mode (db.py:424-425), so on disk it is really
three files (`.db`, `.db-wal`, `.db-shm`). A plain file copy of just the
`.db` file can capture a write mid-flight and produce a torn,
unrestorable backup — worse than no backup, because it creates false
confidence. `perform_backup` therefore uses `sqlite3.Connection.backup()`
(the SQLite backup API) against the live, already-open connection: it is
safe against a concurrent writer and folds the WAL in correctly.

Trigger: `sessions.start_session` calls `run()` once per session start —
sessions stay open for a working day and are not reopened on every CLI
invocation, so this fires roughly once per day rather than on every
command (see sessions.py).

Design:
- Retention: the 7 most recent backups (KEEP). Rotation only deletes an
  old backup after the new one is fully and atomically in place
  (`Path.replace` within the same directory), never before.
- Skip-if-unchanged: the snapshot is taken first (required anyway to get
  a WAL-consistent copy to compare), then hashed against the most recent
  kept backup. A byte-identical snapshot is discarded without entering
  rotation, so a quiet day doesn't evict seven backups that hold real
  history.
- Failure never blocks the caller: every exception is caught and
  reported back as `{"ok": False, "error": ...}`. `run()` additionally
  prints a loud warning to stderr (never stdout — the MCP server speaks
  JSON-RPC over stdout) so a broken backup is never silently swallowed.
"""

from __future__ import annotations

import hashlib
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .db import Database

BACKUP_DIRNAME = "backups"
KEEP = 7


def backup_dir(db_path: Path) -> Path:
    return db_path.parent / BACKUP_DIRNAME


def _existing(bdir: Path) -> list[Path]:
    """Kept backups, oldest first (the naming is lexicographically
    time-ordered, so a plain sort is a chronological sort)."""
    return sorted(bdir.glob("authorlm-*.db"))


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stamp() -> str:
    # Microseconds: two backups could otherwise collide within the same
    # second (e.g. a session that starts, is force-ended, and restarts
    # inside a test or a very fast author).
    return "authorlm-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f") + ".db"


def perform_backup(db: Database) -> dict:
    """Snapshot `db` into `backup_dir(db.path)`, skip if unchanged, then
    rotate to the KEEP most recent. Never raises."""
    started = time.monotonic()
    bdir = backup_dir(db.path)
    tmp_path: Path | None = None
    try:
        bdir.mkdir(parents=True, exist_ok=True)
        tmp_path = bdir / f".tmp-{id(db)}-{time.monotonic_ns()}.db"
        db.conn.commit()  # no dangling transaction going into the snapshot
        dest = sqlite3.connect(str(tmp_path))
        try:
            db.conn.backup(dest)
        finally:
            dest.close()

        existing = _existing(bdir)
        if existing and _hash_file(tmp_path) == _hash_file(existing[-1]):
            tmp_path.unlink()
            tmp_path = None
            return {"ok": True, "skipped": True,
                    "reason": "unchanged since last backup",
                    "elapsed_s": round(time.monotonic() - started, 3)}

        final_path = bdir / _stamp()
        tmp_path.replace(final_path)  # atomic: same directory/filesystem
        tmp_path = None

        kept = _existing(bdir)
        for old in kept[:-KEEP]:
            old.unlink(missing_ok=True)

        return {"ok": True, "skipped": False, "path": str(final_path),
                "elapsed_s": round(time.monotonic() - started, 3)}
    except Exception as err:  # noqa: BLE001 — must never block session start
        return {"ok": False, "error": f"{type(err).__name__}: {err}",
                "elapsed_s": round(time.monotonic() - started, 3)}
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


def run(db: Database) -> dict:
    """`perform_backup`, plus a loud stderr warning on failure. Called
    from `sessions.start_session` — see module docstring for why that's
    the right trigger. stderr, never stdout: the MCP server's stdout is
    a JSON-RPC stream and printing to it would corrupt the protocol."""
    result = perform_backup(db)
    if not result.get("ok"):
        print(
            "AUTHORLM BACKUP FAILED: "
            f"{result.get('error')} (target: {backup_dir(db.path)}) — "
            "the session is starting anyway, but you have no fresh "
            "backup of the knowledge store. Fix the disk space or "
            "permissions problem before this matters.",
            file=sys.stderr,
        )
    return result
