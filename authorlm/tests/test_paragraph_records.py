"""Summary hierarchy, step 2 (docs/summary-hierarchy-design.md §3.2,
§3.5, §4.5, §11.2): the paragraph-records pass. One call per heading
section, headings with no call, the span check counted in the build
report, the 24,000-character split, lazy rebuild of dirty rows only, and
the one cost line. A fake LLM throughout; the live recording is
tests/test_live_llm.py's `paragraph-records` section.

Run: python3 tests/test_paragraph_records.py
"""

from __future__ import annotations

import os

os.environ["AUTHORLM_CONFIG"] = "/nonexistent/authorlm-test/config.toml"
os.environ["AUTHORLM_ENV"] = "/nonexistent/authorlm-test/.env"
os.environ["AUTHORLM_CLIENT"] = "none"

import contextlib
import io
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import _offline_prices  # noqa: E402,F401

from authorlm import api, summaries as sums  # noqa: E402
from authorlm.extraction import VALID_RELATIONS  # noqa: E402
from test_summary_hierarchy import ESSAY, POEMS, REFRAIN  # noqa: E402

PASSED = 0


def check(label: str, condition: bool, context: str = "") -> None:
    global PASSED
    assert condition, f"FAIL: {label}\n{context}"
    PASSED += 1
    print(f"  ok: {label}")


class FakeLLM:
    """Answers every RECORD THESE number with a record whose spans are
    the paragraph's first five words (verbatim), plus one invented span
    and one unknown relation per call so the check has something to
    drop. `skip` numbers get no record."""

    enabled = True
    model = "fake/flash"

    def __init__(self, skip=()):
        self.calls: list[str] = []
        self.skip = set(skip)
        self.system = None

    def complete_json(self, system, user, thinking_budget=None):
        self.system = system
        self.calls.append(user)
        wanted = [int(n) for n in re.search(
            r"RECORD THESE: ([\d, ]+)", user).group(1).split(", ")]
        texts = dict((int(n), t) for n, t in re.findall(
            r"^\[(\d+)\] (.*?)(?=\n\n\[\d+\] |\Z)",
            user.split("THE SECTION:\n\n", 1)[1], re.S | re.M))
        out = []
        for i, n in enumerate(wanted):
            if n in self.skip:
                continue
            span = " ".join(texts[n].split(" ")[:5])
            out.append({
                "n": n, "gist": f"gist of {n}", "claim": None,
                "move": "states",
                "mentions": [{"term": "Field", "span": span, "role": "uses"},
                             {"term": "ghost", "span": "not in the text",
                              "role": "uses"}],
                "definitions": [{"term": "Field", "sentence": span}],
                "relations": [{"from": "Field", "relation": "haunts",
                               "to": "Choice", "span": span}],
                "references": [],
                "depends_on": [wanted[0]] if i else [n + 1]})
        return out

    def stats_line(self):
        return f"LLM: {len(self.calls)} live call(s) (fake)"


