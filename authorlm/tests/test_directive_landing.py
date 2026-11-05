"""`directives._spaced` / `_land_explain` / `_land_ref` / `_append_definition`
are the byte-identical landing seam for Explain and Footnote applies.

`check_directives` covers the end-to-end apply path; this file pins the
spacing edges that keep the local road and the Doc `{{new}}` half
byte-identical — a silent space drift becomes a resolve mismatch.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.directives import (
    _append_definition,
    _land_explain,
    _land_ref,
    _spaced,
)


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def _tag(text: str, start: int, end: int, *, standalone: bool = False) -> dict:
    return {"start": start, "end": end, "standalone": standalone}


def test_spaced_inline_needs_both_sides() -> None:
    text = "The lieholds on."
    # Insert between 'lie' and 'holds' — both sides need a space.
    tag = _tag(text, 7, 7)
    got = _spaced(text, tag, "vital")
    check("inline draft gets a leading and trailing space when prose needs it",
          got == " vital ", repr(got))


def test_spaced_no_leading_before_punctuation() -> None:
    text = "The lie."
    tag = _tag(text, 7, 7)  # before the period
    got = _spaced(text, tag, "vital")
    # before is 'e' (needs space), after is '.' — trailing space only if after
    # is alnum/open-quote; period does not get a trailing space.
    check("no trailing space before sentence punctuation",
          got == " vital", repr(got))


def test_spaced_no_space_after_open_paren() -> None:
    text = "See (here)."
    tag = _tag(text, 5, 5)  # right after '('
    got = _spaced(text, tag, "vital")
    check("open parenthesis does not force a leading space",
          got == "vital ", repr(got))


def test_spaced_standalone_untouched() -> None:
    draft = "why the armour must fall"
    tag = _tag("", 0, 0, standalone=True)
    check("standalone Explain uses the draft verbatim",
          _spaced("anything", tag, draft) == draft)


def test_land_explain_replaces_span() -> None:
    text = "The vital lie.[Explain: what it is] Then on."
    start = text.index("[Explain:")
    end = text.index("]") + 1
    got = _land_explain(text, _tag(text, start, end), "a coping story")
    check("explain lands the passage where the tag was",
          got == "The vital lie. a coping story Then on.", repr(got))


def test_land_ref_adds_space_before_alnum() -> None:
    text = "A claim.[Footnote: src]More."
    start = text.index("[Footnote:")
    end = text.index("]") + 1
    got = _land_ref(text, _tag(text, start, end), "B1")
    check("superscript gets a trailing space when the next char is alnum",
          got == "A claim.[^B1] More.", repr(got))


def test_land_ref_no_space_before_period() -> None:
    text = "A claim.[Footnote: src]."
    start = text.index("[Footnote:")
    end = text.index("]") + 1
    got = _land_ref(text, _tag(text, start, end), "B1")
    check("no trailing space before a period",
          got == "A claim.[^B1].", repr(got))


def test_append_definition_first_and_continuation() -> None:
    body = "# Essay\n\nProse.\n"
    once = _append_definition(body, "B1", "One line.")
    check("first definition is blank-line separated at EOF",
          once.endswith("\n\n[^B1]: One line.\n"), repr(once))
    multi = _append_definition(once, "B2", "Para one.\n\nPara two.")
    check("multi-paragraph definition indents continuation lines",
          "[^B2]: Para one.\n\n    Para two.\n" in multi, repr(multi))
    check("prior definition is preserved", "[^B1]: One line." in multi)


def main() -> None:
    tests = [
        test_spaced_inline_needs_both_sides,
        test_spaced_no_leading_before_punctuation,
        test_spaced_no_space_after_open_paren,
        test_spaced_standalone_untouched,
        test_land_explain_replaces_span,
        test_land_ref_adds_space_before_alnum,
        test_land_ref_no_space_before_period,
        test_append_definition_first_and_continuation,
    ]
    for t in tests:
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
