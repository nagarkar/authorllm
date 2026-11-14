"""Agent edits that land directly, with a change list (changes.py, and the
`agent_edits` setting and `changes_text` step key in book.py). These
tests enforce what the two module docstrings say: the setting is per
manuscript and absent means forms; only a running agent step whose
definition says `changes_text = true` lands a change, inside its scope,
all or none; every change is a row keyed by document id that the author
can list and put back; a first draft is an artifact and an event, not a
change; the author's packs count the changes; and each role file says the
same.

Hermetic: a temporary workspace, no network, no model. No test reaches
Google: the manuscripts have no Doc, or the tab's state is passed in.
Run: python3 tests/test_book_changes.py
"""
from __future__ import annotations

import os

os.environ["AUTHORLM_CONFIG"] = "/nonexistent/authorlm-changes/config.toml"
os.environ["AUTHORLM_ENV"] = "/nonexistent/authorlm-changes/.env"
os.environ["AUTHORLM_CLIENT"] = "none"
os.environ["AUTHORLM_STYLE_DEFAULTS"] = \
    "/nonexistent/authorlm-changes/style-defaults.toml"

import json
import re
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import authorlm  # noqa: E402

# A script run from a worktree can import the MAIN checkout's installed
# package; these tests must exercise this checkout's code.
assert Path(authorlm.__file__).resolve().parent == HERE.parent / "authorlm", (
    f"testing the wrong package: {authorlm.__file__}")

from authorlm import api, book, changes, docs  # noqa: E402

from test_book_workflow import NO_USES, Workspace, quiet  # noqa: E402

AGENTS = HERE.parent.parent / ".claude" / "agents"
# Words the owner has ruled out of everything written for him.
RULED_OUT = re.compile(r"\brefus(e|es|ed|al|als|ing)\b", re.IGNORECASE)

# Seven steps over all three grains: two that change text per chapter, one
# that does not, the author's read of the part, one that changes text for
# the whole book, and the author's approval.
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
id = "lint"
role = "linter"
actor = "agent"
grain = "chapter"
after = ["draft"]
gives = ["chapter"]
returns = "findings, and the fixes made"
leaves_alone = "what the chapter claims"
changes_text = true

[[step]]
id = "critic"
role = "critic"
actor = "agent"
grain = "chapter"
after = ["lint"]
gives = ["chapter"]
returns = "findings"
leaves_alone = "the chapter text"
independent = true

[[step]]
id = "part-read"
role = "author"
actor = "human:author"
grain = "part"
after = ["critic"]
gives = ["part", "changes"]
returns = "the author's verdict on the part"
leaves_alone = "nothing"

[[step]]
id = "line-edit"
role = "line editor"
actor = "agent"
grain = "book"
after = ["part-read"]
gives = ["manuscript"]
returns = "the prose edited, with a list of the changes"
leaves_alone = "structure"
changes_text = true

