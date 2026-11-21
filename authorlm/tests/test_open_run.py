"""`changes._open_run`, `book._find_run` and `book._require_document_ids`
are the who-may-write gates every `book land` / `book draft` walks before
touching a chapter file.

A silent drift here lands text under a person step, a finished agent
step, a step that never changes text, a forms-mode book, or a
manuscript with document ids off — or picks the wrong run when the id
prefix is ambiguous. Hermetic: temp workspace, no Google, no model.
Run: python3 tests/test_open_run.py
"""
from __future__ import annotations

import os

os.environ["AUTHORLM_CONFIG"] = "/nonexistent/authorlm-open-run/config.toml"
os.environ["AUTHORLM_ENV"] = "/nonexistent/authorlm-open-run/.env"
os.environ["AUTHORLM_CLIENT"] = "none"
os.environ["AUTHORLM_STYLE_DEFAULTS"] = \
    "/nonexistent/authorlm-open-run/style-defaults.toml"

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import authorlm  # noqa: E402

assert Path(authorlm.__file__).resolve().parent == HERE.parent / "authorlm", (
    f"testing the wrong package: {authorlm.__file__}")

from authorlm import api, book, changes, docs  # noqa: E402

WORKFLOW = '''[[step]]
id = "interview"
role = "interviewer"
actor = "human:author"
grain = "book"
gives = ["register"]
returns = "the brief"
leaves_alone = "the manuscript"

[[step]]
id = "draft"
role = "drafter"
actor = "agent"
grain = "chapter"
after = ["interview"]
gives = ["chapter"]
returns = "the chapter drafted in its file"
leaves_alone = "every other chapter"
changes_text = true

[[step]]
id = "critic"
role = "critic"
actor = "agent"
grain = "chapter"
after = ["draft"]
gives = ["chapter"]
returns = "findings"
leaves_alone = "the chapter text"
independent = true
'''

TOC = '''[[chapter]]
file = "one.md"

[[chapter]]
file = "two.md"
'''

NO_USES = {"register_uses": "none",
           "register_uses_reason": "this test's step rested on no entry"}


def quiet(function, *args, **kwargs):
    with contextlib.redirect_stdout(io.StringIO()):
        return function(*args, **kwargs)


class OpenRunTests(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.workspace = base / "workspace"
        self.root = base / "book"
        self.root.mkdir()
        for name in ("one.md", "two.md"):
            (self.root / name).write_text(
                f"# {name}\n\nA sentence of {name}. " * 40 + "\n",
                encoding="utf-8")
        (self.root / "toc.toml").write_text(TOC, encoding="utf-8")
        self.db = api.open_db(str(self.workspace), create=True)
        api.register_manuscript(self.db, "book", str(self.root),
                                extraction=False)
        self.manuscript = api.get_manuscript(self.db)
        quiet(api.collect, self.db, self.manuscript, {})
        definition = book.definition_path(self.manuscript)
        definition.parent.mkdir(parents=True)
        definition.write_text(WORKFLOW, encoding="utf-8")
        book.set_agent_edits(self.db, self.manuscript, "direct")

    def tearDown(self) -> None:
        self.db.conn.close()
        self.tmp.cleanup()

    def start(self, step: str, scope: str | None = None) -> dict:
        return book.start(self.db, self.manuscript, step, scope)

    def finish(self, run_id: str, result: dict | None = None) -> dict:
        return book.finish(self.db, self.manuscript, run_id,
                           {**NO_USES, **(result or {})})

    def draft_running(self) -> str:
        self.finish(self.start("interview")["run"]["id"])
        return self.start("draft", "one.md")["run"]["id"]

    def test_require_document_ids_names_the_switch(self) -> None:
        self.assertTrue(docs.document_ids_enabled(self.db, self.manuscript))
        docs.set_document_ids(self.db, self.manuscript, False)
        with self.assertRaises(ValueError) as raised:
            book._require_document_ids(self.db, self.manuscript)
        msg = str(raised.exception)
        self.assertIn("document ids are off", msg)
        self.assertIn("manuscript set --document-ids on", msg)
        self.assertIn(self.manuscript["name"], msg)

    def test_find_run_exact_prefix_and_ambiguity(self) -> None:
        run_id = self.draft_running()
        row = book._find_run(self.db, self.manuscript, run_id)
        self.assertEqual(row["id"], run_id)
        self.assertEqual(
            book._find_run(self.db, self.manuscript, run_id[:8])["id"],
            run_id)
        with self.assertRaises(LookupError) as missing:
            book._find_run(self.db, self.manuscript, "sr-missing")
        self.assertIn("no step run matches", str(missing.exception))
        # Every run id is `sr-<hex>`, so the bare `sr-` prefix matches
        # more than one once a second run exists.
        self.start("draft", "two.md")
        with self.assertRaises(LookupError) as many:
            book._find_run(self.db, self.manuscript, "sr-")
        self.assertIn("more than one step run matches",
                      str(many.exception))

    def test_open_run_happy_path(self) -> None:
        run_id = self.draft_running()
        run, step = changes._open_run(
            self.db, self.manuscript, run_id, "land")
        self.assertEqual(run["id"], run_id)
        self.assertEqual(run["actor"], "agent")
        self.assertEqual(run["status"], "running")
        self.assertEqual(step.id, "draft")
        self.assertTrue(step.changes_text)

    def test_open_run_forms_mode_refuses(self) -> None:
        run_id = self.draft_running()
        book.set_agent_edits(self.db, self.manuscript, "forms")
        with self.assertRaises(ValueError) as raised:
            changes._open_run(self.db, self.manuscript, run_id, "land")
        msg = str(raised.exception)
        self.assertIn("go to the author as forms", msg)
        self.assertIn(self.manuscript["name"], msg)

    def test_open_run_person_step_refuses(self) -> None:
        run_id = self.start("interview")["run"]["id"]
        with self.assertRaises(ValueError) as raised:
            changes._open_run(self.db, self.manuscript, run_id, "draft")
        msg = str(raised.exception)
        self.assertIn("not an agent's", msg)
        self.assertIn("book draft", msg)

    def test_open_run_finished_step_refuses(self) -> None:
        run_id = self.draft_running()
        self.finish(run_id)
        with self.assertRaises(ValueError) as raised:
            changes._open_run(self.db, self.manuscript, run_id, "land")
        msg = str(raised.exception)
        self.assertIn("not running", msg)
        self.assertIn("book next", msg)

    def test_open_run_non_text_step_refuses(self) -> None:
        self.finish(self.start("interview")["run"]["id"])
        self.finish(self.start("draft", "one.md")["run"]["id"])
        run_id = self.start("critic", "one.md")["run"]["id"]
        with self.assertRaises(ValueError) as raised:
            changes._open_run(self.db, self.manuscript, run_id, "land")
        msg = str(raised.exception)
        self.assertIn("not a step that changes text", msg)
        self.assertIn("changes_text = true", msg)


if __name__ == "__main__":
    unittest.main()
