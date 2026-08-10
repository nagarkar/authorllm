"""Guidance Generator (RFC AuthorLM §17.10, Chapter 14).

Proposes, never executes. Every suggestion carries an explanation tracing
it to evidence: the Concept Graph, prior episodes, and learned policies
(§11.6). When no evidence justifies a suggestion, the generator abstains —
a valid output, not a failure (§11.5).
"""

from __future__ import annotations

import json
import re

from .concepts import concept_pattern, mention_pattern, node_name, node_names
from .db import Database, ko_fields, loads, new_id
from .llm import LLMClient

MAX_SUGGESTIONS = 6

INTENT_KINDS = {"bridge", "policy_reminder"}       # generated from your intent
STRUCTURAL_KINDS = {"prerequisite", "objection"}   # standing manuscript findings

# Every kind generate_guidance() itself produces. guidance_history also holds
# rows of other kinds (e.g. 'beat' proposals from the write loop) that share
# the review pathway but have their own lifecycle — queries that manage
# guidance batches must filter to this allowlist, never to "everything".
GUIDANCE_KINDS = frozenset(
    {"bridge", "prerequisite", "definition", "objection", "policy_reminder",
     "focus", "abstention"}
)
_GUIDANCE_KINDS_SQL = ", ".join("?" for _ in GUIDANCE_KINDS)


def intent_coverage_notes(db: Database, manuscript: dict) -> list[str]:
    """Explain when an active intent can't drive suggestions: it names no
    concept in the graph. Transparency beats silently unrelated output."""
    notes = []
    nodes = db.all(
        "SELECT * FROM concept_nodes WHERE manuscript_id = ? AND status != 'retired'",
        (manuscript["id"],),
    )
    for intent in db.all(
        "SELECT * FROM declared_intents WHERE manuscript_id = ? AND status = 'active'",
        (manuscript["id"],),
    ):
        if not any(concept_pattern(nm).search(intent["statement"])
                   for n in nodes for nm in node_names(n)):
            notes.append(
                f"Your intent \"{intent['statement']}\" names no concept in the "
                f"Concept Graph, so no intent-specific suggestions can be "
                f"generated. If this is a new piece, add its concepts first "
                f"(e.g. concept add \"Commentary\") and link them — or expect "
                f"only standing structural findings below."
            )
    return notes

# For edge (A --relation--> B): which side must the reader meet first?
PREREQUISITE_FIRST = {"permits", "motivates", "foreshadows", "leads_to"}  # A before B
PREREQUISITE_SECOND = {"depends_on", "refutes"}                           # B before A


def _first_mentions(files: dict[str, str], names: dict[str, list[str]]) -> dict[str, int]:
    """Global first-mention position of each concept id across the
    manuscript (files in sorted order). A concept counts as mentioned
    under its primary name or any alias."""
    from .structure import ordered_items

    positions: dict[str, int] = {}
    offset = 0
    for _, text in ordered_items(files):
        for node_id, concept_names in names.items():
            if node_id in positions:
                continue
            starts = [m.start() for m in
                      (mention_pattern(nm).search(text) for nm in concept_names) if m]
            if starts:
                positions[node_id] = offset + min(starts)
        offset += len(text) + 1
    return positions


def compute_prerequisite_gaps(db: Database, mid: str, files: dict[str, str]) -> list[dict]:
    """Deterministic prerequisite check (<20ms): concepts that appear in the
    text before (or without) the concepts the graph says the reader must
    meet first. Shared by guidance and the push-lint line after collect."""
    nodes = {n["id"]: n for n in db.all(
        "SELECT * FROM concept_nodes WHERE manuscript_id = ? AND status != 'retired'",
        (mid,),
    )}
    names = {nid: node_names(n) for nid, n in nodes.items()}
    mentions = _first_mentions(files, names)
    gaps = []
    for edge in db.all(
        "SELECT * FROM concept_edges WHERE manuscript_id = ? AND relation != 'co_occurs' "
        "AND status NOT IN ('rejected', 'retired')",
        (mid,),
    ):
        if edge["relation"] in PREREQUISITE_FIRST:
            first, second = edge["from_node"], edge["to_node"]
        elif edge["relation"] in PREREQUISITE_SECOND:
            first, second = edge["to_node"], edge["from_node"]
        else:
            continue
        # Prerequisite ordering applies to ideas, not citations.
        if any(
            node_id in nodes and nodes[node_id]["kind"] == "historical_reference"
            for node_id in (first, second)
        ):
            continue
        if second not in mentions:
            continue
        text = None
        first_name = nodes[first]["name"]
        second_name = nodes[second]["name"]
        if first not in mentions:
            text = (f"'{second_name}' appears in the text, but its "
                    f"prerequisite '{first_name}' never does.")
        elif mentions[second] < mentions[first]:
            text = (f"'{second_name}' first appears before its prerequisite "
                    f"'{first_name}' is introduced.")
        if text:
            gaps.append({
                "edge_id": edge["id"],
                "relation": edge["relation"],
                "status": edge["status"],
                "from_name": nodes[edge["from_node"]]["name"],
                "to_name": nodes[edge["to_node"]]["name"],
                "first": first_name,
                "second": second_name,
                "text": text,
            })
    return gaps


