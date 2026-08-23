"""Critique intake (docs/critique-pass-design.md §3).

External critique enters as a candidate pile with critic provenance:
report items become *proposed* intents and *proposed* style elements;
nothing becomes standing law or a sanctioned work order until the author
triages it. Rejections are kept forever with the author's reason —
they are evidence, often the most valuable kind.

The importer consumes a manifest (JSON) rather than the raw report, so
the judgment step — classifying items and mapping units to scopes — is
visible and auditable, and the load itself is deterministic and
idempotent. `parse_units` turns a markdown report into (unit, items)
pairs for building that manifest.
"""

from __future__ import annotations

import json
import re

from .db import Database, ko_fields, loads
from . import sessions, styles

HEADING = re.compile(r"^#{1,2}\s+(.*)$")
ITEM_START = re.compile(r"^\s{0,3}(\d+)\.\s+(.*)$")


def _clean(text: str) -> str:
    """Markdown → plain prose: unwrap lines, drop bold markers, unescape."""
    text = re.sub(r"\s+", " ", text).strip()
    text = text.replace("**", "")
    text = re.sub(r"\\(.)", r"\1", text)
    return text


def parse_units(md_text: str) -> dict[str, list[str]]:
    """Split a markdown report into {heading: [numbered item texts]}.
    Only numbered list items are captured; sections without them map to
    an empty list (their prose is commentary, not actionable items)."""
    units: dict[str, list[str]] = {}
    title = None
    item_lines: list[str] | None = None

    def flush_item():
        nonlocal item_lines
        if title is not None and item_lines:
            units[title].append(_clean(" ".join(item_lines)))
        item_lines = None

    for line in md_text.splitlines():
        m = HEADING.match(line)
        if m:
            flush_item()
            title = _clean(m.group(1))
            units.setdefault(title, [])
            continue
        m = ITEM_START.match(line)
        if m:
            flush_item()
            item_lines = [m.group(2)]
            continue
        if item_lines is not None:
            if line.strip():
                item_lines.append(line.strip())
            else:
                flush_item()
    flush_item()
    return units


# ------------------------------------------------------------------ import

def _existing(db: Database, manuscript_id: str, table: str, source_id: str,
              unit: str, ordinal: int):
    return db.one(
        f"SELECT id FROM {table} WHERE manuscript_id = ? AND source_id = ? "
        "AND json_extract(metadata, '$.critique.unit') = ? "
        "AND json_extract(metadata, '$.critique.ordinal') = ?",
        (manuscript_id, source_id, unit, ordinal),
    )


def import_manifest(db: Database, manuscript_id: str, manifest: dict) -> dict:
    """Load a critique manifest. Idempotent on (source, unit, ordinal):
    re-importing skips items already present, so a corrected manifest can
    be re-run safely.

    manifest = {
      "source": {"name": ..., "detail": ...},
      "items": [
        {"kind": "intent", "text": ..., "unit": ..., "ordinal": 1,
         "scope": "recapitulation.md" | null},
        {"kind": "style_element", "text": ..., "unit": ..., "ordinal": 1,
         "aspect": "tone", "guide": "SMSTTD house style" | null,
         "file": null | "essay.md", "notes": ...},
      ]
    }
    """
    src = manifest["source"]
    source_id = db.source("critic", src["name"], src.get("detail"))
    imported = {"intents": 0, "style_laws": 0, "skipped": 0}
    errors: list[str] = []
    for item in manifest["items"]:
        unit, ordinal = item["unit"], item["ordinal"]
        meta = json.dumps({"critique": {"unit": unit, "ordinal": ordinal}})
        if item["kind"] == "intent":
            if _existing(db, manuscript_id, "declared_intents", source_id,
                         unit, ordinal):
                imported["skipped"] += 1
                continue
            row = sessions.declare_intent(
                db, manuscript_id, item["text"], scope=item.get("scope"),
                status="proposed", source_id=source_id)
            db.update("declared_intents", row["id"], {"metadata": meta})
            imported["intents"] += 1
        elif item["kind"] == "style_element":
            if _existing(db, manuscript_id, "style_laws", source_id,
                         unit, ordinal):
                imported["skipped"] += 1
                continue
            guide = None
            if item.get("guide"):
                guide_row = db.one(
                    "SELECT * FROM style_guides WHERE manuscript_id = ? AND name = ?",
                    (manuscript_id, item["guide"]))
                if not guide_row:
                    errors.append(f"unknown guide '{item['guide']}' "
                                  f"({unit} #{ordinal})")
                    continue
                guide = dict(guide_row)
            row = styles.add_element(
                db, manuscript_id, item["aspect"], item["text"],
                guide=guide, file=item.get("file"), notes=item.get("notes"),
                status="proposed", source_id=source_id)
            db.update("style_laws", row["id"], {"metadata": meta})
            imported["style_laws"] += 1
        else:
            errors.append(f"unknown kind '{item['kind']}' ({unit} #{ordinal})")
    return {**imported, "source_id": source_id, "errors": errors}


