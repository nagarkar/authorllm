"""`filters.summary_of` and `lenses.summary_of` are what `filter list`
and `lens list` print for each artifact.

Both take the first prose line (front matter never appears; a leading
markdown heading is unwrapped). Lenses also strip surrounding `*` so an
italic thesis line does not show its markup; filters do not. Without a
direct pin, a regression either leaks YAML into the list or truncates
wrongly at the 100-character cap.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.filters import summary_of as filter_summary
from authorlm.lenses import summary_of as lens_summary


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_skips_blank_and_unwraps_heading() -> None:
    body = "\n\n# Catch duplicates\n\nMore prompt.\n"
    check("filter unwraps a leading heading",
          filter_summary(body) == "Catch duplicates")
    check("lens unwraps a leading heading",
          lens_summary(body) == "Catch duplicates")


def test_empty_body() -> None:
    check("filter empty body is ''", filter_summary("") == "")
    check("filter whitespace-only is ''", filter_summary("  \n\n  ") == "")
    check("lens empty body is ''", lens_summary("") == "")


def test_asterisk_strip_differs() -> None:
    body = "*Find the soft spots in the argument.*\n"
    check("filter keeps surrounding asterisks",
          filter_summary(body) == "*Find the soft spots in the argument.*",
          filter_summary(body))
    check("lens strips surrounding asterisks",
          lens_summary(body) == "Find the soft spots in the argument.",
          lens_summary(body))


def test_truncate_at_100() -> None:
    long = "x" * 150
    check("filter truncates at 100",
          filter_summary(long) == "x" * 100)
    check("lens truncates at 100",
          lens_summary(long) == "x" * 100)


def test_first_prose_wins() -> None:
    body = "# Title line\n\nSecond prose should not win.\n"
    check("filter takes the heading unwrap, not later prose",
          filter_summary(body) == "Title line")
    check("lens takes the heading unwrap, not later prose",
          lens_summary(body) == "Title line")


def main() -> None:
    tests = [
        test_skips_blank_and_unwraps_heading,
        test_empty_body,
        test_asterisk_strip_differs,
        test_truncate_at_100,
        test_first_prose_wins,
    ]
    for t in tests:
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
