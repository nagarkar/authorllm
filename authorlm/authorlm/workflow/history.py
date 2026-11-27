"""An essay's earlier workflow instances, for the `earlier_instances` read
request (engines/catalogue.py), which both engines serve: the OpenAI engine
as a function tool, claude-headless through the MCP read server.

letter.md promises the letter "the earlier instances of this workflow on
this essay, with their findings, the author's verdicts and the
discussion"; a new instance started over from an abandoned one
(`core.restart`) reads the abandoned one's letter here. Earlier means
closed: an instance that is still open is the session asking, or holds no
settled verdicts yet. Read-only.

Each instance: its id, kind, number, how it ended (finished, abandoned
with the author's reason, or closed by a skip), its brief, every letter
it had (the one in force last, each superseded one with the step and the
author's note that replaced it), each letter's findings with the author's
verdict and reason and his edits, the Keep list, the discussion of the
letter, and the principles, rulings and skips recorded on it.
"""

from __future__ import annotations

from ..db import Database
from . import context, core


def _findings(rows: list[dict]) -> list[dict]:
    out = []
    for row in rows:
        f = core.finding_view(row)
        body = core._body(row)
        item = {"item": f["item"], "tier": f["tier"], "title": f["title"],
                "where": f["where"], "what": f["what"], "why": f["why"],
                "direction": f["direction"], "verdict": f["verdict"] or "not ruled",
                "reason": f["reason"] or ""}
        if f["edited"]:
            item["edited_by_author"] = f["edited"]
        if body.get("continues"):
            item["continues"] = body["continues"]
        out.append(item)
    return out


def _letters(db: Database, inst: dict, turns: list[dict]) -> list[dict]:
    from .. import workflow_page as page

    letters = context.letter_turns(turns)
    revs = core.revisions(db, inst)
    by_seq = {d["detail"].get("superseded_seq"): d["detail"] for d in revs}
    names = [p["name"] for p in page.passes()]
    out = []
    for i, lt in enumerate(letters):
        in_force = i == len(letters) - 1
        rev = by_seq.get(lt["seq"])
        if in_force:
            rows = core.letter_findings(db, inst)
        elif rev:
            rows = core.findings_of_run(db, inst, rev["superseded_run"])
        else:
            rows = []  # replaced by a redraft before any verdict
        parsed = page.parse_letter(lt["reply"], names)
        entry = {"turn": lt["seq"], "written": lt["created_at"][:16],
                 "in_force": in_force, "findings": _findings(rows),
                 "keep": (page.letter_keep(db, inst, turns)["keep"] if in_force
                          else parsed["keep"]),
                 "passes": parsed["passes"]}
        if lt["author_text"]:
            entry["author_note"] = lt["author_text"]
        if rev:
            entry["superseded"] = {"at_step": rev.get("step"),
                                   "note": rev.get("note") or ""}
        elif not in_force:
            entry["superseded"] = {"at_step": "letter",
                                   "note": "redrafted before any verdict"}
        out.append(entry)
    return out


def instance_history(db: Database, inst: dict) -> dict:
    turns = core.turns(db, inst)
    decisions = core.decisions(db, inst)
    gone = next((d for d in decisions if d["kind"] == "abandon"), None)
    skip = next((d for d in decisions if d["kind"] == "skip"
                 and (d["detail"] or {}).get("to") == "done"), None)
    ended = ("abandoned" if gone else "finished" if inst["finished_at"]
             and not skip else "closed by a skip" if skip else "closed")
    out = {"instance": inst["id"], "kind": inst["kind"], "number": inst["number"],
           "started": inst["created_at"][:16], "ended": ended,
           "brief": inst["brief"] or ""}
    if gone:
        out["abandoned_because"] = gone["reason"]
        out["abandoned_at_step"] = (gone["detail"] or {}).get("step")
    if skip:
        out["closed_because"] = skip["reason"]
    out["letters"] = _letters(db, inst, turns)
    out["discussion"] = [{"author": t["author_text"], "editor": t["reply"]}
                         for t in turns if t["unit_label"] == "letter"
                         and t["action"] == "reply"]
    out["decisions"] = [{"kind": d["kind"], "words": d["words"] or d["reason"]}
                        for d in decisions
                        if d["kind"] in ("principle", "ruling", "skip")]
    return out


def earlier_instances(db: Database, manuscript: dict, essay: str) -> list[dict] | str:
    """Every closed instance of the essay's workflows, oldest first, or a
    sentence saying there is none."""
    closed = [i for i in core.instances(db, manuscript, essay)
              if i["step"] == "done"]
    if not closed:
        return (f"No earlier workflow instance of {essay}: this is its first, "
                "or the others are still open.")
    return [instance_history(db, i) for i in closed]
