"""Document name history helpers (`docs.names_of`, `snapshot_text`,
`earlier_name`): after a rename, every reader of an old snapshot must
find the chapter under whichever name that snapshot held. The rename CUJ
in test_document_identity exercises one path through collect and push;
these checks pin the pure helpers on their own — no name, the wrong
former name, or a snapshot that already uses the new name.
"""

from __future__ import annotations

import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ["AUTHORLM_CONFIG"] = "/nonexistent/authorlm-names/config.toml"
os.environ["AUTHORLM_ENV"] = "/nonexistent/authorlm-names/.env"
os.environ["AUTHORLM_CLIENT"] = "none"
os.environ["AUTHORLM_STYLE_DEFAULTS"] = \
    "/nonexistent/authorlm-names/style-defaults.toml"

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import authorlm  # noqa: E402

assert Path(authorlm.__file__).resolve().parent == HERE.parent / "authorlm", (
    f"testing the wrong package: {authorlm.__file__}")

from authorlm import api, docs  # noqa: E402


def quiet(function, *args, **kwargs):
    with contextlib.redirect_stdout(io.StringIO()):
        return function(*args, **kwargs)


class NameHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.workspace = base / "workspace"
        self.root = base / "book"
        self.root.mkdir()
        (self.root / "old.md").write_text(
            "# Old\n\n" + "A sentence of this chapter. " * 40 + "\n",
            encoding="utf-8")
        (self.root / "toc.toml").write_text(
            '[[chapter]]\nfile = "old.md"\n', encoding="utf-8")
        self.db = api.open_db(str(self.workspace), create=True)
        api.register_manuscript(self.db, "book", str(self.root),
                                extraction=False)
        self.manuscript = api.get_manuscript(self.db)
        self.mid = self.manuscript["id"]
        quiet(api.collect, self.db, self.manuscript, {})
        docs.ensure_documents(self.db, self.manuscript)

    def tearDown(self) -> None:
        self.db.conn.close()
        self.tmp.cleanup()

    def test_names_of_is_just_the_path_when_no_document_row(self):
        self.assertEqual(docs.names_of(self.db, self.mid, "ghost.md"),
                         ["ghost.md"])

    def test_names_of_is_only_the_current_name_before_any_rename(self):
        self.assertEqual(docs.names_of(self.db, self.mid, "old.md"),
                         ["old.md"])

    def test_after_a_rename_snapshot_text_reads_the_old_name(self):
        quiet(api.rename_document, self.db, self.manuscript, {},
              "old.md", "new.md")
        self.assertEqual(docs.names_of(self.db, self.mid, "new.md"),
                         ["new.md", "old.md"])
        self.assertEqual(
            docs.snapshot_text(self.db, self.mid, {"old.md": "WAS"}, "new.md"),
            "WAS")
        self.assertIsNone(
            docs.snapshot_text(self.db, self.mid, {"other.md": "x"}, "new.md"))

    def test_earlier_name_is_the_former_name_missing_from_the_new_snapshot(self):
        quiet(api.rename_document, self.db, self.manuscript, {},
              "old.md", "new.md")
        self.assertEqual(
            docs.earlier_name(self.db, self.mid, "new.md",
                              {"old.md": "WAS"}, {"new.md": "NOW"}),
            "old.md")

    def test_earlier_name_is_none_when_the_old_snapshot_already_has_it(self):
        quiet(api.rename_document, self.db, self.manuscript, {},
              "old.md", "new.md")
        self.assertIsNone(
            docs.earlier_name(self.db, self.mid, "new.md",
                              {"new.md": "NOW"}, {"new.md": "NOW"}))

    def test_earlier_name_is_none_when_the_old_snapshot_holds_no_name(self):
        quiet(api.rename_document, self.db, self.manuscript, {},
              "old.md", "new.md")
        self.assertIsNone(
            docs.earlier_name(self.db, self.mid, "new.md",
                              {"other.md": "x"}, {"new.md": "NOW"}))

    def test_a_second_rename_keeps_every_former_name_in_order(self):
        quiet(api.rename_document, self.db, self.manuscript, {},
              "old.md", "mid.md")
        quiet(api.rename_document, self.db, self.manuscript, {},
              "mid.md", "new.md")
        self.assertEqual(docs.names_of(self.db, self.mid, "new.md"),
                         ["new.md", "mid.md", "old.md"])
        self.assertEqual(
            docs.snapshot_text(self.db, self.mid, {"old.md": "FIRST"},
                               "new.md"),
            "FIRST")
        self.assertEqual(
            docs.earlier_name(self.db, self.mid, "new.md",
                              {"mid.md": "WAS"}, {"new.md": "NOW"}),
            "mid.md")


if __name__ == "__main__":
    unittest.main()
