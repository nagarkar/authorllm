"""Proposal learning loop — firewall, screen, distillation, retraction.

Covers the four stages in authorlm/loop.py and the two invariants they rest
on: machine cuts are never author evidence, and cuts fold rather than
vanish (so a wrong belief stays falsifiable).

Run: python3 tests/test_loop.py
"""

from __future__ import annotations

import os

# Offline suite: pin the project-config and .env lookups away from the
# real ones. Without this a checkout's config.toml (llm enabled, keys in
# .env) is picked up by every test process and the suite makes live,
# billed model calls — and asserts against whatever they return.
os.environ["AUTHORLM_CONFIG"] = "/nonexistent/authorlm-test/config.toml"
os.environ["AUTHORLM_ENV"] = "/nonexistent/authorlm-test/.env"
# And pin client provenance OFF. The suites run INSIDE a Claude Code
# Bash call, so CLAUDE_CODE_SESSION_ID is in their own environment and
# the claude-code adapter would stamp the developer's live chat into
# every fixture row — tests passing for the wrong reason. Same failure
# mode as a leaked config, so it gets the same treatment: pin it.
os.environ["AUTHORLM_CLIENT"] = "none"


import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from authorlm import (  # noqa: E402
    analysis, api, beliefs as bel, loop, proposals as prop,
)
from authorlm.db import Database, ko_fields, loads  # noqa: E402

PASSED = 0


def check(label: str, condition: bool, context: str = "") -> None:
    global PASSED
    assert condition, f"FAIL: {label}\n{context}"
    PASSED += 1
    print(f"  ok: {label}")


class ScriptedLLM:
    """Replies in order; records what it was asked."""

    enabled = True

    def __init__(self, *replies):
        self.replies = list(replies)
        self.prompts: list[tuple[str, str]] = []

    def _next(self, system, user):
        self.prompts.append((system, user))
        return self.replies.pop(0) if self.replies else None

    def complete(self, system, user):
        return self._next(system, user)

    def complete_json(self, system, user):
        reply = self._next(system, user)
        return json.loads(reply) if isinstance(reply, str) else reply


def fixture():
    root = Path(tempfile.mkdtemp(prefix="authorlm-loop-"))
    db = Database(root / "t.db")
    ms = ko_fields("ms")
    ms.update(name="t", path=str(root))
    db.insert("manuscripts", ms)
    node = ko_fields("cn")
    node.update(manuscript_id=ms["id"], name="Nothing", kind="concept",
                notes="The clasp of all opposites.", status="realized",
                introduced_in="a.md", aliases="[]")
    db.insert("concept_nodes", node)
    return db, dict(ms), node["id"]


def note_payload(note: str, name: str = "Nothing") -> dict:
    return {"name": name, "current_note": "The clasp of all opposites.",
            "proposed_note": note, "current_kind": "concept",
            "proposed_kind": "concept"}


# ------------------------------------------------------------------ ① firewall

def test_firewall(db, ms, target):
    a = prop.create(db, ms["id"], "note_update", target, note_payload(
        "The sum of all opposites, all paradoxes, all possibilities; potent, pregnant with paradox."))
    check("first proposal is created", a is not None)

    punct = prop.create(db, ms["id"], "note_update", target, note_payload(
        "The sum of all opposites—all paradoxes, all possibilities; potent, pregnant with paradox."))
    check("firewall suppresses a punctuation-only restatement", punct is None,
          "this is the exact shape that accumulated 18 open 'Nothing' rows")

    distinct = prop.create(db, ms["id"], "note_update", target, note_payload(
        "The empty heart of the Metaphysic, where every distinction is balanced by its opposite."))
    check("a materially different proposal still gets through",
          distinct is not None)

    # A dismissed proposal must not come back in fresh wording either.
    prop.dismiss(db, ms["id"], dict(db.one(
        "SELECT * FROM knowledge_proposals WHERE id = ?", (distinct["id"],))),
        reason="Describes its role, not what it is.")
    again = prop.create(db, ms["id"], "note_update", target, note_payload(
        "The empty heart of the Metaphysic where each distinction is balanced by its opposite."))
    check("firewall covers already-settled proposals, not just open ones",
          again is None)
    return a


# ------------------------------------------------------- ② beliefs & matching

def test_thresholds():
    check("triage beliefs promote at 2 typed explanations",
          bel.validate_min_support("triage-note_update") == 2)
    check("episode-analysis still needs 3 (inferred from diffs, not typed)",
          bel.validate_min_support("episode-analysis") == 3)
    check("an unknown source falls back to the conservative bar",
          bel.validate_min_support("something-new") == bel.VALIDATE_MIN_SUPPORT)
    check("candidate promotes at the per-source bar",
          bel._lifecycle_status("candidate", 2, 0, bel._confidence(2, 0),
                                "triage-note_update") == "validated")
    check("the same support leaves an episode-analysis belief a candidate",
          bel._lifecycle_status("candidate", 2, 0, bel._confidence(2, 0),
                                "episode-analysis") == "candidate")


