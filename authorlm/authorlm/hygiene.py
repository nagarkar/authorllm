"""Deterministic hygiene filters (design: docs/design-backlog.md, "Sweep
framework").

Gates at the LLM→store boundaries plus a retroactive sweep — all pure
string/DB operations, zero tokens. Purpose: protect the author's triage
attention and the purity of the evidence stream (an ungrounded suggestion
the author rejects teaches the policy learner something false).

Division of judgment, deliberately: extracted CONCEPTS are never gated at
admission — an unmentioned concept stays a *declared* hypothesis for
triage (ratified behavior; the author sometimes confirms an abstraction
the text never names verbatim). Groundedness for concepts is therefore a
sweep-report concern. Extracted EDGES are gated at admission: a link is
proposed only when the text asserts it, so an edge whose endpoints the
model was never shown in text is dropped at the door.
"""

from __future__ import annotations

import re

from .concepts import concept_pattern, node_names
from .db import Database, loads


def mentioned_in(files: dict[str, str], names: list[str]) -> str | None:
    """First file whose text mentions any of `names` (word-boundary,
    plural-tolerant, alias-aware via the caller), or None."""
    for fname, text in files.items():
        for name in names:
            if name and concept_pattern(name).search(text):
                return fname
    return None


def ungrounded_concepts(db: Database, mid: str,
                        files: dict[str, str]) -> list[dict]:
    """Unconfirmed machine-extracted concepts whose name and aliases
    appear nowhere in the current manuscript text. Report-only input:
    retiring is the author's call (or the sweep's --apply)."""
    out = []
    for node in db.all(
        "SELECT * FROM concept_nodes WHERE manuscript_id = ? "
        "AND status != 'retired'", (mid,),
    ):
        meta = loads(node["metadata"], {})
        if meta.get("origin") != "extracted" or meta.get("confirmed"):
            continue
        if mentioned_in(files, node_names(node)) is None:
            out.append({"id": node["id"], "name": node["name"], "kind": node["kind"],
                        "status": node["status"]})
    return out


def ungrounded_edges(db: Database, mid: str,
                     files: dict[str, str]) -> list[dict]:
    """Inferred (never author-confirmed) edges with an endpoint that is
    mentioned nowhere in the manuscript — a relationship the text cannot
    be asserting."""
    nodes = {n["id"]: n for n in db.all(
        "SELECT * FROM concept_nodes WHERE manuscript_id = ?", (mid,),
    )}
    out = []
    for edge in db.all(
        "SELECT * FROM concept_edges WHERE manuscript_id = ? "
        "AND status = 'inferred'", (mid,),
    ):
        missing = [
            nodes[edge[side]]["name"]
            for side in ("from_node", "to_node")
            if edge[side] in nodes
            and mentioned_in(files, node_names(nodes[edge[side]])) is None
        ]
        if missing:
            out.append({
                "id": edge["id"],
                "edge": f"{nodes[edge['from_node']]['name']} "
                        f"—{edge['relation']}→ "
                        f"{nodes[edge['to_node']]['name']}",
                "unmentioned": missing,
            })
    return out


def stale_suggestions(db: Database, mid: str,
                      realized_ids: set[str] | None = None,
                      resolved_edge_ids: set[str] | None = None) -> list[dict]:
    """Mark pending guidance suggestions moot by later events: a bridge
    suggestion whose concept the author has since written (realized), a
    prerequisite suggestion whose gap has since closed. Deterministic —
    keyed on the stored dedupe_key — and run at collect, so the review
    surface never offers a no-op."""
    realized_ids = realized_ids or set()
    resolved_edge_ids = resolved_edge_ids or set()
    if not realized_ids and not resolved_edge_ids:
        return []
    staled = []
    for row in db.all(
        "SELECT * FROM guidance_history WHERE manuscript_id = ? "
        "AND state = 'proposed' AND kind IN ('bridge', 'prerequisite')",
        (mid,),
    ):
        key = loads(row["metadata"], {}).get("dedupe_key", "")
        kind, _, ident = key.partition(":")
        moot = ((kind == "bridge" and ident in realized_ids)
                or (kind == "prerequisite" and ident in resolved_edge_ids))
        if moot:
            db.update("guidance_history", row["id"], {"state": "stale"})
            staled.append({"kind": row["kind"],
                           "suggestion": row["suggestion"]})
    return staled


