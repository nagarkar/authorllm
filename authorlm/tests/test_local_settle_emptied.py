"""Local filter settle: an emptied green half is a decline, not a delete.

On the Doc road, `filter resolve` runs `restore_emptied_forms` before
`final_text_from_marked`, so `<<old>>{{}}` (or a bare `<<old>>`) keeps the
old text and records a decline. The local road (`filter resolve --pause`,
then edit the file, then `filter resolve`) went through
`staging.resolve_local` without that restore: an emptied green half was
kept as an accepted empty rewrite and the paragraph vanished; a bare
strike was written into the essay as literal markers.

Hermetic: temporary DB and file, no network, no model.
Run: python3 tests/test_local_settle_emptied.py
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ["AUTHORLM_CONFIG"] = "/nonexistent/authorlm-local-settle/config.toml"
os.environ["AUTHORLM_ENV"] = "/nonexistent/authorlm-local-settle/.env"
os.environ["AUTHORLM_CLIENT"] = "none"

import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from authorlm.db import Database, ko_fields  # noqa: E402
from authorlm import staging  # noqa: E402

OLD = "The Field is the only door."
NEW = "Choice is the only door."


def _written_thread(db: Database, mid: str, file: str) -> None:
    row = ko_fields("dt")
    row.update(
        manuscript_id=mid, origin_type="filter", origin_id=f"run:{file}:1",
        file=file, anchor_quote=None, proposed_old=OLD, proposed_new=NEW,
        note="why", state="written", our_reply_ids="[]",
        last_author_reply_id=None, scope_kind="file", scope_ref=file,
        metadata=json.dumps({"kind": "replace", "anchor_paragraph": 1}))
    db.insert("doc_threads", row)


class LocalSettleEmptied(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="local-settle-"))
        self.db = Database(self.tmp / "authorlm.db")
        self.mid = "ms-local"
        self.db.insert("manuscripts", {
            **ko_fields("ms"), "id": self.mid, "name": "local",
            "path": str(self.tmp)})
        self.file = "ch.md"
        self.path = self.tmp / self.file
        _written_thread(self.db, self.mid, self.file)

    def test_emptied_braces_keep_old_and_decline(self) -> None:
        self.path.write_text(f"<<{OLD}>>{{{{}}}}\n\nNext stays.\n",
                             encoding="utf-8")
        final, _forms, _diffs = staging.resolve_local(
            self.db, self.mid, self.file, self.path)
        state = self.db.one("SELECT state FROM doc_threads WHERE "
                            "manuscript_id = ?", (self.mid,))["state"]
        self.assertIn(OLD, final)
        self.assertNotIn("<<", final)
        self.assertIn("Next stays.", final)
        self.assertEqual(state, "declined")

    def test_bare_strike_keeps_old_and_decline(self) -> None:
        self.path.write_text(f"<<{OLD}>>\n\nNext stays.\n", encoding="utf-8")
        final, _forms, _diffs = staging.resolve_local(
            self.db, self.mid, self.file, self.path)
        state = self.db.one("SELECT state FROM doc_threads WHERE "
                            "manuscript_id = ?", (self.mid,))["state"]
        self.assertIn(OLD, final)
        self.assertNotIn("<<", final)
        self.assertEqual(state, "declined")

    def test_whitespace_green_keeps_old_and_decline(self) -> None:
        self.path.write_text(f"<<{OLD}>>{{{{   }}}}\n\nNext stays.\n",
                             encoding="utf-8")
        final, _forms, _diffs = staging.resolve_local(
            self.db, self.mid, self.file, self.path)
        state = self.db.one("SELECT state FROM doc_threads WHERE "
                            "manuscript_id = ?", (self.mid,))["state"]
        self.assertIn(OLD, final)
        self.assertEqual(state, "declined")


if __name__ == "__main__":
    unittest.main(verbosity=2)
