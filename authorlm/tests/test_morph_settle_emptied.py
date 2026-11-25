"""Morph Doc settle: an emptied green half is a decline, not a delete.

`lens resolve` and `filter resolve` (Doc road) run `restore_emptied_forms`
before `final_text_from_marked`, so `<<old>>{{}}` (or a bare `<<old>>`)
keeps the old text and records a decline. `morph resolve` documented the
same rule (CLI: empty/delete the green half = decline) but settled without
the restore: an emptied green half was kept as an accepted empty rewrite
and the paragraph vanished.

Hermetic: temporary workspace, `_SurgicalDocFake`, no network, no model.
Run: python3 tests/test_morph_settle_emptied.py
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ["AUTHORLM_CONFIG"] = "/nonexistent/authorlm-morph-settle/config.toml"
os.environ["AUTHORLM_ENV"] = "/nonexistent/authorlm-morph-settle/.env"
os.environ["AUTHORLM_CLIENT"] = "none"

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

from test_passes import (DOC_ESSAY, _ALPHA_NEW, _ALPHA_OLD,  # noqa: E402
                         _GAMMA_NEW, _GAMMA_OLD, _SurgicalDocFake)

from authorlm import api, gdocs, morph  # noqa: E402
from authorlm.cli import main as cli_main  # noqa: E402
from authorlm.db import ko_fields  # noqa: E402


def _stage_morph_replaces(db, manuscript, fake) -> None:
    mid = manuscript["id"]
    # Paragraph numbers match `passes.paragraphs_of(DOC_ESSAY)` (title is 1).
    for old, new, n in ((_ALPHA_OLD, _ALPHA_NEW, 2),
                        (_GAMMA_OLD, _GAMMA_NEW, 4)):
        row = ko_fields("dt")
        row.update(
            manuscript_id=mid, origin_type=morph.MORPH_ORIGIN,
            origin_id=f"morph:solo.md:{n}", file="solo.md",
            anchor_quote=None, proposed_old=old, proposed_new=new,
            note=f"morph-{n}", state="accepted", our_reply_ids="[]",
            last_author_reply_id=None, scope_kind="file",
            scope_ref="solo.md",
            metadata=json.dumps({
                "kind": "replace", "anchor_paragraph": n,
                "finding_kind": morph.MORPH_KIND}))
        db.insert("doc_threads", row)
    meta = gdocs._mapping(db, manuscript)
    links = meta.setdefault("gdocs", {})
    links["_master_id"] = "doc-fake"
    links["_container_tab"] = "tab-1"
    links["solo.md"] = {"tab_id": "tab-2", "checked_out": False,
                        "pushed_hash": None}
    gdocs._save_mapping(db, manuscript, meta)
    push = api.morph_push(db, manuscript, {}, "solo.md",
                          services=lambda: (fake, fake))
    assert push["written"] == 2, push


class MorphSettleEmptied(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="morph-settle-"))
        ws = self.tmp / "ws"
        ms = ws / "book"
        ms.mkdir(parents=True)
        (ms / "solo.md").write_text(DOC_ESSAY)
        with contextlib.redirect_stdout(io.StringIO()):
            cli_main(["--workspace", str(ws), "init", "--name", "book",
                      "--path", str(ms)])
        self.db = api.open_db(str(ws))
        self.manuscript = api.get_manuscript(self.db)
        with contextlib.redirect_stdout(io.StringIO()):
            api.collect(self.db, self.manuscript, {})
        self.ms = ms
        self.fake = _SurgicalDocFake([("book", ""), ("solo.md", DOC_ESSAY)])
        _stage_morph_replaces(self.db, self.manuscript, self.fake)

    def test_emptied_braces_keep_old_and_decline(self) -> None:
        self.fake.edit("tab-2", "{{" + _GAMMA_NEW + "}}", "{{}}")
        with contextlib.redirect_stdout(io.StringIO()):
            done = api.morph_resolve(
                self.db, self.manuscript, {}, "solo.md",
                services=lambda: (self.fake, self.fake))
        text = (self.ms / "solo.md").read_text(encoding="utf-8")
        self.assertEqual(done["declined"], 1, done)
        self.assertEqual(done["accepted"], 1, done)
        self.assertIn(_GAMMA_OLD, text)
        self.assertNotIn(_GAMMA_NEW, text)
        self.assertIn(_ALPHA_NEW, text)

    def test_bare_strike_keep_old_and_decline(self) -> None:
        self.fake.edit("tab-2", "{{" + _GAMMA_NEW + "}}", "")
        # Bare strike left as `<<old>>` with no braces.
        tab = self.fake.tab_text("solo.md")
        self.assertIn("<<" + _GAMMA_OLD + ">>", tab)
        self.assertNotIn("{{" + _GAMMA_NEW + "}}", tab)
        with contextlib.redirect_stdout(io.StringIO()):
            done = api.morph_resolve(
                self.db, self.manuscript, {}, "solo.md",
                services=lambda: (self.fake, self.fake))
        text = (self.ms / "solo.md").read_text(encoding="utf-8")
        self.assertEqual(done["declined"], 1, done)
        self.assertIn(_GAMMA_OLD, text)
        self.assertNotIn("<<" + _GAMMA_OLD + ">>", text)


if __name__ == "__main__":
    unittest.main()
