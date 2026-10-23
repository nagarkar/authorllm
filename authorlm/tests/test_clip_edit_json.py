"""`clip edit --json`: the plan/job shape podlm reads (PR #257).

Hermetic: scratch workspace, stubbed `edit_plan` / `edit_start`, no
network, no spend. Mirrors `clip ensure --json` so a shape drift cannot
break the external revise loop while generation still works.
Run: python3 tests/test_clip_edit_json.py
"""
from __future__ import annotations

import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import test_passes  # noqa: E402,F401

from authorlm import media_gen  # noqa: E402
from authorlm.cli import main as cli_main  # noqa: E402

PLAN = {
    "key": "man-at-desk",
    "take": "_clips/man-at-desk-aabbccdd-1122-01.mp4",
    "model": "gemini-omni-1.1-flash",
    "previous_interaction_id": "v1_x",
    "prompt": "warmer light Keep everything else the same.",
    "instruction": "warmer light",
    "duration": 4,
    "resolution": "720p",
    "aspect_ratio": "16:9",
    "est_usd": 0.43,
    "day_spent_usd": 0.0,
    "desc": "aabbccdd",
    "law": "1122",
    "reasons": [],
    "needs_confirm": [],
}


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


class ClipEditJsonTests(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.ws = self.tmp / "ws"
        self.ms = self.ws / "book"
        self.ms.mkdir(parents=True)
        (self.ms / "toc.toml").write_text(
            '[[chapter]]\nfile = "ecce.md"\n', encoding="utf-8")
        (self.ms / "ecce.md").write_text("# Hi\n\nProse.\n", encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            cli_main(["--workspace", str(self.ws), "init", "--name", "book",
                      "--path", str(self.ms), "--extraction", "off"])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def cli(self, *argv: str) -> tuple[int, str, str]:
        return _run(["--workspace", str(self.ws), *argv])

    def test_json_emits_plan_with_null_job(self):
        with mock.patch.object(media_gen, "edit_plan", return_value=dict(PLAN)):
            code, out, err = self.cli(
                "clip", "edit", "man-at-desk", "--take", "01",
                "--text", "warmer light", "--json")
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertEqual(set(payload), {"plan", "job"})
        self.assertIsNone(payload["job"])
        plan = payload["plan"]
        for field in ("key", "take", "model", "previous_interaction_id",
                      "prompt", "instruction", "est_usd", "reasons",
                      "needs_confirm"):
            self.assertIn(field, plan, field)
        self.assertEqual(plan["key"], "man-at-desk")
        self.assertEqual(plan["previous_interaction_id"], "v1_x")
        self.assertEqual(plan["reasons"], [])

    def test_json_generate_dry_run_still_spends_nothing(self):
        started = []

        def fake_start(*a, **k):
            started.append(1)
            return {"id": "should-not-run"}

        with mock.patch.object(media_gen, "edit_plan", return_value=dict(PLAN)), \
             mock.patch.object(media_gen, "edit_start", side_effect=fake_start):
            code, out, err = self.cli(
                "clip", "edit", "man-at-desk", "--take", "01",
                "--text", "warmer light", "--json", "--generate", "--dry-run")
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertIsNone(payload["job"])
        self.assertEqual(started, [])

    def test_json_generate_carries_started_job(self):
        fake_job = {"id": "edit-job-1", "state": "running", "est_usd": 0.43,
                    "edit_of": PLAN["take"]}

        def fake_start(manuscript, key, take, instruction, confirm=False,
                       http=None):
            self.assertEqual(key, "man-at-desk")
            self.assertEqual(take, "01")
            self.assertEqual(instruction, "warmer light")
            return fake_job

        with mock.patch.object(media_gen, "edit_plan", return_value=dict(PLAN)), \
             mock.patch.object(media_gen, "edit_start", side_effect=fake_start):
            code, out, err = self.cli(
                "clip", "edit", "man-at-desk", "--take", "01",
                "--text", "warmer light", "--json", "--generate")
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertEqual(payload["job"], fake_job)
        self.assertEqual(payload["plan"]["instruction"], "warmer light")

    def test_missing_take_or_text_exits(self):
        code, out, err = self.cli(
            "clip", "edit", "man-at-desk", "--text", "x", "--json")
        self.assertNotEqual(code, 0)
        self.assertIn("usage: clip edit", err)
        self.assertEqual(out.strip(), "")
        code, out, err = self.cli(
            "clip", "edit", "man-at-desk", "--take", "01", "--json")
        self.assertNotEqual(code, 0)
        self.assertIn("usage: clip edit", err)


if __name__ == "__main__":
    unittest.main()
