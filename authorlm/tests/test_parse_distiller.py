"""`beliefs.parse_distiller` + `belief_labels` — the grammar that turns a
distiller reply into a seed / match / decline.

After the 2026-10-05 ruling (no belief without the distiller), every raw
author explanation reaches `seed_candidate_belief` only through this
parser. An empty or confused reply must decline; a NEW without EXAMPLE
must decline (platitude guard); MATCH must resolve B1… labels (stable
replay hashes) as well as raw ids. Before this file those branches were
only exercised indirectly by scripted LLM strings in test_loop.py.

Run: python3 tests/test_parse_distiller.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm import beliefs as bel


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


ROWS = [
    {"id": "pol-aaaaaaaa", "statement": "Keep footnotes off the main line.",
     "status": "candidate", "metadata": "{}"},
    {"id": "pol-bbbbbbbb", "statement": "Open with a lived example.",
     "status": "retired",
     "metadata": '{"example": "History opened with its formal definition."}'},
]


def test_belief_labels() -> None:
    labels = bel.belief_labels(ROWS)
    check("B1 maps to the first belief id",
          labels["B1"] == "pol-aaaaaaaa", labels)
    check("B2 maps to the second belief id",
          labels["B2"] == "pol-bbbbbbbb", labels)
    check("a raw id still resolves to itself",
          labels["pol-aaaaaaaa"] == "pol-aaaaaaaa")
    menu = bel.belief_menu(ROWS)
    check("the menu shows B1/B2, not random ids (stable replay bytes)",
          "B1 | Keep footnotes off the main line." in menu
          and "B2 | Open with a lived example." in menu
          and "pol-aaaaaaaa" not in menu
          and "[retired]" in menu
          and "e.g. History opened with its formal definition." in menu,
          menu)
    check("an empty menu states that every explanation is NEW",
          "none yet" in bel.belief_menu([]).lower())


def test_parse_decline_paths() -> None:
    labels = bel.belief_labels(ROWS)
    check("None reply declines",
          bel.parse_distiller(None, labels) == ("none", None, None))
    check("empty reply declines (unreachable model is not a NEW)",
          bel.parse_distiller("", labels) == ("none", None, None))
    check("explicit NONE declines",
          bel.parse_distiller("NONE", labels) == ("none", None, None))
    check("NONE with trailing words still declines",
          bel.parse_distiller("NONE — too situation-specific", labels)
          == ("none", None, None))
    check("NEW without EXAMPLE declines (platitude guard)",
          bel.parse_distiller("NEW: Ensure clarity.", labels)
          == ("none", None, None))
    check("EXAMPLE without NEW declines",
          bel.parse_distiller("EXAMPLE: some case", labels)
          == ("none", None, None))
    check("MATCH naming an unknown label declines",
          bel.parse_distiller("MATCH: B99", labels) == ("none", None, None))


def test_parse_match_and_new() -> None:
    labels = bel.belief_labels(ROWS)
    check("MATCH by B-label returns the real id",
          bel.parse_distiller("MATCH: B1", labels)
          == ("match", "pol-aaaaaaaa", None))
    check("MATCH by raw id still resolves",
          bel.parse_distiller("MATCH: pol-bbbbbbbb", labels)
          == ("match", "pol-bbbbbbbb", None))
    check("NEW + EXAMPLE yields the statement and case",
          bel.parse_distiller(
              'NEW: "A note states what a thing is, not its role."\n'
              'EXAMPLE: "Nothing was re-described by its argumentative role."',
              labels)
          == ("new",
              "A note states what a thing is, not its role.",
              "Nothing was re-described by its argumentative role."))


def main_test() -> None:
    print("belief_labels / belief_menu")
    test_belief_labels()
    print("parse_distiller decline paths")
    test_parse_decline_paths()
    print("parse_distiller match and new")
    test_parse_match_and_new()
    print("\nall checks passed")


if __name__ == "__main__":
    main_test()
