"""`api._merge_local_edits` keeps local file edits across a door resolve.

Wrong overlap detection either drops the author's local edit (writing
the tab's text over the file) or lets a conflict write a bad merge
(it-459f2c04ef77). Lens/door e2e covers one keep and one stop path;
these pins cover the pure three-way edges.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.api import _merge_local_edits


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_non_overlapping_keeps_both() -> None:
    base = "a\nb\nc"
    got = _merge_local_edits(base, "A\nb\nc", "a\nb\nC")
    check("local and resolve edits on different lines both land",
          got == "A\nb\nC", got)


def test_overlapping_hunk_refuses() -> None:
    base = "a\nb\nc"
    check("same-line conflict returns None",
          _merge_local_edits(base, "X\nb\nc", "Y\nb\nc") is None)


def test_identical_dual_edit_kept_once() -> None:
    base = "a\nb\nc"
    got = _merge_local_edits(base, "A\nb\nc", "A\nb\nc")
    check("the same edit on both sides is kept once",
          got == "A\nb\nc", got)


def test_one_side_only() -> None:
    base = "a\nb\nc"
    check("local-only edit is kept",
          _merge_local_edits(base, "A\nb\nc", base) == "A\nb\nc")
    check("resolve-only edit is kept",
          _merge_local_edits(base, base, "a\nb\nC") == "a\nb\nC")
    check("no edits returns the base",
          _merge_local_edits(base, base, base) == base)


def test_multi_hunk_reverse_apply() -> None:
    # Edits are applied high-index first so earlier offsets stay valid.
    base = "a\nb\nc\nd"
    local = "A\nb\nc\nd"
    resolved = "a\nb\nc\nD"
    got = _merge_local_edits(base, local, resolved)
    check("two non-overlapping hunks both survive reverse apply",
          got == "A\nb\nc\nD", got)


def main() -> None:
    for t in (test_non_overlapping_keeps_both, test_overlapping_hunk_refuses,
              test_identical_dual_edit_kept_once, test_one_side_only,
              test_multi_hunk_reverse_apply):
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
