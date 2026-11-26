"""`writing._plan_concepts` unions every concept name across a beat plan,
de-duplicating case-insensitively and sorting by the lowercased key.

The union (not this-beat's slice) is deliberate: block A must be byte-
identical for every beat of one plan so the cache prefix stays stable.
A case fold that drops a spelling, or a per-beat slice that reshuffles
block A, silently invalidates that cache contract.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.writing import _plan_concepts


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_empty_plan() -> None:
    check("empty plan → []", _plan_concepts([]) == [])
    check("non-dict beats ignored",
          _plan_concepts(["noise", None, 3]) == [])


def test_union_casefold_and_sort() -> None:
    plan = [
        {"concepts": ["Zeno", "agency"]},
        {"concepts": ["Agency", "  free will  "]},
        {"concepts": ["zeno", "", "   ", 12, None]},
        "not-a-beat",
        {"concepts": None},
        {},
    ]
    got = _plan_concepts(plan)
    check("case-insensitive union keeps first spelling, sorted by key",
          got == ["agency", "free will", "Zeno"], got)
    check("whitespace-only and non-str names are dropped",
          "12" not in got and "" not in got, got)


def test_single_beat_preserves_order_by_key_not_input() -> None:
    got = _plan_concepts([{"concepts": ["gamma", "Alpha", "beta"]}])
    check("single beat still sorts by lowercased key",
          got == ["Alpha", "beta", "gamma"], got)


def main() -> None:
    tests = [
        test_empty_plan,
        test_union_casefold_and_sort,
        test_single_beat_preserves_order_by_key_not_input,
    ]
    for t in tests:
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
