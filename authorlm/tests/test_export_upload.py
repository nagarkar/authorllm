"""`export … --upload` (any export to the author's Drive, replaced in
place on re-export) and `export teleprompter` (the passages the author
reads on camera, `voice: author` clips, as a large-type DOCX).

Hermetic: a scratch workspace, a fake Drive service, no network, no
model, no spend. Run: python3 tests/test_export_upload.py
"""
from __future__ import annotations

import contextlib
import io
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

# test_passes pins the config/.env/client lookups away from the real ones.
import test_passes  # noqa: E402,F401

from authorlm import api, export, gdocs, media  # noqa: E402
from authorlm.cli import main as cli_main  # noqa: E402

# The shape of the SpiritualHindu monologues: a spoken intro and outro,
# each a clip in a Collage whose text lives in _clips/notes/.
STORY = """# **Look Behind It**

[Collage: Introduction, recorded by Chinmay in Riverside ⇢ collage-intro.md]

[Clip: Namaste, and welcome… ⇢ namaste.md | use: check Downloads folder | audio: full | voice: author]

[/Collage]

In a casual conversation with my son, I explained the picture.

[Collage: Kastrup on the drawer]

[Clip: Kastrup's line | youtube: https://www.youtube.com/watch?v=Z5o3NWxksv0]

[/Collage]

Keep looking behind a thing, and you will find Nothing.

[Collage: Outro, recorded by Chinmay in Riverside ⇢ collage-outro.md]

[Clip: Thank you for listening… ⇢ thanks.md | use: check Downloads folder | audio: full | voice: author]

[/Collage]
"""
NOTES = {
    "collage-intro.md": "Introduction, recorded by Chinmay in Riverside\n",
    "collage-outro.md": "Outro, recorded by Chinmay in Riverside\n",
    "namaste.md": ("Namaste, and welcome to The Spiritual Hindu.\n\n"
                   "I hope you like it. Enjoy.\n"),
    "thanks.md": "Thank you for listening. Namaste.\n",
}


class FakeDrive:
    """Records files().create / files().update; ids count up."""

    def __init__(self):
        self.calls: list[tuple] = []
        self.gone: set[str] = set()
        self.n = 0

    def files(self):
        drive = self

        class _Req:
            def __init__(self, fn):
                self.fn = fn

            def execute(self):
                return self.fn()

        class _Files:
            def create(self, body=None, media_body=None, fields=None):
                def run():
                    drive.n += 1
                    fid = (f"folder-{drive.n}" if body.get("mimeType", "")
                           .endswith(".folder") else f"file-{drive.n}")
                    drive.calls.append(("create", fid, dict(body),
                                        getattr(media_body, "_mimetype", None)))
                    return {"id": fid,
                            "webViewLink": f"https://drive.example/{fid}"}
                return _Req(run)

            def update(self, fileId=None, media_body=None, fields=None,
                       body=None):
                def run():
                    if fileId in drive.gone:
                        err = Exception("not found")
                        err.resp = type("R", (), {"status": 404})()
                        raise err
                    drive.calls.append(("update", fileId, body,
                                        getattr(media_body, "_mimetype", None)))
                    return {"id": fileId,
                            "webViewLink": f"https://drive.example/{fileId}"}
                return _Req(run)

        return _Files()


@contextlib.contextmanager
def _google(fake):
    saved = gdocs.get_service
    gdocs.get_service = lambda *a, **k: fake
    try:
        yield
    finally:
        gdocs.get_service = saved


