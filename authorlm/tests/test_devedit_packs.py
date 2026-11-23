"""`devedit.essay_pack` and `devedit.book_pack` — agent context assembly.

The developmental edit's essay and cross-book agents read only these
packs. A silent drift that drops another essay's summary, omits a
paragraph gist, or lists the wrong "where else" files for a term
misfeeds every finding in the run. No LLM: seeded summary rows and a
`terms.json` snapshot.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm import summaries as sums  # noqa: E402
from authorlm import devedit  # noqa: E402
from authorlm.db import Database, ko_fields  # noqa: E402

TOC = '''[[chapter]]
file = "one.md"

[[chapter]]
file = "two.md"
'''

ONE = "# One\n\n## Ground\n\nThe Dharma is the ground.\n\nIt moves all.\n"
TWO = "# Two\n\n## Return\n\nThe Dharma returns here.\n"


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def _setup(tmp: Path):
    root = tmp / "MS"
    root.mkdir()
    (root / "toc.toml").write_text(TOC)
    (root / "one.md").write_text(ONE)
    (root / "two.md").write_text(TWO)
    db = Database(tmp / "w.db")
    mrow = ko_fields("ms")
    mrow.update(name="MS", path=str(root))
    mid = db.insert("manuscripts", mrow)
    ms = {"name": "MS", "id": mid, "path": str(root)}
    folder = devedit.new_run(ms)
    devedit.confirm_coverage(folder)
    for name, summary in (("one.md", "Essay one summary."),
                          ("two.md", "Essay two summary.")):
        row = ko_fields("es")
        row.update(manuscript_id=mid, file=name, summary=summary,
                   source_hash="x", upstream_hash="", upstream_stale=0,
                   status="current", sections_hash="", model="test")
        db.insert("essay_summaries", row)
    for sec in sums.sections_of(ONE):
        row = ko_fields("ss")
        row.update(manuscript_id=mid, file="one.md",
                   section_hash=sec["section_hash"],
                   section_ordinal=sec["section_ordinal"],
                   heading=sec["heading"],
                   para_hashes=json.dumps(sec["para_hashes"]),
                   summary=f"POINT: section {sec['section_ordinal']}.",
                   model="test", status="current")
        db.insert("section_summaries", row)
        for p in sec["paragraphs"]:
            if p["kind"] == "heading":
                continue
            prow = ko_fields("ps")
            prow.update(manuscript_id=mid, file="one.md",
                        para_hash=p["para_hash"], ordinal=p["ordinal"],
                        section_ordinal=sec["section_ordinal"],
                        kind=p["kind"],
                        record=json.dumps({"gist": f"gist of ¶{p['ordinal']}"}),
                        model="test", status="current")
            db.insert("paragraph_summaries", prow)
    (folder / "terms.json").write_text(json.dumps([
        {"term": "Dharma", "count": 3, "files": ["one.md", "two.md"]},
        {"term": "OnlyHere", "count": 2, "files": ["one.md"]},
    ]))
    return db, ms, folder


def test_essay_pack_assembles_this_essay_others_and_terms() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db, ms, folder = _setup(Path(tmp))
        pack = devedit.essay_pack(db, ms, folder, "one.md")
        check("opens with this essay's context heading",
              pack.startswith("# Context for one.md\n"), pack[:80])
        check("this essay's summary and section POINT rows are present",
              "Essay one summary." in pack
              and "POINT: section 2." in pack, pack)
        check("paragraph gists are listed under their section",
              "- ¶3: gist of ¶3" in pack and "- ¶4: gist of ¶4" in pack, pack)
        check("other covered essays appear by name with their summary",
              "### two.md" in pack and "Essay two summary." in pack, pack)
        check("terms list elsewhere files and 'only here'",
              "- **Dharma** (3×): two.md" in pack
              and "- **OnlyHere** (2×): only here" in pack, pack)
        try:
            devedit.essay_pack(db, ms, folder, "stray.md")
            check("an uncovered file is refused", False)
        except LookupError as err:
            check("an uncovered file is refused by name",
                  "stray.md" in str(err), err)


def test_book_pack_follows_reading_order_and_skips_missing_sections() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db, ms, folder = _setup(Path(tmp))
        pack = devedit.book_pack(db, ms, folder)
        check("opens as the book in summary",
              pack.startswith("# The book in summary\n"), pack[:80])
        one_at = pack.index("## one.md")
        two_at = pack.index("## two.md")
        check("covered files appear in reading order",
              one_at < two_at, (one_at, two_at))
        check("essay summaries are present for both files",
              "Essay one summary." in pack
              and "Essay two summary." in pack, pack)
        check("section rows appear only where seeded (one.md)",
              "### §2 ## Ground" in pack
              and "POINT: section 2." in pack
              and "### §1 ## Return" not in pack, pack)
        check("a file with no section rows still keeps its essay summary",
              pack.index("## two.md") < pack.index("Essay two summary."),
              pack)


def main() -> None:
    tests = [test_essay_pack_assembles_this_essay_others_and_terms,
             test_book_pack_follows_reading_order_and_skips_missing_sections]
    for fn in tests:
        print(fn.__name__)
        fn()
    print(f"OK — {len(tests)} developmental-edit pack cases")


if __name__ == "__main__":
    main()
