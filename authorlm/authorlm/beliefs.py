"""Learning Engine (RFC AuthorLM §17.8, Chapter 8).

Editorial beliefs are discovered from evidence, never authored directly:
review outcomes strengthen or weaken the beliefs a suggestion relied on,
and explained rejections/modifications seed *candidate* beliefs — the
author contributed evidence, not belief edits (Appendix A.10).

Promotion is conservative: false negatives are preferable to false
positives (Common Core §8.5).
"""

from __future__ import annotations

import json
from pathlib import Path

from .db import Database, ko_fields, loads
from .llm import LLMClient

PROMPTS_DIR = Path(__file__).parent / "prompts"

# Registered in prompt_registry as "belief-distill" / "margin-distill".
def belief_distill_system() -> str:
    return (PROMPTS_DIR / "belief-distill.md").read_text(encoding="utf-8")


def margin_distill_system() -> str:
    return (PROMPTS_DIR / "margin-distill.md").read_text(encoding="utf-8")


# How many beliefs the distiller may be shown as match candidates. Unlike
# the recency windows this design replaced, the cap is on BELIEFS, not on
# evidence: with matching working, the belief count stays small because
# fresh evidence reinforces rather than spawns. If this cap ever bites,
# matching has regressed — `menu_truncated` in the return says so.
BELIEF_MENU_LIMIT = 60


def live_beliefs(db: Database, manuscript_id: str, source: str | None = None,
                 limit: int = BELIEF_MENU_LIMIT) -> list[dict]:
    """Beliefs the distiller may match a fresh explanation against, best
    supported first. Retired ones are INCLUDED and marked: a paraphrase of
    a retired belief must reach the author as a revival proposal, not slip
    back in as a new belief under different wording."""
    sql = ("SELECT * FROM editorial_beliefs WHERE manuscript_id = ?")
    args: list = [manuscript_id]
    if source:
        sql += " AND source = ?"
        args.append(source)
    sql += " ORDER BY (status = 'retired'), supporting DESC, created_at LIMIT ?"
    args.append(limit)
    return [dict(r) for r in db.all(sql, tuple(args))]


def belief_menu(rows: list[dict]) -> str:
    if not rows:
        return "BELIEFS ON RECORD: (none yet — every explanation is NEW)"
    lines = []
    for r in rows:
        line = f"  {r['id']} | {r['statement']}"
        if r["status"] == "retired":
            line += " [retired]"
        example = (loads(r.get("metadata"), {}) or {}).get("example")
        if example:
            # Matching against a bare statement is how a specific reason gets
            # folded into a vague rule; the example is what makes the
            # comparison concrete.
            line += f"\n      e.g. {example[:160]}"
        lines.append(line)
    return "BELIEFS ON RECORD (match one, or reply NEW):\n" + "\n".join(lines)


def parse_distiller(reply: str | None,
                    valid_ids: set[str]) -> tuple[str, str | None, str | None]:
    """(verdict, statement_or_id, example) where verdict is none | match | new.

    An unparseable or unknown-id reply is treated as NONE — declining is the
    default posture, so a confused model declines rather than invents.

    A NEW with no EXAMPLE is also refused. That is the structural guard
    against platitudes: a rule whose author cannot point at the case that
    produced it is a rule generalized past its evidence, and vague rules are
    the ones that win, because they match everything.
    """
    if not reply:
        return "none", None, None
    text = reply.strip().strip('"')
    if text.upper().startswith("NONE"):
        return "none", None, None
    lines = text.splitlines()
    for line in lines:
        if line.strip().upper().startswith("MATCH:"):
            bid = line.split(":", 1)[1].strip()
            return ("match", bid, None) if bid in valid_ids else ("none", None, None)
    statement = example = None
    for line in lines:
        stripped = line.strip()
        if stripped.upper().startswith("NEW:"):
            statement = stripped.split(":", 1)[1].strip().strip('"')
        elif stripped.upper().startswith("EXAMPLE:"):
            example = stripped.split(":", 1)[1].strip().strip('"')
    if not statement or not example:
        return "none", None, None
    return "new", statement, example


