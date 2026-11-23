"""`book._scope_key` and `book._diagram_scope` fix which grain's steps
carry a state mark when the diagram is drawn for one scope. Wrong key →
the wrong chapter/part cell is painted, or a filename never resolves.

`_resolve_scope` is the matching start/status gate: a book step refuses
`--scope`, a chapter step must name a real chapter. Pure over a
constructed Layout; filename → id stubs `docs.document_id`.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.book import (BOOK, Layout, Scope, Step,  # noqa: E402
                           _diagram_scope, _resolve_scope, _scope_key)


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


def _step(grain: str) -> Step:
    return Step(id="draft", role="drafter", actor="agent", grain=grain,
                after=(), gives=(), returns="x", artifacts=(),
                leaves_alone="y", independent=False)


def test_scope_key_passes_book_and_dc_ids() -> None:
    check("book stays book", _scope_key(None, {}, BOOK) == BOOK)
    check("dc- id stays itself",
          _scope_key(None, {}, "dc-1") == "dc-1")


def test_scope_key_resolves_filename_via_document_id() -> None:
    with mock.patch("authorlm.book.docs.document_id",
                    return_value="dc-resolved") as doc_id:
        got = _scope_key("db", {"name": "book"}, "chapters/one.md")
    check("filename → document_id result", got == "dc-resolved", got)
    check("document_id was asked with the filename",
          doc_id.call_args.args[2] == "chapters/one.md", doc_id.call_args)


def test_diagram_scope_none_leaves_every_grain_blank() -> None:
    keys = _diagram_scope(None, {}, _layout(), None)
    check("no scope → all grains None",
          keys == {"book": None, "part": None, "chapter": None}, keys)


def test_diagram_scope_chapter_fixes_part_and_book() -> None:
    keys = _diagram_scope(None, {}, _layout(), "dc-1")
    check("chapter sets itself, its part, and the book",
          keys == {"book": BOOK, "part": "dc-p1", "chapter": "dc-1"}, keys)


def test_diagram_scope_part_leaves_chapter_blank() -> None:
    keys = _diagram_scope(None, {}, _layout(), "dc-p2")
    check("part sets itself and the book; chapter stays None",
          keys == {"book": BOOK, "part": "dc-p2", "chapter": None}, keys)


def test_diagram_scope_book_only_fixes_book() -> None:
    keys = _diagram_scope(None, {}, _layout(), BOOK)
    check("book scope only marks the book grain",
          keys == {"book": BOOK, "part": None, "chapter": None}, keys)


def test_diagram_scope_unknown_raises() -> None:
    try:
        _diagram_scope(None, {"name": "book"}, _layout(), "dc-missing")
    except ValueError as err:
        check("names the unknown scope", "dc-missing" in str(err), err)
        check("points at book status", "book status" in str(err), err)
        return
    raise AssertionError("expected ValueError for unknown scope")


def test_resolve_scope_book_step() -> None:
    layout = _layout()
    book_step = _step("book")
    got = _resolve_scope(layout, book_step, None)
    check("book step with no scope → the book",
          got.key == BOOK and got.grain == "book", got)
    got2 = _resolve_scope(layout, book_step, BOOK)
    check("book step with book → the book", got2.key == BOOK, got2)
    try:
        _resolve_scope(layout, book_step, "dc-1")
    except ValueError as err:
        check("book step refuses a chapter scope",
              "whole book" in str(err) and "leave" in str(err).lower(), err)
    else:
        raise AssertionError("expected ValueError")


def test_resolve_scope_chapter_by_key_or_label() -> None:
    layout = _layout()
    chapter = _step("chapter")
    by_key = _resolve_scope(layout, chapter, "dc-2")
    by_label = _resolve_scope(layout, chapter, "two.md")
    check("chapter by dc- id", by_key.key == "dc-2", by_key)
    check("chapter by file label", by_label.key == "dc-2", by_label)
    try:
        _resolve_scope(layout, chapter, None)
    except ValueError as err:
        check("missing scope names the grain",
              "once per chapter" in str(err), err)
    else:
        raise AssertionError("expected ValueError")
    try:
        _resolve_scope(layout, chapter, "front.md")
    except ValueError as err:
        check("non-chapter is refused with the front-matter hint",
              "not a chapter" in str(err)
              and "Front matter" in str(err), err)
    else:
        raise AssertionError("expected ValueError")


def test_resolve_scope_lone_part_defaults() -> None:
    lone = Layout(
        chapters=(Scope("chapter", "dc-1", "one.md"),),
        parts=(Scope("part", "dc-p1", "p1.md"),),
        part_of={"dc-1": "dc-p1"},
        not_yet_documents=(),
    )
    got = _resolve_scope(lone, _step("part"), None)
    check("a single part needs no --scope", got.key == "dc-p1", got)


def main() -> None:
    for test in (
        test_scope_key_passes_book_and_dc_ids,
        test_scope_key_resolves_filename_via_document_id,
        test_diagram_scope_none_leaves_every_grain_blank,
        test_diagram_scope_chapter_fixes_part_and_book,
        test_diagram_scope_part_leaves_chapter_blank,
        test_diagram_scope_book_only_fixes_book,
        test_diagram_scope_unknown_raises,
        test_resolve_scope_book_step,
        test_resolve_scope_chapter_by_key_or_label,
        test_resolve_scope_lone_part_defaults,
    ):
        print(f"{test.__name__}:")
        test()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
