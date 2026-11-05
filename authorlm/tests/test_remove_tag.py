"""`directive_tags.remove_tag` — the shared cut used by landing and resolve.

An inline cut must collapse the surrounding spaces so prose does not keep
a double gap or lose the space before punctuation. A standalone cut must
take its whole line (and the blank separator) without eating the next
paragraph. A silent drift here becomes a Doc resolve mismatch.

Run: python3 tests/test_remove_tag.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.directive_tags import remove_tag, scan_text


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_inline_collapses_space_before_punctuation() -> None:
    text = "The claim [Explain: why] holds."
    tag = scan_text(text, "explain")[0]
    got = remove_tag(text, tag)
    check("space before the tag is dropped when the next char is a letter "
          "so one space remains",
          got == "The claim holds.", repr(got))

    text = "The claim [Explain: why]."
    tag = scan_text(text, "explain")[0]
    got = remove_tag(text, tag)
    check("space before the tag is dropped before sentence punctuation",
          got == "The claim.", repr(got))


def test_inline_keeps_needed_space() -> None:
    text = "See[Explain: x] more."
    tag = scan_text(text, "explain")[0]
    got = remove_tag(text, tag)
    check("no leading space means the join stays tight on the left",
          got == "See more.", repr(got))


def test_standalone_takes_its_line() -> None:
    text = "Prior para.\n\n[Suggestion: tighten]\n\nNext para.\n"
    tag = scan_text(text, "suggestion")[0]
    check("scan marks a own-line suggestion standalone",
          tag["standalone"] is True, tag)
    got = remove_tag(text, tag)
    check("standalone cut drops the tag line and one blank separator",
          got == "Prior para.\n\nNext para.\n", repr(got))


def test_standalone_at_eof() -> None:
    text = "Prior para.\n\n[Judgment: cut]\n"
    tag = scan_text(text, "judgment")[0]
    got = remove_tag(text, tag)
    check("removing the final standalone line leaves one trailing newline",
          got == "Prior para.\n", repr(got))


def main() -> None:
    tests = [
        test_inline_collapses_space_before_punctuation,
        test_inline_keeps_needed_space,
        test_standalone_takes_its_line,
        test_standalone_at_eof,
    ]
    for t in tests:
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
