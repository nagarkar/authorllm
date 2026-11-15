"""`changes._check_items` is the all-or-none gate on every `book land`
batch: each item must name one place, carry a note, and leave a `new`
that can be found again to put back. Integration tests cover land's
happy path; these pins hit the helper's own refusal edges without a
workspace.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.changes import _check_items


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def _raises(label: str, changes, text: str, needle: str) -> None:
    try:
        _check_items(changes, text)
    except ValueError as err:
        check(label, needle in str(err), err)
        return
    check(label, False, "no ValueError")


def test_batch_shape_refusals() -> None:
    _raises("not a list", "nope", "text", "JSON list")
    _raises("empty list", [], "text", "at least one")
    _raises("item missing keys", [{"old": "a"}], "a",
            "needs old, new and note")


def test_item_content_refusals() -> None:
    _raises("empty note",
            [{"old": "alpha", "new": "beta", "note": "  "}],
            "alpha here", "the note is empty")
    _raises("empty old",
            [{"old": "", "new": "beta", "note": "why"}],
            "beta", "old is empty")
    _raises("empty new",
            [{"old": "alpha beta", "new": "  ", "note": "cut"}],
            "alpha beta here", "new is empty")
    _raises("old equals new",
            [{"old": "same", "new": "same", "note": "noop"}],
            "same here", "old and new are the same")


def test_place_and_put_back_refusals() -> None:
    _raises("old missing from the file",
            [{"old": "ghost", "new": "here", "note": "why"}],
            "nothing matches", "not in the file")
    _raises("old stands twice",
            [{"old": "alpha", "new": "beta", "note": "why"}],
            "alpha and alpha again", "stand 2 times")
    _raises("overlapping spans",
            [{"old": "alpha beta", "new": "ALPHA", "note": "one"},
             {"old": "beta gamma", "new": "BETA", "note": "two"}],
            "alpha beta gamma", "overlapping")


def test_a_clean_batch_returns_items_and_text() -> None:
    items, text = _check_items(
        [{"old": "alpha", "new": "beta", "note": " fix "}],
        "alpha alone")
    check("note is stripped", items == [
        {"old": "alpha", "new": "beta", "note": "fix"}], items)
    check("text is rewritten once", text == "beta alone", text)


def main() -> None:
    for test in (
        test_batch_shape_refusals,
        test_item_content_refusals,
        test_place_and_put_back_refusals,
        test_a_clean_batch_returns_items_and_text,
    ):
        print(f"{test.__name__}:")
        test()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
