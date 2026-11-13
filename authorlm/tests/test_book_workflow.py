"""A book as a workflow of roles (book.py): each step is done by a person or
an agent, chosen per book; AuthorLM is the state machine and the record. It
says what is next, hands out the pack and records the result, and calls no
model. These tests enforce the rules the module docstring states: a step
runs only after the steps it follows; a human step waits and names the
person; a pack lists what the role is handed and never limits what it may
read, except that an independent reader is not handed earlier outputs; any
step can be restarted, and what it and its dependents produced is kept,
marked superseded and offered to the rerun as reference with no authority.

Hermetic: a temporary workspace, no network, no model, and the notifier is
always injected or patched (the real one is never called).
Run: python3 tests/test_book_workflow.py
"""
from __future__ import annotations

import os

os.environ["AUTHORLM_CONFIG"] = "/nonexistent/authorlm-book/config.toml"
os.environ["AUTHORLM_ENV"] = "/nonexistent/authorlm-book/.env"
os.environ["AUTHORLM_CLIENT"] = "none"
os.environ["AUTHORLM_STYLE_DEFAULTS"] = \
    "/nonexistent/authorlm-book/style-defaults.toml"

import contextlib
import io
import json
import re
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import authorlm  # noqa: E402

# A script run from a worktree can import the MAIN checkout's installed
# package; these tests must exercise this checkout's code.
assert Path(authorlm.__file__).resolve().parent == HERE.parent / "authorlm", (
    f"testing the wrong package: {authorlm.__file__}")

from authorlm import api, book, docs, register  # noqa: E402
from authorlm.cli import main as cli_main  # noqa: E402

# One part holding two chapters, and a title page that is front matter.
TOC = '''[[chapter]]
file = "title.md"
matter = "front"

[[chapter]]
file = "part.md"

[[chapter]]
file = "one.md"
parent = "part.md"

[[chapter]]
file = "two.md"
parent = "part.md"
'''

FLAT_TOC = '''[[chapter]]
file = "one.md"

[[chapter]]
file = "two.md"
'''

# A five-step workflow over all three grains: small enough to walk by hand.
WORKFLOW = '''[[step]]
id = "interview"
role = "interviewer"
actor = "human:author"
grain = "book"
gives = ["register"]
returns = "the brief, as settled register entries"
leaves_alone = "the manuscript"

[[step]]
id = "draft"
role = "drafter"
actor = "agent"
grain = "chapter"
after = ["interview"]
gives = ["brief", "register", "chapter"]
returns = "the chapter drafted in its file"
leaves_alone = "the outline and every other chapter"

[[step]]
id = "critic"
role = "critic"
actor = "agent"
grain = "chapter"
after = ["draft"]
gives = ["register", "chapter"]
returns = "findings"
leaves_alone = "the chapter text"
independent = true

[[step]]
id = "part-read"
role = "author"
actor = "human:author"
grain = "part"
after = ["critic"]
gives = ["part"]
returns = "the author's verdict on the part"
leaves_alone = "nothing"

[[step]]
id = "assessment"
role = "assessor"
actor = "agent"
grain = "book"
after = ["part-read"]
gives = ["manuscript", "register"]
returns = "an assessment"
leaves_alone = "the manuscript"
'''


# What an agent step that is given the register hands back when its work
# rested on no entry: the explicit answer `book done` asks for.
NO_USES = {"register_uses": "none",
           "register_uses_reason": "this test's step rested on no entry"}


def quiet(function, *args, **kwargs):
    with contextlib.redirect_stdout(io.StringIO()):
        return function(*args, **kwargs)


