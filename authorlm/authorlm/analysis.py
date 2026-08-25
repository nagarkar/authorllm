"""Episode Analyzer — learn editorial judgment from the author's own edits
(RFC Common Core §3.5–3.6 decision identification; AuthorLM §17.8, §19.5).

Until now beliefs were learned only from what the author *said* about
suggestions. This module closes the RFC's central loop: after an episode
ends, the LLM performs retrospective inference over the observed
transitions — what discrete editorial decisions do they show, and does any
decision demonstrate a generalizable pattern? Patterns enter the ordinary
belief machinery as candidates (source 'episode-analysis') and strengthen
across independent episodes, exactly like review evidence.

Everything inferred here is a hypothesis: decisions are stored on the
episode (replayable, inspectable), patterns become candidate beliefs the
author can watch, reject, or see validated in the briefing. Declared
knowledge always outranks this (Common Core §7.7).
"""

from __future__ import annotations

import json

from . import beliefs as bel
from .db import Database, ko_fields, loads
from .llm import LLMClient

ANALYSIS_SYSTEM = (
    "You are an editorial analyst reconstructing an author's editorial "
    "decisions from one writing episode in a philosophy manuscript. You are "
    "given the author's declared intent (if any) and the observed text "
    "changes. Infer: (1) the discrete editorial DECISIONS the changes show — "
    "short factual descriptions of what the author did editorially (e.g. "
    "'opened the section with a lived example before the formal definition', "
    "'moved the objection ahead of the conclusion'), not summaries of the "
    "content; and (2) for each decision, a generalizable editorial PATTERN "
    "phrased as a short normative rule (imperative, at most 15 words) that "
    "could guide future writing — or null if the decision is too "
    "situation-specific to generalize. Only infer patterns the changes "
    "actually demonstrate; when in doubt, use null. Analyze the PROSE — "
    "ignore file-management mechanics entirely (file names, file creation, "
    "numbering); those are not editorial decisions. Reply with JSON only: "
    '{"decisions": [{"action": str, "pattern": str | null}], "outcome": str} '
    "where outcome is one short sentence on what the episode accomplished."
)

MAX_TRANSITIONS = 20
MAX_SNIPPET = 500
MAX_DECISIONS = 8


NEW_FILE_SNIPPET = 1500


def _serialize_transitions(db: Database, episode: dict) -> tuple[str, int]:
    ids = loads(episode["transition_ids"], [])
    lines = []
    for tid in ids[:MAX_TRANSITIONS]:
        t = db.one("SELECT * FROM editorial_transitions WHERE id = ?", (tid,))
        if not t:
            continue
        detail = loads(t["detail"], {})
        lines.append(f"--- {t['kind']} at {t['location']} ---")
        if t["kind"] == "file_added":
            # The transition stores only a count; the analyzer needs the
            # prose itself, from the version this transition produced.
            version = db.one(
                "SELECT files FROM manuscript_versions WHERE id = ?",
                (t["version_after"],),
            )
            content = (loads(version["files"], {}) if version else {}).get(t["location"], "")
            if content:
                lines.append(f"NEW TEXT: {content[:NEW_FILE_SNIPPET]}")
            else:
                lines.append(t["summary"])
            continue
        if detail.get("old_text"):
            lines.append(f"BEFORE: {detail['old_text'][:MAX_SNIPPET]}")
        if detail.get("new_text"):
            lines.append(f"AFTER: {detail['new_text'][:MAX_SNIPPET]}")
        if not detail.get("old_text") and not detail.get("new_text"):
            lines.append(t["summary"])
    return "\n".join(lines), len(ids)


def _pending_episodes(db: Database, manuscript_id: str) -> list[dict]:
    pending = []
    for episode in db.all(
        "SELECT * FROM editorial_episodes WHERE manuscript_id = ? AND status = 'closed' "
        "ORDER BY created_at",
        (manuscript_id,),
    ):
        if loads(episode["metadata"], {}).get("analysis"):
            continue
        if not loads(episode["transition_ids"], []):
            continue
        pending.append(dict(episode))
    return pending


