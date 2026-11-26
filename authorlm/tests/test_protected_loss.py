"""`filtering.protected_loss` is the one comparison for "a protected term
is gone" — used by the filter road's `filter record` and by `book land`
(#439). Matching is `terms.mention_pattern`, so a single-word capital
name is lost when only its lower-case form remains, and a blank entry
in the protected list is skipped. These checks pin that rule hermetically
outside the e2e and book-land integration paths.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.filtering import protected_loss


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_capital_gate_counts_as_loss() -> None:
    lost = protected_loss("The Field is where it happens.",
                          "the field is where it happens.", ["Field"])
    check("Field → field is a loss", lost == ["Field"], lost)


def test_kept_term_brings_no_loss() -> None:
    lost = protected_loss("The Field is where it happens.",
                          "The Field remains open.", ["Field"])
    check("same capital form is kept", lost == [], lost)


def test_only_the_dropped_names_are_returned() -> None:
    lost = protected_loss("Field and Space together.",
                          "Space alone.", ["Field", "Space"])
    check("only Field is lost", lost == ["Field"], lost)


def test_blank_protected_names_are_skipped() -> None:
    lost = protected_loss("nothing here", "still nothing",
                          ["", "  ", None])  # type: ignore[list-item]
    check("blanks never appear", lost == [], lost)


def test_borrowed_lowercase_term_is_lost_when_cut() -> None:
    lost = protected_loss("anattā here", "nothing here", ["anattā"])
    check("lowercase term of art is lost when cut", lost == ["anattā"], lost)


def test_none_old_or_new_is_safe() -> None:
    check("None old with a protected name yields no crash",
          protected_loss(None, "x", ["Field"]) == [])  # type: ignore[arg-type]
    check("None new loses a term that was in old",
          protected_loss("a Field", None, ["Field"]) == ["Field"])  # type: ignore[arg-type]


def main() -> None:
    for test in (
        test_capital_gate_counts_as_loss,
        test_kept_term_brings_no_loss,
        test_only_the_dropped_names_are_returned,
        test_blank_protected_names_are_skipped,
        test_borrowed_lowercase_term_is_lost_when_cut,
        test_none_old_or_new_is_safe,
    ):
        print(f"{test.__name__}:")
        test()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