# ------------------------------------------------------------------ triage

def pending(db: Database, manuscript_id: str,
            scope: str | None = None,
            manuscript: dict | None = None) -> dict:
    """Proposed critique items awaiting the author's verdict.

    `scope` = an essay file: everything the essay's edit pass will
    consult — items scoped to the file, to its toc ancestors, AND
    manuscript-wide — exactly the preflight gate's chain, so a
    just-in-time sitting clears the gate by construction. (Pass
    `manuscript` to resolve the toc chain; without it only the file's
    own items are selected.) `scope` = "manuscript": manuscript-wide
    items only (the global sitting). Style elements are global law and
    appear in every sitting."""
    args: list = [manuscript_id]
    scope_sql = ""
    if scope == "manuscript":
        scope_sql = " AND scope IS NULL"
    elif scope:
        chain: list = [scope]
        if manuscript is None:
            row = db.one("SELECT * FROM manuscripts WHERE id = ?",
                         (manuscript_id,))
            manuscript = dict(row) if row else None
        if manuscript is not None:
            from .passes import toc_ancestors
            chain += toc_ancestors(manuscript, scope)
        marks = ",".join("?" * len(chain))
        scope_sql = f" AND (scope IN ({marks}) OR scope IS NULL)"
        args.extend(chain)
    intents = db.all(
        "SELECT * FROM declared_intents WHERE manuscript_id = ? "
        f"AND status = 'proposed'{scope_sql} ORDER BY created_at, id", tuple(args))
    elements = db.all(
        "SELECT * FROM style_laws WHERE manuscript_id = ? "
        "AND status = 'proposed' ORDER BY created_at, id", (manuscript_id,))
    return {"intents": [dict(r) for r in intents],
            "elements": [dict(r) for r in elements]}


def queue(db: Database, manuscript_id: str,
          scope: str | None = None) -> list[tuple[str, dict]]:
    """The pending queue in its one canonical order (intents in import
    order, then style elements). Every surface — CLI list numbering,
    number-based verdicts, MCP listing — uses this, so a number always
    means the same item within one scope."""
    p = pending(db, manuscript_id, scope)
    return ([("intent", it) for it in p["intents"]]
            + [("element", el) for el in p["elements"]])


def _triage_evidence(db: Database, manuscript_id: str, signal: str,
                     target: str) -> None:
    ev = ko_fields("ev")
    ev.update(manuscript_id=manuscript_id, episode_id=None,
              evidence_type="critique_triage", signal=signal,
              target=target[:200], supports_belief=None, weight="high")
    db.insert("evidence", ev)


def accept_intent(db: Database, manuscript_id: str, intent: dict) -> None:
    db.update("declared_intents", intent["id"], {"status": "active"})
    _triage_evidence(db, manuscript_id, "accepted", intent["statement"])


