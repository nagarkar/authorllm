"""Declared inputs (summary-hierarchy-design.md ruling 10, §6.7, step 5):
the `Input` enum, `context.supply`, the critique pass's OUTLINE and
REFERENCED SECTIONS blocks, and the reader load derived from paragraph
records. No model call anywhere in this suite: records and section
summaries are stored directly.

Run: python3 tests/test_context.py
"""

from __future__ import annotations

import os

os.environ["AUTHORLM_CONFIG"] = "/nonexistent/authorlm-test/config.toml"
os.environ["AUTHORLM_ENV"] = "/nonexistent/authorlm-test/.env"
os.environ["AUTHORLM_CLIENT"] = "none"

import contextlib
import io
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import _offline_prices  # noqa: E402,F401

from authorlm import api, context, manifest, passes, summaries as sums  # noqa: E402
from authorlm.context import Input  # noqa: E402

PASSED = 0


def check(label: str, condition: bool, ctx: str = "") -> None:
    global PASSED
    assert condition, f"FAIL: {label}\n{ctx}"
    PASSED += 1
    print(f"  ok: {label}")


FIELD = """# The Field

## What the Field is

The Field is what remains when every Choice has been subtracted.

A Choice specializes the Field into a world.
"""

DOORS = """# The Doors

## The hall

Every door in the hall opens onto the Field, as the Field essay showed.

The key is a Choice, and no key is ever found.
"""


def _record(gist, mentions=(), references=(), relations=()):
    return {"gist": gist, "claim": gist, "move": "states",
            "mentions": [{"term": t, "span": s, "role": r}
                         for t, s, r in mentions],
            "definitions": [],
            "relations": [{"from": a, "relation": rel, "to": b, "span": s}
                          for a, rel, b, s in relations],
            "references": [{"term": t, "direction": d, "target": None,
                            "span": s} for t, d, s in references],
            "depends_on": []}


