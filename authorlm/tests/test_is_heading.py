"""`filtering.is_heading` labels markdown heading units in the filter
prompt so the model can leave titles alone.

It is free harness signal on every window — wrong classification either
skips prose the author meant to edit or invites the model to rewrite a
title. No prior suite asserted the AT1–H6 / missing-space edges.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.filtering import is_heading


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_heading_levels() -> None:
    for n in range(1, 7):
        unit = "#" * n + " Title"
        check(f"H{n} with space is a heading", is_heading(unit))
    check("H7 is not a heading (ATX max is 6)",
          not is_heading("####### Too many"))
    check("hashes without a following space are not a heading",
          not is_heading("##NoSpace"))
    check("setext-looking prose is not an ATX heading",
          not is_heading("Title\n====="))


def test_first_line_only_and_empty() -> None:
    check("leading whitespace before the hashes still counts",
          is_heading("  ## Indented title\n\nbody"))
    check("a heading that opens a multi-line unit is still a heading",
          is_heading("# Title\n\nFollowing paragraph."))
    check("a prose unit that later contains a heading line is not",
          not is_heading("Prose first.\n\n# Later"))
    check("empty / None are not headings",
          not is_heading("") and not is_heading(None))
    check("tab after hashes counts as the required separator",
          is_heading("##\tTab title"))


def main() -> None:
    for t in (test_heading_levels, test_first_line_only_and_empty):
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
