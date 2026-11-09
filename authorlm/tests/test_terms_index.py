"""`terms.Terms.index` — the book-wide term index (#389).

`devedit.term_index` exercises one happy path (Dharma across two files).
The indexer itself still decides which capitalised spans become terms:
openers stripped, footnotes/directives ignored, plurals collapsed onto
the singular, sentence-initial-only English gated out, and min_count /
min_files thresholds. A silent drift here floods the developmental edit
with English words or drops real terms of art.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.terms import Terms, _paragraphs, capitalised_terms


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_paragraphs_skip_footnotes_and_directives() -> None:
    text = (
        "# Heading\n\n"
        "The Dharma is the ground.\n\n"
        "[^1]: Smith, *Publisher*, 1900.\n\n"
        "[Footnote: author note, not prose]\n\n"
        "The Dharma returns.\n"
    )
    paras = _paragraphs(text)
    check("heading becomes the section, not a paragraph",
          [s for s, _ in paras] == ["Heading", "Heading"], paras)
    check("footnote definitions and directives are dropped",
          [p for _, p in paras]
          == ["The Dharma is the ground.", "The Dharma returns."], paras)


def test_openers_and_not_terms() -> None:
    caps = capitalised_terms("The Dharma is One term. Mr met I alone.")
    check("capitalised_terms keeps multi-word and openers",
          "The Dharma" in caps and "One" in caps, caps)
    files = {
        "a.md": ("The Dharma is the ground. The Dharma returns. "
                 "The Dharma persists.\n\n"
                 "One Two Three are grammar, not terms of art. "
                 "One Two Three again. One Two Three thrice."),
        "b.md": "The Dharma elsewhere. One Two Three elsewhere.",
    }
    names = {t.name for t in Terms(files).index(min_count=2, min_files=1)}
    check("opener 'The' is stripped so the term is Dharma",
          "Dharma" in names and "The Dharma" not in names, names)
    check("bare grammar words in _NOT_TERMS are not indexed",
          not ({"One", "Two", "Three"} & names), names)


def test_plural_collapse_and_thresholds() -> None:
    files = {
        "x.md": ("A Sermon opens. Another Sermon follows. "
                 "Sermons gather twice; Sermons again."),
        "y.md": "The Sermon is elsewhere. Sermons elsewhere.",
    }
    by_name = {t.name: t for t in Terms(files).index(min_count=2, min_files=1)}
    check("plural Sermons collapses onto singular Sermon",
          "Sermon" in by_name and "Sermons" not in by_name, list(by_name))
    check("mention_pattern counts plural hits on the singular term",
          by_name["Sermon"].count >= 5, by_name["Sermon"].count)

    rare = {
        "a.md": "NonceTerm appears once only.",
        "b.md": "Unrelated prose elsewhere.",
    }
    got = Terms(rare).index(min_count=3, min_files=2)
    check("a single hit in one file is below both thresholds",
          got == [], [(t.name, t.count, t.files) for t in got])


def test_sentence_initial_english_is_gated() -> None:
    only_initial = {
        "a.md": ("Experience opens every sentence. Experience again. "
                 "Experience still.\n"),
        "b.md": "Experience opens here too. Experience once more.",
    }
    check("Experience only ever sentence-initial is not a term of art",
          Terms(only_initial).index(min_count=2, min_files=2) == [])

    mid = {
        "a.md": ("Mid Experience is a term. Later Experience mid again. "
                 "Experience mid a third time.\n"),
        "b.md": "Uses Experience mid-sentence. Experience mid again.",
    }
    names = {t.name for t in Terms(mid).index(min_count=2, min_files=2)}
    check("Experience that also appears mid-sentence is kept",
          "Experience" in names, names)


def test_definitions_are_grounding_shaped() -> None:
    files = {
        "a.md": ("The Dharma is the ground of practice. "
                 "The Dharma returns without defining.\n"),
        "b.md": "The Dharma appears again here.",
    }
    dharma = next(t for t in Terms(files).index(min_count=2, min_files=2)
                  if t.name == "Dharma")
    check("definition-shaped sentence is recorded",
          any("is the ground" in s for _loc, s in dharma.definitions),
          dharma.definitions)
    check("a mere mention is not a definition",
          not any("returns without" in s for _loc, s in dharma.definitions),
          dharma.definitions)


def main() -> None:
    for t in (test_paragraphs_skip_footnotes_and_directives,
              test_openers_and_not_terms,
              test_plural_collapse_and_thresholds,
              test_sentence_initial_english_is_gated,
              test_definitions_are_grounding_shaped):
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
