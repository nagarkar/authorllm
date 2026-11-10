"""Italic-term lint helpers: `_term_pattern` plurals and `_is_term` gates.

`test_beat_repetition` pins the happy path (`actual occasion` ↔
`actual occasions`). The plural bridge itself still has to walk
`ies`→`y`, `es`→stem, and plain `s`, and `_is_term` still has to refuse
short words, all-opener spans, and six-word emphasis — otherwise a roman
plural slips past the warning or every `*is*` becomes a false positive.
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


def test_term_pattern_bridges_plurals() -> None:
    entities = lint._term_pattern("entities")
    check("ies→y: italic 'entities' also catches roman 'entity'",
          bool(entities.search("An entity remains."))
          and bool(entities.search("Many entities remain.")),
          entities.pattern)
    classes = lint._term_pattern("classes")
    check("es→stem: italic 'classes' also catches roman 'class'",
          bool(classes.search("A class of events."))
          and bool(classes.search("Many classes appear.")),
          classes.pattern)
    occasions = lint._term_pattern("actual occasions")
    check("plain s: plural italic still catches the singular",
          bool(occasions.search("The actual occasion appears."))
          and bool(occasions.search("Many actual occasions appear.")),
          occasions.pattern)
    mass = lint._term_pattern("mass")
    check("a final 'ss' is not stripped as a plural",
          bool(mass.search("the mass itself"))
          and not mass.search("the mas itself"),
          mass.pattern)


def test_term_pattern_feeds_italic_term_findings() -> None:
    hits = lint.check_italic_terms(
        ["An entity remains here."],
        accepted="Whitehead names the final real things *entities*.")
    check("roman singular of an italic plural is a warning",
          [(f.ref, f.severity) for f in hits] == [("entities", "warning")],
          [(f.ref, f.severity, f.message) for f in hits])
    clean = lint.check_italic_terms(
        ["Each *entity* remains here."],
        accepted="Whitehead names the final real things *entities*.")
    check("italic singular of an italic plural is clean",
          clean == [], clean)


def test_is_term_gates() -> None:
    check("a short single word is not a term",
          not lint._is_term("is") and not lint._is_term("for"))
    check("a real multi-word term of art is kept",
          lint._is_term("actual occasions"))
    check("an all-opener span is not a term",
          not lint._is_term("the and for"))
    check("more than five words is not a term",
          not lint._is_term("a b c d e f"))


def main() -> None:
    tests = [test_term_pattern_bridges_plurals,
             test_term_pattern_feeds_italic_term_findings,
             test_is_term_gates]
    for fn in tests:
        print(fn.__name__)
        fn()
    print(f"OK — {len(tests)} italic-term helper cases")


if __name__ == "__main__":
    main()
