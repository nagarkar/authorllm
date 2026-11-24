"""`forms_page.summary` is every page's header badge (#489): open forms
and how many already carry a ruling, plus the same per tab.

A wrong open/ruled count misleads the author on every page that polls it;
counting a non-producer thread or a resolved one would either hide real
work or invent a badge. Hermetic — temp SQLite, no Google, no model.
Run: python3 tests/test_forms_summary.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("AUTHORLM_CONFIG",
                      "/nonexistent/authorlm-forms-summary/config.toml")
os.environ.setdefault("AUTHORLM_ENV",
                      "/nonexistent/authorlm-forms-summary/.env")
os.environ.setdefault("AUTHORLM_CLIENT", "none")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm import api, forms_page  # noqa: E402
from authorlm.db import Database, ko_fields  # noqa: E402
from authorlm.forms_page import RULING_KEY  # noqa: E402


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def _manuscript(db: Database) -> dict:
    row = ko_fields("ms")
    row.update(name="book", path="/tmp/forms-summary-book",
               metadata="{}")
    mid = db.insert("manuscripts", row)
    return dict(db.one("SELECT * FROM manuscripts WHERE id = ?", (mid,)))


def _thread(db: Database, manuscript: dict, *, tid: str, file: str,
            origin_type: str = api.EXTERNAL_ORIGIN, state: str = "written",
            ruling: dict | None = None) -> None:
    meta: dict = {}
    if ruling is not None:
        meta[RULING_KEY] = ruling
    row = ko_fields("dt")
    row.update(
        id=tid, manuscript_id=manuscript["id"], origin_type=origin_type,
        origin_id=f"{origin_type}:{file}:{tid}", file=file,
        proposed_old="old", proposed_new="new", note="n", state=state,
        our_reply_ids="[]", metadata=json.dumps(meta))
    db.insert("doc_threads", row)


def test_summary_empty_manuscript() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "ws.sqlite")
        ms = _manuscript(db)
        got = forms_page.summary(db, ms)
    check("no threads → zero open and ruled",
          got == {"open": 0, "ruled": 0, "tabs": {}}, got)


def test_summary_counts_open_and_ruled_per_tab() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "ws.sqlite")
        ms = _manuscript(db)
        _thread(db, ms, tid="a1", file="one.md")
        _thread(db, ms, tid="a2", file="one.md",
                ruling={"verdict": "accept", "text": "", "seen": "x",
                        "at": "t"})
        _thread(db, ms, tid="b1", file="two.md",
                origin_type=api.LENS_ORIGIN)
        _thread(db, ms, tid="gone", file="one.md", state="resolved",
                ruling={"verdict": "accept", "text": "", "seen": "x",
                        "at": "t"})
        _thread(db, ms, tid="crit", file="one.md", origin_type="critique")
        got = forms_page.summary(db, ms)
    check("open is every producer written thread across tabs",
          got["open"] == 3, got)
    check("ruled counts only open threads that carry a page ruling",
          got["ruled"] == 1, got)
    check("per-tab open/ruled for one.md",
          got["tabs"]["one.md"] == {"open": 2, "ruled": 1}, got["tabs"])
    check("per-tab open/ruled for two.md",
          got["tabs"]["two.md"] == {"open": 1, "ruled": 0}, got["tabs"])
    check("resolved and non-producer threads stay out of the badge",
          "crit" not in str(got) and got["open"] == 3, got)


def test_other_forms_counts_non_producers_only() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "ws.sqlite")
        ms = _manuscript(db)
        _thread(db, ms, tid="ext", file="solo.md",
                origin_type=api.EXTERNAL_ORIGIN)
        _thread(db, ms, tid="fil", file="solo.md",
                origin_type=api.FILTER_ORIGIN)
        _thread(db, ms, tid="c1", file="solo.md", origin_type="critique")
        _thread(db, ms, tid="c2", file="solo.md", origin_type="critique")
        _thread(db, ms, tid="m1", file="solo.md", origin_type="margin")
        _thread(db, ms, tid="other-file", file="other.md",
                origin_type="critique")
        _thread(db, ms, tid="done", file="solo.md", origin_type="critique",
                state="resolved")
        got = forms_page._other_forms(db, ms, "solo.md")
        empty = forms_page._other_forms(db, ms, "missing.md")
    check("producers are excluded; others grouped by origin_type",
          got == {"critique": 2, "margin": 1}, got)
    check("another file's others are not mixed in",
          "other.md" not in got and got.get("critique") == 2, got)
    check("a tab with no others → empty dict", empty == {}, empty)


def main() -> None:
    for test in (
        test_summary_empty_manuscript,
        test_summary_counts_open_and_ruled_per_tab,
        test_other_forms_counts_non_producers_only,
    ):
        print(f"{test.__name__}:")
        test()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
