"""Named snapshots of a manuscript's style laws.

A snapshot records, under a name the author gives it, every style law
active in one manuscript at that moment: the law's id, its version, and
its words and scope as they stood (aspect, statement, notes, overrides,
guide or file). A snapshot is never edited or deleted, and a name is
taken once per manuscript. Restoring one brings back exactly that set,
through the laws' own machinery (styles.unretire_element, edit_element,
retire_element and the move record), so every change lands in each
law's history like any other: laws in the snapshot come back in force
with their recorded words and scope, and laws in force now that were not
in it are retired, citing the snapshot.

The workspace defaults (library/style-defaults.toml) are not rows in the
database and are not recorded; a snapshot is the manuscript's own law.
Laws that are proposed or rejected now are left alone by a restore and
listed, since bringing them in force is not a restore of law the author
once ratified.
"""

from __future__ import annotations

from . import styles as st
from .db import Database, ko_fields

# The words a restore compares and puts back, through styles.edit_element.
WORDS = ("aspect", "statement", "notes", "overrides")
# `style snapshot <verb>` reads these as verbs, so no snapshot takes them as
# a name.
VERBS = ("list", "show", "restore")


def take(db: Database, manuscript_id: str, name: str, note: str = "") -> dict:
    """Record every active law of the manuscript under `name`. A name the
    manuscript already has is declined (ValueError)."""
    name = (name or "").strip()
    if not name:
        raise ValueError("a snapshot needs a name")
    if name in VERBS:
        raise ValueError(f"'{name}' is a snapshot verb; choose another name")
    if get(db, manuscript_id, name):
        raise ValueError(f"a style-law snapshot named '{name}' already exists; "
                         "names are taken once")
    laws = db.all(
        "SELECT * FROM style_laws WHERE manuscript_id = ? AND status = 'active' "
        "ORDER BY created_at, id", (manuscript_id,))
    row = ko_fields("ss")
    row.update(manuscript_id=manuscript_id, name=name, note=note or "")
    with db.transaction():
        db.insert("style_snapshots", row)
        for law in laws:
            db.insert("style_snapshot_laws", {
                **ko_fields("ssl"), "manuscript_id": manuscript_id,
                "snapshot_id": row["id"], "law_id": law["id"],
                "law_version": law["version"], "aspect": law["aspect"],
                "statement": law["statement"], "notes": law["notes"],
                "overrides": law["overrides"], "guide_id": law["guide_id"],
                "file": law["file"]})
    return {**row, "laws": len(laws)}


def get(db: Database, manuscript_id: str, name: str) -> dict | None:
    row = db.one("SELECT * FROM style_snapshots WHERE manuscript_id = ? "
                 "AND name = ?", (manuscript_id, name))
    return dict(row) if row else None


def _need(db: Database, manuscript_id: str, name: str) -> dict:
    snap = get(db, manuscript_id, name)
    if not snap:
        known = [s["name"] for s in list_snapshots(db, manuscript_id)]
        raise LookupError(f"no style-law snapshot named '{name}'"
                          + (f" (known: {', '.join(known)})" if known else ""))
    return snap


def list_snapshots(db: Database, manuscript_id: str) -> list[dict]:
    """Every snapshot of the manuscript, oldest first, with its law count."""
    return [dict(r) for r in db.all(
        "SELECT s.*, (SELECT COUNT(*) FROM style_snapshot_laws l "
        "WHERE l.snapshot_id = s.id) AS laws FROM style_snapshots s "
        "WHERE s.manuscript_id = ? ORDER BY s.created_at, s.rowid",
        (manuscript_id,))]


