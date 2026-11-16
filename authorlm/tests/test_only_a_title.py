"""`changes._only_a_title` is the draft gate: a chapter that holds only
heading lines (or nothing) may receive a first draft; a file with any
prose line may not. Wrong true → `book draft` overwrites the author's
words; wrong false → a blank chapter cannot be filled under `direct`.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.changes import _only_a_title


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_empty_and_blank_are_titles_only() -> None:
    check("empty file", _only_a_title(""))
    check("whitespace-only lines", _only_a_title("  \n\t\n"))
    check("title plus blank lines", _only_a_title("# One\n\n  \n"))


def test_any_heading_level_alone_is_still_empty() -> None:
    check("H1 alone", _only_a_title("# One\n"))
    check("H2 alone", _only_a_title("## Sub\n"))
    check("stacked headings", _only_a_title("# One\n## Sub\n### Deep\n"))


def test_prose_anywhere_means_not_empty() -> None:
    check("prose under a title",
          not _only_a_title("# One\n\nA sentence.\n"))
    check("prose with no heading", not _only_a_title("Just words.\n"))
    check("hash mid-line is prose, not a heading",
          not _only_a_title("not # a heading\n"))


def main() -> None:
    for test in (
        test_empty_and_blank_are_titles_only,
        test_any_heading_level_alone_is_still_empty,
        test_prose_anywhere_means_not_empty,
    ):
        print(f"{test.__name__}:")
        test()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
