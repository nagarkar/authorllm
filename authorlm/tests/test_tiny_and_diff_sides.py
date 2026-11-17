"""`forms_page._tiny` decides when a short equal run between two word-diff
changes is folded into them; `_diff_sides` builds the Conflict card's
doc/local diffs from a three-way view.

A wrong `_tiny` leaves a noisy equal crumb between deletes, or swallows a
real word. A wrong `_diff_sides` either invents sides when there is no
pushed base, or omits the unified diffs the page shows. Hermetic — no
DB, no Google, no model.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.forms_page import _diff_sides, _tiny, word_diff  # noqa: E402


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_tiny() -> None:
    check("empty / whitespace-only is tiny",
          _tiny("") and _tiny("   ") and _tiny("\n\t"))
    check("one short word (≤3) in a short run is tiny",
          _tiny("and") and _tiny(" a ") and _tiny("or"))
    check("a longer single word is not tiny",
          not _tiny("hello") and not _tiny(" kitten "))
    check("two words are never tiny",
          not _tiny("a b") and not _tiny("red cat"))
    check("a short word in a long-enough run is not tiny",
          not _tiny("  and   "))  # len 8 > 6


def test_tiny_folds_in_word_diff() -> None:
    # "cat" sits between two changes and is short enough to fold, so the
    # middle equal does not stand as its own segment.
    got = word_diff("red cat and dog", "blue cat or dog")
    equal_texts = [s["text"] for s in got if s["op"] == "equal"]
    check("a tiny equal between changes is folded away",
          not any("cat" in t for t in equal_texts), got)
    # "kitten" is long enough to stand between changes.
    got2 = word_diff("red kitten and dog", "blue kitten or dog")
    equals = [s["text"] for s in got2 if s["op"] == "equal"]
    check("a longer equal word still stands between changes",
          any("kitten" in t for t in equals), got2)


def test_diff_sides() -> None:
    missing = _diff_sides({"relpath": "solo.md", "base": None,
                           "doc": "x", "local": "y"})
    check("no pushed base → base_found false and empty sides",
          missing["base_found"] is False
          and missing["doc"] == "" and missing["local"] == ""
          and "no pushed base" in missing["note"]
          and "solo.md" in missing["note"], missing)

    sides = _diff_sides({"relpath": "solo.md", "base": "A\n",
                         "doc": "B\n", "local": "C\n"})
    check("with a base, both unified diffs are filled",
          sides["base_found"] is True
          and "base/solo.md" in sides["doc"]
          and "+B" in sides["doc"]
          and "base/solo.md" in sides["local"]
          and "+C" in sides["local"], sides)

    same = _diff_sides({"relpath": "solo.md", "base": "Same\n",
                        "doc": "Same\n", "local": "Same\n"})
    check("identical sides yield empty unified diffs",
          same["base_found"] is True
          and same["doc"] == "" and same["local"] == "", same)


def main() -> None:
    for test in (test_tiny, test_tiny_folds_in_word_diff, test_diff_sides):
        print(f"{test.__name__}:")
        test()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
