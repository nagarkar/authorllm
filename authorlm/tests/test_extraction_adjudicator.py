"""The per-essay adjudicator and the writes (docs/extraction-redesign.md
§4.5, §4.8, §9 phase 3), and the run-time model choice (ruling §0.2).

Standalone, hermetic, zero LLM calls:  python3 tests/test_extraction_adjudicator.py
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

from llmlm import prices as _prices  # noqa: E402

from authorlm import extraction, usage  # noqa: E402
from authorlm import extraction_adjudicator as adj  # noqa: E402
from authorlm.db import loads  # noqa: E402

import _extraction_fixture as fx  # noqa: E402

PASSED = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASSED
    if not ok:
        raise AssertionError(f"FAILED: {label}\n{detail}")
    PASSED += 1
    print(f"  ok: {label}")


class Stub:
    """The counters `extraction.spent` and `_report_llm` read."""

    enabled = True

    def __init__(self, model: str):
        self.model = model
        self.input_tokens = self.output_tokens = 0
        self.cache_read_tokens = self.cache_write_tokens = 0
        self.calls: list[tuple[str, str]] = []

    def stats_line(self):
        return None

    def _count(self, system: str, user: str) -> None:
        self.calls.append((system, user))
        self.input_tokens += (len(system) + len(user)) // 4
        self.output_tokens += 100


class SectionModel(Stub):
    """Answers each section call with the candidates whose cited paragraph
    is numbered in that call's E block."""

    def __init__(self, items: list[tuple[str, dict]], model="stub/section"):
        super().__init__(model)
        self.items = items

    def complete_json(self, system, user, thinking_budget=None):
        self._count(system, user)
        section = user.split("E — THE SECTION", 1)[1]
        reply = {"concepts": [], "links": [], "aliases": []}
        for key, item in self.items:
            n = item.get("paragraph") or (item.get("paragraphs") or [0])[0]
            if re.search(rf"^\[{n}\] ", section, re.M):
                reply[key].append(item)
        return reply


class Judge(Stub):
    """The adjudicator: a verdict per candidate label, matched off the
    CANDIDATES block it is shown."""

    def __init__(self, verdicts: dict[str, dict], model="stub/judge",
                 fail=False, invent=False):
        super().__init__(model)
        self.verdicts, self.fail, self.invent = verdicts, fail, invent

    def complete_json(self, system, user, thinking_budget=None):
        self._count(system, user)
        if self.fail:
            return None
        out = []
        for line in user.splitlines():
            m = re.match(r"\[([cl]\d+)\] (?:CONCEPT|RELATION) (.*?) — score",
                         line)
            if m and m.group(2) in self.verdicts:
                out.append({"id": m.group(1), **self.verdicts[m.group(2)]})
        if self.invent:
            out.append({"id": "c99", "verdict": "new", "reason": "made up"})
        return {"verdicts": out}


TEXT = fx.FILES["hierarchy.md"] + (
    "\n## **Again**\n\nThe Accord Line holds where the accord holds; the "
    "Accord Line is drawn by the population.\n\n"
    "## **Still**\n\nThe Accord Line again, and the population it keeps.\n")
FILES = {**fx.FILES, "hierarchy.md": TEXT}


def n_of(marker: str) -> int:
    return fx.paragraph_number(TEXT, marker)


def edge_row(n: int) -> dict:
    return next(r for r in fx.EDGES if r["n"] == n)


def items() -> list[tuple[str, dict]]:
    links = [("links", {**fx.link_candidate(edge_row(n)),
                        "paragraph": n_of(edge_row(n)["at"])})
             for n in (2, 3, 4, 5, 6, 7, 16, 17, 20)]
    return links + [
        ("concepts", {"name": "Accord Line", "kind": "concept",
                      "notes": "Where the accord holds.",
                      "paragraphs": [n_of("The Accord Line holds")],
                      "span": "The Accord Line holds where the accord holds",
                      "confidence": 0.9, "why": "named twice"}),
        ("concepts", {"name": "ranking", "kind": "concept",
                      "notes": "The ordering of a population by one metric.",
                      "paragraphs": [n_of("A rank is a place")],
                      "span": "A ranking assigns each member a rank",
                      "confidence": 0.8}),
        ("concepts", {"name": "the ranking", "kind": "historical_reference",
                      "paragraphs": [n_of("A rank is a place")],
                      "span": "outside the ranking that gave it",
                      "confidence": 0.8}),
        ("links", {"from": "Accord Line", "relation": "depends_on",
                   "to": "population", "paragraph": n_of("The Accord Line holds"),
                   "span": "the Accord Line is drawn by the population",
                   "confidence": 0.85}),
    ]