def _precedent_from(db: Database, episode: dict, label: str) -> dict | None:
    meta = loads(episode["metadata"], {})
    decisions = (meta.get("analysis") or {}).get("decisions") or []
    actions = [d["action"] for d in decisions if d.get("action")][:3]
    if not actions:
        return None
    return {
        "label": label,
        "actions": actions,
        "outcome": (meta.get("analysis") or {}).get("outcome"),
        "episode_id": episode["id"],
    }


def find_precedents(db: Database, manuscript_id: str, concept_id: str,
                    limit: int = 2) -> list[dict]:
    """Graph-guided episodic retrieval (RFC §21.8, §19.7): for a concept
    about to be introduced, find how the author handled its already-realized
    neighbors — the analyzed decision sequences of the episodes that
    realized them. Falls back to the most recent analyzed episodes when the
    graph offers nothing."""
    precedents: list[dict] = []
    seen_episodes: set[str] = set()

    edges = db.all(
        "SELECT * FROM concept_edges WHERE manuscript_id = ? "
        "AND status NOT IN ('rejected', 'retired') "
        "AND (from_node = ? OR to_node = ?) "
        "ORDER BY CASE WHEN relation = 'co_occurs' THEN 1 ELSE 0 END",
        (manuscript_id, concept_id, concept_id),
    )

    neighbor_ids = {
        edge["to_node"] if edge["from_node"] == concept_id else edge["from_node"]
        for edge in edges
    }
    neighbors: dict[str, dict] = {}
    if neighbor_ids:
        placeholders = ",".join("?" for _ in neighbor_ids)
        neighbors = {
            row["id"]: row
            for row in db.all(
                f"SELECT * FROM concept_nodes WHERE id IN ({placeholders})",
                tuple(neighbor_ids),
            )
        }

    version_ids = {
        loads(node["metadata"], {}).get("realized_version")
        for node in neighbors.values() if node["status"] == "realized"
    }
    version_ids.discard(None)

    # Map realized_version -> closed episode, in two batched queries instead
    # of one LIKE-scan of editorial_episodes per candidate transition.
    transitions_by_version: dict[str, list[str]] = {}
    if version_ids:
        placeholders = ",".join("?" for _ in version_ids)
        for transition in db.all(
            f"SELECT id, version_after FROM editorial_transitions "
            f"WHERE manuscript_id = ? AND version_after IN ({placeholders})",
            (manuscript_id, *version_ids),
        ):
            transitions_by_version.setdefault(
                transition["version_after"], []).append(transition["id"])

    episode_by_transition: dict[str, dict] = {}
    if transitions_by_version:
        for episode in db.all(
            "SELECT * FROM editorial_episodes WHERE manuscript_id = ? "
            "AND status = 'closed'",
            (manuscript_id,),
        ):
            for transition_id in loads(episode["transition_ids"], []):
                episode_by_transition[transition_id] = dict(episode)

    for edge in edges:
        if len(precedents) >= limit:
            break
        other = edge["to_node"] if edge["from_node"] == concept_id else edge["from_node"]
        neighbor = neighbors.get(other)
        if not neighbor or neighbor["status"] != "realized":
            continue
        version_id = loads(neighbor["metadata"], {}).get("realized_version")
        if not version_id:
            continue
        episode = None
        for transition_id in transitions_by_version.get(version_id, []):
            episode = episode_by_transition.get(transition_id)
            if episode:
                break
        if not episode or episode["id"] in seen_episodes:
            continue
        precedent = _precedent_from(
            db, episode, f"when you worked on '{neighbor['name']}'"
        )
        if precedent:
            seen_episodes.add(episode["id"])
            precedents.append(precedent)

    if not precedents:
        for episode in db.all(
            "SELECT * FROM editorial_episodes WHERE manuscript_id = ? "
            "AND status = 'closed' ORDER BY created_at DESC LIMIT 10",
            (manuscript_id,),
        ):
            if len(precedents) >= limit:
                break
            statement = "(no declared intent)"
            if episode["intent_id"]:
                intent = db.one("SELECT statement FROM declared_intents WHERE id = ?",
                                (episode["intent_id"],))
                if intent:
                    statement = intent["statement"]
            precedent = _precedent_from(
                db, dict(episode), f"in the episode '{statement}'"
            )
            if precedent:
                precedents.append(precedent)

    return precedents


