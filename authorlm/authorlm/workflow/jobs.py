"""Background model turns for the Workflows page.

A model turn can take minutes (a headless Claude session reading the
book). Run inside the page's HTTP request, the browser waited the whole
time, a navigation dropped it, and a turn that ran past its limit was lost
with nothing recorded (2026-11-26: Recommend on wi-7abca47358e0, 900 s, no
turn). So the page starts a model turn as a **job** and returns at once:

    start(workspace, instance, method, params, work) -> job row

`work(db)` runs on its own thread with its own database connection; the
turn it records (session_turns) is the success. The job row
(`workflow_jobs`) is the attempt: `running` with its progress (the engine
reports each read request through `engines.base.report_progress`), then
`done`, or `failed` with the error. A failed attempt stays on the instance
until the author retries or dismisses it; the page shows it with Retry,
which sends the same method and params again (`params`).

One job at a time per instance (`running`): any other change to the
instance waits for it, so a turn never lands on an instance that moved
under it. A job owned by a server process that is gone (a restart during
the turn) is marked failed when next read, "interrupted".

The CLI and tests call the page's methods synchronously; only the page
asks for a job (`workflow_page.dispatch(..., background=True)`).
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from ..db import Database, ko_fields, loads, now_iso

OWNER = f"{os.getpid()}:{uuid.uuid4().hex[:8]}"
_LIVE: dict[str, threading.Thread] = {}
_LOCK = threading.Lock()


def _elapsed(start: str, end: str | None = None) -> float:
    try:
        a = datetime.fromisoformat(start.replace("Z", "+00:00"))
        b = (datetime.fromisoformat(end.replace("Z", "+00:00")) if end
             else datetime.now(timezone.utc))
        return max(0.0, (b - a).total_seconds())
    except (ValueError, AttributeError):
        return 0.0


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _orphaned(row: dict) -> bool:
    """A running row whose thread is gone: this process lost it, or the
    process that owned it is no longer alive."""
    owner = row.get("owner") or ""
    if owner == OWNER:
        return row["id"] not in _LIVE
    try:
        return not _pid_alive(int(owner.split(":", 1)[0]))
    except ValueError:
        return True


def _view(row: dict) -> dict:
    return {"id": row["id"], "method": row["method"],
            "params": loads(row["params"], {}), "state": row["state"],
            "error": row["error"], "progress": loads(row["progress"], {}),
            "started_at": row["started_at"], "finished_at": row["finished_at"],
            "elapsed": round(_elapsed(row["started_at"], row["finished_at"]), 1)}


def _reap(db: Database, rows: list[dict]) -> None:
    for row in rows:
        if row["state"] == "running" and _orphaned(row):
            at = now_iso()
            db.update("workflow_jobs", row["id"], {
                "state": "failed", "finished_at": at,
                "error": "interrupted: the server stopped during the turn; "
                         "nothing was recorded"})
            row.update(state="failed", finished_at=at,
                       error="interrupted: the server stopped during the turn; "
                             "nothing was recorded")


def jobs(db: Database, instance_id: str) -> list[dict]:
    """Every attempt on the instance, oldest first (orphans reaped)."""
    rows = [dict(r) for r in db.all(
        "SELECT * FROM workflow_jobs WHERE instance_id = ? "
        "ORDER BY started_at, created_at", (instance_id,))]
    _reap(db, rows)
    return [_view(r) for r in rows]


def running(db: Database, instance_id: str) -> dict | None:
    return next((j for j in jobs(db, instance_id) if j["state"] == "running"), None)


def current(db: Database, instance_id: str) -> dict | None:
    """What the page shows: the running job, else the latest attempt when
    it failed and is not dismissed."""
    all_ = jobs(db, instance_id)
    run = next((j for j in all_ if j["state"] == "running"), None)
    if run:
        return run
    last = all_[-1] if all_ else None
    return last if last and last["state"] == "failed" else None


def dismiss(db: Database, instance_id: str, job_id: str) -> None:
    row = db.one("SELECT * FROM workflow_jobs WHERE id = ? AND instance_id = ?",
                 (job_id, instance_id))
    if row is None:
        raise LookupError(f"no job '{job_id}' on this instance")
    if row["state"] == "failed":
        db.update("workflow_jobs", job_id, {"state": "dismissed"})


def busy_message(job: dict) -> str:
    return (f"a {job['method']} turn is running on this instance "
            f"({job['elapsed']:.0f} s so far); wait for it to finish")


def start(workspace: str | None, instance_id: str, method: str,
          params: dict[str, Any], work: Callable[[Database], Any],
          open_db: Callable[[str | None], Database]) -> dict:
    """Record a running job and run `work(db)` on a thread. Raises
    ValueError when a job is already running on the instance."""
    with _LOCK:
        db = open_db(workspace)
        run = running(db, instance_id)
        if run:
            raise ValueError(busy_message(run))
        row = {**ko_fields("wj"), "instance_id": instance_id, "method": method,
               "params": json.dumps(params, ensure_ascii=False),
               "state": "running", "error": "", "progress": "{}",
               "owner": OWNER, "started_at": now_iso(), "finished_at": None}
        thread = threading.Thread(target=_run, name=f"workflow-job-{row['id']}",
                                  args=(workspace, row["id"], work, open_db),
                                  daemon=True)
        _LIVE[row["id"]] = thread  # before the row, so no reader reaps it
        db.insert("workflow_jobs", row)
        thread.start()
        return _view(dict(db.one("SELECT * FROM workflow_jobs WHERE id = ?",
                                 (row["id"],))))


def _run(workspace: str | None, job_id: str, work: Callable[[Database], Any],
         open_db: Callable[[str | None], Database]) -> None:
    from ..engines import base

    db = open_db(workspace)
    progress: dict[str, Any] = {}

    def hook(info: dict) -> None:
        progress.update(info)
        db.update("workflow_jobs", job_id,
                  {"progress": json.dumps(progress, ensure_ascii=False)})

    base.set_progress(hook)
    try:
        work(db)
    except BaseException as err:  # noqa: BLE001 — every failure is recorded
        detail = str(err) if isinstance(err, (ValueError, LookupError)) or \
            type(err).__module__.startswith("authorlm") else \
            f"{type(err).__name__}: {err}"
        db.update("workflow_jobs", job_id, {"state": "failed", "error": detail,
                                            "finished_at": now_iso()})
    else:
        db.update("workflow_jobs", job_id, {"state": "done",
                                            "finished_at": now_iso()})
    finally:
        base.set_progress(None)
        _LIVE.pop(job_id, None)


def wait(job_id: str, timeout: float | None = None) -> None:
    """Wait for a job of this process to end (tests)."""
    thread = _LIVE.get(job_id)
    if thread is not None:
        thread.join(timeout)
