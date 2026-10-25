"""Summary hierarchy, step 1 (docs/summary-hierarchy-design.md §4, §7,
§11.1): paragraph kinds, content hashes, section hashes, the two new
tables, freshness derived at read time, and the read API over current
rows. No model call anywhere in this suite.

Run: python3 tests/test_summary_hierarchy.py
"""

from __future__ import annotations

import os

os.environ["AUTHORLM_CONFIG"] = "/nonexistent/authorlm-test/config.toml"
os.environ["AUTHORLM_ENV"] = "/nonexistent/authorlm-test/.env"
os.environ["AUTHORLM_CLIENT"] = "none"

import contextlib
import io
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import _offline_prices  # noqa: E402,F401

from authorlm import api, summaries as sums  # noqa: E402
from authorlm.db import ko_fields  # noqa: E402

PASSED = 0


def check(label: str, condition: bool, context: str = "") -> None:
    global PASSED
    assert condition, f"FAIL: {label}\n{context}"
    PASSED += 1
    print(f"  ok: {label}")


REFRAIN = "He found every door, and had no key."

ESSAY = f"""# The Doors

A preamble paragraph before any section. It names the Field once.

## First Section

The Field is what remains when every Choice has been subtracted. We call
this remainder the Field.

{REFRAIN}

Within the Field a Choice specializes the Field into a world.

## Second Section

**Socrates:** Is the Field then a thing?
**Glaucon:** It is not a thing; it is what things are chosen from.

the door is open
the key is lost
the hall is long

{REFRAIN}

As the Metaphysic showed, every Choice recalls the Field it left; a later
essay will show what the Recapitulation makes of the key.
"""

POEMS = """# Poems

## The Hall

A hall of doors.
No key in hand.

## The Key

Lost, and so
never lost.
"""