def analyze_pending(db: Database, manuscript: dict, llm: LLMClient,
                    progress=None) -> list[dict]:
    """Analyze every closed, unanalyzed episode that has transitions.
    Returns one summary dict per analyzed episode (empty if none pending or
    the LLM is unavailable). `progress(index, total, intent_statement)` is
    called before each LLM call so surfaces can show liveness — analysis of
    a large episode can take a while."""
    mid = manuscript["id"]
    pending = _pending_episodes(db, mid)
    if not pending or not llm.enabled:
        return []

    summaries = []
    for index, episode in enumerate(pending, start=1):
        intent_statement = "(no declared intent)"
        if episode["intent_id"]:
            intent = db.one(
                "SELECT statement FROM declared_intents WHERE id = ?",
                (episode["intent_id"],),
            )
            if intent:
                intent_statement = intent["statement"]
        if progress:
            progress(index, len(pending), intent_statement)
        transitions_text, transition_count = _serialize_transitions(db, episode)
        if not transitions_text.strip():
            continue
        result = llm.complete_json(
            ANALYSIS_SYSTEM,
            f"DECLARED INTENT: {intent_statement}\n\nOBSERVED CHANGES:\n{transitions_text}",
        )
        if not isinstance(result, dict):
            continue  # LLM unavailable/unusable — stays pending for later

        raw_decisions = result.get("decisions")
        if not isinstance(raw_decisions, list):
            # null / wrong-shaped JSON must not crash mid-analyze (key
            # present with null bypasses dict.get's default).
            raw_decisions = []

        decisions = []
        belief_notes = []
        seen_patterns: set[str] = set()
        for item in raw_decisions[:MAX_DECISIONS]:
            if not isinstance(item, dict) or not str(item.get("action", "")).strip():
                continue
            action = str(item["action"]).strip()[:300]
            pattern = item.get("pattern")
            pattern = str(pattern).strip()[:200] if pattern else None
            decisions.append({"action": action, "pattern": pattern})
            if not pattern:
                continue
            # One episode is one observation: two decisions in this same
            # reply generalizing to the identical pattern must not seed or
            # reinforce a belief twice (INV-2a). Scoped to this reply only —
            # cross-episode reinforcement is unaffected.
            normalized_pattern = " ".join(pattern.lower().split())
            if normalized_pattern in seen_patterns:
                continue
            seen_patterns.add(normalized_pattern)
            seeded = bel.seed_candidate_belief(
                db, mid, pattern, source="episode-analysis", llm=None
            )
            if seeded is None:
                continue
            if seeded.get("kind") == "revival_proposal":
                belief_notes.append((seeded["statement"], "revival proposal filed"))
                continue
            note = "reinforced" if seeded.get("supporting", 1) > 1 else "candidate seeded"
            belief_notes.append((seeded["statement"], note))
            ev = ko_fields("ev")
            ev.update(
                manuscript_id=mid, episode_id=episode["id"],
                evidence_type="episode_analysis", signal="pattern",
                target=pattern[:200], supports_belief=seeded.get("id"),
                # Machine-inferred behavior evidence: real, but below the
                # author's explicit word (declared > inferred, §7.7).
                weight="medium",
            )
            db.insert("evidence", ev)

        outcome = str(result.get("outcome") or "").strip()[:300] or None
        meta = loads(episode["metadata"], {})
        meta["analysis"] = {"decisions": decisions, "outcome": outcome}
        changes = {"metadata": json.dumps(meta)}
        if outcome and not episode["outcome"]:
            changes["outcome"] = outcome
        db.update("editorial_episodes", episode["id"], changes)

        summaries.append({
            "episode_id": episode["id"],
            "intent": intent_statement,
            "transitions": transition_count,
            "decisions": decisions,
            "outcome": outcome,
            "beliefs": belief_notes,
        })
    return summaries