def test_semantic_matching(db, ms):
    llm = ScriptedLLM("NEW: A concept note states what a thing is, not its role.\n"
                      "EXAMPLE: 'Nothing' was re-described by its argumentative role.")
    first = bel.seed_candidate_belief(
        db, ms["id"], "The note describes its argumentative role.",
        source="triage-note_update", llm=llm)
    check("a fresh explanation seeds a belief", first is not None)
    check("the distiller was shown an empty belief menu",
          "none yet" in llm.prompts[0][1])

    llm = ScriptedLLM(f"MATCH: {first['id']}")
    second = bel.seed_candidate_belief(
        db, ms["id"], "Flattens an ontological claim into a functional one.",
        source="triage-note_update", llm=llm)
    check("a paraphrase reinforces rather than spawning a duplicate",
          second["id"] == first["id"], str(second))
    check("support accumulated on the one belief", second["supporting"] == 2)
    check("two typed explanations promote it to validated",
          second["status"] == "validated", str(second))
    check("the menu offered the existing belief for matching, by label "
          "not by its random id (stable prompt bytes for record/replay)",
          f"B1 | {first['statement']}" in llm.prompts[0][1]
          and first["id"] not in llm.prompts[0][1])
    check("only one belief exists for this source",
          len(bel.live_beliefs(db, ms["id"], "triage-note_update")) == 1)

    llm = ScriptedLLM("NONE")
    check("a one-off explanation seeds nothing (decline is the default)",
          bel.seed_candidate_belief(db, ms["id"], "Wrong here only.",
                                    source="triage-note_update",
                                    llm=llm) is None)

    unreachable = ScriptedLLM(None)
    seeded = bel.seed_candidate_belief(db, ms["id"], "A raw explanation.",
                                       source="review-explanation",
                                       llm=unreachable)
    check("an unreachable model seeds nothing: the raw explanation never "
          "becomes a belief without the distiller", seeded is None,
          str(seeded))
    check("nor does a disabled one",
          bel.seed_candidate_belief(db, ms["id"], "A raw explanation.",
                                    source="review-explanation",
                                    llm=None) is None)
    return first


def test_episode_analysis_validates_from_pure_machine_inference(db, ms):
    """T1 (risk-register INV-2, Sponsor-ratified 2026-08-25: "INV-2 is
    correct"): three independently closed episodes, each machine-analyzed
    into the SAME inferred pattern, must NOT promote a belief to
    'validated' on zero author input anywhere in the chain — no
    review_suggestion call, no explanation, nothing typed by a human.

    Originally this test PINNED the opposite (buggy) outcome — three
    machine-only episodes reaching 'validated' — as a characterization of
    what analyze_pending + seed_candidate_belief did before the ruling.
    The Sponsor has since ruled that outcome incorrect, which is this
    campaign's one sanctioned reason to flip a characterization test's
    expectation: machine-inferred evidence (episode-analysis, and every
    other SYSTEM_EVIDENCE_TYPES source) may SEED a candidate belief but
    must never by itself validate one."""
    pattern = "Open every abstract definition with a lived example first."
    episode_ids = []
    for i in range(3):
        tr = ko_fields("tr")
        tr.update(manuscript_id=ms["id"], version_before=None,
                   version_after=f"mv-fake-{i}",
                   kind="rewrite", location=f"episode-{i}.md#Heading",
                   summary=f"Rewrote paragraph {i}",
                   detail=json.dumps({"old_text": f"Old text {i}.",
                                      "new_text": f"New text {i}."}))
        db.insert("editorial_transitions", tr)
        ep = ko_fields("ep")
        ep.update(manuscript_id=ms["id"], session_id=f"sess-{i}",
                  intent_id=None, transition_ids=json.dumps([tr["id"]]),
                  outcome=None, status="closed")
        db.insert("editorial_episodes", ep)
        episode_ids.append(ep["id"])

    llm = ScriptedLLM(*[
        json.dumps({"decisions": [{"action": f"decision {i}",
                                   "pattern": pattern}],
                    "outcome": None})
        for i in range(3)
    ])
    summaries = analysis.analyze_pending(db, ms, llm)
    check("all three pending episodes were analyzed in one pass",
          {s["episode_id"] for s in summaries} == set(episode_ids),
          str(summaries))
    # Every episode now leaves TWO episode_analysis-tagged evidence rows
    # for this pattern: the analyzer's own log (analysis.py, unchanged)
    # and the belief-support event beliefs.py records for the same
    # machine-attributed reinforcement (_record_support, new). Both are
    # tagged 'episode_analysis' and both are excluded from
    # _derive_supporting — this assertion is just an accurate count, the
    # exclusion is what the checks below are really about.
    check("every machine touch of this belief is still logged as evidence — "
          "nothing became invisible, it became excluded",
          db.one(
              "SELECT COUNT(*) AS n FROM evidence WHERE manuscript_id = ? "
              "AND evidence_type = 'episode_analysis' AND target = ?",
              (ms["id"], pattern[:200]))["n"] == 6)

    belief = db.one(
        "SELECT * FROM editorial_beliefs WHERE manuscript_id = ? "
        "AND lower(statement) = lower(?)", (ms["id"], pattern))
    check("the belief exists, sourced as episode-analysis, but its derived "
          "supporting count is zero — every evidence row backing it is "
          "machine-inferred and INV-2 excludes all of them",
          belief is not None and belief["source"] == "episode-analysis"
          and belief["supporting"] == 0, str(dict(belief) if belief else None))
    check("it stays 'candidate' — three machine-only episodes are exactly "
          "the scenario the Sponsor ruled must never validate on their own",
          belief["status"] == "candidate", str(dict(belief)))
    return belief


def _guidance_row(ms_id: str, session_id: str, belief_ids: list[str],
                  batch_id: str, suggestion: str = "A guidance suggestion.") -> dict:
    g = ko_fields("gd")
    g.update(manuscript_id=ms_id, session_id=session_id, intent_id=None,
             batch_id=batch_id, batch_index=1, kind="bridge",
             suggestion=suggestion, explanation="why", state="proposed",
             metadata=json.dumps({"belief_ids": belief_ids}))
    return g


