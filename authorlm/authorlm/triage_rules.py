"""Conservative, zero-token rules for bulk graph triage.

This module decides which pending rows are mechanically safe to settle. It
does not mutate the graph; all changes go through :mod:`authorlm.triage`.
"""

from __future__ import annotations

from typing import Any

from . import hygiene
from .concepts import node_aliases
from .db import Database, loads

RULES: dict[str, dict[str, str]] = {
    "concept_known_alias": {
        "triage_type": "concepts",
        "action": "alias",
        "label": "already registered as an alias",
    },
    "concept_duplicate_name": {
        "triage_type": "concepts",
        "action": "alias",
        "label": "duplicates a resolved concept name",
    },
    "concept_unmentioned": {
        "triage_type": "concepts",
        "action": "retire",
        "label": "absent from the manuscript",
    },
    "concept_below_recurrence_bar": {
        "triage_type": "concepts",
        "action": "retire",
        "label": "below the recurrence bar",
    },
    "edge_retired_endpoint": {
        "triage_type": "edges",
        "action": "reject",
        "label": "references a retired concept",
    },
    "edge_self_reference": {
        "triage_type": "edges",
        "action": "reject",
        "label": "self-referential relationship",
    },
    "edge_duplicate_settled": {
        "triage_type": "edges",
        "action": "reject",
        "label": "duplicates a resolved relationship",
    },
    "edge_unmentioned_endpoint": {
        "triage_type": "edges",
        "action": "reject",
        "label": "has an endpoint absent from the manuscript",
    },
}


def _pending_concept(node: dict) -> bool:
    meta = loads(node.get("metadata"), {})
    return (meta.get("origin") == "extracted" and not meta.get("confirmed")
            and node["status"] != "retired")


def _decision(rule: str, row: dict, label: str, reason: str,
              parameters: dict[str, Any] | None = None) -> dict[str, Any]:
    spec = RULES[rule]
    return {
        "triage_type": spec["triage_type"],
        "object_id": row["id"],
        "object_version": row["version"],
        "object_label": label,
        "action": spec["action"],
        "parameters": parameters or {},
        "rule": rule,
        "rule_label": spec["label"],
        "reason": reason,
    }


def _concept_decisions(db: Database, manuscript: dict,
                       files: dict[str, str]) -> tuple[list[dict], list[dict]]:
    mid = manuscript["id"]
    nodes = [dict(row) for row in db.all(
        "SELECT * FROM concept_nodes WHERE manuscript_id = ?", (mid,))]
    live = [node for node in nodes if node["status"] != "retired"]
    pending = [node for node in live if _pending_concept(node)]
    pending_ids = {node["id"] for node in pending}
    settled = [node for node in live if node["id"] not in pending_ids]
    settled_names = {node["name"].casefold(): node for node in settled}
    alias_owners = {
        alias.casefold(): node
        for node in live
        for alias in node_aliases(node)
    }
    drafts = {row["object_id"] for row in db.all(
        "SELECT object_id FROM triage_drafts WHERE manuscript_id = ? "
        "AND triage_type = 'concepts'", (mid,))}
    proposal_targets = {row["target"] for row in db.all(
        "SELECT target FROM knowledge_proposals WHERE manuscript_id = ? "
        "AND state = 'open'", (mid,))}
    settled_edge_nodes = {
        ident
        for row in db.all(
            "SELECT from_node, to_node FROM concept_edges WHERE manuscript_id = ? "
            "AND status NOT IN ('inferred', 'rejected', 'retired')", (mid,))
        for ident in (row["from_node"], row["to_node"])
    }
    ungrounded = {row["id"]: row for row in
                  hygiene.ungrounded_concepts(db, mid, files)}
    below_bar = {row["id"]: row for row in
                 hygiene.below_recurrence_bar(
                     db, mid, files, {row["name"] for row in ungrounded.values()})}

    decisions = []
    protected = []
    for node in sorted(pending, key=lambda row: row["name"].casefold()):
        if node["id"] in drafts:
            protected.append({"triage_type": "concepts", "object_id": node["id"],
                              "object_label": node["name"],
                              "reason": "an author decision is already staged"})
            continue
        canonical = alias_owners.get(node["name"].casefold())
        if canonical and canonical["id"] != node["id"]:
            decisions.append(_decision(
                "concept_known_alias", node, node["name"],
                f"'{node['name']}' is already an alias of '{canonical['name']}'",
                {"canonical_id": canonical["id"]}))
            continue
        canonical = settled_names.get(node["name"].casefold())
        if canonical and canonical["id"] != node["id"]:
            decisions.append(_decision(
                "concept_duplicate_name", node, node["name"],
                f"a resolved concept already has the name '{canonical['name']}'",
                {"canonical_id": canonical["id"]}))
            continue
        finding = ungrounded.get(node["id"])
        rule = "concept_unmentioned"
        reason = "its name and aliases do not occur in the current manuscript"
        if not finding and node["id"] in below_bar:
            finding = below_bar[node["id"]]
            rule = "concept_below_recurrence_bar"
            reason = (f"{finding['contexts']} context(s) across "
                      f"{finding['sections']} section(s) and "
                      f"{finding['files']} file(s)")
        if not finding:
            continue
        if node["id"] in proposal_targets:
            protected.append({"triage_type": "concepts", "object_id": node["id"],
                              "object_label": node["name"],
                              "reason": "an open knowledge proposal references it"})
            continue
        if node["id"] in settled_edge_nodes:
            protected.append({"triage_type": "concepts", "object_id": node["id"],
                              "object_label": node["name"],
                              "reason": "an author-settled relationship references it"})
            continue
        decisions.append(_decision(rule, node, node["name"], reason))
    return decisions, protected


