"""The Concept Graph (RFC AuthorLM Chapter 21) — AuthorLM's enduring asset.

Declared concepts and relationships are authoritative observations. When a
declared concept first appears in manuscript text it becomes *realized*.
*Inferred* edges are the extractor's hypotheses and remain hypotheses until
the author settles them in review or triage (§21.7).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .db import Database, ko_fields, loads


NODE_KINDS = frozenset({
    "concept", "objection", "example", "metaphor", "question",
    "historical_reference", "mathematical_construct", "syllogism",
})
VALID_RELATIONS = {
    "depends_on", "motivates", "contrasts_with", "elaborates", "generalizes",
    "specializes", "answers", "foreshadows", "illustrates", "permits",
    "creates", "distinguishes", "defines", "leads_to", "refutes",
}


def add_concept(
    db: Database, manuscript_id: str, name: str, kind: str = "concept",
    notes: str | None = None, source_id: str | None = None,
) -> dict:
    if kind not in NODE_KINDS:
        raise ValueError(f"invalid concept kind '{kind}'")
    existing = get_concept(db, manuscript_id, name)
    if existing:
        updates = {}
        if existing["status"] == "retired":
            # Re-adding a retired concept revives it as a fresh declaration.
            updates.update(status="declared", introduced_in=None)
        if notes is not None and notes != existing["notes"]:
            updates["notes"] = notes
        if updates:
            db.update("concept_nodes", existing["id"], updates)
        return {**dict(existing), **updates}
    row = ko_fields("cn")
    row.update(
        manuscript_id=manuscript_id, name=name, kind=kind,
        status="declared", introduced_in=None, notes=notes,
        source_id=source_id or db.source("author"),
    )
    db.insert("concept_nodes", row)
    return row


def get_concept(db: Database, manuscript_id: str, name: str):
    """Look a concept up by name or by any of its aliases. A live node
    always wins; a retired exact-name match is returned only when no live
    node answers to the name (so revive-by-add and double-retire checks
    still see it)."""
    exact = db.one(
        "SELECT * FROM concept_nodes WHERE manuscript_id = ? AND lower(name) = lower(?) "
        "ORDER BY CASE WHEN status = 'retired' THEN 1 ELSE 0 END, created_at DESC "
        "LIMIT 1",
        (manuscript_id, name),
    )
    if exact and exact["status"] != "retired":
        return exact
    lowered = name.lower()
    for row in db.all(
        "SELECT * FROM concept_nodes WHERE manuscript_id = ? "
        "AND status != 'retired' AND aliases != '[]'",
        (manuscript_id,),
    ):
        if any(a.lower() == lowered for a in loads(row["aliases"], [])):
            return row
    return exact


def node_aliases(node) -> list[str]:
    try:
        raw = node["aliases"]
    except (KeyError, IndexError):
        return []
    return loads(raw, []) if raw else []


def node_names(node) -> list[str]:
    """Every name that counts as this concept: primary name plus aliases."""
    return [node["name"], *node_aliases(node)]


def add_alias(db: Database, manuscript_id: str, node: dict, alias: str) -> dict:
    """Declare an alternate name for a concept. Aliases resolve in lookups
    and count as mentions in text scans. An alias may not collide with the
    name or alias of a different live concept."""
    other = get_concept(db, manuscript_id, alias)
    if other and other["id"] != node["id"] and other["status"] != "retired":
        raise ValueError(f"'{alias}' already names concept '{other['name']}'")
    aliases = node_aliases(node)
    if alias.lower() == node["name"].lower() or \
            any(a.lower() == alias.lower() for a in aliases):
        return dict(node)
    aliases.append(alias)
    payload = json.dumps(aliases)
    db.update("concept_nodes", node["id"], {"aliases": payload})
    return {**dict(node), "aliases": payload}


def remove_alias(db: Database, manuscript_id: str, node: dict,
                 alias: str) -> dict:
    """Withdraw an alias: the name stops resolving in lookups and stops
    counting as a mention in every text scan."""
    aliases = node_aliases(node)
    kept = [a for a in aliases if a.lower() != alias.lower()]
    if len(kept) == len(aliases):
        raise ValueError(f"'{node['name']}' has no alias '{alias}'")
    payload = json.dumps(kept)
    db.update("concept_nodes", node["id"], {"aliases": payload})
    return {**dict(node), "aliases": payload}


def merge_concepts(db: Database, manuscript_id: str,
                   canonical: dict, duplicate: dict) -> dict:
    """Absorb `duplicate` into `canonical`: its live edges are re-pointed at
    the canonical (self-edges and duplicates of edges the canonical already
    has retire instead), the emptied node retires, and its name and aliases
    become aliases of the canonical."""
    if canonical["id"] == duplicate["id"]:
        raise ValueError("cannot merge a concept into itself")
    repointed = dropped = 0
    for edge in db.all(
        "SELECT * FROM concept_edges WHERE manuscript_id = ? "
        "AND (from_node = ? OR to_node = ?) AND status NOT IN ('rejected', 'retired')",
        (manuscript_id, duplicate["id"], duplicate["id"]),
    ):
        new_from = canonical["id"] if edge["from_node"] == duplicate["id"] else edge["from_node"]
        new_to = canonical["id"] if edge["to_node"] == duplicate["id"] else edge["to_node"]
        existing = db.one(
            "SELECT * FROM concept_edges WHERE manuscript_id = ? AND from_node = ? "
            "AND to_node = ? AND relation = ? AND id != ?",
            (manuscript_id, new_from, new_to, edge["relation"], edge["id"]),
        )
        if new_from == new_to or existing:
            db.update("concept_edges", edge["id"], {"status": "retired"})
            dropped += 1
        else:
            db.update("concept_edges", edge["id"],
                      {"from_node": new_from, "to_node": new_to})
            repointed += 1
    db.update("concept_nodes", duplicate["id"], {"status": "retired"})
    node = dict(canonical)
    for alias in node_names(duplicate):
        node = add_alias(db, manuscript_id, node, alias)
    return {"canonical": node["name"], "aliases": node_aliases(node),
            "repointed": repointed, "dropped": dropped}


def retire_concept(db: Database, manuscript_id: str, node: dict) -> int:
    """Retire a concept and every edge touching it. Nothing is deleted —
    retired knowledge stays historically accessible (Common Core §8.10).
    Returns the number of edges retired."""
    from .db import now_iso

    meta = loads(node.get("metadata"), {}) or {}
    # `at`: when — the text written after it is what a revival must recur
    # in (docs/extraction-redesign.md §4.10).
    meta["retired_from"] = {"status": node.get("status"),
                            "introduced_in": node.get("introduced_in"),
                            "at": now_iso()}
    db.update("concept_nodes", node["id"],
              {"status": "retired", "metadata": json.dumps(meta)})
    edges = db.all(
        "SELECT * FROM concept_edges WHERE manuscript_id = ? "
        "AND (from_node = ? OR to_node = ?) AND status NOT IN ('rejected', 'retired')",
        (manuscript_id, node["id"], node["id"]),
    )
    for edge in edges:
        # Remember what the edge was, and that THIS retirement did it, so
        # a revive restores exactly and only its own collateral.
        emeta = loads(edge["metadata"], {}) or {}
        emeta["retired_from"] = {"status": edge["status"],
                                 "by_node": node["id"]}
        db.update("concept_edges", edge["id"],
                  {"status": "retired", "metadata": json.dumps(emeta)})
    return len(edges)


def revive_concept(db: Database, manuscript_id: str, node: dict) -> dict:
    """The inverse of retire_concept: restore the node's prior status and
    location, and un-retire the edges that THIS node's retirement took
    down (edges retired for other reasons stay retired). Returns
    {status, introduced_in, edges_revived}."""
    meta = loads(node.get("metadata"), {}) or {}
    prior = meta.pop("retired_from", None) or {}
    meta.pop("retired_for_good", None)  # the author's own revive reopens it
    status = prior.get("status") or "declared"
    introduced_in = prior.get("introduced_in") or node.get("introduced_in")
    if status == "retired":
        status = "declared"
    db.update("concept_nodes", node["id"],
              {"status": status, "introduced_in": introduced_in,
               "metadata": json.dumps(meta)})
    revived = 0
    for edge in db.all(
            "SELECT * FROM concept_edges WHERE manuscript_id = ? "
            "AND (from_node = ? OR to_node = ?) AND status = 'retired'",
            (manuscript_id, node["id"], node["id"])):
        emeta = loads(edge["metadata"], {}) or {}
        prior_e = emeta.get("retired_from")
        if not prior_e or prior_e.get("by_node") != node["id"]:
            continue
        other = edge["to_node"] if edge["from_node"] == node["id"] else edge["from_node"]
        other_row = db.one("SELECT status FROM concept_nodes WHERE id = ?", (other,))
        if not other_row or other_row["status"] == "retired":
            continue  # the far end is gone; the edge stays down
        emeta.pop("retired_from", None)
        db.update("concept_edges", edge["id"],
                  {"status": prior_e.get("status") or "inferred",
                   "metadata": json.dumps(emeta)})
        revived += 1
    return {"status": status, "introduced_in": introduced_in,
            "edges_revived": revived}


def link_concepts(
    db: Database, manuscript_id: str, from_name: str, relation: str, to_name: str,
    status: str = "declared", source_id: str | None = None,
) -> dict:
    src = add_concept(db, manuscript_id, from_name, source_id=source_id)
    dst = add_concept(db, manuscript_id, to_name, source_id=source_id)
    existing = db.one(
        "SELECT * FROM concept_edges WHERE manuscript_id = ? AND from_node = ? "
        "AND to_node = ? AND relation = ?",
        (manuscript_id, src["id"], dst["id"], relation),
    )
    if existing:
        return dict(existing)
    row = ko_fields("ce")
    row.update(
        manuscript_id=manuscript_id, from_node=src["id"], relation=relation,
        to_node=dst["id"], status=status, support=0, evidence="[]",
        source_id=source_id or db.source("author"),
    )
    db.insert("concept_edges", row)
    return row


# ASCII, curly, and modifier letter apostrophes: Docs round-trips and LLM
# extraction disagree on the mark. A name stored with one form must still
# match text that uses another (2026-10-28: extract migrate's absent gate
# retired confirmed concepts whose only "absence" was ' vs ’).
_APOSTROPHE = "['\u2018\u2019\u02bc]"


def _escape_name(name: str) -> str:
    """`re.escape`, but any apostrophe in the name matches any apostrophe."""
    folded = (name.replace("\u2018", "'").replace("\u2019", "'")
              .replace("\u02bc", "'"))
    return _APOSTROPHE.join(re.escape(part) for part in folded.split("'"))


def concept_pattern(name: str) -> re.Pattern:
    """Word-boundary match tolerant of simple English plurals
    (trajectory/trajectories, field/fields, class/classes) and of ASCII
    vs curly apostrophes."""
    if name and name[-1].lower() == "y":
        stem = _escape_name(name[:-1])
        return re.compile(rf"\b{stem}(?:y|ies)\b", re.IGNORECASE)
    return re.compile(rf"\b{_escape_name(name)}(?:e?s)?\b", re.IGNORECASE)


def mention_pattern(name: str) -> re.Pattern:
    """Where a concept appears *as a term of art* — for ordering-sensitive
    checks (first mentions, prerequisite gaps). Single-word names declared
    with a capital keep it: the manuscripts capitalize their terms of art
    ('ye name it Space'), so casual English reuse of the same word ('time
    and space') must not count as the concept. Multi-word names collide
    with casual prose far less, and keep the tolerant match — which
    concept_pattern remains for existence checks (realization), where any
    casing of 'the herdsman' is genuinely the concept."""
    if " " not in name.strip() and name[:1].isupper():
        if name[-1].lower() == "y":
            stem = _escape_name(name[:-1])
            return re.compile(rf"\b{stem}(?:y|ies)\b")
        return re.compile(rf"\b{_escape_name(name)}(?:e?s)?\b")
    return concept_pattern(name)


_word_pattern = concept_pattern


def primary_location(items: list[tuple[str, str]], node) -> str | None:
    """THE rule for where a concept was introduced, deterministic and
    author-free (ruled 2026-09-07, the author's words: "the first mention
    of the term"): the first file in READING ORDER whose text carries the
    concept's name or an alias as a word, any casing, plurals tolerated
    (`concept_pattern`). Nothing finer: preferring the capitalized
    term-of-art mention pulled Measurement and Trajectory forward into
    the Metaphysic, where the book's essays had long used the word, and
    that is the misattribution the ruling exists to end. None when no
    file carries it. Every writer of `introduced_in` goes through here
    and every collect re-derives it; the author never triages it."""
    names = (list(node) if isinstance(node, (list, tuple, set))
             else node_names(dict(node)))
    if not names:
        return None
    pats = [concept_pattern(n) for n in names]
    for fname, text in items:
        if any(p.search(text) for p in pats):
            return fname
    return None


def scan_realizations(db: Database, manuscript_id: str, version: dict) -> list[dict]:
    """Mark declared concepts as realized when they appear in text.
    Returns newly realized concept rows."""
    from .structure import ordered_items

    files: dict[str, str] = loads(version["files"], {})
    # Reading order (TOC-authoritative): the first location in reading order
    # is the concept's primary location — definition precedence. Invariant
    # across nodes, so it's computed once rather than per declared concept.
    items = ordered_items(files)
    realized = []
    for node in db.all(
        "SELECT * FROM concept_nodes WHERE manuscript_id = ? AND status = 'declared'",
        (manuscript_id,),
    ):
        fname = primary_location(items, node)
        if fname is None:
            continue
        meta = loads(node["metadata"], {})
        meta.update(realized_at=version["created_at"], realized_version=version["id"])
        db.update(
            "concept_nodes", node["id"],
            {
                "status": "realized",
                "introduced_in": fname,
                "metadata": json.dumps(meta),
            },
        )
        realized.append({**dict(node), "status": "realized", "introduced_in": fname})
    return realized


def rescan_primary_locations(db: Database, manuscript_id: str,
                             version: dict) -> tuple[list[dict], list[dict]]:
    """Re-validate realized concepts against the current text.

    Returns (repointed, vanished): `repointed` are concepts whose primary
    location moved because their introducing text no longer contains them —
    introduced_in is re-pointed to the first reading-order location that
    does (definition precedence follows the text). `vanished` are realized
    concepts that no longer appear anywhere; the caller surfaces those for
    an author decision — never silently demoted."""
    from .structure import ordered_items

    files: dict[str, str] = loads(version["files"], {})
    items = ordered_items(files)
    repointed, vanished = [], []
    for node in db.all(
        "SELECT * FROM concept_nodes WHERE manuscript_id = ? AND status = 'realized'",
        (manuscript_id,),
    ):
        current_first = primary_location(items, node)
        if current_first is None:
            vanished.append(dict(node))
            continue
        old = node["introduced_in"]
        # Definition precedence follows the text, unconditionally (ruled
        # 2026-09-07 after commensurability, Caste and hierarchy stayed
        # pinned to later chapters): the pointer IS the first reading-order
        # mention, re-derived at every collect, never triaged.
        if current_first != old:
            db.update("concept_nodes", node["id"], {"introduced_in": current_first})
            repointed.append({"name": node["name"], "old": old, "new": current_first})
    return repointed, vanished


def node_name(db: Database, node_id: str) -> str:
    row = db.one("SELECT name FROM concept_nodes WHERE id = ?", (node_id,))
    return row["name"] if row else node_id


def unrealized_with_dependents(db: Database, manuscript_id: str) -> list[dict]:
    """Declared-but-unrealized concepts, with the concepts that depend on
    them — prime focus-area candidates (§21.6, §20.3)."""
    results = []
    for node in db.all(
        "SELECT * FROM concept_nodes WHERE manuscript_id = ? AND status = 'declared'",
        (manuscript_id,),
    ):
        dependents = db.all(
            "SELECT ce.relation, cn.name FROM concept_edges ce "
            "JOIN concept_nodes cn ON cn.id = ce.from_node "
            "WHERE ce.manuscript_id = ? AND ce.to_node = ? AND ce.relation != 'co_occurs' "
            "AND ce.status NOT IN ('rejected', 'retired') AND cn.status != 'retired'",
            (manuscript_id, node["id"]),
        )
        supporters = db.all(
            "SELECT ce.relation, cn.name FROM concept_edges ce "
            "JOIN concept_nodes cn ON cn.id = ce.to_node "
            "WHERE ce.manuscript_id = ? AND ce.from_node = ? AND ce.relation != 'co_occurs' "
            "AND ce.status NOT IN ('rejected', 'retired') AND cn.status != 'retired'",
            (manuscript_id, node["id"]),
        )
        results.append(
            {
                "node": dict(node),
                "related": [dict(d) for d in dependents] + [dict(s) for s in supporters],
            }
        )
    return results


def show_concept(db: Database, manuscript: dict, name: str) -> dict:
    node = get_concept(db, manuscript["id"], name)
    if not node:
        raise LookupError(f"no concept named '{name}'")
    edges = [dict(e) for e in db.all(
        "SELECT ce.*, fn.name AS from_name, tn.name AS to_name "
        "FROM concept_edges ce "
        "JOIN concept_nodes fn ON fn.id = ce.from_node "
        "JOIN concept_nodes tn ON tn.id = ce.to_node "
        "WHERE ce.manuscript_id = ? "
        "AND ce.status NOT IN ('rejected', 'retired') "
        "AND (ce.from_node = ? OR ce.to_node = ?)",
        (manuscript["id"], node["id"], node["id"]),
    )]
    return {"node": dict(node), "edges": edges}


# --- compact projections and the scoped slice

_NOTE_PREVIEW = 160


def _preview(text: str | None) -> str | None:
    if not text:
        return None
    return text if len(text) <= _NOTE_PREVIEW else text[:_NOTE_PREVIEW - 1] + "…"


def _compact_concept(row: dict) -> dict:
    out = {"name": row["name"], "kind": row["kind"], "status": row["status"]}
    if row.get("introduced_in"):
        out["introduced_in"] = row["introduced_in"]
    notes = _preview(row.get("notes"))
    if notes:
        out["notes"] = notes
    aliases = loads(row.get("aliases"), [])
    if aliases:
        out["aliases"] = aliases
    return out


def _compact_edge(edge: dict) -> dict:
    return {"id": edge["id"],
            "edge": f"{edge['from_name']} —{edge['relation']}→ {edge['to_name']}",
            "status": edge["status"]}


def compact_concepts(result: dict) -> dict:
    """Compact projection of the whole-graph listing."""
    return {
        "node_count": len(result["nodes"]),
        "nodes": [_compact_concept(dict(n)) for n in result["nodes"]],
        "edge_count": len(result["edges"]),
        "edges": [_compact_edge(e) for e in result["edges"]],
    }


def scoped_concepts(db: Database, manuscript: dict,
                    file: str | None = None,
                    query: str | None = None,
                    text: str | None = None) -> dict:
    """The relevant slice of the graph, compact. `query` matches name,
    notes, or aliases (case-insensitive substring). `file` selects
    concepts realized in that essay: introduced there, or whose name or
    alias appears in its text. Edges are restricted to the selected
    nodes.

    `text` is the CAPTURE OVERRIDE, and it closes a pre-existing
    one-capture violation rather than adding a feature. With `file` and
    no `text` this re-reads the disk through `read_manuscript_files` —
    a SECOND read inside a verb that already took exactly one capture.
    `concept_notes` did precisely that, so `filter run`'s
    CONCEPT NOTES could differ between window N and window N+1 if a
    parallel session edited the file, silently forfeiting the cached
    prefix that block A exists to hold. Supplying the caller's own
    captured text replaces the disk read for the file-scoping match, and
    the file is still RESOLVED against the manuscript so an unknown name
    is still a LookupError. Defaults to today's behaviour."""
    mid = manuscript["id"]
    nodes = [dict(n) for n in db.all(
        "SELECT * FROM concept_nodes WHERE manuscript_id = ? "
        "AND status != 'retired'", (mid,))]
    if query:
        q = query.lower()
        nodes = [n for n in nodes
                 if q in n["name"].lower()
                 or q in (n.get("notes") or "").lower()
                 or any(q in a.lower()
                        for a in loads(n.get("aliases"), []))]
    if file:
        from .revisions import read_manuscript_files
        files = read_manuscript_files(Path(manuscript["path"]))
        rel = next((r for r in files
                    if r == file or r.endswith("/" + file)
                    or Path(r).name == file), None)
        if rel is None:
            raise LookupError(f"no manuscript file matching '{file}'")
        haystack = (files[rel] if text is None else text).lower()
        nodes = [n for n in nodes
                 if n.get("introduced_in") == rel
                 or n["name"].lower() in haystack
                 or any(a.lower() in haystack
                        for a in loads(n.get("aliases"), []))]
    ids = {n["id"] for n in nodes}
    edges = [
        {**dict(e),
         "from_name": node_name(db, e["from_node"]),
         "to_name": node_name(db, e["to_node"])}
        for e in db.all(
            "SELECT * FROM concept_edges WHERE manuscript_id = ? "
            "AND status NOT IN ('rejected', 'retired')", (mid,))
        if e["from_node"] in ids and e["to_node"] in ids]
    return compact_concepts({"nodes": nodes, "edges": edges})


def concept_notes(db: Database, manuscript: dict, file: str,
                  text: str | None = None) -> str:
    """The CONCEPT NOTES block for one essay: its slice of the graph, empty
    when the file is not in the manuscript.

    `text` is the caller's captured essay text, which is what makes this
    block obey the one-capture rule: without it a parallel session's edit
    between window N and window N+1 changes block A and forfeits the cached
    prefix."""
    try:
        graph = scoped_concepts(db, manuscript, file=file, text=text)
    except LookupError:
        return ""
    lines = []
    for node in sorted(graph.get("nodes", []),
                       key=lambda n: n.get("name") or ""):
        notes = (node.get("notes") or "").strip() or "(no notes)"
        # ALIASES ride with the definition, not only with PROTECTED
        # TERMS. A concept reached by an alternate name is the same
        # concept, and a filter that cannot see the alias will read two
        # names for one thing as two things.
        aliases = [a for a in (node.get("aliases") or []) if a]
        also = f" (also: {', '.join(sorted(aliases))})" if aliases else ""
        lines.append(f"- {node['name']}{also} — {notes}")
    # EDGES: the graph's own relations between the concepts this essay
    # touches. `scoped_concepts` already restricts them to the selected
    # nodes, so nothing here reaches outside the slice.
    edges = [e.get("edge") for e in graph.get("edges", []) if e.get("edge")]
    if edges:
        lines.append("")
        lines.append("Relations the author has recorded between them:")
        lines.extend(f"- {e}" for e in sorted(edges))
    return "\n".join(lines)
