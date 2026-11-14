"""toc.toml `summary`: one hidden sentence per essay stating what it argues.

The summary is the developmental edit's standard of drift: the essay pack
(which every checker reads) carries it, and the checker reports whether the
edited essay still supports each of its claims. It never reaches the reader:
not an export, a Doc tab or audio.
Run: python3 tests/test_toc_summary.py
"""
from __future__ import annotations

import os
os.environ["AUTHORLM_CONFIG"] = "/nonexistent/authorlm-summary/config.toml"
os.environ["AUTHORLM_ENV"] = "/nonexistent/authorlm-summary/.env"
os.environ["AUTHORLM_CLIENT"] = "none"

import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from authorlm import devedit, structure  # noqa: E402

devedit.ARCHIVE_ROOT = Path(tempfile.mkdtemp(prefix="devedit-archive-"))
devedit.arc_exempt = lambda db, manuscript, file: True

TOC = '''[[chapter]]
file = "one.md"
summary = "Choice is the only door the Dharma leaves open."

[[chapter]]
file = "two.md"

[[chapter]]
file = "bad.md"
summary = 3

[[chapter]]
file = "multi.md"
summary = """first line
second line"""
'''


class TocSummary(unittest.TestCase):
    def test_summaries_are_read_per_file(self):
        got = structure.toc_summaries(TOC)
        self.assertEqual(got["one.md"],
                         "Choice is the only door the Dharma leaves open.")
        self.assertNotIn("two.md", got)
        self.assertNotIn("bad.md", got)      # invalid: not exposed
        self.assertNotIn("multi.md", got)
        self.assertEqual(structure.toc_summary(TOC, "one.md"),
                         got["one.md"])
        self.assertIsNone(structure.toc_summary(TOC, "two.md"))
        self.assertEqual(structure.toc_summaries("not = [toml"), {})

    def test_problems_name_each_bad_summary(self):
        probs = structure.toc_problems(TOC)
        self.assertEqual(len(probs), 2, probs)
        self.assertTrue(any("bad.md" in p and "string" in p for p in probs))
        self.assertTrue(any("multi.md" in p and "one line" in p for p in probs))
        self.assertTrue(any("bad.md" in p for p in
                            structure.toc_problems('[[chapter]]\nfile = "bad.md"\nsummary = ""\n')))

    def test_serializer_round_trips_quotes(self):
        attrs = {"one.md": {"summary": 'It says "no" to fate.', "matter": "main"}}
        text = structure.serialize_toc_tree([("one.md", 0)], attrs)
        self.assertEqual(tomllib.loads(text)["chapter"][0]["summary"],
                         'It says "no" to fate.')

    def test_essay_pack_carries_summary_for_the_checker(self):
        from authorlm.db import Database
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "MS"
            root.mkdir()
            (root / "toc.toml").write_text(
                '[[chapter]]\nfile = "one.md"\nsummary = "Choice is a door."\n\n'
                '[[chapter]]\nfile = "two.md"\n')
            (root / "one.md").write_text("# One\n\nChoice is a door.\n")
            (root / "two.md").write_text("# Two\n\nMore.\n")
            db = Database(Path(tmp) / "authorlm.db")
            ms = {"name": "MS", "id": "ms", "path": str(root)}
            from authorlm.db import ko_fields
            db.insert("manuscripts", {**ko_fields("ms"), "id": "ms",
                                      "name": "MS", "path": str(root)})
            folder = devedit.new_run(ms)
            devedit.confirm_coverage(folder)
            one = devedit.essay_pack(db, ms, folder, "one.md", trial=True)
            self.assertIn("## What this essay claims", one)
            self.assertIn("Choice is a door.", one)
            two = devedit.essay_pack(db, ms, folder, "two.md", trial=True)
            self.assertIn("(no summary in toc.toml for two.md", two)
            self.assertEqual(two.count("no summary in toc.toml"), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
