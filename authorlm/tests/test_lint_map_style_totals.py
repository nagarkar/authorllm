"""Unit pins for lint_page's edit-offset map and style_page's live totals
and guide tree.

Lint Apply remaps later fix spans through earlier edits (`_map`); a wrong
delta writes into the wrong place once two mechanical fixes land in one
tab. Style laws page totals must count only live (active, not displaced)
laws, and the guide tree must walk parents-before-children — a dangling
parent becomes a root, a self-parent is omitted — otherwise the page
under-counts force or hangs.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ["AUTHORLM_CONFIG"] = "/nonexistent/authorlm-test/config.toml"
os.environ["AUTHORLM_ENV"] = "/nonexistent/authorlm-test/.env"
os.environ["AUTHORLM_CLIENT"] = "none"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm import api, lint_page, style_page, styles  # noqa: E402
from authorlm.db import Database  # noqa: E402

PASSED = 0


def check(label: str, cond: bool, detail: object = "") -> None:
    global PASSED
    print(f"  [{'ok' if cond else 'FAIL'}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)
    PASSED += 1


def test_map() -> None:
    check("no edits leaves the offset alone",
          lint_page._map([], 5) == 5)
    check("a same-length replace before the point does not shift",
          lint_page._map([(0, 2, "XX")], 5) == 5)
    check("a longer replace before the point shifts right",
          lint_page._map([(0, 2, "XXXX")], 5) == 7)
    check("a deletion before the point shifts left",
          lint_page._map([(0, 4, "")], 5) == 1)
    check("an edit ending exactly at the point still counts",
          lint_page._map([(3, 5, "YY")], 5) == 5)
    check("an edit that overlaps or starts at the point is ignored",
          lint_page._map([(3, 6, "YY")], 5) == 5
          and lint_page._map([(5, 7, "ZZ")], 5) == 5)
    check("several earlier edits accumulate",
          lint_page._map([(0, 1, "aa"), (2, 4, "")], 10) == 9)


def test_totals() -> None:
    laws = [
        {"status": "active", "displaced_by": [], "level": "all",
         "aspect": "voice", "chars": 3, "notes": "ab"},
        {"status": "active", "displaced_by": ["x"], "level": "all",
         "aspect": "voice", "chars": 9, "notes": None},
        {"status": "retired", "displaced_by": [], "level": "guide:H",
         "aspect": "formatting", "chars": 4, "notes": "n"},
        {"status": "active", "displaced_by": [], "level": "file:a.md",
         "aspect": "formatting", "chars": 2, "notes": ""},
    ]
    tot = style_page._totals(laws)
    check("retired and displaced laws are out of the live count",
          tot["laws"] == 2 and tot["chars"] == 5 and tot["notes"] == 2, tot)
    check("by_level keeps only live rows",
          tot["by_level"] == {
              "all": {"laws": 1, "chars": 3, "notes": 2},
              "file:a.md": {"laws": 1, "chars": 2, "notes": 0},
          }, tot["by_level"])
    check("by_aspect keeps only live rows",
          tot["by_aspect"] == {
              "voice": {"laws": 1, "chars": 3, "notes": 2},
              "formatting": {"laws": 1, "chars": 2, "notes": 0},
          }, tot["by_aspect"])
    check("an empty list is zero across the board",
          style_page._totals([]) == {
              "laws": 0, "chars": 0, "notes": 0,
              "by_level": {}, "by_aspect": {},
          })


def test_guides_tree() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        ws = root / "ws"
        (ws / ".authorlm").mkdir(parents=True)
        os.environ[styles.DEFAULTS_ENV] = str(ws / "missing-defaults.toml")
        ms = ws / "book"
        ms.mkdir()
        (ms / "a.md").write_text("# A\n\nText.\n", encoding="utf-8")
        db = Database(ws / ".authorlm" / "authorlm.db")
        manuscript = dict(api.register_manuscript(db, "book", str(ms)))
        mid = manuscript["id"]
        # House ← Part ← Recaps; Papers under House; Zebra as a sibling
        # root; Orphan via a dangling parent id (treated as a root). A
        # self-parent guide is unreachable from the roots and is omitted
        # rather than looping forever.
        house = api.define_style_guide(db, manuscript, "House", parent=None)
        part = api.define_style_guide(db, manuscript, "Part", parent="House")
        api.define_style_guide(db, manuscript, "Recaps", parent="Part")
        api.define_style_guide(db, manuscript, "Papers", parent="House")
        api.define_style_guide(db, manuscript, "Zebra", parent=None)
        orphan = api.define_style_guide(db, manuscript, "Orphan", parent=None)
        looped = api.define_style_guide(db, manuscript, "Looped", parent=None)
        db.update("style_guides", orphan["id"], {"parent": "no-such-guide"})
        db.update("style_guides", looped["id"], {"parent": looped["id"]})

        tree = style_page._guides(db, mid)
        names = [(g["name"], g["depth"]) for g in tree]
        check("roots sort by name; dangling parent becomes a root",
              names[0][0] == "House" and ("Orphan", 0) in names
              and ("Zebra", 0) in names, names)
        check("a self-parent guide is omitted, not walked forever",
              "Looped" not in {n for n, _d in names}, names)
        check("every reachable guide appears once",
              len({g["id"] for g in tree}) == len(tree)
              and {g["name"] for g in tree}
              == {"House", "Part", "Recaps", "Papers", "Zebra", "Orphan"},
              names)
        papers = next(g for g in tree if g["name"] == "Papers")
        part_row = next(g for g in tree if g["name"] == "Part")
        recaps_row = next(g for g in tree if g["name"] == "Recaps")
        check("House children keep depth 1; Recaps sits under Part",
              papers["depth"] == 1 and papers["parent"] == house["id"]
              and part_row["depth"] == 1 and part_row["parent"] == house["id"]
              and recaps_row["depth"] == 2
              and recaps_row["parent"] == part["id"], names)


def main() -> None:
    for name, fn in sorted((k, v) for k, v in globals().items()
                           if k.startswith("test_")):
        print(name)
        fn()
    print(f"{PASSED} checks passed")


if __name__ == "__main__":
    main()
