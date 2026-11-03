"""locate_block must match a card by section id, not spoken text alone.

Two paragraphs that say the same words under different [Voice:] tags are
different cards (section_id includes voice). Editing the later card must
rewrite that later block — never the earlier same-text hit
(2026-11-01 audiobook page writeback).

Run: python3 tests/test_locate_block_voice.py
"""
from __future__ import annotations

import os
for _name in tuple(os.environ):
    if _name.endswith("_API_KEY"):
        os.environ.pop(_name)
os.environ["AUTHORLM_CONFIG"] = "/nonexistent/authorlm-locate-voice/config.toml"
os.environ["AUTHORLM_ENV"] = "/nonexistent/authorlm-locate-voice/.env"
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
| guest | Guest | guest-id | eleven_multilingual_v2 | 0.55 | 0.7 | 1.05 | |
| credits | Credits | credits-id | eleven_multilingual_v2 | 0.4 | 0.6 | 0.9 | |
'''


class LocateBlockVoiceTests(unittest.TestCase):

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="authorlm-locate-voice-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "_audio").mkdir()
        (self.root / "_audio/audiobook.toml").write_text(CONFIG, encoding="utf-8")
        (self.root / "_audio/cast.md").write_text(CAST, encoding="utf-8")
        self.md = self.root / "ch.md"
        self.md.write_text(
            "# Title\n\nHello world.\n\n[Voice: guest]\n\nHello world.\n\n"
            "[Voice: body]\n\nSomething else.\n", encoding="utf-8")
        self.config = audio.load_config(self.root)
        self.cast, _ = audio.load_cast(self.root, self.config)
        source = audio.audio_source("ch.md", self.md.read_text(encoding="utf-8"))
        sections, _w, _t = audio.chapter_sections(
            "ch.md", source, self.config, self.cast, "body", [])
        self.speech = [s for s in sections
                       if s.get("type") == "speech" and s.get("kind") != "heading"]
        self.hellos = [s for s in self.speech if s["text"].strip() == "Hello world."]
        self.assertEqual(len(self.hellos), 2)
        self.assertNotEqual(self.hellos[0]["id"], self.hellos[1]["id"])
        self.assertEqual(self.hellos[0]["cast"], "body")
        self.assertEqual(self.hellos[1]["cast"], "guest")

    def test_spoken_only_hits_first(self) -> None:
        """Legacy path (no want_id) still returns the first spoken match."""
        guest = self.hellos[1]
        found = audio.locate_block(
            self.root, ["ch.md"], self.config, guest["text"], guest["kind"])
        self.assertEqual(found["line"], 3)

    def test_want_id_selects_guest_block(self) -> None:
        guest = self.hellos[1]
        found = audio.locate_block(
            self.root, ["ch.md"], self.config, guest["text"], guest["kind"],
            want_id=guest["id"], cast=self.cast,
            default_keys={"ch.md": "body"}, rules=[])
        self.assertEqual(found["line"], 7)
        self.assertEqual(found["markdown"], "Hello world.")

    def test_replace_guest_leaves_body(self) -> None:
        guest = self.hellos[1]
        found = audio.locate_block(
            self.root, ["ch.md"], self.config, guest["text"], guest["kind"],
            want_id=guest["id"], cast=self.cast,
            default_keys={"ch.md": "body"}, rules=[])
        audio.replace_block(self.root, found, "Hello world — guest fix.")
        text = self.md.read_text(encoding="utf-8")
        self.assertIn("# Title\n\nHello world.\n\n[Voice: guest]\n\n"
                      "Hello world — guest fix.\n", text)
        self.assertNotIn("Hello world — guest fix.\n\n[Voice: guest]", text)


if __name__ == "__main__":
    unittest.main()