def main_test() -> None:
    root = Path(tempfile.mkdtemp(prefix="authorlm-context-"))
    try:
        ws = root / "ws"
        ms = ws / "book"
        ms.mkdir(parents=True)
        (ms / "toc.toml").write_text(
            '[[chapter]]\nfile = "field.md"\n\n'
            '[[chapter]]\nfile = "doors.md"\n')
        (ms / "field.md").write_text(FIELD)
        (ms / "doors.md").write_text(DOORS)
        db = api.open_db(str(ws), create=True)
        manuscript = api.register_manuscript(db, "book", str(ms))
        with contextlib.redirect_stdout(io.StringIO()):
            api.collect(db, manuscript, {}, source="test")

        print("the vocabulary:")
        check("Input.parse maps front-matter names to members",
              Input.parse(["concepts", "referenced-sections"])
              == [Input.CONCEPT_SLICE, Input.REFERENCED_SECTIONS])
        try:
            Input.parse(["summaries"])
            refused = False
        except ValueError as err:
            refused = "summaries" in str(err) and "referenced-sections" in str(err)
        check("an unknown name is rejected by name, with the vocabulary",
              refused)
        check("the critique pass declares the two blocks §6.0 found missing",
              Input.SECTION_OUTLINE in passes.INPUTS
              and Input.REFERENCED_SECTIONS in passes.INPUTS)
        check("the reader load declares paragraph records",
              manifest.INPUTS == (Input.PARAGRAPH_RECORDS,))

        print("before any record exists:")
        got = context.supply(db, manuscript, "doors.md",
                             [Input.SECTION_OUTLINE, Input.REFERENCED_SECTIONS,
                              Input.READING_ORDER, Input.ESSAY_TEXT,
                              Input.VALIDATED_BELIEFS])
        check("every declared input is rendered, absent ones empty",
              set(got) == {Input.SECTION_OUTLINE, Input.REFERENCED_SECTIONS,
                           Input.READING_ORDER, Input.ESSAY_TEXT,
                           Input.VALIDATED_BELIEFS}
              and got[Input.SECTION_OUTLINE] == ""
              and got[Input.REFERENCED_SECTIONS] == ""
              and got[Input.VALIDATED_BELIEFS] == "")
        check("READING ORDER marks this essay",
              got[Input.READING_ORDER] == "1. field.md\n2. doors.md  <- this essay")
        check("ESSAY TEXT is the unit whole",
              got[Input.ESSAY_TEXT].strip() == DOORS.strip())
        try:
            context.supply(db, manuscript, "doors.md", [Input.PRIOR_RUNS])
            honest = False
        except NotImplementedError as err:
            honest = "filtering.prior_runs" in str(err)
        check("an input still rendered by its own pass says so by name",
              honest)
        rows = manifest.compute(db, manuscript)
        check("reader load with no records: every row missing, no debt",
              [r["state"] for r in rows] == ["missing", "missing"]
              and all(r["debt"] == [] for r in rows))

        print("records stored by hand (no model):")
        f_secs = sums.sections_of(FIELD)
        d_secs = sums.sections_of(DOORS)
        f_paras = {p["ordinal"]: p for s in f_secs for p in s["paragraphs"]}
        d_paras = {p["ordinal"]: p for s in d_secs for p in s["paragraphs"]}
        for sec, paras in ((f_secs, f_paras), (d_secs, d_paras)):
            for s in sec:
                for p in s["paragraphs"]:
                    if p["kind"] == "heading":
                        sums.store_paragraph_record(
                            db, manuscript, "field.md" if paras is f_paras
                            else "doors.md", p, None, "")
        sums.store_paragraph_record(db, manuscript, "field.md", f_paras[3], _record(
            "Field defined as the remainder after Choice.",
            mentions=[("the Field", "The Field is what remains", "defines"),
                      ("Choice", "every Choice has been subtracted", "uses")]),
            "fake")
        sums.store_paragraph_record(db, manuscript, "field.md", f_paras[4], _record(
            "A Choice makes a world of the Field.",
            mentions=[("Choice", "A Choice specializes the Field", "introduces"),
                      ("the Field", "specializes the Field into a world", "uses")],
            relations=[("Choice", "specializes", "the Field",
                        "A Choice specializes the Field into a world")]),
            "fake")
        sums.store_paragraph_record(db, manuscript, "doors.md", d_paras[3], _record(
            "Doors open onto the Field, recalling the Field essay.",
            mentions=[("the Field", "opens onto the Field", "uses"),
                      ("the hall", "Every door in the hall", "introduces")],
            references=[("the Field", "back", "as the Field essay showed")]),
            "fake")
        sums.store_paragraph_record(db, manuscript, "doors.md", d_paras[4], _record(
            "The key is a Choice; keys are never found.",
            mentions=[("Choice", "The key is a Choice", "uses"),
                      ("the key", "The key is a Choice", "introduces")]),
            "fake")
        sums.store_section_summary(
            db, manuscript, "doors.md", d_secs[1],
            "POINT: The hall's doors all give onto the Field.\n"
            "MOVES: [3] states the opening; [4] names the key.\n"
            "DEPENDS ON: none\nLEAVES OPEN: what a key would be.", "fake")

        got = context.supply(db, manuscript, "doors.md",
                             [Input.SECTION_OUTLINE, Input.REFERENCED_SECTIONS,
                              Input.PARAGRAPH_RECORDS])
        outline = got[Input.SECTION_OUTLINE]
        check("OUTLINE lists every section in order with its paragraph range",
              outline.startswith("[§1] # The Doors (paragraphs 1-1)")
              and "[§2] ## The hall (paragraphs 2-4)" in outline, outline)
        check("a section with no summary is marked, not hidden",
              "!! no section summary" in outline.split("[§2]")[0], outline)
        check("a section with a summary shows it",
              "POINT: The hall's doors" in outline, outline)
        refd = context.referenced_sections(
            db, manuscript, "doors.md", sums.capture(db, manuscript)[0])
        check("a backward reference resolves to the section of ANOTHER essay "
              "where the term was first defined",
              len(refd) == 1 and refd[0]["file"] == "field.md"
              and refd[0]["section_ordinal"] == 2
              and refd[0]["heading"] == "## What the Field is"
              and refd[0]["terms"] == ["the Field"], str(refd))
        check("the referenced block carries the section's TEXT and hash",
              "The Field is what remains" in got[Input.REFERENCED_SECTIONS]
              and refd[0]["section_hash"] in got[Input.REFERENCED_SECTIONS]
              and "referred to for: the Field" in got[Input.REFERENCED_SECTIONS])
        check("no record text reaches the referenced block (text, not summary)",
              "Field defined as the remainder" not in got[Input.REFERENCED_SECTIONS])
        check("PARAGRAPH RECORDS is one JSON line per recorded paragraph",
              got[Input.PARAGRAPH_RECORDS].count("\n") == 3
              and '"gist": "The key is a Choice' in got[Input.PARAGRAPH_RECORDS])

        print("the critique pass renders the two blocks:")
        ctx = {"file": "doors.md", "paragraphs": ["# The Doors", "## The hall"],
               "style": "", "intents": [], "beliefs": [], "concepts": {},
               "learnings": [], "before": [], "after": [],
               "blocks": context.supply(db, manuscript, "doors.md",
                                        list(passes.SUPPLIED))}
        msg = passes.render_user_message(ctx)
        check("OUTLINE and REFERENCED SECTIONS sit between BOOK CONTEXT and "
              "THE ESSAY",
              msg.index("=== BOOK CONTEXT — AFTER ===")
              < msg.index("=== OUTLINE — this essay's sections ===")
              < msg.index("=== REFERENCED SECTIONS")
              < msg.index("=== THE ESSAY"), msg)
        check("a ctx without blocks still renders (both blocks '(none)')",
              passes.render_user_message({**ctx, "blocks": {}})
              .count("(none)") >= 2)

        print("the reader load from records:")
        rows = manifest.compute(db, manuscript)
        by = {r["file"]: r for r in rows}
        check("field.md introduces two terms and owes nothing",
              by["field.md"]["introduces"] == 2 and by["field.md"]["debt"] == []
              and by["field.md"]["state"] == "fresh", str(rows))
        check("doors.md introduces its own terms and owes nothing: the Field "
              "and Choice were supplied by field.md",
              by["doors.md"]["introduces"] == 2 and by["doors.md"]["debt"] == [],
              str(rows))
        (ms / "doors.md").write_text(DOORS + "\nA new paragraph using Dharma.\n")
        with contextlib.redirect_stdout(io.StringIO()):
            api.collect(db, manuscript, {}, source="test")
        rows = manifest.compute(db, manuscript)
        by = {r["file"]: r for r in rows}
        check("an edited essay reads stale and is still shown, from the "
              "records it has",
              by["doors.md"]["state"] == "stale"
              and by["doors.md"]["introduces"] == 2, str(rows))
        lines = manifest.render_lines(rows)
        check("the rendered report marks the stale row",
              any("doors.md" in ln and "!! stale" in ln for ln in lines),
              "\n".join(lines))
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print(f"\n{PASSED} checks passed.")


if __name__ == "__main__":
    main_test()
