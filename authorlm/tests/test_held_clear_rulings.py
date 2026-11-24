"""`forms_page._held_rulings` / `_clear_rulings` keep a Conflicts
settlement in manuscript metadata until the file lands (or the author
clears it).

A lost held ruling after a refresh makes every card look undecided; a
clear that wipes another file's rulings drops a settled conflict. Pure
metadata pins — temp SQLite, no Google, no model.
Run: python3 tests/test_held_clear_rulings.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("AUTHORLM_CONFIG",
                      "/nonexistent/authorlm-held-clear/config.toml")
os.environ.setdefault("AUTHORLM_ENV",
                      "/nonexistent/authorlm-held-clear/.env")
os.environ.setdefault("AUTHORLM_CLIENT", "none")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm import forms_page, gdocs  # noqa: E402
from authorlm.db import Database, ko_fields  # noqa: E402
from authorlm.forms_page import CONFLICT_KEY  # noqa: E402


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def _manuscript(db: Database) -> dict:
    row = ko_fields("ms")
    row.update(name="book", path="/tmp/held-clear-book", metadata="{}")
    mid = db.insert("manuscripts", row)
    return dict(db.one("SELECT * FROM manuscripts WHERE id = ?", (mid,)))


def test_held_rulings_empty_when_nothing_settled() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "ws.sqlite")
        ms = _manuscript(db)
        got = forms_page._held_rulings(db, ms, "solo.md")
    check("no conflict_rulings → empty dict", got == {}, got)


def test_held_rulings_returns_one_file_only() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "ws.sqlite")
        ms = _manuscript(db)
        meta = gdocs._mapping(db, ms)
        meta[CONFLICT_KEY] = {
            "solo.md": {"r1": {"verdict": "mine", "at": "t", "text": None}},
            "other.md": {"r2": {"verdict": "doc", "at": "t", "text": None}},
        }
        gdocs._save_mapping(db, ms, meta)
        solo = forms_page._held_rulings(db, ms, "solo.md")
        other = forms_page._held_rulings(db, ms, "other.md")
        missing = forms_page._held_rulings(db, ms, "gone.md")
    check("solo's card is returned",
          solo == {"r1": {"verdict": "mine", "at": "t", "text": None}}, solo)
    check("other file's rulings are separate",
          other == {"r2": {"verdict": "doc", "at": "t", "text": None}}, other)
    check("unknown file → empty", missing == {}, missing)


def test_clear_rulings_drops_one_file_leaves_the_rest() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "ws.sqlite")
        ms = _manuscript(db)
        meta = gdocs._mapping(db, ms)
        meta[CONFLICT_KEY] = {
            "solo.md": {"r1": {"verdict": "mine", "at": "t", "text": None}},
            "keep.md": {"r2": {"verdict": "both", "at": "t", "text": None}},
        }
        gdocs._save_mapping(db, ms, meta)
        forms_page._clear_rulings(db, ms, "solo.md")
        after = gdocs._read_metadata(db, ms).get(CONFLICT_KEY) or {}
        check("solo cleared", "solo.md" not in after, after)
        check("keep.md still held",
              after.get("keep.md", {}).get("r2", {}).get("verdict") == "both",
              after)
        forms_page._clear_rulings(db, ms, "missing.md")
        still = gdocs._read_metadata(db, ms).get(CONFLICT_KEY) or {}
        check("clear of a missing file is a no-op",
              still == after, still)


def main() -> None:
    for test in (
        test_held_rulings_empty_when_nothing_settled,
        test_held_rulings_returns_one_file_only,
        test_clear_rulings_drops_one_file_leaves_the_rest,
    ):
        print(f"{test.__name__}:")
        test()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