def _run(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = 0
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            cli_main(argv)
        except SystemExit as exc:
            if isinstance(exc.code, str):
                err.write(exc.code + "\n")
                code = 1
            else:
                code = exc.code or 0
    return code, out.getvalue(), err.getvalue()


class ExportTests(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.ws = self.tmp / "ws"
        self.ms = self.ws / "SpiritualHindu"
        (self.ms / "_clips" / "notes").mkdir(parents=True)
        (self.ms / "picture-story.md").write_text(STORY, encoding="utf-8")
        (self.ms / "toc.toml").write_text(
            '[[chapter]]\nfile = "picture-story.md"\n', encoding="utf-8")
        for name, text in NOTES.items():
            (self.ms / "_clips" / "notes" / name).write_text(
                text, encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            cli_main(["--workspace", str(self.ws), "init", "--name",
                      "SpiritualHindu", "--path", str(self.ms),
                      "--extraction", "off"])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def cli(self, *argv: str) -> tuple[int, str, str]:
        return _run(["--workspace", str(self.ws), *argv])

    def uploads(self) -> dict:
        db = api.open_db(str(self.ws))
        return gdocs._mapping(db, api.get_manuscript(db))["gdocs"]["_uploads"]

    # -- the voice: field

    def test_voice_author_is_a_clip_field(self):
        parsed = media.parse_fields("x | use: a.mp4 | voice: author")
        self.assertEqual(parsed["fields"].get("voice"), "author")
        self.assertEqual(parsed["errors"], [])

    def test_voice_is_checked(self):
        listing = media.list_media(self.ms)
        clips = [e for e in listing["media"] if e["kind"] == "clip"]
        # (`use: check Downloads folder` is its own error, as in the
        # real monologues; only the voice rule is under test here.)
        self.assertFalse([e for c in clips for e in c["errors"]
                          if "voice" in e], [c["errors"] for c in clips])
        (self.ms / "picture-story.md").write_text(
            "# T\n\n[Collage: c]\n[Clip: x | use: a.mp4 | voice: narrator]\n"
            "[/Collage]\n\n[Clip: y | use: b.mp4 | voice: author]\nProse.\n"
            "[/Clip]\n", encoding="utf-8")
        clips = [e for e in media.list_media(self.ms)["media"]
                 if e["kind"] == "clip"]
        self.assertTrue(any("voice: 'narrator'" in e for e in clips[0]["errors"]),
                        clips[0]["errors"])
        self.assertTrue(any("Collage" in e for e in clips[1]["errors"]),
                        clips[1]["errors"])

    # -- teleprompter

    def test_teleprompter_passages_are_the_authors_lines_only(self):
        db = api.open_db(str(self.ws))
        passages = export.teleprompter_passages(api.get_manuscript(db),
                                                "picture-story.md")
        self.assertEqual([p["label"] for p in passages], ["Intro", "Outro"])
        self.assertEqual(passages[0]["paragraphs"],
                         ["Namaste, and welcome to The Spiritual Hindu.",
                          "I hope you like it. Enjoy."])
        self.assertEqual(passages[1]["paragraphs"],
                         ["Thank you for listening. Namaste."])

    def test_teleprompter_cli_writes_a_plain_large_type_docx(self):
        code, out, err = self.cli("export", "teleprompter", "--file",
                                  "picture-story.md")
        self.assertEqual(code, 0, err)
        docx = self.ms / "_exports" / "picture-story.teleprompter.docx"
        txt = docx.with_suffix(".txt")
        self.assertIn(str(docx), out)
        self.assertTrue(docx.exists() and txt.exists())
        plain = txt.read_text(encoding="utf-8")
        self.assertIn("Intro\n\nNamaste, and welcome", plain)
        self.assertIn("Outro\n\nThank you for listening.", plain)
        self.assertNotIn("casual conversation", plain)
        with zipfile.ZipFile(docx) as z:
            names = z.namelist()
            body = z.read("word/document.xml").decode("utf-8")
            styles = z.read("word/styles.xml").decode("utf-8")
        self.assertFalse([n for n in names if "header" in n or "footer" in n],
                         names)
        self.assertNotIn("PAGE", body)            # no page-number field
        self.assertNotIn("onfidential", body + styles)
        self.assertIn("Namaste, and welcome to The Spiritual Hindu.", body)
        self.assertNotIn("casual conversation", body)
        self.assertIn('w:sz w:val="64"', styles)   # 32pt body
        self.assertIn('w:line="360"', styles)       # 1.5 line spacing

    def test_teleprompter_whole_puts_the_body_between_the_clips(self):
        db = api.open_db(str(self.ws))
        passages = export.teleprompter_passages(
            api.get_manuscript(db), "picture-story.md", whole=True)
        labels = [p["label"] for p in passages]
        self.assertEqual(labels[0], "Intro")
        self.assertEqual(labels[-1], "Outro")
        self.assertIn("Body", labels)
        body = " ".join(" ".join(p["paragraphs"]) for p in passages
                        if p["label"] == "Body")
        self.assertIn("casual conversation", body)
        self.assertNotIn("[", body)
        code, out, err = self.cli("export", "teleprompter", "--file",
                                  "picture-story.md", "--whole")
        self.assertEqual(code, 0, err)

    def test_teleprompter_whole_reads_an_omit_audio_intro(self):
        (self.ms / "plain.md").write_text(
            "# P\n\n[Omit: audio]\n\nNamaste, said on camera.\n\n"
            "[/Omit]\n\nJust prose.\n", encoding="utf-8")
        db = api.open_db(str(self.ws))
        passages = export.teleprompter_passages(
            api.get_manuscript(db), "plain.md", whole=True)
        self.assertEqual(passages[0]["paragraphs"],
                         ["Namaste, said on camera.", "Just prose."])

    def test_teleprompter_needs_a_file_and_an_author_passage(self):
        code, _out, err = self.cli("export", "teleprompter")
        self.assertEqual(code, 1)
        self.assertIn("--file", err)
        (self.ms / "plain.md").write_text("# P\n\nJust prose.\n",
                                          encoding="utf-8")
        code, _out, err = self.cli("export", "teleprompter", "--file",
                                   "plain.md")
        self.assertEqual(code, 1)
        self.assertIn("voice: author", err)

    # -- --upload

    def test_upload_creates_once_then_replaces_in_place(self):
        fake = FakeDrive()
        with _google(fake):
            code, out, err = self.cli("export", "md", "--file",
                                      "picture-story.md", "--upload")
        self.assertEqual(code, 0, err)
        creates = [c for c in fake.calls if c[0] == "create"]
        self.assertEqual(len(creates), 2, fake.calls)   # folder + file
        _, fid, body, mime = creates[1]
        self.assertEqual(body["name"], "picture-story.md")
        self.assertEqual(body["parents"], ["folder-1"])
        self.assertEqual(mime, "text/markdown")
        self.assertNotIn("mimeType", body)                # as-is, no conversion
        self.assertIn(f"https://drive.example/{fid}", out)
        self.assertEqual(self.uploads()["picture-story.md"]["file_id"], fid)

        with _google(fake):
            code, out, err = self.cli("export", "md", "--file",
                                      "picture-story.md", "--upload")
        self.assertEqual(code, 0, err)
        self.assertEqual(fake.calls[-1][:2], ("update", fid))
        self.assertEqual(len([c for c in fake.calls if c[0] == "create"]), 2)
        self.assertIn(f"https://drive.example/{fid}", out)

    def test_upload_recreates_a_file_deleted_in_drive(self):
        fake = FakeDrive()
        with _google(fake):
            self.cli("export", "md", "--file", "picture-story.md", "--upload")
            first = self.uploads()["picture-story.md"]["file_id"]
            fake.gone.add(first)
            code, _out, err = self.cli("export", "md", "--file",
                                       "picture-story.md", "--upload")
        self.assertEqual(code, 0, err)
        self.assertNotEqual(self.uploads()["picture-story.md"]["file_id"],
                            first)

    def test_upload_adopts_an_existing_drive_file(self):
        fake = FakeDrive()
        with _google(fake):
            code, out, err = self.cli("export", "md", "--file",
                                      "picture-story.md", "--upload",
                                      "--drive-id", "old-123")
        self.assertEqual(code, 0, err)
        self.assertEqual(fake.calls, [("update", "old-123", None,
                                       "text/markdown")])
        self.assertEqual(self.uploads()["picture-story.md"]["file_id"],
                         "old-123")

    def test_teleprompter_uploads_as_a_google_doc_on_request(self):
        fake = FakeDrive()
        with _google(fake):
            code, out, err = self.cli("export", "teleprompter", "--file",
                                      "picture-story.md", "--upload",
                                      "--as-doc")
        self.assertEqual(code, 0, err)
        _, fid, body, mime = fake.calls[-1]
        self.assertEqual(body["mimeType"], gdocs.GDOC_MIME)
        self.assertEqual(body["name"], "picture-story.teleprompter")
        self.assertEqual(mime, export.DOCX_MIME)
        self.assertIn(f"https://docs.google.com/document/d/{fid}/edit", out)
        # A Doc and a plain .docx of the same export are different files.
        with _google(fake):
            self.cli("export", "teleprompter", "--file", "picture-story.md",
                     "--upload")
        self.assertEqual(fake.calls[-1][0], "create")
        self.assertNotIn("mimeType", fake.calls[-1][2])

    def test_as_doc_and_drive_id_need_upload_and_docx(self):
        code, _out, err = self.cli("export", "md", "--as-doc")
        self.assertEqual(code, 1)
        self.assertIn("--upload", err)
        code, _out, err = self.cli("export", "md", "--upload", "--as-doc")
        self.assertEqual(code, 1)
        self.assertIn("docx", err)
        code, _out, err = self.cli("export", "md", "--drive-id", "x")
        self.assertEqual(code, 1)
        self.assertIn("--upload", err)

    def test_upload_without_a_token_says_how_to_authorize(self):
        def no_token(*a, **k):
            raise ValueError("no valid Google token and interactive "
                             "consent is disabled")
        saved = gdocs.get_service
        gdocs.get_service = no_token
        try:
            code, _out, err = self.cli("export", "md", "--upload")
        finally:
            gdocs.get_service = saved
        self.assertEqual(code, 1)
        self.assertIn("doc auth", err)
        # The file is still written locally.
        self.assertTrue((self.ms / "_exports" / "SpiritualHindu.md").exists())


if __name__ == "__main__":
    unittest.main()
