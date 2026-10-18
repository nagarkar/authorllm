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
from authorlm import audio, export, media, media_gen

TOC = '[[chapter]]\nfile = "ecce.md"\n'
FILL = " ".join(["word"] * 120)
# A complete generation spec: every section, 150+ words.
SPEC = ("subject: a tired man at a desk " + FILL + " setting: an office at "
        "dusk look: muted photoreal camera: slow push in light: low "
        "window light motion: slow sound: room tone continuity: none "
        "on-screen text: none")
LONG = SPEC


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
        self.assertEqual(quoted["description"], "lobster line")
        self.assertEqual(quoted["spec_kind"], "youtube_brief")
        # Two words is not a brief: no source, no quote or moment.
        self.assertEqual(quoted["status"], "draft")
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
                         ["tired-man-at-a", "tired-man-at-a-2", "collage-c",
                          "temple"])
        text = path.read_text(encoding="utf-8")
        self.assertIn("[Clip: tired man at a desk late at night ⇢ "
                      "tired-man-at-a.md]", text)
        self.assertIn("[Clip: ⇢ temple.md | use: _assets/temple.mp4 | "
                      "start: 0:01]", text)
        spec = self.root / "_clips" / "specs" / "tired-man-at-a.md"
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
        self.assertEqual(out["spec_kind"], "generation_spec")
        self.assertEqual(out["reasons"], [])
        text = (self.root / "ecce.md").read_text(encoding="utf-8")
        self.assertIn("[Clip: subject: a tired man at a desk word… ⇢ "
                      "tired-man.md]", text)
        # The file holds one section per line.
        stored = (self.root / "_clips" / "specs" / "tired-man.md").read_text(
            encoding="utf-8")
        self.assertIn("\nsetting: an office at dusk\n", stored)
        clip = [e for e in media.list_media(self.root)["media"]
                if e["kind"] == "clip"][0]
        self.assertEqual(clip["status"], "ready")
        with self.assertRaises(LookupError):
            media.set_spec(self.root, "no-such-key", "x")

    def test_set_fields_writes_confirmed_youtube(self):
        (self.root / "ecce.md").write_text(
            "[Collage: c]\n\n[Clip: source: The Matrix Reloaded quote: The "
            "problem is choice. | youtube: TBD]\n\n"
            "[/Collage]\n", encoding="utf-8")
        media.maintain_keys(self.root)
        out = media.set_fields(self.root, "the-problem-is-choice", {
            "youtube": "https://www.youtube.com/watch?v=cHZl2naX1Xk",
            "start": "2:43", "end": "2:50"})
        self.assertEqual(out["tag"], "[Clip: source: The Matrix Reloaded "
                         "quote: The problem is… ⇢ the-problem-is-choice."
                         "md | youtube: https://www.youtube.com/watch?v="
                         "cHZl2naX1Xk | start: 2:43 | end: 2:50]")
        clip = [e for e in media.list_media(self.root)["media"]
                if e["kind"] == "clip"][0]
        self.assertEqual(clip["status"], "ready", clip["errors"])
        out = media.set_fields(self.root, "the-problem-is-choice", {"end": None})
        self.assertNotIn("end:", out["tag"])
        with self.assertRaises(ValueError):
            media.set_fields(self.root, "the-problem-is-choice",
                             {"colour": "red"})

    def test_missing_spec_file_is_an_error(self):
        (self.root / "ecce.md").write_text(
            "[Clip: gone ⇢ gone.md]\nx\n[/Clip]\n", encoding="utf-8")
        rows = media.maintain_keys(self.root)
        self.assertTrue(rows[0]["missing"])
        clip = self.one("[Clip: gone ⇢ gone.md]\nx\n[/Clip]\n")
        self.assertTrue(any("is missing" in e for e in clip["errors"]))

    def test_youtube_brief_vs_generation_spec(self):
        entries = self.listing(
            "[Collage: c]\n\n"
            "[Clip: source: The Matrix Reloaded (2003), the Architect scene "
            "moment: Neo answers the Architect quote: \"Choice. The problem is "
            "choice.\" on screen: Neo and the Architect | youtube: "
            "https://www.youtube.com/watch?v=cHZl2naX1Xk | start: 2:43 | "
            "end: 2:50]\n\n"
            "[Clip: the Architect scene | youtube: "
            "https://www.youtube.com/watch?v=cHZl2naX1Xk]\n\n[/Collage]\n\n"
            "[Clip: a tired man | cast: ecce-man]\nWords.\n[/Clip]\n")["media"]
        full, thin, generated = [e for e in entries if e["kind"] == "clip"]
        self.assertEqual(full["spec_kind"], "youtube_brief")
        self.assertEqual(full["status"], "ready", full["draft_reasons"])
        self.assertEqual(full["query"], "The Matrix Reloaded (2003), the "
                         "Architect scene Neo answers the Architect")
        self.assertEqual(full["quote"], "Choice. The problem is choice.")
        self.assertEqual(full["youtube_brief"]["on screen"],
                         "Neo and the Architect")
        # A brief needs a source and a quote or moment — no word minimum.
        self.assertEqual(thin["status"], "draft")
        self.assertTrue(any("'source:'" in r for r in thin["draft_reasons"]))
        # A generation spec is checked by its own rules.
        self.assertEqual(generated["spec_kind"], "generation_spec")
        self.assertTrue(any("missing" in r for r in generated["draft_reasons"]))

    def test_brief_quote_must_be_whole_sentences(self):
        self.assertFalse(media.is_whole_sentences(
            "accepted the program as long as they were given a choice"))
        self.assertTrue(media.is_whole_sentences(
            "\"As I was saying, she stumbled upon a solution. Choice.\""))
        self.assertTrue(media.is_whole_sentences("Choice. The problem is choice."))
        reasons = media.check_youtube_brief(
            "source: The Matrix Reloaded quote: \"accepted the program as long "
            "as they were given a choice\"")
        self.assertTrue(any("fragment" in r for r in reasons), reasons)

    def test_audio_field(self):
        entries = self.listing(
            "[Montage: m]\n\n[Clip: | use: _assets/temple.mp4 | audio: duck]\n\n"
            "Prose under the music.\n\n[/Montage]\n\n"
            "[Collage: c]\n\n[Clip: | use: _assets/temple.mp4]\n\n[/Collage]\n\n"
            "[Clip: | use: _assets/temple.mp4 | audio: full]\nWords.\n[/Clip]\n")["media"]
        clips = [e for e in entries if e["kind"] == "clip"]
        self.assertEqual([c["audio"] for c in clips], ["duck", "full", "full"])
        self.assertEqual(clips[0]["status"], "ready")
        self.assertTrue(any("only a Collage allows" in e for e in clips[2]["errors"]))
        span = self.one("[Clip: | use: _assets/temple.mp4]\nWords.\n[/Clip]\n")
        self.assertEqual(span["audio"], "mute")
        bad = self.one("[Clip: | use: _assets/temple.mp4 | audio: loud]\nW.\n[/Clip]\n")
        self.assertTrue(any("not one of" in e for e in bad["errors"]))

    def test_key_names_what_the_clip_shows(self):
        (self.root / "ecce.md").write_text(
            "[Collage: c]\n\n[Clip: source: The Matrix Reloaded moment: Neo "
            "opens the door with the key | youtube: TBD]\n\n[/Collage]\n",
            encoding="utf-8")
        rows = media.maintain_keys(self.root)
        self.assertEqual(rows[1]["key"], "neo-opens-the-door")

    def test_kind_change_moves_the_file(self):
        path = self.root / "ecce.md"
        path.write_text("[Clip: the Architect scene]\nx\n[/Clip]\n",
                        encoding="utf-8")
        media.maintain_keys(self.root)
        self.assertTrue((self.root / "_clips/specs/the-architect-scene.md").exists())
        media.set_fields(self.root, "the-architect-scene",
                         {"youtube": "https://www.youtube.com/watch?v=cHZl2naX1Xk"})
        rows = media.maintain_keys(self.root)
        self.assertTrue(rows[0]["moved"])
        self.assertEqual(rows[0]["to"], "youtube_brief")
        self.assertTrue((self.root / "_clips/youtube-briefs/"
                         "the-architect-scene.md").exists())
        self.assertFalse((self.root / "_clips/specs/the-architect-scene.md").exists())

    def test_container_keys(self):
        path = self.root / "ecce.md"
        path.write_text("[Montage: Choice as prison and escape]\n\n"
                        "[Clip: | use: _assets/temple.mp4]\n\nProse here.\n\n"
                        "[/Montage]\n", encoding="utf-8")
        media.maintain_keys(self.root)
        text = path.read_text(encoding="utf-8")
        self.assertIn("[Montage: Choice as prison and escape ⇢ "
                      "montage-choice-as-prison-and.md]", text)
        montage = [e for e in media.list_media(self.root)["media"]
                   if e["kind"] == "montage"][0]
        self.assertEqual(montage["key"], "montage-choice-as-prison-and")
        self.assertEqual(montage["title"], "Choice as prison and escape")
        self.assertEqual(montage["status"], "ready", montage["errors"])
        self.assertEqual(media.list_media(self.root)["keys_provisional"], False)
        with self.assertRaises(ValueError):
            media.set_fields(self.root, "montage-choice-as-prison-and",
                             {"use": "x.mp4"})

    def test_spec_form_and_absorb(self):
        path = self.root / "ecce.md"
        path.write_text("[Clip: tired man | cast: ecce-man]\nWords.\n[/Clip]\n",
                        encoding="utf-8")
        media.maintain_keys(self.root)
        form = media.spec_form(self.root, "tired-man", LONG)
        self.assertEqual(form["old"], "[Clip: tired man ⇢ tired-man.md | "
                         "cast: ecce-man]")
        self.assertEqual(form["new"], f"[Clip: {LONG} ⇢ tired-man.md | "
                         "cast: ecce-man]")
        # The author edits the form in the tab, then accepts it.
        edited = LONG.replace("a tired man", "an exhausted man", 1)
        resolved = path.read_text(encoding="utf-8").replace(
            form["old"], f"[Clip: {edited} ⇢ tired-man.md | cast: ecce-man]")
        out, landed = media.absorb_specs(self.root, resolved, {"tired-man"})
        self.assertEqual([(r["key"], r["spec_kind"], r["reasons"])
                          for r in landed],
                         [("tired-man", "generation_spec", [])])
        self.assertIn("[Clip: subject: an exhausted man at a desk word… ⇢ "
                      "tired-man.md | cast: ecce-man]", out)
        spec = (self.root / "_clips" / "specs" / "tired-man.md").read_text(
            encoding="utf-8").strip()
        self.assertEqual(spec, media.format_sections(edited, "generation_spec"))
        # A declined form leaves the excerpt: nothing lands.
        out2, landed2 = media.absorb_specs(self.root, out, {"tired-man"})
        self.assertEqual(landed2, [])
        self.assertEqual(out2, out)

    # -- slice 3: generation (fake HTTP; nothing is spent)

    class _DB:
        """No style law: effective_style finds nothing."""
        def one(self, *a, **k):
            return None

        def all(self, *a, **k):
            return []

    def _generated_clip(self, words=10):
        (self.root / "ecce.md").write_text(
            "[Clip: x]\n" + " ".join(["word"] * words) + "\n[/Clip]\n",
            encoding="utf-8")
        media.maintain_keys(self.root)
        key = media.list_media(self.root)["media"][0]["key"]
        media.set_spec(self.root, key, SPEC)
        return key

    def test_plan_is_a_dry_run(self):
        key = self._generated_clip(words=10)
        ms = {"path": str(self.root), "id": "m"}
        p = media_gen.plan(self._DB(), ms, key)
        self.assertEqual(p["reasons"], [])
        self.assertEqual(p["model"], "veo-3.1-fast-generate-preview")
        self.assertEqual(p["duration"], 4)          # 10 words ≈ 4 s
        self.assertEqual(p["est_usd"], 0.40)        # 4 s × $0.10
        self.assertIn("SUBJECT: a tired man", p["prompt"])
        self.assertIn("No on-screen text", p["prompt"])
        self.assertEqual(p["fresh_takes"], [])
        self.assertFalse((self.root / "_clips" / "jobs").exists())

    def test_brief_clip_is_not_generated(self):
        (self.root / "ecce.md").write_text(
            "[Collage: c]\n\n[Clip: source: X quote: Y. | youtube: "
            "https://www.youtube.com/watch?v=cHZl2naX1Xk]\n\n[/Collage]\n",
            encoding="utf-8")
        media.maintain_keys(self.root)
        key = [e for e in media.list_media(self.root)["media"]
               if e["kind"] == "clip"][0]["key"]
        p = media_gen.plan(self._DB(), {"path": str(self.root), "id": "m"}, key)
        self.assertTrue(any("only a clip with a generation spec" in r
                            for r in p["reasons"]))

    def test_caps_need_confirm(self):
        key = self._generated_clip(words=10)
        (self.root / "_clips" / "settings.toml").write_text(
            "max_call_usd = 0.10\n", encoding="utf-8")
        ms = {"path": str(self.root), "id": "m"}
        self.assertTrue(media_gen.plan(self._DB(), ms, key)["needs_confirm"])
        with self.assertRaises(ValueError):
            media_gen.start(self._DB(), ms, key, http=lambda *a, **k: {})

    def test_start_poll_take_pick_attach(self):
        os.environ["GEMINI_API_KEY"] = "test-key"
        key = self._generated_clip(words=10)
        ms = {"path": str(self.root), "id": "m"}
        calls = []

        def http(method, url, k, body=None, raw=False, timeout=120):
            calls.append((method, url, body))
            if url.endswith(":predictLongRunning"):
                return {"name": "models/veo/operations/op1"}
            if url.endswith("operations/op1"):
                return {"done": True, "response": {"generateVideoResponse": {
                    "generatedSamples": [{"video": {"uri": "https://x/v.mp4"}}]}}}
            return b"MP4BYTES"
        job = media_gen.start(self._DB(), ms, key, http=http)
        body = calls[0][2]
        self.assertEqual(body["parameters"]["durationSeconds"], "4")
        self.assertEqual(body["parameters"]["personGeneration"], "allow_all")
        self.assertEqual(job["state"], "running")
        self.assertTrue((self.root / "_clips/jobs" /
                         f"{job['id']}.request.json").exists())
        done = media_gen.poll(ms, job["id"], http=http)
        self.assertEqual(done["state"], "done")
        take = self.root / done["take"]
        self.assertEqual(take.read_bytes(), b"MP4BYTES")
        self.assertTrue(take.name.endswith("-01.mp4"))
        entry = media.list_media(self.root, None, key)["media"][0]
        self.assertEqual(entry["chosen"], done["take"])
        self.assertEqual(entry["takes"][0]["stale"], [])
        # Fresh take: ensure returns it rather than generating.
        self.assertEqual(len(media_gen.plan(self._DB(), ms, key)["fresh_takes"]), 1)
        # Attach an outside take, pick it; a spec edit makes takes stale.
        src = self.root / "own.mp4"
        src.write_bytes(b"OWN")
        att = media_gen.attach(self.root, key, src)
        self.assertTrue(att["path"].endswith("-i02.mp4"))
        self.assertEqual(media_gen.pick(self.root, key, "i02")["chosen"],
                         att["path"])
        media.set_spec(self.root, key, SPEC.replace("tired", "weary"))
        entry = media.list_media(self.root, None, key)["media"][0]
        self.assertIn("stale-desc", entry["takes"][0]["stale"])

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