class Notices:
    """The injected notifier: records what would have been sent."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def __call__(self, title: str, body: str) -> bool:
        self.sent.append((title, body))
        return True


class Workspace(unittest.TestCase):
    """One registered manuscript: a part with two chapters, collected
    once, with the five-step workflow above as its definition."""

    toc = TOC

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.workspace = base / "workspace"
        self.root = base / "book"
        self.root.mkdir()
        for name in ("title.md", "part.md", "one.md", "two.md"):
            (self.root / name).write_text(
                f"# {name}\n\n" + f"A sentence of {name}. " * 40 + "\n",
                encoding="utf-8")
        (self.root / "toc.toml").write_text(self.toc, encoding="utf-8")
        self.db = api.open_db(str(self.workspace), create=True)
        api.register_manuscript(self.db, "book", str(self.root),
                                extraction=False)
        self.manuscript = api.get_manuscript(self.db)
        quiet(api.collect, self.db, self.manuscript, {})
        self.definition = book.definition_path(self.manuscript)
        self.definition.parent.mkdir(parents=True)
        self.definition.write_text(WORKFLOW, encoding="utf-8")
        self.notices = Notices()
        self.one = docs.document_id(self.db, self.manuscript, "one.md")
        self.two = docs.document_id(self.db, self.manuscript, "two.md")
        self.part = docs.document_id(self.db, self.manuscript, "part.md")

    def tearDown(self) -> None:
        self.db.conn.close()
        self.tmp.cleanup()

    # ---- helpers over the module

    def start(self, step: str, scope: str | None = None) -> dict:
        return book.start(self.db, self.manuscript, step, scope,
                          notify=self.notices)

    def run_step(self, step: str, scope: str | None = None,
                 result: dict | None = None) -> dict:
        started = self.start(step, scope)
        return book.finish(self.db, self.manuscript, started["run"]["id"],
                           {**NO_USES, **(result or {})})

    def ready(self) -> list[tuple[str, str]]:
        return [(r["step"], r["scope"])
                for r in book.next_runs(self.db, self.manuscript)["ready"]]

    def state(self, step: str, scope: str = "book") -> str:
        board = book.status(self.db, self.manuscript)
        row = next(s for s in board["steps"] if s["id"] == step)
        return next(c for c in row["scopes"] if c["scope"] == scope)["state"]

    def run_row(self, run_id: str) -> dict:
        return book.show_run(self.db, self.manuscript, run_id)["run"]

    # ---- helpers over the command line

    def cli(self, *argv: str) -> str:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), \
                mock.patch.object(book, "send_notification", self.notices):
            cli_main(["-w", str(self.workspace), *argv])
        return out.getvalue()

    def cli_error(self, *argv: str) -> str:
        with self.assertRaises(SystemExit) as raised, \
                contextlib.redirect_stdout(io.StringIO()), \
                mock.patch.object(book, "send_notification", self.notices):
            cli_main(["-w", str(self.workspace), *argv])
        self.assertNotIn(raised.exception.code, (0, None))
        return str(raised.exception.code)


class DefaultDefinitionTests(Workspace):
    def setUp(self) -> None:
        super().setUp()
        self.definition.unlink()

    def test_init_writes_the_shipped_default_into_the_book(self):
        report = book.init_definition(self.db, self.manuscript)
        self.assertEqual(Path(report["path"]), self.definition)
        self.assertEqual(self.definition.read_text(encoding="utf-8"),
                         book.default_definition_path().read_text(
                             encoding="utf-8"))

    def test_the_default_is_the_briefed_sequence(self):
        """The sequence, grains and human steps the default must ship
        with, written out here by hand from the brief."""
        book.init_definition(self.db, self.manuscript)
        steps = book.load_definition(self.manuscript).steps
        self.assertEqual(
            [(s.id, s.actor, s.grain) for s in steps],
            [("intake-brief", "human:author", "book"),
             ("intake-sources", "agent", "book"),
             ("shape-proposal", "agent", "book"),
             ("shape", "human:author", "book"),
             ("voice-sample", "agent", "book"),
             ("voice-approval", "human:author", "book"),
             ("chapter-interview", "human:author", "chapter"),
             ("draft", "agent", "chapter"),
             ("lint", "agent", "chapter"),
             ("critic", "agent", "chapter"),
             ("consistency", "agent", "chapter"),
             ("part-read", "human:author", "part"),
             ("assessment", "agent", "book"),
             ("developmental-edit", "agent", "book"),
             ("developmental-decisions", "human:author", "book"),
             ("line-edit", "agent", "book"),
             ("copyedit", "agent", "book"),
             ("fact-check", "agent", "book"),
             ("production", "agent", "book"),
             ("proofread", "agent", "book"),
             ("cold-read", "agent", "book"),
             ("approval", "human:author", "book")])
        self.assertEqual(
            sorted(s.id for s in steps if s.independent),
            ["assessment", "cold-read", "critic", "proofread"])
        for step in steps:
            self.assertTrue(step.returns.strip(), step.id)
            self.assertTrue(step.leaves_alone.strip(), step.id)
        # Each step follows the one before it, so the book runs in order.
        self.assertEqual(steps[0].after, ())
        for before, step in zip(steps, steps[1:]):
            self.assertIn(before.id, step.after, step.id)

    def test_init_declines_to_replace_a_definition(self):
        book.init_definition(self.db, self.manuscript)
        self.definition.write_text(WORKFLOW, encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "--overwrite"):
            book.init_definition(self.db, self.manuscript)
        self.assertEqual(self.definition.read_text(encoding="utf-8"),
                         WORKFLOW)
        book.init_definition(self.db, self.manuscript, overwrite=True)
        self.assertIn("intake-brief",
                      self.definition.read_text(encoding="utf-8"))

    def test_without_a_definition_the_verbs_name_init(self):
        with self.assertRaisesRegex(LookupError, "authorlm book init"):
            book.status(self.db, self.manuscript)


class DefinitionRulesTests(Workspace):
    def rewrite(self, old: str, new: str) -> None:
        self.assertIn(old, WORKFLOW)
        self.definition.write_text(WORKFLOW.replace(old, new, 1),
                                   encoding="utf-8")

    def test_an_unknown_key_is_an_error_naming_the_key(self):
        self.rewrite('grain = "book"', 'grain = "book"\nindependant = true')
        with self.assertRaisesRegex(ValueError, "independant"):
            book.load_definition(self.manuscript)

    def test_after_must_name_a_step(self):
        self.rewrite('after = ["interview"]', 'after = ["intervew"]')
        with self.assertRaisesRegex(ValueError, "intervew"):
            book.load_definition(self.manuscript)

    def test_steps_may_not_follow_each_other_in_a_circle(self):
        self.rewrite('grain = "book"\ngives = ["register"]',
                     'grain = "book"\nafter = ["assessment"]\n'
                     'gives = ["register"]')
        with self.assertRaisesRegex(ValueError, "circle"):
            book.load_definition(self.manuscript)

    def test_an_actor_is_an_agent_or_a_named_person(self):
        self.rewrite('actor = "agent"', 'actor = "robot"')
        with self.assertRaisesRegex(ValueError, "robot"):
            book.load_definition(self.manuscript)

    def test_a_given_input_must_be_a_declared_name(self):
        self.rewrite('gives = ["manuscript", "register"]',
                     'gives = ["manuscrpt"]')
        with self.assertRaisesRegex(ValueError, "manuscrpt"):
            book.load_definition(self.manuscript)

    def test_an_independent_step_is_not_given_another_steps_result(self):
        self.rewrite('gives = ["register", "chapter"]',
                     'gives = ["register", "chapter", "draft"]')
        with self.assertRaisesRegex(ValueError, "independent"):
            book.load_definition(self.manuscript)


class OrderingTests(Workspace):
    def test_the_book_runs_in_order_across_all_three_grains(self):
        self.assertEqual(self.ready(), [("interview", "book")])
        with self.assertRaisesRegex(ValueError, "interview"):
            self.start("draft", "one.md")
        self.run_step("interview")
        self.assertEqual(self.ready(),
                         [("draft", self.one), ("draft", self.two)])
        self.run_step("draft", "one.md")
        self.assertEqual(self.ready(),
                         [("draft", self.two), ("critic", self.one)])
        self.run_step("critic", self.one)
        # The part is read only when every chapter in it is through.
        self.assertEqual(self.ready(), [("draft", self.two)])
        self.assertEqual(self.state("part-read", self.part), "blocked")
        self.run_step("draft", "two.md")
        self.run_step("critic", "two.md")
        self.assertEqual(self.ready(), [("part-read", self.part)])
        self.assertEqual(self.state("assessment"), "blocked")
        self.run_step("part-read", "part.md")
        self.assertEqual(self.ready(), [("assessment", "book")])
        self.run_step("assessment")
        self.assertEqual(self.ready(), [])
        self.assertEqual(self.state("assessment"), "done")

    def test_next_marks_nothing(self):
        book.next_runs(self.db, self.manuscript)
        book.status(self.db, self.manuscript)
        self.assertEqual(
            self.db.one("SELECT COUNT(*) AS n FROM step_runs")["n"], 0)

    def test_a_running_step_is_not_started_twice(self):
        self.run_step("interview")
        first = self.start("draft", "one.md")
        with self.assertRaisesRegex(ValueError, first["run"]["id"]):
            self.start("draft", "one.md")

    def test_a_done_step_is_run_again_only_by_restart(self):
        self.run_step("interview")
        with self.assertRaisesRegex(ValueError, "book restart interview"):
            self.start("interview")

    def test_a_chapter_step_needs_its_chapter(self):
        self.run_step("interview")
        with self.assertRaisesRegex(ValueError, "--scope"):
            self.start("draft")
        with self.assertRaisesRegex(ValueError, "title.md"):
            self.start("draft", "title.md")

    def test_done_records_the_result_once(self):
        self.run_step("interview")
        started = self.start("draft", "one.md")
        self.assertEqual(started["run"]["status"], "running")
        self.assertEqual(started["run"]["attempt"], 1)
        done = book.finish(self.db, self.manuscript, started["run"]["id"],
                           {"summary": "drafted", "artifacts": ["one.md"],
                            **NO_USES})
        self.assertEqual(done["status"], "done")
        self.assertEqual(self.run_row(done["id"])["result"],
                         {"summary": "drafted", "artifacts": ["one.md"],
                          **NO_USES})
        with self.assertRaisesRegex(ValueError, "already done"):
            book.finish(self.db, self.manuscript, done["id"], {})

    def test_a_result_must_be_an_object(self):
        started = self.start("interview")
        with self.assertRaisesRegex(ValueError, "object"):
            book.finish(self.db, self.manuscript, started["run"]["id"],
                        ["not", "an", "object"])


class FlatBookTests(Workspace):
    toc = FLAT_TOC

    def test_a_book_without_parts_is_read_as_one_part(self):
        self.run_step("interview")
        for chapter in ("one.md", "two.md"):
            self.run_step("draft", chapter)
            self.run_step("critic", chapter)
        self.assertEqual(self.ready(), [("part-read", "book")])
        self.run_step("part-read")
        self.assertEqual(self.ready(), [("assessment", "book")])


class NoChapterYetTests(Workspace):
    toc = '[[chapter]]\nfile = "title.md"\nmatter = "front"\n'

    def test_a_book_step_waits_for_the_chapters_it_follows_to_exist(self):
        """A book with no chapter yet has not had its chapters read: the
        steps that follow chapter and part steps stay blocked."""
        self.run_step("interview")
        self.assertEqual(self.ready(), [])
        self.assertEqual(self.state("assessment"), "blocked")
        with self.assertRaisesRegex(ValueError, "part-read"):
            self.start("assessment")
        board = book.status(self.db, self.manuscript)
        self.assertTrue(any("no chapter yet" in w for w in board["warnings"]))


class HumanStepTests(Workspace):
    def test_a_human_step_waits_names_the_person_and_notifies(self):
        board = book.next_runs(self.db, self.manuscript)
        self.assertEqual(board["ready"][0]["waiting_on"], "the author")
        started = self.start("interview")
        self.assertEqual(started["run"]["status"], "waiting")
        self.assertEqual(started["run"]["actor"], "human:author")
        self.assertEqual(started["waiting_on"], "the author")
        self.assertEqual(len(self.notices.sent), 1)
        title, body = self.notices.sent[0]
        self.assertIn("the author", title + body)
        self.assertIn("interview", title + body)
        status = book.status(self.db, self.manuscript)
        self.assertEqual(
            [(w["who"], w["step"], w["scope"]) for w in status["waiting_on"]],
            [("the author", "interview", "book")])
        self.assertEqual(self.state("interview"), "waiting")

    def test_an_agent_step_notifies_nobody(self):
        self.run_step("interview")
        self.notices.sent.clear()
        started = self.start("draft", "one.md")
        self.assertIsNone(started["waiting_on"])
        self.assertEqual(self.notices.sent, [])

    def test_status_says_plainly_who_is_waited_on(self):
        self.cli("book", "start", "interview")
        self.assertIn("waiting on the author: interview",
                      self.cli("book", "status"))

    def test_a_missing_notifier_never_fails_the_verb(self):
        with mock.patch.object(book.shutil, "which", return_value=None):
            self.assertFalse(book.send_notification("a title", "a body"))
            started = book.start(self.db, self.manuscript, "interview")
        self.assertEqual(started["run"]["status"], "waiting")
        self.assertFalse(started["notified"])

    def test_a_notifier_that_breaks_never_fails_the_verb(self):
        with mock.patch.object(book.shutil, "which",
                               return_value="/nonexistent/superlm-notify"):
            self.assertFalse(book.send_notification("a title", "a body"))


class AssignTests(Workspace):
    def test_assign_swaps_the_actor_and_old_runs_keep_theirs(self):
        self.run_step("interview")
        first = self.run_step("draft", "one.md")
        report = book.assign(self.db, self.manuscript, "draft",
                             "human:ghostwriter")
        self.assertEqual((report["was"], report["actor"]),
                         ("agent", "human:ghostwriter"))
        steps = {s.id: s for s in
                 book.load_definition(self.manuscript).steps}
        self.assertEqual(steps["draft"].actor, "human:ghostwriter")
        self.assertEqual(steps["critic"].actor, "agent")
        second = self.start("draft", "two.md")
        self.assertEqual(second["run"]["actor"], "human:ghostwriter")
        self.assertEqual(second["run"]["status"], "waiting")
        self.assertEqual(second["waiting_on"], "ghostwriter")
        self.assertEqual(self.run_row(first["id"])["actor"], "agent")

    def test_assign_changes_only_that_line_of_the_file(self):
        book.assign(self.db, self.manuscript, "interview", "agent")
        text = self.definition.read_text(encoding="utf-8")
        self.assertEqual(
            text, WORKFLOW.replace('actor = "human:author"',
                                   'actor = "agent"', 1))

    def test_assign_declines_an_actor_that_is_neither(self):
        with self.assertRaisesRegex(ValueError, "human:<who>"):
            book.assign(self.db, self.manuscript, "draft", "somebody")
        self.assertEqual(self.definition.read_text(encoding="utf-8"),
                         WORKFLOW)


class RestartTests(Workspace):
    def setUp(self) -> None:
        super().setUp()
        self.brief = self.run_step("interview", result={"summary": "the brief"})
        self.draft_one = self.run_step(
            "draft", "one.md", {"summary": "FIRST DRAFT OF ONE"})
        self.critic_one = self.run_step(
            "critic", "one.md", {"summary": "findings on one"})
        self.draft_two = self.run_step(
            "draft", "two.md", {"summary": "first draft of two"})

    def test_restart_supersedes_the_step_and_its_dependents_only(self):
        report = book.restart(self.db, self.manuscript, "draft", "one.md",
                              reason="the author gave the wrong year")
        self.assertEqual(
            sorted(r["id"] for r in report["superseded"]),
            sorted([self.draft_one["id"], self.critic_one["id"]]))
        for run in (self.draft_one, self.critic_one):
            row = self.run_row(run["id"])
            self.assertEqual(row["status"], "superseded")
            self.assertEqual(row["superseded_reason"],
                             "the author gave the wrong year")
        # The other chapter and the step before are untouched.
        self.assertEqual(self.run_row(self.draft_two["id"])["status"], "done")
        self.assertEqual(self.run_row(self.brief["id"])["status"], "done")
        # What was produced before is kept.
        self.assertEqual(self.run_row(self.draft_one["id"])["result"],
                         {"summary": "FIRST DRAFT OF ONE", **NO_USES})
        self.assertEqual(self.state("draft", self.one), "ready")
        self.assertEqual(self.state("critic", self.one), "blocked")

    def test_the_rerun_is_handed_the_old_result_as_reference_only(self):
        book.restart(self.db, self.manuscript, "draft", "one.md",
                     reason="the author gave the wrong year")
        again = self.start("draft", "one.md")
        self.assertEqual(again["run"]["attempt"], 2)
        pack = Path(again["pack_path"]).read_text(encoding="utf-8")
        self.assertIn("reference only", pack)
        self.assertIn("no authority", pack)
        self.assertIn("FIRST DRAFT OF ONE", pack)
        self.assertIn("the author gave the wrong year", pack)
        self.assertLess(pack.index("reference only"),
                        pack.index("FIRST DRAFT OF ONE"))
        self.assertEqual(
            self.run_row(self.draft_one["id"])["superseded_by"],
            again["run"]["id"])

    def test_a_first_run_has_no_reference_section(self):
        fresh = self.start("critic", "two.md")
        pack = Path(fresh["pack_path"]).read_text(encoding="utf-8")
        self.assertNotIn("reference only", pack)

    def test_restarting_a_book_step_supersedes_every_chapter_under_it(self):
        report = book.restart(self.db, self.manuscript, "interview",
                              reason="a flawed interview prompt")
        self.assertEqual(
            sorted(r["id"] for r in report["superseded"]),
            sorted(r["id"] for r in (self.brief, self.draft_one,
                                     self.critic_one, self.draft_two)))
        self.assertEqual(self.ready(), [("interview", "book")])

    def test_a_chapter_step_without_a_scope_restarts_every_chapter(self):
        report = book.restart(self.db, self.manuscript, "draft",
                              reason="a flawed drafting prompt")
        self.assertEqual(
            sorted(r["id"] for r in report["superseded"]),
            sorted(r["id"] for r in (self.draft_one, self.critic_one,
                                     self.draft_two)))

    def test_a_dry_run_lists_and_writes_nothing(self):
        report = book.restart(self.db, self.manuscript, "draft", "one.md",
                              reason="a look first", dry_run=True)
        self.assertEqual(len(report["superseded"]), 2)
        self.assertEqual(self.run_row(self.draft_one["id"])["status"], "done")

    def test_restart_needs_a_reason_and_something_to_restart(self):
        with self.assertRaisesRegex(ValueError, "reason"):
            book.restart(self.db, self.manuscript, "draft", "one.md",
                         reason="  ")
        with self.assertRaisesRegex(ValueError, "nothing"):
            book.restart(self.db, self.manuscript, "assessment",
                         reason="never ran")

    def test_a_wider_step_that_rested_on_a_restarted_one_is_flagged(self):
        """A restart supersedes downward only (the same scope and the
        scopes inside it); a part or book step already done on the old
        work is kept as done and flagged, so the author sees it."""
        self.run_step("critic", "two.md")
        self.run_step("part-read", "part.md")
        book.restart(self.db, self.manuscript, "critic", "one.md",
                     reason="the critic missed the thesis")
        board = book.status(self.db, self.manuscript)
        row = next(s for s in board["steps"] if s["id"] == "part-read")
        self.assertEqual(row["scopes"][0]["state"], "done")
        self.assertTrue(row["scopes"][0]["rests_on_restarted"])


class PackTests(Workspace):
    def setUp(self) -> None:
        super().setUp()
        entry = register.add(
            self.db, self.manuscript, "reader", "newcomer",
            author_words="Someone who has never meditated.",
            reading="The reader is new to the practice.")
        register.settle(self.db, self.manuscript, entry["id"])
        thesis = register.add(
            self.db, self.manuscript, "thesis", "two",
            author_words="Chapter two is about posture.",
            reading="The second chapter argues posture comes first.",
            scope="two.md")
        register.settle(self.db, self.manuscript, thesis["id"])
        self.run_step("interview", result={"summary": "THE BRIEF AS ANSWERED"})

    def pack(self, step: str, scope: str | None = None) -> str:
        return Path(self.start(step, scope)["pack_path"]).read_text(
            encoding="utf-8")

    def test_the_pack_states_the_contract(self):
        pack = self.pack("draft", "one.md")
        self.assertIn("drafter", pack)
        self.assertIn("the chapter drafted in its file", pack)
        self.assertIn("the outline and every other chapter", pack)
        self.assertIn(str(self.root / "one.md"), pack)
        # No walls: the pack is what the role is handed, not a limit.
        self.assertIn("may read the whole manuscript", pack)

    def test_the_pack_carries_the_register_in_scope(self):
        pack = self.pack("draft", "one.md")
        self.assertIn("Someone who has never meditated.", pack)
        self.assertIn("The reader is new to the practice.", pack)
        self.assertIn("applies", pack)
        self.assertIn("infers", pack)
        # Another chapter's thesis is not this chapter's.
        self.assertNotIn("Chapter two is about posture.", pack)
        self.assertIn("Chapter two is about posture.",
                      self.pack("draft", "two.md"))

    def test_a_step_is_handed_what_the_steps_before_returned(self):
        self.assertIn("THE BRIEF AS ANSWERED", self.pack("draft", "one.md"))

    def test_an_independent_step_is_not_handed_earlier_outputs(self):
        self.run_step("draft", "one.md", {"summary": "DRAFTING NOTES ON ONE"})
        pack = self.pack("critic", "one.md")
        self.assertNotIn("DRAFTING NOTES ON ONE", pack)
        self.assertIn("independent", pack)
        self.assertIn("withheld", pack)

    def test_an_independent_rerun_is_not_handed_its_own_earlier_read(self):
        self.run_step("draft", "one.md")
        self.run_step("critic", "one.md", {"summary": "THE FIRST VERDICT"})
        book.restart(self.db, self.manuscript, "critic", "one.md",
                     reason="a second cold read")
        pack = self.pack("critic", "one.md")
        self.assertNotIn("THE FIRST VERDICT", pack)
        self.assertIn("withheld", pack)


class RegisterUsesOnDoneTests(Workspace):
    """What rested on the register is not left to the agent's memory: an
    agent step that is given the register cannot be marked done without
    saying which entries its work rested on, or that it rested on none."""

    def setUp(self) -> None:
        super().setUp()
        entry = register.add(
            self.db, self.manuscript, "reader", "newcomer",
            author_words="Someone who has never meditated.",
            reading="The reader is new to the practice.")
        self.entry = register.settle(self.db, self.manuscript, entry["id"])
        self.run_step("interview")
        self.run_id = self.start("draft", "one.md")["run"]["id"]

    def finish(self, result: dict) -> dict:
        return book.finish(self.db, self.manuscript, self.run_id, result)

    def status_of_run(self) -> str:
        return self.run_row(self.run_id)["status"]

    def test_done_is_declined_without_the_key_and_names_it(self):
        with self.assertRaisesRegex(ValueError, "register_uses"):
            self.finish({"summary": "drafted"})
        self.assertEqual(self.status_of_run(), "running")

    def test_the_uses_are_written_under_the_run(self):
        self.finish({"summary": "drafted", "register_uses": [
            {"entry": "reader/newcomer", "mode": "applies",
             "note": "the opening assumes no practice"},
            {"entry": self.entry["id"], "mode": "infers",
             "note": "left the Pali terms out", "document": "one.md"}]})
        self.assertEqual(self.status_of_run(), "done")
        uses = register.uses(self.db, self.manuscript, "reader/newcomer")
        self.assertEqual(
            [(u["mode"], u["note"], u["step_run_id"], u["step"], u["actor"])
             for u in uses],
            [("applies", "the opening assumes no practice", self.run_id,
              "draft", "agent:drafter"),
             ("infers", "left the Pali terms out", self.run_id, "draft",
              "agent:drafter")])
        self.assertIsNone(uses[0]["document_id"])
        self.assertEqual(uses[1]["document_id"], self.one)
        shown = book.show_run(self.db, self.manuscript, self.run_id)
        self.assertEqual([u["mode"] for u in shown["uses"]],
                         ["applies", "infers"])

    def test_none_needs_a_reason(self):
        with self.assertRaisesRegex(ValueError, "register_uses_reason"):
            self.finish({"register_uses": "none"})
        with self.assertRaisesRegex(ValueError, "register_uses_reason"):
            self.finish({"register_uses": "none",
                         "register_uses_reason": "  "})
        self.assertEqual(self.status_of_run(), "running")
        self.finish({"register_uses": "none",
                     "register_uses_reason": "a mechanical pass only"})
        self.assertEqual(self.status_of_run(), "done")
        self.assertEqual(
            register.uses(self.db, self.manuscript, "reader/newcomer"), [])

    def test_an_empty_list_is_not_an_answer(self):
        with self.assertRaisesRegex(ValueError, "none"):
            self.finish({"register_uses": []})
        self.assertEqual(self.status_of_run(), "running")

    def test_an_entry_that_is_not_settled_is_declined_and_nothing_written(
            self):
        register.add(self.db, self.manuscript, "voice", "plain",
                     author_words="Plain.", reading="Plain words.")
        with self.assertRaisesRegex(ValueError, "not settled"):
            self.finish({"register_uses": [
                {"entry": "reader/newcomer", "mode": "applies",
                 "note": "the opening"},
                {"entry": "voice/plain", "mode": "applies",
                 "note": "short sentences"}]})
        self.assertEqual(self.status_of_run(), "running")
        self.assertEqual(
            register.uses(self.db, self.manuscript, "reader/newcomer"), [])

    def test_each_use_says_applies_or_infers_and_what_rests_on_it(self):
        for item, word in (
                ({"entry": "reader/newcomer", "mode": "follows",
                  "note": "x"}, "applies"),
                ({"entry": "reader/newcomer", "mode": "infers"}, "note"),
                ({"mode": "infers", "note": "x"}, "entry"),
                ("reader/newcomer", "object")):
            with self.assertRaisesRegex(ValueError, word):
                self.finish({"register_uses": [item]})
        self.assertEqual(self.status_of_run(), "running")

    def test_a_persons_step_is_exempt(self):
        book.finish(self.db, self.manuscript, self.run_id, NO_USES)
        self.run_step("critic", "one.md")
        self.run_step("draft", "two.md")
        self.run_step("critic", "two.md")
        started = self.start("part-read", "part.md")
        done = book.finish(self.db, self.manuscript, started["run"]["id"],
                           {"summary": "the author's verdict"})
        self.assertEqual(done["status"], "done")

    def test_a_step_not_given_the_register_is_exempt(self):
        text = self.definition.read_text(encoding="utf-8")
        self.definition.write_text(
            text.replace('gives = ["brief", "register", "chapter"]',
                         'gives = ["chapter"]'), encoding="utf-8")
        self.assertEqual(self.finish({"summary": "drafted"})["status"],
                         "done")

    def test_the_pack_states_the_requirement_beside_the_register(self):
        pack = Path(book.pack_path(self.manuscript, self.run_id)).read_text(
            encoding="utf-8")
        self.assertIn("register_uses", pack)
        self.assertIn("register_uses_reason", pack)
        self.assertIn("An inference is fine", pack)
        self.assertLess(pack.index("## The register"),
                        pack.index("register_uses"))
        self.assertLess(pack.index("register_uses"),
                        pack.index("## From the steps before"))

    def test_register_show_names_the_step_and_run_of_each_use(self):
        self.finish({"register_uses": [
            {"entry": "reader/newcomer", "mode": "infers",
             "note": "left the Pali terms out"}]})
        for verb in ("show", "uses"):
            text = self.cli("register", verb, "reader/newcomer")
            self.assertRegex(
                text, rf"infers\b.*draft.*{self.run_id}.*left the Pali")

    def test_the_verb_declines_naming_what_is_missing(self):
        result = self.root / "_book" / "result.json"
        result.write_text(json.dumps({"summary": "drafted"}),
                          encoding="utf-8")
        message = self.cli_error("book", "done", self.run_id,
                                 "--result-json", str(result))
        self.assertIn("error:", message)
        self.assertIn("register_uses", message)


class HistoryTests(Workspace):
    """The log of status changes is the record of what happened: one row
    per change, written with the change, never altered."""

    def events(self, **filters) -> list[tuple]:
        return [(e["step"], e["scope"], e["from_status"], e["to_status"])
                for e in book.history(self.db, self.manuscript,
                                      **filters)["events"]]

    def test_every_status_change_writes_one_event_in_time_order(self):
        self.run_step("interview")
        self.run_step("draft", "one.md")
        book.restart(self.db, self.manuscript, "draft", "one.md",
                     reason="the author gave the wrong year")
        self.start("draft", "one.md")
        self.assertEqual(self.events(), [
            ("interview", "book", None, "waiting"),
            ("interview", "book", "waiting", "done"),
            ("draft", self.one, None, "running"),
            ("draft", self.one, "running", "done"),
            ("draft", self.one, "done", "superseded"),
            ("draft", self.one, None, "running")])

    def test_a_restart_writes_one_event_per_superseded_run_with_its_reason(
            self):
        self.run_step("interview")
        draft = self.run_step("draft", "one.md")
        critic = self.run_step("critic", "one.md")
        book.restart(self.db, self.manuscript, "draft", "one.md",
                     reason="the author gave the wrong year")
        superseded = [e for e in book.history(
            self.db, self.manuscript)["events"]
            if e["to_status"] == "superseded"]
        self.assertEqual(sorted(e["run"] for e in superseded),
                         sorted([draft["id"], critic["id"]]))
        self.assertEqual({e["reason"] for e in superseded},
                         {"the author gave the wrong year"})

    def test_no_run_has_a_status_its_last_event_does_not_give(self):
        self.run_step("interview")
        self.run_step("draft", "one.md")
        failing = self.start("critic", "one.md")
        book.fail(self.db, self.manuscript, failing["run"]["id"],
                  reason="the subagent stopped")
        self.start("draft", "two.md")
        book.restart(self.db, self.manuscript, "interview",
                     reason="a flawed prompt")
        last: dict[str, str] = {}
        for event in book.history(self.db, self.manuscript)["events"]:
            self.assertEqual(event["from_status"], last.get(event["run"]))
            last[event["run"]] = event["to_status"]
        rows = self.db.all("SELECT id, status FROM step_runs")
        self.assertEqual({r["id"]: r["status"] for r in rows}, last)
        self.assertEqual(len(rows), 4)

    def test_only_one_function_writes_a_runs_status(self):
        """Editing this test is the act that lets a second place change a
        run's status, and so change it without an event."""
        source = Path(book.__file__).read_text(encoding="utf-8")
        start = source.index("\ndef _transition(")
        end = source.index("\ndef ", start + 1)
        outside = source[:start] + source[end:]
        self.assertNotIn('db.insert("step_runs"', outside)
        self.assertNotIn('db.insert("step_events"', outside)
        for call in re.findall(r'db\.update\("step_runs".*?\)\n', outside,
                               re.S):
            self.assertNotIn("status", call)
        self.assertNotRegex(source, r'(?i)(UPDATE|DELETE FROM) step_events')
        self.assertNotIn('db.update("step_events"', source)

    def test_history_narrows_by_step_and_scope(self):
        self.run_step("interview")
        self.run_step("draft", "one.md")
        self.run_step("draft", "two.md")
        self.assertEqual(len(self.events(step="draft")), 4)
        self.assertEqual(
            self.events(scope="two.md"),
            [("draft", self.two, None, "running"),
             ("draft", self.two, "running", "done")])
        board = json.loads(self.cli("book", "history", "--step", "interview",
                                    "--json"))
        self.assertEqual([e["to_status"] for e in board["events"]],
                         ["waiting", "done"])
        self.assertIn("next", board)
        self.assertIn("waiting", self.cli("book", "history"))