def _edge_decisions(db: Database, manuscript: dict,
                    files: dict[str, str]) -> tuple[list[dict], list[dict]]:
    mid = manuscript["id"]
    nodes = {row["id"]: dict(row) for row in db.all(
        "SELECT * FROM concept_nodes WHERE manuscript_id = ?", (mid,))}
    edges = [dict(row) for row in db.all(
        "SELECT * FROM concept_edges WHERE manuscript_id = ? "
        "AND status = 'inferred' ORDER BY created_at", (mid,))]
    drafts = {row["object_id"] for row in db.all(
        "SELECT object_id FROM triage_drafts WHERE manuscript_id = ? "
        "AND triage_type = 'edges'", (mid,))}
    ungrounded = {row["id"]: row for row in
                  hygiene.ungrounded_edges(db, mid, files)}

    decisions = []
    protected = []
    for edge in edges:
        source = nodes.get(edge["from_node"])
        target = nodes.get(edge["to_node"])
        from_name = source["name"] if source else edge["from_node"]
        to_name = target["name"] if target else edge["to_node"]
        label = f"{from_name} -{edge['relation']}-> {to_name}"
        if edge["id"] in drafts:
            protected.append({"triage_type": "edges", "object_id": edge["id"],
                              "object_label": label,
                              "reason": "an author decision is already staged"})
            continue
        if not source or not target or source["status"] == "retired" \
                or target["status"] == "retired":
            rule = "edge_retired_endpoint"
            reason = "one or both endpoints are missing or retired"
        elif edge["from_node"] == edge["to_node"]:
            rule = "edge_self_reference"
            reason = "both endpoints resolve to the same concept"
        elif db.one(
            "SELECT id FROM concept_edges WHERE manuscript_id = ? "
            "AND from_node = ? AND relation = ? AND to_node = ? AND id != ? "
            "AND status NOT IN ('inferred', 'rejected', 'retired')",
            (mid, edge["from_node"], edge["relation"], edge["to_node"], edge["id"]),
        ):
            rule = "edge_duplicate_settled"
            reason = "the same relationship is already settled"
        elif edge["id"] in ungrounded:
            rule = "edge_unmentioned_endpoint"
            missing = ", ".join(ungrounded[edge["id"]]["unmentioned"])
            reason = f"manuscript text does not mention: {missing}"
        else:
            continue
        decisions.append(_decision(rule, edge, label, reason))
    return decisions, protected


def plan(db: Database, manuscript: dict, files: dict[str, str], *,
         include_concepts: bool = True,
         include_edges: bool = True) -> dict[str, Any]:
    """Return deterministic decisions without changing graph or draft state."""
    decisions: list[dict] = []
    protected: list[dict] = []
    if include_concepts:
        found, skipped = _concept_decisions(db, manuscript, files)
        decisions.extend(found)
        protected.extend(skipped)
    if include_edges:
        found, skipped = _edge_decisions(db, manuscript, files)
        decisions.extend(found)
        protected.extend(skipped)
    return {
        "decisions": decisions,
        "protected": protected,
        "counts": {
            "concepts": sum(d["triage_type"] == "concepts" for d in decisions),
            "edges": sum(d["triage_type"] == "edges" for d in decisions),
            "protected": len(protected),
        },
    }
