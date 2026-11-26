"""`book.edits_line` is the one sentence every agent pack carries on how
edits land in this book (#438). Wrong branch → an agent lands under forms,
or reports when it should land. Pack CUJs assert substrings; these checks
pin all four (mode × changes_text) branches on the helper itself.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.book import Step, edits_line


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def _step(changes_text: bool) -> Step:
    return Step(id="draft", role="drafter", actor="agent", grain="chapter",
                after=(), gives=(), returns="the chapter", artifacts=(),
                leaves_alone="every other chapter", independent=False,
                changes_text=changes_text)


def test_direct_and_changes_text_names_book_land() -> None:
    line = edits_line("direct", _step(True), "sr-1", "book")
    check("says directly and that this step changes text",
          line.startswith("How edits land in this book: directly. Your "
                          "step changes text"), line)
    check("names book land with the run id",
          "authorlm book land sr-1 <file>" in line, line)


def test_direct_without_changes_text_forbids_land() -> None:
    line = edits_line("direct", _step(False), "sr-1", "book")
    check("says directly for other steps",
          "directly, for the steps that change text" in line, line)
    check("tells this role to land nothing", "land nothing" in line, line)
    check("does not name book land", "book land" not in line, line)


def test_forms_and_changes_text_keeps_land_closed() -> None:
    line = edits_line("forms", _step(True), "sr-1", "book")
    check("says forms for the author",
          "as forms for the author" in line, line)
    check("book land is not open", "`book land` is not open" in line, line)


def test_forms_without_changes_text_is_report_only() -> None:
    line = edits_line("forms", _step(False), "sr-1", "book")
    check("says forms for the author",
          "as forms for the author" in line, line)
    check("says this step changes no text",
          "Your step changes no text: you report." in line, line)
    check("does not name book land", "book land" not in line, line)


def main() -> None:
    for test in (
        test_direct_and_changes_text_names_book_land,
        test_direct_without_changes_text_forbids_land,
        test_forms_and_changes_text_keeps_land_closed,
        test_forms_without_changes_text_is_report_only,
    ):
        print(f"{test.__name__}:")
        test()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
