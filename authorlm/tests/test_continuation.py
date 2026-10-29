"""Continuation files: export setting `continuation_level`.

A file whose first heading is at the setting's level or deeper carries on
the essay before it: no page break in the PDF, and one audiobook chapter
instead of two. Unset, every file starts anew (the old behaviour).
Run: python3 tests/test_continuation.py
"""
from __future__ import annotations

import os
for _name in tuple(os.environ):
    if _name.endswith("_API_KEY"):
        os.environ.pop(_name)
os.environ["AUTHORLM_CONFIG"] = "/nonexistent/authorlm-continuation/config.toml"
os.environ["AUTHORLM_ENV"] = "/nonexistent/authorlm-continuation/.env"
os.environ["AUTHORLM_CLIENT"] = "none"

import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from authorlm import audio, export, structure

CONFIG = '''schema = 1
[text]
voice = "body"
[headings]
voice = "body"
[credits]
voice = "body"
opening = "{title}."
closing = "End."
'''
CAST = '''| Key | Voice | Voice ID | Model | Stability | Similarity | Speed | Note |
| --- | --- | --- | --- | --- | --- | --- | --- |
| body | Body | body-id | eleven_multilingual_v2 | 0.6 | 0.8 | 1.0 | |
'''


class ContinuationTests(unittest.TestCase):

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="authorlm-continuation-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "_audio").mkdir()
        (self.root / "_audio/audiobook.toml").write_text(CONFIG, encoding="utf-8")
        (self.root / "_audio/cast.md").write_text(CAST, encoding="utf-8")
        (self.root / "recap.md").write_text(
            "# Recap\n\nOpening.\n\n## Part One\n\nFirst part.\n", encoding="utf-8")
        (self.root / "recap-two.md").write_text(
            "## Part Two\n\nSecond part.\n", encoding="utf-8")
        (self.root / "next.md").write_text(
            "# Next\n\nA new essay.\n", encoding="utf-8")
        (self.root / "toc.toml").write_text(
            '[[chapter]]\nfile = "recap.md"\n\n'
            '[[chapter]]\nfile = "recap-two.md"\n\n'
            '[[chapter]]\nfile = "next.md"\n', encoding="utf-8")
        self.manuscript = {
            "path": str(self.root), "name": "Book", "title": "Book",
            "subtitle": "", "author": "Author", "narrator": "Reader",
            "publisher": "Publisher", "copyright_year": 2026,
            "copyright_owner": "Author", "language": "en",
        }

    def set_level(self, value: str) -> None:
        export.set_setting(self.manuscript, "continuation_level", value)

    def test_rule(self) -> None:
        self.assertTrue(structure.is_continuation("x.md", "## A\n", 2))
        self.assertTrue(structure.is_continuation("x.md", "> quote\n\n### A\n", 2))
        self.assertFalse(structure.is_continuation("x.md", "# A\n\n## B\n", 2))
        self.assertFalse(structure.is_continuation("x.md", "## A\n", None))
        self.assertFalse(structure.is_continuation("title.md", "## A\n", 2))
        self.assertFalse(structure.is_continuation("x.md", "No heading.\n", 2))

    def test_setting_validates(self) -> None:
        with self.assertRaises(ValueError):
            self.set_level("seven")
        self.set_level("2")
        self.assertEqual(export.continuation_level(self.manuscript), 2)
        self.set_level("")
        self.assertIsNone(export.continuation_level(self.manuscript))

    def roles(self) -> dict[str, bool]:
        text, order, _ = export.publish_markdown(
            self.manuscript, "stripped", semantic_sections=True, fmt="pdf")
        import re
        marked = {}
        for attrs, body in re.findall(r"::: \{([^}]*)\}\n(.*?)\n:::", text, re.S):
            name = {"# Recap": "recap.md", "## Part Two": "recap-two.md",
                    "# Next": "next.md"}[body.split("\n", 1)[0]]
            marked[name] = "authorlm-continuation" in attrs
        return marked

    def test_export_marks_only_continuations(self) -> None:
        self.assertEqual(self.roles(), {"recap.md": False, "recap-two.md": False,
                                        "next.md": False})
        self.set_level("2")
        self.assertEqual(self.roles(), {"recap.md": False, "recap-two.md": True,
                                        "next.md": False})

    def test_audio_folds_continuation_into_previous_chapter(self) -> None:
        built = audio.build(self.manuscript)
        self.assertEqual([c["stem"] for c in built["book"]["chapters"]],
                         ["recap", "recap-two", "next"])
        self.set_level("2")
        built = audio.build(self.manuscript)
        self.assertEqual([c["stem"] for c in built["book"]["chapters"]],
                         ["recap", "next"])
        recap = built["chapters"]["recap"]
        texts = [s["text"] for s in recap["sections"] if s["type"] == "speech"]
        self.assertIn("First part.", texts)
        self.assertIn("Second part.", texts)
        self.assertLess(texts.index("First part."), texts.index("Second part."))
        ids = [s["id"] for s in recap["sections"]]
        self.assertEqual(len(ids), len(set(ids)), "section ids collide")
        self.assertNotIn("recap-two", built["chapters"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
