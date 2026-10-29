"""Retake must not destroy a good paragraph take when the write fails mid-flight."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from authorlm import generation


class TakeAtomicTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="authorlm-take-atomic-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "audio").mkdir()
        self.rel = "audio/abababababababababababababababab.mp3_44100_128.mp3"
        self.final = self.root / self.rel
        self.final.write_bytes(b"GOOD-PAID-TAKE")

    def test_successful_write_replaces_via_tmp_then_rename(self) -> None:
        seen: list[Path] = []
        real_write = Path.write_bytes

        def tracking(self_path: Path, data: bytes) -> None:
            seen.append(self_path)
            return real_write(self_path, data)

        with mock.patch.object(Path, "write_bytes", tracking):
            out = generation.write_take_bytes(self.root, self.rel, b"NEW-TAKE")
        self.assertEqual(out, self.final)
        self.assertEqual(self.final.read_bytes(), b"NEW-TAKE")
        self.assertTrue(seen and str(seen[0]).endswith(".mp3.tmp"))
        self.assertFalse(self.final.with_name(self.final.name + ".tmp").exists())

    def test_failed_write_leaves_previous_take(self) -> None:
        def boom(self_path: Path, data: bytes) -> None:
            self.assertTrue(str(self_path).endswith(".mp3.tmp"), str(self_path))
            real = open(self_path, "wb")
            real.write(b"PARTIAL-CORRUPT")
            real.close()
            raise OSError("disk full")

        with mock.patch.object(Path, "write_bytes", boom):
            with self.assertRaises(OSError):
                generation.write_take_bytes(self.root, self.rel, b"NEW-TAKE")
        self.assertEqual(self.final.read_bytes(), b"GOOD-PAID-TAKE")
        self.assertFalse(self.final.with_name(self.final.name + ".tmp").exists())

    def test_failed_rename_leaves_previous_take(self) -> None:
        def bad_replace(src, dst):
            raise OSError("cross-device rename refused")

        with mock.patch.object(generation.os, "replace", bad_replace):
            with self.assertRaises(OSError):
                generation.write_take_bytes(self.root, self.rel, b"NEW-TAKE")
        self.assertEqual(self.final.read_bytes(), b"GOOD-PAID-TAKE")
        self.assertFalse(self.final.with_name(self.final.name + ".tmp").exists())


if __name__ == "__main__":
    unittest.main()