VERDICTS = {
    "population —depends_on→ accord": {"verdict": "new",
                                       "reason": "a population is defined by accord"},
    "rank —depends_on→ ranking": {"verdict": "new",
                                  "reason": "a rank is a place in a ranking"},
    "ranking —depends_on→ population": {"verdict": "new",
                                        "reason": "it orders a population"},
    "memetic population —specializes→ population": {
        "verdict": "new", "reason": "a mimetic population is a population"},
    "ideological population —specializes→ ranking": {
        "verdict": "retype", "relation": "illustrates",
        "reason": "the creed shows what a ranking becomes"},
    "Jati —specializes→ endogamy": {
        "verdict": "drop", "reason": "Jati is a population, endogamy a practice"},
    "Accord Line (concept)": {"verdict": "new",
                              "reason": "a named line the essay builds on"},
    "ranking (concept)": {"verdict": "improves", "existing": "ranking",
                          "notes": "The ordering of a population by a metric; "
                                   "each member's place in it is its rank.",
                          "reason": "the span adds what a rank is"},
    "the ranking (historical_reference)": {"verdict": "new",
                                           "reason": "looks distinct"},
    "Accord Line —depends_on→ population": {"verdict": "new",
                                            "reason": "drawn by the population"},
}


def run(db, ms, judge, knob=None, run_id="extract-t1"):
    knob = knob or extraction.knobs({})
    payloads = extraction.assemble_essay(db, ms, "hierarchy.md", knob=knob)
    return extraction.run(db, ms, [("hierarchy.md", payloads)],
                          extract_llm=SectionModel(items()), judge_llm=judge,
                          knob=knob, run_id=run_id, advance_watermark=False)


def rows(db, run_id):
    return [r for r in db.all(
        "SELECT * FROM evidence WHERE evidence_type = 'extraction_adjudication'")
        if (loads(r["metadata"], {}) or {}).get("run") == run_id]


