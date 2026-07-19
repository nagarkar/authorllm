"""The plan: what should be written, and where does it belong?

Joins the active declared intents (the author's open todos) with the
Concept Graph and the TOC reading order to propose placements for every
unrealized concept (RFC §21.2: topic sequencing and future chapter
planning). Placement reasoning is deterministic and free (P9); optional
stub drafting is the generative layer, written into `_drafts/` — an
observation-ignored directory — as Level-2 prepared revisions the author
may pull in or ignore (§16.1).
"""

from __future__ import annotations

import re
from pathlib import Path

from .analysis import find_precedents
from .concepts import concept_pattern
from .db import Database, loads
from .guidance import PREREQUISITE_FIRST, PREREQUISITE_SECOND
from .llm import LLMClient
from .structure import reading_order

DRAFTS_DIR = "_drafts"
SKIP_KINDS = {"historical_reference", "mathematical_construct"}


def build_plan(db: Database, manuscript: dict) -> dict:
    """Deterministic writing plan: one item per unrealized concept, with a
    placement derived from realized graph neighbors and the reading order,
    prerequisite ordering, and any matching active intent."""
    mid = manuscript["id"]
    latest = db.one(
        "SELECT files FROM manuscript_versions WHERE manuscript_id = ? "
        "ORDER BY version_no DESC LIMIT 1",
        (mid,),
    )
    files = loads(latest["files"], {}) if latest else {}
    order, toc_unlisted = reading_order(files)
    position = {name: index for index, name in enumerate(order)}

    nodes = {n["id"]: dict(n) for n in db.all(
        "SELECT * FROM concept_nodes WHERE manuscript_id = ? AND status != 'retired'",
        (mid,),
    )}
    edges = [dict(e) for e in db.all(
        "SELECT * FROM concept_edges WHERE manuscript_id = ? "
        "AND relation != 'co_occurs' AND status NOT IN ('rejected', 'retired')",
        (mid,),
    )]
    intents = [dict(i) for i in db.all(
        "SELECT * FROM declared_intents WHERE manuscript_id = ? AND status = 'active'",
        (mid,),
    )]

    def prerequisites_of(node_id: str) -> list[str]:
        required = []
        for edge in edges:
            if edge["relation"] in PREREQUISITE_SECOND and edge["from_node"] == node_id:
                required.append(edge["to_node"])
            elif edge["relation"] in PREREQUISITE_FIRST and edge["to_node"] == node_id:
                required.append(edge["from_node"])
        return required

    items = []
    for node_id, node in nodes.items():
        if node["status"] == "realized" or node["kind"] in SKIP_KINDS:
            continue
        neighbors = []
        for edge in edges:
            if node_id not in (edge["from_node"], edge["to_node"]):
                continue
            other_id = edge["to_node"] if edge["from_node"] == node_id else edge["from_node"]
            other = nodes.get(other_id)
            if other:
                neighbors.append((other, edge["relation"]))
        realized_neighbors = [
            (other, rel) for other, rel in neighbors
            if other["status"] == "realized" and other["introduced_in"] in position
        ]
        write_first = [
            nodes[p]["name"] for p in prerequisites_of(node_id)
            if p in nodes and nodes[p]["status"] != "realized"
        ]
        matching = [
            i["statement"] for i in intents
            if concept_pattern(node["name"]).search(i["statement"])
        ]

        if realized_neighbors:
            anchor, _ = max(realized_neighbors,
                            key=lambda pair: position[pair[0]["introduced_in"]])
            placement_file = anchor["introduced_in"]
            placement = (f"in or just after {placement_file} "
                         f"(near '{anchor['name']}')")
        else:
            placement_file = None
            placement = "a new standalone document (add it to toc.md where it belongs)"

        reasons = []
        if matching:
            reasons.append(f"serves your active intent: \"{matching[0]}\"")
        if realized_neighbors:
            related = "; ".join(
                f"'{other['name']}' ({relation}, in {other['introduced_in']})"
                for other, relation in realized_neighbors[:3]
            )
            reasons.append(f"anchored by realized neighbors: {related}")
        if not reasons:
            reasons.append("declared in the Concept Graph but unconnected — "
                           "consider linking it first")

        precedents = find_precedents(db, mid, node_id, limit=1)
        items.append({
            "concept": node["name"],
            "kind": node["kind"],
            "notes": node["notes"],
            "placement": placement,
            "placement_file": placement_file,
            "write_first": write_first,
            "reasons": reasons,
            "intent": matching[0] if matching else None,
            "precedent": precedents[0] if precedents else None,
            "_position": position.get(placement_file, len(order)),
        })

    # Prerequisites before dependents; then by manuscript position.
    items.sort(key=lambda i: (len(i["write_first"]), i["_position"], i["concept"]))
    for item in items:
        item.pop("_position")
    return {"items": items, "toc_unlisted": toc_unlisted}


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "concept"


def draft_stubs(db: Database, manuscript: dict, llm: LLMClient,
                items: list[dict], limit: int = 3) -> list[dict]:
    """Level-2 prepared revisions: draft an opening stub for the top plan
    items into _drafts/ (ignored by observation). The author pulls text in,
    or doesn't — the manuscript is never touched."""
    drafts_dir = Path(manuscript["path"]) / DRAFTS_DIR
    results = []
    for item in items[:limit]:
        context = [
            f"Concept to introduce: {item['concept']} ({item['kind']}).",
        ]
        if item["notes"]:
            context.append(f"Working definition: {item['notes']}")
        if item["intent"]:
            context.append(f"The author's declared intent: {item['intent']}")
        if item["placement_file"]:
            context.append(f"It will live {item['placement']}.")
        if item["precedent"]:
            context.append(
                f"Precedent — {item['precedent']['label']}, the author: "
                + "; then ".join(item["precedent"]["actions"]) + "."
            )
        draft = llm.complete(
            "You are an editorial collaborator for a philosophy manuscript. "
            "Draft a short opening passage (2-3 paragraphs) introducing the "
            "concept, following the author's demonstrated approach when a "
            "precedent is given. Reply with the passage only. Format any "
            "mathematics as MathJax: inline $...$, display $$...$$.",
            "\n".join(context),
        )
        if not draft:
            continue
        drafts_dir.mkdir(exist_ok=True)
        path = drafts_dir / f"{_slug(item['concept'])}.md"
        path.write_text(
            f"<!-- AuthorLM draft stub for '{item['concept']}' — placement: "
            f"{item['placement']}. Pull in what serves you; delete the rest. -->\n\n"
            f"{draft.strip()}\n",
            encoding="utf-8",
        )
        results.append({"concept": item["concept"], "path": str(path)})
    return results
