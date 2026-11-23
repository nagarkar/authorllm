"""`book._still_landed` warns a restarted pack that earlier applied
edits are still in the chapter files; `_entry_lines` is the register
block every pack embeds for one settled choice.

A missed landed warning lets a role treat a restarted chapter as clean;
wrong entry lines mis-state the author's words or scope. Hermetic —
`applied_by_run` and `_label` stubbed; no workspace.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.book import _entry_lines, _still_landed  # noqa: E402


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_still_landed_empty_when_nothing_applied() -> None:
    with mock.patch("authorlm.changes.applied_by_run", return_value=[]):
        got = _still_landed(None, {"name": "book"},
                            [{"id": "sr-1"}, {"id": "sr-2"}])
    check("no applied changes → no section", got == [], got)


def test_still_landed_names_counts_and_revert() -> None:
    found = [
        {"run": "sr-1", "applied": 2,
         "revert": "authorlm book revert --run sr-1 --reason '<why>' "
                   "-m book"},
        {"run": "sr-3", "applied": 1,
         "revert": "authorlm book revert --run sr-3 --reason '<why>' "
                   "-m book"},
    ]
    with mock.patch("authorlm.changes.applied_by_run",
                    return_value=found) as applied:
        earlier = [{"id": "sr-1"}, {"id": "sr-2"}, {"id": "sr-3"}]
        got = _still_landed("db", {"name": "book", "id": 7}, earlier)
    check("asks applied_by_run with every earlier run id",
          list(applied.call_args.args[2]) == ["sr-1", "sr-2", "sr-3"],
          applied.call_args)
    text = "\n".join(got)
    check("totals applied across runs", "3 applied change(s)" in text, text)
    check("embeds each revert command",
          found[0]["revert"] in text and found[1]["revert"] in text, text)
    check("names the per-run applied counts",
          "(2 applied)" in text and "(1 applied)" in text, text)
    check("says a restart puts nothing back",
          "A restart puts nothing back" in text, text)
    check("section ends with a blank line", got[-1] == "", got)


def test_entry_lines_book_scope_without_test() -> None:
    with mock.patch("authorlm.book._label", return_value="the book"):
        lines = _entry_lines(
            {"kind": "purpose", "key": "main", "id": "re-1", "scope": None,
             "author_words": "to argue X", "reading": "the book argues X",
             "test": None},
            None)
    check("heading names kind/key, id, and whole-book where",
          lines[0] == "### purpose/main (re-1, the whole book)", lines[0])
    check("author's words and reading are listed",
          "- Author's words: to argue X" in lines
          and "- Reading: the book argues X" in lines, lines)
    check("no test line when test is empty",
          not any(line.startswith("- Test:") for line in lines), lines)
    check("ends with a blank line", lines[-1] == "", lines)


def test_entry_lines_chapter_scope_with_test() -> None:
    with mock.patch("authorlm.book._label", return_value="one.md") as label:
        lines = _entry_lines(
            {"kind": "thesis", "key": "ch1", "id": "re-9", "scope": "dc-1",
             "author_words": None, "reading": None,
             "test": "a reader can restate the claim"},
            "db")
    check("_label was asked with the entry's scope",
          label.call_args.args == ("db", "dc-1"), label.call_args)
    check("where uses the label, not the raw id",
          lines[0] == "### thesis/ch1 (re-9, one.md)", lines[0])
    check("missing author words / reading say none yet",
          "- Author's words: (none yet)" in lines
          and "- Reading: (none yet)" in lines, lines)
    check("test line is included when set",
          "- Test: a reader can restate the claim" in lines, lines)


def main() -> None:
    for test in (
        test_still_landed_empty_when_nothing_applied,
        test_still_landed_names_counts_and_revert,
        test_entry_lines_book_scope_without_test,
        test_entry_lines_chapter_scope_with_test,
    ):
        print(f"{test.__name__}:")
        test()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
