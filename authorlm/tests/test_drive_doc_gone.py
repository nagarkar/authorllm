"""`drive_upload._doc_gone` decides whether a Drive update failure means
"the author deleted the file — recreate" vs a real error that must stop
the export.

After the #169 split this helper lives only in `drive_upload` (export no
longer owns Drive). Wrong status handling either swallows a 403/500 and
silently creates a duplicate Doc, or treats a hand-deleted file as fatal
and leaves the author stuck. Nothing in the suite called it directly
before this file; `test_api` / `test_export_upload` only exercise it
through a full fake Drive round-trip.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.drive_upload import _doc_gone


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_only_http_404_counts_as_gone() -> None:
    gone = Exception("not found")
    gone.resp = type("R", (), {"status": 404})()
    check("404 is gone", _doc_gone(gone) is True)

    forbidden = Exception("forbidden")
    forbidden.resp = type("R", (), {"status": 403})()
    check("403 is not gone", _doc_gone(forbidden) is False)

    server = Exception("server")
    server.resp = type("R", (), {"status": 500})()
    check("500 is not gone", _doc_gone(server) is False)


def test_missing_resp_or_status_is_not_gone() -> None:
    check("bare Exception is not gone", _doc_gone(Exception("x")) is False)
    bare = Exception("x")
    bare.resp = object()  # no status attribute
    check("resp without status is not gone", _doc_gone(bare) is False)
    null_status = Exception("x")
    null_status.resp = type("R", (), {"status": None})()
    check("resp.status None is not gone", _doc_gone(null_status) is False)


def main() -> None:
    tests = [test_only_http_404_counts_as_gone,
             test_missing_resp_or_status_is_not_gone]
    for t in tests:
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
