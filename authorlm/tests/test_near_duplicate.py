"""`loop.similarity` / `loop.near_duplicate` are the deterministic
proposal firewall (Common Core §8.9 / loop invariant “never re-ask”).

`proposals.create` calls them on every extraction over the whole queue,
so a silent threshold drift either floods the author with restatements
or suppresses genuine refinements. `test_loop.test_firewall` exercises
the door through `create`; this file pins the Jaccard math and the
calibrated 0.72 bar directly.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.loop import NEAR_DUPLICATE, near_duplicate, similarity


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_threshold_constant() -> None:
    check("NEAR_DUPLICATE stays at the measured 0.72 bar",
          NEAR_DUPLICATE == 0.72, NEAR_DUPLICATE)


def test_empty_and_stopwords() -> None:
    check("empty strings score 0", similarity("", "hello") == 0.0)
    check("stop-word-only text scores 0 against anything",
          similarity("the and or but of to", "a flower blooms") == 0.0)
    check("identical content words score 1 even when stop words differ",
          similarity("the flower blooms", "a flower that blooms") == 1.0)


def test_punctuation_restatement() -> None:
    # The exact shape that once accumulated 18 open 'Nothing' rows.
    a = ("The sum of all opposites, all paradoxes, all possibilities; "
         "potent, pregnant with paradox.")
    b = ("The sum of all opposites—all paradoxes, all possibilities; "
         "potent, pregnant with paradox.")
    check("punctuation-only restatement is Jaccard-identical",
          similarity(a, b) == 1.0, similarity(a, b))
    hit = near_duplicate(b, [("prior", a)])
    check("near_duplicate collapses that restatement",
          hit == ("prior", 1.0), hit)


def test_true_restatement_above_bar() -> None:
    a = ("Unredeemed entities that wander without a body after death")
    b = ("Unredeemed souls that wander without a body after death")
    score = similarity(a, b)
    check(f"entity/soul restatement sits at/above 0.72 (measured {score:.3f})",
          score >= NEAR_DUPLICATE, score)
    check("near_duplicate reports it",
          near_duplicate(b, [("p1", a)]) is not None)


def test_material_noun_change_below_bar() -> None:
    # Docstring risk-zone example: the noun phrase that matters differs.
    a = ("the spiritual realm equally existent with the corporeal realm")
    b = ("the spiritual realm equally existent with the realm of qualities")
    score = similarity(a, b)
    check(f"corporeal vs qualities stays below 0.72 (measured {score:.3f})",
          score < NEAR_DUPLICATE, score)
    check("near_duplicate leaves it alone",
          near_duplicate(b, [("p1", a)]) is None)


def test_picks_highest_above_threshold() -> None:
    candidate = "A flower that blooms only at night under cold moonlight"
    existing = [
        ("low", "A stone that sits in the river forever"),
        ("mid", "A flower that blooms only at night under cold stars"),
        ("high", "A flower that blooms only at night under cold moonlight"),
    ]
    hit = near_duplicate(candidate, existing)
    check("returns the highest-scoring prior above the bar",
          hit is not None and hit[0] == "high" and hit[1] == 1.0, hit)


def test_custom_threshold() -> None:
    a = "alpha beta gamma"
    b = "alpha beta delta"
    score = similarity(a, b)
    check("fixture is a partial overlap", 0 < score < 1.0, score)
    check("below a raised threshold → None",
          near_duplicate(b, [("x", a)], threshold=0.99) is None)
    check("at/above a lowered threshold → hit",
          near_duplicate(b, [("x", a)], threshold=score) == ("x", score))


def main() -> None:
    tests = [test_threshold_constant, test_empty_and_stopwords,
             test_punctuation_restatement, test_true_restatement_above_bar,
             test_material_noun_change_below_bar,
             test_picks_highest_above_threshold, test_custom_threshold]
    for t in tests:
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