def sweep(db: Database, manuscript: dict, files: dict[str, str]) -> dict:
    """The retroactive hygiene pass over the existing piles. Also marks
    currently-moot pending suggestions stale (bridge suggestions whose
    concept is realized by now)."""
    mid = manuscript["id"]
    realized_now = {
        n["id"] for n in db.all(
            "SELECT id FROM concept_nodes WHERE manuscript_id = ? "
            "AND status = 'realized'", (mid,),
        )
    }
    ungrounded = ungrounded_concepts(db, mid, files)
    ungrounded_names = {c["name"] for c in ungrounded}
    below_bar = below_recurrence_bar(db, mid, files, ungrounded_names)
    return {
        "ungrounded_concepts": ungrounded,
        "ungrounded_edges": ungrounded_edges(db, mid, files),
        "below_bar": below_bar,
        "suggestions_stale": stale_suggestions(db, mid,
                                               realized_ids=realized_now),
    }


def below_recurrence_bar(db: Database, mid: str, files: dict[str, str],
                         ungrounded_names: set[str] | None = None) -> list[dict]:
    """Unconfirmed recurring-kind concepts that fail the ratified bar.

    Ungrounded concepts are excluded because the stronger zero-context rule
    already accounts for them.
    """
    ungrounded_names = ungrounded_names or {
        row["name"] for row in ungrounded_concepts(db, mid, files)
    }
    below_bar = []
    for node in db.all(
        "SELECT * FROM concept_nodes WHERE manuscript_id = ? "
        "AND status != 'retired'", (mid,),
    ):
        meta = loads(node["metadata"], {})
        if (meta.get("origin") != "extracted" or meta.get("confirmed")
                or node["kind"] not in RECURRENCE_GATED_KINDS
                or node["name"] in ungrounded_names):  # 0-context: listed above
            continue
        admitted, stats = passes_recurrence_bar(files, node_names(node))
        if not admitted:
            below_bar.append({"id": node["id"], "name": node["name"],
                              "kind": node["kind"],
                              **stats})
    return below_bar


# --------------------------------------------- recurrence bar (admission)

# Ratified 2026-08-08 (author, verbatim): "Concepts are words or groups
# of words that are used multiple times. A phrase is something that
# might occur once or twice, but isn't a continuing motif." The bar
# gates machine-extracted hypotheses of these kinds only; declarations
# by the author bypass it (declaration is ratification), and coined-name
# kinds (question/objection) plus one-shot-by-nature kinds
# (historical_reference, mathematical_construct, syllogism) are exempt.
RECURRENCE_GATED_KINDS = {"concept", "metaphor", "example"}

_PARA_SPLIT = re.compile(r"\n\s*\n")
_HEADING_LINE = re.compile(r"^#{1,6}\s")


def recurrence_stats(files: dict[str, str], names: list[str]) -> dict:
    """Distinct-context counts for a term across the manuscript:
    occurrences within one paragraph collapse to a single context, each
    context attributed to its file and containing section (heading)."""
    patterns = [concept_pattern(n) for n in names if n]
    contexts = 0
    sections: set[tuple[str, str]] = set()
    files_hit: set[str] = set()
    for fname, text in files.items():
        section = ""
        for para in _PARA_SPLIT.split(text):
            para = para.strip()
            if not para:
                continue
            if _HEADING_LINE.match(para):
                section = para.splitlines()[0]
            if any(p.search(para) for p in patterns):
                contexts += 1
                sections.add((fname, section))
                files_hit.add(fname)
    return {"contexts": contexts, "sections": len(sections),
            "files": len(files_hit)}


def passes_recurrence_bar(files: dict[str, str],
                          names: list[str]) -> tuple[bool, dict]:
    """The ratified bar: ≥2 paragraph-contexts spanning ≥2 sections or
    ≥2 files. Twice in one paragraph fails; an essay-local structure
    named in several of its sections passes."""
    stats = recurrence_stats(files, names)
    ok = (stats["contexts"] >= 2
          and (stats["files"] >= 2 or stats["sections"] >= 2))
    return ok, stats
