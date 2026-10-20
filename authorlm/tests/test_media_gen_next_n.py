"""`media_gen._next_n`: numbered vs imported take slots.

`attach` mints `iNN`; generation mints `NN`. Both share one counter so a
fresh take never collides with an imported file of the same key.
Hermetic: scratch `_clips/` tree, no network.
Run: python3 tests/test_media_gen_next_n.py
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from authorlm import media, media_gen  # noqa: E402


class NextNTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / media.CLIPS_DIR).mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def _take(self, name: str) -> None:
        (self.root / media.CLIPS_DIR / name).write_bytes(b"MP4")

    def test_empty_starts_at_01(self):
        self.assertEqual(media_gen._next_n(self.root, "man-at-desk"), "01")
        self.assertEqual(
            media_gen._next_n(self.root, "man-at-desk", imported=True), "i01")

    def test_advances_past_numbered_and_imported(self):
        # Generation and attach share the numeric max: i02 and 01 → next is 03.
        self._take("man-at-desk-aaaaaaaa-0000-01.mp4")
        self._take("man-at-desk-bbbbbbbb-0000-i02.mp4")
        self.assertEqual(media_gen._next_n(self.root, "man-at-desk"), "03")
        self.assertEqual(
            media_gen._next_n(self.root, "man-at-desk", imported=True), "i03")

    def test_other_keys_do_not_collide(self):
        self._take("other-clip-aaaaaaaa-0000-09.mp4")
        self.assertEqual(media_gen._next_n(self.root, "man-at-desk"), "01")


if __name__ == "__main__":
    unittest.main()
