"""Stitch must not destroy a good chapter mp3 when encode fails mid-flight."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from authorlm import generation
from authorlm.audio import AudioError


class StitchAtomicTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="authorlm-stitch-atomic-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "audio").mkdir()
        self.final = self.root / "audio" / "sermons.mp3_44100_128.mp3"
        self.final.write_bytes(b"GOOD-CHAPTER-AUDIO")
        self.inputs = [str(self.root / "audio" / "seg.mp3")]
        Path(self.inputs[0]).write_bytes(b"seg")

    def test_failed_encode_leaves_previous_stitched_file(self) -> None:
        def boom(argv, what):
            tmp = Path(argv[-1])
            self.assertTrue(str(tmp).endswith(".mp3.tmp"), argv[-1])
            tmp.write_bytes(b"PARTIAL-CORRUPT")
            raise AudioError("ffmpeg stitch exited 1: boom")

        with mock.patch.object(generation, "_run", side_effect=boom):
            with self.assertRaises(AudioError):
                generation.concat_inputs(
                    "ffmpeg", self.root, "sermons", "mp3_44100_128",
                    self.inputs, None)
        self.assertEqual(self.final.read_bytes(), b"GOOD-CHAPTER-AUDIO")
        self.assertFalse(self.final.with_name(self.final.name + ".tmp").exists())
        self.assertEqual(list(self.root.glob("stitch_list*.txt")), [])

    def test_successful_encode_replaces_via_tmp_then_rename(self) -> None:
        seen: list[str] = []

        def ok(argv, what):
            tmp = Path(argv[-1])
            seen.append(str(tmp))
            self.assertTrue(str(tmp).endswith(".mp3.tmp"))
            self.assertFalse(tmp.resolve() == self.final.resolve())
            tmp.write_bytes(b"NEW-CHAPTER-AUDIO")

        with mock.patch.object(generation, "_run", side_effect=ok):
            rel = generation.concat_inputs(
                "ffmpeg", self.root, "sermons", "mp3_44100_128",
                self.inputs, None)
        self.assertEqual(rel, "audio/sermons.mp3_44100_128.mp3")
        self.assertEqual(self.final.read_bytes(), b"NEW-CHAPTER-AUDIO")
        self.assertTrue(seen and seen[0].endswith(".mp3.tmp"))
        self.assertFalse(Path(seen[0]).exists())

    def test_measure_list_is_scoped_per_stem(self) -> None:
        written: list[str] = []

        def fake_run(argv, **kwargs):
            written.append(argv[argv.index("-i") + 1])
            class Proc:
                returncode = 0
                stderr = (
                    '{"input_i":"-20.0","input_tp":"-3.0","input_lra":"5.0",'
                    '"input_thresh":"-30.0","target_offset":"0.1"}'
                )
            return Proc()

        with mock.patch.object(generation.subprocess, "run", side_effect=fake_run):
            generation.measure_loudness("ffmpeg", self.root, "sermons!", self.inputs)
            generation.measure_loudness("ffmpeg", self.root, "other", self.inputs)
        self.assertIn("stitch_measure_list.sermons.txt", written[0])
        self.assertIn("stitch_measure_list.other.txt", written[1])
        # lists cleaned up
        self.assertEqual(list(self.root.glob("stitch_measure_list*.txt")), [])


if __name__ == "__main__":
    unittest.main()
