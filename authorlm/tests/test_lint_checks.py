"""Individual `lint.check_*` helpers encode ratified style laws that
`write propose` can refuse as errors.

`test_api.check_lint` exercises delta / mark_changes / reading_grade through
the report seam. The per-law matchers — What-openers, term-in-definition,
em-dash stacks, this-not-that carve-outs, overlap runs, and redefinition —
had no direct unit coverage, so a regex drift could weaken an error gate
or flood warnings without a failing suite.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm import lint


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def _codes(findings) -> list[str]:
    return [f.code for f in findings]


def test_what_opener() -> None:
    hits = lint.check_what_opener([
        "## What Is Ground",
        "What follows is the claim.",
        "The question is what remains.",
    ])
    check("heading is skipped even when it starts with What",
          all(f.unit != 1 for f in hits), _codes(hits))
    check("prose What-opener is an error",
          any(f.code == "what-opener" and f.severity == "error"
              and f.unit == 2 for f in hits),
          [(f.unit, f.code, f.severity) for f in hits])
    check("mid-sentence 'what' is not an opener",
          all(f.unit != 3 for f in hits), _codes(hits))


def test_term_in_definition() -> None:
    hits = lint.check_term_in_definition([
        "A Monad is a simple substance that is a Monad.",
        "A Monad is a simple substance without parts.",
        "Ground names the condition of appearance.",
    ])
    check("circular definition is an error naming the term",
          any(f.code == "term-in-definition" and f.severity == "error"
              and f.ref == "Monad" for f in hits),
          [(f.ref, f.message) for f in hits])
    check("clean definition is silent",
          all("without parts" not in f.quote for f in hits))
    check("non-definitional 'names' without restating the subject is silent",
          all(f.unit != 3 for f in hits), _codes(hits))


def test_em_dashes() -> None:
    one = lint.check_em_dashes([
        "A claim — with one aside — stays info."])
    check("one em-dash pair is info, not error",
          len(one) == 1 and one[0].code == "em-dash"
          and one[0].severity == "info",
          [(f.code, f.severity) for f in one])
    stacked = lint.check_em_dashes([
        "A — one — B — two — C — three — D."])
    check("three or more em-dashes escalate to error",
          any(f.code == "em-dash-stack" and f.severity == "error"
              for f in stacked),
          [(f.code, f.severity, f.message) for f in stacked])
    ascii_stack = lint.check_em_dashes([
        "A -- one -- B -- two -- C -- three -- D."])
    check("ASCII ' -- ' stacks the same way",
          any(f.code == "em-dash-stack" for f in ascii_stack),
          [(f.code, f.message) for f in ascii_stack])


def test_this_not_that_carveouts() -> None:
    bad = lint.check_this_not_that([
        "It is not appearance, but reality itself."])
    check("'not X but Y' is a warning",
          any(f.code == "this-not-that" and f.severity == "warning"
              for f in bad),
          [(f.severity, f.message) for f in bad])
    ok = lint.check_this_not_that([
        "It is not only appearance, but also reality itself.",
        "It is not merely a sign, but rather a cause."])
    check("'not only/just/merely … but also/rather' carve-outs stay silent",
          ok == [], [(f.message,) for f in ok])
    tail = lint.check_this_not_that([
        "Call it a cause, not a mere sign."])
    check("'X, not Y' tail is info",
          any(f.code == "this-not-that" and f.severity == "info"
              for f in tail),
          [(f.severity, f.message) for f in tail])


def test_sentence_length() -> None:
    short = " ".join(["word"] * 40) + "."
    long = " ".join(["word"] * 41) + "."
    hits = lint.check_sentence_length([short, long], limit=40)
    check("exactly at the limit is silent",
          all(f.unit != 1 for f in hits), _codes(hits))
    check("one word over the limit warns",
          any(f.code == "long-sentence" and f.severity == "warning"
              and f.unit == 2 for f in hits),
          [(f.unit, f.message) for f in hits])


def test_overlap_and_redefinition() -> None:
    phrase = ("the spiritual realm equally existent with the corporeal "
              "realm today")
    # 8+ word verbatim run across files.
    units = [f"Elsewhere we said {phrase} and moved on."]
    corpus = {"01-prior.md": [f"Open with {phrase} before the turn."]}
    overlaps = lint.check_overlap(units, corpus, n=8)
    check("verbatim eight-word run is an overlap error",
          any(f.code == "overlap" and f.severity == "error"
              and "01-prior.md" in f.message for f in overlaps),
          [(f.message, f.ref) for f in overlaps])

    redefs = lint.check_redefinition(
        ["A Monad is a simple substance without parts."],
        [("Monad", "01-prior.md"), ("Ground", "02-other.md")],
        this_file="03-here.md")
    check("defining a foreign concept is a redefinition finding",
          any(f.code == "redefinition" and "01-prior.md" in f.message
              for f in redefs),
          [(f.code, f.message) for f in redefs])
    same = lint.check_redefinition(
        ["A Monad is a simple substance without parts."],
        [("Monad", "03-here.md")],
        this_file="03-here.md")
    check("redefining in the introducing file is silent",
          same == [], [(f.message,) for f in same])


def main() -> None:
    for t in (test_what_opener, test_term_in_definition, test_em_dashes,
              test_this_not_that_carveouts, test_sentence_length,
              test_overlap_and_redefinition):
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
