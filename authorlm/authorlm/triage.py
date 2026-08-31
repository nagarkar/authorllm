"""Shared concept/edge triage schemas and mutation service.

The CLI, MCP tools, and web app all consume this module. Surfaces choose how
to present a decision; this module owns what the decision means.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from . import concepts as cg
from .db import Database, ko_fields, loads, now_iso
from .extraction import VALID_RELATIONS, record_triage

CONCEPT_TRIAGE_HELP = (
    "Keys: [k]eep  [r]etire  [s]kip (or Enter)  [n] reword notes  "
    "[a] alias of another concept  [x] quit — or retype: [c]oncept "
    "[o]bjection [e]xample [m]etaphor [q]uestion "
    "[h]istorical_reference [t] mathematical_construct"
)

EDGE_TRIAGE_HELP = (
    "Keys: [k]onfirm as-is  [r]eject  [f]lip direction  "
    "[a] these are one concept (alias-merge)  [s]kip (or Enter)  "
    "[x] quit — or retype the relation by number, name, or unique prefix"
)

DETERMINISTIC_TRIAGE_HELP = (
    "report mechanically safe retire, merge, and reject decisions without "
    "LLM calls; add --apply to execute them atomically"
)


def _optional_reason(placeholder: str) -> dict[str, Any]:
    return {
        "label": "Reason (optional)",
        "placeholder": placeholder,
        "required": False,
    }


def _required_reason(placeholder: str) -> dict[str, Any]:
    return {
        "label": "Reason (required)",
        "placeholder": placeholder,
        "required": True,
    }


PROPOSAL_TRIAGE_HELP = (
    "Proposals are conflicts with knowledge you have already settled: a "
    "reframed definition, a retired concept recurring, a rejected "
    "relationship argued again. Unlike concept and edge triage — which "
    "curate machine HYPOTHESES that were never settled — accepting here "
    "overwrites a decision you already made, so the settled version wins by "
    "default. Rows are grouped by concept because proposals against one "
    "concept are competing rewrites of a single note: pick at most one. "
    "Dismissal reasons are the highest-value evidence in the system — they "
    "are what the distiller turns into beliefs."
)

CRITIQUE_TRIAGE_HELP = (
    "Critique items are an external critic's proposals — revision tasks and "
    "candidate standing rules imported from an editorial report. Nothing "
    "here is law: the critic's authority makes an item exist, only the "
    "author's verdict makes it real. Accept turns an item into an active "
    "intent or style law; Reject refuses it and REQUIRES the author's "
    "reason, verbatim — an explained rejection is the highest-value "
    "evidence the system can receive; Revise accepts it in the author's "
    "own wording, flipping provenance to the author with the critic's "
    "original kept as lineage. Rows are grouped by sitting: rule on the "
    "manuscript-wide items first (they set the law every essay pass "
    "consults), then each essay's items just-in-time, in reading order."
)

TRIAGE_SCHEMAS: dict[str, dict[str, Any]] = {
    "concepts": {
        "id": "concepts",
        "label": "Concepts",
        "singular": "concept",
        "help": CONCEPT_TRIAGE_HELP,
        "default_profile": "concept-keepability",
        "columns": [
            {"id": "name", "label": "Concept", "kind": "text", "width": 240},
            {"id": "kind", "label": "Kind", "kind": "enum", "width": 150},
            {"id": "status", "label": "Status", "kind": "status", "width": 105},
            {"id": "introduced_in", "label": "Introduced", "kind": "text", "width": 160},
            {"id": "notes", "label": "Notes", "kind": "long_text", "width": 330},
            {"id": "aliases", "label": "Aliases", "kind": "list", "width": 180},
            {"id": "version", "label": "Row v", "kind": "number", "width": 70},
        ],
        "actions": [
            {"id": "keep", "label": "Keep", "help": "Confirm the extracted concept as-is."},
            {"id": "retire", "label": "Retire", "help": "Retire the concept and its live edges.",
             "reason": _optional_reason("Why should this concept be retired?")},
            {"id": "retype", "label": "Retype", "help": "Confirm the concept with a different kind.",
             "parameter": {"id": "kind", "label": "Concept kind", "options": sorted(cg.NODE_KINDS)},
             "reason": _optional_reason("Why should this concept have a different kind?")},
            {"id": "alias", "label": "Alias merge", "help": "Merge this concept into an existing canonical concept.",
             "parameter": {"id": "canonical_id", "label": "Canonical concept", "source": "concepts"},
             "reason": _optional_reason("Why are these the same concept?")},
            {"id": "reword_notes", "label": "Reword notes", "help": "Update notes without settling the concept.",
             "parameter": {"id": "notes", "label": "New notes", "multiline": True}},
        ],
    },
    "edges": {
        "id": "edges",
        "label": "Edges",
        "singular": "edge",
        "help": EDGE_TRIAGE_HELP,
        "default_profile": "edge-keepability",
        "columns": [
            {"id": "from_name", "label": "From", "kind": "text", "width": 190},
            {"id": "relation", "label": "Relation", "kind": "enum", "width": 160},
            {"id": "to_name", "label": "To", "kind": "text", "width": 190},
            {"id": "status", "label": "Status", "kind": "status", "width": 105},
            {"id": "support", "label": "Support", "kind": "number", "width": 85},
            {"id": "evidence", "label": "Evidence", "kind": "list", "width": 290},
            {"id": "version", "label": "Row v", "kind": "number", "width": 70},
        ],
        "actions": [
            {"id": "keep", "label": "Keep", "help": "Confirm the inferred relationship as-is."},
            {"id": "reject", "label": "Reject", "help": "Reject the inferred relationship."},
            {"id": "flip", "label": "Flip & keep", "help": "Reverse the relationship direction and confirm it."},
            {"id": "retype", "label": "Retype", "help": "Confirm the relationship with a different relation.",
             "parameter": {"id": "relation", "label": "Relation", "options": sorted(VALID_RELATIONS)},
             "reason": _optional_reason("Why should this relationship have a different type?")},
            {"id": "alias", "label": "Alias merge", "help": "Merge one endpoint into the other canonical concept.",
             "parameter": {"id": "canonical_id", "label": "Canonical endpoint", "source": "edge_endpoints"},
             "reason": _optional_reason("Why are these endpoints the same concept?")},
        ],
    },
    "critique": {
        "id": "critique",
        "label": "Critique",
        "singular": "critique item",
        "help": CRITIQUE_TRIAGE_HELP,
        # No analyzer by design: a critique item is already a critic's
        # opinion; the author's verdict is the only judgment that counts.
        "default_profile": None,
        # Grouped by sitting: the global items first, then each essay's
        # just-in-time pile, matching the design's triage sequencing.
        "group_by": {"id": "scope_label", "label": "Sitting"},
        "columns": [
            {"id": "id", "label": "Id", "kind": "text", "width": 130},
            {"id": "kind", "label": "Kind", "kind": "enum", "width": 110},
            {"id": "scope_label", "label": "Sitting", "kind": "enum", "width": 150},
            {"id": "unit", "label": "Unit", "kind": "text", "width": 180},
            # editable: an inline edit of this column stages the named
            # action with the new text as its parameter — the app's
            # edit-in-place path to Revise & accept.
            {"id": "statement", "label": "Item", "kind": "long_text",
             "width": 430, "editable": "revise"},
            {"id": "source_name", "label": "Critic", "kind": "enum", "width": 200},
            {"id": "raised", "label": "Raised", "kind": "text", "width": 105},
            {"id": "version", "label": "Row v", "kind": "number", "width": 70},
        ],
        "actions": [
            {"id": "accept", "label": "Accept",
             "help": "The item becomes an active intent (or active style law). "
                     "The author's verdict, not the critic's authority, makes it real."},
            {"id": "reject", "label": "Reject",
             "help": "The critic's item is refused. The reason is the author's, "
                     "verbatim — it is the evidence the system learns from.",
             "reason": _required_reason("Why is the critic wrong here?")},
            # hidden: no action-bar button — the Item column's edit-in-place
            # (editable: "revise") is the only path that stages this action.
            {"id": "revise", "label": "Revise & accept", "hidden": True,
             "help": "Double-click the Item cell and type your own wording — "
                     "the edit stages this action. Provenance flips to the "
                     "author; the critic's original survives as lineage and "
                     "the original→final diff is recorded as evidence.",
             "parameter": {"id": "text", "label": "The author's wording",
                           "multiline": True}},
        ],
    },
    "proposals": {
        "id": "proposals",
        "label": "Proposals",
        "singular": "proposal",
        "help": PROPOSAL_TRIAGE_HELP,
        "default_profile": None,
        # Grouped by concept: within one concept the proposals are competing
        # rewrites of the SAME note, not independent questions. 617 open rows
        # were 280 concepts; judged per row that is 617 decisions, judged per
        # concept it is 280, most of them one keystroke.
        "group_by": {"id": "target_name", "label": "Concept"},
        "columns": [
            {"id": "id", "label": "Id", "kind": "text", "width": 130},
            {"id": "target_name", "label": "Concept", "kind": "text", "width": 190},
            {"id": "kind", "label": "Kind", "kind": "enum", "width": 130},
            {"id": "summary", "label": "Proposes", "kind": "text", "width": 300},
            {"id": "current_note", "label": "Current", "kind": "long_text", "width": 300},
            {"id": "proposed_note", "label": "Proposed", "kind": "long_text", "width": 300},
            {"id": "rebased", "label": "Re-based", "kind": "text", "width": 90},
            {"id": "raised", "label": "Raised", "kind": "text", "width": 105},
            {"id": "version", "label": "Row v", "kind": "number", "width": 70},
        ],
        "actions": [
            {"id": "accept", "label": "Accept",
             "help": "Apply the proposal to the settled object."},
            {"id": "dismiss", "label": "Dismiss",
             "help": "Keep the settled version. The reason is the evidence the "
                     "system learns from — give it verbatim.",
             "reason": _optional_reason("Why is the settled version right?")},
            {"id": "edge", "label": "Kind, not identity",
             "help": "Alias proposals only: record 'canonical generalizes "
                     "alias' instead of merging the two concepts."},
        ],
    },
}


class TriageConflict(ValueError):
    def __init__(self, conflicts: list[dict[str, Any]]):
        self.conflicts = conflicts
        super().__init__(f"{len(conflicts)} selected row(s) changed since the decision was staged")


def schema(triage_type: str, profile: dict[str, Any] | None = None) -> dict[str, Any]:
    if triage_type not in TRIAGE_SCHEMAS:
        raise ValueError(f"unknown triage type '{triage_type}'")
    result = json.loads(json.dumps(TRIAGE_SCHEMAS[triage_type]))
    result["analysis_columns"] = (profile or {}).get("output_fields", [])
    return result


def _profile_files(manuscript: dict) -> list[Path]:
    built_in = Path(__file__).parent / "triage_profiles"
    local = Path(manuscript["path"]) / "_triage" / "profiles"
    return sorted(built_in.glob("*.json")) + sorted(local.glob("*.json"))


def list_profiles(manuscript: dict, triage_type: str | None = None) -> list[dict[str, Any]]:
    profiles: dict[tuple[str, str], dict[str, Any]] = {}
    built_in = Path(__file__).parent / "triage_profiles"
    for path in _profile_files(manuscript):
        try:
            profile = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as err:
            raise ValueError(f"invalid triage profile {path}: {err}") from err
        required = {"id", "version", "triage_type", "label", "prompt", "output_fields"}
        missing = required - profile.keys()
        if missing:
            raise ValueError(f"triage profile {path} is missing {', '.join(sorted(missing))}")
        if profile["triage_type"] not in TRIAGE_SCHEMAS:
            raise ValueError(f"triage profile {path} has unknown type '{profile['triage_type']}'")
        output_ids = {field.get("id") for field in profile["output_fields"]}
        core = {"score", "confidence", "why", "evidence"}
        if not core <= output_ids:
            raise ValueError(
                f"triage profile {path} must define the core output fields: "
                f"{', '.join(sorted(core))}")
        profile["source"] = "built-in" if path.parent == built_in else "manuscript"
        profile["path"] = str(path)
        profiles[(profile["id"], str(profile["version"]))] = profile
    def version_key(profile: dict[str, Any]):
        parts = re.split(r"(\d+)", str(profile["version"]))
        return profile["id"], tuple(int(part) if part.isdigit() else part for part in parts)

    result = sorted(profiles.values(), key=version_key)
    if triage_type:
        result = [p for p in result if p["triage_type"] == triage_type]
    return result


def resolve_profile(manuscript: dict, triage_type: str,
                    profile_id: str | None = None,
                    profile_version: str | None = None) -> dict[str, Any]:
    profile_id = profile_id or TRIAGE_SCHEMAS[triage_type]["default_profile"]
    candidates = [p for p in list_profiles(manuscript, triage_type)
                  if p["id"] == profile_id]
    if profile_version is not None:
        candidates = [p for p in candidates if str(p["version"]) == str(profile_version)]
    if not candidates:
        suffix = f"@{profile_version}" if profile_version else ""
        raise LookupError(f"no {triage_type} analyzer profile '{profile_id}{suffix}'")
    return candidates[-1]


def profile_snapshot(profile: dict[str, Any]) -> tuple[str, str]:
    clean = {k: v for k, v in profile.items() if k not in {"path", "source"}}
    raw = json.dumps(clean, sort_keys=True, separators=(",", ":"))
    return raw, hashlib.sha256(raw.encode()).hexdigest()


def _concept_rows(db: Database, manuscript_id: str) -> list[dict[str, Any]]:
    result = []
    for raw in db.all(
        "SELECT n.*, s.name AS source_name FROM concept_nodes n "
        "LEFT JOIN sources s ON s.id = n.source_id "
        "WHERE n.manuscript_id = ? ORDER BY lower(n.name)", (manuscript_id,)
    ):
        row = dict(raw)
        meta = loads(row["metadata"], {})
        row["aliases"] = loads(row["aliases"], [])
        row["pending"] = (meta.get("origin") == "extracted"
                          and not meta.get("confirmed")
                          and row["status"] != "retired")
        row["lifecycle"] = "pending" if row["pending"] else "previously_decided"
        result.append(row)
    return result


def _edge_rows(db: Database, manuscript_id: str) -> list[dict[str, Any]]:
    rows = db.all(
        "SELECT e.*, f.name AS from_name, t.name AS to_name, s.name AS source_name "
        "FROM concept_edges e JOIN concept_nodes f ON f.id = e.from_node "
        "JOIN concept_nodes t ON t.id = e.to_node "
        "LEFT JOIN sources s ON s.id = e.source_id "
        "WHERE e.manuscript_id = ? ORDER BY lower(f.name), e.relation, lower(t.name)",
        (manuscript_id,),
    )
    result = []
    for raw in rows:
        row = dict(raw)
        row["evidence"] = loads(row["evidence"], [])
        row["pending"] = row["status"] == "inferred"
        row["lifecycle"] = "pending" if row["pending"] else "previously_decided"
        result.append(row)
    return result


def _proposal_rows(db: Database, manuscript_id: str) -> list[dict[str, Any]]:
    """Open proposals, flattened for the grid. `summary` reuses
    proposals.describe so the wording matches every other surface."""
    from . import proposals as prop

    result = []
    for raw in db.all(
        "SELECT p.*, n.name AS concept_name FROM knowledge_proposals p "
        "LEFT JOIN concept_nodes n ON n.id = p.target "
        "WHERE p.manuscript_id = ? AND p.state = 'open' "
        "ORDER BY lower(coalesce(n.name, p.target)), p.created_at",
        (manuscript_id,)
    ):
        row = dict(raw)
        payload = loads(row["payload"], {})
        meta = loads(row["metadata"], {})
        row["summary"] = prop.describe(row)[0]
        row["target_name"] = (row.pop("concept_name", None)
                              or payload.get("name")
                              or payload.get("alias") or row["target"])
        row["current_note"] = payload.get("current_note") or ""
        row["proposed_note"] = (payload.get("proposed_note")
                                or payload.get("notes") or "")
        # A re-based row had its 'current' corrected after the note moved on;
        # the author should know they are not reading the original framing.
        row["rebased"] = "yes" if meta.get("rebased") else ""
        row["raised"] = (row["created_at"] or "")[:10]
        row["pending"] = True
        row["lifecycle"] = "pending"
        result.append(row)
    return result


def _critique_rows(db: Database, manuscript_id: str) -> list[dict[str, Any]]:
    """Pending critique items — proposed critic-sourced intents and style
    elements — flattened for the grid. Two tables, one pile: the author
    triages an editorial report, not our storage layout."""
    result = []
    for raw in db.all(
        "SELECT i.*, s.name AS source_name FROM declared_intents i "
        "JOIN sources s ON s.id = i.source_id "
        "WHERE i.manuscript_id = ? AND i.status = 'proposed' "
        "AND s.kind = 'critic' ORDER BY i.created_at",
        (manuscript_id,)
    ):
        row = dict(raw)
        meta = loads(row["metadata"], {}).get("critique", {})
        row["kind"] = "intent"
        row["scope_label"] = row.get("scope") or "manuscript-wide"
        row["unit"] = meta.get("unit") or ""
        row["ordinal"] = meta.get("ordinal")
        row["raised"] = (row["created_at"] or "")[:10]
        row["pending"] = True
        row["lifecycle"] = "pending"
        result.append(row)
    for raw in db.all(
        "SELECT l.*, s.name AS source_name FROM style_laws l "
        "JOIN sources s ON s.id = l.source_id "
        "WHERE l.manuscript_id = ? AND l.status = 'proposed' "
        "AND s.kind = 'critic' ORDER BY l.created_at",
        (manuscript_id,)
    ):
        row = dict(raw)
        meta = loads(row["metadata"], {}).get("critique", {})
        row["kind"] = "style element"
        row["scope_label"] = row.get("file") or "manuscript-wide"
        row["unit"] = meta.get("unit") or ""
        row["ordinal"] = meta.get("ordinal")
        row["raised"] = (row["created_at"] or "")[:10]
        row["pending"] = True
        row["lifecycle"] = "pending"
        result.append(row)
    result.sort(key=lambda r: (r["scope_label"] != "manuscript-wide",
                               r["scope_label"], r["unit"],
                               r["ordinal"] if r["ordinal"] is not None else 0))
    return result


def list_rows(db: Database, manuscript: dict, triage_type: str) -> list[dict[str, Any]]:
    if triage_type == "concepts":
        return _concept_rows(db, manuscript["id"])
    if triage_type == "edges":
        return _edge_rows(db, manuscript["id"])
    if triage_type == "proposals":
        return _proposal_rows(db, manuscript["id"])
    if triage_type == "critique":
        return _critique_rows(db, manuscript["id"])
    raise ValueError(f"unknown triage type '{triage_type}'")


def _latest_manuscript_version(db: Database, manuscript_id: str) -> dict | None:
    row = db.one(
        "SELECT * FROM manuscript_versions WHERE manuscript_id = ? "
        "ORDER BY version_no DESC LIMIT 1", (manuscript_id,))
    return dict(row) if row else None


def snapshot(db: Database, manuscript: dict, triage_type: str,
             profile_id: str | None = None,
             profile_version: str | None = None) -> dict[str, Any]:
    # Proposals have no analyzer profile and want none: a proposal is already
    # the machine's opinion, so scoring it with a second model would be the
    # system grading its own homework. The grid renders the same either way —
    # analysis columns simply stay empty.
    profile = (resolve_profile(manuscript, triage_type, profile_id,
                               profile_version)
               if TRIAGE_SCHEMAS[triage_type].get("default_profile") else None)
    rows = list_rows(db, manuscript, triage_type)
    by_id = {row["id"]: row for row in rows}
    current_version = _latest_manuscript_version(db, manuscript["id"])
    local_dirty = False
    if current_version:
        from .revisions import checksum, read_manuscript_files

        try:
            local_dirty = checksum(read_manuscript_files(Path(manuscript["path"]))) \
                != current_version["checksum"]
        except OSError:
            local_dirty = True
    assessments = db.all(
        "SELECT a.*, r.profile_id, r.profile_version, r.manuscript_version_id, "
        "r.created_at AS run_created_at FROM triage_assessments a "
        "JOIN triage_runs r ON r.id = a.run_id "
        "WHERE a.manuscript_id = ? AND a.triage_type = ? AND r.profile_id = ? "
        "AND r.profile_version = ? ORDER BY r.created_at DESC",
        (manuscript["id"], triage_type, profile["id"], str(profile["version"])),
    ) if profile else []
    seen: set[str] = set()
    for raw in assessments:
        item = dict(raw)
        object_id = item["object_id"]
        if object_id in seen or object_id not in by_id:
            continue
        seen.add(object_id)
        current = (current_version is not None and not local_dirty
                   and item["manuscript_version_id"] == current_version["id"]
                   and item["object_version"] == by_id[object_id]["version"])
        by_id[object_id]["analysis"] = {
            **loads(item["output"], {}),
            "state": "current" if current else "outdated",
            "run_id": item["run_id"],
            "profile_id": item["profile_id"],
            "profile_version": item["profile_version"],
        }
    drafts = db.all(
        "SELECT * FROM triage_drafts WHERE manuscript_id = ? AND triage_type = ?",
        (manuscript["id"], triage_type),
    )
    for raw in drafts:
        draft = dict(raw)
        if draft["object_id"] not in by_id:
            continue
        by_id[draft["object_id"]]["draft"] = {
            "action": draft["action"],
            "parameters": loads(draft["parameters"], {}),
            "reason": draft["reason"],
            "state": ("current" if draft["object_version"] == by_id[draft["object_id"]]["version"]
                      else "conflict"),
        }
    incomplete = [dict(r) for r in db.all(
        "SELECT id, profile_id, profile_version, requested_ids, created_at "
        "FROM triage_runs WHERE manuscript_id = ? AND triage_type = ? "
        "AND status = 'incomplete' ORDER BY created_at DESC",
        (manuscript["id"], triage_type),
    )]
    for run in incomplete:
        run["requested_ids"] = loads(run["requested_ids"], [])
        completed = db.all("SELECT object_id FROM triage_assessments WHERE run_id = ?",
                           (run["id"],))
        done = {r["object_id"] for r in completed}
        run["remaining_ids"] = [i for i in run["requested_ids"] if i not in done]
    return {
        "manuscript": {"id": manuscript["id"], "name": manuscript["name"]},
        "manuscript_version": ({"id": current_version["id"],
                                "version_no": current_version["version_no"],
                                "checksum": current_version["checksum"],
                                "local_dirty": local_dirty}
                               if current_version else None),
        "schema": schema(triage_type, profile),
        "profile": profile,
        "profiles": list_profiles(manuscript, triage_type),
        "rows": rows,
        "incomplete_runs": incomplete,
    }


def _table(triage_type: str) -> str:
    return {"concepts": "concept_nodes", "edges": "concept_edges",
            "proposals": "knowledge_proposals"}.get(triage_type) \
        or _raise_unknown(triage_type)


def _fetch_row(db: Database, manuscript_id: str, triage_type: str,
               object_id: str) -> dict[str, Any] | None:
    """Fetch one triageable object by id. Critique items span two tables
    (declared_intents and style_laws) — every other type is one table."""
    if triage_type == "critique":
        for table in ("declared_intents", "style_laws"):
            row = db.one(f"SELECT * FROM {table} WHERE manuscript_id = ? "
                         "AND id = ?", (manuscript_id, object_id))
            if row:
                return dict(row)
        return None
    row = db.one(f"SELECT * FROM {_table(triage_type)} "
                 "WHERE manuscript_id = ? AND id = ?",
                 (manuscript_id, object_id))
    return dict(row) if row else None


def _raise_unknown(triage_type: str):
    raise ValueError(f"unknown triage type '{triage_type}'")


def _validate_action(db: Database, manuscript: dict, triage_type: str,
                     row: dict, action: str, parameters: dict[str, Any]) -> None:
    actions = {a["id"] for a in TRIAGE_SCHEMAS[triage_type]["actions"]}
    if action not in actions:
        raise ValueError(f"'{action}' is not a {triage_type} triage action")
    if triage_type == "concepts" and action == "retype":
        if parameters.get("kind") not in cg.NODE_KINDS:
            raise ValueError(f"invalid concept kind '{parameters.get('kind', '')}'")
    if triage_type == "edges" and action == "retype":
        if parameters.get("relation") not in VALID_RELATIONS:
            raise ValueError(f"invalid relation '{parameters.get('relation', '')}'")
    if action == "reword_notes" and not str(parameters.get("notes", "")).strip():
        raise ValueError("Reword notes needs non-empty notes")
    if triage_type == "critique" and action == "revise" \
            and not str(parameters.get("text", "")).strip():
        raise ValueError("Revise needs the author's wording")
    if action == "alias":
        canonical_id = parameters.get("canonical_id")
        if not canonical_id:
            raise ValueError("Alias merge needs a canonical concept")
        canonical = db.one(
            "SELECT * FROM concept_nodes WHERE manuscript_id = ? AND id = ? "
            "AND status != 'retired'", (manuscript["id"], canonical_id))
        if not canonical:
            raise LookupError("the canonical concept no longer exists")
        if triage_type == "concepts" and canonical_id == row["id"]:
            raise ValueError("a concept cannot be its own alias")
        if triage_type == "edges" and canonical_id not in {row["from_node"], row["to_node"]}:
            raise ValueError("the canonical concept must be one endpoint of the edge")


def stage_decisions(db: Database, manuscript: dict, triage_type: str,
                    decisions: list[dict[str, Any]]) -> dict[str, Any]:
    staged = []
    with db.transaction():
        for decision in decisions:
            object_id = decision["object_id"]
            row = _fetch_row(db, manuscript["id"], triage_type, object_id)
            if not row:
                raise LookupError(f"no {TRIAGE_SCHEMAS[triage_type]['singular']} '{object_id}'")
            action = decision["action"]
            parameters = decision.get("parameters") or {}
            _validate_action(db, manuscript, triage_type, row, action, parameters)
            action_schema = next(item for item in TRIAGE_SCHEMAS[triage_type]["actions"]
                                 if item["id"] == action)
            existing = db.one(
                "SELECT * FROM triage_drafts WHERE manuscript_id = ? "
                "AND triage_type = ? AND object_id = ?",
                (manuscript["id"], triage_type, object_id),
            )
            changes = {
                "object_version": row["version"],
                "action": action,
                "parameters": json.dumps(parameters),
                "reason": decision.get("reason") if action_schema.get("reason") else None,
                "updated_at": now_iso(),
            }
            if existing:
                db.update("triage_drafts", existing["id"], changes)
            else:
                draft = ko_fields("td")
                draft.update(manuscript_id=manuscript["id"], triage_type=triage_type,
                             object_id=object_id, **changes)
                db.insert("triage_drafts", draft)
            staged.append(object_id)
    return {"staged": staged}


def unstage_decisions(db: Database, manuscript: dict, triage_type: str,
                      object_ids: list[str]) -> dict[str, Any]:
    if not object_ids:
        return {"unstaged": []}
    marks = ",".join("?" for _ in object_ids)
    with db.transaction():
        db.conn.execute(
            f"DELETE FROM triage_drafts WHERE manuscript_id = ? AND triage_type = ? "
            f"AND object_id IN ({marks})", (manuscript["id"], triage_type, *object_ids))
    return {"unstaged": object_ids}


def _record_edge_triage(db: Database, manuscript_id: str,
                        description: str, signal: str,
                        reason: str | None = None) -> None:
    ev = ko_fields("ev")
    target = description[:200]
    if reason:
        target = f"{description} - reason: {reason}"
    ev.update(manuscript_id=manuscript_id, episode_id=None,
              evidence_type="edge_triage", signal=signal,
              target=target,
              supports_belief=None, weight="high")
    db.insert("evidence", ev)


def apply_action(db: Database, manuscript: dict, triage_type: str, row: dict,
                 action: str, parameters: dict[str, Any] | None = None,
                 reason: str | None = None,
                 *, record_evidence: bool = True) -> dict[str, Any]:
    parameters = parameters or {}
    _validate_action(db, manuscript, triage_type, row, action, parameters)
    mid = manuscript["id"]
    if triage_type == "critique":
        # Routed through critique.py, never reimplemented here: the verbs
        # own the evidence recording, provenance flips, and lineage.
        from . import critique as crit

        is_element = "aspect" in row
        if action == "accept":
            (crit.accept_element if is_element else crit.accept_intent)(
                db, mid, row)
        elif action == "reject":
            if not reason:
                raise ValueError(
                    "a rejection needs the author's reason, verbatim — it is "
                    "the evidence the system learns from")
            (crit.reject_element if is_element else crit.reject_intent)(
                db, mid, row, reason)
        elif action == "revise":
            (crit.revise_element if is_element else crit.revise_intent)(
                db, mid, row, parameters["text"].strip())
        else:
            raise ValueError(f"'{action}' is not a critique triage action")
        return {"id": row["id"], "action": action}

    if triage_type == "proposals":
        # Routed through proposals.py, never reimplemented here: adopt() has
        # eight kind-specific effects (merging concepts, restoring edges,
        # retiring, re-declaring) and dismiss() has declared semantics for
        # `vanished`. Duplicating any of that would be the two-code-paths bug
        # this loop was built to end.
        from . import proposals as prop

        if action == "accept":
            message = prop.adopt(db, mid, row)
        elif action == "edge":
            message = prop.demote_to_edge(db, mid, row)
        elif action == "dismiss":
            if not reason:
                raise ValueError(
                    "a dismissal needs the author's reason, verbatim — it is "
                    "the evidence the system learns from")
            message = prop.dismiss(db, mid, row, reason=reason)
        else:
            raise ValueError(f"'{action}' is not a proposal triage action")
        return {"id": row["id"], "action": action, "message": message}

    if triage_type == "concepts":
        if action in {"keep", "retype"}:
            meta = loads(row["metadata"], {})
            extracted = meta.get("origin") == "extracted"
            meta["confirmed"] = True
            changes: dict[str, Any] = {"metadata": json.dumps(meta)}
            if action == "retype":
                changes["kind"] = parameters["kind"]
                if extracted and record_evidence:
                    record_triage(db, mid, row, "retyped", parameters["kind"], reason)
            elif extracted and record_evidence:
                record_triage(db, mid, row, "confirmed")
            db.update("concept_nodes", row["id"], changes)
            return {"id": row["id"], "action": action,
                    "kind": changes.get("kind", row["kind"])}
        if action == "retire":
            if (record_evidence
                    and loads(row["metadata"], {}).get("origin") == "extracted"):
                record_triage(db, mid, row, "rejected", reason=reason)
            edges = cg.retire_concept(db, mid, row)
            return {"id": row["id"], "action": action, "edges_retired": edges}
        if action == "alias":
            canonical = dict(db.one("SELECT * FROM concept_nodes WHERE id = ?",
                                    (parameters["canonical_id"],)))
            result = cg.merge_concepts(db, mid, canonical, row)
            if record_evidence:
                record_triage(db, mid, row, "merged", canonical["name"], reason)
            return {"id": row["id"], "action": action,
                    "canonical": canonical["name"], **result}
        if action == "reword_notes":
            db.update("concept_nodes", row["id"], {"notes": parameters["notes"].strip()})
            return {"id": row["id"], "action": action}

    from_name = cg.node_name(db, row["from_node"])
    to_name = cg.node_name(db, row["to_node"])
    if action in {"keep", "flip", "retype"}:
        relation = parameters.get("relation", row["relation"])
        changes: dict[str, Any] = {"status": "declared", "relation": relation}
        if action == "flip":
            changes.update(from_node=row["to_node"], to_node=row["from_node"])
            from_name, to_name = to_name, from_name
        db.update("concept_edges", row["id"], changes)
        retyped = action == "retype" or (action == "flip" and "relation" in parameters)
        if record_evidence:
            after = f"{from_name} —{relation}→ {to_name}"
            description = after
            if retyped:
                before = (f"{cg.node_name(db, row['from_node'])} "
                          f"—{row['relation']}→ {cg.node_name(db, row['to_node'])}")
                description = f"{before} ⇒ {after}"
            _record_edge_triage(
                db, mid, description, "retyped" if retyped else "confirmed",
                reason if retyped else None)
        return {"id": row["id"], "action": action, "from_name": from_name,
                "relation": relation, "to_name": to_name}
    if action == "reject":
        db.update("concept_edges", row["id"], {"status": "rejected"})
        if record_evidence:
            _record_edge_triage(db, mid,
                                f"{from_name} —{row['relation']}→ {to_name}",
                                "rejected", reason)
        return {"id": row["id"], "action": action}
    if action == "alias":
        canonical_id = parameters["canonical_id"]
        duplicate_id = (row["to_node"] if canonical_id == row["from_node"]
                        else row["from_node"])
        canonical = dict(db.one("SELECT * FROM concept_nodes WHERE id = ?", (canonical_id,)))
        duplicate = dict(db.one("SELECT * FROM concept_nodes WHERE id = ?", (duplicate_id,)))
        result = cg.merge_concepts(db, mid, canonical, duplicate)
        if record_evidence:
            record_triage(db, mid, duplicate, "merged", canonical["name"], reason)
        return {"id": row["id"], "action": action,
                "canonical": canonical["name"], **result}
    raise ValueError(f"unsupported action '{action}'")


def apply_one(db: Database, manuscript: dict, triage_type: str, row: dict,
              action: str, parameters: dict[str, Any] | None = None,
              reason: str | None = None,
              *, record_evidence: bool = True) -> dict[str, Any]:
    with db.transaction():
        return apply_action(db, manuscript, triage_type, row, action,
                            parameters, reason,
                            record_evidence=record_evidence)


def _record_deterministic_decision(db: Database, manuscript_id: str,
                                   decision: dict[str, Any]) -> None:
    event = ko_fields("ev")
    event["metadata"] = json.dumps({
        "triage_type": decision["triage_type"],
        "object_id": decision["object_id"],
        "rule": decision["rule"],
    })
    event.update(
        manuscript_id=manuscript_id,
        episode_id=None,
        evidence_type="deterministic_triage",
        signal=decision["action"],
        target=f"{decision['object_label']} - {decision['reason']}",
        supports_belief=None,
        weight="low",
    )
    db.insert("evidence", event)


def apply_deterministic(db: Database, manuscript: dict,
                        decisions: list[dict[str, Any]]) -> dict[str, Any]:
    """Apply a generated deterministic plan atomically.

    These are system decisions, not author verdicts, so they receive their
    own provenance and never enter concept/edge triage feedback.
    """
    from .triage_rules import RULES

    if not decisions:
        return {"applied": [], "count": 0}
    current: dict[tuple[str, str], dict] = {}
    conflicts = []
    with db.transaction():
        for decision in decisions:
            spec = RULES.get(decision.get("rule"))
            triage_type = decision.get("triage_type")
            if not spec or (spec["triage_type"], spec["action"]) != (
                    triage_type, decision.get("action")):
                raise ValueError(f"invalid deterministic triage rule "
                                 f"'{decision.get('rule', '')}'")
            row = db.one(
                f"SELECT * FROM {_table(triage_type)} "
                "WHERE manuscript_id = ? AND id = ?",
                (manuscript["id"], decision["object_id"]),
            )
            if not row or row["version"] != decision["object_version"]:
                conflicts.append({
                    "object_id": decision["object_id"],
                    "staged_version": decision["object_version"],
                    "current_version": row["version"] if row else None,
                })
                continue
            row = dict(row)
            if triage_type == "concepts" and not (
                loads(row["metadata"], {}).get("origin") == "extracted"
                and not loads(row["metadata"], {}).get("confirmed")
                and row["status"] != "retired"
            ):
                conflicts.append({"object_id": row["id"],
                                  "staged_version": decision["object_version"],
                                  "current_version": row["version"]})
                continue
            if triage_type == "edges" and row["status"] != "inferred":
                conflicts.append({"object_id": row["id"],
                                  "staged_version": decision["object_version"],
                                  "current_version": row["version"]})
                continue
            _validate_action(db, manuscript, triage_type, row,
                             decision["action"], decision.get("parameters") or {})
            current[(triage_type, row["id"])] = row
        if conflicts:
            raise TriageConflict(conflicts)

        applied = []
        ordered = sorted(decisions, key=lambda item: item["triage_type"] != "edges")
        for decision in ordered:
            row = current[(decision["triage_type"], decision["object_id"])]
            result = apply_action(
                db, manuscript, decision["triage_type"], row,
                decision["action"], decision.get("parameters") or {},
                record_evidence=False,
            )
            _record_deterministic_decision(db, manuscript["id"], decision)
            applied.append({**result, "rule": decision["rule"]})
    return {"applied": applied, "count": len(applied)}


def apply_selected(db: Database, manuscript: dict, triage_type: str,
                   object_ids: list[str]) -> dict[str, Any]:
    if not object_ids:
        raise ValueError("select at least one row to apply")
    applied = []
    with db.transaction():
        marks = ",".join("?" for _ in object_ids)
        drafts = [dict(r) for r in db.all(
            f"SELECT * FROM triage_drafts WHERE manuscript_id = ? AND triage_type = ? "
            f"AND object_id IN ({marks})", (manuscript["id"], triage_type, *object_ids))]
        by_id = {d["object_id"]: d for d in drafts}
        missing = [object_id for object_id in object_ids if object_id not in by_id]
        if missing:
            raise ValueError(f"{len(missing)} selected row(s) have no pending decision")
        conflicts = []
        current: dict[str, dict] = {}
        for object_id in object_ids:
            row = _fetch_row(db, manuscript["id"], triage_type, object_id)
            draft = by_id[object_id]
            if not row or row["version"] != draft["object_version"]:
                conflicts.append({"object_id": object_id,
                                  "staged_version": draft["object_version"],
                                  "current_version": row["version"] if row else None})
                continue
            current[object_id] = dict(row)
            _validate_action(db, manuscript, triage_type, current[object_id],
                             draft["action"], loads(draft["parameters"], {}))
        if conflicts:
            raise TriageConflict(conflicts)
        if len(object_ids) > 1 and any(draft["action"] == "alias"
                                       for draft in drafts):
            raise ValueError("alias merges must be applied one row at a time")
        for object_id in object_ids:
            draft = by_id[object_id]
            applied.append(apply_action(
                db, manuscript, triage_type, current[object_id], draft["action"],
                loads(draft["parameters"], {}), draft["reason"]))
        db.conn.execute(
            f"DELETE FROM triage_drafts WHERE manuscript_id = ? AND triage_type = ? "
            f"AND object_id IN ({marks})", (manuscript["id"], triage_type, *object_ids))
    return {"applied": applied, "count": len(applied)}
