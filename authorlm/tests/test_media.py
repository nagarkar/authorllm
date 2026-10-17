"""Media slice 1: [Clip] / [Collage] grammar, validation, `media list`,
and stripping from every book export (docs/video-clips-design.md).

Hermetic: a scratch manuscript on disk, no DB, no model, no spend.
Run: python3 tests/test_media.py
"""
from __future__ import annotations

import os
os.environ["AUTHORLM_CONFIG"] = "/nonexistent/authorlm-media/config.toml"
os.environ["AUTHORLM_ENV"] = "/nonexistent/authorlm-media/.env"
os.environ["AUTHORLM_CLIENT"] = "none"

import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from authorlm import audio, export, media

TOC = '[[chapter]]\nfile = "ecce.md"\n'
LONG = " ".join(["word"] * 150)


class MediaTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "_assets").mkdir()
        (self.root / "_assets" / "kailash.jpg").write_bytes(b"")
        (self.root / "_assets" / "temple.mp4").write_bytes(b"")
        (self.root / "toc.toml").write_text(TOC, encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def listing(self, body: str) -> dict:
        (self.root / "ecce.md").write_text(body, encoding="utf-8")
        return media.list_media(self.root)

    def one(self, body: str) -> dict:
        clips = [e for e in self.listing(body)["media"] if e["kind"] == "clip"]
        self.assertEqual(len(clips), 1, clips)
        return clips[0]

    # -- shapes and spoken words

    def test_span_carries_spoken_words(self):
        clip = self.one(
            "# Ecce\n\n[Clip: tired man, office, dusk]\n"
            "He worked late.[Footnote: cite x] Nobody noticed.[^E1]\n\n"
            "[Omit: audio]\nNot read aloud.\n[/Omit]\n\nHe went home.\n"
            "[/Clip]\n")
        self.assertEqual(clip["shape"], "span")
        self.assertEqual(clip["kind_of"], "generated")
        self.assertEqual(clip["words"],
                         "He worked late. Nobody noticed. He went home.")
        self.assertEqual(clip["first_sentence"], "He worked late.")
        self.assertEqual(clip["last_sentence"], "He went home.")
        self.assertEqual(clip["status"], "draft")   # under 150 words
        self.assertEqual(clip["line"], 3)

    def test_spec_of_150_words_is_ready(self):
        clip = self.one(f"[Clip: {LONG}]\nWords.\n[/Clip]\n")
        self.assertEqual(clip["status"], "ready")

    def test_collage_is_a_cutaway(self):
        entries = self.listing(
            "Setup line. Then comes the line:\n\n"
            "[Collage: Peterson on responsibility]\n"
            "[Clip: lobster line | youtube: https://www.youtube.com/watch?"
            "v=abcdefg123 | start: 1:23 | end: 2:05]\n"
            "[Clip: | use: _assets/temple.mp4 | start: 0:40 | end: 0:52]\n"
            "[/Collage]\n\nThat is the reaction. And more.\n")["media"]
        collage, quoted, used = entries
        self.assertEqual(collage["kind"], "collage")
        self.assertEqual(collage["status"], "ready", collage["errors"])
        self.assertEqual(collage["children"], [quoted["key"], used["key"]])
        self.assertEqual(collage["before_sentence"], "Then comes the line:")
        self.assertEqual(collage["after_sentence"], "That is the reaction.")
        self.assertNotIn("words", collage)
        self.assertEqual(quoted["shape"], "point")
        self.assertEqual(quoted["kind_of"], "quoted")
        self.assertEqual(quoted["label"], "lobster line")
        self.assertEqual(quoted["description"], "")
        self.assertEqual(quoted["status"], "ready")
        # Every clip in a cutaway shares its edges.
        self.assertEqual(used["before_sentence"], "Then comes the line:")
        self.assertEqual(used["after_sentence"], "That is the reaction.")

    def test_montage_plays_under_prose(self):
        entries = self.listing(
            "[Montage: Choice as prison and escape]\n"
            "[Clip: | use: _assets/temple.mp4]\n"
            "Choice made the prison possible. It is also the escape.\n"
            "[Clip: | use: _assets/temple.mp4 | start: 0:01]\n"
            "Last line.\n[/Montage]\n")["media"]
        montage, first, second = entries
        self.assertEqual(montage["kind"], "montage")
        self.assertEqual(montage["status"], "ready", montage["errors"])
        self.assertEqual(montage["words"], "Choice made the prison possible. "
                         "It is also the escape. Last line.")
        self.assertEqual(montage["first_sentence"],
                         "Choice made the prison possible.")
        self.assertEqual(montage["last_sentence"], "Last line.")
        self.assertEqual(first["shape"], "montage")
        self.assertEqual(first["after_sentence"],
                         "Choice made the prison possible.")
        self.assertEqual(second["before_sentence"], "It is also the escape.")

    def test_container_rules(self):
        def errors(body):
            return [e for x in self.listing(body)["media"]
                    if x["kind"] in ("collage", "montage") for e in x["errors"]]
        self.assertTrue(any("belongs in a [Montage]" in e for e in errors(
            "[Collage: c]\n[Clip: | use: _assets/temple.mp4]\nProse.\n"
            "[/Collage]\n")))
        self.assertTrue(any("clips alone are a [Collage]" in e for e in errors(
            "[Montage: m]\n[Clip: | use: _assets/temple.mp4]\n[/Montage]\n")))
        self.assertTrue(any("holds no clips" in e for e in errors(
            "[Montage: m]\nProse.\n[/Montage]\n")))
        self.assertTrue(any("it needs [/Collage]" in e for e in errors(
            "[Collage: c]\n[Clip: | use: _assets/temple.mp4]\n[/Montage]\n")))
        self.assertTrue(any("montage inside the collage" in e for e in errors(
            "[Collage: c]\n[Montage: m]\n[/Montage]\n[/Collage]\n")))

    # -- §5 field combinations

    def assertError(self, body: str, fragment: str):
        clip = self.one(body)
        self.assertEqual(clip["status"], "invalid", clip)
        self.assertTrue(any(fragment in e for e in clip["errors"]),
                        clip["errors"])

    def test_bare_clip_must_point(self):
        self.assertError("[Clip: ]\nx\n[/Clip]\n", "must point at something")

    def test_timestamp_on_generated_clip(self):
        self.assertError(f"[Clip: {LONG} | start: 0:10]\nx\n[/Clip]\n",
                         "is a timestamp")

    def test_image_trim_points(self):
        self.assertError("[Clip: | use: _assets/kailash.jpg | start: 0:10]\n"
                         "x\n[/Clip]\n", "a still has no in or out point")

    def test_end_before_start(self):
        self.assertError("[Clip: | use: _assets/temple.mp4 | start: 1:00 | "
                         "end: 0:30]\nx\n[/Clip]\n", "end: is not after start:")

    def test_end_needs_start_on_generated(self):
        self.assertError(f"[Clip: {LONG} | end: _assets/kailash.jpg]\nx\n"
                         "[/Clip]\n", "end: needs start:")

    def test_unknown_field_and_cast(self):
        self.assertError(f"[Clip: {LONG} | colour: red]\nx\n[/Clip]\n",
                         "unknown field 'colour:'")
        self.assertError(f"[Clip: {LONG} | cast: nobody]\nx\n[/Clip]\n",
                         "'nobody' is not defined")

    def test_cast_on_pointer_clip(self):
        self.assertError("[Clip: | use: _assets/temple.mp4 | cast: x]\nx\n"
                         "[/Clip]\n", "applies only to a generated clip")

    def test_missing_file_and_bad_url(self):
        self.assertError("[Clip: | use: _assets/none.mp4]\nx\n[/Clip]\n",
                         "does not exist")
        self.assertError("[Clip: | youtube: https://example.com/x]\nx\n"
                         "[/Clip]\n", "not a YouTube video url")

    def test_generated_start_frame_path(self):
        clip = self.one(f"[Clip: {LONG} | start: _assets/kailash.jpg | "
                        'text: "Mount Kailash"]\nx\n[/Clip]\n')
        self.assertEqual(clip["status"], "ready", clip["errors"])
        self.assertEqual(clip["fields"]["text"], "Mount Kailash")

    # -- §8i structure

    def test_structure_errors(self):
        self.assertError("[Clip: a | use: _assets/temple.mp4]\nx\n## H\n",
                         "crosses the heading")
        self.assertError("[Clip: a | use: _assets/temple.mp4]\nx\n",
                         "never closed")
        self.assertError("[Omit: audio]\n[Clip: | use: _assets/temple.mp4]\n"
                         "x\n[/Clip]\n[/Omit]\n", "not spoken in audio")
        listing = self.listing("[Collage: c]\n[Clip: | use: _assets/temple.mp4]"
                               "\nx\n[/Clip]\n[/Collage]\n")
        self.assertTrue(any("inside a Collage" in p
                            for p in listing["problems"]), listing["problems"])
        nested = self.listing("[Collage: a]\n[Collage: b]\n[/Collage]\n")
        self.assertTrue(any("collage inside the collage" in e
                            for c in nested["media"] for e in c["errors"]))

    def test_span_crosses_paragraphs(self):
        clip = self.one(f"[Clip: {LONG}]\nOne.\n\nTwo.\n[/Clip]\n")
        self.assertEqual(clip["status"], "ready")
        self.assertEqual(clip["words"], "One. Two.")

    def test_prior_names_a_clip(self):
        entries = self.listing(
            f"[Clip: {LONG} | prior: later-clip-key]\nx\n[/Clip]\n")["media"]
        self.assertTrue(any("is not a clip key" in e for e in entries[0]["errors"]))

    # -- illustrations and collect rows

    def test_illustration_entry_and_span_wrapping_it(self):
        entries = self.listing(
            "Before the image. Last sentence here.\n\n"
            "[Illustration: a cave mouth | caption: The cave.]\n\n"
            f"[Clip: {LONG}]\nUnder.\n\n[Illustration: inside]\n\nMore.\n"
            "[/Clip]\n")["media"]
        kinds = [e["kind"] for e in entries]
        self.assertEqual(kinds, ["illustration", "clip", "illustration"])
        self.assertEqual(entries[0]["before_sentence"], "Last sentence here.")
        self.assertEqual(entries[0]["caption"], "The cave.")
        self.assertEqual(entries[0]["status"], "unrendered")
        self.assertEqual(entries[1]["words"], "Under. More.")

    def test_not_ready_rows(self):
        (self.root / "ecce.md").write_text(
            "[Clip: short]\nx\n[/Clip]\n[/Clip]\n", encoding="utf-8")
        rows = media.not_ready(self.root)
        self.assertEqual([r["status"] for r in rows], ["draft", "invalid"])

    # -- slice 2: keys, specs, fields

    def test_collect_mints_keys_and_maintains_excerpts(self):
        body = ("[Clip: tired man at a desk late at night]\nWords.\n[/Clip]\n\n"
                "[Clip: tired man at a desk late at night]\nMore.\n[/Clip]\n\n"
                "[Collage: c]\n\n[Clip: | use: _assets/temple.mp4 | start: 0:01]"
                "\n\n[/Collage]\n")
        path = self.root / "ecce.md"
        path.write_text(body, encoding="utf-8")
        rows = media.maintain_keys(self.root)
        self.assertEqual([r["key"] for r in rows],
                         ["tired-man-at-a", "tired-man-at-a-2", "temple"])
        text = path.read_text(encoding="utf-8")
        self.assertIn("[Clip: tired man at a desk late at night ⇢ "
                      "tired-man-at-a.md]", text)
        self.assertIn("[Clip: ⇢ temple.md | use: _assets/temple.mp4 | "
                      "start: 0:01]", text)
        spec = self.root / "_clips" / "prompts" / "tired-man-at-a.md"
        self.assertEqual(spec.read_text(encoding="utf-8").strip(),
                         "tired man at a desk late at night")
        # Idempotent, and an excerpt edit does not hold.
        self.assertEqual(media.maintain_keys(self.root), [])
        path.write_text(text.replace("tired man at a desk late at night ⇢ "
                                     "tired-man-at-a.md",
                                     "edited excerpt ⇢ tired-man-at-a.md"),
                        encoding="utf-8")
        fixed = media.maintain_keys(self.root)
        self.assertTrue(fixed[0]["excerpt_fixed"])
        self.assertIn("tired man at a desk late at night ⇢ tired-man-at-a.md",
                      path.read_text(encoding="utf-8"))
        # Keys survive in media list; the spec is the description.
        clips = [e for e in media.list_media(self.root)["media"]
                 if e["kind"] == "clip"]
        self.assertEqual(clips[0]["key"], "tired-man-at-a")
        self.assertTrue(clips[0]["keyed"])
        self.assertEqual(clips[0]["description"],
                         "tired man at a desk late at night")

    def test_set_spec_lands_and_refreshes_excerpt(self):
        (self.root / "ecce.md").write_text(
            "[Clip: tired man]\nWords.\n[/Clip]\n", encoding="utf-8")
        media.maintain_keys(self.root)
        out = media.set_spec(self.root, "tired-man", LONG)
        self.assertEqual(out["words"], 150)
        text = (self.root / "ecce.md").read_text(encoding="utf-8")
        self.assertIn("[Clip: word word word word word word word word… ⇢ "
                      "tired-man.md]", text)
        clip = [e for e in media.list_media(self.root)["media"]
                if e["kind"] == "clip"][0]
        self.assertEqual(clip["status"], "ready")
        with self.assertRaises(LookupError):
            media.set_spec(self.root, "no-such-key", "x")

    def test_set_fields_writes_confirmed_youtube(self):
        (self.root / "ecce.md").write_text(
            "[Collage: c]\n\n[Clip: Neo answers | youtube: TBD]\n\n"
            "[/Collage]\n", encoding="utf-8")
        media.maintain_keys(self.root)
        out = media.set_fields(self.root, "neo-answers", {
            "youtube": "https://www.youtube.com/watch?v=cHZl2naX1Xk",
            "start": "2:43", "end": "2:50"})
        self.assertEqual(out["tag"], "[Clip: Neo answers ⇢ neo-answers.md | "
                         "youtube: https://www.youtube.com/watch?v=cHZl2naX1Xk"
                         " | start: 2:43 | end: 2:50]")
        clip = [e for e in media.list_media(self.root)["media"]
                if e["kind"] == "clip"][0]
        self.assertEqual(clip["status"], "ready", clip["errors"])
        out = media.set_fields(self.root, "neo-answers", {"end": None})
        self.assertNotIn("end:", out["tag"])
        with self.assertRaises(ValueError):
            media.set_fields(self.root, "neo-answers", {"colour": "red"})

    def test_missing_spec_file_is_an_error(self):
        (self.root / "ecce.md").write_text(
            "[Clip: gone ⇢ gone.md]\nx\n[/Clip]\n", encoding="utf-8")
        rows = media.maintain_keys(self.root)
        self.assertTrue(rows[0]["missing"])
        clip = self.one("[Clip: gone ⇢ gone.md]\nx\n[/Clip]\n")
        self.assertTrue(any("is missing" in e for e in clip["errors"]))

    # -- exports

    def test_strip_for_book(self):
        text = ("Intro.\n\n[Clip: x]\nKept prose.\n[/Clip]\n\n"
                "[Montage: c]\nMontage prose kept.\n[Clip: y | use: a.mp4]\n"
                "[/Montage]\n\n[Collage: cut]\n[Clip: z | use: a.mp4]\n"
                "[/Collage]\n\nAfter.\n")
        out, removed = media.strip_for_book(text, "ecce.md")
        for kept in ("Intro.", "Kept prose.", "Montage prose kept.", "After."):
            self.assertIn(kept, out)
        self.assertNotIn("[Clip", out)
        self.assertNotIn("[Collage", out)
        self.assertNotIn("[Montage", out)
        self.assertNotIn("[/", out)
        self.assertEqual(removed, 5)

    def test_book_and_audio_exports_drop_tags(self):
        body = ("# Ecce\n\nIntro.\n\n[Clip: tired man]\nKept prose.\n[/Clip]\n\n"
                "[Montage: c]\n[Clip: | use: a.mp4]\nMontage prose.\n[/Montage]\n")
        (self.root / "ecce.md").write_text(body, encoding="utf-8")
        manuscript = {"path": str(self.root), "name": "t", "id": "m"}
        for fmt in ("md", "pdf", "epub", "docx"):
            text, _order, _warnings = export.publish_markdown(
                manuscript, "full", fmt=fmt)
            self.assertIn("Kept prose.", text, fmt)
            self.assertIn("Montage prose.", text, fmt)
            self.assertNotIn("[Clip", text, fmt)
            self.assertNotIn("[Montage", text, fmt)
        spoken = audio.audio_source("ecce.md", body)
        self.assertIn("Montage prose.", spoken)
        self.assertNotIn("[Clip", spoken)

    def test_check_manuscript_reports_unclosed_collage(self):
        (self.root / "ecce.md").write_text("[Collage: c]\nprose\n",
                                           encoding="utf-8")
        problems = export.check_manuscript({"path": str(self.root)})
        self.assertTrue(any("never closed" in p for p in problems), problems)


if __name__ == "__main__":
    unittest.main()