def check_run(root: Path) -> None:
    print("one essay, end to end:")
    db, ms = fx.build(root / "run", files=FILES)
    db.update("concept_nodes", db.one(
        "SELECT id FROM concept_nodes WHERE name = 'ranking'")["id"],
        {"notes": "The ordering of a population."})
    judge = Judge(VERDICTS)
    report = run(db, ms, judge)
    essay = report["essays"][0]
    check("one adjudicator call for the whole essay",
          len(judge.calls) == 1 and essay["judged"], str(len(judge.calls)))
    sent = judge.calls[0][1]
    check("the adjudicator sees G, P, the essay summary and every survivor "
          "with its span, score and the gates' notes",
          "G — GLOSSARY" in sent and "P — PRECEDENTS" in sent
          and "ESSAY SUMMARY" in sent
          and "in the graph as: ranking" in sent
          and "near the live concept: ranking" in sent
          and "span: “A population is a bundle" in sent, sent[:1500])
    check("...but no candidate a gate failed: #16 and #17 never reach it",
          "ranking —leads_to→ rank" not in sent
          and "metric —leads_to→ ranking" not in sent)

    node = db.one("SELECT * FROM concept_nodes WHERE name = 'Accord Line'")
    meta = loads(node["metadata"], {})
    check("a new concept is an unconfirmed extracted node with §4.8's "
          "metadata.extraction, exactly",
          meta["origin"] == "extracted" and meta["confirmed"] is False
          and set(meta["extraction"]) == {"run", "file", "section",
                                           "paragraphs", "span", "score",
                                           "adjudicator", "reason"}
          and meta["extraction"]["adjudicator"] == "stub/judge"
          and meta["extraction"]["reason"] == "a named line the essay "
                                             "builds on"
          and node["introduced_in"] == "hierarchy.md", json.dumps(meta))

    def edge(a, rel, b):
        return db.one(
            "SELECT e.* FROM concept_edges e JOIN concept_nodes f ON "
            "f.id = e.from_node JOIN concept_nodes t ON t.id = e.to_node "
            "WHERE f.name = ? AND e.relation = ? AND t.name = ?", (a, rel, b))

    pa = edge("population", "depends_on", "accord")
    evidence = loads(pa["evidence"], [])
    check("a new relation is inferred, its evidence the list of spans and "
          "its support that list's length (§4.8)",
          pa["status"] == "inferred" and pa["support"] == 1
          and evidence == [{"run": "extract-t1", "file": "hierarchy.md",
                            "section": "Populations",
                            "paragraph": n_of("bundle of individual"),
                            "span": "A population is a bundle of individual "
                                    "trajectories in sufficient accord"}],
          json.dumps(evidence))
    check("...and metadata.extraction = {score, adjudicator, reason}",
          set(loads(pa["metadata"], {})["extraction"]) == {"score",
                                                           "adjudicator",
                                                           "reason"})
    check("a retype reaches the author with the adjudicator's relation",
          edge("ideological population", "illustrates", "ranking") is not None
          and edge("ideological population", "specializes", "ranking") is None)
    check("a relation may end on the concept the same run created",
          edge("Accord Line", "depends_on", "population") is not None)
    check("a drop and an unjudged candidate write nothing",
          edge("Jati", "specializes", "endogamy") is None
          and edge("ranking", "depends_on", "metric") is None)

    by = {r["target"]: r for r in rows(db, "extract-t1")}
    check("one extraction_adjudication row per concept and relation "
          "candidate, gate failures included",
          len(by) == essay["candidates"] == 13, str(sorted(by)))
    sig = {t: r["signal"] for t, r in by.items()}
    check("signals are §4.8's: new, retype, improves, subsumed, drop, "
          "duplicate", sig["population —depends_on→ accord"] == "new"
          and sig["ideological population —illustrates→ ranking"] == "retype"
          and sig["ranking (concept)"] == "improves"
          and sig["the ranking (historical_reference)"] == "subsumed"
          and sig["Jati —specializes→ endogamy"] == "drop"
          and sig["ranking —depends_on→ metric"] == "drop"
          and sig["ranking —leads_to→ rank"] == "duplicate", json.dumps(sig))
    meta = loads(by["Jati —specializes→ endogamy"]["metadata"], {})
    check("every verdict keeps its reason in the row's metadata",
          meta["reason"] == "Jati is a population, endogamy a practice"
          and meta["adjudicator"] == "stub/judge"
          and loads(by["ranking —depends_on→ metric"]["metadata"], {})
          ["reason"] == "not judged by the adjudicator", json.dumps(meta))
    check("a retype's row says what it was retyped from",
          loads(by["ideological population —illustrates→ ranking"]
                ["metadata"], {})["retyped_from"]
          == "ideological population —specializes→ ranking")
    check("extraction never seeds a belief: rows are system evidence with "
          "supports_belief NULL, and no belief exists",
          all(r["supports_belief"] is None and r["weight"] == "low"
              for r in by.values())
          and db.one("SELECT count(*) AS n FROM editorial_beliefs")["n"] == 0)

    note = db.one("SELECT * FROM knowledge_proposals WHERE kind = 'note_update'")
    payload = loads(note["payload"], {})
    check("`improves` is a note_update through proposals.create, source "
          "extraction/<run>, now with its span",
          note["source"] == "extraction/extract-t1" and note["state"] == "open"
          and payload["proposed_note"].startswith("The ordering of a "
                                                  "population by a metric")
          and payload["span"] == "A ranking assigns each member a rank",
          json.dumps(payload))
    alias = db.one("SELECT * FROM knowledge_proposals WHERE kind = 'alias'")
    check("a near-name the adjudicator called new is an alias proposal on "
          "the live concept, never a node (§4.7)",
          alias is not None and loads(alias["payload"], {})["alias"]
          == "the ranking"
          and db.one("SELECT 1 FROM concept_nodes WHERE name = "
                     "'the ranking'") is None)
    entry = extraction.runs(db, ms)[-1]
    check("the run is recorded in manuscripts.metadata.extraction.runs",
          entry["id"] == "extract-t1" and entry["kind"] == "extract"
          and entry["model"] == "stub/section"
          and entry["adjudicator"] == "stub/judge" and entry["units"] == 8
          and entry["reached"] == {"concepts": 1, "relations": 6,
                                   "proposals": 2}
          and "started" in entry and "cost" in entry, json.dumps(entry))
    text = extraction.render_run(report)
    check("the printed report names the run, the gates and what reached "
          "triage", "run extract-t1" in text
          and "reached triage: 1 concept(s), 6 relation(s), 2 proposal(s)"
          in text and "gates: " in text, text)

    print("a second run re-finds what the first wrote:")
    report2 = run(db, ms, Judge(VERDICTS), run_id="extract-t2")
    pa = edge("population", "depends_on", "accord")
    check("a re-found edge gains the run's span: evidence 2, support 2",
          pa["support"] == 2 and [e["run"] for e in loads(pa["evidence"], [])]
          == ["extract-t1", "extract-t2"], pa["evidence"])
    check("...and is logged duplicate, not asked again",
          {r["target"]: r["signal"] for r in rows(db, "extract-t2")}
          ["population —depends_on→ accord"] == "duplicate")
    check("no second node for the concept the first run wrote",
          db.one("SELECT count(*) AS n FROM concept_nodes WHERE name = "
                 "'Accord Line'")["n"] == 1
          and report2["essays"][0]["written"]["nodes"] == [])