class FailedRunTests(Workspace):
    def test_a_failed_run_blocks_what_follows_and_is_started_again(self):
        self.run_step("interview")
        first = self.start("draft", "one.md")
        failed = book.fail(self.db, self.manuscript, first["run"]["id"],
                           reason="the subagent stopped")
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(self.state("draft", self.one), "failed")
        self.assertEqual(self.state("critic", self.one), "blocked")
        self.assertIn(("draft", self.one), self.ready())
        again = self.start("draft", "one.md")
        self.assertEqual(again["run"]["attempt"], 2)
        self.assertIn("the subagent stopped",
                      Path(again["pack_path"]).read_text(encoding="utf-8"))
        with self.assertRaisesRegex(ValueError, "reason"):
            book.fail(self.db, self.manuscript, again["run"]["id"],
                      reason="")


class DefinitionVersionTests(Workspace):
    def rewrite(self, old: str, new: str) -> None:
        text = self.definition.read_text(encoding="utf-8")
        self.assertIn(old, text)
        self.definition.write_text(text.replace(old, new, 1),
                                   encoding="utf-8")

    def cell(self, step: str, scope: str) -> dict:
        board = book.status(self.db, self.manuscript)
        row = next(s for s in board["steps"] if s["id"] == step)
        return next(c for c in row["scopes"] if c["scope"] == scope)

    def setUp(self) -> None:
        super().setUp()
        self.run_step("interview")
        self.draft = self.run_step("draft", "one.md")

    def test_a_run_records_the_definition_and_contract_it_started_under(self):
        row = self.run_row(self.draft["id"])
        self.assertRegex(row["definition_hash"], r"^[0-9a-f]{12}$")
        self.assertRegex(row["contract_hash"], r"^[0-9a-f]{12}$")
        self.assertFalse(self.cell("draft", self.one)["contract_changed"])

    def test_status_says_when_a_run_was_made_under_an_earlier_contract(self):
        self.rewrite('returns = "the chapter drafted in its file"',
                     'returns = "the chapter drafted, and a list of gaps"')
        self.assertTrue(self.cell("draft", self.one)["contract_changed"])
        self.assertFalse(self.cell("interview", "book")["contract_changed"])
        self.assertIn("earlier version of this step's contract",
                      self.cli("book", "status"))
        later = self.run_step("draft", "two.md")
        self.assertNotEqual(self.run_row(later["id"])["contract_hash"],
                            self.run_row(self.draft["id"])["contract_hash"])
        self.assertFalse(self.cell("draft", self.two)["contract_changed"])

    def test_steps_are_matched_by_id_not_by_position(self):
        """A new step put in front of the others moves every position and
        changes the file, and no existing run is disturbed."""
        added = ('[[step]]\nid = "welcome"\nrole = "host"\nactor = "agent"\n'
                 'grain = "book"\nreturns = "a welcome"\n'
                 'leaves_alone = "everything"\n\n')
        self.definition.write_text(
            added + self.definition.read_text(encoding="utf-8"),
            encoding="utf-8")
        self.assertEqual(self.state("interview"), "done")
        self.assertEqual(self.state("draft", self.one), "done")
        self.assertFalse(self.cell("draft", self.one)["contract_changed"])
        later = self.run_step("draft", "two.md")
        self.assertNotEqual(self.run_row(later["id"])["definition_hash"],
                            self.run_row(self.draft["id"])["definition_hash"])
        self.assertEqual(self.run_row(later["id"])["contract_hash"],
                         self.run_row(self.draft["id"])["contract_hash"])

    def test_a_change_of_actor_is_not_a_change_of_contract(self):
        book.assign(self.db, self.manuscript, "draft", "human:ghostwriter")
        self.assertFalse(self.cell("draft", self.one)["contract_changed"])

    def test_a_run_of_a_step_no_longer_defined_is_named(self):
        text = self.definition.read_text(encoding="utf-8")
        self.definition.write_text(
            text.replace('id = "draft"', 'id = "write"')
                .replace('after = ["draft"]', 'after = ["write"]'),
            encoding="utf-8")
        board = book.status(self.db, self.manuscript)
        self.assertTrue(any(self.draft["id"] in w and "draft" in w
                            for w in board["warnings"]))