def main_test() -> None:
    root = Path(tempfile.mkdtemp(prefix="authorlm-hier-"))
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

        print("schema:")
        cols = db._columns("paragraph_summaries")
        check("paragraph_summaries has the §4.1 columns",
              {"para_hash", "ordinal", "section_ordinal", "kind", "record",
               "model", "status"} <= cols, str(cols))
        cols = db._columns("section_summaries")
        check("section_summaries has the §4.2 columns",
              {"section_hash", "section_ordinal", "heading", "para_hashes",
               "summary", "model", "status"} <= cols, str(cols))
        cols = db._columns("essay_summaries")
        check("essay_summaries gained sections_hash and model (§4.3)",
              {"sections_hash", "model"} <= cols, str(cols))

        print("kinds:")
        secs = sums.sections_of(ESSAY)
        paras = sums.paragraphs_of(ESSAY)
        kinds = [p["kind"] for p in paras]
        check("three sections: preamble under the title, then two headed",
              len(secs) == 3 and [s["heading"] for s in secs]
              == ["# The Doors", "## First Section", "## Second Section"],
              str([s["heading"] for s in secs]))
        check("heading paragraphs are kind=heading", kinds[0] == "heading"
              and kinds[2] == "heading" and kinds[6] == "heading", str(kinds))
        check("speaker-prefixed block is dialogue", kinds[7] == "dialogue",
              str(kinds))
        check("block of short lines is verse", kinds[8] == "verse", str(kinds))
        check("everything else is prose",
              all(kinds[i] == "prose" for i in (1, 3, 4, 5, 9, 10)),
              str(kinds))
        check("ordinals are positions in the whole file",
              [p["ordinal"] for p in paras] == list(range(1, 12)),
              str([p["ordinal"] for p in paras]))
        check("a lone 'Note:' line is prose, not dialogue",
              sums.paragraph_kind("Note: a colon in prose, nothing more.")
              == "prose")
        check("in a verse file every non-heading block is verse",
              [p["kind"] for p in sums.paragraphs_of(POEMS, verse_file=True)]
              == ["heading", "heading", "verse", "heading", "verse"])
        check("verse_files_of reads the toc's form flag",
              sums.verse_files_of(manuscript) == {"poems.md"})

        print("hashes:")
        refrains = [p for p in paras if p["text"] == REFRAIN]
        check("the refrain appears twice with ONE hash",
              len(refrains) == 2
              and refrains[0]["para_hash"] == refrains[1]["para_hash"]
              and refrains[0]["ordinal"] != refrains[1]["ordinal"])
        check("trailing whitespace does not move the hash",
              sums.para_hash("a b \n c  ") == sums.para_hash("a b\n c"))
        check("a word change does", sums.para_hash("a b") != sums.para_hash("a c"))
        check("a line break inside a block counts (verse)",
              sums.para_hash("a\nb") != sums.para_hash("a b"))
        h1 = secs[1]["section_hash"]
        reordered = dict(secs[1], para_hashes=list(reversed(secs[1]["para_hashes"])))
        check("section hash is over ORDERED paragraph hashes",
              sums.section_hash(reordered["para_hashes"]) != h1)
        check("the refrain sits in two sections' lists",
              refrains[0]["para_hash"] in secs[1]["para_hashes"]
              and refrains[0]["para_hash"] in secs[2]["para_hashes"])
        check("a file without headings is one section",
              len(sums.sections_of("one.\n\ntwo.")) == 1
              and sums.sections_of("one.\n\ntwo.")[0]["heading"] is None)

        print("status before any row:")
        st = sums.level_status(db, manuscript, "doors.md", ESSAY)
        check("paragraph level missing, 10 distinct rows needed",
              st["paragraph"]["state"] == "missing"
              and st["paragraph"]["needed"] == 10
              and len(st["paragraph"]["missing"]) == 10, str(st))
        check("section level missing, 3 needed",
              st["section"]["state"] == "missing"
              and st["section"]["missing"] == [1, 2, 3], str(st))
        check("essay missing", st["essay"]["state"] == "missing")
        check("for_unit returns None per absent level",
              sums.for_unit(db, manuscript, "doors.md") ==
              {"file": "doors.md", "essay": None, "sections": None,
               "paragraphs": None, "paragraph_level_stale": False,
               "section_level_stale": False},
              str(sums.for_unit(db, manuscript, "doors.md")))
        check("paragraph_records and mentions are empty",
              sums.paragraph_records(db, manuscript) == []
              and sums.mentions(db, manuscript) == [])

        print("storing records:")
        field_def = paras[3]
        rec = {
            "gist": "The Field is the remainder after every Choice.",
            "claim": "The Field is what remains when every Choice has been subtracted.",
            "move": "defines",
            "mentions": [
                {"term": "the Field", "span": "The Field is what remains", "role": "defines"},
                {"term": "Choice", "span": "every Choice has been subtracted", "role": "uses"},
                {"term": "Field", "span": "this remainder the Field", "role": "names"},
                {"term": "Ghost", "span": "a span not in the paragraph", "role": "uses"},
                {"term": "Oddrole", "span": "The Field", "role": "shouts"},
            ],
            "definitions": [
                {"term": "the Field", "sentence": "We call\nthis remainder the Field."},
                {"term": "Nope", "sentence": "never written"},
            ],
            "relations": [
                {"from": "the Field", "relation": "contrasts_with", "to": "Choice",
                 "span": "every Choice has been subtracted"},
                {"from": "the Field", "relation": "haunts", "to": "Choice",
                 "span": "The Field"},
            ],
            "references": [],
            "depends_on": [],
        }
        row, dropped = sums.store_paragraph_record(
            db, manuscript, "doors.md", field_def, rec, "flash-model")
        stored = json.loads(row["record"])
        check("spans are byte-checked: the ghost mention and the unwritten "
              "definition are dropped and counted",
              [m["term"] for m in stored["mentions"]]
              == ["the Field", "Choice", "Field"]
              and [d["term"] for d in stored["definitions"]] == ["the Field"]
              and dropped["span"] == 2, f"{stored}\n{dropped}")
        check("unknown role and relation are dropped and counted",
              [r["relation"] for r in stored["relations"]] == ["contrasts_with"]
              and dropped["role"] == 1 and dropped["relation"] == 1, str(dropped))
        check("row carries kind, ordinal, section and model",
              row["kind"] == "prose" and row["ordinal"] == 4
              and row["section_ordinal"] == 2 and row["model"] == "flash-model"
              and row["status"] == "current")
        hrow, _ = sums.store_paragraph_record(
            db, manuscript, "doors.md", paras[0], None, "flash-model")
        check("a heading row stores the empty record with no model",
              json.loads(hrow["record"]) == sums.EMPTY_RECORD
              and hrow["model"] == "" and hrow["kind"] == "heading")
        rrow, _ = sums.store_paragraph_record(
            db, manuscript, "doors.md", refrains[0],
            {"gist": "The refrain.", "move": "restates",
             "mentions": [{"term": "key", "span": "had no key", "role": "uses"}]},
            "flash-model")
        again, _ = sums.store_paragraph_record(
            db, manuscript, "doors.md", refrains[1],
            {"gist": "The refrain, again.", "move": "restates",
             "mentions": [{"term": "key", "span": "had no key", "role": "uses"}]},
            "flash-model")
        check("storing the refrain's second occurrence updates the ONE row",
              again["id"] == rrow["id"] and again["ordinal"] == refrains[1]["ordinal"]
              and db.one("SELECT COUNT(*) AS n FROM paragraph_summaries")["n"] == 3)
        last = paras[10]
        sums.store_paragraph_record(
            db, manuscript, "doors.md", last,
            {"gist": "Looks back and forward.", "move": "recalls",
             "mentions": [{"term": "Choice", "span": "every Choice recalls", "role": "uses"},
                          {"term": "the Field", "span": "the Field it left", "role": "uses"}],
             "references": [
                 {"term": "the Field", "direction": "back", "target": "the Metaphysic",
                  "span": "As the Metaphysic showed"},
                 {"term": "key", "direction": "forward", "target": "the Recapitulation",
                  "span": "a later\nessay will show"},
                 {"term": "x", "direction": "sideways", "span": "every Choice"}],
             "depends_on": [field_def["para_hash"]]},
            "flash-model")

        print("status with partial rows:")
        st = sums.level_status(db, manuscript, "doors.md", ESSAY)
        check("paragraph level is stale (4 of 10 current), the rest named",
              st["paragraph"]["state"] == "stale"
              and st["paragraph"]["current"] == 4
              and len(st["paragraph"]["missing"]) == 6, str(st["paragraph"]))

        print("section rows:")
        for sec in secs:
            sums.store_section_summary(
                db, manuscript, "doors.md", sec,
                f"POINT: section {sec['section_ordinal']}.\n", "luna")
        st = sums.level_status(db, manuscript, "doors.md", ESSAY)
        check("section level fresh once every section has a row",
              st["section"]["state"] == "fresh" and st["section"]["missing"] == [],
              str(st["section"]))
        srow = db.one("SELECT * FROM section_summaries WHERE section_ordinal = 2")
        check("section row keeps the ordered paragraph hashes and heading",
              json.loads(srow["para_hashes"]) == secs[1]["para_hashes"]
              and srow["heading"] == "## First Section"
              and srow["section_hash"] == secs[1]["section_hash"])

        print("essay row:")
        erow = ko_fields("es")
        erow.update(manuscript_id=manuscript["id"], file="doors.md",
                    summary="MOVES: the doors.", source_hash=sums._hash(ESSAY),
                    upstream_hash=sums._hash(""), upstream_stale=0,
                    sections_hash=sums.sections_hash(
                        [s["section_hash"] for s in secs]), model="luna")
        db.insert("essay_summaries", erow)
        st = sums.level_status(db, manuscript, "doors.md", ESSAY)
        check("essay fresh, sections not moved",
              st["essay"] == {"state": "fresh", "sections_moved": False}, str(st))

        print("for_unit:")
        unit = sums.for_unit(db, manuscript, "doors.md")
        check("essay projection: summary, hash, state, derived lists",
              unit["essay"]["summary"] == "MOVES: the doors."
              and unit["essay"]["state"] == "fresh"
              and unit["essay"]["stale"] is False
              and unit["essay"]["introduces"] == ["the Field"]
              and unit["essay"]["assumes"] == ["Choice", "key"]
              and unit["essay"]["promises"] == ["key"]
              and unit["essay"]["debts"] == ["the Field"], str(unit["essay"]))
        check("sections: heading, ordinal range, hash, summary, terms",
              [s["paragraphs"] for s in unit["sections"]]
              == [[1, 2], [3, 6], [7, 11]]
              and unit["sections"][1]["source_hash"] == secs[1]["section_hash"]
              and unit["sections"][1]["summary"] == "POINT: section 2."
              and unit["sections"][1]["terms"] == ["the Field", "Choice", "Field", "key"]
              and unit["sections"][2]["terms"] == ["key", "Choice", "the Field"]
              and not unit["section_level_stale"], str(unit["sections"]))
        ps = unit["paragraphs"]
        check("paragraphs: one entry per position, refrain twice, gaps stale",
              len(ps) == 11 and ps[3]["gist"].startswith("The Field is")
              and ps[4]["gist"] == ps[9]["gist"] == "The refrain, again."
              and ps[4]["source_hash"] == ps[9]["source_hash"]
              and ps[1]["gist"] is None and ps[1]["stale"]
              and unit["paragraph_level_stale"], str(ps))
        check("paragraph terms come from mentions",
              ps[3]["terms"] == ["the Field", "Choice", "Field"])

        print("paragraph_records and mentions:")
        recs = sums.paragraph_records(db, manuscript)
        check("records in reading order, refrain once per occurrence, "
              "ordinals from the text",
              [r["ordinal"] for r in recs] == [1, 4, 5, 10, 11], str(recs))
        check("a record carries every §7 field",
              set(recs[1]) >= {"file", "para_hash", "ordinal", "section_ordinal",
                               "kind", "gist", "claim", "move", "mentions",
                               "definitions", "relations", "references",
                               "depends_on"}, str(recs[1].keys()))
        check("depends_on is kept as hashes; unknown direction dropped",
              recs[4]["depends_on"] == [field_def["para_hash"]]
              and [r["direction"] for r in recs[4]["references"]]
              == ["back", "forward"])
        check("files= restricts", sums.paragraph_records(
            db, manuscript, files=["poems.md"]) == [])
        ms_ = sums.mentions(db, manuscript)
        check("mentions flattened across the book, refrain counted twice",
              [m["ordinal"] for m in ms_ if m["term"] == "key"] == [5, 10],
              str(ms_))
        check("term filter is case-insensitive",
              {m["ordinal"] for m in sums.mentions(db, manuscript, "choice")}
              == {4, 11})
        check("recurrence is a query: 'the Field' in 2 distinct paragraphs",
              len({m["para_hash"] for m in sums.mentions(db, manuscript, "the field")}) == 2)

        print("edits derive staleness (§4.4), nothing stored:")
        edited = ESSAY.replace("every Choice has been subtracted",
                               "every Choice has been removed")
        st = sums.level_status(db, manuscript, "doors.md", edited)
        check("one paragraph edited: one paragraph row needed, its section "
              "stale, essay stale, sections moved",
              len(st["paragraph"]["missing"]) == 7
              and st["section"]["state"] == "stale"
              and st["section"]["missing"] == [2]
              and st["essay"]["state"] == "stale"
              and st["essay"]["sections_moved"], str(st))
        moved = ESSAY.replace(
            f"{REFRAIN}\n\nWithin the Field", f"Within the Field").replace(
            "into a world.\n", f"into a world.\n\n{REFRAIN}\n")
        st = sums.level_status(db, manuscript, "doors.md", moved)
        check("paragraph moved within its section: no new paragraph row, "
              "section stale",
              len(st["paragraph"]["missing"]) == 6
              and st["section"]["missing"] == [2], str(st))
        check("nothing was written by reading",
              db.one("SELECT COUNT(*) AS n FROM paragraph_summaries "
                     "WHERE status = 'current'")["n"] == 4
              and db.one("SELECT COUNT(*) AS n FROM section_summaries")["n"] == 3)

        print("orphans:")
        found = sums.orphan_rows(db, manuscript, "doors.md", edited)
        check("the edited paragraph's old row and its section are orphans",
              [r["para_hash"] for r in found["paragraph"]]
              == [field_def["para_hash"]]
              and [r["section_ordinal"] for r in found["section"]] == [2],
              str(found))
        gone = ESSAY.replace(f"{REFRAIN}\n\n", "")
        found = sums.orphan_rows(db, manuscript, "doors.md", gone)
        check("deleting both refrains orphans its one row",
              [r["para_hash"] for r in found["paragraph"]]
              == [refrains[0]["para_hash"]], str(found))
        counts = sums.mark_orphans(db, manuscript, "doors.md", gone)
        check("mark_orphans flips them and counts",
              counts == {"paragraph": 1, "section": 2}
              and db.one("SELECT status FROM paragraph_summaries WHERE id = ?",
                         (rrow["id"],))["status"] == "orphaned")
        check("orphaned rows leave the read API",
              all(r["ordinal"] not in (5, 10) for r in
                  sums.paragraph_records(db, manuscript))
              and sums.mentions(db, manuscript, "key") == [])
        back, _ = sums.store_paragraph_record(
            db, manuscript, "doors.md", refrains[0],
            {"gist": "The refrain."}, "flash-model")
        check("storing a returned paragraph resurrects its row",
              back["id"] == rrow["id"] and back["status"] == "current")

        print("summarize_unit writes the new columns:")
        class _LLM:
            model = "luna"
            def complete(self, system, user):
                return "MOVES: [1]-[11] all of it."
        new = sums.summarize_unit(db, manuscript, "doors.md", ESSAY, [], _LLM())
        check("model and sections_hash stored on the essay row",
              new["model"] == "luna" and new["sections_hash"]
              == sums.sections_hash([s["section_hash"] for s in secs]))
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print(f"\nall checks passed ({PASSED})")


if __name__ == "__main__":
    main_test()
