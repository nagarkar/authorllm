"""Self-improvement tasks: AuthorLM's own defects as knowledge objects.

The same evidence discipline the system applies to the manuscript, applied
to the tool. When AuthorLM misbehaves during an authoring session (e.g. a
prerequisite-gap false positive) and the author confirms the behavior is
wrong, the collaborating agent files an improvement task *at that moment* —
capturing the evidence trail and the author's verdict before the
conversation moves on.

Scope is deliberately narrow: defects with a structured-prose repro
(given / observed / expected). Feature ideas go through the normal
grilling/issue workflow, not this queue. That keeps `improve run`
automatable and the queue honest.

Lifecycle: open → in_progress (bundle emitted) → proposed (fixing agent
recorded a fix + encoded test) → resolved (author confirmed). Dismissal
requires a reason, recorded as evidence like any rejected guidance.

Improvement tasks are tool-knowledge, not manuscript-knowledge: they
surface only in `improve list` / the MCP improve tools and a one-line
count in `status` — never in briefings, guidance, or episode analysis.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .db import Database, ko_fields

STATUSES = ("open", "in_progress", "proposed", "resolved", "dismissed")
ACTIVE = ("open", "in_progress", "proposed")


def git_context() -> dict:
    """The AuthorLM source tree's git state right now — the Time Machine
    anchor for code references. Evidence cites file:line in the code as it
    exists *at filing time*; without the commit hash, an urgent fix (or any
    later change) silently invalidates those references."""
    root = repo_root()
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True,
            text=True, timeout=5,
        ).stdout.strip()
        if not commit:
            return {}
        dirty = bool(subprocess.run(
            ["git", "status", "--porcelain"], cwd=root, capture_output=True,
            text=True, timeout=5,
        ).stdout.strip())
        return {"git_commit": commit, "git_dirty": dirty}
    except (OSError, subprocess.TimeoutExpired):
        return {}


def file_task(
    db: Database, title: str, evidence: str, given: str, observed: str,
    expected: str, manuscript_id: str | None = None,
    session_id: str | None = None, context: dict | None = None,
) -> dict:
    """Record a confirmed tool defect. All repro parts are mandatory —
    a task without a verifiable repro cannot be run or closed honestly.
    Filing-time context (git commit, manuscript version, intent) is
    stamped into metadata so the task stays interpretable after the code
    or manuscript moves on."""
    for field, value in (("title", title), ("evidence", evidence),
                        ("given", given), ("observed", observed),
                        ("expected", expected)):
        if not (value or "").strip():
            raise ValueError(f"improvement task needs a non-empty '{field}'")
    stamp = {**git_context(), **(context or {})}
    row = ko_fields("it")
    row.update(
        manuscript_id=manuscript_id, session_id=session_id, title=title,
        evidence=evidence, given=given, observed=observed, expected=expected,
        status="open", resolution=None, metadata=json.dumps(stamp),
    )
    db.insert("improvement_tasks", row)
    return row


def list_tasks(db: Database, status: str | None = None) -> list[dict]:
    """All tasks (newest first); `status` filters, 'active' means any
    unfinished state."""
    if status == "active":
        marks = ", ".join("?" for _ in ACTIVE)
        rows = db.all(
            f"SELECT * FROM improvement_tasks WHERE status IN ({marks}) "
            "ORDER BY created_at DESC", ACTIVE)
    elif status:
        rows = db.all(
            "SELECT * FROM improvement_tasks WHERE status = ? "
            "ORDER BY created_at DESC", (status,))
    else:
        rows = db.all("SELECT * FROM improvement_tasks ORDER BY created_at DESC")
    return [dict(r) for r in rows]


def open_count(db: Database) -> int:
    marks = ", ".join("?" for _ in ACTIVE)
    return db.one(
        f"SELECT COUNT(*) AS n FROM improvement_tasks WHERE status IN ({marks})",
        ACTIVE)["n"]


def find_task(db: Database, prefix: str) -> dict:
    rows = [dict(r) for r in db.all(
        "SELECT * FROM improvement_tasks WHERE id LIKE ?", (f"%{prefix}%",))]
    if not rows:
        raise LookupError(f"no improvement task matching '{prefix}'")
    if len(rows) > 1:
        raise LookupError(f"'{prefix}' is ambiguous ({len(rows)} tasks)")
    return rows[0]


def repo_root() -> Path:
    """The AuthorLM source tree this running code came from — where the
    fixing agent must work."""
    return Path(__file__).resolve().parent.parent


def bundle(db: Database, task: dict) -> str:
    """The self-contained prompt for a coding agent: evidence + repro +
    repo conventions. Designed to be pasted into (or piped to) a Claude
    Code session with no other context."""
    from .db import loads

    manuscript = None
    if task["manuscript_id"]:
        manuscript = db.one("SELECT name, path FROM manuscripts WHERE id = ?",
                            (task["manuscript_id"],))
    stamp = loads(task["metadata"], {})
    root = repo_root()
    time_machine = []
    if stamp.get("git_commit"):
        time_machine += [
            f"Code at filing time: commit {stamp['git_commit'][:12]}"
            + (" (working tree was dirty)" if stamp.get("git_dirty") else ""),
            "Evidence line references were made against that commit. If HEAD "
            "differs, run `git diff " + stamp["git_commit"][:12] + " -- <cited "
            "files>` FIRST — the defect may have moved or been hotfixed.",
        ]
    if stamp.get("manuscript_version_id"):
        time_machine.append(
            f"Manuscript at filing time: version {stamp['manuscript_version_id']}"
            + (f" (v{stamp['manuscript_version_no']})" if stamp.get("manuscript_version_no") else "")
            + " — its complete file text is frozen in manuscript_versions; "
            "resolve manuscript line references against it, not the working "
            "tree (`authorlm diff`).")
    if stamp.get("intent_id"):
        time_machine.append(f"Active intent at filing time: {stamp['intent_id']}")
    lines = [
        f"# AuthorLM improvement task {task['id']}: {task['title']}",
        "",
        "You are fixing a defect in AuthorLM (a writing collaborator tool), "
        "observed during an authoring session and confirmed wrong by the author.",
        "",
        f"Repository: {root}",
        f"Test entry point: python3 tests/test_api.py  (run from {root})",
        "",
        "## Evidence",
        task["evidence"],
        "",
        "## Repro (structured prose — encode this as a test)",
        f"Given: {task['given']}",
        f"Observed: {task['observed']}",
        f"Expected (author's verdict): {task['expected']}",
        "",
        "## Conventions",
        "- No backward compatibility during prototyping: replace cleanly, "
        "no legacy fallbacks or compatibility shims.",
        "- Encode the repro as a test that FAILS before your fix and PASSES "
        "after it, in the existing check() style of tests/test_api.py.",
        "- When the fix is done and tests pass, record it (do NOT close the "
        "task — the author confirms resolution):",
        f"    authorlm improve propose {task['id']} --note \"<fix summary; test name>\"",
        "",
        "## Provenance (filing-time context — the Time Machine)",
        f"Filed {task['created_at']}"
        + (f" during session {task['session_id']}" if task["session_id"] else "")
        + (f" while working on manuscript '{manuscript['name']}'" if manuscript else "")
        + ".",
        *time_machine,
    ]
    return "\n".join(lines)


def transition(db: Database, task: dict, action: str,
               note: str | None = None) -> dict:
    """Advance a task's lifecycle. Actions: run (open→in_progress),
    propose (→proposed, note = fix summary + encoded test), close
    (→resolved, author's confirmation), dismiss (→dismissed, reason
    mandatory)."""
    status = task["status"]
    if status in ("resolved", "dismissed"):
        raise ValueError(f"task {task['id']} is already {status}")
    if action == "run":
        changes = {"status": "in_progress"}
        message = "marked in_progress — bundle follows"
    elif action == "propose":
        if not (note or "").strip():
            raise ValueError("propose needs --note: fix summary + the name "
                             "of the encoded test")
        changes = {"status": "proposed", "resolution": note}
        message = "fix recorded as proposed — awaiting author confirmation"
    elif action == "close":
        resolution = note or task["resolution"]
        if not (resolution or "").strip():
            raise ValueError("close needs a resolution on record — run "
                             "'propose' first or pass --note")
        changes = {"status": "resolved", "resolution": resolution}
        message = "resolved — author confirmed"
    elif action == "dismiss":
        if not (note or "").strip():
            raise ValueError("dismissal requires a reason — it is recorded "
                             "as evidence")
        changes = {"status": "dismissed", "resolution": note}
        message = "dismissed"
    else:
        raise ValueError(f"unknown action '{action}' (run|propose|close|dismiss)")
    db.update("improvement_tasks", task["id"], changes)
    _record_evidence(db, task, action, note)
    return {"task_id": task["id"], "status": changes["status"],
            "message": message}


def _record_evidence(db: Database, task: dict, signal: str,
                     note: str | None) -> None:
    """Author judgments about the tool are evidence too — but the evidence
    table is manuscript-keyed, so only tasks with manuscript provenance
    leave a trace there."""
    if not task["manuscript_id"] or signal == "run":
        return
    ev = ko_fields("ev")
    ev.update(
        manuscript_id=task["manuscript_id"], episode_id=None,
        evidence_type="improvement_task", signal=signal,
        target=(f"{task['title']}" + (f" — {note}" if note else ""))[:200],
        supports_belief=None, weight="medium",
    )
    db.insert("evidence", ev)
