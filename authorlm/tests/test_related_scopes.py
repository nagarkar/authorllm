"""`book._related_scopes` is the grain-crossing map every wait and
restart walks: which chapter/part/book scopes a step rests on. Wrong
mapping → a chapter step starts before its part is done, or a restart
leaves the wrong dependents flagged. Pure over a constructed Layout.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.book import BOOK, Layout, Scope, _related_scopes


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def _layout() -> Layout:
    return Layout(
        chapters=(
            Scope("chapter", "dc-1", "one.md"),
            Scope("chapter", "dc-2", "two.md"),
            Scope("chapter", "dc-3", "three.md"),
        ),
        parts=(
            Scope("part", "dc-p1", "p1.md"),
            Scope("part", "dc-p2", "p2.md"),
        ),
        part_of={"dc-1": "dc-p1", "dc-2": "dc-p1", "dc-3": "dc-p2"},
        not_yet_documents=(),
    )


def test_same_grain_is_itself() -> None:
    layout = _layout()
    check("chapter→chapter",
          _related_scopes(layout, "chapter", "dc-1", "chapter") == ["dc-1"])
    check("part→part",
          _related_scopes(layout, "part", "dc-p1", "part") == ["dc-p1"])


def test_any_scope_rests_on_the_book() -> None:
    layout = _layout()
    check("chapter→book",
          _related_scopes(layout, "chapter", "dc-1", "book") == [BOOK])
    check("part→book",
          _related_scopes(layout, "part", "dc-p1", "book") == [BOOK])


def test_book_expands_to_every_finer_scope() -> None:
    layout = _layout()
    check("book→chapter is every chapter in order",
          _related_scopes(layout, "book", BOOK, "chapter")
          == ["dc-1", "dc-2", "dc-3"])
    check("book→part is every part in order",
          _related_scopes(layout, "book", BOOK, "part")
          == ["dc-p1", "dc-p2"])


def test_part_and_chapter_cross() -> None:
    layout = _layout()
    check("chapter→part is its owning part",
          _related_scopes(layout, "chapter", "dc-1", "part") == ["dc-p1"])
    check("part→chapter is the chapters it holds",
          _related_scopes(layout, "part", "dc-p1", "chapter")
          == ["dc-1", "dc-2"])
    check("other part's chapters stay out",
          _related_scopes(layout, "part", "dc-p2", "chapter") == ["dc-3"])


def main() -> None:
    for test in (
        test_same_grain_is_itself,
        test_any_scope_rests_on_the_book,
        test_book_expands_to_every_finer_scope,
        test_part_and_chapter_cross,
    ):
        print(f"{test.__name__}:")
        test()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
