"""`api._twin_warning` is §9.2's informational line for byte-identical
accepted replacements.

Twins settle by position in the Doc; the warning must fire when two
accepted edits share the same `proposed_old`, name their units, and stay
silent for empty/old-distinct cases. A silent miss leaves the author to
discover RISK-6 the hard way (decline attaches to the twin they kept).
A false positive on empty olds or distinct paragraphs is noise on every
push. Filter push e2e covers transport; these pins cover the grouping.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.api import _twin_warning


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def _t(old: str, unit: int | None = 1) -> dict:
    meta = {} if unit is None else {"anchor_paragraph": unit}
    return {"proposed_old": old, "metadata": json.dumps(meta)}


def test_no_twins() -> None:
    check("empty accepted list is silent", _twin_warning([]) is None)
    check("one replacement is silent",
          _twin_warning([_t("same")]) is None)
    check("distinct paragraphs are silent",
          _twin_warning([_t("alpha", 1), _t("beta", 2)]) is None)


def test_empty_old_never_groups() -> None:
    # Empty proposed_old is common for pure insertions; grouping those
    # would spam every push that stages two inserts.
    check("two empty olds do not twin",
          _twin_warning([_t("", 1), _t("", 2)]) is None)


def test_twins_name_units_sorted() -> None:
    got = _twin_warning([_t("same para", 5), _t("same para", 2)])
    check("twins produce a warning line", got is not None, got)
    assert got is not None
    check("count is two", "2 of these changes" in got, got)
    check("clamped paragraph text appears", "«same para»" in got, got)
    check("units sort ascending", "units 2 and 5" in got, got)
    check("position-settle guidance is present",
          "settle by position" in got, got)
    check("deletion hazard is named", "DELETING" in got, got)


def test_missing_anchor_reads_as_zero() -> None:
    got = _twin_warning([
        {"proposed_old": "x", "metadata": "{}"},
        {"proposed_old": "x", "metadata": json.dumps({"anchor_paragraph": 3})},
    ])
    check("missing anchor_paragraph becomes unit 0",
          got is not None and "units 0 and 3" in got, got)


def test_two_twin_groups() -> None:
    got = _twin_warning([
        _t("first", 1), _t("first", 4),
        _t("second", 2), _t("second", 7), _t("second", 9),
    ])
    check("two groups yield two lines",
          got is not None and got.count("\n") == 1, got)
    assert got is not None
    check("first group counted", "2 of these changes" in got, got)
    check("second group counted", "3 of these changes" in got, got)


def main() -> None:
    for t in (test_no_twins, test_empty_old_never_groups,
              test_twins_name_units_sorted, test_missing_anchor_reads_as_zero,
              test_two_twin_groups):
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