def _closed_episode(db, ms_id: str, session_id: str) -> dict:
    ep = ko_fields("ep")
    ep.update(manuscript_id=ms_id, session_id=session_id, intent_id=None,
              transition_ids="[]", outcome=None, status="closed")
    db.insert("editorial_episodes", ep)
    return ep


def test_inv2_cross_session_author_evidence_validates(db, ms):
    """INV-2 regression (b): genuine author evidence, from two DISTINCT
    sessions, DOES validate a candidate belief. The fix must not throw out
    legitimate cross-session support along with the machine-only kind —
    this is the guidance.py:343-344 cross-session clause, now true for
    promotion as well as for what guidance displays."""
    belief = bel.seed_candidate_belief(
        db, ms["id"], "Keep footnotes off the main line of argument.",
        source="review-explanation", distilled=True)
    check("a fresh explanation seeds a candidate", belief is not None
          and belief["status"] == "candidate", str(belief))

    for i in range(2):
        ep = _closed_episode(db, ms["id"], f"inv2b-sess-{i}")
        g = _guidance_row(ms["id"], f"inv2b-sess-{i}", [belief["id"]],
                          f"inv2b-batch-{i}")
        db.insert("guidance_history", g)
        bel.record_review(db, ms["id"], g, "accepted", None, ep["id"], llm=None)

    after = db.one("SELECT * FROM editorial_beliefs WHERE id = ?",
                   (belief["id"],))
    check("two accepts from two distinct sessions validate it "
          "(review-explanation needs 2 distinct sessions)",
          after["status"] == "validated" and after["supporting"] == 3,
          str(dict(after)))


def test_inv2_same_session_dedup(db, ms):
    """INV-2 regression (c): the SAME observation, replayed within one
    session, counts once — "makes replay idempotent" from the ruling.
    Three same-session accepts of the identically-worded suggestion must
    not look like three independent pieces of evidence; they collapse
    into the one unit creation already contributed, so the belief crosses
    the review-explanation bar of 2 (not 4, which is what three ADDITIONAL
    uncollapsed units would wrongly give it). Two DIFFERENT same-session
    observations are a different matter — see
    test_inv2_cross_session_author_evidence_validates's sibling scenario
    in test_e2e.py, where an explanation that seeds a belief and a later,
    separate acceptance of that belief's own reminder both count even
    though both land in one session, because they record different
    things."""
    belief = bel.seed_candidate_belief(
        db, ms["id"], "Never end a chapter on a subordinate clause.",
        source="review-explanation", distilled=True)

    ep = _closed_episode(db, ms["id"], "inv2c-sess")
    same_suggestion = "The same suggestion, reviewed three times in one sitting."
    for i in range(3):
        g = _guidance_row(ms["id"], "inv2c-sess", [belief["id"]],
                          f"inv2c-batch-{i}", same_suggestion)
        db.insert("guidance_history", g)
        bel.record_review(db, ms["id"], g, "accepted", None, ep["id"], llm=None)

    after = db.one("SELECT * FROM editorial_beliefs WHERE id = ?",
                   (belief["id"],))
    check("three same-session, identically-worded accepts collapse into "
          "ONE additional unit of support, not three — supporting is 2 "
          "(creation + one replay-deduped unit), never 4",
          after["supporting"] == 2, str(dict(after)))
    check("2 real units meets the review-explanation bar of 2",
          after["status"] == "validated", str(dict(after)))


def test_inv2_no_retroactive_demotion(db, ms):
    """INV-2 regression (d) — the constraint that matters most: a belief
    validated TODAY, with its stored `supporting` predating evidence
    linkage (the real shape on the live database: 8 of 12 validated
    beliefs have zero linked evidence rows yet carry supporting of 2-11),
    must NOT be demoted by switching supporting from a stored counter to
    one derived from the evidence table. The derived count floors at
    whatever was already on record for a validated belief — it governs
    only FUTURE promotion."""
    row = ko_fields("pol")
    row.update(manuscript_id=ms["id"],
              statement="Ghost-validated: predates evidence linkage.",
              status="validated", confidence=bel._confidence(6, 0),
              supporting=6, contradicting=0, outstanding_questions="[]",
              source="review-explanation", source_id=db.source("author"))
    db.insert("editorial_beliefs", row)
    check("this validated belief starts with zero linked evidence rows — "
          "exactly the shape found on the live database",
          db.one("SELECT COUNT(*) AS n FROM evidence WHERE supports_belief = ?",
                 (row["id"],))["n"] == 0)

    # A later, unrelated touch — one more accepted suggestion tied to it.
    g = _guidance_row(ms["id"], "inv2d-sess", [row["id"]], "inv2d-batch")
    db.insert("guidance_history", g)
    bel.record_review(db, ms["id"], g, "accepted", None, None, llm=None)

    after = db.one("SELECT * FROM editorial_beliefs WHERE id = ?", (row["id"],))
    check("the belief survives at 'validated' — the pre-existing "
          "supporting count is a floor, not overwritten by the (much "
          "smaller) freshly-derived one",
          after["status"] == "validated" and after["supporting"] == 6,
          str(dict(after)))


