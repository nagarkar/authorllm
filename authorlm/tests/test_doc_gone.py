"""`export._doc_gone` decides whether a Drive/Docs failure means the
remembered file was deleted, so export may recreate it in place.

Upload and export-Doc paths both catch broad `Exception` and only
swallow a 404. Mis-classifying a 403/5xx as gone would drop the
stored id and create a duplicate; missing a real 404 would leave the
author unable to re-export after a Drive delete.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.export import _doc_gone


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def _err(status=None, with_resp=True):
    err = Exception("drive")
    if with_resp:
        err.resp = type("R", (), {"status": status})()
    return err


def test_404_is_gone() -> None:
    check("HTTP 404 means the remembered Drive file is gone",
          _doc_gone(_err(404)) is True)


def test_other_status_is_not_gone() -> None:
    for status in (400, 403, 500, None):
        check(f"HTTP {status!r} is not treated as gone",
              _doc_gone(_err(status)) is False, status)


def test_missing_resp_is_not_gone() -> None:
    check("an Exception without .resp is not gone",
          _doc_gone(Exception("plain")) is False)
    err = Exception("x")
    err.resp = type("R", (), {})()  # no .status
    check("resp object lacking status is not gone",
          _doc_gone(err) is False)


def main() -> None:
    tests = [test_404_is_gone, test_other_status_is_not_gone,
             test_missing_resp_is_not_gone]
    for t in tests:
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
