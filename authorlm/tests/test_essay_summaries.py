"""Summary hierarchy, step 4 (docs/summary-hierarchy-design.md §3.4, §4.5,
§6.6, §11.4): the essay-summary pass. The essay is composed from its
section summaries (never the text) and the prior essay summaries; the
model writes FLOW, MOVES, MOTIFS, DOES NOT and code derives INTRODUCES,
ASSUMES, PROMISES, DEBTS from the paragraph records and renders them
under those labels; `sections_hash` tells "my sections changed";
`ensure(level="essay")` rebuilds the two lower levels first and prints
one cost line per level; the structural coverage check replaces the
paragraph citation parser. Fake LLMs throughout; the live recording is
tests/test_live_llm.py's `essay-summary` section.

Run: python3 tests/test_essay_summaries.py
"""

from __future__ import annotations

import os

os.environ["AUTHORLM_CONFIG"] = "/nonexistent/authorlm-test/config.toml"
os.environ["AUTHORLM_ENV"] = "/nonexistent/authorlm-test/.env"
os.environ["AUTHORLM_CLIENT"] = "none"

import contextlib
import io
import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import _offline_prices  # noqa: E402,F401

from authorlm.context import Input  # noqa: E402
from authorlm import api, manifest, summaries as sums  # noqa: E402
from test_paragraph_records import FakeLLM as FakeParagraphLLM  # noqa: E402
from test_section_summaries import FakeSectionLLM  # noqa: E402
from test_summary_hierarchy import ESSAY, POEMS  # noqa: E402

PASSED = 0


def check(label: str, condition: bool, context: str = "") -> None:
    global PASSED
    assert condition, f"FAIL: {label}\n{context}"
    PASSED += 1
    print(f"  ok: {label}")


class RecordingParagraphLLM(FakeParagraphLLM):
    """The paragraph fake, with one paragraph of doors.md DEFINING the
    Field and one making a backward and a forward reference, so the
    derived lists have something to derive (§6.6)."""

    def complete_json(self, system, user, thinking_budget=None, schema=None):
        out = super().complete_json(system, user, thinking_budget, schema)
        for rec in out:
            text = re.search(rf"^\[{rec['n']}\] (.*?)(?=\n\n\[\d+\] |\Z)",
                             user.split("THE SECTION:\n\n", 1)[1],
                             re.S | re.M).group(1)
            if "We call" in text:
                rec["mentions"][0]["role"] = "defines"
                rec["definitions"] = [{"term": "Field",
                                       "sentence": "We call\nthis remainder the Field."}]
            if "As the Metaphysic showed" in text:
                rec["references"] = [
                    {"term": "Choice", "direction": "back",
                     "target": "the Metaphysic", "span": "As the Metaphysic showed"},
                    {"term": "the key", "direction": "forward",
                     "target": "the Recapitulation",
                     "span": "a later\nessay will show"}]
                rec["mentions"].append({"term": "Choice", "span": "every Choice",
                                        "role": "uses"})
        return out


class FakeEssayLLM:
    """Writes the four model labels; FLOW names every heading in the
    SECTIONS block and MOVES cites every COVER THESE number, unless
    `drop_flow` or `skip` says otherwise."""

    enabled = True
    model = "fake/luna-essay"

    def __init__(self, drop_flow=False, skip=()):
        self.calls: list[str] = []
        self.systems: list[str] = []
        self.drop_flow, self.skip = drop_flow, set(skip)

    def complete(self, system, user, thinking_budget=None):
        self.systems.append(system)
        self.calls.append(user)
        unit = user.split("THE UNIT: ", 1)[1].split("\n", 1)[0]
        cover = re.search(r"COVER THESE: (.*)", user).group(1)
        wanted = [int(n) for n in cover.split(", ")] if cover != "none" else []
        heads = re.findall(r"^\[\d+\] (.*)$", user.split("SECTIONS (", 1)[1], re.M)
        flow = "; ".join(f"'{h.lstrip('#').strip()}' carries the line"
                         for h in heads if h != "(preamble)")
        moves = " ".join(f"[{n}] move {n}." for n in wanted if n not in self.skip)
        return ((f"FLOW: {flow}\n" if not self.drop_flow else "")
                + f"MOVES: {moves or 'none'} The essay {unit}.\n"
                + "MOTIFS: the door, the key\n"
                + "DOES NOT: re-explain the Field\n"
                + "INTRODUCES: the model must not write this")

    def stats_line(self):
        return f"LLM: {len(self.calls)} live call(s) (fake-essay)"


