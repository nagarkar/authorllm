"""`forms_page._paragraph_around` / `_paragraph_before` supply the
prefix/suffix/anchor strings the Merge Forms page shows around each
open form.

Wrong blank-line bounds attach a neighboring paragraph; skipping
`strip_pending` leaves `<<old>>{{new}}` markers in the UI context and
mis-attributes the surrounding prose. Pure over a marked string —
no DB, no Doc call.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.forms_page import _paragraph_around, _paragraph_before


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_paragraph_around_bounds() -> None:
    text = "Para one.\n\nBefore TARGET after.\n\nPara three.\n"
    start = text.index("TARGET")
    end = start + len("TARGET")
    before, after = _paragraph_around(text, start, end)
    check("prefix is only the text before the span in this paragraph",
          before == "Before ", before)
    check("suffix is only the text after the span in this paragraph",
          after == " after.", after)

    # Span at the start of the file's first paragraph.
    first = "HEAD rest of line.\n\nNext.\n"
    b, a = _paragraph_around(first, 0, 4)
    check("start-of-file span has empty prefix", b == "", b)
    check("start-of-file suffix stays in-paragraph", a == " rest of line.", a)


def test_paragraph_around_strips_neighbor_forms() -> None:
    # Another replace form sits in the same paragraph; context must show
    # its OLD half, never the green new half or the markers.
    text = ("Intro.\n\n"
            "Lead <<struck>>{{green}} mid TARGET tail "
            "<<old2>>{{new2}}.\n\n"
            "Outro.\n")
    start = text.index("TARGET")
    end = start + len("TARGET")
    before, after = _paragraph_around(text, start, end)
    check("prefix collapses sibling form to old half",
          before == "Lead struck mid ", before)
    check("suffix collapses trailing form to old half",
          after == " tail old2.", after)
    check("green / markers never appear in context",
          "green" not in before + after
          and "<<" not in before + after
          and "{{" not in before + after,
          (before, after))


def test_paragraph_before_anchor() -> None:
    text = "First para.\n\nSecond para with <<old>>{{new}} form.\n\nThird.\n"
    at_third = text.index("Third")
    got = _paragraph_before(text, at_third)
    check("previous paragraph, forms read as old halves",
          got == "Second para with old form.", got)

    at_second = text.index("Second")
    check("paragraph before the second is the first",
          _paragraph_before(text, at_second) == "First para.",
          _paragraph_before(text, at_second))

    check("no prior break → text from the start of the file",
          _paragraph_before("Only one paragraph here.", 5)
          == "Only",
          _paragraph_before("Only one paragraph here.", 5))


def main() -> None:
    tests = [
        test_paragraph_around_bounds,
        test_paragraph_around_strips_neighbor_forms,
        test_paragraph_before_anchor,
    ]
    for t in tests:
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
