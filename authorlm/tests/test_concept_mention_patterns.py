"""Realization vs first-mention firewall (`concept_pattern` /
`mention_pattern`).

`concept_pattern` is the existence check: plurals and any casing count.
`mention_pattern` is stricter for single-word capitalized names so casual
English ("time and space") does not count as the term of art ("Space").
test_api exercises one capital-mention path through guidance; nothing pins
plural stems or the battlefield≠Field boundary hermetically.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.concepts import concept_pattern, mention_pattern


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_concept_plurals_and_boundaries() -> None:
    traj = concept_pattern("Trajectory")
    check("singular matches", bool(traj.search("a Trajectory ends")))
    check("plural ies matches", bool(traj.search("many trajectories")))
    check("substring battlefield does not match Field",
          concept_pattern("Field").search("battlefield") is None)
    check("word Field still matches",
          bool(concept_pattern("Field").search("a Field opens")))
    check("simple plural fields matches",
          bool(concept_pattern("Field").search("fields of view")))


def test_mention_capital_gate() -> None:
    space = mention_pattern("Space")
    check("capitalized term of art counts",
          bool(space.search("ye name it Space")))
    check("casual lowercase does not count",
          space.search("time and space") is None)
    check("plural Spaces still counts when capitalized",
          bool(space.search("two Spaces collide")))


def test_mention_multiword_stays_tolerant() -> None:
    pat = mention_pattern("the herdsman")
    check("multi-word keeps case-insensitive match",
          bool(pat.search("The Herdsman arrives")))
    check("multi-word lowercase also matches",
          bool(pat.search("saw the herdsman leave")))


def test_mention_y_stem() -> None:
    pat = mention_pattern("Trajectory")
    check("capital Trajectory matches",
          bool(pat.search("Trajectory")))
    check("plural Trajectories matches",
          bool(pat.search("Trajectories diverge")))
    check("lowercase trajectory does not (single-word capital gate)",
          pat.search("trajectory") is None)


def main() -> None:
    tests = [
        test_concept_plurals_and_boundaries,
        test_mention_capital_gate,
        test_mention_multiword_stays_tolerant,
        test_mention_y_stem,
    ]
    for t in tests:
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