def test_x7_13_floor_is_one_time_grandfather(db, ms):
    """X7-13: `_validated_floor` was a PERMANENT ratchet, not the one-time
    grandfather INV-2 intended. Because `reinforce_belief`/`merge_beliefs`
    persisted `max(derived, floor)` back into the stored `supporting`
    column, and the floor read that SAME stored column on every call, a
    validated belief's floor could only ever grow and never expired — the
    reviewer measured 9 contradicting author verdicts needed to demote a
    belief whose real derived support was 0.

    The fix: the floor applies for exactly ONE call — the belief's first
    touch under this code — which stamps metadata['derived_validation']
    so every later call (and every belief validated for the first time
    under this code, whose status was 'candidate' when the floor check
    ran) derives purely. Reusing the exact live shape from
    test_inv2_no_retroactive_demotion (validated, supporting on record,
    zero linked evidence): the first touch still isn't retroactively
    demoted (INV-2's guarantee, unchanged) — even when that first touch is
    itself a contradicting verdict. But with zero real supporting
    evidence, exactly ONE further contradicting verdict — not 9 — demotes
    it."""
    row = ko_fields("pol")
    row.update(manuscript_id=ms["id"],
              statement="X7-13 legacy: validated before this fix landed.",
              status="validated", confidence=bel._confidence(5, 0),
              supporting=5, contradicting=0, outstanding_questions="[]",
              source="review-explanation", source_id=db.source("author"))
    db.insert("editorial_beliefs", row)

    # First touch: a contradicting verdict. The grandfather floor still
    # protects it — the Sponsor has not ruled on THIS belief yet.
    g1 = _guidance_row(ms["id"], "x713-sess-0", [row["id"]], "x713-batch-0")
    db.insert("guidance_history", g1)
    bel.record_review(db, ms["id"], g1, "rejected",
                      "X7-13 first rejection — grandfather still spends here.",
                      None, llm=None)
    touched = db.one("SELECT * FROM editorial_beliefs WHERE id = ?", (row["id"],))
    check("first touch: still validated — even a rejection does not "
          "retroactively demote it (INV-2's guarantee, preserved)",
          touched["status"] == "validated", str(dict(touched)))
    meta = loads(touched["metadata"], {})
    check("the one-time grandfather is now spent (stamped in metadata)",
          meta.get("derived_validation") is True, str(meta))

    # Second touch: ONE MORE contradicting verdict. No support evidence
    # exists for this belief anywhere — pure derivation says supporting
    # is 0, and the floor no longer applies.
    g2 = _guidance_row(ms["id"], "x713-sess-1", [row["id"]], "x713-batch-1")
    db.insert("guidance_history", g2)
    bel.record_review(db, ms["id"], g2, "rejected",
                      "X7-13 second rejection — this one must demote it.",
                      None, llm=None)
    after = db.one("SELECT * FROM editorial_beliefs WHERE id = ?", (row["id"],))
    check("ONE further contradicting verdict demotes it — not 9 (X7-13); "
          "the floor already spent, so supporting derives to 0",
          after["status"] == "candidate" and after["supporting"] == 0,
          str(dict(after)))


def test_x7_13_no_floor_for_newly_validated(db, ms):
    """X7-13 (companion claim): a belief that validates for the FIRST TIME
    under this code never sees a floor at all — `_validated_floor` reads
    the belief's status as it stood BEFORE the promoting call (i.e. still
    'candidate'), so the floor gate is false on that very call, and the
    call stamps metadata['derived_validation'] immediately. No belief
    validated after this fix ships can ever be floored."""
    belief = bel.seed_candidate_belief(
        db, ms["id"], "X7-13(b): a fresh rule with no history at all.",
        source="review-explanation", distilled=True)
    check("a fresh candidate belief has no floor",
          bel._validated_floor(belief) == 0, str(belief))

    for i in range(2):
        ep = _closed_episode(db, ms["id"], f"x713b-sess-{i}")
        g = _guidance_row(ms["id"], f"x713b-sess-{i}", [belief["id"]],
                          f"x713b-batch-{i}")
        db.insert("guidance_history", g)
        bel.record_review(db, ms["id"], g, "accepted", None, ep["id"], llm=None)

    after = db.one("SELECT * FROM editorial_beliefs WHERE id = ?", (belief["id"],))
    check("it validated purely on derived evidence",
          after["status"] == "validated", str(dict(after)))
    meta = loads(after["metadata"], {})
    check("the promoting call itself stamped the grandfather as spent — "
          "this belief was never eligible for a floor in the first place",
          meta.get("derived_validation") is True, str(meta))
    check("_validated_floor is 0 for it, even though status is now "
          "'validated' — the stamp forecloses it permanently",
          bel._validated_floor(dict(after)) == 0, str(dict(after)))