VALIDATE_MIN_SUPPORT = 3
# Per-source promotion bars. The two kinds of evidence are not comparable:
# a triage or review belief is distilled from explanations the author TYPED
# — deliberate, high-signal, and rare — while an episode-analysis belief is
# inferred from a diff with no explanation attached at all. Holding both to
# one bar throws away the distinction that makes written reasons valuable.
# Measured (tools/simulate_loop.py --distil): replaying 42 real dismissal
# explanations through the live distiller yields 7 beliefs, 4 of them
# validated at this bar. Growth is logarithmic — M rose 8.4x (5 -> 42) while
# N rose 2.3x (3 -> 7), against log10(42)/log10(5) = 2.32 — so belief count
# is governed by distiller matching, not by the promotion bar, and NO
# volume-adaptive threshold is needed. One was deliberately not added: it
# would mask a matching regression by lowering the bar exactly when volume
# rose, which is how such a bug becomes permanent.
VALIDATE_MIN_SUPPORT_BY_SOURCE = {
    "triage-note_update": 2,
    "triage-alias": 2,
    "margin-thread": 2,
    "review-explanation": 2,
    "critique-triage": 2,
    "episode-analysis": 3,
}
VALIDATE_MIN_CONFIDENCE = 0.7
DEMOTE_CONFIDENCE = 0.55
RETIRE_CONFIDENCE = 0.35
RETIRE_MIN_OBSERVATIONS = 4


def _confidence(supporting: int, contradicting: int) -> float:
    # Laplace-smoothed proportion of supporting evidence.
    return round((supporting + 1) / (supporting + contradicting + 2), 3)


def validate_min_support(source: str | None) -> int:
    return VALIDATE_MIN_SUPPORT_BY_SOURCE.get(source or "", VALIDATE_MIN_SUPPORT)


def _lifecycle_status(status: str, supporting: int, contradicting: int,
                      conf: float, source: str | None = None) -> str:
    if status == "retired":
        return status
    total = supporting + contradicting
    if conf < RETIRE_CONFIDENCE and total >= RETIRE_MIN_OBSERVATIONS:
        return "retired"
    if status == "validated" and conf < DEMOTE_CONFIDENCE:
        return "candidate"
    if (status == "candidate" and supporting >= validate_min_support(source)
            and conf >= VALIDATE_MIN_CONFIDENCE):
        return "validated"
    return status


def reinforce_belief(db: Database, belief_id: str, signal: str, question: str | None = None) -> dict:
    """Apply one piece of review evidence to a belief's evidence record."""
    belief = db.one("SELECT * FROM editorial_beliefs WHERE id = ?", (belief_id,))
    if belief is None:
        return {}
    supporting = belief["supporting"] + (1 if signal in ("accepted", "modified") else 0)
    contradicting = belief["contradicting"] + (1 if signal == "rejected" else 0)
    conf = _confidence(supporting, contradicting)
    status = _lifecycle_status(belief["status"], supporting, contradicting,
                               conf, belief["source"])
    questions = loads(belief["outstanding_questions"], [])
    if question and question not in questions:
        questions.append(question)
    changes = {
        "supporting": supporting,
        "contradicting": contradicting,
        "confidence": conf,
        "status": status,
        "outstanding_questions": json.dumps(questions),
    }
    db.update("editorial_beliefs", belief_id, changes)
    return {**dict(belief), **changes}


def seed_candidate_belief(
    db: Database, manuscript_id: str, statement: str, source: str,
    llm: LLMClient | None = None,
) -> dict | None:
    """An explained review outcome seeds (or reinforces) a candidate belief.
    With an LLM available, the raw explanation is distilled into a normative
    belief statement (the verbatim explanation is kept in metadata); the LLM
    may also judge the explanation too situation-specific to generalize, in
    which case no belief is seeded (returns None) — the explanation still
    persists as review evidence either way."""
    original = statement
    example = None
    if llm and getattr(llm, "enabled", False):
        rows = live_beliefs(db, manuscript_id, source)
        reply = llm.complete(belief_distill_system(),
                             f"{belief_menu(rows)}\n\nEXPLANATION:\n{statement}")
        # An empty reply is an unreachable model, NOT a decline. Only an
        # explicit NONE declines; otherwise fall through and seed the raw
        # explanation, which is what happens with no LLM configured at all.
        if reply:
            verdict, payload, example = parse_distiller(
                reply, {r["id"] for r in rows})
            if verdict == "none":
                return None
            if verdict == "match":
                return _reinforce_or_revive(db, manuscript_id, payload, original)
            statement = payload
    # Exact match is now only a safety net (and the whole story with no LLM):
    # semantic matching happens in the distiller above, because paraphrase is
    # exactly what string equality cannot see.
    existing = db.one(
        "SELECT * FROM editorial_beliefs WHERE manuscript_id = ? AND lower(statement) = lower(?)",
        (manuscript_id, statement),
    )
    if existing:
        if existing["status"] == "retired":
            # A retired belief stays retired, but fresh supporting evidence
            # files a revival proposal rather than vanishing.
            from . import proposals

            proposals.create(
                db, manuscript_id, "belief_revival", existing["id"],
                {"statement": existing["statement"], "new_explanation": original},
                source="review-explanation",
            )
            return {"kind": "revival_proposal", "statement": existing["statement"]}
        return reinforce_belief(db, existing["id"], "accepted")
    row = ko_fields("pol")
    if statement != original:
        meta = {"original_explanation": original}
        if example:
            meta["example"] = example
        row["metadata"] = json.dumps(meta)
    row.update(
        manuscript_id=manuscript_id,
        statement=statement,
        status="candidate",
        confidence=_confidence(1, 0),
        supporting=1,
        contradicting=0,
        outstanding_questions="[]",
        source=source,
        # The distiller authored the normative statement — a system
        # conjecture until the author's reviews validate it.
        source_id=db.source("system"),
    )
    db.insert("editorial_beliefs", row)
    return row


