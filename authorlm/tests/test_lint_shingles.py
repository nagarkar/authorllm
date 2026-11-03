"""Lint word-normalization, syllable count, and n-gram shingles.

These three helpers feed reading-grade, overlap checks, `best_match`,
and `critic.nearest_passages`. A wrong lowercasing / apostrophe strip
changes every similarity ratio; a broken syllable heuristic moves grade
bands; empty or short text must yield empty shingle sets so nearest
passages stay quiet instead of scoring noise. #158 covers best_match /
changed_only — not these foundations.
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


def test_normalize_words() -> None:
    got = lint._normalize_words("The Field's \"pull\" — and Field’s.")
    check("lowercases; mid-word apostrophes stay (possessives match)",
          got == ["the", "field's", "pull", "and", "field’s"], repr(got))
    check("leading/trailing apostrophes strip so quoted words align",
          lint._normalize_words("'Hello' ’World’") == ["hello", "world"],
          repr(lint._normalize_words("'Hello' ’World’")))
    check("empty / punctuation-only → []",
          lint._normalize_words("") == []
          and lint._normalize_words("… — !") == [])


def test_syllables() -> None:
    check("empty → 0", lint._syllables("") == 0)
    check("short words count as 1",
          lint._syllables("a") == 1 and lint._syllables("the") == 1)
    check("simple open syllables",
          lint._syllables("paper") == 2 and lint._syllables("idea") >= 2)
    check("silent trailing e drops a count when safe",
          lint._syllables("make") == 1 and lint._syllables("table") == 2)
    check("never returns below 1 for a real word",
          lint._syllables("strengths") >= 1)


def test_shingles() -> None:
    text = "The quick brown fox jumps over the lazy dog"
    tri = lint.shingles(text, 3)
    check("3-grams cover sliding windows",
          ("the", "quick", "brown") in tri
          and ("lazy", "dog") not in tri
          and ("over", "the", "lazy") in tri,
          repr(sorted(tri)[:3]))
    check("n larger than word count → empty",
          lint.shingles("one two", 5) == set())
    check("n=1 yields every normalized word once as a singleton",
          lint.shingles("A a B", 1) == {("a",), ("b",)})
    a = lint.shingles("the field is open to the reader now", 3)
    b = lint.shingles("the field is closed to the reader now", 3)
    check("near-duplicate passages share overlapping 3-grams",
          ("the", "field", "is") in (a & b)
          and ("to", "the", "reader") in (a & b),
          repr(a & b))


def test_sentences_protect_abbreviations() -> None:
    got = lint.sentences("See e.g. the Field. Then go.")
    check("e.g. does not split a sentence",
          got == ["See e.g. the Field.", "Then go."], repr(got))


def main() -> None:
    for t in (test_normalize_words, test_syllables, test_shingles,
              test_sentences_protect_abbreviations):
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
