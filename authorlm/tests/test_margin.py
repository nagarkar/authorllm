"""`gdocs.Margin` / `_open_comments` — one comments read per push verb.

A push used to call Drive comments.list three times (rebuild check,
anchor hold, table check). Margin keeps the first successful list, and
also keeps a failed read so each asker still takes its own safe side
instead of silently retrying into a different answer mid-verb.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm import gdocs


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


class _FakeDrive:
    """Minimal Drive comments.list stand-in for `fetch_open_comments`."""

    def __init__(self, pages: list[dict] | None = None,
                 err: Exception | None = None):
        self.pages = pages or []
        self.err = err
        self.calls = 0

    def comments(self):
        return self

    def list(self, **kwargs):
        return self

    def execute(self):
        self.calls += 1
        if self.err is not None:
            raise self.err
        # One page, then stop — matches fetch_open_comments' paging loop.
        if self.calls == 1:
            return {"comments": self.pages, "nextPageToken": None}
        return {"comments": [], "nextPageToken": None}


def test_margin_reads_once() -> None:
    comments = [
        {"id": "c1", "content": "note", "createdTime": "2026-01-01T00:00:00Z",
         "quotedFileContent": {"value": "quote"}, "resolved": False,
         "author": {"displayName": "A"}, "replies": []},
        {"id": "c0", "content": "earlier",
         "createdTime": "2025-01-01T00:00:00Z",
         "quotedFileContent": {"value": "q"}, "resolved": False,
         "author": {"displayName": "A"}, "replies": []},
    ]
    fake = _FakeDrive(pages=comments)
    margin = gdocs.Margin(fake, "doc-1")
    first = margin.comments()
    second = margin.comments()
    check("comments are sorted by createdTime",
          [c["id"] for c in first] == ["c0", "c1"],
          [c["id"] for c in first])
    check("the second ask reuses the first list object",
          first is second)
    check("Drive was hit only once", fake.calls == 1, fake.calls)


def test_margin_keeps_a_failed_read() -> None:
    fake = _FakeDrive(err=RuntimeError("Drive down"))
    margin = gdocs.Margin(fake, "doc-1")
    try:
        margin.comments()
    except RuntimeError as err:
        check("first ask surfaces the failure", "Drive down" in str(err))
    else:
        raise AssertionError("expected RuntimeError")
    try:
        margin.comments()
    except RuntimeError as err:
        check("second ask re-raises the same failure, no retry",
              "Drive down" in str(err) and fake.calls == 1, fake.calls)
    else:
        raise AssertionError("expected RuntimeError on retry")


def test_open_comments_routes_by_doc_id() -> None:
    real = gdocs.fetch_open_comments
    hits: list[str] = []

    def stub(service, doc_id):
        hits.append(doc_id)
        return [{"id": f"from-{doc_id}"}]

    try:
        gdocs.fetch_open_comments = stub  # type: ignore[assignment]
        margin = gdocs.Margin("svc", "doc-A")
        # Prime the cache for A.
        got_a = gdocs._open_comments("svc", "doc-A", margin)
        check("matching margin.doc_id uses Margin",
              got_a == [{"id": "from-doc-A"}] and hits == ["doc-A"], hits)
        # Different id must not reuse A's cache.
        got_b = gdocs._open_comments("svc", "doc-B", margin)
        check("mismatched doc_id bypasses Margin and fetches",
              got_b == [{"id": "from-doc-B"}] and hits == ["doc-A", "doc-B"],
              hits)
        # None margin always fetches.
        got_direct = gdocs._open_comments("svc", "doc-A", None)
        check("margin=None always calls fetch_open_comments",
              got_direct == [{"id": "from-doc-A"}]
              and hits == ["doc-A", "doc-B", "doc-A"], hits)
        # Matching margin still does not re-fetch A.
        again = gdocs._open_comments("svc", "doc-A", margin)
        check("cached Margin still does not re-fetch",
              again is got_a and hits == ["doc-A", "doc-B", "doc-A"], hits)
    finally:
        gdocs.fetch_open_comments = real


def main() -> None:
    for t in (test_margin_reads_once, test_margin_keeps_a_failed_read,
              test_open_comments_routes_by_doc_id):
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
