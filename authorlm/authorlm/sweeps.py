"""Sweep framework vanguards (design: docs/sweep-framework.md).

Two of the three sweep classes live here:

- `readiness` — a PURE auditor: deterministic checks over the registries,
  zero tokens. Run any time; it is the pre-publication checklist.
- `ontology` — a NARROWING auditor: everything extractable
  deterministically is (changed paragraphs, the concepts they mention,
  those concepts' ratified claims); the LLM is asked only for the final
  semantic judgment on assembled (passage, claims) pairs. Findings are
  quote-grounded (hygiene) and land as `incongruence` proposals — the
  jurisdiction for conflicts with settled knowledge.

The third class (LENSES, prompt-first) lives in lenses.py.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from . import proposals
from .concepts import concept_pattern, node_names
from .db import Database, loads
from .revisions import read_manuscript_files

_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n")


# ------------------------------------------------------------- readiness

def readiness(db: Database, manuscript: dict) -> dict:
    """The pre-publication checklist: is every registry in a state an
    export should ship from? Pure auditor — DB + filesystem, no LLM."""
    from .export import load_settings
    from .gdocs import doc_status
    from .illus import slot_report
    from .structure import reading_order

    mid = manuscript["id"]
    root = Path(manuscript["path"])
    items: list[dict] = []

    def item(check: str, ok: bool, detail: str) -> None:
        items.append({"check": check, "ok": ok, "detail": detail})

    # Checked first, before anything else touches `db`: every other item
    # below queries the store, and on a corrupted database those queries
    # fail unpredictably (which table's pages are damaged is arbitrary) —
    # OPS-4. Once the store itself is unsound none of the other findings
    # would be trustworthy anyway, so report just this and stop.
    try:
        integrity_rows = db.all("PRAGMA integrity_check")
        integrity_ok = (len(integrity_rows) == 1
                         and str(integrity_rows[0][0]).lower() == "ok")
        integrity_detail = ("ok" if integrity_ok else
                             "; ".join(str(r[0]) for r in integrity_rows[:5]))
    except Exception as err:  # a store too corrupt to even run the check
        integrity_ok, integrity_detail = False, f"{type(err).__name__}: {err}"
    item("database sound", integrity_ok, integrity_detail)
    if not integrity_ok:
        return {"ready": False, "items": items, "blocking": ["database sound"]}

    files = read_manuscript_files(root)
    slots = slot_report(root)
    item("illustrations rendered", not slots["unrendered"],
         f"{len(slots['unrendered'])} unrendered slot(s)"
         if slots["unrendered"] else "every slot has a candidate")
    item("no orphaned illustration files", not slots["orphaned"],
         f"{len(slots['orphaned'])} orphaned file(s) (prompt edited or "
         "removed)" if slots["orphaned"] else "none")
    item("no malformed illustration refs", not slots["malformed_refs"],
         f"{len(slots['malformed_refs'])} tag(s) carry a ⇢ that doesn't "
         "resolve to a file — fix or delete the ref"
         if slots["malformed_refs"] else "none")

    open_props = proposals.open_proposals(db, mid)
    item("proposals settled", not open_props,
         f"{len(open_props)} open proposal(s) — conflicts with settled "
         "knowledge await verdicts" if open_props else "none open")

    intents = db.all(
        "SELECT * FROM declared_intents WHERE manuscript_id = ? "
        "AND status = 'active'", (mid,))
    item("intents closed", not intents,
         f"{len(intents)} active intent(s) still open"
         if intents else "none active")

    # A file with an open writeup is mid-surgery: on disk it is a
    # placeholder (or, for a created file, whatever beats have landed so
    # far) and the essay is pinned in the database. Without this the
    # checklist reported "ready" over a truncated essay.
    open_writeups = db.all(
        "SELECT file FROM writeups WHERE manuscript_id = ? "
        "AND status = 'active' ORDER BY file", (mid,))
    item("no open writeups", not open_writeups,
         f"mid-rewrite (finish with 'write complete' or put back with "
         f"'write abandon'): {', '.join(r['file'] for r in open_writeups)}"
         if open_writeups else "no writeup is open")

    _, unlisted = reading_order(files)
    item("toc covers every file", not unlisted,
         f"unlisted (alphabetical fallback): {', '.join(unlisted)}"
         if unlisted else "reading order fully declared")

    checked_out = [f for f, s in doc_status(db, manuscript).items()
                   if not f.startswith("_") and isinstance(s, dict)
                   and s.get("checked_out")]
    item("no Google Docs checkouts", not checked_out,
         f"checked out (Doc is the working copy): {', '.join(checked_out)}"
         if checked_out else "local files are the working copy")

    settings = load_settings(manuscript)
    missing = [k for k in ("title", "author") if not settings.get(k)]
    item("export settings set", not missing,
         f"unset: {', '.join(missing)}" if missing else
         "title and author present")

    item("pandoc available", shutil.which("pandoc") is not None,
         "found" if shutil.which("pandoc") else
         "not on PATH — brew install pandoc")

    unconfirmed = db.one(
        "SELECT COUNT(*) AS n FROM concept_nodes WHERE manuscript_id = ? "
        "AND status != 'retired' AND metadata LIKE '%\"confirmed\": false%'",
        (mid,))["n"]
    item("triage backlog", unconfirmed == 0,
         f"{unconfirmed} unconfirmed extracted concept(s) "
         "(informational — not a publication blocker)"
         if unconfirmed else "empty")

    blocking = [i for i in items if not i["ok"]
                and i["check"] != "triage backlog"]
    return {"ready": not blocking, "items": items,
            "blocking": [i["check"] for i in blocking]}


# -------------------------------------------------------------- ontology

ONTOLOGY_SYSTEM = """\
You audit a philosophy manuscript for internal consistency. For each
numbered ITEM you receive a paragraph of the manuscript and the SETTLED
claims (the author's ratified definitions and relationships) for the
concepts that paragraph mentions. Your sole task: report where the
paragraph CONTRADICTS a resolved claim. Respect the author's framework —
you are comparing the text against the author's own settled knowledge,
never against outside views. Only clear contradictions; stylistic
variation, elaboration, or partial coverage are NOT findings. Reply with
JSON only:
{"findings": [{"item": <n>, "concept": "<name>", "quote": "<verbatim
sentence from the paragraph that conflicts>", "claim": "<the resolved
claim it conflicts with>", "why": "<one short sentence>"}]}
Return {"findings": []} when nothing conflicts."""


def _changed_files(db: Database, mid: str) -> set[str] | None:
    rows = db.all(
        "SELECT * FROM manuscript_versions WHERE manuscript_id = ? "
        "ORDER BY version_no DESC LIMIT 2", (mid,))
    if len(rows) < 2:
        return None
    new, old = loads(rows[0]["files"], {}), loads(rows[1]["files"], {})
    return {f for f in new if old.get(f) != new[f]} | (set(old) - set(new))


def _claims_for(db: Database, mid: str, node: dict,
                nodes_by_id: dict) -> list[str]:
    claims = []
    if node["notes"]:
        claims.append(f"{node['name']}: {node['notes']}")
    for edge in db.all(
        "SELECT * FROM concept_edges WHERE manuscript_id = ? "
        "AND relation IN ('refutes', 'depends_on', 'contrasts_with') "
        "AND status IN ('declared', 'confirmed') "
        "AND (from_node = ? OR to_node = ?)",
        (mid, node["id"], node["id"]),
    ):
        a = nodes_by_id.get(edge["from_node"])
        b = nodes_by_id.get(edge["to_node"])
        if a and b:
            claims.append(f"{a['name']} {edge['relation'].replace('_', ' ')} "
                          f"{b['name']}")
    return claims


def ontology(db: Database, manuscript: dict, llm,
             file: str | None = None) -> dict:
    """Narrowing auditor: check changed (or one file's) paragraphs against
    the resolved claims of the concepts they mention. Returns a summary;
    findings land as `incongruence` proposals for the author's verdict."""
    mid = manuscript["id"]
    files = read_manuscript_files(Path(manuscript["path"]))
    if file:
        if file not in files:
            raise LookupError(f"'{file}' is not a manuscript file")
        targets = {file}
        scope = file
    else:
        changed = _changed_files(db, mid)
        targets = (changed & set(files)) if changed else set()
        scope = f"{len(targets)} changed file(s)"
        if not targets:
            return {"scope": scope, "pairs": 0, "findings": 0,
                    "dropped_ungrounded": 0, "note": "nothing changed "
                    "since the previous version — name a file to audit it"}

    nodes = [dict(n) for n in db.all(
        "SELECT * FROM concept_nodes WHERE manuscript_id = ? "
        "AND status != 'retired'", (mid,))]
    nodes_by_id = {n["id"]: n for n in nodes}
    # Each node's claims are fixed for the sweep — compute once per node
    # instead of once per (paragraph, matched node) pair.
    claims_by_node = {n["id"]: _claims_for(db, mid, n, nodes_by_id) for n in nodes}

    # Deterministic narrowing: (paragraph, settled claims) pairs.
    pairs: list[dict] = []
    for fname in sorted(targets):
        for paragraph in _PARAGRAPH_SPLIT.split(files[fname]):
            paragraph = paragraph.strip()
            if len(paragraph) < 80:
                continue
            claims: list[str] = []
            for node in nodes:
                if any(concept_pattern(nm).search(paragraph)
                       for nm in node_names(node)):
                    claims.extend(claims_by_node[node["id"]])
            if claims:
                pairs.append({"file": fname, "paragraph": paragraph,
                              "claims": sorted(set(claims))})
    if not pairs:
        return {"scope": scope, "pairs": 0, "findings": 0,
                "dropped_ungrounded": 0}

    user = "\n\n".join(
        f"ITEM {i}\nPARAGRAPH ({p['file']}):\n{p['paragraph']}\n"
        f"SETTLED:\n" + "\n".join(f"- {c}" for c in p["claims"])
        for i, p in enumerate(pairs, start=1))
    result = llm.complete_json(ONTOLOGY_SYSTEM, user)
    findings = (result or {}).get("findings", []) \
        if isinstance(result, dict) else []

    created, dropped = 0, 0
    for f in findings:
        if not isinstance(f, dict):
            dropped += 1
            continue
        try:
            item = int(f.get("item", 0))
        except (TypeError, ValueError):
            dropped += 1
            continue
        # Python wraps negative indices, so `item=0` (-> index -1) or any
        # other non-positive value would silently bind to the WRONG
        # paragraph instead of tripping an IndexError. Bounds-check
        # explicitly rather than relying on indexing to raise.
        if not (1 <= item <= len(pairs)):
            dropped += 1
            continue
        pair = pairs[item - 1]
        quote = " ".join(str(f.get("quote", "")).split())
        # Hygiene gate: the quote must be verbatim in the paragraph.
        if not quote or quote.lower() not in \
                " ".join(pair["paragraph"].split()).lower():
            dropped += 1
            continue
        concept = str(f.get("concept", "")).strip()
        node = next((n for n in nodes if n["name"].lower()
                     == concept.lower()), None)
        if node is None:
            dropped += 1
            continue
        if proposals.create(
            db, mid, "incongruence", node["id"],
            {"file": pair["file"], "concept": node["name"], "quote": quote,
             "claim": str(f.get("claim", ""))[:300],
             "why": str(f.get("why", ""))[:300]},
            source="ontology-sweep",
        ):
            created += 1
    return {"scope": scope, "pairs": len(pairs), "findings": created,
            "dropped_ungrounded": dropped}
