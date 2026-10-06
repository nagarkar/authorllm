"""`morph.targets` decides which paragraphs `morph run` sends to the
model, and so which paragraphs it may stage edits against.

It has five behaviors: `full=True` returns every prose paragraph; fewer
than two collected versions returns None; a newest version that does
not carry the file returns None; otherwise only prose paragraphs whose
text differs from the same position in the previous version (a
paragraph past the old file's end counts as changed); and None, never
an empty list, when nothing changed — `run` reads a falsy result as
"nothing changed" and makes no model call. This file calls `targets`
directly against a tiny fake database, and pins `morph._window` at both
ends.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm import morph


REL = "essay.md"
CURRENT = "\n\n".join(["# Heading", "Alpha prose.", "Beta prose.",
                       "Gamma prose.", "Delta prose."]) + "\n"
MANUSCRIPT = {"id": "m-1"}


class FakeDB:
    """Stands in for `Database`: `targets` only calls `all(sql, params)`
    and reads `row["files"]`, newest version first."""

    def __init__(self, versions: list[dict[str, str]]) -> None:
        self.rows = [{"files": json.dumps(v)} for v in versions]
        self.calls: list[tuple] = []

    def all(self, sql: str, params: tuple = ()) -> list[dict]:
        self.calls.append((sql, params))
        return list(self.rows)


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def _targets(versions: list[dict[str, str]], text: str = CURRENT, *,
             full: bool = False):
    return morph.targets(FakeDB(versions), MANUSCRIPT, REL, {REL: text},
                         full=full)


def _units(*parts: str) -> str:
    return "\n\n".join(parts) + "\n"


def test_full() -> None:
    got = _targets([], full=True)
    check("full=True gives every prose paragraph, heading left out",
          got == [2, 3, 4, 5], repr(got))
    got = _targets([], "# Only a heading\n", full=True)
    check("full=True on a heading-only file gives None, not []",
          got is None, repr(got))


def test_too_few_versions() -> None:
    got = _targets([])
    check("no collected versions gives None", got is None, repr(got))
    got = _targets([{REL: CURRENT}])
    check("one collected version gives None", got is None, repr(got))


def test_newest_lacks_file() -> None:
    got = _targets([{"other.md": "x\n"}, {REL: CURRENT}])
    check("newest version without the file gives None", got is None,
          repr(got))


def test_changed_and_appended() -> None:
    old = _units("# Old Heading", "Alpha prose.", "Beta CHANGED.",
                 "Gamma prose.")
    got = _targets([{REL: CURRENT}, {REL: old}])
    check("a changed paragraph and one past the old end are targeted; "
          "a changed heading is not", got == [3, 5], repr(got))


def test_unchanged_is_none() -> None:
    got = _targets([{REL: CURRENT}, {REL: CURRENT}])
    check("an unchanged file gives None, not []", got is None, repr(got))


def test_old_version_lacks_file() -> None:
    got = _targets([{REL: CURRENT}, {"other.md": "x\n"}])
    check("a file new since the previous version targets every prose "
          "paragraph", got == [2, 3, 4, 5], repr(got))


def test_queries_this_manuscript() -> None:
    db = FakeDB([{REL: CURRENT}, {REL: CURRENT}])
    morph.targets(db, MANUSCRIPT, REL, {REL: CURRENT})
    check("history is read for this manuscript's id",
          len(db.calls) == 1 and db.calls[0][1] == ("m-1",),
          repr(db.calls))
    db = FakeDB([])
    morph.targets(db, MANUSCRIPT, REL, {REL: CURRENT}, full=True)
    check("full=True reads no history", db.calls == [], repr(db.calls))


def test_window() -> None:
    cases = [((5, 1), [1, 2, 3]), ((5, 5), [3, 4, 5]),
             ((5, 3), [1, 2, 3, 4, 5]), ((1, 1), [1])]
    for args, want in cases:
        got = morph._window(*args)
        check(f"_window{args} == {want}", got == want, repr(got))


def main() -> None:
    tests = [test_full, test_too_few_versions, test_newest_lacks_file,
             test_changed_and_appended, test_unchanged_is_none,
             test_old_version_lacks_file, test_queries_this_manuscript,
             test_window]
    for t in tests:
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
