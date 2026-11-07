"""Filter-settle warning helpers: uncovered units, pushed_from recovery,
falsified prefix, and the dual-active-filter warning.

These are the loud-but-never-blocking checks around sequential filter
settlement. Wrong answers either invent a triage verdict on unmark
(`_pushed_from`), hide that later windows were drafted against a dead
prefix (`_falsified_prefix`), claim a run is complete when units remain
(`_uncovered_units`), or stay silent when two filters share a file
(`_other_active_filters`). Push/status e2e hits some of these through
verbs; these pins cover the pure predicates.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.api import (
    FILTER_ORIGIN,
    _falsified_prefix,
    _other_active_filters,
    _pushed_from,
    _uncovered_units,
)
from authorlm.db import Database, ko_fields


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def _db() -> Database:
    root = Path(tempfile.mkdtemp(prefix="authorlm-filter-settle-"))
    return Database(root / "authorlm.db")


def _run(db: Database, mid: str, rel: str, *, name: str = "tone",
         klass: str = "sequential", status: str = "active",
         cursor: int = 0, unit_count: int = 4) -> dict:
    row = ko_fields("fr")
    row.update(
        manuscript_id=mid, file=rel, unit_count=unit_count, cursor=cursor,
        status=status, **{"filter": name, "class": klass})
    db.insert("filter_runs", row)
    return row


def _thread(db: Database, mid: str, run: dict, n: int, state: str,
            created: str = "2026-01-01T00:00:00") -> dict:
    rel = run["file"]
    row = ko_fields("dt")
    row.update(
        created_at=created, manuscript_id=mid, origin_type=FILTER_ORIGIN,
        origin_id=f"{run['id']}:{rel}:{n}", file=rel,
        proposed_old="old", proposed_new="new", note="n",
        state=state, our_reply_ids="[]", scope_kind="file", scope_ref=rel,
        metadata=json.dumps({"anchor_paragraph": n}))
    db.insert("doc_threads", row)
    return row


# ------------------------------------------------------------------ uncovered

def test_uncovered_units() -> None:
    check("cursor at end → no uncovered range",
          _uncovered_units({"cursor": 3, "unit_count": 3}) == [])
    check("cursor past end → no uncovered range",
          _uncovered_units({"cursor": 5, "unit_count": 3}) == [])
    check("partial run reports (cursor+1, unit_count)",
          _uncovered_units({"cursor": 1, "unit_count": 4}) == [(2, 4)])
    check("untouched run reports the full span",
          _uncovered_units({"cursor": 0, "unit_count": 2}) == [(1, 2)])


# ------------------------------------------------------------------ pushed_from

def test_pushed_from_defaults_and_reads() -> None:
    check("missing metadata recovers as accepted",
          _pushed_from({}) == "accepted")
    check("empty metadata recovers as accepted",
          _pushed_from({"metadata": "{}"}) == "accepted")
    check("recorded proposed is preserved",
          _pushed_from({"metadata": json.dumps({"pushed_from": "proposed"})})
          == "proposed")
    check("explicit accepted is preserved",
          _pushed_from({"metadata": json.dumps({"pushed_from": "accepted"})})
          == "accepted")


# ------------------------------------------------------------------ falsified

def test_falsified_prefix_sequential() -> None:
    db = _db()
    mid, rel = "m-1", "essay.md"
    run = _run(db, mid, rel)
    _thread(db, mid, run, 1, "accepted")
    _thread(db, mid, run, 2, "rejected")
    _thread(db, mid, run, 3, "proposed")
    _thread(db, mid, run, 4, "accepted")
    got = _falsified_prefix(db, mid, run)
    check("rejecting unit 2 names later live units",
          got == [{"n": 2, "downstream": [3, 4]}], got)


def test_falsified_prefix_skips_dead_downstream() -> None:
    db = _db()
    mid, rel = "m-1", "essay.md"
    run = _run(db, mid, rel)
    _thread(db, mid, run, 1, "declined")
    _thread(db, mid, run, 2, "withdrawn")
    _thread(db, mid, run, 3, "rejected")
    _thread(db, mid, run, 4, "proposed")
    got = _falsified_prefix(db, mid, run)
    # Unit 1's only later non-dead is 4 (2 withdrawn, 3 rejected).
    # Unit 3's later live is 4.
    check("withdrawn/rejected later units are not downstream",
          got == [{"n": 1, "downstream": [4]},
                  {"n": 3, "downstream": [4]}], got)


def test_falsified_prefix_global_is_empty() -> None:
    db = _db()
    mid, rel = "m-1", "essay.md"
    run = _run(db, mid, rel, klass="global")
    _thread(db, mid, run, 1, "rejected")
    _thread(db, mid, run, 2, "proposed")
    check("global class never falsifies a prefix",
          _falsified_prefix(db, mid, run) == [])


def test_falsified_prefix_reject_with_no_later() -> None:
    db = _db()
    mid, rel = "m-1", "essay.md"
    run = _run(db, mid, rel)
    _thread(db, mid, run, 1, "accepted")
    _thread(db, mid, run, 2, "rejected")
    check("a terminal reject has no falsified tail",
          _falsified_prefix(db, mid, run) == [])


# ------------------------------------------------------------------ dual active

def test_other_active_filters_warns() -> None:
    db = _db()
    mid, rel = "m-1", "essay.md"
    _run(db, mid, rel, name="tone")
    check("alone → no warning",
          _other_active_filters(db, mid, rel, "tone") is None)
    _run(db, mid, rel, name="pace")
    got = _other_active_filters(db, mid, rel, "tone")
    check("names the other active filter",
          got is not None and got.startswith("pace already has"), got)
    check("settled runs do not warn",
          _other_active_filters(db, mid, "other.md", "tone") is None)
    _run(db, mid, rel, name="clarity", status="settled")
    again = _other_active_filters(db, mid, rel, "tone")
    check("settled peer is omitted from the warning",
          again is not None and "clarity" not in again, again)


def main() -> None:
    for t in (test_uncovered_units, test_pushed_from_defaults_and_reads,
              test_falsified_prefix_sequential,
              test_falsified_prefix_skips_dead_downstream,
              test_falsified_prefix_global_is_empty,
              test_falsified_prefix_reject_with_no_later,
              test_other_active_filters_warns):
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
