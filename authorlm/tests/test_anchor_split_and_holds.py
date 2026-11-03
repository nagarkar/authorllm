"""Doc sync doors that silently corrupt when wrong.

`_split_by_anchors` — every pull after Google dropped tab-title headings
cuts the export at each tab's first paragraph. A short anchor that is a
prefix of a longer mid-chapter line used to steal the rest of the prior
tab into the next; pull then overwrote local files with the wrong text.

`_holds_doc_edits` — the push guard for a hand-settled conflict. Set
membership treated a Doc add/delete of a repeated line as held whenever
another copy remained, so push restored (or wiped) the Doc's edit.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.gdocs import _holds_doc_edits, _split_by_anchors


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_short_anchor_does_not_steal_prior_tab() -> None:
    text = (
        "Opening of A.\n\n"
        "Yes. This is still chapter A elaborating.\n\n"
        "More A.\n\n"
        "Yes.\n\n"
        "Real body of B.\n"
    )
    out = _split_by_anchors(text, ["a.md", "b.md"],
                            {"a.md": "Opening of A.", "b.md": "Yes."})
    check("short anchor matches only the exact line",
          out.get("b.md", "").startswith("Yes.\n"), out)
    check("prior tab keeps the 'Yes. …' prose line",
          "Yes. This is still chapter A elaborating." in out.get("a.md", ""),
          out.get("a.md"))
    check("stolen mid-chapter line is not b's start",
          not out.get("b.md", "").startswith("Yes. This is still"), out)


def test_long_anchor_prefix_still_matches() -> None:
    text = (
        "Opening of one.\n\nBody of one.\n\n"
        "Opening of two with a long first paragraph that continues.\n\n"
        "Body of two.\n"
    )
    # Anchor shorter than the export line but ≥ _ANCHOR_PREFIX_MIN: the
    # export joined the next clause onto the same line.
    anchors = {
        "one.md": "Opening of one.",
        "two.md": "Opening of two with a long first paragraph",
    }
    out = _split_by_anchors(text, ["one.md", "two.md"], anchors)
    check("long-enough prefix still finds the tab",
          "two.md" in out and out["two.md"].startswith("Opening of two"),
          out)


def test_holds_rejects_duplicate_line_false_positive() -> None:
    base = "Alpha.\n\nBeta.\n\nGamma.\n"
    doc_add = "Alpha.\n\nBeta.\n\nBeta.\n\nGamma.\n"
    local = "Alpha.\n\nBeta.\n\nGamma.\n\n# my local edit\n"
    check("Doc's second copy of a repeated line is not held by set membership",
          _holds_doc_edits(local, base, doc_add) is False)

    base_dup = "Intro.\n\nShared line.\n\nMiddle.\n\nShared line.\n\nEnd.\n"
    doc_rm = "Intro.\n\nShared line.\n\nMiddle.\n\nEnd.\n"
    check("Doc's deletion of one of two identical lines is not held while both remain",
          _holds_doc_edits(base_dup, base_dup, doc_rm) is False)


def test_holds_settled_adjacent_edit() -> None:
    # Same fixture as test_audio's bind-again settled conflict.
    base = "x\n\na\n\nb\n\nc\n\nd\n"
    doc = "x\n\nA\n\nn1\n\nb\n\nn2\n\nc\n\nd\n"
    settled = "x\n\nA\n\nn1\n\nB\n\nn2\n\nc\n\nd\n"
    lost = "x\n\nA\n\nB\n\nn2\n\nc\n\nd\n"
    check("settled conflict with adjacent local edit holds",
          _holds_doc_edits(settled, base, doc) is True)
    check("dropping a Doc-added line refuses",
          _holds_doc_edits(lost, base, doc) is False)


if __name__ == "__main__":
    test_short_anchor_does_not_steal_prior_tab()
    test_long_anchor_prefix_still_matches()
    test_holds_rejects_duplicate_line_false_positive()
    test_holds_settled_adjacent_edit()
    print("ok")
