"""Promoting a workflow decision to a style law.

A principle ticked at the wrap-up, or a ruling the author marked as one, is
a `workflow_decisions` row and nothing more. Promoting it is a separate,
later decision on one chosen row (owner: "After the decision has been
taken in the moment, the decision to promote them is a separate decision,
and I may want to select a subset to promote"): never in bulk, never
automatic. The wording may be edited before the law is written; the law
goes through the style-law add path (`api.add_style_law`), so it gets its
history row like any other, and its notes quote the decision's words,
reason, and the instance it came from.

Rule (owner): a promoted decision carries the law's id in
`workflow_decisions.law_id` (a foreign key to `style_laws(id)`), and a
decision with a law is not promoted again. `doctor` checks that every
such id names an existing law (`dangling`).
"""

from __future__ import annotations

from ..db import Database
from . import core

# The decision kinds that can become law: what the author stated as a
# principle (Q24) or explicitly marked as a ruling (Q14). Skips, switches
# and list changes are about one instance, not about the prose.
PROMOTABLE = ("principle", "ruling")


def _law(db: Database, law_id: str | None) -> dict | None:
    if not law_id:
        return None
    row = db.one("SELECT l.id, l.aspect, l.statement, l.status, l.file, "
                 "g.name AS guide FROM style_laws l LEFT JOIN style_guides g "
                 "ON g.id = l.guide_id WHERE l.id = ?", (law_id,))
    return dict(row) if row else {"id": law_id, "missing": True}


def promotable(db: Database, instance: dict) -> list[dict]:
    """The instance's principles and rulings, oldest first, each with its
    law (`law`: id, aspect, statement, status, guide) once promoted."""
    return [{**d, "law": _law(db, d.get("law_id"))}
            for d in core.decisions(db, instance) if d["kind"] in PROMOTABLE]


def find_decision(db: Database, instance: dict, ref: str) -> dict:
    """A decision of the instance by its id or an unambiguous id prefix."""
    ref = (ref or "").strip()
    if not ref:
        raise LookupError("name the decision to promote (its id, wd-…)")
    rows = [d for d in core.decisions(db, instance) if d["id"].startswith(ref)]
    if not rows:
        raise LookupError(f"no decision '{ref}' on instance {instance['id']}")
    if len(rows) > 1:
        raise LookupError(f"'{ref}' names {len(rows)} decisions; give more of the id")
    return rows[0]


def notes_for(instance: dict, decision: dict) -> str:
    """The law's notes: why it is law, in the decision's own words."""
    words = decision["words"].replace('"', "'")
    parts = [f'Promoted from workflow decision {decision["id"]} '
             f'({decision["kind"]}, {decision["created_at"][:10]}) on '
             f'{instance["file"]}, {instance["kind"]} #{instance["number"]} '
             f'({instance["id"]}), at the '
             f'{decision["detail"].get("step") or "?"} step: "{words}"']
    if decision.get("reason"):
        parts.append(f"Reason recorded then: {decision['reason']}")
    return ". ".join(parts)


def plan(db: Database, manuscript: dict, instance: dict, ref: str,
         statement: str | None = None, aspect: str | None = None,
         guide: str | None = None) -> dict:
    """What a promotion would write, checked; raises when it cannot.
    Writes nothing (the --dry-run and the page's preview)."""
    from .. import styles as st

    d = find_decision(db, instance, ref)
    if d["kind"] not in PROMOTABLE:
        raise ValueError(f"a {d['kind']} decision is not promoted to law; "
                         f"only {' or '.join(PROMOTABLE)}")
    if d.get("law_id"):
        raise ValueError(f"decision {d['id']} is already promoted: law {d['law_id']}")
    text = (statement if statement is not None else d["words"]).strip()
    if not text:
        raise ValueError("the law needs a statement")
    if not aspect:
        raise ValueError(f"choose the law's aspect (one of "
                         f"{', '.join(st.PROSE_ASPECTS)})")
    if aspect not in st.ASPECTS:
        raise ValueError(f"unknown aspect '{aspect}'")
    if guide:
        g = st.get_guide(db, manuscript["id"], guide)
        if not g:
            raise LookupError(f"no style guide named '{guide}'")
    else:
        g = st.root_guide(db, manuscript["id"])
        if not g:
            raise LookupError("the manuscript has no style guide yet; name one "
                              "with --guide or create the root guide")
    return {"decision": d, "statement": text, "aspect": aspect,
            "guide": g["name"], "notes": notes_for(instance, d)}


def promote(db: Database, manuscript: dict, instance: dict, ref: str,
            statement: str | None = None, aspect: str | None = None,
            guide: str | None = None, actor: str = "cli") -> dict:
    """Write the law and mark the decision with its id, in one
    transaction. Returns the plan with `law` (the new row)."""
    from .. import api

    p = plan(db, manuscript, instance, ref, statement, aspect, guide)
    with db.transaction():
        law = api.add_style_law(db, manuscript, p["aspect"], p["statement"],
                                guide_name=p["guide"], notes=p["notes"],
                                actor=actor)
        db.update("workflow_decisions", p["decision"]["id"], {"law_id": law["id"]})
    return {**p, "law": law}


def dangling(db: Database) -> list[dict]:
    """Promoted decisions whose law id names no style law (for doctor)."""
    return [dict(r) for r in db.all(
        "SELECT d.id, d.instance_id, d.law_id FROM workflow_decisions d "
        "LEFT JOIN style_laws l ON l.id = d.law_id "
        "WHERE d.law_id IS NOT NULL AND l.id IS NULL")]


def doctor_check(path) -> dict:
    """`authorlm doctor`: every promoted decision's law id names a style
    law. Opens the database read-only, so it never migrates; no database,
    or one from before `law_id`, has nothing promoted and passes."""
    import sqlite3
    from pathlib import Path

    name = "promoted decisions"
    path = Path(path)
    if not path.is_file():
        return {"name": name, "ok": True, "state": "no-database",
                "detail": f"no database at {path}", "fix": ""}
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(workflow_decisions)")}
        if "law_id" not in cols:
            return {"name": name, "ok": True, "state": "not-migrated",
                    "detail": "no law_id column yet (added the next time a "
                              "verb opens the workspace)", "fix": ""}
        rows = conn.execute(
            "SELECT d.id, d.law_id FROM workflow_decisions d LEFT JOIN "
            "style_laws l ON l.id = d.law_id WHERE d.law_id IS NOT NULL "
            "AND l.id IS NULL").fetchall()
        total = conn.execute("SELECT COUNT(*) FROM workflow_decisions "
                             "WHERE law_id IS NOT NULL").fetchone()[0]
    finally:
        conn.close()
    if rows:
        listed = ", ".join(f"{d} → {law}" for d, law in rows)
        return {"name": name, "ok": False, "state": "dangling",
                "detail": f"{len(rows)} promoted decision(s) name a law that "
                          f"does not exist: {listed}",
                "fix": "find what removed the law (style_laws rows are never "
                       "deleted by authorlm), then restore it from a backup"}
    return {"name": name, "ok": True, "state": "ok",
            "detail": f"{total} promoted decision(s), each with its law", "fix": ""}
