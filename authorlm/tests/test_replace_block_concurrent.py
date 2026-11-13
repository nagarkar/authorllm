"""Concurrent audiobook paragraph edits must not erase each other.

The audiobook page serves Mac and phone on ThreadingHTTPServer threads.
Two replace_block calls that each read the whole file, patch one block,
and write back can share one pre-write snapshot — both succeed, one edit
is lost. The lock inside replace_block makes the second save re-read.

Run: python3 tests/test_replace_block_concurrent.py
"""
from __future__ import annotations

import os
for _name in tuple(os.environ):
    if _name.endswith("_API_KEY"):
        os.environ.pop(_name)
os.environ["AUTHORLM_CONFIG"] = "/nonexistent/authorlm-replace-concurrent/config.toml"
os.environ["AUTHORLM_ENV"] = "/nonexistent/authorlm-replace-concurrent/.env"
os.environ["AUTHORLM_CLIENT"] = "none"

import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from authorlm import audio


class ReplaceBlockConcurrentTests(unittest.TestCase):

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="authorlm-replace-concurrent-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "ch.md"

    def test_two_paragraph_edits_both_land(self) -> None:
        """Mac+phone saving different paragraphs of one chapter keep both."""
        for _ in range(40):
            self.path.write_text("Alpha old.\n\nBeta old.\n", encoding="utf-8")
            found_a = {"file": "ch.md", "line": 1, "lines": 1,
                       "markdown": "Alpha old."}
            found_b = {"file": "ch.md", "line": 3, "lines": 1,
                       "markdown": "Beta old."}
            with ThreadPoolExecutor(max_workers=2) as pool:
                fa = pool.submit(audio.replace_block, self.root, found_a,
                                 "Alpha NEW.")
                fb = pool.submit(audio.replace_block, self.root, found_b,
                                 "Beta NEW.")
                fa.result()
                fb.result()
            text = self.path.read_text(encoding="utf-8")
            self.assertEqual(text, "Alpha NEW.\n\nBeta NEW.\n")

    def test_lock_serializes_read_check_write(self) -> None:
        """Forced interleaving without the lock loses an edit; with it, both land.

        This mirrors the race ThreadingHTTPServer exposes: both threads pass
        the stale-block check against the same snapshot, then each write_text
        overwrites the other's patch."""
        self.path.write_text("Alpha old.\n\nBeta old.\n", encoding="utf-8")
        found_a = {"file": "ch.md", "line": 1, "lines": 1, "markdown": "Alpha old."}
        found_b = {"file": "ch.md", "line": 3, "lines": 1, "markdown": "Beta old."}
        barrier = threading.Barrier(2)
        errors: list[BaseException] = []

        def unlocked_race(found: dict, new: str) -> None:
            try:
                text = self.path.read_text(encoding="utf-8")
                lines = text.split("\n")
                start, count = found["line"] - 1, found["lines"]
                if "\n".join(lines[start:start + count]) != found["markdown"]:
                    raise audio.AudioError("changed")
                barrier.wait(timeout=2)
                lines[start:start + count] = new.split("\n")
                self.path.write_text("\n".join(lines), encoding="utf-8")
            except BaseException as err:  # noqa: BLE001 — collect for the assert
                errors.append(err)

        t1 = threading.Thread(target=unlocked_race,
                              args=(found_a, "Alpha NEW."))
        t2 = threading.Thread(target=unlocked_race,
                              args=(found_b, "Beta NEW."))
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        self.assertEqual(errors, [])
        lost = self.path.read_text(encoding="utf-8")
        self.assertNotEqual(
            lost, "Alpha NEW.\n\nBeta NEW.\n",
            "unlocked race should demonstrate the lost-edit failure mode")

        self.path.write_text("Alpha old.\n\nBeta old.\n", encoding="utf-8")
        with ThreadPoolExecutor(max_workers=2) as pool:
            fa = pool.submit(audio.replace_block, self.root, found_a,
                             "Alpha NEW.")
            fb = pool.submit(audio.replace_block, self.root, found_b,
                             "Beta NEW.")
            fa.result()
            fb.result()
        self.assertEqual(self.path.read_text(encoding="utf-8"),
                         "Alpha NEW.\n\nBeta NEW.\n")


if __name__ == "__main__":
    unittest.main()
