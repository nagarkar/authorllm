"""`book._register_section` is the pack block that lists every register
entry in the run's scope and says whether `register_uses` is required.

Wrong document-id set → a chapter pack carries another part's rulings, or
a book pack misses settled choices. Missed must-declare wording lets an
agent finish without saying what rested on the register. Hermetic —
`register.in_scope` and `_entry_lines` stubbed; no workspace.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.book import (Layout, Scope, USES_KEY,  # noqa: E402
                           _register_section)


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


def _entry(status: str, key: str = "main") -> dict:
    return {"kind": "thesis", "key": key, "id": f"re-{key}-{status}",
            "scope": "dc-1", "status": status,
            "author_words": f"words-{key}", "reading": f"read-{key}",
            "test": None}


def test_book_scope_asks_in_scope_with_none() -> None:
    with mock.patch("authorlm.register.in_scope",
                    return_value=[]) as in_scope:
        lines = _register_section(
            "db", {"name": "book"}, _layout(),
            Scope("book", "book", "the book"), "sr-1", False)
    check("book grain → document_ids is None",
          in_scope.call_args.args[2] is None, in_scope.call_args)
    text = "\n".join(lines)
    check("empty settled says nothing is settled",
          "Nothing is settled in this scope yet." in text, text)
    check("optional path names the CLI use command",
          "authorlm register use" in text and "sr-1" in text, text)
    check("optional path does not say Required",
          "**Required.**" not in text, text)


def test_chapter_scope_passes_only_that_chapter() -> None:
    with mock.patch("authorlm.register.in_scope",
                    return_value=[]) as in_scope:
        _register_section(
            "db", {"name": "book"}, _layout(),
            Scope("chapter", "dc-2", "two.md"), "sr-9", True)
    check("chapter grain → only that document id",
          in_scope.call_args.args[2] == ["dc-2"], in_scope.call_args)


def test_part_scope_includes_part_and_its_chapters() -> None:
    with mock.patch("authorlm.register.in_scope",
                    return_value=[]) as in_scope:
        _register_section(
            "db", {"name": "book"}, _layout(),
            Scope("part", "dc-p1", "p1.md"), "sr-2", True)
    check("part grain → part id plus chapter ids under it",
          in_scope.call_args.args[2] == ["dc-p1", "dc-1", "dc-2"],
          in_scope.call_args)


def test_must_declare_uses_required_wording() -> None:
    with mock.patch("authorlm.register.in_scope", return_value=[]):
        lines = _register_section(
            "db", {"name": "book"}, _layout(),
            Scope("chapter", "dc-1", "one.md"), "sr-7", True)
    text = "\n".join(lines)
    check("required banner is present", "**Required.**" in text, text)
    check("names the uses key the result must carry",
          USES_KEY in text, text)
    check("shows the none-with-reason escape",
          f'"{USES_KEY}": "none"' in text, text)


def test_groups_proposed_and_reopened_under_headings() -> None:
    entries = [_entry("settled", "a"), _entry("proposed", "b"),
               _entry("reopened", "c")]
    fake_lines = {
        "re-a-settled": ["### settled-a", ""],
        "re-b-proposed": ["### proposed-b", ""],
        "re-c-reopened": ["### reopened-c", ""],
    }

    def entry_lines(entry, db):
        return list(fake_lines[entry["id"]])

    with mock.patch("authorlm.register.in_scope", return_value=entries), \
            mock.patch("authorlm.book._entry_lines", side_effect=entry_lines):
        lines = _register_section(
            "db", {"name": "book"}, _layout(),
            Scope("chapter", "dc-1", "one.md"), "sr-3", False)
    text = "\n".join(lines)
    check("settled entry is embedded without a Settled heading",
          "### settled-a" in text
          and "**Settled**" not in text, text)
    check("proposed gets its not-settled heading",
          "Proposed, not settled" in text and "### proposed-b" in text, text)
    check("reopened gets its reconsidering heading",
          "Reopened: the author is reconsidering" in text
          and "### reopened-c" in text, text)
    check("no empty-settled note when settled entries exist",
          "Nothing is settled in this scope yet." not in text, text)


def main() -> None:
    for test in (
        test_book_scope_asks_in_scope_with_none,
        test_chapter_scope_passes_only_that_chapter,
        test_part_scope_includes_part_and_its_chapters,
        test_must_declare_uses_required_wording,
        test_groups_proposed_and_reopened_under_headings,
    ):
        print(f"{test.__name__}:")
        test()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
