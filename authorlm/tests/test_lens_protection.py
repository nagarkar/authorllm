"""`lenses.protected_files` and `_is_card` gate which chapters a lens
may touch and how the reading-order block labels them.

A missing `register = "protected"` match lets a lens rewrite a hymn the
author exempted; treating a long back-matter essay as a card drops it
from proportion targets. Both are pure lookups over toc.toml + word
count — no DB — and no hermetic test called them directly before.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.lenses import CARD_WORDS, _is_card, protected_files


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


FILES = {
    "toc.toml": (
        '[[chapter]]\nfile = "title.md"\nmatter = "front"\n\n'
        '[[chapter]]\nfile = "essay.md"\n\n'
        '[[chapter]]\nfile = "hymn.md"\nregister = "protected"\n\n'
        '[[chapter]]\nfile = "notes.md"\nmatter = "back"\n'
        'register = "protected"\n'
    ),
    "title.md": "# Title\n\n" + "short " * 10,
    "essay.md": "# Essay\n\n" + "word " * (CARD_WORDS + 50),
    "hymn.md": "# Hymn\n\n" + "line " * 20,
    "notes.md": "# Notes\n\n" + "note " * (CARD_WORDS + 10),
}


def test_protected_files_from_toc() -> None:
    got = protected_files(FILES)
    check("both register=protected entries are listed",
          got == ["hymn.md", "notes.md"], got)
    bare = {**FILES, "toc.toml": '[[chapter]]\nfile = "essay.md"\n'}
    check("no protected register → empty list",
          protected_files(bare) == [], protected_files(bare))


def test_is_card_is_word_count_not_matter() -> None:
    matter = {"title.md": "front", "essay.md": "main",
              "hymn.md": "main", "notes.md": "back"}
    check("short front-matter title is a card",
          _is_card(FILES, matter, "title.md"))
    check("short hymn is a card even without matter=front",
          _is_card(FILES, matter, "hymn.md"))
    check("long main essay is not a card",
          not _is_card(FILES, matter, "essay.md"))
    check("long back-matter notes are not a card — matter alone "
          "does not make one",
          not _is_card(FILES, matter, "notes.md"))
    check("missing file counts as empty → a card",
          _is_card(FILES, matter, "absent.md"))


def main() -> None:
    tests = [
        test_protected_files_from_toc,
        test_is_card_is_word_count_not_matter,
    ]
    for t in tests:
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
