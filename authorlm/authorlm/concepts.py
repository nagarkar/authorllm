"""The Concept Graph (RFC AuthorLM Chapter 21) — AuthorLM's enduring asset.

Declared concepts and relationships are authoritative observations. When a
declared concept first appears in manuscript text it becomes *realized*.
Repeated co-occurrence of two concepts in the same paragraph produces an
*inferred* edge, which remains a hypothesis until reinforced (§21.7).
"""

from __future__ import annotations

import json
import re

from .db import Database, ko_fields, loads

# An inferred co-occurrence edge needs this much support to be reported.
CO_OCCURRENCE_THRESHOLD = 2


def add_concept(
    db: Database, manuscript_id: str, name: str, kind: str = "concept",
    notes: str | None = None,
) -> dict:
    existing = get_concept(db, manuscript_id, name)
    if existing:
        if existing["status"] == "retired":
            # Re-adding a retired concept revives it as a fresh declaration.
            db.update(
                "concept_nodes", existing["id"],
                {"status": "declared", "introduced_in": None},
            )
            return {**dict(existing), "status": "declared", "introduced_in": None}
        return dict(existing)
    row = ko_fields("cn")
    row.update(
        manuscript_id=manuscript_id, name=name, kind=kind,
        status="declared", introduced_in=None, notes=notes,
    )
    db.insert("concept_nodes", row)
    return row


def get_concept(db: Database, manuscript_id: str, name: str):
    return db.one(
        "SELECT * FROM concept_nodes WHERE manuscript_id = ? AND lower(name) = lower(?)",
        (manuscript_id, name),
    )


def retire_concept(db: Database, manuscript_id: str, node: dict) -> int:
    """Retire a concept and every edge touching it. Nothing is deleted —
    retired knowledge stays historically accessible (Common Core §8.10).
    Returns the number of edges retired."""
    db.update("concept_nodes", node["id"], {"status": "retired"})
    edges = db.all(
        "SELECT * FROM concept_edges WHERE manuscript_id = ? "
        "AND (from_node = ? OR to_node = ?) AND status NOT IN ('rejected', 'retired')",
        (manuscript_id, node["id"], node["id"]),
    )
    for edge in edges:
        db.update("concept_edges", edge["id"], {"status": "retired"})
    return len(edges)


def link_concepts(
    db: Database, manuscript_id: str, from_name: str, relation: str, to_name: str,
    status: str = "declared",
) -> dict:
    src = add_concept(db, manuscript_id, from_name)
    dst = add_concept(db, manuscript_id, to_name)
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
    )
    db.insert("concept_edges", row)
    return row


def concept_pattern(name: str) -> re.Pattern:
    """Word-boundary match tolerant of simple English plurals
    (trajectory/trajectories, field/fields, class/classes)."""
    if name and name[-1].lower() == "y":
        stem = re.escape(name[:-1])
        return re.compile(rf"\b{stem}(?:y|ies)\b", re.IGNORECASE)
    return re.compile(rf"\b{re.escape(name)}(?:e?s)?\b", re.IGNORECASE)


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
            stem = re.escape(name[:-1])
            return re.compile(rf"\b{stem}(?:y|ies)\b")
        return re.compile(rf"\b{re.escape(name)}(?:e?s)?\b")
    return concept_pattern(name)


_word_pattern = concept_pattern


def scan_realizations(db: Database, manuscript_id: str, version: dict) -> list[dict]:
    """Mark declared concepts as realized when they appear in text.
    Returns newly realized concept rows."""
    from .structure import ordered_items

    files: dict[str, str] = loads(version["files"], {})
    realized = []
    for node in db.all(
        "SELECT * FROM concept_nodes WHERE manuscript_id = ? AND status = 'declared'",
        (manuscript_id,),
    ):
        pattern = _word_pattern(node["name"])
        # Reading order (TOC-authoritative): the first location in reading
        # order is the concept's primary location — definition precedence.
        for fname, text in ordered_items(files):
            if pattern.search(text):
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
                break
    return realized


def scan_co_occurrences(db: Database, manuscript_id: str, version: dict) -> list[dict]:
    """Count paragraph-level co-occurrence of known concepts; create or
    reinforce inferred edges. Returns edges that newly crossed the
    reporting threshold."""
    from .structure import content_files

    files: dict[str, str] = content_files(loads(version["files"], {}))
    nodes = db.all(
        "SELECT * FROM concept_nodes WHERE manuscript_id = ? AND status != 'retired'",
        (manuscript_id,),
    )
    if len(nodes) < 2:
        return []
    patterns = {n["id"]: _word_pattern(n["name"]) for n in nodes}
    counts: dict[tuple[str, str], int] = {}
    for text in files.values():
        for para in re.split(r"\n\s*\n", text):
            present = [n["id"] for n in nodes if patterns[n["id"]].search(para)]
            for i, a in enumerate(present):
                for b in present[i + 1:]:
                    key = (min(a, b), max(a, b))
                    counts[key] = counts.get(key, 0) + 1

    newly_significant = []
    for (a, b), count in counts.items():
        if count < CO_OCCURRENCE_THRESHOLD:
            continue
        declared = db.one(
            "SELECT * FROM concept_edges WHERE manuscript_id = ? AND relation != 'co_occurs' "
            "AND status NOT IN ('rejected', 'retired') "
            "AND ((from_node = ? AND to_node = ?) OR (from_node = ? AND to_node = ?))",
            (manuscript_id, a, b, b, a),
        )
        if declared:
            continue  # an explicit relationship already covers this pair
        edge = db.one(
            "SELECT * FROM concept_edges WHERE manuscript_id = ? AND relation = 'co_occurs' "
            "AND from_node = ? AND to_node = ?",
            (manuscript_id, a, b),
        )
        if edge:
            if edge["support"] != count:
                db.update("concept_edges", edge["id"], {"support": count})
            continue
        row = ko_fields("ce")
        row.update(
            manuscript_id=manuscript_id, from_node=a, relation="co_occurs",
            to_node=b, status="inferred", support=count,
            evidence=json.dumps([version["id"]]),
        )
        db.insert("concept_edges", row)
        newly_significant.append(row)
    return newly_significant


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
        pattern = _word_pattern(node["name"])
        current_first = next(
            (name for name, text in items if pattern.search(text)), None
        )
        if current_first is None:
            vanished.append(dict(node))
            continue
        old = node["introduced_in"]
        old_text = files.get(old, "") if old else ""
        if current_first != old and not pattern.search(old_text):
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
