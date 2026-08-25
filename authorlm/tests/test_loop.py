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
    check("the menu offered the existing belief for matching",
          first["id"] in llm.prompts[0][1])
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
    check("an unreachable model is not read as a refusal", seeded is not None,
          "empty reply must fall back to raw seeding, not silently decline")
    return first


def test_episode_analysis_validates_from_pure_machine_inference(db, ms):
    """T1 (risk-register INV-2 repro, "the repro the Sponsor needs to rule
    on in the morning"): three independently closed episodes, each machine-
    analyzed into the SAME inferred pattern, promote a belief straight to
    'validated' with zero author input anywhere in the chain — no
    review_suggestion call, no explanation, nothing typed by a human. This
    characterizes what analyze_pending + seed_candidate_belief do today; it
    does not endorse the outcome as correct (Gate-1 flags exactly this as
    a product-semantics question, not a bug to silently fix)."""
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
    check("zero author-explanation evidence was recorded for this belief — "
          "every bit of support is machine-inferred",
          db.one(
              "SELECT COUNT(*) AS n FROM evidence WHERE manuscript_id = ? "
              "AND evidence_type = 'episode_analysis' AND target = ?",
              (ms["id"], pattern[:200]))["n"] == 3)

    belief = db.one(
        "SELECT * FROM editorial_beliefs WHERE manuscript_id = ? "
        "AND lower(statement) = lower(?)", (ms["id"], pattern))
    check("the belief exists, sourced as episode-analysis, with support "
          "from all three episodes and no author explanation",
          belief is not None and belief["source"] == "episode-analysis"
          and belief["supporting"] == 3, str(dict(belief) if belief else None))
    check("it reached 'validated' status with zero author input — the "
          "exact scenario Gate-1 flagged for a Sponsor ruling",
          belief["status"] == "validated", str(dict(belief)))
    return belief


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
    print("episode analysis (pure machine inference)")
    test_episode_analysis_validates_from_pure_machine_inference(db, ms)
    test_platitude_guard(db, ms)
    test_retired_belief_returns_as_proposal(db, ms)
    print("batch distillation")
    test_distil_batching(db, ms)
    print("reconcile")
    test_reconcile(db, ms, target)
    test_reconcile_other_kinds(db, ms, target)
    print("screen and folds")
    folded_proposal, law_entry = test_screen_and_folds(db, ms, target)
    print("proposals triage type")
    test_proposals_triage_type(db, ms, target)
    print("cli parity")
    test_cli_parity()
    print("retraction")
    test_retraction(db, ms, folded_proposal, law_entry)
    print(f"\nall checks passed ({PASSED})")


if __name__ == "__main__":
    main_test()
