"""`threads.own_line_form` / `math_spans` / `in_math` / `pending_forms`
decide which bare `{{…}}` count as staged insertions on Doc-road resolve.

A false positive FIFO-steals author set notation or TeX braces and
collapses the real planted insert to empty (silent manuscript
corruption). A false negative leaves critique / footnote inserts
unresolved. `test_passes` covers the Doc-road twin; this file pins the
pure helpers and the list that every resolve path reads.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.threads import in_math, math_spans, own_line_form, pending_forms


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_math_spans_inline_and_display() -> None:
    text = "Prose ${{a,b}}$ then $$\n{{-q}}\n$$ end."
    spans = math_spans(text)
    check("finds inline then display",
          len(spans) == 2 and spans[0][0] == text.find("$")
          and spans[1][0] == text.find("$$"), spans)
    check("inline brace start is inside math",
          in_math(text, text.find("{{")) is True)
    check("prose before the first $ is outside math",
          in_math(text, 0) is False)
    check("text after the closing $$ is outside math",
          in_math(text, len(text) - 1) is False)


def test_own_line_form_inline_vs_planted() -> None:
    prose = "See {{title}} in the margin.\n"
    start = prose.find("{{")
    end = prose.find("}}") + 2
    check("inline author braces are not own-line",
          own_line_form(prose, start, end) is False)

    planted = "Body.\n\n{{insert here}}\n\nMore.\n"
    start = planted.find("{{")
    end = planted.find("}}") + 2
    check("own-line planted insert passes",
          own_line_form(planted, start, end) is True)

    stacked = "Body.\n\n{{def1}}\n{{def2}}\n\nMore.\n"
    a0 = stacked.find("{{")
    a1 = stacked.find("}}") + 2
    b0 = stacked.find("{{", a1)
    b1 = stacked.find("}}", b0) + 2
    check("stacked footnote defs each pass (single newline)",
          own_line_form(stacked, a0, a1) is True
          and own_line_form(stacked, b0, b1) is True)


def test_math_braces_never_own_line_forms() -> None:
    inline = "Keep ${{a,b},{c}}$ intact.\n"
    start = inline.find("{{")
    end = inline.find("}}", start) + 2
    # First }} closes the first pair; own_line_form only needs the start
    # position for the math gate, but feed a span covering the first pair.
    check("inline math braces refuse own-line",
          own_line_form(inline, start, end) is False)

    display = "Before.\n\n$$\n{{a,b}}\n$$\n\nAfter.\n"
    start = display.find("{{")
    end = display.find("}}") + 2
    check("display-block set notation refuses own-line",
          own_line_form(display, start, end) is False)
    check("display-block start is inside math",
          in_math(display, start) is True)

    oneline = "Before.\n\n$${{a,b}}$$\n\nAfter.\n"
    start = oneline.find("{{")
    end = oneline.find("}}") + 2
    check("one-line $$…$$ set notation refuses own-line",
          own_line_form(oneline, start, end) is False)


def test_pending_forms_lists_only_real_inserts() -> None:
    text = (
        "Prose ${{a,b},{c}}$ here.\n\n"
        "$$\n{{-q,+q}}\n$$\n\n"
        "{{real insert}}\n\n"
        "Inline {{title}} stays.\n"
        "<<old>>{{new}}\n"
    )
    forms = pending_forms(text)
    kinds = [(f["kind"], f["new"]) for f in forms]
    check("only the planted insert and the replace are listed",
          kinds == [("insert", "real insert"), ("replace", "new")],
          kinds)
    check("math and inline author braces are absent",
          all(f["new"] not in ("a,b},{c", "-q,+q", "title")
              for f in forms), forms)

    stacked = "Body.\n\n{{def1}}\n{{def2}}\n\nMore.\n"
    check("stacked defs both appear as inserts",
          [(f["kind"], f["new"]) for f in pending_forms(stacked)]
          == [("insert", "def1"), ("insert", "def2")])


def main() -> None:
    tests = [
        test_math_spans_inline_and_display,
        test_own_line_form_inline_vs_planted,
        test_math_braces_never_own_line_forms,
        test_pending_forms_lists_only_real_inserts,
    ]
    for t in tests:
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
