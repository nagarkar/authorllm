"""Learning Engine (RFC AuthorLM §17.8, Chapter 8).

Editorial policies are discovered from evidence, never authored directly:
review outcomes strengthen or weaken the policies a suggestion relied on,
and explained rejections/modifications seed *candidate* policies — the
author contributed evidence, not policy edits (Appendix A.10).

Promotion is conservative: false negatives are preferable to false
positives (Common Core §8.5).
"""

from __future__ import annotations

import json

from .db import Database, ko_fields, loads
from .llm import LLMClient

DISTILL_SYSTEM = (
    "You distill an author's review explanation into a reusable editorial "
    "policy. Reply with one short normative statement in the imperative, at "
    "most 15 words, no quotes, no preamble. If the explanation is too "
    "situation-specific to generalize, reply with exactly: NONE"
)

VALIDATE_MIN_SUPPORT = 3
VALIDATE_MIN_CONFIDENCE = 0.7
DEMOTE_CONFIDENCE = 0.55
RETIRE_CONFIDENCE = 0.35
RETIRE_MIN_OBSERVATIONS = 4


def _confidence(supporting: int, contradicting: int) -> float:
    # Laplace-smoothed proportion of supporting evidence.
    return round((supporting + 1) / (supporting + contradicting + 2), 3)


def _lifecycle_status(status: str, supporting: int, contradicting: int, conf: float) -> str:
    if status == "retired":
        return status
    total = supporting + contradicting
    if conf < RETIRE_CONFIDENCE and total >= RETIRE_MIN_OBSERVATIONS:
        return "retired"
    if status == "validated" and conf < DEMOTE_CONFIDENCE:
        return "candidate"
    if status == "candidate" and supporting >= VALIDATE_MIN_SUPPORT and conf >= VALIDATE_MIN_CONFIDENCE:
        return "validated"
    return status


def reinforce_policy(db: Database, policy_id: str, signal: str, question: str | None = None) -> dict:
    """Apply one piece of review evidence to a policy's belief record."""
    policy = db.one("SELECT * FROM editorial_policies WHERE id = ?", (policy_id,))
    if policy is None:
        return {}
    supporting = policy["supporting"] + (1 if signal in ("accepted", "modified") else 0)
    contradicting = policy["contradicting"] + (1 if signal == "rejected" else 0)
    conf = _confidence(supporting, contradicting)
    status = _lifecycle_status(policy["status"], supporting, contradicting, conf)
    questions = loads(policy["outstanding_questions"], [])
    if question and question not in questions:
        questions.append(question)
    changes = {
        "supporting": supporting,
        "contradicting": contradicting,
        "confidence": conf,
        "status": status,
        "outstanding_questions": json.dumps(questions),
    }
    db.update("editorial_policies", policy_id, changes)
    return {**dict(policy), **changes}


def seed_candidate_policy(
    db: Database, manuscript_id: str, statement: str, source: str,
    llm: LLMClient | None = None,
) -> dict | None:
    """An explained review outcome seeds (or reinforces) a candidate policy.
    With an LLM available, the raw explanation is distilled into a normative
    policy statement (the verbatim explanation is kept in metadata); the LLM
    may also judge the explanation too situation-specific to generalize, in
    which case no policy is seeded (returns None) — the explanation still
    persists as review evidence either way."""
    original = statement
    if llm:
        distilled = llm.complete(DISTILL_SYSTEM, statement)
        if distilled:
            distilled = distilled.strip().strip('"')
            if distilled.upper() == "NONE":
                return None
            statement = distilled
    existing = db.one(
        "SELECT * FROM editorial_policies WHERE manuscript_id = ? AND lower(statement) = lower(?)",
        (manuscript_id, statement),
    )
    if existing:
        if existing["status"] == "retired":
            # A retired policy stays retired, but fresh supporting evidence
            # files a revival proposal rather than vanishing.
            from . import proposals

            proposals.create(
                db, manuscript_id, "policy_revival", existing["id"],
                {"statement": existing["statement"], "new_explanation": original},
                source="review-explanation",
            )
            return {"kind": "revival_proposal", "statement": existing["statement"]}
        return reinforce_policy(db, existing["id"], "accepted")
    row = ko_fields("pol")
    if statement != original:
        row["metadata"] = json.dumps({"original_explanation": original})
    row.update(
        manuscript_id=manuscript_id,
        statement=statement,
        status="candidate",
        confidence=_confidence(1, 0),
        supporting=1,
        contradicting=0,
        outstanding_questions="[]",
        source=source,
    )
    db.insert("editorial_policies", row)
    return row


def _curation_evidence(db: Database, manuscript_id: str, signal: str,
                       target: str, policy_id: str) -> None:
    ev = ko_fields("ev")
    ev.update(
        manuscript_id=manuscript_id, episode_id=None,
        evidence_type="policy_curation", signal=signal,
        target=target[:200], supports_policy=policy_id, weight="high",
    )
    db.insert("evidence", ev)


