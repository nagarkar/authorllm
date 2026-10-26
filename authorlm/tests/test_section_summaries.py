"""Summary hierarchy, step 3 (docs/summary-hierarchy-design.md §3.3,
§3.5, §4.5, §11.3): the section-summary pass. One call per section with
the text as authority and the paragraph records as skeleton, conditioned
on the earlier sections' POINT lines; MOVES coverage as set equality on
the body paragraphs, misses reported; heading-only sections stored with
no call; `ensure(level="section")` rebuilds the paragraph level first and
prints one cost line per level. Fake LLMs throughout; the live recording
is tests/test_live_llm.py's `section-summary` section.

Run: python3 tests/test_section_summaries.py
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

from authorlm import api, summaries as sums  # noqa: E402
from test_paragraph_records import FakeLLM as FakeParagraphLLM  # noqa: E402
from test_summary_hierarchy import ESSAY, POEMS  # noqa: E402

PASSED = 0


def check(label: str, condition: bool, context: str = "") -> None:
    global PASSED
    assert condition, f"FAIL: {label}\n{context}"
    PASSED += 1
    print(f"  ok: {label}")


class FakeSectionLLM:
    """Writes the four labels; MOVES places every COVER THESE number in
    its own move unless `drop` (left out), `twice` (cited in two moves)
    or `extra` (a number not in the section) says otherwise."""

    enabled = True
    model = "fake/luna"

    def __init__(self, drop=(), twice=(), extra=()):
        self.calls: list[str] = []
        self.systems: list[str] = []
        self.drop, self.twice, self.extra = set(drop), set(twice), list(extra)

    def complete(self, system, user, thinking_budget=None):
        self.systems.append(system)
        self.calls.append(user)
        heading = re.search(r"^SECTION: (.*)$", user, re.M).group(1)
        wanted = [int(n) for n in re.search(
            r"COVER THESE: ([\d, ]+)", user).group(1).split(", ")]
        moves = [f"[{n}] carries move {n}" for n in wanted if n not in self.drop]
        moves += [f"[{n}] again" for n in self.twice]
        moves += [f"[{n}] from elsewhere" for n in self.extra]
        return (f"POINT: The point of {heading}.\n"
                "MOVES:\n" + "\n".join(moves) + "\n"
                "DEPENDS ON: none\nLEAVES OPEN: none\n")

    def stats_line(self):
        return f"LLM: {len(self.calls)} live call(s) (fake-luna)"


def main_test() -> None:
    root = Path(tempfile.mkdtemp(prefix="authorlm-secsum-"))
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

        print("prompt, client, length:")
        prompt = sums.section_prompt()
        check("the prompt names the four labels in order",
              [prompt.index(f"\n{lb}:") for lb in sums.SECTION_LABELS]
              == sorted(prompt.index(f"\n{lb}:") for lb in sums.SECTION_LABELS))
        llm = sums.section_llm({"llm": {"enabled": True},
                                "critique": {"summarizer_model": "openai/luna"}})
        check("[critique] summarizer_model, ledger purpose summaries.section",
              llm.model == "openai/luna" and llm.purpose == "summaries.section")
        llm = sums.section_llm({"llm": {"enabled": True},
                                "critique": {"summarizer_model": "openai/luna"},
                                "summaries": {"section_model": "x/y"}})
        check("[summaries] section_model wins when given", llm.model == "x/y")
        both = sums.clients({"llm": {"enabled": True}})
        check("clients() gives one client per built level",
              set(both) == set(sums.LEVELS)
              and both["paragraph"].purpose == "summaries.paragraph")
        check("length: floored at 30 for a tiny section",
              sums.section_target_length("one two three") == (30, 40))
        check("length: n/10 to n/7.5 in the middle",
              sums.section_target_length(" ".join(["w"] * 1000)) == (100, 133))
        check("length: capped at 250",
              sums.section_target_length(" ".join(["w"] * 5000)) == (240, 250))

        print("coverage is set equality (§3.3, ruling 8):")
        cov = sums.moves_coverage(
            "POINT: p\nMOVES:\n[2] [3] opens\n[4] closes\nDEPENDS ON: none",
            [2, 3, 4])
        check("every body paragraph in exactly one move is complete",
              cov["complete"], str(cov))
        cov = sums.moves_coverage(
            "POINT: p\nMOVES:\n[2]-[4] everything\n[2] again\n[9] stray\n"
            "DEPENDS ON: [3] is not a move", [2, 3, 4])
        check("a range covers only its ends, a repeat is duplicated, a "
              "stray is unknown, and other labels are not read",
              not cov["complete"] and cov["missing"] == [3]
              and cov["duplicated"] == [2] and cov["unknown"] == [9], str(cov))
        check("POINT is read up to the next label",
              sums.point_of("POINT: The Field\nremains.\nMOVES:\n[1] x")
              == "The Field remains.")

        print("ensure(level=section) builds the paragraph level first:")
        para, sec = FakeParagraphLLM(), FakeSectionLLM()
        lines: list[str] = []
        report = sums.ensure(db, manuscript,
                             {"paragraph": para, "section": sec},
                             level="section", files=["doors.md"],
                             echo=lines.append)
        check("the paragraph level was built on its own client",
              len(para.calls) == 3 and report["paragraph"]["calls"] == 3
              and report["paragraph"]["level"] == "paragraph", str(report))
        # doors.md: preamble, First Section, Second Section — each with a body.
        check("one section call per section", report["calls"] == 3
              and report["sections"] == 3 and len(sec.calls) == 3, str(report))
        check("two cost lines, one per level, each with its own stats",
              len(lines) == 2 and "paragraph level rebuilt" in lines[0]
              and "fake)" in lines[0] and "section level rebuilt" in lines[1]
              and "3 section(s) in 3 call(s)" in lines[1]
              and "fake-luna" in lines[1] and report["line"] == lines[1],
              str(lines))
        st = sums.level_status(db, manuscript, "doors.md", ESSAY)
        check("both levels fresh", st["paragraph"]["state"] == "fresh"
              and st["section"]["state"] == "fresh", str(st))
        check("no coverage miss and nothing skipped",
              not report["misses"] and not report["skipped"], str(report))

        print("the payload (§3.3 conditioning):")
        first, second, third = sec.calls
        check("the text is the authority: every paragraph numbered by file "
              "ordinal, heading included",
              "[3] ## First Section" in second and "[4] The Field is what"
              in second, second)
        check("the skeleton carries the paragraph gists by ordinal",
              "SKELETON:\n[4] gist of 4\n[5] gist of 5\n[6] gist of 6" in second,
              second)
        check("COVER THESE names the body paragraphs only",
              "COVER THESE: 4, 5, 6\n" in second, second)
        check("the first section sees no earlier POINT",
              "(this is the first section)" in first, first)
        check("later sections see the earlier POINT lines, by heading",
              "# The Doors: The point of # The Doors." in third
              and "## First Section: The point of ## First Section." in third
              and "MOVES" not in third.split("CONCEPTS")[0].split("EARLIER")[1],
              third)
        check("the graph slice and the target length are in the payload",
              "CONCEPTS (author's ratified definitions):" in second
              and "TARGET LENGTH: 30-40 words" in second, second)
        check("the row stores the summary, model and the ordered hashes",
              all(r["model"] == "fake/luna" and r["summary"].startswith("POINT:")
                  for r in db.all("SELECT * FROM section_summaries"))
              and sums.for_unit(db, manuscript, "doors.md")["sections"][1]
              ["summary"].startswith("POINT: The point of ## First Section"))

        print("lazy: a second ensure is free:")
        again = sums.ensure(db, manuscript, {"paragraph": para, "section": sec},
                            level="section", files=["doors.md"],
                            echo=lines.append)
        check("no call on either level, no line",
              again["calls"] == 0 and again["paragraph"]["calls"] == 0
              and len(lines) == 2 and again["line"] is None, str(again))

        print("an edit rebuilds one paragraph and one section:")
        edited = ESSAY.replace("Within the Field a Choice specializes",
                               "Within the Field each Choice specializes")
        (ms / "doors.md").write_text(edited)
        rep = sums.ensure(db, manuscript, {"paragraph": para, "section": sec},
                          level="section", files=["doors.md"], echo=lines.append)
        check("one paragraph call, one section call, for First Section",
              rep["paragraph"]["calls"] == 1 and rep["calls"] == 1
              and "SECTION: ## First Section" in sec.calls[-1], str(rep))
        check("the old section row is orphaned, the new one current",
              [r["status"] for r in db.all(
                  "SELECT status FROM section_summaries WHERE heading = "
                  "'## First Section' ORDER BY created_at")] == ["orphaned", "current"])
        check("the Second Section's call was not repeated; its POINT came "
              "from the stored row", len(sec.calls) == 4)

        print("coverage misses are reported, the row stored (§3.3):")
        bad = FakeSectionLLM(drop={5}, twice={3}, extra={99})
        rep = sums.ensure(db, manuscript,
                          {"paragraph": FakeParagraphLLM(), "section": bad},
                          level="section", files=["poems.md"], echo=lines.append)
        # poems.md: "# Poems" heading-only, then two verse sections with
        # one body paragraph each (ordinals 3 and 5).
        check("a heading-only section is stored without a call",
              rep["headings_only"] == 1 and rep["calls"] == 2, str(rep))
        check("the miss names the file, the section and what went wrong",
              [(f, n, cov["missing"], cov["duplicated"], cov["unknown"])
               for f, n, cov in rep["misses"]]
              == [("poems.md", 2, [], [3], [99]),
                  ("poems.md", 3, [5], [], [3, 99])]
              and "poems.md §2 (duplicated [3], unknown [99])" in rep["line"]
              and "§3 (missing [5], unknown [3, 99])" in rep["line"],
              str(rep))
        check("the section with the miss is stored (reported, not gated)",
              sums.level_status(db, manuscript, "poems.md", POEMS,
                                verse_file=True)["section"]["state"] == "fresh")
        heading_only = db.one(
            "SELECT * FROM section_summaries WHERE heading = '# Poems'")
        check("the heading-only row carries the fixed summary and no model",
              heading_only["summary"] == sums.HEADING_ONLY_SUMMARY
              and heading_only["model"] == "")

        print("a section whose records are incomplete is skipped:")
        (ms / "doors.md").write_text(ESSAY + "\nA closing paragraph, new.\n")
        skipper = FakeParagraphLLM(skip={12})
        rep = sums.ensure(db, manuscript,
                          {"paragraph": skipper, "section": sec},
                          level="section", files=["doors.md"], echo=lines.append)
        check("the paragraph got no record, so its section is skipped and "
              "named", rep["paragraph"]["missing"] == [("doors.md", 12)]
              and rep["skipped"] == [("doors.md", 3)] and rep["calls"] == 0
              and "1 section(s) skipped" in rep["line"], str(rep))
        check("the First Section, whose text came back, is restored "
              "without a call", rep["resurrected"] == 1
              and "1 restored" in rep["line"], str(rep))
        rep = sums.ensure(db, manuscript, {"paragraph": para, "section": sec},
                          level="section", files=["doors.md"], echo=lines.append)
        check("the next ensure completes both",
              rep["paragraph"]["records"] == 1 and rep["calls"] == 1
              and not rep["skipped"], str(rep))

        print("guards:")
        try:
            sums.ensure(db, manuscript, {"paragraph": para}, level="section")
            ok = False
        except LookupError as err:
            ok = "section level" in str(err)
        check("a missing client for a level is named", ok)
        (ms / "doors.md").write_text(ESSAY)
        rep = sums.ensure(db, manuscript, para, level="paragraph",
                          files=["doors.md"], echo=lines.append)
        check("level=paragraph alone leaves the section level stale",
              sums.level_status(db, manuscript, "doors.md", ESSAY)
              ["section"]["state"] == "stale" and "section" not in rep)

        class Off(FakeSectionLLM):
            enabled = False
        (ms / "doors.md").write_text(ESSAY.replace("A preamble paragraph",
                                                   "A prefatory paragraph"))
        try:
            sums.ensure(db, manuscript, {"paragraph": para, "section": Off()},
                        level="section", files=["doors.md"])
            ok = False
        except RuntimeError as err:
            ok = "disabled" in str(err)
        check("a dirty section with the LLM off raises, naming why", ok)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print(f"\nall checks passed ({PASSED})")


if __name__ == "__main__":
    main_test()
