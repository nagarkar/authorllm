"""`changes._scope_filter` turns a `--scope` label into the document ids
`list_changes` may show: None for the whole book, a part's documents, or
one chapter id.

Wrong allow list → the change list hides edits the author asked for, or
shows another chapter's. Hermetic — `_scope_key`, `layout`, and
`_scope_documents` stubbed; no workspace.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.book import BOOK, Layout, Scope  # noqa: E402
from authorlm.changes import _scope_filter  # noqa: E402


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


def test_none_scope_is_unrestricted() -> None:
    got = _scope_filter(None, {"name": "book"}, None)
    check("scope=None → None (whole book)", got is None, got)


def test_book_key_is_unrestricted() -> None:
    with mock.patch("authorlm.changes.book._scope_key",
                    return_value=BOOK) as key:
        got = _scope_filter("db", {"name": "book"}, "the book")
    check("_scope_key asked with the label",
          key.call_args.args[2] == "the book", key.call_args)
    check("book key → None", got is None, got)


def test_part_key_uses_scope_documents() -> None:
    layout = _layout()
    with mock.patch("authorlm.changes.book._scope_key",
                    return_value="dc-p1"), \
         mock.patch("authorlm.changes.book.layout",
                    return_value=layout) as layout_fn, \
         mock.patch("authorlm.changes._scope_documents",
                    return_value=["dc-p1", "dc-1", "dc-2"]) as docs:
        got = _scope_filter("db", {"name": "book", "id": 1}, "p1.md")
    check("layout loaded for a non-book key",
          layout_fn.call_args.args[0] == "db", layout_fn.call_args)
    check("_scope_documents asked for the part",
          docs.call_args.args == (layout, "part", "dc-p1"), docs.call_args)
    check("returns the part's document list",
          got == ["dc-p1", "dc-1", "dc-2"], got)


def test_chapter_key_is_a_singleton_list() -> None:
    layout = _layout()
    with mock.patch("authorlm.changes.book._scope_key",
                    return_value="dc-2"), \
         mock.patch("authorlm.changes.book.layout", return_value=layout), \
         mock.patch("authorlm.changes._scope_documents") as docs:
        got = _scope_filter("db", {"name": "book"}, "two.md")
    check("chapter key → [key] without calling _scope_documents",
          got == ["dc-2"] and not docs.called, (got, docs.called))


def main() -> None:
    for test in (
        test_none_scope_is_unrestricted,
        test_book_key_is_unrestricted,
        test_part_key_uses_scope_documents,
        test_chapter_key_is_a_singleton_list,
    ):
        print(f"{test.__name__}:")
        test()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
