"""`gdocs._tab_is_empty` chooses full rewrite vs surgical Doc push.

An empty tab must rewrite so the first push lands content. Unknown or
failed reads must return False so comment-bearing tabs stay surgical and
anchors are not orphaned. Missing mapping / service / tab id is never
"empty".
"""

from __future__ import annotations

import json
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


REL = "essay.md"
MANUSCRIPT = {"id": "m-1", "name": "Book", "path": "/tmp/book"}
BRIDGE = gdocs.DocBridge("gdocs", Path("/tmp/book"), "Book",
                        toc_sync=True, rich_manifest=True)


class FakeDB:
    """`_mapping` only reads `one(... )['metadata']`."""

    def __init__(self, meta: dict) -> None:
        self.meta = meta

    def one(self, sql: str, params: tuple = ()) -> dict:
        return {"metadata": json.dumps(self.meta)}


def _linked(tab_id: str = "tab-1") -> dict:
    return {"gdocs": {"_master_id": "doc-1", REL: {"tab_id": tab_id}}}


def test_missing_prereqs_are_not_empty() -> None:
    db = FakeDB(_linked())
    check("no docs_service → False",
          gdocs._tab_is_empty(db, MANUSCRIPT, BRIDGE, REL, None) is False)
    check("no master id → False",
          gdocs._tab_is_empty(FakeDB({"gdocs": {REL: {"tab_id": "t"}}}),
                              MANUSCRIPT, BRIDGE, REL, object()) is False)
    check("no tab_id → False",
          gdocs._tab_is_empty(FakeDB({"gdocs": {"_master_id": "doc-1",
                                                REL: {}}}),
                              MANUSCRIPT, BRIDGE, REL, object()) is False)
    check("unmapped relpath → False",
          gdocs._tab_is_empty(FakeDB({"gdocs": {"_master_id": "doc-1"}}),
                              MANUSCRIPT, BRIDGE, REL, object()) is False)


def test_paragraph_presence() -> None:
    real = gdocs._doc_paragraphs
    calls: list[tuple] = []

    def fake(docs_service, master_id: str, tab_id: str):
        calls.append((docs_service, master_id, tab_id))
        return []

    try:
        gdocs._doc_paragraphs = fake  # type: ignore[assignment]
        check("empty paragraph list → True",
              gdocs._tab_is_empty(FakeDB(_linked()), MANUSCRIPT, BRIDGE, REL,
                                  "svc") is True)
        check("read uses master and tab id",
              calls == [("svc", "doc-1", "tab-1")], repr(calls))

        gdocs._doc_paragraphs = (  # type: ignore[assignment]
            lambda *_a, **_k: ["a paragraph"])
        check("non-empty paragraph list → False",
              gdocs._tab_is_empty(FakeDB(_linked()), MANUSCRIPT, BRIDGE, REL,
                                  "svc") is False)
    finally:
        gdocs._doc_paragraphs = real


def test_failed_read_is_not_empty() -> None:
    real = gdocs._doc_paragraphs

    def boom(*_a, **_k):
        raise RuntimeError("Docs API down")

    try:
        gdocs._doc_paragraphs = boom  # type: ignore[assignment]
        check("exception during read → False (surgical path)",
              gdocs._tab_is_empty(FakeDB(_linked()), MANUSCRIPT, BRIDGE, REL,
                                  "svc") is False)
    finally:
        gdocs._doc_paragraphs = real


def main() -> None:
    tests = [test_missing_prereqs_are_not_empty, test_paragraph_presence,
             test_failed_read_is_not_empty]
    for t in tests:
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
