"""`forms_page.warm_all` starts the background reader for every manuscript
that still has open (`written`) forms, not only the page server's own.

Without this pin a regression could warm nothing (or the wrong books): the
author opens another manuscript and waits on a cold Doc read. Pure over a
temp workspace with `warm` stubbed — no Google, no threads.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from unittest import mock

os.environ["AUTHORLM_CONFIG"] = "/nonexistent/authorlm-test/config.toml"
os.environ["AUTHORLM_ENV"] = "/nonexistent/authorlm-test/.env"
os.environ["AUTHORLM_CLIENT"] = "none"
os.environ["AUTHORLM_STYLE_DEFAULTS"] = "/nonexistent/authorlm-test/style-defaults.toml"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm import api, forms_page  # noqa: E402
from authorlm.db import ko_fields  # noqa: E402


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def _book(root: Path, db, name: str) -> dict:
    ms = root / "books" / name
    ms.mkdir(parents=True)
    (ms / "ch.md").write_text("# Chapter\n\nProse.\n")
    return api.register_manuscript(db, name, str(ms))


def _thread(db, manuscript: dict, *, state: str,
            file: str = "ch.md", suffix: str = "1") -> None:
    row = ko_fields("dt")
    row.update(
        manuscript_id=manuscript["id"], origin_type="external",
        origin_id=f"warm:{manuscript['name']}:{suffix}", file=file,
        anchor_quote=None, proposed_old="Prose.", proposed_new="Prose now.",
        note="n", state=state, our_reply_ids="[]",
        last_author_reply_id=None, scope_kind="file", scope_ref=file,
        metadata=json.dumps({"kind": "replace", "anchor_paragraph": 2}))
    db.insert("doc_threads", row)


def test_warm_all_selects_written_and_survives_a_failure() -> None:
    root = Path(tempfile.mkdtemp(prefix="authorlm-warm-all-"))
    try:
        ws = root / "workspace"
        db = api.open_db(str(ws), create=True)
        alpha = _book(root, db, "alpha")
        bravo = _book(root, db, "bravo")
        charlie = _book(root, db, "charlie")
        _thread(db, alpha, state="written", suffix="a1")
        _thread(db, alpha, state="written", suffix="a2")  # same book twice
        _thread(db, bravo, state="resolved", suffix="b1")
        _thread(db, charlie, state="written", suffix="c1")
        db.conn.close()

        warmed: list[str] = []

        def fake_warm(_workspace, name):
            warmed.append(name)
            if name == "charlie":
                raise RuntimeError("session boom")

        with mock.patch.object(forms_page, "warm", side_effect=fake_warm):
            started = forms_page.warm_all(str(ws))

        check("only manuscripts with written threads are candidates",
              warmed == ["alpha", "charlie"], warmed)
        check("duplicate written threads still warm the book once",
              warmed.count("alpha") == 1, warmed)
        check("resolved-only manuscripts stay cold",
              "bravo" not in warmed, warmed)
        check("a failed warm is omitted from the returned list",
              started == ["alpha"], started)
        check("names are ordered by manuscript name",
              warmed == sorted(warmed), warmed)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_warm_all_empty_when_nothing_is_open() -> None:
    root = Path(tempfile.mkdtemp(prefix="authorlm-warm-all-empty-"))
    try:
        ws = root / "workspace"
        db = api.open_db(str(ws), create=True)
        book = _book(root, db, "solo")
        _thread(db, book, state="accepted")
        db.conn.close()
        with mock.patch.object(forms_page, "warm") as warm:
            started = forms_page.warm_all(str(ws))
        check("no written threads → nothing started",
              started == [] and warm.call_count == 0, started)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main() -> None:
    for test in (
        test_warm_all_selects_written_and_survives_a_failure,
        test_warm_all_empty_when_nothing_is_open,
    ):
        print(f"{test.__name__}:")
        test()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
