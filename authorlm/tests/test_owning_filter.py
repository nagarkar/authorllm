"""`api._owning_filter` names the filter on a refusal by its run id.

Dual-producer refusals on push/unmark/resolve call this helper so the
author sees `'tone'` rather than an opaque `fr-…` row id. A wrong
prefix split or a manuscript miss returns `None`, and the caller falls
back to "the filter pass" — still correct, but less useful. Push e2e
covers one happy refusal; these pins cover the lookup edges.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.api import _owning_filter
from authorlm.db import Database, ko_fields


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def _db() -> Database:
    root = Path(tempfile.mkdtemp(prefix="authorlm-owning-filter-"))
    return Database(root / "authorlm.db")


def _run(db: Database, mid: str, *, name: str = "tone") -> dict:
    row = ko_fields("fr")
    row.update(
        manuscript_id=mid, file="essay.md", unit_count=4, cursor=0,
        status="active", **{"filter": name, "class": "sequential"})
    db.insert("filter_runs", row)
    return row


def test_names_filter_from_origin_prefix() -> None:
    db = _db()
    mid = "ms-1"
    run = _run(db, mid, name="tone")
    thread = {"origin_id": f"{run['id']}:essay.md:2"}
    check("prefix before the first colon names the filter",
          _owning_filter(db, mid, thread) == "tone")


def test_missing_run_or_manuscript_returns_none() -> None:
    db = _db()
    mid = "ms-1"
    run = _run(db, mid, name="clarity")
    check("unknown run id → None",
          _owning_filter(db, mid, {"origin_id": "fr-missing:essay.md:1"})
          is None)
    check("run on another manuscript → None",
          _owning_filter(db, "ms-other",
                         {"origin_id": f"{run['id']}:essay.md:1"}) is None)
    check("empty origin_id → None",
          _owning_filter(db, mid, {"origin_id": ""}) is None)
    check("missing origin_id → None",
          _owning_filter(db, mid, {}) is None)


def test_origin_without_colon_still_looks_up() -> None:
    db = _db()
    mid = "ms-1"
    run = _run(db, mid, name="voice")
    # split(":", 1)[0] on a bare id is the whole string — rare, but the
    # helper must not crash, and a matching row still names the filter.
    check("bare run id (no colon) still resolves",
          _owning_filter(db, mid, {"origin_id": run["id"]}) == "voice")


def main() -> None:
    for t in (test_names_filter_from_origin_prefix,
              test_missing_run_or_manuscript_returns_none,
              test_origin_without_colon_still_looks_up):
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
