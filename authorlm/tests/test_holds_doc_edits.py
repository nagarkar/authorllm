"""`gdocs._holds_doc_edits` is the push-guard door for a hand-settled conflict.

Wrong True silently overwrites Doc edits that the local file never took;
wrong False blocks a push that already holds every Doc change (adjacent
local edits make git report "conflict" even though nothing of the Doc's
is lost). Blank lines must not count.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.gdocs import _holds_doc_edits


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_identical_and_blank_noise() -> None:
    text = "a\n\nb\n"
    check("identical local/doc/base holds",
          _holds_doc_edits(text, text, text) is True)
    # Doc only changed blank-line density; nothing non-blank moved.
    check("blank-only Doc drift is ignored",
          _holds_doc_edits("a\n\nb\n", "a\nb\n", "a\n\n\nb\n") is True)


def test_holds_every_doc_edit() -> None:
    base = "x\n\na\n\nb\n\nc\n\nd\n"
    doc = "x\n\nA\n\nn1\n\nb\n\nn2\n\nc\n\nd\n"
    settled = "x\n\nA\n\nn1\n\nB\n\nn2\n\nc\n\nd\n"
    check("local that took every Doc add/replace holds (adjacent local edit ok)",
          _holds_doc_edits(settled, base, doc) is True)


def test_lost_doc_add_or_removal() -> None:
    base = "x\n\na\n\nb\n\nc\n\nd\n"
    doc = "x\n\nA\n\nn1\n\nb\n\nn2\n\nc\n\nd\n"
    lost_add = "x\n\nA\n\nB\n\nn2\n\nc\n\nd\n"
    check("dropping a Doc-added non-blank line refuses",
          _holds_doc_edits(lost_add, base, doc) is False)

    # Doc removed `a`; local still has it.
    doc_rm = "x\n\nb\n\nc\n"
    local_kept = "x\n\na\n\nb\n\nc\n"
    check("keeping a Doc-removed non-blank line refuses",
          _holds_doc_edits(local_kept, base, doc_rm) is False)


def test_replace_must_land() -> None:
    base = "one\n\ntwo\n"
    doc = "ONE\n\ntwo\n"
    check("Doc replace present locally holds",
          _holds_doc_edits("ONE\n\ntwo\n", base, doc) is True)
    check("Doc replace missing locally refuses",
          _holds_doc_edits("one\n\ntwo\n", base, doc) is False)
    check("wrong substitute for Doc replace refuses",
          _holds_doc_edits("uno\n\ntwo\n", base, doc) is False)


if __name__ == "__main__":
    test_identical_and_blank_noise()
    test_holds_every_doc_edit()
    test_lost_doc_add_or_removal()
    test_replace_must_land()
    print("ok")