def check_ceilings_and_failures(root: Path) -> None:
    print("per-essay ceilings and `extract screened`:")
    db, ms = fx.build(root / "caps", files=FILES)
    knob = extraction.knobs({"extraction": {"max_links_per_essay": 2,
                                            "max_concepts_per_essay": 0}})
    run(db, ms, Judge(VERDICTS), knob, run_id="extract-cap")
    signals = {r["target"]: r["signal"] for r in rows(db, "extract-cap")}
    reached = [t for t, s in signals.items() if s in ("new", "retype")]
    check("at most the ceiling reaches triage; the rest is screened",
          len(reached) == 2 and list(signals.values()).count("screened") >= 4,
          json.dumps(signals))
    check("a relation whose new endpoint was screened is screened with it",
          signals["Accord Line —depends_on→ population"] == "screened"
          and signals["Accord Line (concept)"] == "screened")
    listed = extraction.screened(db, ms, "hierarchy.md")
    check("`extract screened` lists them, best score first, with span and "
          "reason", listed and listed[0]["run"] == "extract-cap"
          and all(a["score"] >= b["score"] for a, b in zip(listed, listed[1:]))
          and all(r["span"] and "ceiling" in r["reason"] for r in listed),
          json.dumps(listed[:2]))

    print("the adjudicator may not add candidates, and may fail:")
    db2, ms2 = fx.build(root / "invent", files=FILES)
    run(db2, ms2, Judge(VERDICTS, invent=True), run_id="extract-inv")
    check("a verdict for an id it was not given is ignored",
          len(rows(db2, "extract-inv")) == 13
          and db2.one("SELECT count(*) AS n FROM concept_nodes WHERE "
                      "json_extract(metadata, '$.extraction.run') = "
                      "'extract-inv'")["n"] == 1)
    db3, ms3 = fx.build(root / "fail", files=FILES)
    report = run(db3, ms3, Judge(VERDICTS, fail=True), run_id="extract-f")
    logged = {r["signal"] for r in rows(db3, "extract-f")}
    check("a failed adjudicator call writes nothing for the survivors and "
          "logs only the gate failures; the run is incomplete",
          not report["essays"][0]["judged"] and report["run"]["incomplete"]
          and logged <= set(extraction.SKIPS)
          and db3.one("SELECT count(*) AS n FROM concept_edges WHERE "
                      "status = 'inferred'")["n"] == 0, str(logged))