def main_test() -> None:
    root = Path(tempfile.mkdtemp(prefix="authorlm-pararec-"))
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

        print("prompt and client:")
        prompt = sums.paragraph_prompt()
        check("the prompt names every move and every valid relation",
              all(m in prompt for m in sums.MOVES)
              and all(r in prompt for r in VALID_RELATIONS)
              and "<<" not in prompt)
        check("the prompt names every mention role and direction",
              all(r in prompt for r in sums.MENTION_ROLES)
              and all(f'"{d}"' in prompt for d in sums.REFERENCE_DIRECTIONS))
        llm = sums.paragraph_llm({"llm": {"enabled": True}})
        check("no config: the flash tier, ledger purpose summaries.paragraph",
              llm.model == sums.DEFAULT_PARAGRAPH_MODEL
              and llm.purpose == "summaries.paragraph", llm.model)
        llm = sums.paragraph_llm({"llm": {"enabled": True},
                                  "critique": {"summarizer_model": "openai/luna"}})
        check("without [summaries] paragraph_model it falls through to "
              "[critique] summarizer_model", llm.model == "openai/luna")
        llm = sums.paragraph_llm({"llm": {"enabled": True},
                                  "critique": {"summarizer_model": "openai/luna"},
                                  "summaries": {"paragraph_model": "gemini/x"}})
        check("[summaries] paragraph_model wins", llm.model == "gemini/x")

        print("first ensure:")
        fake = FakeLLM()
        lines: list[str] = []
        report = sums.ensure(db, manuscript, fake, files=["doors.md"],
                             echo=lines.append)
        # doors.md: preamble (1 body para), First Section (3, refrain
        # among them), Second Section (4, refrain again): 3 calls, the
        # refrain recorded once, 3 headings with no call.
        check("one call per section with a body; headings get no call",
              report["calls"] == 3 and len(fake.calls) == 3, str(report))
        check("every distinct non-heading paragraph got a record, the "
              "refrain once", report["records"] == 7, str(report))
        check("three heading rows stored without a call",
              report["headings"] == 3, str(report))
        st = sums.level_status(db, manuscript, "doors.md", ESSAY)
        check("the paragraph level is fresh after ensure",
              st["paragraph"]["state"] == "fresh", str(st["paragraph"]))
        check("the refrain is asked for once and shown once per occurrence",
              sum(c.count(f"] {REFRAIN}") for c in fake.calls) == 2
              and sum(c.split("RECORD THESE: ")[1].split("\n")[0].count(",")
                      for c in fake.calls) == 7 - 3,
              "\n---\n".join(fake.calls))
        check("only the asked file was touched",
              sums.level_status(db, manuscript, "poems.md", POEMS,
                                verse_file=True)["paragraph"]["state"]
              == "missing")

        print("the span and vocabulary check (§7 guarantee 1, 4):")
        check("the invented span is dropped and counted, once per record",
              report["dropped"]["span"] == 7, str(report["dropped"]))
        check("the unknown relation is dropped and counted",
              report["dropped"]["relation"] == 7, str(report["dropped"]))
        recs = sums.paragraph_records(db, manuscript, files=["doors.md"])
        body = [r for r in recs if r["kind"] != "heading"]
        check("stored mentions keep only verbatim spans",
              all(len(r["mentions"]) == 1 and r["mentions"][0]["span"]
                  in next(p["text"] for p in sums.paragraphs_of(ESSAY)
                          if p["para_hash"] == r["para_hash"])
                  for r in body), json.dumps(body[:1]))
        check("definitions survive the byte check, relations do not",
              all(len(r["definitions"]) == 1 and not r["relations"]
                  for r in body))
        check("model stored on body rows, '' on heading rows",
              all(r["model"] == "fake/flash" for r in db.all(
                  "SELECT model FROM paragraph_summaries WHERE kind != 'heading'"))
              and all(r["model"] == "" for r in db.all(
                  "SELECT model FROM paragraph_summaries WHERE kind = 'heading'")))
        heads = [r for r in recs if r["kind"] == "heading"]
        check("heading rows carry the empty record",
              len(heads) == 3 and all(r["gist"] == "" and not r["mentions"]
                                      for r in heads))

        print("depends_on (§7 guarantee 5):")
        sec2 = sums.sections_of(ESSAY)[2]
        first = sec2["paragraphs"][1]
        # The refrain's one row was recorded in the First Section, so
        # its depends_on points there; every other later paragraph here
        # was recorded in this call.
        later = [r for r in body if r["section_ordinal"] == 3
                 and r["ordinal"] > first["ordinal"]
                 and r["para_hash"] != sums.para_hash(REFRAIN)]
        check("an earlier ordinal of the same section becomes its hash",
              later and all(r["depends_on"] == [first["para_hash"]]
                            for r in later), json.dumps(later))
        check("a forward ordinal is dropped and counted",
              not next(r for r in body if r["ordinal"] == first["ordinal"]
                       )["depends_on"]
              and report["dropped"]["depends_on"] == 3, str(report["dropped"]))

        print("the cost line (ruling 9):")
        check("one line, with the client's stats line",
              len(lines) == 1 and "paragraph level rebuilt" in lines[0]
              and "7 record(s) in 3 call(s)" in lines[0]
              and "LLM: 3 live call(s)" in lines[0]
              and report["line"] == lines[0], str(lines))

        print("lazy: a second ensure is free:")
        again = sums.ensure(db, manuscript, fake, files=["doors.md"],
                            echo=lines.append)
        check("nothing dirty, no call, no line",
              again["calls"] == 0 and len(fake.calls) == 3 and len(lines) == 1
              and again["line"] is None, str(again))

        print("an edit rebuilds only its section:")
        edited = ESSAY.replace("Within the Field a Choice specializes",
                               "Within the Field each Choice specializes")
        (ms / "doors.md").write_text(edited)
        rep = sums.ensure(db, manuscript, fake, files=["doors.md"],
                          echo=lines.append)
        check("one call, one record, for the edited paragraph's section",
              rep["calls"] == 1 and rep["records"] == 1
              and "RECORD THESE: " in fake.calls[-1]
              and "## First Section" in fake.calls[-1], str(rep))
        check("the unchanged neighbours ride along as context only",
              fake.calls[-1].count("\n[") >= 2
              and fake.calls[-1].split("RECORD THESE: ")[1]
              .split("\n")[0].count(",") == 0, fake.calls[-1])
        old = db.one("SELECT status FROM paragraph_summaries WHERE para_hash = ?",
                     (sums.para_hash(
                         "Within the Field a Choice specializes the Field "
                         "into a world."),))
        check("the old paragraph's row is orphaned by the rebuild",
              old and old["status"] == "orphaned", str(old and dict(old)))

        print("an orphan whose text comes back costs no call:")
        (ms / "doors.md").write_text(ESSAY)
        rep = sums.ensure(db, manuscript, fake, files=["doors.md"],
                          echo=lines.append)
        check("restored without a model call",
              rep["calls"] == 0 and rep["resurrected"] == 1
              and len(fake.calls) == 4, str(rep))
        check("…and the level is fresh again",
              sums.level_status(db, manuscript, "doors.md", ESSAY)
              ["paragraph"]["state"] == "fresh")

        print("a record the model omits stays dirty:")
        skipper = FakeLLM(skip={2, 3})
        rep = sums.ensure(db, manuscript, skipper, files=["poems.md"],
                          echo=lines.append)
        st = sums.level_status(db, manuscript, "poems.md", POEMS,
                               verse_file=True)
        check("missing records are reported and the level stays stale",
              rep["missing"] == [("poems.md", 3)]
              and st["paragraph"]["state"] == "stale"
              and "got no record" in rep["line"], str(rep))
        check("verse rows are stored as kind=verse",
              all(r["kind"] == "verse" for r in sums.paragraph_records(
                  db, manuscript, files=["poems.md"]) if r["kind"] != "heading"))
        rep = sums.ensure(db, manuscript, FakeLLM(), files=["poems.md"],
                          echo=lines.append)
        check("the next ensure asks for exactly the missing one",
              rep["calls"] == 1 and rep["records"] == 1, str(rep))

        print("the 24,000-character split (§3.5):")
        big = [{"ordinal": i, "text": "w" * 9000, "para_hash": str(i)}
               for i in range(1, 6)]
        chunks = sums._chunks(big)
        check("chunks cut at paragraph boundaries under the cap",
              [len(c) for c in chunks] == [2, 2, 1]
              and all(sum(len(p["text"]) for p in c) <= sums.MAX_SECTION_CHARS
                      for c in chunks))
        check("a single paragraph over the cap is a chunk of its own",
              [len(c) for c in sums._chunks(
                  [{"ordinal": 1, "text": "x" * 30000, "para_hash": "a"},
                   {"ordinal": 2, "text": "y", "para_hash": "b"}])] == [1, 1])
        long_para = "The Field " + "and the Field " * 700
        long_essay = ("# Long\n\n" + "\n\n".join(
            long_para + f"part {i}." for i in range(3)) + "\n")
        (ms / "long.md").write_text(long_essay)
        (ms / "toc.toml").write_text(
            (ms / "toc.toml").read_text() + '\n[[chapter]]\nfile = "long.md"\n')
        fake2 = FakeLLM()
        rep = sums.ensure(db, manuscript, fake2, files=["long.md"],
                          echo=lines.append)
        check("a section over the cap takes more than one call",
              rep["calls"] == 2 and rep["records"] == 3, str(rep))

        print("guards:")
        try:
            sums.ensure(db, manuscript, fake, level="section")
            ok = False
        except ValueError as err:
            ok = "no builder yet" in str(err)
        check("an unbuilt level is named, not silently skipped", ok)
        try:
            sums.ensure(db, manuscript, fake, files=["nope.md"])
            ok = False
        except LookupError as err:
            ok = "nope.md" in str(err)
        check("an unknown file is named", ok)
        (ms / "doors.md").write_text(ESSAY + "\nA new closing paragraph.\n")

        class Off(FakeLLM):
            enabled = False
        try:
            sums.ensure(db, manuscript, Off(), files=["doors.md"])
            ok = False
        except RuntimeError as err:
            ok = "disabled" in str(err)
        check("a dirty paragraph with the LLM off raises, naming why", ok)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print(f"\nall checks passed ({PASSED})")


if __name__ == "__main__":
    main_test()
