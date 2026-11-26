"""`forms_page._overlapping_form`: a widened Merge-forms edit must not
rewrite words another open form still owns.

The page's decide path turns such an edit down; a silent miss lets one
ruling overwrite a neighbour's span, and a false hit blocks a harmless
edit. Integration covers the happy path; these pins are the helper alone.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.forms_page import _overlapping_form


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


class FakeDB:
    def __init__(self, rows: list[dict]):
        self.rows = rows
        self.last_args = None

    def all(self, sql: str, args=()):
        self.last_args = (sql, args)
        return list(self.rows)


THREAD = {"id": "t-self", "manuscript_id": "ms-1", "file": "a.md"}


def test_no_neighbour() -> None:
    db = FakeDB([])
    got = _overlapping_form(db, THREAD, {"head": "before ", "tail": " after"})
    check("no other open form → None", got is None)
    check("the query scopes to this manuscript, file, and excludes self",
          db.last_args[1] == ("ms-1", "a.md", "t-self"), db.last_args)


def test_neighbour_in_head_or_tail() -> None:
    db = FakeDB([
        {"id": "t-other", "proposed_old": "owned words"},
        {"id": "t-insert", "proposed_old": ""},
    ])
    check("old words that sit in the widened head block the edit",
          _overlapping_form(db, THREAD,
                            {"head": "prefix owned words more", "tail": ""})
          == "owned words")
    check("old words that sit in the widened tail block the edit",
          _overlapping_form(db, THREAD,
                            {"head": "", "tail": " and owned words end"})
          == "owned words")
    check("an insert neighbour (empty old) never overlaps",
          _overlapping_form(
              FakeDB([{"id": "t-ins", "proposed_old": ""}]),
              THREAD, {"head": "anything", "tail": "else"}) is None)
    check("a neighbour whose old half is outside the widened context is fine",
          _overlapping_form(
              FakeDB([{"id": "t-far", "proposed_old": "elsewhere"}]),
              THREAD, {"head": "prefix ", "tail": " suffix"}) is None)


def main() -> None:
    for t in (test_no_neighbour, test_neighbour_in_head_or_tail):
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
