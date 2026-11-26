"""`changes._require_writable` is the Doc-side gate every land / draft /
revert walks before rewriting a chapter file.

A silent drift here overwrites a checked-out Doc working copy, lands on
top of unsettled forms, or ignores a tab that is ahead of the file. The
helper takes `tab_state` so Google is never reached. Hermetic.
Run: python3 tests/test_require_writable.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.changes import _require_writable  # noqa: E402


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def _raises(label: str, *, status=None, pending=None, tab_state=None,
            needle: str) -> None:
    manuscript = {"id": 1, "name": "Book"}
    with mock.patch("authorlm.gdocs.doc_status",
                    return_value={"one.md": status} if status is not None
                    else {}), \
         mock.patch("authorlm.gdocs.forms_pending",
                    return_value=pending):
        try:
            _require_writable(
                mock.Mock(), manuscript, "one.md", {}, None, tab_state)
        except ValueError as err:
            check(label, needle in str(err), err)
            return
    check(label, False, "no ValueError")


def test_checked_out_refuses() -> None:
    _raises("checked out to Docs",
            status={"checked_out": True, "tab_id": "t1"},
            tab_state=lambda _: "unchanged",
            needle="checked out to Google Docs")


def test_pending_forms_refuses() -> None:
    _raises("unsettled forms name their origin",
            status={"checked_out": False},
            pending="filter",
            tab_state=lambda _: "unchanged",
            needle="carries filter forms")


def test_doc_ahead_and_conflict_refuse() -> None:
    for state in ("doc_ahead", "conflict"):
        _raises(f"{state} needs a pull first",
                status={"checked_out": False},
                pending=None,
                tab_state=lambda _rel, s=state: s,
                needle="edits that are not in the file yet")


def test_unread_tab_refuses() -> None:
    def boom(_rel: str) -> str:
        raise RuntimeError("token expired")

    _raises("a tab that cannot be read is declined",
            status={"checked_out": False},
            pending=None,
            tab_state=boom,
            needle="could not be read")


def test_clean_states_pass() -> None:
    manuscript = {"id": 1, "name": "Book"}
    for state in ("unchanged", "local_ahead", "unlinked", None):
        with mock.patch("authorlm.gdocs.doc_status", return_value={}), \
             mock.patch("authorlm.gdocs.forms_pending", return_value=None):
            got = _require_writable(
                mock.Mock(), manuscript, "one.md", {}, None,
                (lambda _rel, s=state: s))
        check(f"{state!r} is writable", got == state, got)


def main() -> None:
    for test in (
        test_checked_out_refuses,
        test_pending_forms_refuses,
        test_doc_ahead_and_conflict_refuse,
        test_unread_tab_refuses,
        test_clean_states_pass,
    ):
        print(f"{test.__name__}:")
        test()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
