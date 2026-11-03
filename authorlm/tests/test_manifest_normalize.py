"""`manifest._normalize` is the term cleaner behind essay debt columns.

It strips markdown emphasis, leading articles, and trailing periods, then
resolves against the concept graph (or keeps the cleaned form). Terms
shorter than three characters are refused. A silent change either invents
debt for noise tokens or drops real unmet concepts from the on-ramp.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.manifest import _normalize


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


CANON = {"undertow": "Undertow", "free will": "Free Will"}


def test_markdown_and_article_stripped() -> None:
    got = _normalize("the *Undertow*.", CANON)
    check("markdown, article, and trailing period collapse to the canon",
          got == "Undertow", repr(got))


def test_unknown_keeps_cleaned_form() -> None:
    got = _normalize("`Habit`", CANON)
    check("unknown term keeps its cleaned casing",
          got == "Habit", repr(got))


def test_short_terms_refused() -> None:
    for term in ("a", "an", "be", "  ", "***", "the a"):
        got = _normalize(term, CANON)
        check(f"{term!r} is too short after cleaning",
              got is None, repr(got))


def test_multiword_canon() -> None:
    got = _normalize("A Free Will", CANON)
    check("multi-word term resolves via lowercased lookup",
          got == "Free Will", repr(got))


def test_underscore_emphasis() -> None:
    got = _normalize("_Undertow_", CANON)
    check("underscore emphasis is stripped like backticks/stars",
          got == "Undertow", repr(got))


def main() -> None:
    tests = [test_markdown_and_article_stripped,
             test_unknown_keeps_cleaned_form, test_short_terms_refused,
             test_multiword_canon, test_underscore_emphasis]
    for t in tests:
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
