"""`changes._scope_documents` and `changes._file_in_scope` are the land
gate: an agent edit must name a file inside the run's scope. Wrong allow
list → a chapter run rewrites another chapter; a missed refusal lets
cross-scope land through. Hermetic — Layout in memory, docs helpers
stubbed; no workspace, no Google, no model.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.book import BOOK, Layout, Scope  # noqa: E402
from authorlm.changes import _file_in_scope, _scope_documents  # noqa: E402


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


def test_scope_documents_book_is_unrestricted() -> None:
    layout = _layout()
    check("book grain → None (whole manuscript)",
          _scope_documents(layout, "book", BOOK) is None)
    check("book key alone → None even on a part grain label",
          _scope_documents(layout, "part", BOOK) is None)


def test_scope_documents_chapter_and_part() -> None:
    layout = _layout()
    check("chapter → only itself",
          _scope_documents(layout, "chapter", "dc-1") == ["dc-1"])
    check("part → the part page plus its chapters",
          _scope_documents(layout, "part", "dc-p1")
          == ["dc-p1", "dc-1", "dc-2"])
    check("other part stays separate",
          _scope_documents(layout, "part", "dc-p2")
          == ["dc-p2", "dc-3"])


def test_file_in_scope_allows_inside_chapter() -> None:
    manuscript = {"name": "book", "path": "/tmp/book"}
    run = {"id": "sr-1", "step": "draft", "grain": "chapter",
           "scope": "dc-1", "scope_label": "one.md"}
    with mock.patch("authorlm.changes.book.layout", return_value=_layout()), \
         mock.patch("authorlm.changes.docs._resolve_relpath",
                    return_value="one.md"), \
         mock.patch("authorlm.changes.docs.document_id",
                    return_value="dc-1"):
        relpath, document_id, path = _file_in_scope(
            None, manuscript, run, "one.md")
    check("relpath returned", relpath == "one.md", relpath)
    check("document id returned", document_id == "dc-1", document_id)
    check("path is under the manuscript",
          path == Path("/tmp/book/one.md"), path)


def test_file_in_scope_refuses_outside_chapter() -> None:
    manuscript = {"name": "book", "path": "/tmp/book"}
    run = {"id": "sr-1", "step": "draft", "grain": "chapter",
           "scope": "dc-1", "scope_label": "one.md"}
    with mock.patch("authorlm.changes.book.layout", return_value=_layout()), \
         mock.patch("authorlm.changes.docs._resolve_relpath",
                    return_value="two.md"), \
         mock.patch("authorlm.changes.docs.document_id",
                    return_value="dc-2"):
        try:
            _file_in_scope(None, manuscript, run, "two.md")
        except ValueError as err:
            msg = str(err)
            check("names the file", "two.md" in msg, msg)
            check("names the run", "sr-1" in msg and "draft" in msg, msg)
            check("says outside this run's scope",
                  "outside this run's scope" in msg, msg)
            check("nothing was changed",
                  "Nothing was changed" in msg, msg)
            return
    raise AssertionError("expected ValueError for out-of-scope file")


def test_file_in_scope_book_run_allows_any_file() -> None:
    manuscript = {"name": "book", "path": "/tmp/book"}
    run = {"id": "sr-9", "step": "line-edit", "grain": "book",
           "scope": BOOK, "scope_label": "the book"}
    with mock.patch("authorlm.changes.book.layout", return_value=_layout()), \
         mock.patch("authorlm.changes.docs._resolve_relpath",
                    return_value="three.md"), \
         mock.patch("authorlm.changes.docs.document_id",
                    return_value="dc-3"):
        relpath, document_id, _path = _file_in_scope(
            None, manuscript, run, "three.md")
    check("book run accepts any chapter file",
          relpath == "three.md" and document_id == "dc-3",
          (relpath, document_id))


def test_file_in_scope_part_run_allows_its_chapters() -> None:
    manuscript = {"name": "book", "path": "/tmp/book"}
    run = {"id": "sr-2", "step": "part-read", "grain": "part",
           "scope": "dc-p1", "scope_label": "p1.md"}
    with mock.patch("authorlm.changes.book.layout", return_value=_layout()), \
         mock.patch("authorlm.changes.docs._resolve_relpath",
                    return_value="two.md"), \
         mock.patch("authorlm.changes.docs.document_id",
                    return_value="dc-2"):
        relpath, document_id, _path = _file_in_scope(
            None, manuscript, run, "two.md")
    check("part run allows a chapter it holds",
          relpath == "two.md" and document_id == "dc-2",
          (relpath, document_id))

    with mock.patch("authorlm.changes.book.layout", return_value=_layout()), \
         mock.patch("authorlm.changes.docs._resolve_relpath",
                    return_value="three.md"), \
         mock.patch("authorlm.changes.docs.document_id",
                    return_value="dc-3"):
        try:
            _file_in_scope(None, manuscript, run, "three.md")
        except ValueError as err:
            check("part run refuses another part's chapter",
                  "outside this run's scope" in str(err), err)
            return
    raise AssertionError("expected ValueError")


def main() -> None:
    for test in (
        test_scope_documents_book_is_unrestricted,
        test_scope_documents_chapter_and_part,
        test_file_in_scope_allows_inside_chapter,
        test_file_in_scope_refuses_outside_chapter,
        test_file_in_scope_book_run_allows_any_file,
        test_file_in_scope_part_run_allows_its_chapters,
    ):
        print(f"{test.__name__}:")
        test()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
