"""`findings.edit_entry` is the one gate every producer's `replacement`
passes through before it is staged as a `<<old>>{{new}}` edit in the
author's Doc tab.

It refuses in six ways: quote not verbatim, quote in more than one unit,
quote more than once within its unit, unit already carrying an edit from
this batch, replacement carrying reserved markers, and a replacement that
would empty the unit or leave it unchanged. Before this file only the
multi-unit refusal was exercised (indirectly, in test_passes.py). This
file calls `edit_entry` directly and pins every refusal by its reason
string, plus the success case.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm import findings


UNITS = ["Alpha says a thing.", "Beta repeats, repeats.",
         "Gamma here.", "Gamma here."]


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def _refused(label: str, got: tuple, needle: str) -> None:
    entry, reason = got
    check(f"{label}: no entry", entry is None, repr(got))
    check(f"{label}: reason names '{needle}'", needle in reason, reason)


def test_success() -> None:
    got = findings.edit_entry(list(UNITS), "says a thing", "says it", "n",
                              set())
    check("a verbatim, unique quote yields the staged entry",
          got == ({"n": 1, "new": "Alpha says it.", "why": "n"}, ""),
          repr(got))


def test_reserved_markers() -> None:
    for replacement in ("x {{y", "x }}", "<<x", "x>>"):
        got = findings.edit_entry(list(UNITS), "says a thing", replacement,
                                  "n", set())
        _refused(f"replacement {replacement!r}", got, "reserved markers")


def test_within_unit_duplicate() -> None:
    got = findings.edit_entry(list(UNITS), "repeats", "says", "n", set())
    _refused("quote twice in one unit", got,
             "more than once within its unit")


def test_multi_unit() -> None:
    got = findings.edit_entry(list(UNITS), "Gamma here.", "Gamma there.",
                              "n", set())
    _refused("quote in two units", got, "2 units")


def test_taken_unit() -> None:
    got = findings.edit_entry(list(UNITS), "says a thing", "says it", "n",
                              {1})
    _refused("unit already staged", got, "already carries a staged edit")


def test_empty_result() -> None:
    got = findings.edit_entry(list(UNITS), "Alpha says a thing.", "   ",
                              "n", set())
    _refused("whitespace-only result", got, "leave the unit empty")


def test_identical_result() -> None:
    got = findings.edit_entry(list(UNITS), "says a thing", "says a thing",
                              "n", set())
    _refused("no-op replacement", got, "identical to the quoted text")


def test_not_verbatim() -> None:
    got = findings.edit_entry(list(UNITS), "Alpha  says", "Alpha said",
                              "n", set())
    _refused("quote off by whitespace", got, "not verbatim")


def test_taken_units_untouched() -> None:
    taken = {3}
    findings.edit_entry(list(UNITS), "says a thing", "says it", "n", taken)
    check("a successful call leaves taken_units unchanged", taken == {3},
          repr(taken))
    findings.edit_entry(list(UNITS), "says a thing", "x>>", "n", taken)
    check("a refused call leaves taken_units unchanged", taken == {3},
          repr(taken))


def main() -> None:
    tests = [test_success, test_reserved_markers,
             test_within_unit_duplicate, test_multi_unit, test_taken_unit,
             test_empty_result, test_identical_result, test_not_verbatim,
             test_taken_units_untouched]
    for t in tests:
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
