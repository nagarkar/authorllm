"""Session-Opening Learning Briefing (RFC AuthorLM §20.3, §19.3).

The primary interface between the engine and the author: what was learned
since the last session, what the engine is uncertain about, and what it
believes should be investigated next. No strengthening or weakening of
editorial knowledge happens silently.
"""

from __future__ import annotations

from .concepts import unrealized_with_dependents
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

    # Belief deltas: evidence recorded since the last session, per belief.
    belief_changes = []
    for row in db.all(
        "SELECT supports_belief, "
        "  SUM(CASE WHEN signal IN ('accepted','modified') THEN 1 ELSE 0 END) AS plus, "
        "  SUM(CASE WHEN signal = 'rejected' THEN 1 ELSE 0 END) AS minus "
        "FROM evidence WHERE manuscript_id = ? AND created_at > ? "
        "  AND supports_belief IS NOT NULL "
        "GROUP BY supports_belief",
        (manuscript_id, since),
    ):
        belief = db.one(
            "SELECT * FROM editorial_beliefs WHERE id = ?", (row["supports_belief"],)
        )
        if belief:
            belief_changes.append({
                "statement": belief["statement"],
                "status": belief["status"],
                "confidence": belief["confidence"],
                "supporting": belief["supporting"],
                "contradicting": belief["contradicting"],
                "delta_supporting": row["plus"],
                "delta_contradicting": row["minus"],
            })

    new_beliefs = [
        dict(p) for p in db.all(
            "SELECT * FROM editorial_beliefs WHERE manuscript_id = ? "
            "AND created_at > ? AND status != 'retired'",
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

    inferred_edges = [dict(edge) for edge in db.all(
        "SELECT ce.*, fn.name AS from_name, tn.name AS to_name "
        "FROM concept_edges ce "
        "JOIN concept_nodes fn ON fn.id = ce.from_node "
        "JOIN concept_nodes tn ON tn.id = ce.to_node "
        "WHERE ce.manuscript_id = ? AND ce.status = 'inferred'",
        (manuscript_id,),
    )]

    contradictions = [
        dict(r) for r in db.all(
            "SELECT er.*, gh.suggestion FROM editorial_reviews er "
            "JOIN guidance_history gh ON gh.id = er.guidance_id "
            "WHERE er.manuscript_id = ? AND er.created_at > ? AND er.decision = 'rejected'",
            (manuscript_id, since),
        )
    ]

    outstanding_questions = []
    for belief in db.all(
        "SELECT * FROM editorial_beliefs WHERE manuscript_id = ? AND status != 'retired'",
        (manuscript_id,),
    ):
        for question in loads(belief["outstanding_questions"], []):
            outstanding_questions.append(
                {"belief_id": belief["id"], "statement": belief["statement"], "question": question}
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

    # Profiles on record (declared context — market, positioning): one
    # line so every session knows they exist. Content stays on demand
    # via get_profile.
    from pathlib import Path

    profiles = []
    ms_row = db.one("SELECT path FROM manuscripts WHERE id = ?",
                    (manuscript_id,))
    if ms_row:
        root = Path(ms_row["path"]) / "_profiles"
        if root.exists():
            profiles = [
                {"key": f.stem,
                 "words": len(f.read_text(encoding="utf-8").split())}
                for f in sorted(root.glob("*.md"))]

    # Non-author-sourced material announces itself (critique-pass design
    # §2): critic input must never silently blend into the author's record.
    from . import critique as crit

    critique_pending = [
        {"name": r["name"],
         "intents_proposed": r["intents"]["proposed"],
         "elements_proposed": r["elements"]["proposed"]}
        for r in crit.status(db, manuscript_id)
        if r["intents"]["proposed"] or r["elements"]["proposed"]]

    return {
        "since": since,
        "critique_pending": critique_pending,
        "profiles": profiles,
        "belief_changes": belief_changes,
        "new_beliefs": new_beliefs,
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
            "beliefs_changed": len(belief_changes),
            "beliefs_seeded": len(new_beliefs),
            "concepts_realized": len(realized_concepts),
            # Displayed edges include everything still awaiting confirmation;
            # velocity counts only edges inferred during this period.
            "edges_inferred": sum(1 for e in inferred_edges if e["created_at"] > since),
        },
    }