def test_x7_13_revival_does_not_double_count_merged_evidence(db, ms):
    """The reviewer's second X7-13 finding: reviving a merged-away
    duplicate used to make the SAME evidence rows support both beliefs at
    once. The duplicate re-derives its own evidence the moment it goes
    live again (belief_revival's own reinforce_belief call), but nothing
    recomputed the canonical — its stored `supporting`, absorbed at merge
    time via `_merged_into`'s metadata walk, stayed stale and kept
    counting the same evidence too. proposals.adopt's belief_revival path
    now recomputes the canonical in the same call that revives the
    duplicate, closing the double count immediately rather than waiting
    on the canonical's next unrelated touch."""
    dup = bel.seed_candidate_belief(
        db, ms["id"], "X7-13 dup: to be merged then revived.",
        source="review-explanation", distilled=True)
    canon = bel.seed_candidate_belief(
        db, ms["id"], "X7-13 canon: the survivor of the merge.",
        source="review-explanation", distilled=True)

    ep = _closed_episode(db, ms["id"], "x713rev-sess-extra")
    g = _guidance_row(ms["id"], "x713rev-sess-extra", [dup["id"]],
                      "x713rev-batch-extra")
    db.insert("guidance_history", g)
    bel.record_review(db, ms["id"], g, "accepted", None, ep["id"], llm=None)

    dup_row = db.one("SELECT * FROM editorial_beliefs WHERE id = ?", (dup["id"],))
    canon_row = db.one("SELECT * FROM editorial_beliefs WHERE id = ?", (canon["id"],))
    check("dup carries 2 units of its own real support before the merge",
          dup_row["supporting"] == 2, str(dict(dup_row)))

    merged = bel.merge_beliefs(db, ms["id"], dict(dup_row), dict(canon_row))
    check("canonical absorbs the duplicate's evidence on merge (1 + 2)",
          merged["supporting"] == 3, str(merged))

    revival = prop.create(db, ms["id"], "belief_revival", dup["id"],
                          {"statement": dup["statement"],
                           "new_explanation": "actually still right"})
    prop.adopt(db, ms["id"], revival)

    dup_after = db.one("SELECT * FROM editorial_beliefs WHERE id = ?", (dup["id"],))
    canon_after = db.one("SELECT * FROM editorial_beliefs WHERE id = ?",
                         (canon["id"],))
    check("revived duplicate is live again with only its OWN evidence "
          "(its earlier 2 units plus the revival's own support = 3, which "
          "genuinely clears the review-explanation bar on its own merits "
          "— not the floor, since it was never 'validated' going into "
          "this call)",
          dup_after["status"] == "validated" and dup_after["supporting"] == 3,
          str(dict(dup_after)))
    check("canonical is recomputed on revival — back to just its own "
          "evidence (1), no longer double-counting the revived duplicate's",
          canon_after["supporting"] == 1, str(dict(canon_after)))


def test_platitude_guard(db, ms):
    """A NEW with no EXAMPLE is refused. A rule whose author cannot point at
    the case that produced it has been generalized past its evidence — and
    vague rules are the ones that win, because they match everything."""
    llm = ScriptedLLM("NEW: Ensure terms are defined accurately.")
    check("a belief with no grounding example is refused",
          bel.seed_candidate_belief(db, ms["id"], "some explanation",
                                    source="triage-note_update",
                                    llm=llm) is None)
    grounded = ScriptedLLM(
        "NEW: A revised note must not drop an alias the text established.\n"
        "EXAMPLE: the proposal dropped 'The Chid', set up in Sermon Five.")
    out = bel.seed_candidate_belief(db, ms["id"], "Loses the alias 'The Chid'.",
                                    source="triage-alias", llm=grounded)
    check("a grounded belief is kept", out is not None)
    check("the example is stored with it",
          "The Chid" in (loads(out.get("metadata"), {}) or {}).get("example", ""),
          str(out.get("metadata")))
    menu = bel.belief_menu(bel.live_beliefs(db, ms["id"], "triage-alias"))
    check("the match menu shows examples, so matching compares grounded rules",
          "e.g." in menu, menu[:200])


def test_retired_belief_returns_as_proposal(db, ms):
    seed = ScriptedLLM("NEW: Keep sermons free of modern idiom.\n"
                       "EXAMPLE: 'basically' appeared in Sermon Three.")
    belief = bel.seed_candidate_belief(db, ms["id"], "Modern idiom intruded.",
                                       source="triage-alias", llm=seed)
    db.update("editorial_beliefs", belief["id"], {"status": "retired"})
    llm = ScriptedLLM(f"MATCH: {belief['id']}")
    out = bel.seed_candidate_belief(db, ms["id"], "Idiom again.",
                                    source="triage-alias", llm=llm)
    check("matching a retired belief files a revival proposal, not a revival",
          out.get("kind") == "revival_proposal", str(out))
    check("the retired belief stays retired",
          db.one("SELECT status FROM editorial_beliefs WHERE id = ?",
                 (belief["id"],))["status"] == "retired")
    check("retired beliefs appear on the menu so paraphrases cannot slip past",
          "[retired]" in llm.prompts[0][1])


# ------------------------------------------------------------- ③ batch distil

def test_distil_batching(db, ms):
    spec = loop.spec_for("proposals/note_update")
    before = loop.pending_explanations(db, ms["id"], spec)
    check("dismissal explanations are queued for distillation", before,
          "proposals._record_evidence must store the reason whole")
    check("the verbatim reason survives in full",
          any("Describes its role, not what it is." == r["explanation"]
              for r in before), str([r["explanation"] for r in before]))

    single = loop.distil(db, ms, spec, [], ScriptedLLM("NEW: x\nEXAMPLE: y"))
    check("an empty batch distils nothing", single is None)

    ev = ko_fields("ev")
    ev.update(manuscript_id=ms["id"], episode_id=None,
              evidence_type=spec.evidence_type, signal="dismissed",
              target="second dismissal", supports_belief=None, weight="high",
              metadata=json.dumps({"explanation": "Another role description."}))
    db.insert("evidence", ev)

    llm = ScriptedLLM("NEW: Notes must state being, not argumentative role.\n"
                      "EXAMPLE: two dismissals both rejected role-descriptions.")
    seeded = loop.distil_pending(db, ms, spec, llm)
    check("two pending explanations distil as one batch", seeded is not None)
    check("the batch prompt names the >= 2 rule",
          "at least two" in llm.prompts[0][1], llm.prompts[0][1][:200])
    check("consumed explanations are not distilled twice",
          loop.distil_pending(db, ms, spec,
                              ScriptedLLM("NEW: y\nEXAMPLE: z")) is None)


# ----------------------------------------------- INV-2a analysis dedupe

