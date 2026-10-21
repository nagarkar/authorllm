"""`media_gen.chosen` / `pick` / `attach` — the take the author (or
podlm) selected for a clip.

`chosen.json` is the store of record (the Doc never carries an embed
line, so a pull would lose it). An entry that points at a missing file
must read as "no choice" so collage/montage do not bind a dead path.
`pick` accepts bare or zero-padded take numbers; a missing take raises.
`attach` refuses a non-video source before copying.

`test_media` walks attach→pick on a happy path after a Veo round-trip.
This file pins the orphan, refusal, and zfill edges hermetically.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm import media, media_gen


class ChosenPickAttach(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="authorlm-chosen-")
        self.root = Path(self.tmp.name)
        (self.root / "ecce.md").write_text(
            "# Ecce\n\n[Clip: tired man]\nProse here is fine.\n[/Clip]\n",
            encoding="utf-8")
        media.maintain_keys(self.root)
        self.key = media.list_media(self.root)["media"][0]["key"]
        self.clips = self.root / media.CLIPS_DIR

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _take(self, n: str, body: bytes = b"MP4") -> Path:
        name = f"{self.key}-aaaaaaaa-0000-{n}.mp4"
        path = self.clips / name
        path.write_bytes(body)
        return path

    def test_orphaned_chosen_reads_as_none(self) -> None:
        (self.clips / "chosen.json").write_text(
            json.dumps({self.key: f"{self.key}-aaaaaaaa-0000-01.mp4"}) + "\n",
            encoding="utf-8")
        self.assertIsNone(media_gen.chosen(self.root, self.key))

    def test_pick_accepts_bare_or_zfilled_n(self) -> None:
        self._take("01")
        self._take("02")
        out = media_gen.pick(self.root, self.key, "2")
        self.assertEqual(out["key"], self.key)
        self.assertEqual(out["chosen"],
                         f"_clips/{self.key}-aaaaaaaa-0000-02.mp4")
        self.assertEqual(media_gen.chosen(self.root, self.key),
                         f"{self.key}-aaaaaaaa-0000-02.mp4")
        out2 = media_gen.pick(self.root, self.key, "01")
        self.assertEqual(out2["chosen"],
                         f"_clips/{self.key}-aaaaaaaa-0000-01.mp4")

    def test_pick_missing_take_raises(self) -> None:
        self._take("01")
        with self.assertRaises(LookupError) as ctx:
            media_gen.pick(self.root, self.key, "99")
        self.assertIn("no take 99", str(ctx.exception))
        self.assertIn(self.key, str(ctx.exception))

    def test_attach_refuses_non_video(self) -> None:
        src = self.root / "notes.txt"
        src.write_text("not a video", encoding="utf-8")
        with self.assertRaises(ValueError) as ctx:
            media_gen.attach(self.root, self.key, src)
        self.assertIn("not a video", str(ctx.exception))
        self.assertFalse(any(self.clips.glob("*-i*.txt")))

    def test_attach_copies_and_auto_chooses_when_none(self) -> None:
        src = self.root / "own.mp4"
        src.write_bytes(b"OWN")
        out = media_gen.attach(self.root, self.key, src)
        self.assertTrue(out["path"].endswith("-i01.mp4"))
        dest = self.root / out["path"]
        self.assertEqual(dest.read_bytes(), b"OWN")
        self.assertEqual(media_gen.chosen(self.root, self.key), dest.name)


if __name__ == "__main__":
    unittest.main()
