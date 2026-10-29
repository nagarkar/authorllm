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

from .db import Database, SYSTEM_EVIDENCE_TYPES, ko_fields, loads
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


def belief_labels(rows: list[dict]) -> dict[str, str]:
    """Menu label → belief id. The menu shows B1, B2, … rather than the
    random ids, so the same beliefs make the same prompt (the record/replay
    suite keys on its exact bytes). A reply naming the raw id still
    resolves."""
    labels = {f"B{i}": r["id"] for i, r in enumerate(rows, 1)}
    labels.update({r["id"]: r["id"] for r in rows})
    return labels


def belief_menu(rows: list[dict]) -> str:
    if not rows:
        return "BELIEFS ON RECORD: (none yet — every explanation is NEW)"
    lines = []
    for i, r in enumerate(rows, 1):
        line = f"  B{i} | {r['statement']}"
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
                    labels: dict[str, str]) -> tuple[str, str | None, str | None]:
    """(verdict, statement_or_id, example) where verdict is none | match | new.
    `labels` is `belief_labels(rows)`; a match returns the real id.

    An unparseable or unknown-label reply is treated as NONE — declining is the
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
            bid = labels.get(line.split(":", 1)[1].strip())
            return ("match", bid, None) if bid else ("none", None, None)
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


# Evidence signals that represent genuine endorsement (an accepted or
# modified suggestion, an explanation that matched/created a belief).
# 'rejected' contradicts; 'deferred' and everything else (declared,
# adopted, retired, merged, converted, pattern, ...) neither supports nor
# contradicts and is left out of both counts, exactly as before.
SUPPORT_SIGNALS = ("accepted", "modified")


def _support_evidence_type(source: str | None) -> str:
    """Maps a belief `source` to the evidence_type this module writes on
    its behalf when it creates or reinforces a belief. 'episode-analysis'
    is the one source whose own events must stay machine-flagged so
    SYSTEM_EVIDENCE_TYPES excludes them from `_derive_supporting` (INV-2);
    every other source is author-originated and logged as author_review."""
    return "episode_analysis" if source == "episode-analysis" else "author_review"


def _record_support(db: Database, manuscript_id: str, belief_id: str,
                    source: str | None, target: str,
                    episode_id: str | None = None) -> None:
    """The evidence row backing one belief creation or reinforcement event.
    `_derive_supporting` counts only evidence rows, so every call site that
    used to bump the stored `supporting` counter in place now writes one of
    these FIRST — machine-sourced calls land tagged 'episode_analysis' and
    are excluded on the very next read, non-machine calls land tagged
    'author_review' and count."""
    ev = ko_fields("ev")
    ev.update(
        manuscript_id=manuscript_id, episode_id=episode_id,
        evidence_type=_support_evidence_type(source), signal="accepted",
        target=target[:200], supports_belief=belief_id, weight="medium",
    )
    db.insert("evidence", ev)


def _merged_into(db: Database, manuscript_id: str, belief_id: str) -> list[str]:
    """Ids of beliefs (transitively) folded into `belief_id` by merge_beliefs
    — found by walking metadata.curation, never by repointing evidence rows
    (existing evidence is never rewritten). Lets a merged duplicate's
    evidence keep counting toward the canonical belief without touching a
    single historical row."""
    rows = db.all(
        "SELECT id, metadata FROM editorial_beliefs "
        "WHERE manuscript_id = ? AND status = 'retired'",
        (manuscript_id,),
    )
    children_of: dict[str, list[str]] = {}
    for r in rows:
        curation = (loads(r["metadata"], {}) or {}).get("curation", {})
        if curation.get("action") == "merged" and curation.get("into"):
            children_of.setdefault(curation["into"], []).append(r["id"])
    found: list[str] = []
    seen = {belief_id}
    frontier = [belief_id]
    while frontier:
        for child in children_of.get(frontier.pop(), []):
            if child in seen:  # X7-12: guards a merge cycle from wedging this.
                continue
            seen.add(child)
            found.append(child)
            frontier.append(child)
    return found


def _derive_supporting(db: Database, manuscript_id: str, belief_id: str) -> int:
    """Supporting count derived from distinct-session evidence, excluding
    machine-inferred sources (INV-2 — Sponsor-ratified 2026-08-25: "INV-2
    is correct"). An episode-analysis conjecture may SEED a candidate
    belief but can never by itself validate one — SYSTEM_EVIDENCE_TYPES
    (db.py) is excluded here, regardless of session, which is what makes
    this the actual fix rather than a session-counting refinement.

    Within one session, two evidence rows count once ONLY when they are
    the same observation replayed (same episode/session AND the same
    logged `target`) — this is the "makes replay idempotent" half of the
    ruling: re-running the same machine analysis, or the author repeating
    the identical typed explanation twice in one sitting, must not look
    like two independent observations. Two rows from the same session
    that record two DIFFERENT things — e.g. the explanation that seeded a
    candidate belief, and a later, separate acceptance of that belief's
    own reminder suggestion (guidance.py's own review-once-per-session
    gate already stops that reminder itself from being farmed) — are two
    genuinely distinct author acts and both count, even same-session.
    Evidence with no episode (e.g. a briefing answer or a proposal
    adoption, recorded outside any episode) has no session to correlate
    against, so each such row is its own group by construction (grouped
    with its own evidence id, which is unique)."""
    ids = [belief_id] + _merged_into(db, manuscript_id, belief_id)
    id_ph = ", ".join("?" for _ in ids)
    sys_ph = ", ".join("?" for _ in SYSTEM_EVIDENCE_TYPES)
    sig_ph = ", ".join("?" for _ in SUPPORT_SIGNALS)
    rows = db.all(
        f"SELECT e.id AS eid, e.target AS target, "
        f"ep.session_id AS session_id FROM evidence e "
        f"LEFT JOIN editorial_episodes ep ON ep.id = e.episode_id "
        f"WHERE e.supports_belief IN ({id_ph}) "
        f"AND e.evidence_type NOT IN ({sys_ph}) "
        f"AND e.signal IN ({sig_ph})",
        (*ids, *SYSTEM_EVIDENCE_TYPES, *SUPPORT_SIGNALS),
    )
    groups = {(r["session_id"] or r["eid"], r["target"]) for r in rows}
    return len(groups)


def _validated_floor(belief: dict) -> int:
    """The one exception to pure derivation — and, per X7-13, a ONE-TIME
    exception, not a permanent ratchet. A belief that is validated TODAY
    must not be retroactively demoted by this change (8 of 12 validated
    beliefs on the live database carry stored `supporting` of 2-11 with
    zero linked evidence rows — evidence linkage predates them — and their
    fate is a separate decision the Sponsor has not ruled on). So: on a
    validated belief's FIRST touch under this code, supporting floors at
    whatever is already on record.

    X7-13: the original version of this function returned
    `belief["supporting"]` — the STORED column — every time `status ==
    "validated"`. Because `reinforce_belief`/`merge_beliefs` persist
    `max(derived, floor)` back into that same column, the floor became
    self-referential: it could grow (fresh positive evidence raises the
    stored value, which becomes next call's floor) but never shrink, and
    it applied on every single call for as long as the belief stayed
    validated — a permanent, ever-climbing ratchet. Measured: 9
    contradicting verdicts were needed to demote a belief with zero real
    derived support.

    The fix is `_stamp_validation` below: the call that applies this floor
    also stamps `metadata['derived_validation']`, and this function reads
    that stamp — never the stored `supporting` value it may itself have
    inflated — to know whether the grandfather has already been spent. A
    belief that was NOT already validated before the current call (i.e.
    every belief validating for the first time under this code) never
    reaches this branch at all: `status` here is read BEFORE the current
    call's changes, so it is still 'candidate'. And a belief `belief
    demote` sends back to 'candidate' cannot be resurrected by it either —
    the status check below is false the moment status is no longer
    'validated', regardless of what the metadata stamp says."""
    if belief["status"] != "validated":
        return 0
    meta = loads(belief["metadata"], {}) or {}
    if meta.get("derived_validation"):
        return 0
    return belief["supporting"]


def _stamp_validation(meta: dict, status: str) -> dict:
    """Marks a belief's metadata as validated-under-pure-derivation the
    moment its (about-to-be-written) status is 'validated', so
    `_validated_floor` never floors it again — this is what makes the
    grandfather in `_validated_floor` a ONE-TIME allowance rather than a
    standing entitlement. Idempotent: a belief already stamped is left
    alone (also covers the ordinary case of a validated belief simply
    staying validated on some later, unrelated touch)."""
    if status == "validated" and not meta.get("derived_validation"):
        meta = {**meta, "derived_validation": True}
    return meta


def reinforce_belief(db: Database, belief_id: str, signal: str, question: str | None = None) -> dict:
    """Recompute one belief's evidence record. Callers must persist the
    evidence row for THIS event (supports_belief = belief_id) before
    calling — supporting is derived from the evidence table, not
    incremented in place, so there is nothing to derive from until the row
    exists."""
    belief = db.one("SELECT * FROM editorial_beliefs WHERE id = ?", (belief_id,))
    if belief is None:
        return {}
    supporting = max(_derive_supporting(db, belief["manuscript_id"], belief_id),
                     _validated_floor(belief))
    contradicting = belief["contradicting"] + (1 if signal == "rejected" else 0)
    conf = _confidence(supporting, contradicting)
    status = _lifecycle_status(belief["status"], supporting, contradicting,
                               conf, belief["source"])
    questions = loads(belief["outstanding_questions"], [])
    if question and question not in questions:
        questions.append(question)
    meta = _stamp_validation(loads(belief["metadata"], {}) or {}, status)
    changes = {
        "supporting": supporting,
        "contradicting": contradicting,
        "confidence": conf,
        "status": status,
        "outstanding_questions": json.dumps(questions),
        "metadata": json.dumps(meta),
    }
    db.update("editorial_beliefs", belief_id, changes)
    return {**dict(belief), **changes}


def seed_candidate_belief(
    db: Database, manuscript_id: str, statement: str, source: str,
    llm: LLMClient | None = None, episode_id: str | None = None,
    distilled: bool = False,
) -> dict | None:
    """An explained review outcome seeds (or reinforces) a candidate belief.

    `statement` is the author's raw explanation, which becomes a belief only
    through the distiller: it is rewritten as a normative statement (the
    verbatim explanation kept in metadata), matched to a belief on record,
    or judged too situation-specific (NONE). With no LLM, or no reply,
    nothing is seeded (returns None) — the explanation still persists as
    review evidence. Seeding the raw words is how "a one-off exception for
    this chapter only" became a belief (author ruling 2026-10-05).

    `distilled=True` is for a statement a model already wrote (episode
    analysis, the margin distiller); it skips the distiller."""
    original = statement
    example = None
    if not distilled:
        if not (llm and getattr(llm, "enabled", False)):
            return None
        rows = live_beliefs(db, manuscript_id, source)
        reply = llm.complete(belief_distill_system(),
                             f"{belief_menu(rows)}\n\nEXPLANATION:\n{statement}")
        verdict, payload, example = parse_distiller(
            reply, belief_labels(rows))
        if verdict == "none":
            return None
        if verdict == "match":
            return _reinforce_or_revive(db, manuscript_id, payload, original,
                                        source=source, episode_id=episode_id)
        statement = payload
    # Exact match is only a safety net: semantic matching happens in the
    # distiller above, because paraphrase is what string equality cannot see.
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
        _record_support(db, manuscript_id, existing["id"], source, original, episode_id)
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
    _record_support(db, manuscript_id, row["id"], source, original, episode_id)
    return row


def _reinforce_or_revive(db: Database, manuscript_id: str, belief_id: str,
                         original: str, source: str | None = None,
                         episode_id: str | None = None) -> dict | None:
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
    _record_support(db, manuscript_id, belief["id"], source, original, episode_id)
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


def demote_belief(db: Database, manuscript_id: str, belief: dict,
                  reason: str) -> dict:
    """Author/Sponsor-initiated demotion: the belief is not banned like a
    retirement — it goes back to 'candidate' because it may still be
    right, and real future evidence can re-validate it. That is the
    entire difference from retire_belief above; everything else about the
    shape (reason required and recorded verbatim, curation metadata,
    auditable evidence) mirrors it exactly.

    X7-13's seam with the validated-floor fix: this only ever WRITES
    status='candidate' here — it never re-derives or re-floors anything.
    The next `reinforce_belief`/`merge_beliefs` call for this belief reads
    that status as 'candidate' BEFORE computing anything, so
    `_validated_floor` returns 0 regardless of any stale
    metadata['derived_validation'] stamp — a demoted belief cannot be
    bounced straight back to 'validated' by the floor. It can still climb
    back to 'validated' on genuine derived support, which is the point."""
    meta = loads(belief["metadata"], {})
    meta["curation"] = {"action": "demoted", "reason": reason}
    db.update("editorial_beliefs", belief["id"],
              {"status": "candidate", "metadata": json.dumps(meta)})
    _curation_evidence(db, manuscript_id, "demoted",
                       f"{belief['statement']} — {reason}", belief["id"])
    return {"id": belief["id"], "statement": belief["statement"],
            "status": "candidate"}


def merge_beliefs(db: Database, manuscript_id: str, duplicate: dict,
                   canonical: dict, reason: str | None = None) -> dict:
    """Fold a duplicate belief's evidence record into the canonical one and
    retire the duplicate. The duplicate's statement stays in the table
    (retired), so it remains banned from re-seeding.

    The duplicate is retired FIRST so `_derive_supporting` can see the
    fold (via `_merged_into`'s metadata walk) when it recomputes canonical's
    count — no evidence row is repointed, existing evidence is never
    rewritten. This replaces the old `canonical["supporting"] +
    duplicate["supporting"]` counter arithmetic, which summed whatever
    numbers happened to be stored (possibly machine-inflated) instead of
    counting real evidence."""
    meta = loads(duplicate["metadata"], {})
    meta["curation"] = {"action": "merged", "into": canonical["id"]}
    if reason:
        meta["curation"]["reason"] = reason
    db.update("editorial_beliefs", duplicate["id"],
              {"status": "retired", "metadata": json.dumps(meta)})

    supporting = max(_derive_supporting(db, manuscript_id, canonical["id"]),
                     _validated_floor(canonical))
    contradicting = canonical["contradicting"] + duplicate["contradicting"]
    conf = _confidence(supporting, contradicting)
    status = _lifecycle_status(canonical["status"], supporting,
                               contradicting, conf)
    questions = loads(canonical["outstanding_questions"], [])
    for question in loads(duplicate["outstanding_questions"], []):
        if question not in questions:
            questions.append(question)
    canon_meta = _stamp_validation(loads(canonical["metadata"], {}) or {}, status)
    changes = {
        "supporting": supporting, "contradicting": contradicting,
        "confidence": conf, "status": status,
        "outstanding_questions": json.dumps(questions),
        "metadata": json.dumps(canon_meta),
    }
    db.update("editorial_beliefs", canonical["id"], changes)
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

        # Evidence is written BEFORE reinforcement: supporting is derived
        # from the evidence table (INV-2), so this event has to exist
        # there before reinforce_belief can see it.
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

        updated = reinforce_belief(db, belief_id, decision, question)
        if updated:
            updated_beliefs.append(updated)

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
            db, manuscript_id, explanation, source="review-explanation", llm=llm,
            episode_id=episode_id,
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
            bid = belief_labels(rows).get(line.split(":", 1)[1].strip())
            if bid:
                return _reinforce_or_revive(db, manuscript_id, bid, explanation,
                                            source="margin-thread")
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
                                   source="margin-thread", distilled=True)
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
