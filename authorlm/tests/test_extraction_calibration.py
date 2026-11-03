"""`extract calibrate --replay` (docs/extraction-redesign.md §4.6, §6.1,
§9 phase 4): the historical candidates through the gates and the
adjudicator, against the author's verdicts, and the thresholds moved.

Standalone, hermetic, zero LLM calls:
    python3 tests/test_extraction_calibration.py
    python3 tests/test_extraction_calibration.py --show   (print the replays)
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _offline_prices  # noqa: E402,F401  (llmlm prices: no network)

from authorlm import extraction  # noqa: E402
from authorlm import extraction_calibration as cal  # noqa: E402
from authorlm.concepts import link_concepts  # noqa: E402
from authorlm.db import ko_fields  # noqa: E402
from authorlm.triage import _record_edge_triage  # noqa: E402

import _extraction_fixture as fx  # noqa: E402

PASSED = 0
SHOW = "--show" in sys.argv

# §2.4's three retypes, as the author corrected them.
RETYPED = {11: ("Samsara", "illustrates", "Dharma"),
           12: ("Experience", "depends_on", "Discernment"),
           18: ("supremacism", "specializes", "Contraction of discernment")}


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASSED
    if not ok:
        raise AssertionError(f"FAILED: {label}\n{detail}")
    PASSED += 1
    print(f"  ok: {label}")


class Judge:
    """A stand-in adjudicator: `policy(label)` → verdict dict or None."""

    enabled = True

    def __init__(self, policy, model="stub/judge"):
        self.policy, self.model = policy, model
        self.input_tokens = self.output_tokens = 0
        self.cache_read_tokens = self.cache_write_tokens = 0
        self.calls = 0

    def stats_line(self):
        return None

    def complete_json(self, system, user, thinking_budget=None):
        self.calls += 1
        out = []
        for line in user.splitlines():
            m = re.match(r"\[([cl]\d+)\] (?:CONCEPT|RELATION) (.*?) — score",
                         line)
            if m:
                verdict = self.policy(m.group(2))
                if verdict:
                    out.append({"id": m.group(1), **verdict})
        return {"verdicts": out}


def passthrough(_label: str) -> dict:
    return {"verdict": "new", "reason": "passes everything (the floor)"}


def author(label: str) -> dict:
    """The author's own §2.4 verdicts — a perfect adjudicator, the
    CEILING of the design (track-a §4 measured it)."""
    for row in fx.EDGES:
        if f"{row['src']} —{row['rel']}→ {row['dst']}" == label:
            if row["verdict"] == "confirm":
                return {"verdict": "new", "reason": "the author kept it"}
            if row["verdict"] == "retype":
                a, rel, b = RETYPED[row["n"]]
                return {"verdict": "retype", "relation": rel,
                        "reverse": a == row["dst"], "reason": "retyped"}
            return {"verdict": "drop", "reason": "the author rejected it"}
    return {"verdict": "drop", "reason": "unknown"}


def history_db(root: Path):
    """The §2.4 sample as history: author-declared endpoints, the twenty
    edges as the extractor proposed them (system source), each settled
    the way the author settled it, with its edge_triage row."""
    db, ms = fx.build(root, nodes=[])
    for name, kind, aliases in fx.NODES:
        fx.add_node(db, ms, name, kind, aliases, extracted=False)
    system = db.source("system")
    for row in fx.EDGES:
        before = f"{row['src']} —{row['rel']}→ {row['dst']}"
        if row["verdict"] == "retype":
            a, rel, b = RETYPED[row["n"]]
            link_concepts(db, ms["id"], a, rel, b, status="declared",
                          source_id=system)
            _record_edge_triage(db, ms["id"], f"{before} ⇒ {a} —{rel}→ {b}",
                                "retyped")
        else:
            kept = row["verdict"] == "confirm"
            link_concepts(db, ms["id"], row["src"], row["rel"], row["dst"],
                          status="declared" if kept else "rejected",
                          source_id=system)
            _record_edge_triage(db, ms["id"], before,
                                "confirmed" if kept else "rejected")
    return db, ms


def outcome(replay, n: int) -> dict:
    row = next(r for r in fx.EDGES if r["n"] == n)
    want = (row["src"], row["rel"], row["dst"])
    return next(c for cs in replay.groups.values() for c in cs
                if c["type"] == "link"
                and (c["_hist"]["from"], c["_hist"]["relation"],
                     c["_hist"]["to"]) == want)


def check_history(root: Path) -> None:
    print("the history and its labels:")
    db, ms = history_db(root / "hist")
    hist = cal.historical(db, ms)
    labels = {f"{h['from']} —{h['relation']}→ {h['to']}": h["label"]
              for h in hist["links"]}
    check("every §2.4 edge is a historical candidate, labelled from its "
          "edge_triage row (a retype replayed as it was PROPOSED)",
          len(hist["links"]) == 20
          and labels["Discernment —defines→ Experience"] == "retyped"
          and labels["population —depends_on→ accord"] == "kept"
          and labels["Franz Boas —elaborates→ Cultural relativism"]
          == "rejected" and all(h["label_source"] == "triage"
                                for h in hist["links"]), json.dumps(labels))
    check("author-declared concepts are not candidates",
          hist["concepts"] == [])


def check_section_2_4_replay(root: Path) -> None:
    print("the §2.4 sample, replayed:")
    db, ms = history_db(root / "s24")
    knob = extraction.knobs({})

    replay = cal.prepare(db, ms, knob)
    report = cal.measure(replay, knob, {})
    if SHOW:
        print("\n--- gates only ---\n" + cal.render(report) + "\n")
    sig = {n: outcome(replay, n).get("signal") for n in range(1, 21)}
    check("re-located against the version they came from: #1, #8, #14, #19 "
          "fail gate 2 — no paragraph names both ends",
          all(sig[n] == "ungrounded" for n in (1, 8, 14, 19))
          and all(outcome(replay, n)["co_paragraph"] is False
                  for n in (1, 8, 14, 19)), json.dumps(sig))
    check("#16 and #17 fold into #5 and #7 at gate 6 (and #15 into #3)",
          all(sig[n] == "duplicate" for n in (15, 16, 17)), json.dumps(sig))
    check("gate 7 is measured, not applied: #9, #11, #12, #13, #18 go on",
          all(sig[n] is None for n in (9, 11, 12, 13, 18)), json.dumps(sig))
    s25 = report["section_2_5"]
    check("today's precision is §2.4's: 10 of 20 kept or retyped",
          sum(c["today"]["good"] for c in report["by_relation"].values()) == 10
          and sum(c["today"]["n"] for c in report["by_relation"].values())
          == 20)
    check("the gates alone lift the proposable relations to 75% (6 of 8), "
          "with no ungrounded or mirror edge reaching the author",
          s25["edges confirmed or retyped"][0] == 0.75
          and s25["edges with no co-paragraph reaching triage"][0] == 0
          and s25["mirror-duplicate edges reaching triage"][0] == 0,
          json.dumps(s25))
    check("a gates-only replay moves nothing and records nothing",
          not report["moved"] and report["thresholds"] == report["before"]
          and not extraction.runs(db, ms))

    floor_judge = Judge(passthrough)
    replay = cal.prepare(db, ms, knob)
    cal.adjudicate(replay, floor_judge)
    floor = cal.measure(replay, knob, {})
    if SHOW:
        print("--- adjudicator passes everything (the floor) ---\n"
              + cal.render(floor) + "\n")
    check("one adjudicator call per essay with survivors",
          floor_judge.calls == len([e for e, cs in replay.groups.items()
                                    if any(c["_gates_passed"] for c in cs)]))
    ceiling_judge = Judge(author)
    replay = cal.prepare(db, ms, knob)
    cal.adjudicate(replay, ceiling_judge)
    ceiling = cal.measure(replay, knob, {})
    if SHOW:
        print("--- the author's own verdicts (the ceiling) ---\n"
              + cal.render(ceiling) + "\n")
    check("with the author's verdicts the pipeline is exact: every relation "
          "reaching triage is one the author kept or retyped",
          ceiling["section_2_5"]["edges confirmed or retyped"][0] == 1.0,
          json.dumps(ceiling["section_2_5"]))
    check("...and too few candidates per relation to move the proposable "
          "set (fewer than MIN_SUPPORT labelled)",
          ceiling["thresholds"]["proposable_relations"]
          == sorted(extraction.DEFAULT_PROPOSABLE))
    text = cal.render(ceiling)
    check("the printed replay has the per-relation table and §2.5's bar",
          "depends_on *" in text and "§2.5, the bar:" in text
          and "✓ edges with no co-paragraph reaching triage: 0" in text
          and "note_update proposals adopted: not measurable" in text, text)


LAB_PAIRS = {"leads_to": range(1, 6), "permits": range(6, 11),
             "depends_on": range(11, 21)}


def lab_db(root: Path):
    """Twenty pairs, each asserted in its own paragraph, settled so each
    threshold has something to move: leads_to kept five times, permits
    rejected five times, depends_on half and half with confidences that
    separate the kept from the rejected."""
    paragraphs = []
    for relation, pairs in LAB_PAIRS.items():
        paragraphs.append(f"## **{relation}**")
        for i in pairs:
            paragraphs.append(f"Term{2 * i - 1} relates to Term{2 * i}, "
                              f"as the {relation} section says.")
    files = {"lab.md": "# **Lab**\n\n" + "\n\n".join(paragraphs) + "\n"}
    db, ms = fx.build(root, files=files, nodes=[])
    for i in range(1, 41):
        fx.add_node(db, ms, f"Term{i}", extracted=False)
    system = db.source("system")
    for relation, pairs in LAB_PAIRS.items():
        for i in pairs:
            a, b = f"Term{2 * i - 1}", f"Term{2 * i}"
            kept = (relation == "leads_to"
                    or (relation == "depends_on" and i <= 16))
            edge = link_concepts(db, ms["id"], a, relation, b,
                                 status="declared" if kept else "rejected",
                                 source_id=system)
            _record_edge_triage(db, ms["id"], f"{a} —{relation}→ {b}",
                                "confirmed" if kept else "rejected")
            if relation == "depends_on":
                ev = ko_fields("ev")
                ev.update(manuscript_id=ms["id"], episode_id=None,
                          evidence_type="extraction_adjudication",
                          signal="new", target=f"{a} —{relation}→ {b}",
                          supports_belief=None, weight="low",
                          metadata=json.dumps({"edge_id": edge["id"],
                                               "confidence": 0.95 if kept
                                               else 0.72}))
                db.insert("evidence", ev)
    return db, ms


def check_thresholds_move(root: Path) -> None:
    print("the three thresholds move on measurement (§4.6):")
    db, ms = lab_db(root / "lab")
    knob = extraction.knobs({})
    replay = cal.prepare(db, ms, knob)
    judge = Judge(passthrough)
    cal.adjudicate(replay, judge)
    budget = cal.DECISION_BUDGET
    cal.DECISION_BUDGET = 6
    try:
        report = cal.measure(replay, knob, {"max_links_per_essay": 15})
    finally:
        cal.DECISION_BUDGET = budget
    if SHOW:
        print("\n--- lab ---\n" + cal.render(report) + "\n")
    t = report["thresholds"]
    check("a relation kept ≥ 60% on ≥ 5 candidates joins the proposable set",
          "leads_to" in t["proposable_relations"]
          and any(m.startswith("leads_to in") for m in report["moved"]),
          json.dumps(report["moved"]))
    check("one under 60% leaves it",
          "permits" not in t["proposable_relations"]
          and any(m.startswith("permits out") for m in report["moved"]))
    check("exactly 60% holds its place",
          "depends_on" in t["proposable_relations"])
    check("the confidence floor rises to the lowest value whose candidates "
          "meet the 75% bar (0.70 → 0.75)",
          t["confidence_floor"] == 0.75
          and "confidence floor 0.70 → 0.75" in report["moved"])
    check("the relation ceiling falls until a full run fits the decision "
          "budget", t["max_links_per_essay"] == 6
          and "max_links_per_essay 15 → 6" in report["moved"])
    entry = cal.record(db, ms, report, "calibrate-lab", extraction.spent(judge))
    knob2 = extraction.knobs({}, db, ms)
    check("recorded in manuscripts.metadata.extraction.runs, and the next "
          "run's knobs read them back",
          entry["kind"] == "calibrate" and entry["thresholds"] == t
          and knob2["calibrated_by"] == "calibrate-lab"
          and "leads_to" in knob2["proposable_relations"]
          and knob2["confidence_floor"] == 0.75
          and knob2["max_links_per_essay"] == 6, json.dumps(entry))
    check("the switch survives beside the record (set_manuscript_extraction "
          "merges)",
          (extraction.set_manuscript_extraction(db, ms, False) or True)
          and extraction.runs(db, ms)[-1]["id"] == "calibrate-lab"
          and not extraction.manuscript_extraction_enabled(db, ms))


def check_leave_out(root: Path) -> None:
    print("the leave-out rule — no verdict grades itself:")
    db, ms = fx.build(root / "leave", nodes=[])
    fx.add_node(db, ms, "imitation", status="retired", confirmed=False,
                introduced_in="hierarchy.md")
    from authorlm.extraction import record_triage

    node = dict(db.one("SELECT * FROM concept_nodes WHERE name = 'imitation'"))
    record_triage(db, ms["id"], node, "rejected",
                  reason="a word, not a concept")
    knob = extraction.knobs({})
    replay = cal.prepare(db, ms, knob)
    cand = next(c for cs in replay.groups.values() for c in cs
                if c["type"] == "concept")
    check("a rejected candidate is replayed as the fresh candidate it was: "
          "not banned by its own retirement, judged by the gates",
          cand["_hist"]["label"] == "rejected"
          and cand.get("signal") == "below_bar", str(cand.get("signal")))
    check("...and absent from its own essay's precedents",
          "imitation" not in replay.essay_payloads["hierarchy.md"].precedents)


def check_cli(root: Path) -> None:
    print("the verb:")
    from authorlm.cli import main

    ws = root / "cli"
    db, ms = fx.build(ws, nodes=[], workspace=True)
    for name, kind, aliases in fx.NODES:
        fx.add_node(db, ms, name, kind, aliases, extracted=False)
    system = db.source("system")
    for row in fx.EDGES[:6]:
        link_concepts(db, ms["id"], row["src"], row["rel"], row["dst"],
                      status="declared", source_id=system)
    os.environ["AUTHORLM_CONFIG"] = str(ws / ".authorlm" / "config.toml")
    (ws / ".authorlm" / "config.toml").write_text(
        '[llm]\nenabled = true\nprovider = "openai"\n'
        'base_url = "http://127.0.0.1:9/v1"\nmodel = "stub"\n'
        '[extraction]\nenabled = false\nadjudicator_choices = ["stub-judge"]\n')

    def cli(*argv: str) -> tuple[int, str]:
        buffer = io.StringIO()
        code = 0
        try:
            with contextlib.redirect_stdout(buffer):
                main(["--workspace", str(ws), *argv])
        except SystemExit as err:
            code = 0 if err.code in (0, None) else 1
            if isinstance(err.code, str):
                buffer.write(err.code)
        return code, buffer.getvalue()

    code, out = cli("extract", "calibrate")
    check("`extract calibrate` says which thresholds are in force",
          code == 0 and "thresholds from config.toml" in out, out)
    code, out = cli("extract", "calibrate", "--replay", "--gates-only")
    check("`--replay --gates-only` runs with the switch off, free, and "
          "records nothing",
          code == 0 and "gates only: nothing recorded" in out
          and "§2.5, the bar:" in out and not extraction.runs(db, ms), out)
    code, out = cli("extract", "calibrate", "--replay", "hierarchy.md",
                    "--gates-only")
    check("...and can be held to one essay",
          code == 0 and "replay: 1 essay(s)" in out, out)
    code, out = cli("extract", "calibrate", "--replay")
    check("an adjudicated replay asks for its model first — with no "
          "terminal, --adjudicator is required",
          code == 1 and "--adjudicator" in out and "stub-judge" in out, out)


def main_test() -> None:
    root = Path(tempfile.mkdtemp(prefix="authorlm-extract-cal-"))
    try:
        check_history(root)
        check_section_2_4_replay(root)
        check_thresholds_move(root)
        check_leave_out(root)
        check_cli(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print(f"\nAll {PASSED} checks passed.")


if __name__ == "__main__":
    main_test()
