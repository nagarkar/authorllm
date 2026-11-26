"""`forms_page._span_of_passage` turns a whole-passage edit (prefix +
span + suffix, as the Merge forms card shows) into the form's own words.

A wrong cut accepts words outside the form's span, or rejects a valid
edit. The page's decide road covers the happy path; these pins are the
edges: empty affixes, a before-side change, an after-side change.
Hermetic — no DB, no Google, no model.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.forms_page import _span_of_passage  # noqa: E402


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_span_of_passage() -> None:
    check("prefix and suffix peel off the form's words",
          _span_of_passage("Before the claim stands after.",
                           "Before ", " after.")
          == "the claim stands")

    check("empty affixes leave the whole edit as the span",
          _span_of_passage("just the span", "", "") == "just the span")

    check("an empty suffix peels only the prefix",
          _span_of_passage("Lead mid", "Lead ", "") == "mid")

    check("an empty prefix peels only the suffix",
          _span_of_passage("mid Tail", "", " Tail") == "mid")

    # lstrip/rstrip on the affixes: leading space on the prefix and
    # trailing space on the suffix are ignored for the match, so a card
    # that trims display whitespace still peels correctly.
    check("leading space on the prefix is ignored for the match",
          _span_of_passage("PRE mid SUF", "  PRE ", " SUF") == "mid")

    try:
        _span_of_passage("X mid after.", "Before ", " after.")
    except ValueError as exc:
        check("changing words before the span fails naming that side",
              "before this form's span" in str(exc)
              and "Before" in str(exc), exc)
    else:
        check("changing words before the span fails naming that side", False)

    try:
        _span_of_passage("Before mid Y", "Before ", " after.")
    except ValueError as exc:
        check("changing words after the span fails naming that side",
              "after this form's span" in str(exc)
              and "after" in str(exc), exc)
    else:
        check("changing words after the span fails naming that side", False)


def main() -> None:
    print("test_span_of_passage:")
    test_span_of_passage()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