def generate_guidance(
    db: Database,
    manuscript: dict,
    session: dict,
    llm: LLMClient | None = None,
) -> list[dict]:
    """Run the guidance heuristics and persist a new batch. Returns the
    persisted rows (a single 'abstention' row when nothing is justified)."""
    mid = manuscript["id"]
    batch_id = new_id("gb")

    # Newly generated guidance supersedes unreviewed proposals in this
    # session. Guidance kinds only: a pending 'beat' proposal from the write
    # loop must survive a mid-writeup guidance run.
    db.conn.execute(
        "UPDATE guidance_history SET state = 'superseded' "
        f"WHERE session_id = ? AND state = 'proposed' "
        f"AND kind IN ({_GUIDANCE_KINDS_SQL})",
        (session["id"], *GUIDANCE_KINDS),
    )
    db.conn.commit()

    intents = db.all(
        "SELECT * FROM declared_intents WHERE manuscript_id = ? AND status = 'active'",
        (mid,),
    )
    latest = db.one(
        "SELECT * FROM manuscript_versions WHERE manuscript_id = ? "
        "ORDER BY version_no DESC LIMIT 1",
        (mid,),
    )
    files: dict[str, str] = loads(latest["files"], {}) if latest else {}
    nodes = {n["id"]: n for n in db.all(
        "SELECT * FROM concept_nodes WHERE manuscript_id = ? AND status != 'retired'",
        (mid,),
    )}
    validated = db.all(
        "SELECT * FROM editorial_policies WHERE manuscript_id = ? AND status = 'validated' "
        "ORDER BY confidence DESC",
        (mid,),
    )
    # Candidates surface as reminders too — reviewing them is how they earn
    # (or lose) the evidence that promotes or retires them (§8.2–§8.3).
    reminder_policies = db.all(
        "SELECT * FROM editorial_policies WHERE manuscript_id = ? "
        "AND status IN ('validated', 'candidate') "
        "ORDER BY CASE status WHEN 'validated' THEN 0 ELSE 1 END, confidence DESC",
        (mid,),
    )

    candidates: list[dict] = []

    # 1. Introduce concepts named by an active intent but not yet realized.
    for intent in intents:
        for node in nodes.values():
            if node["status"] != "declared":
                continue
            if not any(concept_pattern(nm).search(intent["statement"])
                       for nm in node_names(node)):
                continue
            related = db.all(
                "SELECT * FROM concept_edges WHERE manuscript_id = ? AND relation != 'co_occurs' "
                "AND status NOT IN ('rejected', 'retired') "
                "AND (from_node = ? OR to_node = ?)",
                (mid, node["id"], node["id"]),
            )
            relation_notes = [
                f"{node_name(db, e['from_node'])} —{e['relation']}→ {node_name(db, e['to_node'])}"
                for e in related
            ]
            anchors = [
                f"{nodes[e[side]]['name']} (introduced in {nodes[e[side]]['introduced_in']})"
                for e in related
                for side in ("from_node", "to_node")
                if e[side] != node["id"] and nodes[e[side]]["status"] == "realized"
            ]
            suggestion = f"Introduce '{node['name']}' — declared in the Concept Graph but not yet realized in text."
            explanation_parts = [
                f"Serves your declared intent: \"{intent['statement']}\".",
                f"'{node['name']}' exists in the Concept Graph as a declared placeholder (§21.6).",
            ]
            policy_ids = []
            if anchors:
                explanation_parts.append(
                    "Anchor it to already-realized neighbors: " + "; ".join(sorted(set(anchors))) + "."
                )
            if relation_notes:
                explanation_parts.append("Graph relationships: " + "; ".join(relation_notes) + ".")
            # Episodic memory (§19.7, §21.8): how did the author handle this
            # concept's neighbors? Their analyzed decision sequences become
            # precedents — and, since the explanation feeds the drafting
            # prompt, the drafted text follows the author's demonstrated
            # approach rather than a generic one.
            from .analysis import find_precedents

            for precedent in find_precedents(db, mid, node["id"]):
                line = (f"Precedent — {precedent['label']}, you: "
                        + "; then ".join(precedent["actions"]) + ".")
                if precedent["outcome"]:
                    line += f" Outcome: {precedent['outcome']}"
                explanation_parts.append(line)
            if validated:
                top = validated[0]
                suggestion += f" Follow your validated policy: \"{top['statement']}\""
                explanation_parts.append(
                    f"Policy \"{top['statement']}\" is validated "
                    f"(confidence {top['confidence']}, {top['supporting']} supporting / "
                    f"{top['contradicting']} contradicting reviews)."
                )
                policy_ids.append(top["id"])
            candidates.append({
                "kind": "bridge",
                "key": f"bridge:{node['id']}",
                "intent_id": intent["id"],
                "suggestion": suggestion,
                "explanation": " ".join(explanation_parts),
                "policy_ids": policy_ids,
            })

    # 2. Prerequisite gaps: a concept appears in text before its prerequisite.
    for gap in compute_prerequisite_gaps(db, mid, files):
        settle = ""
        if gap["status"] == "inferred":
            settle = (
                f" This rests on an UNCONFIRMED inferred relationship — settle it "
                f"instead of writing, if it's wrong: confirm: concept confirm "
                f"{gap['edge_id'][:8]} {gap['relation']} · dismiss: concept "
                f"reject-edge {gap['edge_id'][:8]}."
            )
        candidates.append({
            "kind": "prerequisite",
            "key": f"prerequisite:{gap['edge_id']}",
            "intent_id": intents[0]["id"] if intents else None,
            "suggestion": f"Bridge the prerequisite gap: introduce '{gap['first']}' before '{gap['second']}', or add a forward-reference bridge.",
            "explanation": (
                f"{gap['text']} The Concept Graph records "
                f"{gap['from_name']} —{gap['relation']}→ {gap['to_name']} "
                f"({gap['status']}). Readers meet ideas in text order, so prerequisite "
                f"concepts should be realized first (§21.2 prerequisite detection)."
                + settle
            ),
            "policy_ids": [],
        })

    # 3. Unanswered objections.
    for node in nodes.values():
        if node["kind"] != "objection":
            continue
        answered = db.one(
            "SELECT id FROM concept_edges WHERE manuscript_id = ? AND to_node = ? "
            "AND relation = 'answers' AND status NOT IN ('rejected', 'retired')",
            (mid, node["id"]),
        )
        if not answered:
            candidates.append({
                "kind": "objection",
                "key": f"objection:{node['id']}",
                "intent_id": intents[0]["id"] if intents else None,
                "suggestion": f"Address the open objection '{node['name']}' — no answer is recorded in the Concept Graph.",
                "explanation": (
                    f"Objection node '{node['name']}' ({node['status']}) has no incoming "
                    f"'answers' relationship. Unanswered objections are a standing editorial "
                    f"question (§21.3)." + (f" Note: {node['notes']}" if node["notes"] else "")
                ),
                "policy_ids": [],
            })

    # Never re-propose something the author already ruled on. Matching is by
    # stable dedupe key (kind + target), not by suggestion text, which changes
    # as policies strengthen. Rejected/accepted/modified suppress forever;
    # deferred suppresses within this session only. This runs BEFORE policy
    # reminders so a suppressed suggestion doesn't consume its policy slot.
    def already_reviewed(key: str) -> bool:
        row = db.one(
            "SELECT id FROM guidance_history WHERE manuscript_id = ? "
            "AND metadata LIKE ? "
            "AND (state IN ('accepted','rejected','modified') "
            "     OR (state = 'deferred' AND session_id = ?))",
            (mid, f'%"dedupe_key": "{key}"%', session["id"]),
        )
        return row is not None

    candidates = [c for c in candidates if not already_reviewed(c["key"])]

    # 4. Reminders for learned policies (validated first, then candidates —
    #    reviewing a candidate reminder is what promotes or retires it).
    if intents:
        used = {pid for c in candidates for pid in c["policy_ids"]}
        # A policy is only reviewable once per session: supporting evidence
        # must come from independent sessions/episodes (§11.2).
        reviewed_this_session: set[str] = set()
        for row in db.all(
            "SELECT metadata FROM guidance_history WHERE session_id = ? "
            "AND kind = 'policy_reminder' "
            "AND state IN ('accepted','rejected','modified','deferred')",
            (session["id"],),
        ):
            reviewed_this_session.update(loads(row["metadata"], {}).get("policy_ids", []))
        added = 0
        for policy in reminder_policies:
            if policy["id"] in used or policy["id"] in reviewed_this_session or added >= 3:
                continue
            if policy["status"] == "validated":
                label = "apply your policy"
                basis = "Validated policy"
            else:
                label = "does this candidate policy apply?"
                basis = "Candidate policy — accepting strengthens it, rejecting weakens it"
            candidates.append({
                "kind": "policy_reminder",
                "key": f"policy_reminder:{policy['id']}",
                "intent_id": intents[0]["id"],
                "suggestion": f"While pursuing this intent, {label}: \"{policy['statement']}\"",
                "explanation": (
                    f"{basis} (confidence {policy['confidence']}, "
                    f"{policy['supporting']} supporting / {policy['contradicting']} "
                    f"contradicting reviews; source: {policy['source']})."
                ),
                "policy_ids": [policy["id"]],
            })
            added += 1

    # Intent-linked suggestions first; standing structural findings after.
    candidates.sort(key=lambda c: 0 if c["kind"] in INTENT_KINDS else 1)
    candidates = candidates[:MAX_SUGGESTIONS]

    # Optional LLM enrichment: draft text for the first bridge suggestion.
    if llm and candidates and candidates[0]["kind"] == "bridge":
        # Drafts arrive in the target file's ratified register: when the
        # active intent names a file, its effective style guide rides along.
        style_context = ""
        target_file = next(
            (m.group(0) for i in intents
             for m in [re.search(r"[\w./-]+\.md", i["statement"])] if m),
            None,
        )
        if target_file:
            from .styles import render as render_style

            style_context = render_style(db, mid, target_file)
        draft = llm.complete(
            "You are an editorial collaborator for a philosophy manuscript. "
            "Draft a short bridge paragraph. Reply with the paragraph only. "
            "Format any mathematics as MathJax: inline math in $...$, display "
            "equations in $$...$$."
            + (f"\n{style_context}" if style_context else ""),
            candidates[0]["explanation"],
        )
        if draft:
            candidates[0]["explanation"] += f" Draft to consider:\n    {draft.strip()}"

    rows: list[dict] = []
    if not candidates:
        row = ko_fields("gd")
        row.update(
            manuscript_id=mid, session_id=session["id"],
            intent_id=intents[0]["id"] if intents else None,
            batch_id=batch_id, batch_index=0, kind="abstention",
            suggestion="No suggestion offered.",
            explanation=(
                "Insufficient evidence for an editorially justified recommendation: "
                "no unrealized declared concepts match the active intent, no prerequisite "
                "gaps or unanswered objections were found, and no validated policies apply. "
                "Abstention preserves trust better than an unfounded suggestion (§11.5)."
            ),
            state="proposed",
        )
        db.insert("guidance_history", row)
        return [row]

    for index, cand in enumerate(candidates, start=1):
        row = ko_fields("gd")
        row.update(
            manuscript_id=mid, session_id=session["id"], intent_id=cand["intent_id"],
            batch_id=batch_id, batch_index=index, kind=cand["kind"],
            suggestion=cand["suggestion"], explanation=cand["explanation"],
            state="proposed",
        )
        row["metadata"] = json.dumps(
            {"policy_ids": cand["policy_ids"], "dedupe_key": cand["key"]}
        )
        db.insert("guidance_history", row)
        rows.append(row)
    return rows
