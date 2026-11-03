"""The [Signature: uri] tag: plain text in the files, a signature block
(image, author's name, export date) in every reader output, dropped
from the audio-clean build. Run: python3 tests/test_signature_tag.py
"""
from __future__ import annotations

import os
for _name in tuple(os.environ):
    if _name.endswith("_API_KEY"):
        os.environ.pop(_name)
os.environ["AUTHORLM_CONFIG"] = "/nonexistent/authorlm-signature/config.toml"
os.environ["AUTHORLM_ENV"] = "/nonexistent/authorlm-signature/.env"
os.environ["AUTHORLM_CLIENT"] = "none"

import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from authorlm import export

PNG = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)  # existence is all that matters


class SignatureTagTests(unittest.TestCase):

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="authorlm-signature-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "_assets").mkdir()
        (self.root / "_assets/signature.png").write_bytes(PNG)
        self.source = ("# Letter\n\nWith thanks.\n\n"
                       "[Signature: _assets/signature.png]\n")
        (self.root / "letter.md").write_text(self.source, encoding="utf-8")
        (self.root / "toc.toml").write_text(
            '[[chapter]]\nfile = "letter.md"\n', encoding="utf-8")
        self.manuscript = {
            "path": str(self.root), "name": "Book", "title": "Book",
            "author": "Ada Author", "copyright_owner": "Ada Author",
        }

    def build(self, fmt: str, variant: str = "images") -> str:
        text, _order, _warnings = export.publish_markdown(
            self.manuscript, variant, fmt=fmt)
        return text

    def test_tag_expands_to_image_name_and_date(self) -> None:
        stamp = date.today().strftime("%-d %B %Y")
        for fmt in ("pdf", "docx", "epub", "md"):
            text = self.build(fmt)
            self.assertNotIn("[Signature:", text, fmt)
            image = text.index("![](_assets/signature.png){width=40%}")
            name = text.index("\nAda Author\n")
            when = text.index(f"\n{stamp}\n")
            self.assertLess(image, name, fmt)
            self.assertLess(name, when, fmt)
            self.assertIn("With thanks.", text)

    def test_audio_clean_build_drops_the_tag(self) -> None:
        text = self.build("md", variant="stripped")
        self.assertNotIn("Signature", text)
        self.assertNotIn("signature.png", text)
        self.assertNotIn("Ada Author", text)
        self.assertIn("With thanks.", text)

    def test_missing_image_raises_naming_the_uri(self) -> None:
        (self.root / "letter.md").write_text(
            "# Letter\n\n[Signature: _assets/nowhere.png]\n", encoding="utf-8")
        with self.assertRaises(RuntimeError) as caught:
            self.build("pdf")
        self.assertIn("_assets/nowhere.png", str(caught.exception))

    def test_empty_uri_raises(self) -> None:
        (self.root / "letter.md").write_text(
            "# Letter\n\n[Signature: ]\n", encoding="utf-8")
        with self.assertRaises(RuntimeError):
            self.build("pdf")

    def test_home_uri_resolves_against_home(self) -> None:
        home = self.root / "home"
        home.mkdir()
        (home / "sig.png").write_bytes(PNG)
        old = os.environ.get("HOME")
        os.environ["HOME"] = str(home)
        self.addCleanup(lambda: os.environ.__setitem__("HOME", old))
        (self.root / "letter.md").write_text(
            "# Letter\n\n[Signature: ~/sig.png]\n", encoding="utf-8")
        text = self.build("docx")
        self.assertIn(f"![]({home / 'sig.png'}){{width=40%}}", text)

    def test_relative_uri_resolves_against_manuscript_root(self) -> None:
        self.assertEqual(
            export.signature_path("_assets/signature.png", self.root),
            self.root / "_assets/signature.png")
        absolute = self.root / "_assets/signature.png"
        self.assertEqual(export.signature_path(str(absolute), self.root),
                         absolute)
        self.assertIn(f"![]({absolute}){{width=40%}}",
                      export.resolve_signatures(
                          f"[Signature: {absolute}]", self.root, "A", {"pdf"}))

    def test_manuscript_file_is_unchanged_after_export(self) -> None:
        self.build("pdf")
        self.build("md", variant="stripped")
        self.assertEqual((self.root / "letter.md").read_text(encoding="utf-8"),
                         self.source)

    def test_tag_is_case_insensitive(self) -> None:
        (self.root / "letter.md").write_text(
            "# Letter\n\n[signature: _assets/signature.png]\n", encoding="utf-8")
        self.assertIn("![](_assets/signature.png)", self.build("epub"))


if __name__ == "__main__":
    unittest.main()
