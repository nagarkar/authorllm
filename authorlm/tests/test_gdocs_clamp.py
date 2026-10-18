"""`gdocs.clamp` is the one truncation used in Doc fault lines, settle
receipts, and CLI proposal diffs.

Wrong length or a missing ellipsis either floods a message with the
whole paragraph or silently chops mid-token without the mark that the
quote was cut — both make a Doc divergence harder to spot.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.gdocs import clamp


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_short_and_exact() -> None:
    check("short text is unchanged",
          clamp("alpha beta") == "alpha beta")
    exact = "a" * 60
    check("exact default limit keeps every character",
          clamp(exact) == exact, len(clamp(exact)))


def test_truncates_with_ellipsis() -> None:
    text = "word " * 20  # longer than 60 once flattened
    got = clamp(text)
    check("over-limit result ends with an ellipsis",
          got.endswith("…"), got)
    check("over-limit result is exactly the limit",
          len(got) == 60, len(got))
    check("prefix is the first limit-1 characters of the flat form",
          got[:-1] == " ".join(text.split())[:59], got)


def test_flattens_whitespace() -> None:
    check("newlines and runs collapse to single spaces before the cut",
          clamp("alpha\n\n  beta\tgamma") == "alpha beta gamma")


def test_custom_limit() -> None:
    got = clamp("abcdefghij", limit=5)
    check("custom limit truncates to that length",
          got == "abcd…", got)


def main() -> None:
    tests = [
        test_short_and_exact,
        test_truncates_with_ellipsis,
        test_flattens_whitespace,
        test_custom_limit,
    ]
    for t in tests:
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
