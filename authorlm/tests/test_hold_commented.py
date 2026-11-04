"""`gdocs._hold_commented` is the surgical-push door for open margin comments.

A changed tab paragraph that still carries an open comment must keep its
OLD text in the target while every other change goes through. Wrong release
silently drops the author's open thread; wrong hold blocks an unrelated
edit forever. One-for-one replaces hold paragraph by paragraph; uneven
replaces and deletes that touch a commented paragraph hold as a block.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.gdocs import _hold_commented


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_no_marks_passthrough() -> None:
    canonical = ["a", "b"]
    local = ["A", "B", "C"]
    target, held = _hold_commented(canonical, local, {})
    check("no marks returns local unchanged", target == local, target)
    check("no marks holds nothing", held == [], held)


def test_one_for_one_replace() -> None:
    canonical = ["old-a", "old-b", "old-c"]
    local = ["new-a", "new-b", "new-c"]
    marks = {1: [{"comment_id": "c1", "content": "note"}]}
    target, held = _hold_commented(canonical, local, marks)
    check("commented paragraph keeps old text",
          target == ["new-a", "old-b", "new-c"], target)
    check("held names the old paragraph and the local rewrite",
          len(held) == 1
          and held[0]["paragraph"] == "old-b"
          and held[0]["local"] == "new-b"
          and held[0]["comments"] == [{"comment_id": "c1", "content": "note"}],
          held)
    check("held comments are a copy, not the caller's list",
          held[0]["comments"] is not marks[1])


def test_uneven_replace_holds_block() -> None:
    canonical = ["a", "commented", "c"]
    local = ["a", "one", "two", "c"]  # commented → two paras
    marks = {1: [{"comment_id": "c1"}]}
    target, held = _hold_commented(canonical, local, marks)
    check("uneven replace keeps the whole commented canonical span",
          target == ["a", "commented", "c"], target)
    check("held local is the joined replacement span",
          len(held) == 1 and held[0]["local"] == "one\n\ntwo", held)


def test_delete_touching_comment_holds() -> None:
    canonical = ["keep", "commented", "gone"]
    local = ["keep"]
    marks = {1: [{"comment_id": "c1"}]}
    target, held = _hold_commented(canonical, local, marks)
    check("delete that touches a commented paragraph keeps the span",
          target == ["keep", "commented", "gone"], target)
    check("held records an empty local rewrite for a pure delete",
          len(held) == 1 and held[0]["local"] == "", held)


def test_insertions_go_through() -> None:
    canonical = ["a", "b"]
    local = ["a", "inserted", "b"]
    marks = {0: [{"comment_id": "c0"}], 1: [{"comment_id": "c1"}]}
    target, held = _hold_commented(canonical, local, marks)
    check("insertions touch no existing anchor and land",
          target == local and held == [], (target, held))


def test_equal_span_with_marks_untouched() -> None:
    canonical = ["same", "also"]
    local = ["same", "also"]
    marks = {0: [{"comment_id": "c0"}]}
    target, held = _hold_commented(canonical, local, marks)
    check("unchanged text with open comments is not held",
          target == local and held == [], (target, held))


if __name__ == "__main__":
    test_no_marks_passthrough()
    test_one_for_one_replace()
    test_uneven_replace_holds_block()
    test_delete_touching_comment_holds()
    test_insertions_go_through()
    test_equal_span_with_marks_untouched()
    print("ok")