[[step]]
id = "approval"
role = "author"
actor = "human:author"
grain = "book"
after = ["line-edit"]
gives = ["changes"]
returns = "the author's approval"
leaves_alone = "nothing"
'''

ONE = ("# One\n\nThe river ran teh long way round the hill. Nobody in the "
       "village minded the delay.\n\nWhat the miller wanted was a shorter "
       "road to market. He never got it.\n")
TWO = ("# Two\n\nThe second winter came early, and the mill stood idle "
       "until the thaw.\n\nIn the spring the miller walked to market on "
       "the old road.\n")

TYPO = {"old": "ran teh long way", "new": "ran the long way",
        "note": "Typo: 'teh'. Spelling, style law default:spelling."}
MINDED = {"old": "Nobody in the village minded the delay",
          "new": "No one in the village minded the delay",
          "note": "The voice entry prefers 'no one'."}


def unlinked(_relpath: str) -> str:
    return "unlinked"


class Book(Workspace):
    """The workspace of the workflow tests, with distinct chapter text,
    the seven-step workflow above and `agent_edits` set to direct."""

    mode = "direct"

    def setUp(self) -> None:
        super().setUp()
        (self.root / "one.md").write_text(ONE, encoding="utf-8")
        (self.root / "two.md").write_text(TWO, encoding="utf-8")
        quiet(api.collect, self.db, self.manuscript, {})
        self.definition.write_text(WORKFLOW, encoding="utf-8")
        if self.mode == "direct":
            book.set_agent_edits(self.db, self.manuscript, "direct")

    def text(self, name: str = "one.md") -> str:
        return (self.root / name).read_text(encoding="utf-8")

    def running(self, step: str, scope: str | None = None) -> str:
        return self.start(step, scope)["run"]["id"]

    def lint_run(self, chapter: str = "one.md") -> str:
        """A running lint step for `chapter`, with what precedes it done."""
        if self.state("interview") != "done":
            self.run_step("interview")
        self.run_step("draft", chapter)
        return self.running("lint", chapter)

    def land(self, run_id: str, file: str, items: list[dict], **kw) -> dict:
        return changes.land(self.db, self.manuscript, {}, run_id, file,
                            items, tab_state=kw.pop("tab_state", unlinked),
                            **kw)

    def rows(self) -> list[dict]:
        return [dict(r) for r in self.db.all(
            "SELECT * FROM step_changes ORDER BY created_at, seq")]

    def listed(self, **filters) -> list[dict]:
        return changes.list_changes(self.db, self.manuscript,
                                    **filters)["changes"]

    def to_the_part_read(self) -> None:
        """Every chapter step done for both chapters; one change landed
        in each by its lint run."""
        for chapter, item in (("one.md", TYPO), ("two.md", {
                "old": "stood idle until the thaw",
                "new": "stood still until the thaw",
                "note": "The term entry keeps 'idle' for people."})):
            run = self.lint_run(chapter)
            self.land(run, chapter, [item])
            book.finish(self.db, self.manuscript, run, NO_USES)
            self.run_step("critic", chapter)


# ------------------------------------------------------------------ setting

class SettingTests(Book):
    mode = "forms"

    def test_the_setting_defaults_to_forms_and_round_trips(self):
        self.assertEqual(book.agent_edits(self.db, self.manuscript), "forms")
        self.assertEqual(
            book.status(self.db, self.manuscript)["agent_edits"], "forms")
        report = book.set_agent_edits(self.db, self.manuscript, "direct")
        self.assertEqual(report, {"mode": "direct", "was": "forms",
                                  "changed": True})
        self.assertEqual(book.agent_edits(self.db, self.manuscript),
                         "direct")
        # Read from the row, by a second look at the manuscript.
        again = api.get_manuscript(self.db)
        self.assertEqual(book.agent_edits(self.db, again), "direct")
        book.set_agent_edits(self.db, self.manuscript, "forms")
        self.assertEqual(book.agent_edits(self.db, again), "forms")

    def test_the_setting_sits_beside_the_others_in_the_metadata(self):
        book.set_agent_edits(self.db, self.manuscript, "direct")
        self.assertTrue(docs.document_ids_enabled(self.db, self.manuscript))
        docs.set_document_ids(self.db, self.manuscript, True)
        self.assertEqual(book.agent_edits(self.db, self.manuscript),
                         "direct")

    def test_only_the_two_values_are_taken(self):
        with self.assertRaisesRegex(ValueError, "forms or direct"):
            book.set_agent_edits(self.db, self.manuscript, "sometimes")
        self.assertEqual(book.agent_edits(self.db, self.manuscript), "forms")

    def test_the_verb_sets_it_and_the_boards_show_it(self):
        self.assertIn("agent_edits: forms",
                      self.cli("manuscript", "show", "-m", "book"))
        out = self.cli("manuscript", "set", "--agent-edits", "direct",
                       "-m", "book")
        self.assertIn("agent_edits: direct", out)
        self.assertIn("agent edits: direct",
                      self.cli("book", "status", "-m", "book"))
        self.cli("manuscript", "set", "--agent-edits", "forms", "-m", "book")
        self.assertIn("agent edits: forms",
                      self.cli("book", "status", "-m", "book"))


# --------------------------------------------------------------- definition

class DefinitionTests(unittest.TestCase):
    def setUp(self) -> None:
        path = book.default_definition_path()
        self.steps = book.parse_definition(
            path.read_text(encoding="utf-8"), path).steps

    def test_the_default_definitions_text_changing_steps_are_exactly_the_six(
            self):
        self.assertEqual(
            [s.id for s in self.steps if s.changes_text],
            ["draft", "lint", "line-edit", "copyedit", "production",
             "proofread"])
        for step in self.steps:
            if step.changes_text:
                self.assertEqual(step.actor, "agent", step.id)

    def test_the_authors_reads_are_given_the_changes(self):
        self.assertEqual([s.id for s in self.steps if "changes" in s.gives],
                         ["part-read", "approval"])
        for step in self.steps:
            if "changes" in step.gives:
                self.assertTrue(step.actor.startswith("human:"), step.id)

    def test_changes_text_is_true_or_false_and_part_of_the_contract(self):
        path = Path("workflow.toml")
        with self.assertRaisesRegex(ValueError, "changes_text must be true"):
            book.parse_definition(WORKFLOW.replace(
                "changes_text = true", 'changes_text = "yes"', 1), path)
        steps = {s.id: s for s in book.parse_definition(WORKFLOW,
                                                        path).steps}
        without = {s.id: s for s in book.parse_definition(
            WORKFLOW.replace("changes_text = true\n", ""), path).steps}
        self.assertNotEqual(book.contract_hash(steps["lint"]),
                            book.contract_hash(without["lint"]))
        self.assertEqual(book.contract_hash(steps["critic"]),
                         book.contract_hash(without["critic"]))


# -------------------------------------------------------------------- land

class LandDeclinesTests(Book):
    def assert_nothing_changed(self) -> None:
        self.assertEqual(self.text("one.md"), ONE)
        self.assertEqual(self.text("two.md"), TWO)
        self.assertEqual(self.rows(), [])

    def test_land_declines_under_forms(self):
        run = self.lint_run()
        book.set_agent_edits(self.db, self.manuscript, "forms")
        with self.assertRaisesRegex(ValueError,
                                    "go to the author as forms"):
            self.land(run, "one.md", [TYPO])
        self.assert_nothing_changed()

    def test_land_declines_for_a_run_that_is_not_a_running_agent_step(self):
        self.run_step("interview")
        done = self.run_step("draft", "one.md")["id"]
        with self.assertRaisesRegex(ValueError, "is done, not running"):
            self.land(done, "one.md", [TYPO])
        run = self.running("lint", "one.md")
        book.fail(self.db, self.manuscript, run, reason="stopped")
        with self.assertRaisesRegex(ValueError, "is failed, not running"):
            self.land(run, "one.md", [TYPO])
        self.assert_nothing_changed()

    def test_land_declines_for_a_persons_step(self):
        self.run_step("interview")
        book.assign(self.db, self.manuscript, "draft", "human:author")
        waiting = self.running("draft", "one.md")
        with self.assertRaisesRegex(ValueError, "not an\\s+agent's"):
            self.land(waiting, "one.md", [TYPO])
        self.assert_nothing_changed()

    def test_a_step_without_changes_text_may_not_land(self):
        run = self.lint_run()
        book.finish(self.db, self.manuscript, run, NO_USES)
        critic = self.running("critic", "one.md")
        with self.assertRaisesRegex(ValueError,
                                    "not a step that changes text"):
            self.land(critic, "one.md", [TYPO])
        self.assert_nothing_changed()

    def test_a_chapter_run_lands_only_in_its_chapter(self):
        run = self.lint_run("one.md")
        with self.assertRaisesRegex(ValueError, "outside this run's scope"):
            self.land(run, "two.md", [{
                "old": "came early", "new": "came late", "note": "a test"}])
        self.assert_nothing_changed()

    def test_a_missing_or_doubled_old_declines_the_whole_batch(self):
        run = self.lint_run()
        missing = {"old": "words that are nowhere", "new": "x y z",
                   "note": "a test"}
        doubled = {"old": "the", "new": "that", "note": "a test"}
        with self.assertRaises(ValueError) as raised:
            self.land(run, "one.md", [TYPO, missing, doubled])
        message = str(raised.exception)
        self.assertIn("all or none", message)
        self.assertIn("item 2 («words that are nowhere»)", message)
        self.assertIn("not in the file", message)
        self.assertIn("item 3 («the»)", message)
        self.assertRegex(message, r"stand \d+ times")
        self.assertNotIn("item 1", message)
        self.assert_nothing_changed()
        # The good item alone lands.
        self.land(run, "one.md", [TYPO])
        self.assertIn("ran the long way", self.text())

    def test_every_item_needs_a_note(self):
        run = self.lint_run()
        with self.assertRaisesRegex(ValueError, "the note is empty"):
            self.land(run, "one.md", [{**TYPO, "note": "  "}])
        with self.assertRaisesRegex(ValueError, "old, new and note"):
            self.land(run, "one.md", [{"old": "teh", "new": "the"}])
        with self.assertRaisesRegex(ValueError, "a JSON list"):
            self.land(run, "one.md", [])
        self.assert_nothing_changed()

    def test_a_change_that_could_not_be_put_back_is_declined(self):
        run = self.lint_run()
        with self.assertRaisesRegex(ValueError, "new is empty"):
            self.land(run, "one.md", [{"old": " He never got it.",
                                       "new": "", "note": "a cut"}])
        # 'teh' stands once, but 'the' would then stand many times.
        with self.assertRaisesRegex(ValueError,
                                    "could not be found again"):
            self.land(run, "one.md", [{"old": "teh", "new": "the",
                                       "note": "typo"}])
        with self.assertRaisesRegex(ValueError, "overlapping"):
            self.land(run, "one.md", [TYPO, {
                "old": "teh long way round", "new": "the long road round",
                "note": "a test"}])
        self.assert_nothing_changed()

    def test_a_tab_the_author_edited_is_pulled_first(self):
        run = self.lint_run()
        for state in ("doc_ahead", "conflict"):
            with self.assertRaisesRegex(ValueError, "doc pull one.md"):
                self.land(run, "one.md", [TYPO],
                          tab_state=lambda _rel, s=state: s)

        def unreadable(_rel: str) -> str:
            raise OSError("no credentials")

        with self.assertRaisesRegex(ValueError, "could not be\\s+read"):
            self.land(run, "one.md", [TYPO], tab_state=unreadable)
        self.assert_nothing_changed()
        # A tab that holds nothing new does not stop a landing.
        self.land(run, "one.md", [TYPO], tab_state=lambda _rel: "unchanged")

    def test_a_manuscript_with_no_doc_never_reaches_google(self):
        self.assertEqual(changes.google_tab_state(
            self.db, self.manuscript, "one.md", {}), "unlinked")
        run = self.lint_run()
        result = changes.land(self.db, self.manuscript, {}, run, "one.md",
                              [TYPO])
        self.assertNotIn("doc push", result["next"])


class LandTests(Book):
    def test_a_landing_changes_the_file_and_records_each_change(self):
        run = self.lint_run()
        result = self.land(run, "one.md", [TYPO, MINDED])
        self.assertEqual(self.text(), ONE.replace(
            "ran teh long way", "ran the long way").replace(
            "Nobody in the village", "No one in the village"))
        rows = self.rows()
        self.assertEqual([(r["seq"], r["old"], r["new"], r["note"],
                           r["status"]) for r in rows],
                         [(1, TYPO["old"], TYPO["new"], TYPO["note"],
                           "applied"),
                          (2, MINDED["old"], MINDED["new"], MINDED["note"],
                           "applied")])
        for row in rows:
            self.assertTrue(row["id"].startswith("sc-"))
            self.assertEqual((row["run_id"], row["step"], row["role"],
                              row["document_id"]),
                             (run, "lint", "linter", self.one))
        self.assertEqual([c["id"] for c in result["landed"]],
                         [r["id"] for r in rows])
        # A second batch carries the sequence on.
        self.land(run, "one.md", [{
            "old": "He never got it", "new": "He never did get it",
            "note": "Rhythm."}])
        self.assertEqual([r["seq"] for r in self.rows()], [1, 2, 3])

    def test_the_change_list_survives_a_rename(self):
        run = self.lint_run()
        self.land(run, "one.md", [TYPO])
        quiet(api.rename_document, self.db, self.manuscript, {}, "one.md",
              "river.md")
        self.assertEqual(self.rows()[0]["document_id"], self.one)
        listed = self.listed(file="river.md")
        self.assertEqual([c["file"] for c in listed], ["river.md"])
        report = changes.revert(self.db, self.manuscript, {},
                                [listed[0]["id"]], reason="keep the typo",
                                tab_state=unlinked)
        self.assertEqual(len(report["reverted"]), 1)
        self.assertIn("ran teh long way", self.text("river.md"))

    def test_the_text_before_and_after_a_landing_are_both_snapshots(self):
        run = self.lint_run()
        # An edit nobody collected: it predates the landing.
        edited = ONE.replace("He never got it.", "He never got it, quite.")
        (self.root / "one.md").write_text(edited, encoding="utf-8")
        result = self.land(run, "one.md", [TYPO])
        versions = {r["source"]: json.loads(r["files"])["one.md"]
                    for r in self.db.all(
                        "SELECT source, files FROM manuscript_versions")}
        self.assertEqual(versions["pre-book-land"], edited)
        self.assertEqual(versions["book-land"], self.text())
        self.assertIn("ran the long way", versions["book-land"])
        self.assertIsNotNone(result["version_no"])

    def test_a_landing_shows_in_the_history(self):
        run = self.lint_run()
        self.land(run, "one.md", [TYPO, MINDED])
        events = [e for e in book.history(self.db, self.manuscript,
                                          step="lint")["events"]]
        self.assertEqual([(e["from_status"], e["to_status"], e["reason"])
                          for e in events],
                         [(None, "running", ""),
                          ("running", "running",
                           "landed 2 change(s) in one.md")])
        self.assertEqual(self.run_row(run)["status"], "running")
        self.assertIn("landed 2 change(s) in one.md",
                      self.cli("book", "history", "--step", "lint",
                               "-m", "book"))

    def test_book_done_records_the_count_and_nothing_else_changes(self):
        run = self.lint_run()
        self.land(run, "one.md", [TYPO, MINDED])
        done = book.finish(self.db, self.manuscript, run,
                           {**NO_USES, "summary": "two fixes"})
        self.assertEqual(done["result"]["changes_landed"], 2)
        self.assertEqual(done["result"]["summary"], "two fixes")
        self.assertEqual(done["status"], "done")
        # A run that landed nothing records no count.
        other = self.run_step("critic", "one.md")
        self.assertNotIn("changes_landed", other["result"])

    def test_a_book_step_lands_in_any_chapter(self):
        self.to_the_part_read()
        self.run_step("part-read", "part.md")
        run = self.running("line-edit")
        self.land(run, "two.md", [{
            "old": "walked to market", "new": "walked to the market",
            "note": "Article, as elsewhere in the chapter."}])
        self.assertIn("walked to the market", self.text("two.md"))

    def test_the_verb_reads_the_batch_and_quotes_each_change(self):
        run = self.lint_run()
        batch = self.root.parent / "batch.json"
        batch.write_text(json.dumps([TYPO]), encoding="utf-8")
        out = self.cli("book", "land", run, "one.md", "--changes-json",
                       str(batch), "-m", "book")
        self.assertIn("Landed 1 change(s) in one.md", out)
        self.assertIn("«ran teh long way»", out)
        self.assertIn("«ran the long way»", out)
        self.assertIn(TYPO["note"], out)
        self.assertIn("ran the long way", self.text())
        error = self.cli_error("book", "land", run, "one.md",
                               "--changes-json", str(batch), "-m", "book")
        self.assertIn("not in the file", error)


# ------------------------------------------------------------- change list

class ChangesListTests(Book):
    def setUp(self) -> None:
        super().setUp()
        self.to_the_part_read()
        self.run_step("part-read", "part.md")
        self.line = self.running("line-edit")
        self.land(self.line, "one.md", [MINDED])

    def test_the_list_is_in_reading_order_then_in_the_order_landed(self):
        self.assertEqual([(c["file"], c["step"], c["role"])
                          for c in self.listed()],
                         [("one.md", "lint", "linter"),
                          ("one.md", "line-edit", "line editor"),
                          ("two.md", "lint", "linter")])

    def test_the_list_narrows(self):
        self.assertEqual(len(self.listed(run=self.line)), 1)
        self.assertEqual(len(self.listed(step="lint")), 2)
        self.assertEqual(len(self.listed(file="two.md")), 1)
        self.assertEqual(len(self.listed(scope="one.md")), 2)
        self.assertEqual(len(self.listed(scope="part.md")), 3)
        self.assertEqual(len(self.listed(scope="book")), 3)
        self.assertEqual(len(self.listed(scope="part.md", file="two.md",
                                         step="lint")), 1)
        self.assertEqual(len(self.listed(status="reverted")), 0)
        first = self.listed(file="two.md")[0]
        changes.revert(self.db, self.manuscript, {}, [first["id"]],
                       reason="idle is right", tab_state=unlinked)
        self.assertEqual([c["id"] for c in self.listed(status="reverted")],
                         [first["id"]])
        self.assertEqual(len(self.listed(status="applied")), 2)
        landed_at = self.listed(step="lint", file="one.md")[0]["landed_at"]
        self.assertEqual(len(self.listed(since=landed_at)), 2)
        with self.assertRaisesRegex(ValueError, "applied or reverted"):
            self.listed(status="gone")

    def test_the_author_reads_the_words_in_full_and_no_line_numbers(self):
        out = self.cli("book", "changes", "--scope", "one.md", "-m", "book")
        for change in self.listed(scope="one.md"):
            self.assertIn(change["id"], out)
            self.assertIn(f"«{change['old']}»", out)
            self.assertIn(f"«{change['new']}»", out)
            self.assertIn(change["note"], out)
            self.assertIn(f"{change['role']} ({change['step']}", out)
        self.assertIn("2 applied, 0 put back", out)
        self.assertNotRegex(out, r"(?i)\bline \d|\bl\.\s?\d|:\d+:|¶")
        as_json = json.loads(self.cli("book", "changes", "--json", "-m",
                                      "book"))
        self.assertEqual(len(as_json["changes"]), 3)


# ------------------------------------------------------------------ revert

class RevertTests(Book):
    def setUp(self) -> None:
        super().setUp()
        self.run = self.lint_run()
        landed = self.land(self.run, "one.md", [TYPO, MINDED])["landed"]
        self.typo, self.minded = (c["id"] for c in landed)

    def revert(self, *ids: str, **kw) -> dict:
        return changes.revert(self.db, self.manuscript, {}, list(ids),
                              tab_state=unlinked, **kw)

    def test_revert_puts_the_text_back_and_keeps_the_row(self):
        report = self.revert(self.minded,
                             reason="I say 'nobody', it is how I talk",
                             by="author")
        self.assertEqual(report["declined"], [])
        self.assertIn("Nobody in the village minded", self.text())
        self.assertIn("ran the long way", self.text())
        row = next(r for r in self.rows() if r["id"] == self.minded)
        self.assertEqual((row["status"], row["reverted_by"],
                          row["reverted_reason"]),
                         ("reverted", "author",
                          "I say 'nobody', it is how I talk"))
        self.assertTrue(row["reverted_at"])
        self.assertEqual(len(self.rows()), 2)
        self.assertEqual(len(self.listed()), 2)
        last = book.history(self.db, self.manuscript)["events"][-1]
        self.assertEqual((last["run"], last["actor"]), (self.run, "author"))
        self.assertIn(f"put back {self.minded} in one.md: I say 'nobody'",
                      last["reason"])
        # Put back once: a second try leaves it alone and says who did.
        again = self.revert(self.minded, reason="again")
        self.assertEqual(again["reverted"], [])
        self.assertIn("already put back by author", again["declined"][0]["why"])

    def test_revert_declines_when_the_text_has_moved_on(self):
        moved = self.text().replace("No one in the village minded",
                                    "Not a soul in the village minded")
        (self.root / "one.md").write_text(moved, encoding="utf-8")
        report = self.revert(self.minded, self.typo, reason="put both back")
        self.assertEqual([c["id"] for c in report["reverted"]], [self.typo])
        declined = report["declined"][0]
        self.assertEqual(declined["id"], self.minded)
        self.assertIn("the text has moved on", declined["why"])
        self.assertIn("What stands there now: «", declined["why"])
        self.assertIn("Not a soul in the village minded the delay",
                      declined["why"])
        self.assertIn("Not a soul in the village minded", self.text())
        self.assertIn("ran teh long way", self.text())
        statuses = {r["id"]: r["status"] for r in self.rows()}
        self.assertEqual(statuses, {self.typo: "reverted",
                                    self.minded: "applied"})

    def test_a_whole_run_is_put_back_and_a_done_run_stays_done(self):
        book.finish(self.db, self.manuscript, self.run, NO_USES)
        report = changes.revert(self.db, self.manuscript, {}, run=self.run,
                                reason="the lint was too eager",
                                tab_state=unlinked)
        self.assertEqual(len(report["reverted"]), 2)
        self.assertEqual(self.text(), ONE)
        self.assertEqual(self.run_row(self.run)["status"], "done")
        last = book.history(self.db, self.manuscript)["events"][-1]
        self.assertEqual((last["from_status"], last["to_status"]),
                         ("done", "done"))

    def test_revert_needs_a_reason_and_a_target(self):
        with self.assertRaisesRegex(ValueError, "records why"):
            self.revert(self.typo, reason=" ")
        with self.assertRaisesRegex(ValueError, "not both, not neither"):
            self.revert(reason="why")
        with self.assertRaisesRegex(LookupError, "no change matches"):
            self.revert("sc-nothing", reason="why")
        self.assertEqual({r["status"] for r in self.rows()}, {"applied"})

    def test_revert_works_whatever_the_setting_is_now(self):
        book.set_agent_edits(self.db, self.manuscript, "forms")
        self.assertEqual(len(self.revert(self.typo,
                                         reason="x")["reverted"]), 1)

    def test_the_verb_says_what_it_put_back_and_what_it_left(self):
        out = self.cli("book", "revert", self.typo, "--reason",
                       "the typo is the joke", "-m", "book")
        self.assertIn(f"Put back {self.typo} in one.md", out)
        self.assertIn("is again «ran teh long way»", out)
        error = self.cli_error("book", "revert", self.typo, "--reason",
                               "again", "-m", "book")
        self.assertEqual(error, "1")


# -------------------------------------------------------------------- packs

class PackTests(Book):
    def pack(self, step: str, scope: str | None = None) -> str:
        return Path(self.start(step, scope)["pack_path"]).read_text(
            encoding="utf-8")

    def test_every_agent_pack_says_how_edits_land(self):
        self.run_step("interview")
        draft = self.start("draft", "one.md")
        pack = Path(draft["pack_path"]).read_text(encoding="utf-8")
        self.assertIn("How edits land in this book: directly. Your step "
                      "changes text", pack)
        self.assertIn(f"authorlm book land {draft['run']['id']} <file>",
                      pack)
        self.assertLess(pack.index("How edits land in this book"),
                        pack.index("## Your contract"))
        book.finish(self.db, self.manuscript, draft["run"]["id"], NO_USES)
        self.run_step("lint", "one.md")
        critic = self.pack("critic", "one.md")
        self.assertIn("How edits land in this book: directly, for the "
                      "steps that change text. Yours is not one", critic)
        self.assertNotIn("book land", critic)
        # The same two steps in a book whose edits go to the author.
        book.set_agent_edits(self.db, self.manuscript, "forms")
        draft = self.pack("draft", "two.md")
        self.assertIn("How edits land in this book: as forms for the "
                      "author. A change to words waits", draft)
        self.assertIn("`book land` is not open", draft)

    def test_a_persons_pack_carries_no_such_line(self):
        self.assertNotIn("How edits land in this book",
                         self.pack("interview"))

    def test_the_part_read_pack_names_the_count_and_the_command(self):
        self.to_the_part_read()
        first = self.start("part-read", "part.md")
        pack = Path(first["pack_path"]).read_text(encoding="utf-8")
        self.assertIn("## What agent roles changed", pack)
        self.assertIn("Agent roles have landed 2 change(s) in part.md.",
                      pack)
        self.assertIn("The author has not read this scope before", pack)
        self.assertIn("    authorlm book changes --scope part.md -m book",
                      pack)
        self.assertIn("authorlm book revert <change-id>", pack)
        self.assertIn("  - changes: the changes agent roles landed in this "
                      "scope, counted below: authorlm book changes --scope "
                      "part.md -m book", pack)
        read = book.finish(self.db, self.manuscript, first["run"]["id"], {})
        # After that read: one change put back, one more landed.
        typo = self.listed(file="one.md")[0]["id"]
        changes.revert(self.db, self.manuscript, {}, [typo],
                       reason="keep it", tab_state=unlinked)
        self.land(self.running("line-edit"), "one.md", [MINDED])
        book.restart(self.db, self.manuscript, "part-read", "part.md",
                     reason="the author wants a second read")
        pack = self.pack("part-read", "part.md")
        self.assertIn("Agent roles have landed 3 change(s) in part.md, 1 "
                      "of them since put back.", pack)
        self.assertIn("1 of them landed since the author last read this "
                      f"scope (part-read, run {read['id']})", pack)
        self.assertIn("    authorlm book changes --scope part.md --since "
                      f"{read['finished_at']} -m book", pack)
        since = self.listed(scope="part.md", since=read["finished_at"])
        self.assertEqual([c["new"] for c in since], [MINDED["new"]])

    def test_a_pack_with_nothing_landed_says_so(self):
        book.set_agent_edits(self.db, self.manuscript, "forms")
        self.run_step("interview")
        for chapter in ("one.md", "two.md"):
            for step in ("draft", "lint", "critic"):
                self.run_step(step, chapter)
        pack = self.pack("part-read", "part.md")
        self.assertIn("Agent roles have landed no change in part.md. Edits "
                      "in this book go to the author as forms.", pack)

    def test_the_approval_pack_counts_the_whole_book(self):
        self.to_the_part_read()
        self.run_step("part-read", "part.md")
        run = self.running("line-edit")
        self.land(run, "one.md", [MINDED])
        book.finish(self.db, self.manuscript, run, NO_USES)
        pack = self.pack("approval")
        self.assertIn("Agent roles have landed 3 change(s) in the book.",
                      pack)
        self.assertIn("    authorlm book changes -m book", pack)
        # The part read covered part of the book, not the book.
        self.assertIn("The author has not read this scope before", pack)


# -------------------------------------------------------------------- draft

class DraftTests(Book):
    DRAFT = ("# One\n\nThe river took the long way round the hill, and "
             "the village took its time.\n")

    def setUp(self) -> None:
        super().setUp()
        (self.root / "one.md").write_text("# One\n", encoding="utf-8")
        quiet(api.collect, self.db, self.manuscript, {})
        self.run_step("interview")
        self.run = self.running("draft", "one.md")

    def draft(self, run: str, file: str, text: str) -> dict:
        return changes.draft(self.db, self.manuscript, {}, run, file, text,
                             tab_state=unlinked)

    def test_a_first_draft_is_an_artifact_and_an_event_not_a_change(self):
        result = self.draft(self.run, "one.md", self.DRAFT)
        self.assertEqual(self.text(), self.DRAFT)
        self.assertEqual(result["words"], len(self.DRAFT.split()))
        self.assertFalse(result["replaced"])
        self.assertEqual(self.rows(), [])
        last = book.history(self.db, self.manuscript)["events"][-1]
        self.assertEqual((last["from_status"], last["to_status"]),
                         ("running", "running"))
        self.assertIn("drafted one.md:", last["reason"])
        latest = self.db.one("SELECT source, files FROM manuscript_versions "
                             "ORDER BY version_no DESC LIMIT 1")
        self.assertEqual(latest["source"], "book-draft")
        self.assertEqual(json.loads(latest["files"])["one.md"], self.DRAFT)
        # A correction in the same run is a change; the drafter may also
        # write its own draft over, whole.
        self.land(self.run, "one.md", [{
            "old": "took its time", "new": "took its own time",
            "note": "The voice entry: plain emphasis."}])
        self.assertTrue(self.draft(self.run, "one.md",
                                   self.DRAFT)["replaced"])
        done = book.finish(self.db, self.manuscript, self.run,
                           {**NO_USES, "artifacts": ["notes.md"]})
        self.assertEqual(done["result"]["artifacts"],
                         ["notes.md", "one.md"])
        self.assertEqual(done["result"]["drafted"], ["one.md"])
        self.assertEqual(done["result"]["changes_landed"], 1)

    def test_a_file_that_holds_prose_is_not_drafted_over(self):
        with self.assertRaisesRegex(ValueError, "outside this run's scope"):
            self.draft(self.run, "two.md", self.DRAFT)
        book.fail(self.db, self.manuscript, self.run, reason="stopped")
        other = self.running("draft", "two.md")
        with self.assertRaisesRegex(ValueError, "already holds prose"):
            self.draft(other, "two.md", self.DRAFT)
        with self.assertRaisesRegex(ValueError, "the draft is empty"):
            self.draft(other, "two.md", "# Two\n")
        self.assertEqual(self.text("two.md"), TWO)

    def test_a_rerun_of_the_step_may_write_the_chapter_again(self):
        self.draft(self.run, "one.md", self.DRAFT)
        book.finish(self.db, self.manuscript, self.run, NO_USES)
        book.restart(self.db, self.manuscript, "draft", "one.md",
                     reason="the author gave the wrong river")
        rerun = self.running("draft", "one.md")
        again = self.DRAFT.replace("river", "stream")
        self.assertTrue(self.draft(rerun, "one.md", again)["replaced"])
        self.assertEqual(self.text(), again)
        kept = [json.loads(r["files"])["one.md"] for r in self.db.all(
            "SELECT files FROM manuscript_versions")]
        self.assertIn(self.DRAFT, kept)

    def test_draft_declines_under_forms(self):
        book.set_agent_edits(self.db, self.manuscript, "forms")
        with self.assertRaisesRegex(ValueError,
                                    "go to the author as forms"):
            self.draft(self.run, "one.md", self.DRAFT)
        self.assertEqual(self.text(), "# One\n")


# --------------------------------------------------------------------- lint

class LintTests(Book):
    def test_a_whole_chapter_is_linted_with_the_words_quoted(self):
        report = changes.lint_chapter(self.db, self.manuscript, "one.md")
        opener = next(f for f in report["findings"]
                      if f["quote"].startswith("What the miller wanted"))
        self.assertEqual(opener["severity"], "error")
        self.assertEqual(self.text(), ONE)
        out = self.cli("book", "lint", "one.md", "-m", "book")
        self.assertIn("«What the miller wanted was a shorter road to "
                      "market.»", out)
        self.assertIn(f"[error] {opener['code']}", out)
        self.assertNotIn("¶", out)
        # Open under forms too: it changes nothing.
        book.set_agent_edits(self.db, self.manuscript, "forms")
        self.assertIn("one.md:", self.cli("book", "lint", "one.md",
                                          "-m", "book"))

    def test_a_chapter_is_not_reported_as_repeating_itself(self):
        report = changes.lint_chapter(self.db, self.manuscript, "two.md")
        self.assertEqual([f for f in report["findings"]
                          if f["code"] == "overlap"], [])


# --------------------------------------------------------- protected terms

FIELD = ("# One\n\nThe miller named the low ground the Field, and nobody "
         "argued. The Field lay past the weir, where the road ended.\n\n"
         "The old monk spoke of anattā to anyone who would stop. Few "
         "stopped for long.\n")
DICTIONARY = ("# **Pronunciations**\n\n| Term | Say it | Note |\n"
              "| --- | --- | --- |\n| anattā | ah-nah-TAH | Pali |\n")


class ProtectedTermTests(Book):
    """A landing that drops a term of the book's vocabulary warns and
    still lands. The terms: a concept, `Field`, and a pronunciation
    dictionary term, `anattā`."""

    def setUp(self) -> None:
        super().setUp()
        (self.root / "one.md").write_text(FIELD, encoding="utf-8")
        (self.root / "pronunciations.md").write_text(DICTIONARY,
                                                     encoding="utf-8")
        api.add_concept(self.db, self.manuscript, "Field")
        quiet(api.collect, self.db, self.manuscript, {})
        self.run = self.lint_run()

    def test_a_dropped_term_is_warned_and_the_change_still_lands(self):
        dropped = {"old": "named the low ground the Field",
                   "new": "named the low ground the meadow",
                   "note": "Plainer word, style law default:plain-words."}
        result = self.land(self.run, "one.md", [dropped])
        self.assertIn("named the low ground the meadow", self.text())
        change = result["landed"][0]
        self.assertEqual(change["protected_lost"], ["Field"])
        self.assertEqual(len(result["warnings"]), 1)
        warning = result["warnings"][0]
        self.assertEqual((warning["change"], warning["terms"],
                          warning["old"], warning["new"], warning["note"]),
                         (change["id"], ["Field"], dropped["old"],
                          dropped["new"], dropped["note"]))
        for part in ("'Field'", change["id"], dropped["note"],
                     f"authorlm book revert {change['id']}"):
            self.assertIn(part, warning["message"])
        # Kept on the row, in the column every row already has.
        row = self.rows()[0]
        self.assertEqual(row["status"], "applied")
        self.assertEqual(json.loads(row["metadata"])["protected_lost"],
                         ["Field"])
        self.assertEqual(self.listed()[0]["protected_lost"], ["Field"])
        self.assertIn("! drops or re-capitalizes a term of the book's "
                      "vocabulary: 'Field'",
                      self.cli("book", "changes", "-m", "book"))

    def test_a_term_whose_capital_is_changed_is_warned(self):
        result = self.land(self.run, "one.md", [{
            "old": "The Field lay past the weir",
            "new": "The field lay past the weir",
            "note": "Lower case for a common noun."}])
        self.assertIn("The field lay past the weir", self.text())
        self.assertEqual([w["terms"] for w in result["warnings"]],
                         [["Field"]])

    def test_a_term_that_is_kept_brings_no_warning(self):
        result = self.land(self.run, "one.md", [
            {"old": "The Field lay past the weir, where the road ended",
             "new": "The Field lay past the weir, at the end of the road",
             "note": "Rhythm."},
            {"old": "Few stopped for long", "new": "Few stayed long",
             "note": "Shorter."}])
        self.assertEqual(result["warnings"], [])
        self.assertEqual([c["protected_lost"] for c in result["landed"]],
                         [[], []])
        self.assertIn("at the end of the road", self.text())
        self.assertTrue(all("protected_lost" not in json.loads(
            r["metadata"]) for r in self.rows()))
        self.assertNotIn("! drops", self.cli("book", "changes", "-m",
                                             "book"))

    def test_a_cut_passage_that_held_a_term_is_warned_among_others(self):
        """A dictionary term, lost in a cut; the batch's other change is
        not warned, and both land."""
        result = self.land(self.run, "one.md", [
            {"old": "The old monk spoke of anattā to anyone who would "
                    "stop. Few stopped for long.",
             "new": "Few stopped for long.",
             "note": "The monk is not in the outline."},
            {"old": "and nobody argued", "new": "and no one argued",
             "note": "The voice entry prefers 'no one'."}])
        self.assertEqual(len(result["landed"]), 2)
        self.assertEqual([(w["change"], w["terms"])
                          for w in result["warnings"]],
                         [(result["landed"][0]["id"], ["anattā"])])
        self.assertNotIn("anattā", self.text())
        self.assertIn("and no one argued", self.text())

    def test_the_verb_prints_each_warning(self):
        batch = self.root.parent / "batch.json"
        batch.write_text(json.dumps([{
            "old": "named the low ground the Field",
            "new": "named the low ground the meadow",
            "note": "Plainer word."}]), encoding="utf-8")
        out = self.cli("book", "land", self.run, "one.md",
                       "--changes-json", str(batch), "-m", "book")
        self.assertIn("Landed 1 change(s) in one.md", out)
        self.assertIn("warning: sc-", out)
        self.assertIn("drops or re-capitalizes 'Field'", out)
        self.assertIn("Its note: «Plainer word.»", out)
        self.assertIn("named the low ground the meadow", self.text())


# ------------------------------------------------- restart and what landed

class RestartTests(Book):
    """A restart changes no file: it says what the superseded runs landed
    and how to put it back, and the rerun's pack says so too."""

    def setUp(self) -> None:
        super().setUp()
        self.lint = self.lint_run()
        landed = self.land(self.lint, "one.md", [TYPO, MINDED])["landed"]
        book.finish(self.db, self.manuscript, self.lint, NO_USES)
        changes.revert(self.db, self.manuscript, {}, [landed[1]["id"]],
                       reason="I say 'nobody'", tab_state=unlinked)
        self.command = (f"authorlm book revert --run {self.lint} --reason "
                        "'<why>' -m book")

    def test_restart_reports_what_the_runs_landed_and_how_to_put_it_back(
            self):
        before = self.text()
        for dry_run in (True, False):
            with self.subTest(dry_run=dry_run):
                report = book.restart(self.db, self.manuscript, "draft",
                                      "one.md", reason="wrong brief",
                                      dry_run=dry_run)
                # Two runs are superseded; only the one that landed
                # changes is listed, with those still applied.
                self.assertEqual([r["step"] for r in report["superseded"]],
                                 ["draft", "lint"])
                self.assertEqual(report["landed"], [{
                    "run": self.lint, "step": "lint",
                    "scope_label": "one.md", "applied": 1,
                    "revert": self.command}])
        # It puts nothing back by itself.
        self.assertEqual(self.text(), before)
        self.assertEqual([r["status"] for r in self.rows()],
                         ["applied", "reverted"])

    def test_a_restart_of_runs_that_landed_nothing_reports_none(self):
        changes.revert(self.db, self.manuscript, {}, run=self.lint,
                       reason="start over", tab_state=unlinked)
        report = book.restart(self.db, self.manuscript, "draft", "one.md",
                              reason="wrong brief")
        self.assertEqual(report["landed"], [])

    def test_the_verb_prints_the_count_and_the_command(self):
        out = self.cli("book", "restart", "draft", "--scope", "one.md",
                       "--reason", "wrong brief", "-m", "book")
        self.assertIn("superseded 2 run(s)", out)
        self.assertIn("1 change(s) these runs landed are still in the "
                      "chapter files. A restart puts nothing back", out)
        self.assertIn(f"lint (one.md), 1 applied:  {self.command}", out)

    def test_the_reruns_pack_says_the_earlier_changes_still_stand(self):
        book.restart(self.db, self.manuscript, "draft", "one.md",
                     reason="wrong brief")
        draft = self.start("draft", "one.md")
        pack = Path(draft["pack_path"]).read_text(encoding="utf-8")
        # The draft run landed nothing, so its rerun is told nothing.
        self.assertIn("## Earlier attempt: reference only", pack)
        self.assertNotIn("still in the chapter files", pack)
        book.finish(self.db, self.manuscript, draft["run"]["id"], NO_USES)
        pack = Path(self.start("lint", "one.md")["pack_path"]).read_text(
            encoding="utf-8")
        section = pack[pack.index("## Earlier attempt: reference only"):
                       pack.index("## When you are done")]
        self.assertIn("The earlier attempt's landed changes are still in "
                      "the chapter files unless they were put back: 1 "
                      "applied change(s).", section)
        self.assertIn(f"    {self.command}    (1 applied)", section)


# --------------------------------------------------------------- role files

class RoleFileTests(unittest.TestCase):
    """Each role file agrees with the definition about landing."""

    def setUp(self) -> None:
        path = book.default_definition_path()
        steps = [s for s in book.parse_definition(
            path.read_text(encoding="utf-8"), path).steps
            if s.actor == "agent"]
        self.changing = sorted({s.role for s in steps if s.changes_text})
        self.reporting = sorted({s.role for s in steps}
                                - set(self.changing))

    def text(self, role: str) -> str:
        return (AGENTS / f"{book.role_agent_name(role)}.md").read_text(
            encoding="utf-8")

    def test_every_text_changing_role_file_says_how_to_land(self):
        self.assertEqual(self.changing, [
            "copy editor", "drafter", "line editor", "linter",
            "production editor", "proofreader"])
        for role in self.changing:
            with self.subTest(role=role):
                text = self.text(role)
                self.assertIn("authorlm book land <run-id>", text)
                self.assertIn('"How edits land in this book"', text)
                self.assertIn("**If it says directly", text)
                self.assertIn("**If it says as forms for the author**", text)
                self.assertIn('"note"', text)
        self.assertIn("authorlm book draft <run-id>", self.text("drafter"))

    def test_no_reporting_role_file_tells_the_agent_to_land(self):
        self.assertEqual(len(self.reporting), 8)
        for role in self.reporting:
            with self.subTest(role=role):
                text = self.text(role)
                self.assertNotIn("book land", text)
                self.assertNotIn("book draft", text)
                self.assertIn("You never land a change in a chapter file",
                              text)

    def test_the_forms_rules_are_kept_word_for_word(self):
        """Under forms the existing text applies unchanged."""
        for role in ("copy editor", "line editor", "linter", "proofreader"):
            with self.subTest(role=role):
                text = self.text(role)
                self.assertIn(
                    "3. A change to words in a file the author has called "
                    "a first draft is a form: `authorlm revise stage "
                    "<file> --origin <name>`", text)
                self.assertIn("7. If you cannot tell whether a file has "
                              "the author's first-draft word, stop with "
                              "`NEEDS AUTHOR` (below).", text)
        self.assertIn("the road is the beat loop", self.text("drafter"))
        self.assertIn("Never accept a beat on your own judgment",
                      self.text("drafter"))

    def test_the_ruling_on_sources_is_written_down_and_the_drafter_agrees(
            self):
        """The owner's question and answer, word for word, under `direct`
        in the house skill, with the reading labelled as a reading."""
        skill = " ".join((AGENTS.parent / "skills" / "authorlm"
                          / "SKILL.md").read_text(encoding="utf-8").split())
        part = skill[skill.index("**How agent edits land**"):
                     skill.index("**Restart from any point:**")]
        self.assertIn(
            'the owner was asked, 2026-11-13, "In a ghostwritten book '
            "where edits land directly, should the drafter still stop for "
            "you to confirm each chapter's list of sources before it "
            'writes?" and answered "No, keep going". The lead developer\'s '
            "reading (a reading, not the owner's words): under `direct` "
            "the drafter does not stop for the source list", part)
        self.assertIn("Under `forms` nothing changes", part)
        self.assertIsNone(RULED_OUT.search(part))
        drafter = self.text("drafter")
        direct = drafter[drafter.index("**If it says directly"):
                         drafter.index("**If it says as forms")]
        self.assertIn("You do not stop for the author to confirm the "
                      "chapter's list of sources", direct)
        self.assertIn("Name the sources you used in your result", direct)
        # Under forms the drafter's rule stands as it was.
        self.assertIn("The author confirms the list before the first beat.",
                      drafter[drafter.index("**If it says as forms"):])
        self.assertIsNone(RULED_OUT.search(drafter))

    def test_nothing_written_here_uses_a_word_the_owner_ruled_out(self):
        files = [Path(changes.__file__), Path(book.__file__),
                 Path(__file__), book.default_definition_path()]
        for path in files:
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path.name):
                self.assertIsNone(RULED_OUT.search(text))


if __name__ == "__main__":
    unittest.main()
