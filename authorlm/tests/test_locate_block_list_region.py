"""locate_block one-per-item must skip region tag lines when picking a list line.

A list written against [Only: audio] / [Omit: …] with no blank line (the
natural Obsidian style) keeps those tags in the raw block. Matching by
`block["line"] + i` rewrote the open tag (or the previous item) instead of
the card's list line — data loss on audiobook page edit (2026-11-06).

Run: python3 tests/test_locate_block_list_region.py
"""
from __future__ import annotations

import os
for _name in tuple(os.environ):
    if _name.endswith("_API_KEY"):
        os.environ.pop(_name)
os.environ["AUTHORLM_CONFIG"] = "/nonexistent/authorlm-locate-list/config.toml"
os.environ["AUTHORLM_ENV"] = "/nonexistent/authorlm-locate-list/.env"
os.environ["AUTHORLM_CLIENT"] = "none"

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from authorlm import audio

CONFIG = '''schema = 1
[text]
voice = "body"
lists = "one-per-item"
[headings]
voice = "essay-default"
[credits]
voice = "credits"
opening = "{title}."
closing = "End."
'''
CAST = '''| Key | Voice | Voice ID | Model | Stability | Similarity | Speed | Note |
| --- | --- | --- | --- | --- | --- | --- | --- |
| body | Body | body-id | eleven_multilingual_v2 | 0.6 | 0.8 | 0.97 | |
| credits | Credits | credits-id | eleven_multilingual_v2 | 0.4 | 0.6 | 0.9 | |
'''


class LocateBlockListRegionTests(unittest.TestCase):

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="authorlm-locate-list-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "_audio").mkdir()
        (self.root / "_audio/audiobook.toml").write_text(CONFIG, encoding="utf-8")
        (self.root / "_audio/cast.md").write_text(CAST, encoding="utf-8")
        self.md = self.root / "ch.md"
        self.md.write_text(
            "# Title\n\n"
            "[Only: audio]\n"
            "- Alpha item\n"
            "- Beta item\n"
            "[/Only]\n\n"
            "Plain para.\n",
            encoding="utf-8")
        self.config = audio.load_config(self.root)
        self.cast, _ = audio.load_cast(self.root, self.config)
        source = audio.audio_source("ch.md", self.md.read_text(encoding="utf-8"))
        sections, _w, _t = audio.chapter_sections(
            "ch.md", source, self.config, self.cast, "body", [])
        self.speech = [s for s in sections
                       if s.get("type") == "speech" and s.get("kind") != "heading"]
        self.alpha = next(s for s in self.speech if s["text"] == "Alpha item")
        self.beta = next(s for s in self.speech if s["text"] == "Beta item")

    def test_locate_skips_region_open_for_first_item(self) -> None:
        found = audio.locate_block(
            self.root, ["ch.md"], self.config, self.alpha["text"],
            self.alpha["kind"], want_id=self.alpha["id"], cast=self.cast,
            default_keys={"ch.md": "body"}, rules=[])
        self.assertEqual(found["line"], 4)
        self.assertEqual(found["markdown"], "- Alpha item")

    def test_locate_second_item_is_not_first_line(self) -> None:
        found = audio.locate_block(
            self.root, ["ch.md"], self.config, self.beta["text"],
            self.beta["kind"], want_id=self.beta["id"], cast=self.cast,
            default_keys={"ch.md": "body"}, rules=[])
        self.assertEqual(found["line"], 5)
        self.assertEqual(found["markdown"], "- Beta item")

    def test_edit_alpha_keeps_region_and_beta(self) -> None:
        found = audio.locate_block(
            self.root, ["ch.md"], self.config, self.alpha["text"],
            self.alpha["kind"], want_id=self.alpha["id"], cast=self.cast,
            default_keys={"ch.md": "body"}, rules=[])
        audio.replace_block(self.root, found, "- Alpha item fixed")
        text = self.md.read_text(encoding="utf-8")
        self.assertEqual(
            text,
            "# Title\n\n"
            "[Only: audio]\n"
            "- Alpha item fixed\n"
            "- Beta item\n"
            "[/Only]\n\n"
            "Plain para.\n")

    def test_omit_all_audio_substitute_list(self) -> None:
        """[Omit: all] then [Only: audio] against a list: two leading tags."""
        self.md.write_text(
            "# Title\n\n"
            "[Omit: all]\n"
            "[Only: audio]\n"
            "- Solo item\n"
            "[/Only]\n"
            "[/Omit]\n\n"
            "Plain para.\n",
            encoding="utf-8")
        source = audio.audio_source("ch.md", self.md.read_text(encoding="utf-8"))
        sections, _w, _t = audio.chapter_sections(
            "ch.md", source, self.config, self.cast, "body", [])
        solo = next(s for s in sections
                    if s.get("type") == "speech" and s.get("text") == "Solo item")
        found = audio.locate_block(
            self.root, ["ch.md"], self.config, solo["text"], solo["kind"],
            want_id=solo["id"], cast=self.cast,
            default_keys={"ch.md": "body"}, rules=[])
        self.assertEqual(found["markdown"], "- Solo item")
        audio.replace_block(self.root, found, "- Solo item revised")
        text = self.md.read_text(encoding="utf-8")
        self.assertIn("[Omit: all]\n[Only: audio]\n- Solo item revised\n[/Only]\n[/Omit]",
                      text)


if __name__ == "__main__":
    unittest.main()