def test_analysis_pattern_dedupe(db, ms):
    """INV-2a: two decisions in ONE analysis reply that carry the identical
    'pattern' string must not double-count as two independent pieces of
    supporting evidence — one episode is one observation, regardless of how
    many decisions within it happen to generalize to the same rule."""
    from authorlm import analysis

    transition = ko_fields("tr")
    transition.update(
        manuscript_id=ms["id"], version_before=None, version_after="mv-fake",
        kind="rewrite", location="a.md#Intro", summary="reworded the opening",
        detail=json.dumps({"old_text": "Before text.", "new_text": "After text."}))
    db.insert("editorial_transitions", transition)

    episode = ko_fields("ep")
    episode.update(
        manuscript_id=ms["id"], session_id="se-fake", intent_id=None,
        transition_ids=json.dumps([transition["id"]]), outcome=None,
        status="closed")
    db.insert("editorial_episodes", episode)

    reply = json.dumps({
        "decisions": [
            {"action": "opened with an anecdote before the definition",
             "pattern": "Open with a concrete example."},
            {"action": "reordered the objection ahead of the reply",
             "pattern": "Open with a concrete example."},
        ],
        "outcome": "reworked the opening",
    })
    summaries = analysis.analyze_pending(db, ms, ScriptedLLM(reply))
    check("both decisions are still recorded on the episode",
          len(summaries) == 1 and len(summaries[0]["decisions"]) == 2,
          str(summaries))
    belief = db.one(
        "SELECT * FROM editorial_beliefs WHERE manuscript_id = ? "
        "AND lower(statement) = lower(?)",
        (ms["id"], "Open with a concrete example."))
    check("a pattern repeated within one reply seeds/reinforces only once",
          belief is not None and belief["supporting"] == 1,
          str(dict(belief) if belief else None))


# ------------------------------------------------------------- reconcile

def test_reconcile(db, ms, target):
    """The world-has-moved axis: a proposal can stop being a question
    without anyone judging it."""
    spec = loop.spec_for("proposals/note_update")
    node = db.one("SELECT * FROM concept_nodes WHERE id = ?", (target,))

    satisfied = prop.create(db, ms["id"], "note_update", target, note_payload(
        "A settled definition the author has since written verbatim."))
    db.update("concept_nodes", target,
              {"notes": "A settled definition the author has since written "
                        "verbatim."})
    live = prop.create(db, ms["id"], "note_update", target, note_payload(
        "Something else entirely: a claim about provenance and dating."))

    evidence_before = db.one(
        "SELECT COUNT(*) AS n FROM evidence WHERE manuscript_id = ?",
        (ms["id"],))["n"]
    rows = [r for r in prop.open_proposals(db, ms["id"])
            if r["kind"] == "note_update"]
    dry = loop.reconcile_queue(db, ms["id"], spec, rows, apply=False)
    check("a proposal the note already satisfies is detected",
          any(e["id"] == satisfied["id"] for e in dry[loop.SATISFIED]),
          str(dry))
    check("dry run changes nothing",
          satisfied["id"] in {r["id"] for r in prop.open_proposals(db, ms["id"])})

    out = loop.reconcile_queue(db, ms["id"], spec, rows, apply=True)
    still_open = {r["id"] for r in prop.open_proposals(db, ms["id"])}
    check("a satisfied proposal leaves the open queue",
          satisfied["id"] not in still_open)
    check("reconciliation writes no author evidence",
          db.one("SELECT COUNT(*) AS n FROM evidence WHERE manuscript_id = ?",
                 (ms["id"],))["n"] == evidence_before,
          "nobody judged anything — the question just stopped being one")
    meta = loads(db.one("SELECT metadata FROM knowledge_proposals WHERE id = ?",
                        (satisfied["id"],))["metadata"], {})
    check("the resolution is attributed to reconcile, not the author",
          meta.get("by") == "reconcile" and meta.get("verdict") == "satisfied",
          str(meta))

    # `live` was raised against the ORIGINAL note, which has since changed.
    stale_ids = {e["id"] for e in out[loop.STALE]}
    check("a proposal raised against an older note is stale, not resolved",
          live["id"] in stale_ids, str(out[loop.STALE]))
    check("a re-based proposal stays OPEN for the author",
          live["id"] in still_open,
          "only 19 of 347 collapse on re-basing; the rest include regressions")
    payload = loads(db.one("SELECT payload FROM knowledge_proposals WHERE id = ?",
                           (live["id"],))["payload"], {})
    check("re-basing rewrites current_note to the note as it now stands",
          payload["current_note"] == "A settled definition the author has "
                                     "since written verbatim.",
          str(payload)[:160])

    db.update("concept_nodes", target, {"notes": node["notes"]})


def test_reconcile_other_kinds(db, ms, target):
    node = db.one("SELECT * FROM concept_nodes WHERE id = ?", (target,))
    vanished = prop.create(db, ms["id"], "vanished", target,
                           {"name": node["name"], "was_in": "a.md"})
    db.update("concept_nodes", target, {"status": "retired"})
    spec = loop.spec_for("proposals/vanished")
    out = loop.reconcile_queue(db, ms["id"], spec, [dict(db.one(
        "SELECT * FROM knowledge_proposals WHERE id = ?",
        (vanished["id"],)))], apply=True)
    check("a 'vanished' proposal for an already-retired concept is satisfied",
          len(out[loop.SATISFIED]) == 1, str(out))
    check("it does NOT go through proposals.dismiss (which would un-retire it)",
          db.one("SELECT status FROM concept_nodes WHERE id = ?",
                 (target,))["status"] == "retired",
          "dismiss() re-declares a vanished concept as a placeholder")
    db.update("concept_nodes", target, {"status": node["status"]})


