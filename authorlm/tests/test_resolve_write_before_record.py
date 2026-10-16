"""Settle must write final bytes before flipping threads off `written`.

Critique resolve already did write-then-record. Filter (via
`staging.resolve_local`), lens, morph, directives, and outside-caller
resolve recorded first; a crash left markers on disk with `cleaned`
rows. `filter unmark` then restored OLD and dropped accepted greens
with no re-apply path.
"""
from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from authorlm import api, passes, staging
from authorlm.cli import main as cli_main
from authorlm.db import ko_fields


def _init_essay(text: str, rel: str = "essay.md"):
    root = Path(tempfile.mkdtemp(prefix="resolve-order-"))
    ws = root / "ws"
    ms = ws / "manuscript"
    ms.mkdir(parents=True)
    (ms / rel).write_text(text, encoding="utf-8")
    with contextlib.redirect_stdout(io.StringIO()):
        cli_main(["--workspace", str(ws), "init", "--name", "book",
                  "--path", str(ms)])
    db = api.open_db(str(ws))
    manuscript = api.get_manuscript(db)
    return root, db, manuscript, ms, rel


def _written_replace(db, mid: str, rel: str, old: str, new: str,
                     origin: str = "filter"):
    row = ko_fields("dt")
    row.update(
        manuscript_id=mid, file=rel, state="written", origin_type=origin,
        proposed_old=old, proposed_new=new, origin_id="t1",
        anchor_quote=old, note="", scope_kind=None, scope_ref=None,
        our_reply_ids="[]", last_author_reply_id=None,
        metadata=json.dumps({"pushed_from": "accepted"}),
    )
    cols = {r[1] for r in db.all("PRAGMA table_info(doc_threads)")}
    db.insert("doc_threads", {k: v for k, v in row.items() if k in cols})
    return dict(db.one("SELECT * FROM doc_threads ORDER BY rowid DESC LIMIT 1"))


class WriteBeforeRecordTests(unittest.TestCase):
    def tearDown(self):
        if hasattr(self, "root"):
            import shutil
            shutil.rmtree(self.root, ignore_errors=True)

    def test_resolve_local_does_not_record(self):
        old, new = "The old claim stands.", "The claim stands firmly here."
        self.root, db, manuscript, ms, rel = _init_essay(
            f"# T\n\n<<{old}>>{{{{{new}}}}}\n")
        mid = manuscript["id"]
        _written_replace(db, mid, rel, old, new)
        path = ms / rel
        final, forms = staging.resolve_local(
            db, mid, rel, path, origin_type="filter",
            evidence_type="filter_edit")
        self.assertIn(new, final)
        self.assertEqual(forms[0]["new"], new)
        row = db.one("SELECT state FROM doc_threads")
        self.assertEqual(row["state"], "written",
                         "resolve_local must not flip state before the write")

    def test_filter_finalize_records_only_after_write(self):
        old, new = "The old claim stands.", "The claim stands firmly here."
        self.root, db, manuscript, ms, rel = _init_essay(
            f"# T\n\n<<{old}>>{{{{{new}}}}}\n\nNext.\n")
        mid = manuscript["id"]
        _written_replace(db, mid, rel, old, new)
        path = ms / rel
        order: list[str] = []

        real_write = api._write_resolved_text
        real_record = passes.record_resolution

        def write_probe(p, root, final):
            order.append("write")
            self.assertEqual(
                db.one("SELECT state FROM doc_threads")["state"], "written")
            return real_write(p, root, final)

        def record_probe(*args, **kwargs):
            order.append("record")
            self.assertNotIn("<<", path.read_text(encoding="utf-8"))
            self.assertIn(new, path.read_text(encoding="utf-8"))
            return real_record(*args, **kwargs)

        run = {
            "id": "fr-test", "status": "paused", "file": rel,
            "unit_count": 1, "mode": "local",
        }
        # Minimal filter_runs row so finalize's update does not fail hard
        # if the table is touched — use a fake via mock of db.update path.
        with mock.patch.object(api, "_write_resolved_text", write_probe), \
             mock.patch.object(passes, "record_resolution", record_probe), \
             mock.patch.object(api, "_run_threads",
                               return_value=[{"state": "cleaned"}]), \
             mock.patch.object(api, "_uncovered_units", return_value=[]), \
             mock.patch.object(api, "_falsified_prefix", return_value=None), \
             mock.patch.object(api, "_latest_version_id", return_value=None), \
             mock.patch.object(api, "_run_mode", return_value="local"), \
             mock.patch.object(api, "collect", return_value={}), \
             mock.patch("authorlm.summaries.summarizer_llm") as llm_mod, \
             mock.patch.object(passes, "settle_learnings", return_value=None):
            llm_mod.return_value.enabled = False
            # filter_runs update — ignore missing row
            orig_update = db.update

            def update_safe(table, *args, **kwargs):
                if table == "filter_runs":
                    return None
                return orig_update(table, *args, **kwargs)

            with mock.patch.object(db, "update", side_effect=update_safe):
                api._filter_finalize(db, manuscript, {}, run, rel, path,
                                     warnings=[])
        self.assertEqual(order, ["write", "record"])
        self.assertEqual(db.one("SELECT state FROM doc_threads")["state"],
                         "cleaned")
        self.assertEqual(path.read_text(encoding="utf-8").strip(),
                         f"# T\n\n{new}\n\nNext.")

    def test_unmark_refuses_cleaned_with_markers(self):
        old, new = "The old claim stands.", "The claim stands firmly here."
        marked = f"# T\n\n<<{old}>>{{{{{new}}}}}\n"
        self.root, db, manuscript, ms, rel = _init_essay("# T\n\nplain\n")
        mid = manuscript["id"]
        row = _written_replace(db, mid, rel, old, new)
        (ms / rel).write_text(marked, encoding="utf-8")
        # Simulate the old crash: record without write
        final, forms = passes.final_text_from_marked(
            marked, written=[row], kinds=("replace",))
        passes.record_resolution(db, mid, rel, forms, origin_type="filter",
                                 evidence_type="filter_edit",
                                 final_text=final)
        self.assertEqual(db.one("SELECT state FROM doc_threads")["state"],
                         "cleaned")
        with self.assertRaises(ValueError) as ctx:
            api.filter_unmark(db, manuscript, rel)
        self.assertIn("cleaned", str(ctx.exception))
        self.assertIn("<<", (ms / rel).read_text(encoding="utf-8"))
        self.assertIn(new, str(ctx.exception) + new)  # still recoverable info
        # Markers preserved — author can still recover by hand
        self.assertIn(old, (ms / rel).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
