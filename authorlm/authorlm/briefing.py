"""Session-Opening Learning Briefing (RFC AuthorLM §20.3, §19.3).

The primary interface between the engine and the author: what was learned
since the last session, what the engine is uncertain about, and what it
believes should be investigated next. No strengthening or weakening of
editorial knowledge happens silently.
"""

from __future__ import annotations

from .concepts import node_name, unrealized_with_dependents
from .db import Database, loads

EPOCH = "1970-01-01T00:00:00Z"


def _briefing_cutoff(db: Database, manuscript_id: str) -> str:
    """Start of the most recent ended session: the briefing recaps what was
    learned during and since the author's last visit (RFC A.14)."""
    row = db.one(
        "SELECT started_at FROM sessions WHERE manuscript_id = ? AND status = 'ended' "
        "ORDER BY ended_at DESC LIMIT 1",
        (manuscript_id,),
    )
    return row["started_at"] if row else EPOCH


def build_briefing(db: Database, manuscript_id: str, since: str | None = None) -> dict:
    if since is None:
        since = _briefing_cutoff(db, manuscript_id)

    # Policy deltas: evidence recorded since the last session, per policy.
    policy_changes = []
    for row in db.all(
        "SELECT supports_policy, "
        "  SUM(CASE WHEN signal IN ('accepted','modified') THEN 1 ELSE 0 END) AS plus, "
        "  SUM(CASE WHEN signal = 'rejected' THEN 1 ELSE 0 END) AS minus "
        "FROM evidence WHERE manuscript_id = ? AND created_at > ? "
        "  AND supports_policy IS NOT NULL "
        "GROUP BY supports_policy",
        (manuscript_id, since),
    ):
        policy = db.one(
            "SELECT * FROM editorial_policies WHERE id = ?", (row["supports_policy"],)
        )
        if policy:
            policy_changes.append({
                "statement": policy["statement"],
                "status": policy["status"],
                "confidence": policy["confidence"],
                "supporting": policy["supporting"],
                "contradicting": policy["contradicting"],
                "delta_supporting": row["plus"],
                "delta_contradicting": row["minus"],
            })

    new_policies = [
        dict(p) for p in db.all(
            "SELECT * FROM editorial_policies WHERE manuscript_id = ? AND created_at > ?",
            (manuscript_id, since),
        )
    ]

    realized_concepts = []
    for node in db.all(
        "SELECT * FROM concept_nodes WHERE manuscript_id = ? AND status = 'realized'",
        (manuscript_id,),
    ):
        meta = loads(node["metadata"], {})
        if meta.get("realized_at", EPOCH) > since:
            realized_concepts.append(dict(node))

    from .proposals import describe, open_proposals

    proposals = [
        {**row, "summary": describe(row)[0]}
        for row in open_proposals(db, manuscript_id)
    ]

    unconfirmed_concepts = []
    for node in db.all(
        "SELECT * FROM concept_nodes WHERE manuscript_id = ? AND status != 'retired'",
        (manuscript_id,),
    ):
        meta = loads(node["metadata"], {})
        if meta.get("origin") == "extracted" and not meta.get("confirmed"):
            unconfirmed_concepts.append(dict(node))

    inferred_edges = [
        {
            **dict(edge),
            "from_name": node_name(db, edge["from_node"]),
            "to_name": node_name(db, edge["to_node"]),
        }
        for edge in db.all(
            "SELECT * FROM concept_edges WHERE manuscript_id = ? AND status = 'inferred'",
            (manuscript_id,),
        )
    ]

    contradictions = [
        dict(r) for r in db.all(
            "SELECT er.*, gh.suggestion FROM editorial_reviews er "
            "JOIN guidance_history gh ON gh.id = er.guidance_id "
            "WHERE er.manuscript_id = ? AND er.created_at > ? AND er.decision = 'rejected'",
            (manuscript_id, since),
        )
    ]

    outstanding_questions = []
    for policy in db.all(
        "SELECT * FROM editorial_policies WHERE manuscript_id = ? AND status != 'retired'",
        (manuscript_id,),
    ):
        for question in loads(policy["outstanding_questions"], []):
            outstanding_questions.append(
                {"policy_id": policy["id"], "statement": policy["statement"], "question": question}
            )

    active_intents = [
        dict(i) for i in db.all(
            "SELECT * FROM declared_intents WHERE manuscript_id = ? AND status = 'active'",
            (manuscript_id,),
        )
    ]

    focus_areas = unrealized_with_dependents(db, manuscript_id)

    # TOC completeness: the reading order is only authoritative when every
    # active file is listed (structure must cover the whole manuscript).
    toc_unlisted: list[str] = []
    latest = db.one(
        "SELECT files FROM manuscript_versions WHERE manuscript_id = ? "
        "ORDER BY version_no DESC LIMIT 1",
        (manuscript_id,),
    )
    if latest:
        from .structure import TOC_FILENAME, reading_order

        files = loads(latest["files"], {})
        if TOC_FILENAME in files:
            _, toc_unlisted = reading_order(files)

    evidence_count = db.one(
        "SELECT COUNT(*) AS n FROM evidence WHERE manuscript_id = ? AND created_at > ?",
        (manuscript_id, since),
    )["n"]

    return {
        "since": since,
        "policy_changes": policy_changes,
        "new_policies": new_policies,
        "realized_concepts": realized_concepts,
        "unconfirmed_concepts": unconfirmed_concepts,
        "proposals": proposals,
        "inferred_edges": inferred_edges,
        "contradictions": contradictions,
        "outstanding_questions": outstanding_questions,
        "active_intents": active_intents,
        "focus_areas": focus_areas,
        "toc_unlisted": toc_unlisted,
        # Learning velocity (§24.4): understanding gained, not words written.
        "learning_velocity": {
            "new_evidence": evidence_count,
            "policies_changed": len(policy_changes),
            "policies_seeded": len(new_policies),
            "concepts_realized": len(realized_concepts),
            # Displayed edges include everything still awaiting confirmation;
            # velocity counts only edges inferred during this period.
            "edges_inferred": sum(1 for e in inferred_edges if e["created_at"] > since),
        },
    }
