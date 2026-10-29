"""Issue #173: default voices are explicitly declared in audiobook.toml.

Blind regression tests written from the requirement and existing public API
tests, without reading audio.py or its diff. All manuscripts are synthetic.
Run: python3 tests/test_audio_voice_config.py
"""
from __future__ import annotations

import os
for _name in tuple(os.environ):
    if _name.endswith("_API_KEY"):
        os.environ.pop(_name)
os.environ["AUTHORLM_CONFIG"] = "/nonexistent/authorlm-voice-test/config.toml"
os.environ["AUTHORLM_ENV"] = "/nonexistent/authorlm-voice-test/.env"
os.environ["AUTHORLM_CLIENT"] = "none"

import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from authorlm import audio, paths

CONFIG = '''schema = 1
[text]
voice = "body"
[headings]
voice = "headings"
[credits]
voice = "credits"
opening = "{title}. Written by {author}."
closing = "End of {title}."
'''
CAST = '''| Key | Voice | Voice ID | Model | Stability | Similarity | Speed | Note |
| --- | --- | --- | --- | --- | --- | --- | --- |
| narrator | Narrator | narrator-id | eleven_multilingual_v2 | 0.5 | 0.75 | 1.0 | |
| body | Body | body-id | eleven_multilingual_v2 | 0.6 | 0.8 | 0.97 | |
| headings | Headings | headings-id | eleven_multilingual_v2 | 0.7 | 0.9 | 1.1 | |
| credits | Credits | credits-id | eleven_multilingual_v2 | 0.4 | 0.6 | 0.9 | |
| chitta_darshana | Chapter | chapter-id | eleven_multilingual_v2 | 0.65 | 0.85 | 0.95 | |
| guest | Guest | guest-id | eleven_multilingual_v2 | 0.55 | 0.7 | 1.05 | |
'''


