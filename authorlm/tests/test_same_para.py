"""`gdocs._norm_para` / `_same_para` / `first_divergence` / `divergence_note`
align Doc paragraphs with the markdown export for diff-push.

A false mismatch blocks a surgical push or points the author at the
wrong paragraph; missing a hard-break join (illustration + footnote
definition glued by Google's export) sends them to a rebuild instead of
naming the joined tag. Markup and punctuation must not break alignment.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.gdocs import (
    _norm_para,
    _same_para,
    divergence_note,
    first_divergence,
)


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_norm_para_strips_markup_and_case() -> None:
    check("emphasis markers and case drop out",
          _norm_para("Two **Bold** here.") == "twoboldhere")
    check("punctuation and whitespace collapse",
          _norm_para("Hello, world!") == "helloworld")
    check("empty stays empty", _norm_para("") == "")
    check("whitespace-only collapses to empty",
          _norm_para("  \t  ") == "")


def test_same_para_prefix_and_width() -> None:
    check("markup-aside agreement",
          _same_para("Two **bold** here.", "Two bold here.") is True)
    check("both empty agree", _same_para("", "") is True)
    check("empty vs non-empty disagree",
          _same_para("", "x") is False)
    check("common prefix within width agrees",
          _same_para("abcdefghij", "abcdefXXXX", width=6) is True)
    check("disagreement past width is seen",
          _same_para("abcdefghij", "abcdefXXXX", width=7) is False)
    check("shorter side limits the compared prefix",
          _same_para("ab", "abXXX") is True)
    check("different prose disagree",
          _same_para("Alpha", "Beta") is False)


def test_first_divergence_alignment_and_hard_break() -> None:
    plain = first_divergence(
        ["# One", "Two **bold** here.", "Three."],
        ["# One", "Two bold here.", "Four."],
    )
    check("alignment ignores markup; first real split is named",
          plain["index"] == 2 and plain["doc"] == "Three."
          and plain["export"] == "Four."
          and plain["hard_break_after"] is None, plain)

    hard = first_divergence(
        ["[Illustration: a]", "Footnote body.", "After."],
        ["[Illustration: a]\nFootnote body.", "After."],
    )
    check("export hard-break join is recognised",
          hard["index"] == 0
          and hard["hard_break_after"] == "[Illustration: a]"
          and "\n" in hard["export"], hard)

    short = first_divergence(["A", "B"], ["A"])
    check("export shorter: index at the missing paragraph",
          short["index"] == 1 and short["doc"] == "B"
          and short["export"] == ""
          and short["hard_break_after"] is None, short)

    long = first_divergence(["A"], ["A", "B"])
    check("doc shorter: index at the extra export paragraph",
          long["index"] == 1 and long["doc"] == ""
          and long["export"] == "B"
          and long["hard_break_after"] is None, long)


def test_divergence_note_hard_break_vs_rebuild() -> None:
    hard = divergence_note(
        ["[Illustration: a lone tracker kneeling]", "def"],
        ["[Illustration: a lone tracker kneeling]\ndef"],
        "solo.md",
    )
    check("hard-break note names the joined tag and rebuild push",
          "first divergence at paragraph 1" in hard
          and "hard line break after «[Illustration: a lone tracker"
          in hard
          and "authorlm doc push solo.md --rebuild" in hard, hard)

    plain = divergence_note(
        ["# One", "Two", "Three"],
        ["# One", "Two", "Four"],
        "a.md",
    )
    check("plain divergence points at rebuild",
          "first divergence at paragraph 3" in plain
          and "authorlm doc push a.md --rebuild" in plain
          and "hard line break" not in plain, plain)

    long_doc = "X" * 100
    clipped = divergence_note([long_doc], ["Y"], "z.md")
    check("sides longer than 80 chars are clipped with an ellipsis",
          "Doc «" + ("X" * 79) + "…" in clipped
          and long_doc not in clipped, clipped)


def main() -> None:
    tests = [
        test_norm_para_strips_markup_and_case,
        test_same_para_prefix_and_width,
        test_first_divergence_alignment_and_hard_break,
        test_divergence_note_hard_break_vs_rebuild,
    ]
    for t in tests:
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