class DiagramTests(Workspace):
    """The diagram is drawn from the definition file and the runs, as
    Mermaid flowchart text. It is checked here by structure only: nothing
    in this suite renders it."""

    def diagram(self, scope: str | None = None) -> dict:
        return book.diagram(self.db, self.manuscript, scope)

    def node(self, step: str, scope: str | None = None) -> dict:
        return next(n for n in self.diagram(scope)["nodes"]
                    if n["step"] == step)

    def test_it_is_a_mermaid_flowchart_by_structure(self):
        self.run_step("interview")
        self.run_step("draft", "one.md")
        board = self.diagram("one.md")
        lines = [line.strip() for line in board["mermaid"].splitlines()
                 if line.strip()]
        self.assertEqual(lines[0], "flowchart TD")
        self.assertEqual(sum(l.startswith("subgraph ") for l in lines),
                         sum(l == "end" for l in lines))
        self.assertEqual(sum(l.startswith("subgraph ") for l in lines), 3)
        declared = set()
        used_classes = set()
        defined_classes = set()
        edges = []
        name = r"[A-Za-z_][A-Za-z0-9_]*"
        shapes = (rf'({name})\["[^"]*"\]', rf'({name})\(\["[^"]*"\]\)',
                  rf'({name})\{{\{{"[^"]*"\}}\}}')
        for line in lines[1:]:
            if line == "end" or re.fullmatch(
                    rf'subgraph {name}\["[^"]*"\]', line):
                continue
            node = next((m for shape in shapes
                         if (m := re.fullmatch(shape, line))), None)
            edge = re.fullmatch(rf"({name}) --> ({name})", line)
            class_def = re.fullmatch(rf"classDef ({name}) [a-z\-:#0-9,. ]+",
                                     line)
            class_use = re.fullmatch(rf"class ({name}) ({name})", line)
            if node:
                declared.add(node.group(1))
            elif edge:
                edges.append(edge.groups())
            elif class_def:
                defined_classes.add(class_def.group(1))
            elif class_use:
                self.assertIn(class_use.group(1), declared, line)
                used_classes.add(class_use.group(2))
            else:
                self.fail(f"not a line of a Mermaid flowchart: {line}")
        self.assertEqual(len(declared), 5)
        for left, right in edges:
            self.assertIn(left, declared)
            self.assertIn(right, declared)
        self.assertLessEqual(used_classes, defined_classes)
        self.assertLessEqual({"blocked", "ready", "running", "waiting",
                              "done", "failed", "reran", "human",
                              "independent"}, defined_classes)
        # Superseded is a run's status, never a step's state on the board.
        self.assertNotIn("superseded", defined_classes)

    def test_nodes_and_edges_come_from_the_definition(self):
        board = self.diagram()
        self.assertEqual(
            [(n["step"], n["grain"], n["actor"], n["independent"])
             for n in board["nodes"]],
            [("interview", "book", "human:author", False),
             ("draft", "chapter", "agent", False),
             ("critic", "chapter", "agent", True),
             ("part-read", "part", "human:author", False),
             ("assessment", "book", "agent", False)])
        self.assertEqual(
            [(e["from"], e["to"]) for e in board["edges"]],
            [("interview", "draft"), ("draft", "critic"),
             ("critic", "part-read"), ("part-read", "assessment")])
        drawn = re.findall(r"(?m)^\s*(\w+) --> (\w+)$", board["mermaid"])
        node_of = {n["step"]: n["node"] for n in board["nodes"]}
        self.assertEqual(drawn, [(node_of[e["from"]], node_of[e["to"]])
                                 for e in board["edges"]])
        # With no scope there is no live state to show, only the marks
        # for a person's step and an independent step.
        self.assertEqual({n["status"] for n in board["nodes"]}, {None})
        used = set(re.findall(r"(?m)^\s*class \w+ (\w+)$", board["mermaid"]))
        self.assertEqual(used, {"human", "independent"})

    def test_a_human_step_and_an_independent_step_are_marked(self):
        text = self.diagram()["mermaid"]
        human = self.node("interview")["node"]
        independent = self.node("critic")["node"]
        agent = self.node("draft")["node"]
        self.assertIn(f'{human}(["', text)
        self.assertIn(f'{independent}{{{{"', text)
        self.assertIn(f'{agent}["', text)
        self.assertIn("human:author", text)
        self.assertIn("independent", text)

    def test_adding_a_step_changes_the_diagram(self):
        before = self.diagram()["mermaid"]
        with self.definition.open("a", encoding="utf-8") as handle:
            handle.write('\n[[step]]\nid = "approval"\nrole = "author"\n'
                         'actor = "human:author"\ngrain = "book"\n'
                         'after = ["assessment"]\nreturns = "approval"\n'
                         'leaves_alone = "nothing"\n')
        after = self.diagram()
        self.assertNotEqual(after["mermaid"], before)
        self.assertEqual(after["nodes"][-1]["step"], "approval")
        self.assertIn({"from": "assessment", "to": "approval"},
                      after["edges"])

    def test_reassigning_an_actor_changes_the_diagram(self):
        before = self.diagram()["mermaid"]
        book.assign(self.db, self.manuscript, "draft", "human:ghostwriter")
        after = self.diagram()["mermaid"]
        self.assertNotEqual(after, before)
        self.assertIn(f'{self.node("draft")["node"]}(["', after)
        self.assertIn("human:ghostwriter", after)

    def test_a_node_carries_its_status_in_the_scope_asked_for(self):
        self.assertEqual(self.node("interview", "one.md")["status"], "ready")
        self.assertEqual(self.node("draft", "one.md")["status"], "blocked")
        before = self.diagram("one.md")["mermaid"]
        started = self.start("interview")
        self.assertEqual(self.node("interview", "one.md")["status"],
                         "waiting")
        self.assertNotEqual(self.diagram("one.md")["mermaid"], before)
        book.finish(self.db, self.manuscript, started["run"]["id"], {})
        self.run_step("draft", "one.md")
        board = self.diagram("one.md")
        status = {n["step"]: n["status"] for n in board["nodes"]}
        self.assertEqual(status, {"interview": "done", "draft": "done",
                                  "critic": "ready", "part-read": "blocked",
                                  "assessment": "blocked"})
        self.assertIn(f'class {self.node("draft")["node"]} done',
                      board["mermaid"])
        # The other chapter has its own state.
        self.assertEqual(self.node("draft", "two.md")["status"], "ready")
        book.restart(self.db, self.manuscript, "draft", "one.md",
                     reason="again")
        # A restarted step is ready again; that it ran before is a second
        # mark, not its state.
        restarted = self.node("draft", "one.md")
        self.assertEqual(restarted["status"], "ready")
        self.assertEqual(restarted["earlier_attempts"], 1)
        text = self.diagram("one.md")["mermaid"]
        self.assertIn(f'class {restarted["node"]} ready', text)
        self.assertIn(f'class {restarted["node"]} reran', text)
        failing = self.start("draft", "one.md")
        book.fail(self.db, self.manuscript, failing["run"]["id"],
                  reason="stopped")
        self.assertEqual(self.node("draft", "one.md")["status"], "failed")

    def test_after_a_restart_the_diagram_says_what_status_says(self):
        """Reported defect: after `book restart` of a step whose follower
        was running, the diagram called both nodes superseded while
        `book status` said ready and blocked."""
        self.run_step("interview")
        self.start("draft", "one.md")
        book.restart(self.db, self.manuscript, "interview", reason="x")
        self.assertEqual(self.state("interview"), "ready")
        self.assertEqual(self.state("draft", self.one), "blocked")
        board = self.diagram("one.md")
        status = {n["step"]: n["status"] for n in board["nodes"]}
        self.assertEqual(status["interview"], "ready")
        self.assertEqual(status["draft"], "blocked")
        self.assertNotIn("superseded", board["mermaid"])
        self.assertIn("attempt 2", board["mermaid"])

    def test_every_node_carries_the_state_the_status_board_gives(self):
        self.run_step("interview")
        self.run_step("draft", "one.md")
        failing = self.start("critic", "one.md")
        book.fail(self.db, self.manuscript, failing["run"]["id"],
                  reason="stopped")
        self.start("draft", "two.md")
        book.restart(self.db, self.manuscript, "draft", "one.md",
                     reason="again")
        status = json.loads(self.cli("book", "status", "--json"))
        on_board = {(s["id"], c["scope"]): (c["state"], c["run"])
                    for s in status["steps"] for c in s["scopes"]}
        for scope, part in ((self.one, self.part), (self.two, self.part)):
            keys = {"book": "book", "part": part, "chapter": scope}
            drawn = json.loads(self.cli("book", "diagram", "--scope", scope,
                                        "--json"))
            for node in drawn["nodes"]:
                self.assertEqual(
                    (node["status"], node["run"]),
                    on_board[(node["step"], keys[node["grain"]])],
                    (node["step"], scope))

    def test_the_verb_prints_mermaid_or_the_parts_as_json(self):
        self.assertTrue(self.cli("book", "diagram").startswith(
            "flowchart TD\n"))
        board = json.loads(self.cli("book", "diagram", "--scope", "one.md",
                                    "--json"))
        self.assertEqual(board["scope"], self.one)
        self.assertEqual(len(board["nodes"]), 5)
        self.assertIn("mermaid", board)
        self.assertIn("next", board)


