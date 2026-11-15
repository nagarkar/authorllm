"""`changes._short` truncates the words quoted in land/revert errors;
`changes._warning` is the payload `book land` returns when a landed
change drops a protected term. Wrong truncation → unreadable errors;
wrong warning text → the author is not told which term fell, or that
the change still landed and how to put it back.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.changes import LOST_KEY, _short, _warning


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_short_flattens_and_ellipsis_at_the_limit() -> None:
    check("whitespace collapses", _short("a  \n  b") == "a b")
    exact = "x" * 60
    check("exact limit is unchanged", _short(exact) == exact)
    long = "x" * 61
    got = _short(long)
    check("one over the limit ends in an ellipsis",
          got == ("x" * 59) + "…" and len(got) == 60, got)
    check("custom limit", _short("abcdef", limit=4) == "abc…")


def test_warning_names_one_term_and_how_to_revert() -> None:
    change = {"id": "sc-1", LOST_KEY: ["Field"], "old": "The Field",
              "new": "the field", "note": "tone"}
    got = _warning({"name": "book"}, change)
    check("carries the change id and terms",
          got["change"] == "sc-1" and got["terms"] == ["Field"], got)
    check("singular wording", "a term of the book's" in got["message"],
          got["message"])
    check("quotes old and new",
          "«The Field» became «the field»" in got["message"], got["message"])
    check("names the revert command for this manuscript",
          "authorlm book revert sc-1 --reason '<why>' -m book"
          in got["message"], got["message"])


def test_warning_uses_plural_for_several_terms() -> None:
    change = {"id": "sc-2", LOST_KEY: ["Field", "Space"], "old": "a",
              "new": "b", "note": "cut"}
    got = _warning({"name": "book"}, change)
    check("lists both terms",
          "'Field', 'Space'" in got["message"], got["message"])
    check("plural wording", "terms of the book's" in got["message"],
          got["message"])


def main() -> None:
    for test in (
        test_short_flattens_and_ellipsis_at_the_limit,
        test_warning_names_one_term_and_how_to_revert,
        test_warning_uses_plural_for_several_terms,
    ):
        print(f"{test.__name__}:")
        test()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