def _reinforce_or_revive(db: Database, manuscript_id: str, belief_id: str,
                         original: str) -> dict | None:
    """Fresh evidence for an existing belief. A retired belief stays retired
    — the evidence files a revival proposal so the author decides, rather
    than the machine quietly re-adopting something they turned down."""
    belief = db.one("SELECT * FROM editorial_beliefs WHERE id = ?", (belief_id,))
    if belief is None:
        return None
    if belief["status"] == "retired":
        from . import proposals

        proposals.create(
            db, manuscript_id, "belief_revival", belief["id"],
            {"statement": belief["statement"], "new_explanation": original},
            source="review-explanation",
        )
        return {"kind": "revival_proposal", "statement": belief["statement"]}
    return reinforce_belief(db, belief["id"], "accepted")


def _curation_evidence(db: Database, manuscript_id: str, signal: str,
                       target: str, belief_id: str) -> None:
    ev = ko_fields("ev")
    ev.update(
        manuscript_id=manuscript_id, episode_id=None,
        evidence_type="belief_curation", signal=signal,
        target=target[:200], supports_belief=belief_id, weight="high",
    )
    db.insert("evidence", ev)


def retire_belief(db: Database, manuscript_id: str, belief: dict,
                  reason: str) -> dict:
    """Author-initiated retirement: the belief is wrong or unwanted. Kept
    for history; seed_candidate_belief treats retired statements as banned
    (fresh support files a revival proposal instead)."""
    meta = loads(belief["metadata"], {})
    meta["curation"] = {"action": "retired", "reason": reason}
    db.update("editorial_beliefs", belief["id"],
              {"status": "retired", "metadata": json.dumps(meta)})
    _curation_evidence(db, manuscript_id, "retired",
                       f"{belief['statement']} — {reason}", belief["id"])
    return {"id": belief["id"], "statement": belief["statement"],
            "status": "retired"}