def main_test() -> None:
    root = Path(tempfile.mkdtemp(prefix="authorlm-essaysum-"))
    try:
        ws = root / "ws"
        ms = ws / "book"
        ms.mkdir(parents=True)
        (ms / "toc.toml").write_text(
            '[[chapter]]\nfile = "doors.md"\n\n'
            '[[chapter]]\nfile = "poems.md"\nform = "verse"\n')
        (ms / "doors.md").write_text(ESSAY)
        (ms / "poems.md").write_text(POEMS)
        db = api.open_db(str(ws), create=True)
        manuscript = api.register_manuscript(db, "book", str(ms))
        with contextlib.redirect_stdout(io.StringIO()):
            api.collect(db, manuscript, {}, source="test")

        print("prompt and clients:")
        prompt = sums.summarizer_prompt()
        check("the prompt asks for the four model labels in order and "
              "names the derived four as code's (ruling 6)",
              [prompt.index(f"\n{lb}:") for lb in sums.ESSAY_MODEL_LABELS]
              == sorted(prompt.index(f"\n{lb}:") for lb in sums.ESSAY_MODEL_LABELS)
              and "Do NOT write INTRODUCES, ASSUMES, PROMISES or DEBTS" in prompt)
        check("the prompt takes section summaries, never the text",
              "never see the unit's text" in prompt and "SECTIONS:" in prompt)
        llms = sums.clients({"llm": {"enabled": True},
                             "critique": {"summarizer_model": "openai/luna"}})
        check("clients() covers every level; the essay client is Luna on "
              "the ledger key critique.summarizer (ruling 5, §10)",
              set(llms) == set(sums.LEVELS) == {"paragraph", "section", "essay"}
              and llms["essay"].model == "openai/luna"
              and llms["essay"].purpose == "critique.summarizer")

        print("rendering (ruling 6, §6.6):")
        block, missing = sums.render_essay_block(
            "FLOW: a; b.\nMOVES: [1] x.\nMOTIFS: m\nDOES NOT: d\n"
            "INTRODUCES: smuggled",
            {"introduces": ["Field"], "assumes": ["Choice", "the key"],
             "promises": [], "debts": ["Choice"]})
        check("the eight labels in stored order, derived lists rendered as "
              "comma-separated terms, 'none' for an empty one, and a label "
              "the model wrote against orders is dropped",
              block == ("FLOW: a; b.\nMOVES: [1] x.\nINTRODUCES: Field\n"
                        "ASSUMES: Choice, the key\nPROMISES: none\n"
                        "DEBTS: Choice\nMOTIFS: m\nDOES NOT: d")
              and missing == [], block)
        block, missing = sums.render_essay_block(
            "MOVES: [1] x.", {k: [] for k in ("introduces", "assumes",
                                            "promises", "debts")})
        check("a model label left out is rendered 'none' and reported",
              "FLOW: none" in block and missing == ["FLOW", "MOTIFS", "DOES NOT"],
              str((block, missing)))
        check("the reader load no longer parses the rendered lists: it "
              "declares paragraph records (step 5, ruling 10)",
              not hasattr(manifest, "_field")
              and manifest.INPUTS == (Input.PARAGRAPH_RECORDS,))

        print("ensure(level=essay) builds all three levels, lowest first:")
        para, sec, ess = RecordingParagraphLLM(), FakeSectionLLM(), FakeEssayLLM()
        llms = {"paragraph": para, "section": sec, "essay": ess}
        lines: list[str] = []
        report = sums.ensure(db, manuscript, llms, level="essay",
                             echo=lines.append)
        check("three cost lines, one per level, each on its own client",
              len(lines) == 3 and "paragraph level" in lines[0]
              and "section level" in lines[1] and "essay level" in lines[2]
              and "fake-essay" in lines[2], str(lines))
        check("the report nests: essay → section → paragraph",
              report["level"] == "essay" and report["section"]["level"] == "section"
              and report["section"]["paragraph"]["level"] == "paragraph",
              str(report.keys()))
        check("one essay call per unit, in reading order, both built",
              report["calls"] == 2 and report["built"] == ["doors.md", "poems.md"]
              and not report["skipped"], str(report))
        doors_call = ess.calls[0]
        check("the essay call carries the section summaries of the unit, "
              "numbered by section with COVER THESE, and not its text",
              "SECTIONS (3, in order):" in doors_call
              and "[2] ## First Section\nPOINT: The point of ## First Section."
              in doors_call
              and "COVER THESE: 1, 2, 3" in doors_call
              and "what remains when every Choice" not in doors_call,
              doors_call)
        check("the second unit is conditioned on the first's stored block "
              "(PRIOR SUMMARIES), autoregressive as before",
              "[doors.md]\nFLOW:" in ess.calls[1]
              and "(this is the first unit)" in doors_call, ess.calls[1])
        row = sums.all_summaries(db, manuscript["id"])["doors.md"]
        check("the stored block: FLOW first, then MOVES, the derived four, "
              "MOTIFS, DOES NOT",
              [row["summary"].index(lb + ":") for lb in sums.ESSAY_LABELS]
              == sorted(row["summary"].index(lb + ":") for lb in sums.ESSAY_LABELS),
              row["summary"])
        check("INTRODUCES is derived: the Field is defined in doors.md and "
              "nowhere earlier; ASSUMES holds what it only uses; DEBTS the "
              "backward reference's term, PROMISES the forward one's",
              "INTRODUCES: Field" in row["summary"]
              and "ASSUMES: Choice" in row["summary"]
              and "DEBTS: Choice" in row["summary"]
              and "PROMISES: the key" in row["summary"], row["summary"])
        check("the model's smuggled INTRODUCES line is not stored",
              "smuggled" not in row["summary"]
              and "the model must not write this" not in row["summary"],
              row["summary"])
        secs = sums.sections_of(ESSAY)
        check("model and sections_hash stored; every unit fresh",
              row["model"] == "fake/luna-essay"
              and row["sections_hash"] == sums.sections_hash(
                  [s["section_hash"] for s in secs])
              and all(r["state"] == "fresh" for r in sums.status(db, manuscript)),
              str(sums.status(db, manuscript)))
        check("coverage complete: the fake cites every section and names "
              "every heading", not report["misses"], str(report["misses"]))
        unit = sums.for_unit(db, manuscript, "doors.md")
        check("for_unit serves the essay with its derived lists as lists",
              unit["essay"]["introduces"] == ["Field"]
              and unit["essay"]["state"] == "fresh", str(unit["essay"]))

        print("laziness (ruling 3) and the deliberate pass (§8):")
        lines.clear()
        again = sums.ensure(db, manuscript, llms, level="essay",
                            echo=lines.append)
        check("a second ensure makes no call at any level and prints nothing",
              again["calls"] == 0 and again["section"]["calls"] == 0
              and again["section"]["paragraph"]["calls"] == 0 and lines == [],
              str(again))
        (ms / "doors.md").write_text(ESSAY.replace(
            "Within the Field a Choice specializes the Field into a world.",
            "Within the Field a Choice specializes the Field into one world."))
        with contextlib.redirect_stdout(io.StringIO()):
            api.collect(db, manuscript, {}, source="test")
        states = {r["file"]: r["state"] for r in sums.status(db, manuscript)}
        hs = sums.hierarchy_status(db, manuscript)[0]
        check("one paragraph edit: doors stale (source and sections hash), "
              "poems upstream_stale; one paragraph row and one section row "
              "wanting",
              states == {"doors.md": "stale", "poems.md": "upstream_stale"}
              and hs["essay"]["sections_moved"]
              and hs["paragraph"]["state"] == "stale"
              and len(hs["paragraph"]["missing"]) == 1
              and hs["section"]["missing"] == [2], str((states, hs)))
        lines.clear()
        report = sums.ensure(db, manuscript, llms, level="essay",
                             echo=lines.append)
        check("the lazy path costs one call per level for the edited essay "
              "and leaves the upstream_stale neighbour alone",
              report["section"]["paragraph"]["calls"] == 1
              and report["section"]["calls"] == 1
              and report["calls"] == 1 and report["built"] == ["doors.md"]
              and report["marked"] == 0, str(report))
        states = {r["file"]: r["state"] for r in sums.status(db, manuscript)}
        check("doors fresh again; poems still upstream_stale (tolerated)",
              states == {"doors.md": "fresh", "poems.md": "upstream_stale"},
              str(states))
        report = sums.rebuild(db, manuscript, llms)
        check("the deliberate rebuild refreshes the upstream_stale unit too",
              report["built"] == ["poems.md"]
              and all(r["state"] == "fresh" for r in sums.status(db, manuscript)),
              str(report))
        n_before = len(ess.calls)
        report = sums.rebuild(db, manuscript, llms, force=True)
        check("--all forces every essay row with no new lower-level call",
              report["built"] == ["doors.md", "poems.md"]
              and len(ess.calls) == n_before + 2
              and report["section"]["calls"] == 0, str(report))
        report = sums.rebuild(db, manuscript, llms, level="section", force=True)
        check("--level section --all forces the section rows, the paragraph "
              "level stays lazy, the essay level is not touched",
              report["level"] == "section" and report["calls"] == 5
              and report["paragraph"]["calls"] == 0
              and len(ess.calls) == n_before + 2, str(report))
        states = {r["file"]: r["state"] for r in sums.status(db, manuscript)}
        check("…and the essays still read fresh: the section hashes did "
              "not move", all(st == "fresh" for st in states.values()), str(states))

        print("a pre-hierarchy essay row is stale by sections_hash:")
        db.update("essay_summaries", sums.all_summaries(
            db, manuscript["id"])["poems.md"]["id"], {"sections_hash": ""})
        check("'' never matches the text's section structure, so the first "
              "full rebuild replaces it (ruling 6)",
              {r["file"]: r["state"] for r in sums.status(db, manuscript)}
              ["poems.md"] == "stale")
        sums.ensure(db, manuscript, llms, level="essay", echo=lambda _l: None)

        print("skips and misses are reported, never repaired:")
        (ms / "doors.md").write_text(ESSAY + "\nA closing paragraph.\n")
        with contextlib.redirect_stdout(io.StringIO()):
            api.collect(db, manuscript, {}, source="test")
        skipper = {"paragraph": FakeParagraphLLM(skip=(12,)),
                   "section": FakeSectionLLM(), "essay": ess}
        report = sums.ensure(db, manuscript, skipper, level="essay",
                             echo=lambda _l: None)
        check("a paragraph with no record skips its section, and the essay "
              "whose section is missing is skipped with the reason",
              report["section"]["skipped"] == [("doors.md", 3)]
              and report["skipped"] == [("doors.md", "section(s) 3 have no "
                                                     "current summary")]
              and report["built"] == [], str(report))
        sloppy = {"paragraph": RecordingParagraphLLM(), "section": FakeSectionLLM(),
                  "essay": FakeEssayLLM(drop_flow=True, skip=(2,))}
        report = sums.ensure(db, manuscript, sloppy, level="essay",
                             echo=lambda _l: None)
        check("a MOVES block that skips a section and a missing FLOW are a "
              "coverage miss and a missing label — stored all the same",
              report["built"] == ["doors.md"]
              and report["misses"][0][1]["missing"] == [2]
              and len(report["misses"][0][1]["flow_missing"]) == 3
              and report["labels_missing"] == [("doors.md", ["FLOW"])]
              and {r["file"]: r["state"] for r in sums.status(db, manuscript)}
              ["doors.md"] == "fresh", str(report))
        check("the cost line says so",
              "coverage miss: doors.md (missing [2]" in report["line"]
              and "missing a label: doors.md (FLOW)" in report["line"],
              report["line"])
        check("summarize outline's pieces: flow_of reads FLOW ('' here), "
              "point_of each section",
              sums.flow_of(sums.all_summaries(db, manuscript["id"])
                           ["doors.md"]["summary"]) == "none"
              and sums.point_of(sums._current_section_rows(
                  db, manuscript["id"], "doors.md")[
                  sums.sections_of(ESSAY + "\nA closing paragraph.\n")[0]
                  ["section_hash"]]["summary"]) == "The point of # The Doors.")

        print("guards:")
        try:
            sums.ensure(db, manuscript, {"paragraph": para, "section": sec},
                        level="essay", echo=lambda _l: None)
            ok = False
        except LookupError as err:
            ok = "essay" in str(err)
        check("a clients dict without the essay level is named", ok)

        class Off:
            enabled = False
            model = "off"

            def stats_line(self):
                return None

        (ms / "poems.md").write_text(POEMS + "\n## Coda\n\nA last line.\n")
        with contextlib.redirect_stdout(io.StringIO()):
            api.collect(db, manuscript, {}, source="test")
        sums.ensure(db, manuscript, llms, level="section", echo=lambda _l: None)
        try:
            sums.ensure(db, manuscript, Off(), level="essay",
                        files=["poems.md"], echo=lambda _l: None)
            ok = False
        except RuntimeError as err:
            ok = "poems.md" in str(err) and "disabled" in str(err)
        check("a dirty essay with the LLM off raises, naming why", ok)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print(f"\nall checks passed ({PASSED})")


if __name__ == "__main__":
    main_test()
