"""`doc add` registers the new file in toc.toml when the manuscript has
one, so a Doc push of it never leaves toc.toml and the Doc's tab order
disagreeing. Run: python3 tests/test_doc_add_toc.py
"""
from __future__ import annotations

import os
os.environ["AUTHORLM_CONFIG"] = "/nonexistent/authorlm-docadd/config.toml"
os.environ["AUTHORLM_ENV"] = "/nonexistent/authorlm-docadd/.env"
os.environ["AUTHORLM_CLIENT"] = "none"

import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from authorlm import docs, structure

TOC = '''# Table of contents

[[chapter]]
file = "a.md"

[[chapter]]
file = "b.md"
'''


def order(root: Path) -> list[str]:
    return [n for n, _ in
            structure.parse_toc_tree((root / "toc.toml").read_text())]


class DocAddTocTests(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.ms = {"path": str(self.root)}

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_about_the_book_hint(self) -> None:
        self.assertIn("profile", docs.about_the_book_hint("synopsis.md"))
        self.assertIn("profile", docs.about_the_book_hint("perspective-sheet"))
        self.assertIsNone(docs.about_the_book_hint("chapter3.md"))

    def test_add_appends_to_toc(self) -> None:
        (self.root / "toc.toml").write_text(TOC)
        docs.add_doc(self.ms, "c")
        self.assertEqual(order(self.root), ["a.md", "b.md", "c.md"])
        self.assertTrue(TOC.rstrip() in (self.root / "toc.toml").read_text())

    def test_add_after_places_it(self) -> None:
        (self.root / "toc.toml").write_text(TOC)
        docs.add_doc(self.ms, "c", after="a.md")
        docs.add_doc(self.ms, "z", after="start")
        self.assertEqual(order(self.root), ["z.md", "a.md", "c.md", "b.md"])

    def test_add_after_parent_skips_its_descendants(self) -> None:
        """A sibling after a chapter with children must follow the whole
        subtree. Wedging between the parent and its first child keeps the
        children's written parent= lines but tree_to_parents reparents
        them under the new file (Doc tab sync then steals the children)."""
        nested = '''# Table of contents

[[chapter]]
file = "ch1.md"

[[chapter]]
file = "ch3.md"

[[chapter]]
file = "ch3-a.md"
parent = "ch3.md"

[[chapter]]
file = "ch3-a-i.md"
parent = "ch3-a.md"

[[chapter]]
file = "ch3-b.md"
parent = "ch3.md"

[[chapter]]
file = "ch4.md"
'''
        (self.root / "toc.toml").write_text(nested)
        docs.add_doc(self.ms, "epilogue", after="ch3.md")
        text = (self.root / "toc.toml").read_text()
        self.assertEqual(
            order(self.root),
            ["ch1.md", "ch3.md", "ch3-a.md", "ch3-a-i.md", "ch3-b.md",
             "epilogue.md", "ch4.md"])
        parents = dict(structure.tree_to_parents(
            structure.parse_toc_tree(text)))
        self.assertEqual(parents["ch3-a.md"], "ch3.md")
        self.assertEqual(parents["ch3-a-i.md"], "ch3-a.md")
        self.assertEqual(parents["ch3-b.md"], "ch3.md")
        self.assertIsNone(parents["epilogue.md"])
        # After a mid-subtree child: land after that child's own
        # descendants, still under the same parent, before the next
        # sibling of the anchor.
        docs.add_doc(self.ms, "ch3-a-ii", after="ch3-a.md")
        text = (self.root / "toc.toml").read_text()
        self.assertEqual(
            order(self.root),
            ["ch1.md", "ch3.md", "ch3-a.md", "ch3-a-i.md", "ch3-a-ii.md",
             "ch3-b.md", "epilogue.md", "ch4.md"])
        parents = dict(structure.tree_to_parents(
            structure.parse_toc_tree(text)))
        self.assertEqual(parents["ch3-a-ii.md"], "ch3.md")
        self.assertEqual(parents["ch3-a-i.md"], "ch3-a.md")
        self.assertEqual(parents["ch3-b.md"], "ch3.md")

    def test_unknown_anchor_writes_nothing(self) -> None:
        (self.root / "toc.toml").write_text(TOC)
        with self.assertRaises(LookupError):
            docs.add_doc(self.ms, "c", after="nope.md")
        self.assertEqual((self.root / "toc.toml").read_text(), TOC)
        self.assertFalse((self.root / "c.md").exists())

    def test_no_toc_creates_none(self) -> None:
        docs.add_doc(self.ms, "c")
        self.assertFalse((self.root / "toc.toml").exists())


if __name__ == "__main__":
    unittest.main()