def merge_beliefs(db: Database, manuscript_id: str, duplicate: dict,
                   canonical: dict, reason: str | None = None) -> dict:
    """Fold a duplicate belief's evidence record into the canonical one and
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
    db.update("editorial_beliefs", canonical["id"], changes)
    meta = loads(duplicate["metadata"], {})
    meta["curation"] = {"action": "merged", "into": canonical["id"]}
    if reason:
        meta["curation"]["reason"] = reason
    db.update("editorial_beliefs", duplicate["id"],
              {"status": "retired", "metadata": json.dumps(meta)})
    _curation_evidence(
        db, manuscript_id, "merged",
        f"{duplicate['statement']} ⇒ {canonical['statement']}",
        canonical["id"])
    return {**dict(canonical), **changes,
            "merged": duplicate["statement"]}


def convert_belief(db: Database, manuscript_id: str, belief: dict,
                   element: dict, reason: str | None = None) -> dict:
    """Retire a belief whose substance now lives as a style element. The
    element is created by the caller (style machinery needs the manuscript
    record); this records the linkage and the ban."""
    meta = loads(belief["metadata"], {})
    meta["curation"] = {"action": "converted", "style_element": element["id"]}
    if reason:
        meta["curation"]["reason"] = reason
    db.update("editorial_beliefs", belief["id"],
              {"status": "retired", "metadata": json.dumps(meta)})
    _curation_evidence(
        db, manuscript_id, "converted",
        f"{belief['statement']} ⇒ style element {element['id']}",
        belief["id"])
    return {"id": belief["id"], "statement": belief["statement"],
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
    persist the review and evidence, and update belief beliefs."""
    db.update("guidance_history", guidance["id"], {"state": decision})

    review = ko_fields("rv")
    review.update(
        manuscript_id=manuscript_id,
        guidance_id=guidance["id"],
        decision=decision,
        explanation=explanation,
    )
    db.insert("editorial_reviews", review)

    belief_ids = loads(guidance["metadata"], {}).get("belief_ids", [])
    updated_beliefs = []
    for belief_id in belief_ids:
        question = None
        if decision in ("rejected", "modified") and explanation:
            question = f"Review of '{guidance['suggestion'][:60]}': {explanation}"
        updated = reinforce_belief(db, belief_id, decision, question)
        if updated:
            updated_beliefs.append(updated)

        ev = ko_fields("ev")
        ev.update(
            manuscript_id=manuscript_id,
            episode_id=episode_id,
            evidence_type="author_review",
            signal=decision,
            target=guidance["suggestion"][:120],
            supports_belief=belief_id,
            # An explained decision is the highest-quality evidence (§19.11).
            weight="high" if explanation else "medium",
        )
        db.insert("evidence", ev)

    if not belief_ids:
        ev = ko_fields("ev")
        ev.update(
            manuscript_id=manuscript_id,
            episode_id=episode_id,
            evidence_type="author_review",
            signal=decision,
            target=guidance["suggestion"][:120],
            supports_belief=None,
            weight="high" if explanation else "medium",
        )
        db.insert("evidence", ev)

    seeded = None
    if decision in ("rejected", "modified") and explanation:
        seeded = seed_candidate_belief(
            db, manuscript_id, explanation, source="review-explanation", llm=llm
        )

    return {"review": review, "beliefs": updated_beliefs, "seeded_belief": seeded}

def seed_margin_candidate(db: Database, manuscript_id: str,
                          explanation: str, file: str,
                          guide_chain: list[dict],
                          llm: LLMClient | None) -> dict | None:
    """Margin-thread explanations seed candidates only through the
    scoped, decline-capable distiller — never raw (the author's
    overreach guardrail). Without an LLM, the explanation stays
    evidence and nothing is seeded."""
    if not llm or not getattr(llm, "enabled", False):
        return None
    chain = ", ".join(f"{g['name']} ({g['id']})" for g in guide_chain)
    rows = live_beliefs(db, manuscript_id, "margin-thread")
    reply = llm.complete(
        margin_distill_system(),
        f"{belief_menu(rows)}\n\nFILE: {file}\n"
        f"GUIDE CHAIN (nearest first): {chain}\n"
        f"AUTHOR FEEDBACK (verbatim): {explanation}")
    if not reply or reply.strip().upper().startswith("NONE"):
        return None
    for line in reply.strip().splitlines():
        if line.strip().upper().startswith("MATCH:"):
            bid = line.split(":", 1)[1].strip()
            if bid in {r["id"] for r in rows}:
                return _reinforce_or_revive(db, manuscript_id, bid, explanation)
            return None
    scope_kind, statement, example = None, None, None
    for line in reply.strip().splitlines():
        upper = line.strip().upper()
        if upper.startswith("SCOPE:"):
            scope_kind = line.split(":", 1)[1].strip().lower()
        elif upper.startswith("STATEMENT:"):
            statement = line.split(":", 1)[1].strip().strip('"')
        elif upper.startswith("EXAMPLE:"):
            example = line.split(":", 1)[1].strip().strip('"')
    if not statement or scope_kind not in ("file", "guide", "manuscript"):
        return None
    seeded = seed_candidate_belief(db, manuscript_id, statement,
                                   source="margin-thread", llm=None)
    if seeded and seeded.get("id"):
        scope_ref = (file if scope_kind == "file"
                     else guide_chain[0]["id"] if scope_kind == "guide"
                     and guide_chain else manuscript_id)
        meta = loads(db.one(
            "SELECT metadata FROM editorial_beliefs WHERE id = ?",
            (seeded["id"],))["metadata"], {}) or {}
        meta.update(scope_kind=scope_kind, scope_ref=scope_ref,
                    original_explanation=explanation)
        if example:
            meta["example"] = example
        db.update("editorial_beliefs", seeded["id"],
                  {"metadata": json.dumps(meta)})
    return seeded
