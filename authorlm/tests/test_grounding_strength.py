"""`extraction.grounding_strength` and `extraction_calibration._best`
choose which paragraph a relocated candidate is judged on.

A passing mention earlier in reading order used to win (#291): "hierarchy"
was judged on "hierarchy of processes…" in another essay, the adjudicator
dropped that span, and the essay's defining uses were retired. Strength
1.0 (heading or definition-shaped) must beat 0.7 (passing mention); among
equals, reading order wins via `max` stability on the earlier hit.

Standalone, hermetic, zero LLM calls:
    python3 tests/test_grounding_strength.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.extraction import grounding_strength
from authorlm.extraction_calibration import _best


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_grounding_strength() -> None:
    print("grounding_strength:")
    check("heading paragraph → 1.0",
          grounding_strength("Hierarchy", "Hierarchy",
                             "# Hierarchy\n\nbody") == 1.0)
    check("span that is itself a heading line → 1.0",
          grounding_strength("# Hierarchy", "Hierarchy") == 1.0)
    check("definition after the name → 1.0",
          grounding_strength("Hierarchy is the nesting of processes.",
                             "Hierarchy") == 1.0)
    check("definition before the name → 1.0",
          grounding_strength("We call this nesting Hierarchy.",
                             "Hierarchy") == 1.0)
    check("passing mention → 0.7",
          grounding_strength(
              "a hierarchy of processes enveloped within each process",
              "Hierarchy") == 0.7)
    check("empty name never matches a definition → 0.7",
          grounding_strength("Hierarchy is nested.", "") == 0.7)
    check("name absent from the span → 0.7",
          grounding_strength("Something else is nested.",
                             "Hierarchy") == 0.7)


def test_best_prefers_definition() -> None:
    print("_best:")
    # hit tuple: (file, n, paragraph, heading, section_ordinal) — _best
    # only reads hit[2] (the paragraph) through span_of.
    mention = ("other.md", 1,
               "a hierarchy of processes enveloped within each process",
               "Aside", 1)
    definition = ("hierarchy.md", 3,
                  "Hierarchy is the nesting of processes within a process.",
                  "The term", 2)
    span_of = lambda p: p  # the whole paragraph is the span under test

    check("empty hits → None", _best([], "Hierarchy", span_of) is None)
    got = _best([mention, definition], "Hierarchy", span_of)
    check("definition beats an earlier passing mention",
          got is definition, got)
    # Same strength: max keeps the first among equals (reading order).
    early = ("a.md", 1, "Hierarchy is early.", "A", 1)
    late = ("b.md", 1, "Hierarchy is late.", "B", 1)
    got = _best([early, late], "Hierarchy", span_of)
    check("among equal strength, first in reading order wins",
          got is early, got)
    only = [mention]
    check("sole hit is returned even when weak",
          _best(only, "Hierarchy", span_of) is mention)


if __name__ == "__main__":
    test_grounding_strength()
    test_best_prefers_definition()
    print("ALL PASSED")
