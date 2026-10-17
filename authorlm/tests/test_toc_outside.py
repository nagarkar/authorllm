"""toc.toml `outside`: Doc tabs that are not part of the book.

Papers and side pieces are mirrored to the Doc but never enter the
reading order, the full-book export, or the TOC <-> tab-order sync.
Run: python3 tests/test_toc_outside.py
"""
from __future__ import annotations

import os
os.environ["AUTHORLM_CONFIG"] = "/nonexistent/authorlm-outside/config.toml"
os.environ["AUTHORLM_ENV"] = "/nonexistent/authorlm-outside/.env"
os.environ["AUTHORLM_CLIENT"] = "none"

import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from authorlm import gdocs, structure

TOC = '''# Table of contents
outside = ["papers/", "side.md"]

[[chapter]]
file = "a.md"

[[chapter]]
file = "b.md"
parent = "a.md"
matter = "back"
'''


class OutsideTests(unittest.TestCase):

    def files(self) -> dict[str, str]:
        return {"toc.toml": TOC, "a.md": "# A\n", "b.md": "# B\n",
                "side.md": "# Side\n", "papers/p0.md": "# P0\n",
                "stray.md": "# Stray\n"}

    def test_reading_order_leaves_outside_files_out(self) -> None:
        order, unlisted = structure.reading_order(self.files())
        self.assertEqual(order, ["a.md", "b.md", "stray.md"])
        self.assertEqual(unlisted, ["stray.md"])

    def test_naming_an_outside_file_still_builds_it(self) -> None:
        self.assertEqual(structure.select_chapters(self.files(), ["papers/p0.md"]),
                         ["papers/p0.md"])
        self.assertEqual(structure.select_chapters(self.files(), ["a"]),
                         ["a.md", "b.md"])

    def test_outside_entries(self) -> None:
        outside = structure.toc_outside(TOC)
        self.assertTrue(structure.is_outside("papers/agency.md", outside))
        self.assertTrue(structure.is_outside("side.md", outside))
        self.assertFalse(structure.is_outside("papers.md", outside))
        self.assertEqual(structure.toc_outside("not = [toml"), [])

    def test_doc_reorder_keeps_outside_list_and_excludes_its_tabs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "toc.toml").write_text(TOC, encoding="utf-8")
            doc_pairs = [("b.md", None), ("a.md", None), ("side.md", None),
                         ("papers/p0.md", None)]
            local_tree = structure.parse_toc_tree(TOC)
            gdocs.rewrite_toc_from_doc({"path": tmp}, doc_pairs, local_tree)
            text = (Path(tmp) / "toc.toml").read_text(encoding="utf-8")
        self.assertEqual(structure.toc_outside(text), ["papers/", "side.md"])
        self.assertEqual([n for n, _ in structure.parse_toc_tree(text)],
                         ["b.md", "a.md"])
        self.assertEqual(structure.toc_attrs(text)["b.md"], {"matter": "back"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