def reject_intent(db: Database, manuscript_id: str, intent: dict,
                  reason: str) -> None:
    """A rejection is kept forever; the author's reason, verbatim, is the
    evidence the system learns from."""
    db.update("declared_intents", intent["id"],
              {"status": "rejected", "outcome": reason})
    _triage_evidence(db, manuscript_id, "rejected",
                     f"{intent['statement']} — {reason}")


def _lineage(item: dict) -> str:
    """Metadata for a modified acceptance: the critic's original text and
    source survive as lineage under the critique key."""
    meta = loads(item["metadata"], {})
    meta.setdefault("critique", {})
    meta["critique"]["original_text"] = item["statement"]
    meta["critique"]["original_source_id"] = item["source_id"]
    return json.dumps(meta)


def revise_intent(db: Database, manuscript_id: str, intent: dict,
                  new_text: str) -> None:
    """Modified acceptance: the author's words win, so provenance flips to
    the author — the accepted statement is now the author's judgment, with
    the critic's original kept as lineage. The original→final pair is
    recorded as evidence; modified acceptances are the highest-value
    evidence stream."""
    db.update("declared_intents", intent["id"], {
        "status": "active", "statement": new_text,
        "source_id": db.source("author"), "metadata": _lineage(intent)})
    _triage_evidence(db, manuscript_id, "modified",
                     f"{intent['statement']} → {new_text}")


def revise_element(db: Database, manuscript_id: str, element: dict,
                   new_text: str) -> None:
    db.update("style_laws", element["id"], {
        "status": "active", "statement": new_text,
        "source_id": db.source("author"), "metadata": _lineage(element)})
    _triage_evidence(db, manuscript_id, "modified",
                     f"{element['statement']} → {new_text}")


def accept_element(db: Database, manuscript_id: str, element: dict) -> None:
    db.update("style_laws", element["id"], {"status": "active"})
    _triage_evidence(db, manuscript_id, "accepted", element["statement"])


def reject_element(db: Database, manuscript_id: str, element: dict,
                   reason: str) -> None:
    meta = loads(element["metadata"], {})
    meta["rejection_reason"] = reason
    db.update("style_laws", element["id"],
              {"status": "rejected", "metadata": json.dumps(meta)})
    _triage_evidence(db, manuscript_id, "rejected",
                     f"{element['statement']} — {reason}")


def status(db: Database, manuscript_id: str) -> list[dict]:
    """Per-critic tallies for briefing badges and `critique status`."""
    rows = db.all(
        """SELECT s.id AS source_id, s.name,
                  SUM(CASE WHEN i.status = 'proposed' THEN 1 ELSE 0 END) AS proposed,
                  SUM(CASE WHEN i.status IN ('active', 'completed') THEN 1 ELSE 0 END) AS accepted,
                  SUM(CASE WHEN i.status = 'rejected' THEN 1 ELSE 0 END) AS rejected
           FROM sources s JOIN declared_intents i ON i.source_id = s.id
           WHERE s.kind = 'critic' AND i.manuscript_id = ?
           GROUP BY s.id""", (manuscript_id,))
    result = []
    for r in rows:
        el = db.one(
            """SELECT SUM(CASE WHEN status = 'proposed' THEN 1 ELSE 0 END) AS proposed,
                      SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) AS accepted,
                      SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END) AS rejected
               FROM style_laws WHERE manuscript_id = ? AND source_id = ?""",
            (manuscript_id, r["source_id"]))
        result.append({
            "source_id": r["source_id"], "name": r["name"],
            "intents": {"proposed": r["proposed"] or 0,
                        "accepted": r["accepted"] or 0,
                        "rejected": r["rejected"] or 0},
            "elements": {"proposed": el["proposed"] or 0,
                         "accepted": el["accepted"] or 0,
                         "rejected": el["rejected"] or 0},
        })
    return result
