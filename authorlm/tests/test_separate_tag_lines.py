"""`gdocs.separate_tag_lines` blanks between consecutive `[…]` tag lines
in the bytes handed to a markdown import.

Google's importer turns a soft break into a space, so stacked
`[Illustration:]` / `[Clip:]` lines without a blank line land as one
paragraph and a later export cannot split them (#285). `_md_units` keeps
local diff units separate; this helper is the import-path counterpart —
apply only to import bytes, never to hashed or local text.

Standalone, hermetic:
    python3 tests/test_separate_tag_lines.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm import gdocs


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_separate_tag_lines() -> None:
    print("separate_tag_lines:")
    stacked = ("Opening.\n\n"
               "[Illustration: one ⇢ one.md]\n"
               "[Illustration: two ⇢ two.md]\n"
               "[Clip: three]\n\n"
               "Closing.\n")
    got = gdocs.separate_tag_lines(stacked)
    check("blank line between consecutive tag lines",
          "[Illustration: one ⇢ one.md]\n\n[Illustration: two ⇢ two.md]\n\n"
          "[Clip: three]" in got, got)
    check("prose before/after unchanged",
          got.startswith("Opening.\n\n") and got.endswith("Closing.\n"), got)
    already = ("[Illustration: one ⇢ one.md]\n\n"
               "[Illustration: two ⇢ two.md]\n")
    check("already-blank-separated tags stay as they are",
          gdocs.separate_tag_lines(already) == already)
    prose = "a line\nand more\n"
    check("soft-broken prose is not blank-separated",
          gdocs.separate_tag_lines(prose) == prose)
    mixed = ("Para.\n[Illustration: alone]\nNext para.\n")
    check("a lone tag between prose gets no extra blank",
          gdocs.separate_tag_lines(mixed) == mixed, mixed)
    # Footnote definitions look like [^n]: … — _TAG_LINE excludes [^…
    note = "[^1]: a note\n[^2]: another\n"
    check("stacked footnote definitions are not treated as tags",
          gdocs.separate_tag_lines(note) == note)


if __name__ == "__main__":
    test_separate_tag_lines()
    print("ALL PASSED")
