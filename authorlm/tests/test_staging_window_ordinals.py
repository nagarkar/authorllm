"""Multi-window filter staging must not reuse 1..k ordinals.

A filter run keeps one owner id across windows. Keying origin_id by the
reply's 1..k position made window 2 overwrite and supersede window 1's
proposals before triage. Unit-keyed ids and window-scoped supersede keep
earlier windows put.
"""

from __future__ import annotations

from pathlib import Path
import tempfile

from authorlm.db import Database, ko_fields
from authorlm import staging


def _db() -> tuple[Database, str]:
    td = Path(tempfile.mkdtemp())
    db = Database(td / "w.db")
    mid = "ms_win"
    row = ko_fields("ms")
    row.update(
        id=mid, name="t", path=str(td), author="", copyright_owner="",
        paperback_isbn="", hardcover_isbn="", trim_width="", trim_height="",
        bleed="", narrator="", publisher="", copyright_year="", language="",
        metadata="{}",
    )
    db.insert("manuscripts", row)
    return db, mid


def _open(db: Database, mid: str) -> list[dict]:
    return [dict(r) for r in db.all(
        "SELECT origin_id, state, proposed_old, proposed_new FROM doc_threads "
        "WHERE manuscript_id = ? AND state = 'proposed' ORDER BY origin_id",
        (mid,))]


def test_later_window_keeps_earlier_proposals():
    db, mid = _db()
    text = "One.\n\nTwo.\n\nThree.\n\nFour.\n\nFive.\n\nSix."
    owner = "run_abc"
    staging.stage_edits(
        db, mid, owner, "a.md", text,
        [{"n": 1, "new": "One'", "why": "w"},
         {"n": 2, "new": "Two'", "why": "w"}],
        window=(1, 3),
    )
    staging.stage_edits(
        db, mid, owner, "a.md", text,
        [{"n": 4, "new": "Four'", "why": "w"},
         {"n": 5, "new": "Five'", "why": "w"}],
        window=(4, 6),
    )
    open_rows = _open(db, mid)
    assert [r["origin_id"] for r in open_rows] == [
        f"{owner}:a.md:1", f"{owner}:a.md:2",
        f"{owner}:a.md:4", f"{owner}:a.md:5",
    ], open_rows
    assert [r["proposed_old"] for r in open_rows] == [
        "One.", "Two.", "Four.", "Five.",
    ], open_rows


def test_rerecord_same_window_supersedes_dropped_unit_only():
    db, mid = _db()
    text = "One.\n\nTwo.\n\nThree.\n\nFour."
    owner = "run_xyz"
    staging.stage_edits(
        db, mid, owner, "a.md", text,
        [{"n": 1, "new": "One'", "why": "w"},
         {"n": 2, "new": "Two'", "why": "w"}],
        window=(1, 2),
    )
    staging.stage_edits(
        db, mid, owner, "a.md", text,
        [{"n": 3, "new": "Three'", "why": "w"},
         {"n": 4, "new": "Four'", "why": "w"}],
        window=(3, 4),
    )
    staging.stage_edits(
        db, mid, owner, "a.md", text,
        [{"n": 1, "new": "One''", "why": "w"}],
        window=(1, 2),
    )
    open_rows = _open(db, mid)
    assert [r["origin_id"] for r in open_rows] == [
        f"{owner}:a.md:1", f"{owner}:a.md:3", f"{owner}:a.md:4",
    ], open_rows
    assert open_rows[0]["proposed_new"] == "One''"
    withdrawn = [dict(r) for r in db.all(
        "SELECT origin_id, state FROM doc_threads WHERE manuscript_id = ? "
        "AND state = 'withdrawn'", (mid,))]
    assert [r["origin_id"] for r in withdrawn] == [f"{owner}:a.md:2"], withdrawn


def test_lens_style_full_supersede_without_window():
    """Findings/lenses omit window and still supersede the whole batch."""
    db, mid = _db()
    text = "One.\n\nTwo.\n\nThree."
    owner = "repair@batch01"
    staging.stage_edits(
        db, mid, owner, "a.md", text,
        [{"n": 1, "new": "One'", "why": "w"},
         {"n": 2, "new": "Two'", "why": "w"}],
        origin_type="lens", verb_stem="lens",
    )
    staging.stage_edits(
        db, mid, owner, "a.md", text,
        [{"n": 3, "new": "Three'", "why": "w"}],
        origin_type="lens", verb_stem="lens",
    )
    open_rows = _open(db, mid)
    assert [r["origin_id"] for r in open_rows] == [f"{owner}:a.md:3"], open_rows
    withdrawn = list(db.all(
        "SELECT origin_id FROM doc_threads WHERE manuscript_id = ? "
        "AND state = 'withdrawn' ORDER BY origin_id", (mid,)))
    assert [r["origin_id"] for r in withdrawn] == [
        f"{owner}:a.md:1", f"{owner}:a.md:2",
    ]


if __name__ == "__main__":
    test_later_window_keeps_earlier_proposals()
    print("  [ok] later window keeps earlier proposals")
    test_rerecord_same_window_supersedes_dropped_unit_only()
    print("  [ok] re-record supersedes only within window")
    test_lens_style_full_supersede_without_window()
    print("  [ok] lens batch still full-supersedes")
    print("all ok")