def check_choice(root: Path) -> None:
    print("the adjudicator model, chosen at run time (ruling §0.2):")
    fake = {"opus-x": {"input_cost_per_token": 5e-6,
                       "output_cost_per_token": 2.5e-5},
            "gemini/pro-x": {"input_cost_per_token": 1.25e-6,
                             "output_cost_per_token": 1e-5},
            "gemini/flash-x": {"input_cost_per_token": 3e-7,
                               "output_cost_per_token": 2.5e-6}}
    previous = usage.PRICES
    usage.PRICES = _prices.Prices(root / "prices.json",
                                  fetch=lambda: dict(fake),
                                  notify=lambda changed: None, overrides=())
    usage._PRICE_CACHE.clear()
    try:
        db, ms = fx.build(root / "choice", files=FILES)
        config = {"llm": {"enabled": True, "model": "gemini/flash-x"},
                  "extraction": {
                      "adjudicator_model": "anthropic/opus-x",
                      "adjudicator_choices": ["anthropic/opus-x",
                                              "gemini/pro-x", "gemini/flash-x",
                                              "openai/unpriced-x"]}}
        knob = extraction.knobs(config)
        payloads = extraction.assemble_essay(db, ms, "hierarchy.md", knob=knob)
        est = adj.estimate_scope([("hierarchy.md", payloads)],
                                 knob["proposable_relations"])
        table = adj.choice_rows(config, knob, est)
        check("every offered model is priced through usage.estimate — "
              "llmlm's cache, never a constant — and an unpriced one says so",
              [r["model"] for r in table] == config["extraction"]
              ["adjudicator_choices"]
              and table[0]["cost"] > table[1]["cost"] > table[2]["cost"] > 0
              and table[3]["cost"] is None and table[0]["default"],
              json.dumps(table))
        shown = adj.render_choices(table, est, "gemini/flash-x", 0.01)
        check("the question shows each model's estimated cost, the default, "
              "and the assumption the estimate rests on",
              "1. anthropic/opus-x" in shown and "(default" in shown
              and "price unknown" in shown
              and "assuming ~4 candidates per section" in shown, shown)
        check("--adjudicator answers it without asking",
              adj.choose(config, knob, est, answer="gemini/pro-x",
                         ask=lambda rows: 1 / 0) == "gemini/pro-x")
        check("the asked-for answer is the model picked",
              adj.choose(config, knob, est,
                         ask=lambda rows: rows[2]["model"]) == "gemini/flash-x")
        try:
            adj.choose(config, knob, est)
            check("with no terminal and no --adjudicator, the run refuses "
                  "rather than spend unasked", False)
        except LookupError as err:
            check("with no terminal and no --adjudicator, the run refuses "
                  "rather than spend unasked", "--adjudicator" in str(err))
        plain = extraction.knobs({"extraction": {
            **config["extraction"], "adjudicator_prompt": "default"}})
        check("adjudicator_prompt = default takes adjudicator_model unasked",
              adj.choose(config, plain, est) == "anthropic/opus-x")
    finally:
        usage.PRICES = previous
        usage._PRICE_CACHE.clear()


def check_cli(root: Path) -> None:
    print("the verb: asked before the first call; screened; runs:")
    from authorlm.cli import main

    ws = root / "cli"
    db, ms = fx.build(ws, files=FILES, workspace=True)
    knob = extraction.knobs({"extraction": {"max_links_per_essay": 1}})
    run(db, ms, Judge(VERDICTS), knob, run_id="extract-cli")
    os.environ["AUTHORLM_CONFIG"] = str(ws / ".authorlm" / "config.toml")
    (ws / ".authorlm" / "config.toml").write_text(
        '[llm]\nenabled = true\nprovider = "openai"\n'
        'base_url = "http://127.0.0.1:9/v1"\nmodel = "stub"\n'
        '[extraction]\nenabled = true\nadjudicator_choices = ["stub-judge"]\n')

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

    code, out = cli("extract", "hierarchy.md", "--native")
    check("`--native` with no terminal and no --adjudicator stops before any "
          "call, naming the choices", code == 1 and "--adjudicator" in out
          and "stub-judge" in out, out)
    code, out = cli("extract", "screened", "hierarchy.md")
    check("`extract screened <file>` prints the held-back candidates by run",
          code == 0 and "run extract-cli" in out and "ceiling" in out, out)
    code, out = cli("extract", "runs")
    check("`extract runs` lists the recorded runs",
          code == 0 and "extract-cli" in out and "stub/judge" in out, out)


def main_test() -> None:
    root = Path(tempfile.mkdtemp(prefix="authorlm-extract-adj-"))
    try:
        check_run(root)
        check_ceilings_and_failures(root)
        check_choice(root)
        check_cli(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print(f"\nAll {PASSED} checks passed.")


if __name__ == "__main__":
    main_test()