class VoiceConfigTests(unittest.TestCase):
    """Each case enforces issue #173's explicit-voice contract."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="authorlm-voice-config-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / "_audio/audiobook.toml"
        self.config.parent.mkdir()
        self.config.write_text(CONFIG, encoding="utf-8")
        (self.config.parent / "cast.md").write_text(CAST, encoding="utf-8")
        (self.root / "sermons.md").write_text(
            "# Sermons\n\nDefault paragraph.\n\n[Voice: guest]\n\n"
            "Guest first.\n\nGuest second.\n\n[Voice: narrator]\n\n"
            "Narrator first.\n\nNarrator second.\n\n## Reset\n\n"
            "Reset paragraph.\n", encoding="utf-8")
        (self.root / "child.md").write_text(
            "# Child\n\nChild paragraph.\n", encoding="utf-8")
        (self.root / "toc.toml").write_text(
            '[[chapter]]\nfile = "sermons.md"\n\n'
            '[[chapter]]\nfile = "child.md"\nparent = "sermons.md"\n',
            encoding="utf-8")
        self.manuscript = {
            "path": str(self.root), "name": "Synthetic book", "title": "Book",
            "subtitle": "", "author": "Author", "narrator": "Reader",
            "publisher": "Publisher", "copyright_year": 2026,
            "copyright_owner": "Author", "language": "en",
        }

    def assert_refused(self, action, *clues: str) -> None:
        """Errors name the offending value or field so it can be repaired."""
        with self.assertRaises(audio.AudioError) as caught:
            action()
        for clue in clues:
            self.assertIn(clue.lower(), str(caught.exception).lower())

    def speech(self, built: dict, stem: str) -> dict:
        return {s["text"]: s for s in built["chapters"][stem]["sections"]
                if s["type"] == "speech"}

    def test_init_writes_explicit_roles(self) -> None:
        """#173: the seeded file explicitly declares all default roles."""
        with tempfile.TemporaryDirectory() as fresh:
            audio.init({**self.manuscript, "path": fresh})
            import tomllib
            saved = tomllib.loads((Path(fresh) / "_audio/audiobook.toml")
                                 .read_text(encoding="utf-8"))
            for section in ("text", "headings", "credits"):
                self.assertEqual(saved[section]["voice"], "narrator")

    def test_init_fills_missing_config_without_changing_cast(self) -> None:
        """#173 recovery: an author's cast survives initialization of config."""
        cast_path = self.config.parent / "cast.md"
        original_cast = b"# My chosen voices\n\n" + cast_path.read_bytes()
        cast_path.write_bytes(original_cast)
        self.config.unlink()
        audio.init(self.manuscript)
        self.assertEqual(cast_path.read_bytes(), original_cast)
        import tomllib
        saved = tomllib.loads(self.config.read_text(encoding="utf-8"))
        for section in ("text", "headings", "credits"):
            self.assertEqual(saved[section]["voice"], "narrator")

    def test_init_fills_missing_cast_without_changing_config(self) -> None:
        """#173 recovery: an author's explicit config survives cast seeding."""
        original_config = b"# Keep my declared roles and comments\n" + self.config.read_bytes()
        self.config.write_bytes(original_config)
        cast_path = self.config.parent / "cast.md"
        cast_path.unlink()
        audio.init(self.manuscript)
        self.assertEqual(self.config.read_bytes(), original_config)
        rows, _warnings = audio.parse_cast(cast_path.read_text(encoding="utf-8"))
        self.assertEqual([row["key"] for row in rows], ["narrator"])

    def test_init_refuses_when_both_files_exist_without_changing_either(self) -> None:
        """#173 recovery: initialization cannot overwrite two existing files."""
        cast_path = self.config.parent / "cast.md"
        original_config = self.config.read_bytes()
        original_cast = cast_path.read_bytes()
        self.assert_refused(lambda: audio.init(self.manuscript), "already exist")
        self.assertEqual(self.config.read_bytes(), original_config)
        self.assertEqual(cast_path.read_bytes(), original_cast)

    def test_missing_config_is_actionable(self) -> None:
        """#173: absent config must not silently choose narrator."""
        self.config.unlink()
        self.assert_refused(lambda: audio.load_config(self.root), "audiobook.toml")

    def test_required_voice_fields_are_not_filled_in(self) -> None:
        """#173: required roles cannot be omitted, blank, or non-strings."""
        for section, role in (("text", "body"), ("headings", "headings"),
                              ("credits", "credits")):
            for replacement in ("", 'voice = ""\n', 'voice = 7\n'):
                with self.subTest(section=section, replacement=replacement):
                    self.config.write_text(CONFIG.replace(
                        f'[{section}]\nvoice = "{role}"\n',
                        f'[{section}]\n{replacement}'), encoding="utf-8")
                    self.assert_refused(lambda: audio.load_config(self.root), section, "voice")

    def test_chapter_declarations_require_unique_file_and_voice(self) -> None:
        """#173: malformed and duplicate chapter declarations are refused."""
        cases = [
            ('[[chapter]]\nvoice = "body"\n', "file"),
            ('[[chapter]]\nfile = "sermons.md"\n', "voice"),
            ('[[chapter]]\nfile = 1\nvoice = "body"\n', "file"),
            ('[[chapter]]\nfile = ""\nvoice = "body"\n', "file"),
            ('[[chapter]]\nfile = "sermons.md"\nvoice = ""\n', "voice"),
            ('[[chapter]]\nfile = "sermons.md"\nvoice = 1\n', "voice"),
            ('[chapter]\nfile = "sermons.md"\nvoice = "body"\n', "chapter"),
            ('[[chapter]]\nfile = "sermons.md"\nvoice = "body"\n'
             '[[chapter]]\nfile = "sermons.md"\nvoice = "guest"\n', "sermons.md"),
        ]
        for extra, clue in cases:
            with self.subTest(declaration=extra):
                self.config.write_text(CONFIG + extra, encoding="utf-8")
                self.assert_refused(lambda: audio.load_config(self.root), clue)

    def test_unknown_roles_and_files_are_named(self) -> None:
        """#173: build names unknown cast roles and non-TOC files."""
        cases = [
            (CONFIG.replace('voice = "body"', 'voice = "missing_body"'), "missing_body"),
            (CONFIG.replace('voice = "headings"', 'voice = "missing_headings"'), "missing_headings"),
            (CONFIG.replace('voice = "credits"', 'voice = "missing_credits"'), "missing_credits"),
            (CONFIG + '[[chapter]]\nfile = "sermons.md"\nvoice = "missing_chapter"\n', "missing_chapter"),
            (CONFIG + '[[chapter]]\nfile = "absent.md"\nvoice = "body"\n', "absent.md"),
        ]
        for config, clue in cases:
            with self.subTest(clue=clue):
                self.config.write_text(config, encoding="utf-8")
                self.assert_refused(lambda: audio.build(self.manuscript), clue)

    def test_legacy_toc_voice_requires_migration(self) -> None:
        """#173: TOC voices require migration instead of silent ignoring."""
        toc = self.root / "toc.toml"
        toc.write_text(toc.read_text().replace('file = "sermons.md"',
                       'file = "sermons.md"\nvoice = "guest"'), encoding="utf-8")
        self.assert_refused(lambda: audio.build(self.manuscript),
                            "toc.toml", "voice", "audiobook.toml")

    def test_roles_persist_and_reset_at_headings(self) -> None:
        """#173: plain tags persist until another tag or a heading."""
        built = audio.build(self.manuscript)
        speech = self.speech(built, "sermons")
        self.assertEqual({text: s["cast"] for text, s in speech.items()}, {
            "Sermons": "headings", "Default paragraph.": "body",
            "Guest first.": "guest", "Guest second.": "guest",
            "Narrator first.": "narrator", "Narrator second.": "narrator",
            "Reset": "headings", "Reset paragraph.": "body",
        })
        for stem in ("_opening-credits", "_closing-credits"):
            self.assertEqual({s["cast"] for s in self.speech(built, stem).values()}, {"credits"})
        self.assertEqual(built["book"]["schema"], 1)
        self.assertEqual(built["chapters"]["sermons"]["voiceDefault"], "body")
        for section in speech.values():
            self.assertIsInstance(section["voiceId"], str)
            self.assertTrue(section["voiceId"])
            self.assertEqual(section["model"], "eleven_multilingual_v2")
            for key in ("stability", "similarity", "speed"):
                self.assertIsInstance(section[key], (float, int))
        self.assertEqual(tuple(speech["Default paragraph."][key]
                               for key in ("voiceId", "stability", "similarity", "speed")),
                         ("body-id", 0.6, 0.8, 0.97))

    def test_chapter_override_is_exact_file_without_inheritance(self) -> None:
        """#173: chapter overrides affect only the named file's body."""
        self.config.write_text(CONFIG + '[[chapter]]\nfile = "sermons.md"\n'
                               'voice = "chitta_darshana"\n', encoding="utf-8")
        built = audio.build(self.manuscript)
        speech = self.speech(built, "sermons")
        self.assertEqual(speech["Default paragraph."]["cast"], "chitta_darshana")
        self.assertEqual(speech["Reset paragraph."]["cast"], "chitta_darshana")
        self.assertEqual(speech["Reset"]["cast"], "headings")
        self.assertEqual(speech["Guest second."]["cast"], "guest")
        self.assertEqual(self.speech(built, "child")["Child paragraph."]["cast"], "body")
        self.assertEqual(built["chapters"]["sermons"]["voiceDefault"], "chitta_darshana")
        self.assertEqual(built["chapters"]["child"]["voiceDefault"], "body")


if __name__ == "__main__":
    assert not any(name.endswith("_API_KEY") for name in os.environ)
    assert not paths.config_path().exists()
    assert paths.load_env() == []
    unittest.main(verbosity=2)
