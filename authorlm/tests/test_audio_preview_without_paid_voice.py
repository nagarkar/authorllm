"""Issue #300, 2026-10-26: Mac say preview must not require an ElevenLabs voice.

Owner requirement: fresh audio init with explicit narrator roles and a blank
Voice ID must build for free preview. Paid generation must reject every missing
selected Voice ID before sending even the first valid paragraph to ElevenLabs.
Blind tests: only existing public API tests and export fixtures were consulted.
Run: python3 tests/test_audio_preview_without_paid_voice.py
"""
from __future__ import annotations

import os
for environment_name in tuple(os.environ):
    if environment_name.endswith("_API_KEY"):
        os.environ.pop(environment_name)
os.environ["AUTHORLM_CONFIG"] = "/nonexistent/authorlm-preview-test/config.toml"
os.environ["AUTHORLM_ENV"] = "/nonexistent/authorlm-preview-test/.env"
os.environ["AUTHORLM_CLIENT"] = "none"

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from authorlm import audio, generation, paths


def render_preview(root: Path, speech: dict, voice: str, rate: int) -> Path:
    """Stand in for Mac say at the external renderer boundary."""
    output = generation.preview_path(root, speech["id"])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(speech["text"].encode())
    return output


class PreviewWithoutPaidVoiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="authorlm-free-preview-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.manuscript = {
            "path": str(self.root), "name": "Synthetic preview book",
            "title": "Book", "subtitle": "", "author": "Author",
            "narrator": "Reader", "publisher": "Publisher",
            "copyright_year": 2026, "copyright_owner": "Author", "language": "en",
        }
        (self.root / "chapter.md").write_text(
            "# Chapter\n\nFirst paragraph.\n\nSecond paragraph.\n", encoding="utf-8")
        (self.root / "toc.toml").write_text(
            '[[chapter]]\nfile = "chapter.md"\n', encoding="utf-8")

    def write_export(self, voice_ids: list[str]) -> list[dict]:
        """Hand-authored schema-1 export isolates generation from build's gate."""
        audio.init(self.manuscript)
        export_root = self.root / "_audio"
        (export_root / "chapters").mkdir(parents=True)
        sections = [{
            "type": "speech", "id": str(index + 1) * 32,
            "kind": "paragraph", "text": f"Paragraph number {index + 1}.",
            "cast": "narrator", "voiceId": voice_id, "voiceName": "Reader",
            "model": "eleven_multilingual_v2", "stability": 0.5,
            "similarity": 0.75, "speed": 1.0, "pronunciations": [],
            "source": {"line": 3 + index * 2},
        } for index, voice_id in enumerate(voice_ids)]
        chapter = {"schema": 1, "file": "chapter.md", "stem": "chapter",
                   "title": "Chapter", "voiceDefault": "narrator", "sections": sections}
        book = {"schema": 1, "title": "Book", "quality": "mp3_44100_128",
                "model": "eleven_multilingual_v2", "paragraphGapMs": 700,
                "chapters": [{"stem": "chapter", "title": "Chapter",
                              "file": "chapters/chapter.json"}],
                "pronunciationDictionary": {"id": None, "versionId": None}}
        (export_root / "audiobook.json").write_text(json.dumps(book), encoding="utf-8")
        (export_root / "chapters/chapter.json").write_text(
            json.dumps(chapter), encoding="utf-8")
        return sections

    def test_fresh_init_builds_with_blank_narrator_voice_id(self) -> None:
        audio.init(self.manuscript)
        built = audio.build(self.manuscript)
        speeches = [section for chapter in built["chapters"].values()
                    for section in chapter["sections"] if section["type"] == "speech"]
        self.assertTrue(speeches)
        self.assertTrue(all(section["cast"] == "narrator" for section in speeches))
        self.assertTrue(all(section["voiceId"] == "" for section in speeches))
        self.assertIn("First paragraph.", [section["text"] for section in speeches])
        export_root = self.root / "_audio"
        (export_root / "chapters").mkdir(exist_ok=True)
        (export_root / "audiobook.json").write_text(
            json.dumps(built["book"]), encoding="utf-8")
        for stem, chapter in built["chapters"].items():
            (export_root / "chapters" / f"{stem}.json").write_text(
                json.dumps(chapter), encoding="utf-8")
        result = generation.preview(self.manuscript, None, renderer=render_preview)
        self.assertEqual(len(result["rendered"]), len(speeches))
        for section in speeches:
            self.assertEqual(generation.preview_path(export_root, section["id"])
                             .read_bytes(), section["text"].encode())

    def test_free_preview_renders_blank_voice_ids_without_api_key(self) -> None:
        sections = self.write_export(["", ""])

        result = generation.preview(self.manuscript, "chapter", renderer=render_preview)
        self.assertEqual(len(result["rendered"]), 2)
        for section in sections:
            self.assertEqual(generation.preview_path(self.root / "_audio", section["id"])
                             .read_bytes(), section["text"].encode())

    def assert_paid_generation_rejected(self, voice_ids: list[str]) -> None:
        self.write_export(voice_ids)
        requests = []

        def unexpected_remote_call(request, **kwargs):
            requests.append(request)
            raise AssertionError("Paid provider contacted before Voice ID validation")

        client = audio.ElevenLabs("synthetic-key", opener=unexpected_remote_call)
        with self.assertRaises(audio.AudioError) as caught:
            generation.generate(self.manuscript, "chapter", remaining=True, client=client)
        self.assertRegex(str(caught.exception).lower(), r"voice[ _]?id")
        self.assertIn("narrator", str(caught.exception).lower())
        self.assertEqual(requests, [])
        self.assertFalse(list((self.root / "_audio/audio").glob("*.mp3")))

    def test_paid_generation_rejects_missing_voice_before_provider_call(self) -> None:
        self.assert_paid_generation_rejected([""])

    def test_paid_generation_preflights_missing_voice_after_valid_selection(self) -> None:
        self.assert_paid_generation_rejected(["synthetic-valid-voice", ""])

    def test_paid_adapter_rejects_blank_voice_id_before_request(self) -> None:
        requests = []

        def unexpected_remote_call(request, **kwargs):
            requests.append(request)
            raise AssertionError("Paid adapter sent a request with a blank Voice ID")

        client = audio.ElevenLabs("synthetic-key", opener=unexpected_remote_call)
        voice = {"voice_id": "", "model": "eleven_multilingual_v2",
                 "stability": 0.5, "similarity": 0.75, "speed": 1.0}
        with self.assertRaises(audio.AudioError) as caught:
            client.tts("Synthetic paragraph.", voice, "mp3_44100_128")
        self.assertRegex(str(caught.exception).lower(), r"voice[ _]?id")
        self.assertEqual(requests, [])


if __name__ == "__main__":
    assert not any(name.endswith("_API_KEY") for name in os.environ)
    assert not paths.config_path().exists()
    assert paths.load_env() == []
    unittest.main(verbosity=2)