class DocumentIdsTests(Workspace):
    def test_book_verbs_decline_while_document_ids_are_off(self):
        docs.set_document_ids(self.db, self.manuscript, False)
        for argv in (("book", "status"), ("book", "next"),
                     ("book", "init"), ("book", "start", "interview"),
                     ("book", "restart", "interview", "--reason", "x"),
                     ("book", "assign", "interview", "agent"),
                     ("book", "done", "sr-000000000000"),
                     ("book", "show", "sr-000000000000"),
                     ("book", "fail", "sr-000000000000", "--reason", "x"),
                     ("book", "history"), ("book", "diagram")):
            message = self.cli_error(*argv)
            self.assertIn("error:", message, argv)
            self.assertIn("authorlm manuscript set --document-ids on",
                          message, argv)
        self.assertEqual(
            self.db.one("SELECT COUNT(*) AS n FROM step_runs")["n"], 0)


class VerbTests(Workspace):
    def test_boards_parse_and_carry_next(self):
        status = json.loads(self.cli("book", "status", "--json"))
        self.assertEqual([s["id"] for s in status["steps"]],
                         ["interview", "draft", "critic", "part-read",
                          "assessment"])
        self.assertIn("next", status)
        upcoming = json.loads(self.cli("book", "next", "--json"))
        self.assertEqual([r["step"] for r in upcoming["ready"]], ["interview"])
        self.assertIn("authorlm book start interview", upcoming["next"])

    def test_start_prints_the_pack_path_and_done_takes_a_result_file(self):
        started = json.loads(self.cli("book", "start", "interview", "--json"))
        run_id = started["run"]["id"]
        self.assertTrue(run_id.startswith("sr-"))
        self.assertTrue(Path(started["pack_path"]).is_file())
        text = self.cli("book", "restart", "interview", "--reason", "again",
                        "--dry-run")
        self.assertIn(run_id, text)
        result = self.root / "_book" / "result.json"
        result.write_text(json.dumps({"summary": "answered"}),
                          encoding="utf-8")
        self.cli("book", "done", run_id, "--result-json", str(result))
        shown = json.loads(self.cli("book", "show", run_id, "--json"))
        self.assertEqual(shown["run"]["status"], "done")
        self.assertEqual(shown["run"]["result"], {"summary": "answered"})

    def test_start_in_plain_text_points_at_the_pack(self):
        text = self.cli("book", "start", "interview")
        self.assertIn("waiting on the author", text)
        self.assertRegex(text, r"Read .*_book/packs/sr-[0-9a-f]+\.md")

    def test_restart_prints_exactly_what_was_superseded(self):
        self.cli("book", "start", "interview")
        run_id = self.db.one("SELECT id FROM step_runs")["id"]
        self.cli("book", "done", run_id)
        text = self.cli("book", "restart", "interview", "--reason",
                        "wrong information")
        self.assertIn("superseded 1", text)
        self.assertIn(run_id, text)

    def test_assign_by_verb(self):
        self.cli("book", "assign", "draft", "human:author")
        self.assertIn('actor = "human:author"\ngrain = "chapter"',
                      self.definition.read_text(encoding="utf-8"))

    def test_an_unknown_step_exits_with_an_error(self):
        message = self.cli_error("book", "start", "polish")
        self.assertIn("error:", message)
        self.assertIn("polish", message)

    def test_the_default_parses_as_toml_with_only_declared_keys(self):
        data = tomllib.loads(book.default_definition_path().read_text(
            encoding="utf-8"))
        self.assertEqual(set(data), {"step"})
        for step in data["step"]:
            self.assertLessEqual(set(step), set(book.STEP_KEYS), step["id"])


if __name__ == "__main__":
    unittest.main()