def retire_policy(db: Database, manuscript_id: str, policy: dict,
                  reason: str) -> dict:
    """Author-initiated retirement: the policy is wrong or unwanted. Kept
    for history; seed_candidate_policy treats retired statements as banned
    (fresh support files a revival proposal instead)."""
    meta = loads(policy["metadata"], {})
    meta["curation"] = {"action": "retired", "reason": reason}
    db.update("editorial_policies", policy["id"],
              {"status": "retired", "metadata": json.dumps(meta)})
    _curation_evidence(db, manuscript_id, "retired",
                       f"{policy['statement']} — {reason}", policy["id"])
    return {"id": policy["id"], "statement": policy["statement"],
            "status": "retired"}


def merge_policies(db: Database, manuscript_id: str, duplicate: dict,
                   canonical: dict, reason: str | None = None) -> dict:
    """Fold a duplicate policy's belief record into the canonical one and
    retire the duplicate. The duplicate's statement stays in the table
    (retired), so it remains banned from re-seeding."""
    supporting = canonical["supporting"] + duplicate["supporting"]
    contradicting = canonical["contradicting"] + duplicate["contradicting"]
    conf = _confidence(supporting, contradicting)
    status = _lifecycle_status(canonical["status"], supporting,
                               contradicting, conf)
    questions = loads(canonical["outstanding_questions"], [])
    for question in loads(duplicate["outstanding_questions"], []):
        if question not in questions:
            questions.append(question)
    changes = {
        "supporting": supporting, "contradicting": contradicting,
        "confidence": conf, "status": status,
        "outstanding_questions": json.dumps(questions),
    }
    db.update("editorial_policies", canonical["id"], changes)
    meta = loads(duplicate["metadata"], {})
    meta["curation"] = {"action": "merged", "into": canonical["id"]}
    if reason:
        meta["curation"]["reason"] = reason
    db.update("editorial_policies", duplicate["id"],
              {"status": "retired", "metadata": json.dumps(meta)})
    _curation_evidence(
        db, manuscript_id, "merged",
        f"{duplicate['statement']} ⇒ {canonical['statement']}",
        canonical["id"])
    return {**dict(canonical), **changes,
            "merged": duplicate["statement"]}


def convert_policy(db: Database, manuscript_id: str, policy: dict,
                   element: dict, reason: str | None = None) -> dict:
    """Retire a policy whose substance now lives as a style element. The
    element is created by the caller (style machinery needs the manuscript
    record); this records the linkage and the ban."""
    meta = loads(policy["metadata"], {})
    meta["curation"] = {"action": "converted", "style_element": element["id"]}
    if reason:
        meta["curation"]["reason"] = reason
    db.update("editorial_policies", policy["id"],
              {"status": "retired", "metadata": json.dumps(meta)})
    _curation_evidence(
        db, manuscript_id, "converted",
        f"{policy['statement']} ⇒ style element {element['id']}",
        policy["id"])
    return {"id": policy["id"], "statement": policy["statement"],
            "status": "retired", "style_element": element["id"]}


def record_review(
    db: Database,
    manuscript_id: str,
    guidance: dict,
    decision: str,
    explanation: str | None,
    episode_id: str | None,
    llm: LLMClient | None = None,
) -> dict:
    """Record an author review of one suggestion: update guidance state,
    persist the review and evidence, and update policy beliefs."""
    db.update("guidance_history", guidance["id"], {"state": decision})

    review = ko_fields("rv")
    review.update(
        manuscript_id=manuscript_id,
        guidance_id=guidance["id"],
        decision=decision,
        explanation=explanation,
    )
    db.insert("editorial_reviews", review)

    policy_ids = loads(guidance["metadata"], {}).get("policy_ids", [])
    updated_policies = []
    for policy_id in policy_ids:
        question = None
        if decision in ("rejected", "modified") and explanation:
            question = f"Review of '{guidance['suggestion'][:60]}': {explanation}"
        updated = reinforce_policy(db, policy_id, decision, question)
        if updated:
            updated_policies.append(updated)

        ev = ko_fields("ev")
        ev.update(
            manuscript_id=manuscript_id,
            episode_id=episode_id,
            evidence_type="author_review",
            signal=decision,
            target=guidance["suggestion"][:120],
            supports_policy=policy_id,
            # An explained decision is the highest-quality evidence (§19.11).
            weight="high" if explanation else "medium",
        )
        db.insert("evidence", ev)

    if not policy_ids:
        ev = ko_fields("ev")
        ev.update(
            manuscript_id=manuscript_id,
            episode_id=episode_id,
            evidence_type="author_review",
            signal=decision,
            target=guidance["suggestion"][:120],
            supports_policy=None,
            weight="high" if explanation else "medium",
        )
        db.insert("evidence", ev)

    seeded = None
    if decision in ("rejected", "modified") and explanation:
        seeded = seed_candidate_policy(
            db, manuscript_id, explanation, source="review-explanation", llm=llm
        )

    return {"review": review, "policies": updated_policies, "seeded_policy": seeded}