def show(db: Database, manuscript_id: str, name: str) -> dict:
    """The snapshot and its laws, each with its status now and whether its
    words or scope have changed since."""
    snap = _need(db, manuscript_id, name)
    rows = db.all(
        "SELECT l.*, s.status AS now_status, s.version AS now_version, "
        "s.aspect AS now_aspect, s.statement AS now_statement, "
        "s.notes AS now_notes, s.overrides AS now_overrides, "
        "s.guide_id AS now_guide_id, s.file AS now_file "
        "FROM style_snapshot_laws l LEFT JOIN style_laws s ON s.id = l.law_id "
        "WHERE l.snapshot_id = ? ORDER BY l.rowid", (snap["id"],))
    laws = []
    for r in rows:
        r = dict(r)
        changed = [k for k in (*WORDS, "guide_id", "file")
                   if r["now_status"] is not None and r[k] != r["now_" + k]]
        laws.append({
            "id": r["law_id"], "version": r["law_version"], "aspect": r["aspect"],
            "statement": r["statement"], "notes": r["notes"],
            "overrides": r["overrides"], "guide_id": r["guide_id"],
            "file": r["file"], "status_now": r["now_status"] or "missing",
            "changed": changed})
    return {**snap, "laws": laws}


def plan(db: Database, manuscript_id: str, name: str) -> dict:
    """What a restore of `name` would change, without changing anything:
    `unretire` (retired now, in the snapshot), `edit` (words differ:
    field → (now, then)), `move` (scope differs), `retire` (active now,
    not in the snapshot), `left` (proposed, rejected or missing now)."""
    snap = show(db, manuscript_id, name)
    recorded = {law["id"]: law for law in snap["laws"]}
    out: dict = {"snapshot": snap, "unretire": [], "edit": [], "move": [],
                 "retire": [], "left": []}
    for law in snap["laws"]:
        if law["status_now"] not in ("active", "retired"):
            out["left"].append(law)
            continue
        now = dict(db.one("SELECT * FROM style_laws WHERE id = ?", (law["id"],)))
        if law["status_now"] == "retired":
            out["unretire"].append(law)
        diff = {k: (now[k], law[k]) for k in WORDS if now[k] != law[k]}
        if diff:
            out["edit"].append({**law, "diff": diff})
        if (now["guide_id"], now["file"]) != (law["guide_id"], law["file"]):
            out["move"].append({**law, "from": _level(db, now),
                                "to": _level(db, law)})
    for law in db.all(
            "SELECT * FROM style_laws WHERE manuscript_id = ? AND status = 'active' "
            "ORDER BY created_at, id", (manuscript_id,)):
        if law["id"] not in recorded:
            out["retire"].append(dict(law))
    return out


def restore(db: Database, manuscript_id: str, name: str, actor: str = "cli",
            dry_run: bool = False) -> dict:
    """Bring back exactly the snapshot's set (see plan). One transaction:
    all of it lands, or none. Every change is in each law's history with
    the snapshot's name as its reason."""
    changes = plan(db, manuscript_id, name)
    if dry_run:
        return changes
    reason = f"restore of style-law snapshot '{name}'"
    with db.transaction():
        for law in changes["unretire"]:
            row = dict(db.one("SELECT * FROM style_laws WHERE id = ?", (law["id"],)))
            st.unretire_element(db, row, actor=actor, reason=reason)
        for law in changes["edit"]:
            row = dict(db.one("SELECT * FROM style_laws WHERE id = ?", (law["id"],)))
            # edit_element reads None as "no change" and "" as "clear".
            st.edit_element(db, row, actor=actor, **{
                k: (then if then is not None else "")
                for k, (_now, then) in law["diff"].items()})
        for law in changes["move"]:
            row = dict(db.one("SELECT * FROM style_laws WHERE id = ?", (law["id"],)))
            db.update("style_laws", row["id"],
                      {"guide_id": law["guide_id"], "file": law["file"]})
            st.record_event(db, {**row, "file": law["file"]}, "move", actor,
                            {"from": law["from"], "to": law["to"],
                             "reason": reason})
        for law in changes["retire"]:
            st.retire_element(db, law, reason=reason, actor=actor)
    return changes


def _level(db: Database, row: dict) -> str:
    """Where a law sits, named as `style show` names it."""
    if row.get("file"):
        return f"{row['file']} (file-local)"
    guide = db.one("SELECT name FROM style_guides WHERE id = ?", (row["guide_id"],))
    return guide["name"] if guide else "?"
