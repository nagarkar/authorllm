"""`changes._stands_now` is the sentence a declined `book revert` shows
when the words a change put in are gone: the paragraph that shares most
with them, or a clear "nothing close" when the file has moved on. Wrong
match → the author is pointed at the wrong place; a false hit on short
noise would invent a place that is not there.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.changes import _stands_now


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_quotes_the_paragraph_that_shares_most() -> None:
    text = "Alpha alone.\n\nBeta gamma delta epsilon.\n\nZed far away."
    got = _stands_now(text, "Beta gamma")
    check("names the shared paragraph",
          got == "What stands there now: «Beta gamma delta epsilon.»", got)


def test_nothing_close_when_the_file_has_moved_on() -> None:
    got = _stands_now("Alpha.\n\nZed.",
                      "completely unrelated words that share almost nothing")
    check("says nothing close",
          got == "Nothing close to those words is in the file now.", got)


def test_exact_paragraph_still_counts() -> None:
    got = _stands_now("Short.\n\nOther.", "Short.")
    check("exact match is quoted",
          got == "What stands there now: «Short.»", got)


def test_picks_the_best_of_several_paragraphs() -> None:
    text = ("One two.\n\n"
            "One two three four five six seven.\n\n"
            "One two three.")
    got = _stands_now(text, "One two three four five six seven eight")
    check("longest shared paragraph wins",
          "One two three four five six seven" in got, got)


def main() -> None:
    for test in (
        test_quotes_the_paragraph_that_shares_most,
        test_nothing_close_when_the_file_has_moved_on,
        test_exact_paragraph_still_counts,
        test_picks_the_best_of_several_paragraphs,
    ):
        print(f"{test.__name__}:")
        test()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