# ------------------------------------------------------------------ ④ screen

def test_screen_and_folds(db, ms, target):
    spec = loop.spec_for("proposals/note_update")
    law = [e for e in loop.active_law(db, ms["id"], spec)]
    check("a validated belief becomes active law", law, str(law))
    check("an unblessed belief is marked unaccepted",
          law[0]["accepted"] is False)

    fresh = prop.create(db, ms["id"], "note_update", target, note_payload(
        "The origin from which the two Fields arise, as previously established."))
    rows = [r for r in prop.open_proposals(db, ms["id"])
            if r["kind"] == "note_update"]

    unattributed = ScriptedLLM(json.dumps({"cut": [{"n": 1, "reason": "meh"}]}))
    check("a cut naming no law is refused",
          loop.screen(db, ms["id"], spec, rows, unattributed) == [])

    out_of_range = ScriptedLLM(json.dumps(
        {"cut": [{"n": 99, "law": law[0]["id"], "reason": "x"}]}))
    check("a cut pointing at no proposal is refused",
          loop.screen(db, ms["id"], spec, rows, out_of_range) == [])

    idx = next(n for n, r in enumerate(rows, 1) if r["id"] == fresh["id"])
    good = ScriptedLLM(json.dumps({"cut": [
        {"n": idx, "law": law[0]["id"], "reason": "describes role, not being"}]}))
    evidence_before = db.one(
        "SELECT COUNT(*) AS n FROM evidence WHERE manuscript_id = ?",
        (ms["id"],))["n"]
    cuts = loop.screen(db, ms["id"], spec, rows, good)
    check("the screen cuts a proposal that violates a named law",
          len(cuts) == 1, str(cuts))
    check("the cut resolves the proposal out of the open queue",
          fresh["id"] not in {r["id"] for r in prop.open_proposals(db, ms["id"])})
    check("machine cuts write no author evidence (invariant 1)",
          db.one("SELECT COUNT(*) AS n FROM evidence WHERE manuscript_id = ?",
                 (ms["id"],))["n"] == evidence_before,
          "a belief that scored its own firings would ratchet its confidence")
    meta = loads(db.one("SELECT metadata FROM knowledge_proposals WHERE id = ?",
                        (fresh["id"],))["metadata"], {})
    check("the cut names the law that made it", meta.get("law") == law[0]["id"])

    folds = loop.folded(db, ms["id"], spec)
    check("cuts fold into one line per belief (invariant 2)",
          len(folds) == 1 and folds[0]["count"] == 1, str(folds))
    check("the fold carries the belief's statement for scanning",
          folds[0]["statement"])
    check("the fold shows the belief is unblessed",
          folds[0]["accepted"] is False)

    listing = api.list_proposals(db, ms)
    check("list_proposals is compact by default",
          all("payload" not in p for p in listing["open"]))
    check("folds ride along with the open list", listing["folded"])
    expanded = api.list_proposals(db, ms, belief=law[0]["id"])
    check("a fold expands to the proposals it swallowed",
          expanded["count"] == 1 and
          expanded["proposals"][0]["id"] == fresh["id"], str(expanded))
    return fresh, law[0]


def test_folded_edge_contradicts(db, ms, target, law_entry):
    """`edge` on a screen-folded proposal still reaches past the cut.

    For note_update, demote_to_edge returns an error string (alias-only),
    but `_contradict_folding_belief` still runs for any non-dismiss action.
    A regression that skipped contradict on that error path would leave a
    wrong cutting belief unfalsifiable while the author thought they had
    overruled it."""
    spec = loop.spec_for("proposals/note_update")
    to_edge = prop.create(db, ms["id"], "note_update", target, note_payload(
        "A phrasing the screen will fold so edge can overrule the cut."))
    rows = [r for r in prop.open_proposals(db, ms["id"])
            if r["kind"] == "note_update"]
    idx = next(n for n, r in enumerate(rows, 1) if r["id"] == to_edge["id"])
    cutter = ScriptedLLM(json.dumps({"cut": [
        {"n": idx, "law": law_entry["id"],
         "reason": "describes role, not being"}]}))
    check("a cut folds a proposal for the edge check",
          len(loop.screen(db, ms["id"], spec, rows, cutter)) == 1)
    before = db.one("SELECT * FROM editorial_beliefs WHERE id = ?",
                    (law_entry["id"],))
    result = api.resolve_proposal(db, ms, to_edge["id"], "edge")
    after = db.one("SELECT * FROM editorial_beliefs WHERE id = ?",
                   (law_entry["id"],))
    check("edge on a folded note_update still reports folded",
          result.get("folded") is True, str(result))
    check("…even though demote_to_edge refuses non-alias kinds",
          "error" in str(result.get("message", "")).lower(), str(result))
    check("edge still contradicts the folding belief "
          "(the demote error must not skip the retract path)",
          result.get("contradicted", {}).get("belief") == law_entry["id"],
          str(result))
    check("contradiction is recorded on the belief",
          after["contradicting"] == before["contradicting"] + 1)
    check("confidence falls", after["confidence"] < before["confidence"])


