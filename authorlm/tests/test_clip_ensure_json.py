"""`clip ensure --json`: the plan/job shape podlm reads (PR #229).

Hermetic: scratch workspace, no network, no spend. `--generate` is
exercised only with `media_gen.start` stubbed.
Run: python3 tests/test_clip_ensure_json.py
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

from authorlm import media, media_gen  # noqa: E402
from authorlm.cli import main as cli_main  # noqa: E402

FILL = " ".join(["word"] * 120)
SPEC = ("subject: a tired man at a desk " + FILL + " setting: an office at "
        "dusk look: muted photoreal camera: slow push in light: low "
        "window light motion: slow sound: room tone continuity: none "
        "on-screen text: none")


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


class ClipEnsureJsonTests(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.ws = self.tmp / "ws"
        self.ms = self.ws / "book"
        self.ms.mkdir(parents=True)
        (self.ms / "toc.toml").write_text(
            '[[chapter]]\nfile = "ecce.md"\n', encoding="utf-8")
        (self.ms / "ecce.md").write_text(
            "[Clip: x]\n" + " ".join(["word"] * 10) + "\n[/Clip]\n",
            encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            cli_main(["--workspace", str(self.ws), "init", "--name", "book",
                      "--path", str(self.ms), "--extraction", "off"])
        media.maintain_keys(self.ms)
        self.key = media.list_media(self.ms)["media"][0]["key"]
        media.set_spec(self.ms, self.key, SPEC)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def cli(self, *argv: str) -> tuple[int, str, str]:
        return _run(["--workspace", str(self.ws), *argv])

    def test_json_emits_plan_with_null_job(self):
        code, out, err = self.cli("clip", "ensure", self.key, "--json")
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertEqual(set(payload), {"plan", "job"})
        self.assertIsNone(payload["job"])
        plan = payload["plan"]
        for field in ("key", "model", "duration", "est_usd", "prompt",
                      "reasons", "needs_confirm", "fresh_takes"):
            self.assertIn(field, plan, field)
        self.assertEqual(plan["key"], self.key)
        self.assertEqual(plan["reasons"], [])
        self.assertEqual(plan["fresh_takes"], [])
        self.assertFalse((self.ms / "_clips" / "jobs").exists())

    def test_json_generate_dry_run_still_spends_nothing(self):
        # --dry-run wins: podlm can preview the billed road without a job.
        code, out, err = self.cli(
            "clip", "ensure", self.key, "--json", "--generate", "--dry-run")
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertIsNone(payload["job"])
        self.assertFalse((self.ms / "_clips" / "jobs").exists())

    def test_json_generate_carries_started_job(self):
        fake_job = {"id": "clip-job-1", "state": "running", "est_usd": 0.40}

        def fake_start(db, manuscript, key, model=None, confirm=False,
                       http=None):
            self.assertEqual(key, self.key)
            return fake_job

        with mock.patch.object(media_gen, "start", side_effect=fake_start):
            code, out, err = self.cli(
                "clip", "ensure", self.key, "--json", "--generate")
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertEqual(payload["job"], fake_job)
        self.assertEqual(payload["plan"]["key"], self.key)

    def test_json_unknown_key_exits(self):
        code, out, err = self.cli("clip", "ensure", "no-such-clip", "--json")
        self.assertNotEqual(code, 0)
        self.assertIn("no clip with key", err)
        self.assertEqual(out.strip(), "")


if __name__ == "__main__":
    unittest.main()