def test_retraction(db, ms, folded_proposal, law_entry):
    """Adopting what the screen cut is the only event that can contradict a
    belief which is actively cutting."""
    before = db.one("SELECT * FROM editorial_beliefs WHERE id = ?",
                    (law_entry["id"],))
    result = api.resolve_proposal(db, ms, folded_proposal["id"], "accept")
    check("a folded proposal is still reachable by id", result["folded"] is True,
          str(result))
    check("adopting it contradicts the belief that cut it",
          result["contradicted"]["belief"] == law_entry["id"], str(result))
    after = db.one("SELECT * FROM editorial_beliefs WHERE id = ?",
                   (law_entry["id"],))
    check("contradiction is recorded on the belief",
          after["contradicting"] == before["contradicting"] + 1)
    check("confidence falls", after["confidence"] < before["confidence"])


def test_cli_parity():
    """`proposal screen` must exist on the CLI too: the MCP-only version of
    this verb is how the illustration distiller ended up wired one way on
    one surface and another way on the other."""
    from authorlm import cli

    parser_actions = None
    import argparse

    for action in cli.build_parser()._subparsers._group_actions[0].choices.items():
        if action[0] == "proposal":
            for a in action[1]._actions:
                if isinstance(a, argparse._StoreAction) and a.dest == "action":
                    parser_actions = a.choices
    check("CLI exposes 'proposal screen' alongside the MCP tool",
          parser_actions and "screen" in parser_actions, str(parser_actions))


def test_proposals_triage_type(db, ms, target):
    """Proposals are the triage app's third type. Concept/edge triage curates
    machine HYPOTHESES; proposals are conflicts with knowledge the author
    already settled — same grid, different guard."""
    from authorlm import triage

    check("'proposals' is a registered triage type",
          "proposals" in triage.TRIAGE_SCHEMAS)
    schema = triage.TRIAGE_SCHEMAS["proposals"]
    check("its actions are the proposal verbs, not the concept ones",
          {a["id"] for a in schema["actions"]} == {"accept", "dismiss", "edge"},
          str([a["id"] for a in schema["actions"]]))
    check("rows group by concept — proposals against one concept are "
          "competing rewrites of a single note",
          schema.get("group_by", {}).get("id") == "target_name")
    check("dismissal requires a reason at the schema level",
          next(a for a in schema["actions"]
               if a["id"] == "dismiss").get("reason") is not None)

    fresh = prop.create(db, ms["id"], "note_update", target, note_payload(
        "A proposal raised only so the grid has something to render."))
    rows = triage.list_rows(db, ms, "proposals")
    row = next(r for r in rows if r["id"] == fresh["id"])
    for column in ("target_name", "summary", "current_note", "proposed_note",
                   "rebased", "raised"):
        check(f"the grid row carries '{column}'", column in row, str(row.keys()))
    check("the summary matches proposals.describe, so every surface agrees",
          row["summary"] == prop.describe(dict(db.one(
              "SELECT * FROM knowledge_proposals WHERE id = ?",
              (fresh["id"],))))[0])

    try:
        triage.apply_one(db, ms, "proposals", row, "dismiss", {}, None)
        check("a reasonless dismissal is refused", False, "it was allowed")
    except ValueError as err:
        check("a reasonless dismissal is refused — the reason IS the evidence",
              "verbatim" in str(err), str(err))

    before = db.one("SELECT COUNT(*) AS n FROM evidence WHERE manuscript_id = ? "
                    "AND evidence_type = 'proposal_review'", (ms["id"],))["n"]
    triage.apply_one(db, ms, "proposals", row, "dismiss", {},
                     "The settled note is context-bound.")
    check("dismissing through the grid records author evidence",
          db.one("SELECT COUNT(*) AS n FROM evidence WHERE manuscript_id = ? "
                 "AND evidence_type = 'proposal_review'",
                 (ms["id"],))["n"] == before + 1,
          "unlike screen cuts and reconciliation, this IS a judgment")
    check("it leaves the open queue",
          fresh["id"] not in {r["id"] for r in prop.open_proposals(db, ms["id"])})


def main_test():
    db, ms, target = fixture()
    print("firewall")
    test_firewall(db, ms, target)
    print("thresholds")
    test_thresholds()
    print("semantic matching")
    test_semantic_matching(db, ms)
    print("episode analysis (pure machine inference) — INV-2 (a)")
    test_episode_analysis_validates_from_pure_machine_inference(db, ms)
    print("INV-2 (b) cross-session author evidence validates")
    test_inv2_cross_session_author_evidence_validates(db, ms)
    print("INV-2 (c) same-session evidence dedupes")
    test_inv2_same_session_dedup(db, ms)
    print("INV-2 (d) no retroactive demotion")
    test_inv2_no_retroactive_demotion(db, ms)
    print("X7-13 floor is a one-time grandfather, not a permanent ratchet")
    test_x7_13_floor_is_one_time_grandfather(db, ms)
    test_x7_13_no_floor_for_newly_validated(db, ms)
    test_x7_13_revival_does_not_double_count_merged_evidence(db, ms)
    test_platitude_guard(db, ms)
    test_retired_belief_returns_as_proposal(db, ms)
    print("batch distillation")
    test_distil_batching(db, ms)
    print("analysis pattern dedupe")
    test_analysis_pattern_dedupe(db, ms)
    print("reconcile")
    test_reconcile(db, ms, target)
    test_reconcile_other_kinds(db, ms, target)
    print("screen and folds")
    folded_proposal, law_entry = test_screen_and_folds(db, ms, target)
    print("proposals triage type")
    test_proposals_triage_type(db, ms, target)
    print("cli parity")
    test_cli_parity()
    print("folded edge still contradicts")
    test_folded_edge_contradicts(db, ms, target, law_entry)
    print("retraction")
    test_retraction(db, ms, folded_proposal, law_entry)
    print(f"\nall checks passed ({PASSED})")


if __name__ == "__main__":
    main_test()
