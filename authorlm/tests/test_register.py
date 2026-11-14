"""The register of settled choices (register.py): every choice the author
settles is an entry holding the author's words verbatim and the machine's
reading of them, and every piece of work that rests on an entry records a
use labelled applies or infers. These tests enforce the rules the module
docstring states: no entry is settled without both words and reading; a
settled entry is never edited in place (a change makes a new entry that
supersedes the old one and returns the work that rested on it); an entry
can be reopened; a key is unique among the live entries of its kind.

Hermetic: a temporary workspace, no network, no model.
Run: python3 tests/test_register.py
"""
from __future__ import annotations

import os

os.environ["AUTHORLM_CONFIG"] = "/nonexistent/authorlm-register/config.toml"
os.environ["AUTHORLM_ENV"] = "/nonexistent/authorlm-register/.env"
os.environ["AUTHORLM_CLIENT"] = "none"
os.environ["AUTHORLM_STYLE_DEFAULTS"] = \
    "/nonexistent/authorlm-register/style-defaults.toml"

import contextlib
import io
import json
import sys
import tempfile
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

from authorlm import api, book, docs, register  # noqa: E402
from authorlm.cli import main as cli_main  # noqa: E402

WORDS = "I want a reader who has never meditated to finish it in a weekend."
READING = ("The reader is a newcomer to the practice. The book is short "
           "enough to read in two days.")


def quiet(function, *args, **kwargs):
    with contextlib.redirect_stdout(io.StringIO()):
        return function(*args, **kwargs)


class Workspace(unittest.TestCase):
    """One registered manuscript with two chapters, collected once."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.workspace = base / "workspace"
        self.root = base / "book"
        self.root.mkdir()
        for name in ("one.md", "two.md"):
            (self.root / name).write_text(
                f"# {name}\n\n" + "A sentence of this chapter. " * 40 + "\n",
                encoding="utf-8")
        (self.root / "toc.toml").write_text(
            '[[chapter]]\nfile = "one.md"\n\n[[chapter]]\nfile = "two.md"\n',
            encoding="utf-8")
        self.db = api.open_db(str(self.workspace), create=True)
        api.register_manuscript(self.db, "book", str(self.root),
                                extraction=False)
        self.manuscript = api.get_manuscript(self.db)
        quiet(api.collect, self.db, self.manuscript, {})

    def tearDown(self) -> None:
        self.db.conn.close()
        self.tmp.cleanup()

    def cli(self, *argv: str) -> str:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli_main(["-w", str(self.workspace), *argv])
        return out.getvalue()

    def cli_error(self, *argv: str) -> str:
        with self.assertRaises(SystemExit) as raised, \
                contextlib.redirect_stdout(io.StringIO()):
            cli_main(["-w", str(self.workspace), *argv])
        self.assertNotIn(raised.exception.code, (0, None))
        return str(raised.exception.code)

    def settled(self, kind: str = "reader", key: str = "newcomer") -> dict:
        entry = register.add(self.db, self.manuscript, kind, key,
                             author_words=WORDS, reading=READING)
        return register.settle(self.db, self.manuscript, entry["id"])


class SettleTests(Workspace):
    def test_an_entry_starts_proposed_with_a_register_id(self):
        entry = register.add(self.db, self.manuscript, "reader", "newcomer",
                             author_words=WORDS)
        self.assertTrue(entry["id"].startswith("re-"))
        self.assertEqual(entry["status"], "proposed")
        self.assertEqual(entry["author_words"], WORDS)

    def test_settling_needs_the_authors_words(self):
        entry = register.add(self.db, self.manuscript, "reader", "newcomer",
                             reading=READING)
        with self.assertRaisesRegex(ValueError, "author's words"):
            register.settle(self.db, self.manuscript, entry["id"])
        self.assertEqual(
            register.show(self.db, self.manuscript, entry["id"])
            ["entry"]["status"], "proposed")

    def test_settling_needs_the_reading(self):
        entry = register.add(self.db, self.manuscript, "reader", "newcomer",
                             author_words=WORDS)
        with self.assertRaisesRegex(ValueError, "reading"):
            register.settle(self.db, self.manuscript, entry["id"])
        self.assertEqual(
            register.show(self.db, self.manuscript, entry["id"])
            ["entry"]["status"], "proposed")

    def test_blank_words_do_not_count(self):
        entry = register.add(self.db, self.manuscript, "reader", "newcomer",
                             author_words="   ", reading=READING)
        with self.assertRaisesRegex(ValueError, "author's words"):
            register.settle(self.db, self.manuscript, entry["id"])

    def test_settle_may_supply_what_is_missing_and_keeps_words_verbatim(self):
        entry = register.add(self.db, self.manuscript, "reader", "newcomer",
                             author_words=WORDS)
        settled = register.settle(self.db, self.manuscript, entry["id"],
                                  reading=READING)
        self.assertEqual(settled["status"], "settled")
        self.assertEqual(settled["author_words"], WORDS)
        self.assertEqual(settled["reading"], READING)
        self.assertTrue(settled["settled_at"])

    def test_an_undeclared_kind_is_declined_naming_the_kinds(self):
        with self.assertRaisesRegex(ValueError, "purpose"):
            register.add(self.db, self.manuscript, "mood", "calm")

    def test_success_is_a_brief_kind_and_settles_like_the_others(self):
        """#435: success is the brief's central entry; without it in
        KINDS / BRIEF_KINDS the intake interview cannot record what the
        author argued out, and the pack's `gives = brief` line omits it."""
        self.assertIn("success", register.KINDS)
        self.assertEqual(
            register.BRIEF_KINDS,
            ("purpose", "reader", "promise", "comparable", "takeaway",
             "success"))
        self.assertTrue(set(register.BRIEF_KINDS) <= set(register.KINDS))
        self.assertIn("success", book.GIVES["brief"])
        entry = register.add(
            self.db, self.manuscript, "success", "chapter-and-book",
            author_words="A chapter succeeds when the reader can restate "
                         "its one claim in their own words.",
            reading="Success is the reader's restated claim, not length.")
        settled = register.settle(self.db, self.manuscript, entry["id"])
        self.assertEqual(settled["kind"], "success")
        self.assertEqual(settled["status"], "settled")
        listed = register.list_entries(self.db, self.manuscript,
                                       kind="success")
        self.assertEqual([e["id"] for e in listed], [settled["id"]])

    def test_a_key_must_be_a_short_slug(self):
        with self.assertRaisesRegex(ValueError, "key"):
            register.add(self.db, self.manuscript, "reader", "The Newcomer!")

    def test_an_entry_is_found_by_kind_and_key(self):
        entry = self.settled()
        found = register.show(self.db, self.manuscript, "reader/newcomer")
        self.assertEqual(found["entry"]["id"], entry["id"])

    def test_an_entry_may_be_scoped_to_one_document(self):
        document = docs.document_id(self.db, self.manuscript, "one.md")
        entry = register.add(self.db, self.manuscript, "thesis", "one",
                             author_words=WORDS, reading=READING,
                             scope="one.md")
        self.assertEqual(entry["scope"], document)
        book_wide = self.settled()
        self.assertIsNone(book_wide["scope"])


class InScopeTests(Workspace):
    def test_none_means_every_live_entry(self):
        book_wide = self.settled()
        scoped = register.add(self.db, self.manuscript, "thesis", "one",
                              author_words=WORDS, reading=READING,
                              scope="one.md")
        register.settle(self.db, self.manuscript, scoped["id"])
        ids = [e["id"] for e in register.in_scope(
            self.db, self.manuscript, None)]
        self.assertEqual(sorted(ids), sorted([book_wide["id"], scoped["id"]]))

    def test_a_chapter_sees_book_wide_entries_and_its_own(self):
        book_wide = self.settled()
        one = docs.document_id(self.db, self.manuscript, "one.md")
        two = docs.document_id(self.db, self.manuscript, "two.md")
        for key, scope in (("one", "one.md"), ("two", "two.md")):
            entry = register.add(self.db, self.manuscript, "thesis", key,
                                 author_words=WORDS, reading=READING,
                                 scope=scope)
            register.settle(self.db, self.manuscript, entry["id"])
        handed = register.in_scope(self.db, self.manuscript, [one])
        self.assertEqual(
            sorted((e["kind"], e["key"]) for e in handed),
            [("reader", "newcomer"), ("thesis", "one")])
        self.assertNotIn(two, [e["scope"] for e in handed])


class KeyTests(Workspace):
    def test_a_key_is_unique_among_live_entries_of_its_kind(self):
        self.settled()
        with self.assertRaisesRegex(ValueError, "already"):
            register.add(self.db, self.manuscript, "reader", "newcomer",
                         author_words="someone else")

    def test_the_same_key_is_free_under_another_kind(self):
        self.settled()
        other = register.add(self.db, self.manuscript, "promise", "newcomer")
        self.assertEqual(other["kind"], "promise")

    def test_a_superseded_entry_does_not_hold_its_key(self):
        entry = self.settled()
        revised = register.revise(self.db, self.manuscript, entry["id"],
                                  reading="A newcomer, with no time limit.")
        live = register.list_entries(self.db, self.manuscript, kind="reader")
        self.assertEqual([e["id"] for e in live], [revised["entry"]["id"]])


class ReviseAndReopenTests(Workspace):
    def use(self, entry: dict, mode: str, note: str) -> dict:
        return register.use(self.db, self.manuscript, entry["id"],
                            mode=mode, actor="agent:drafter", note=note)

    def test_revising_a_settled_entry_supersedes_it_and_returns_its_uses(self):
        entry = self.settled()
        self.use(entry, "applies", "chapter one opens for a newcomer")
        self.use(entry, "infers", "left the Pali terms out")
        result = register.revise(
            self.db, self.manuscript, entry["id"],
            reading="A newcomer; the weekend is a wish, not a length limit.")
        new, old = result["entry"], result["superseded"]
        self.assertNotEqual(new["id"], entry["id"])
        self.assertEqual(new["supersedes"], entry["id"])
        self.assertEqual(new["status"], "settled")
        self.assertEqual(new["author_words"], WORDS)
        self.assertEqual(old["status"], "superseded")
        # The old entry stays readable, words and reading as they were.
        kept = register.show(self.db, self.manuscript, entry["id"])
        self.assertEqual(kept["entry"]["reading"], READING)
        self.assertEqual(kept["superseded_by"], new["id"])
        self.assertEqual(
            sorted((u["mode"], u["note"]) for u in result["uses"]),
            [("applies", "chapter one opens for a newcomer"),
             ("infers", "left the Pali terms out")])

    def test_new_words_without_a_new_reading_wait_for_the_author(self):
        """The reading is shown when an entry is settled: words the
        machine has not yet read back are not settled."""
        entry = self.settled()
        result = register.revise(self.db, self.manuscript, entry["id"],
                                 author_words="A reader in a hurry.")
        self.assertEqual(result["entry"]["status"], "proposed")
        self.assertEqual(result["entry"]["author_words"],
                         "A reader in a hurry.")

    def test_revising_with_nothing_changed_is_declined(self):
        entry = self.settled()
        with self.assertRaisesRegex(ValueError, "nothing"):
            register.revise(self.db, self.manuscript, entry["id"],
                            reading=READING)
        self.assertEqual(
            register.show(self.db, self.manuscript, entry["id"])
            ["entry"]["status"], "settled")

    def test_a_proposed_entry_is_revised_in_place(self):
        entry = register.add(self.db, self.manuscript, "reader", "newcomer",
                             author_words=WORDS)
        result = register.revise(self.db, self.manuscript, entry["id"],
                                 reading=READING)
        self.assertEqual(result["entry"]["id"], entry["id"])
        self.assertIsNone(result["superseded"])
        self.assertEqual(result["entry"]["reading"], READING)

    def test_reopening_returns_the_work_that_rested_on_the_entry(self):
        entry = self.settled()
        self.use(entry, "applies", "chapter one opens for a newcomer")
        result = register.reopen(self.db, self.manuscript, entry["id"],
                                 reason="the author wants experts too")
        self.assertEqual(result["entry"]["status"], "reopened")
        self.assertEqual([u["note"] for u in result["uses"]],
                         ["chapter one opens for a newcomer"])
        again = register.settle(self.db, self.manuscript, entry["id"])
        self.assertEqual(again["status"], "settled")

    def test_only_a_settled_entry_can_be_reopened(self):
        entry = register.add(self.db, self.manuscript, "reader", "newcomer")
        with self.assertRaisesRegex(ValueError, "proposed"):
            register.reopen(self.db, self.manuscript, entry["id"])

    def test_a_reopened_entry_takes_new_words_only_by_revise(self):
        entry = self.settled()
        register.reopen(self.db, self.manuscript, entry["id"])
        with self.assertRaisesRegex(ValueError, "revise"):
            register.settle(self.db, self.manuscript, entry["id"],
                            author_words="Different words.")


class UseTests(Workspace):
    def test_a_use_says_applies_or_infers_and_nothing_else(self):
        entry = self.settled()
        with self.assertRaisesRegex(ValueError, "applies"):
            register.use(self.db, self.manuscript, entry["id"],
                         mode="follows", actor="agent:drafter")

    def test_work_cannot_rest_on_an_entry_that_is_not_settled(self):
        entry = register.add(self.db, self.manuscript, "reader", "newcomer",
                             author_words=WORDS, reading=READING)
        with self.assertRaisesRegex(ValueError, "not settled"):
            register.use(self.db, self.manuscript, entry["id"],
                         mode="applies", actor="agent:drafter")

    def test_a_use_names_its_document_by_id(self):
        entry = self.settled()
        use = register.use(self.db, self.manuscript, entry["id"],
                           mode="applies", actor="agent:drafter",
                           document="two.md", note="chapter two's opening")
        self.assertEqual(
            use["document_id"],
            docs.document_id(self.db, self.manuscript, "two.md"))
        self.assertEqual(
            [u["id"] for u in register.uses(self.db, self.manuscript,
                                            entry["id"])], [use["id"]])


class VerbTests(Workspace):
    def test_show_prints_words_reading_and_every_use_with_its_label(self):
        self.cli("register", "add", "reader", "newcomer",
                 "--author-words", WORDS, "--reading", READING)
        self.cli("register", "settle", "reader/newcomer")
        self.cli("register", "use", "reader/newcomer", "--mode", "applies",
                 "--actor", "agent:drafter", "--note", "opens for a newcomer")
        self.cli("register", "use", "reader/newcomer", "--mode", "infers",
                 "--actor", "agent:drafter", "--note", "no Pali terms")
        shown = self.cli("register", "show", "reader/newcomer")
        self.assertIn("Author's words", shown)
        self.assertIn(WORDS, shown)
        self.assertIn("Reading", shown)
        self.assertIn("newcomer to the practice", shown)
        self.assertRegex(shown, r"applies\b.*opens for a newcomer")
        self.assertRegex(shown, r"infers\b.*no Pali terms")

    def test_settle_shows_the_reading_beside_the_words(self):
        self.cli("register", "add", "reader", "newcomer",
                 "--author-words", WORDS, "--reading", READING)
        settled = self.cli("register", "settle", "reader/newcomer")
        self.assertIn(WORDS, settled)
        self.assertIn("newcomer to the practice", settled)

    def test_settle_without_a_reading_exits_with_the_fix(self):
        self.cli("register", "add", "reader", "newcomer",
                 "--author-words", WORDS)
        message = self.cli_error("register", "settle", "reader/newcomer")
        self.assertIn("error:", message)
        self.assertIn("--reading", message)

    def test_revise_and_reopen_list_the_work_that_needs_a_look(self):
        self.cli("register", "add", "reader", "newcomer",
                 "--author-words", WORDS, "--reading", READING)
        self.cli("register", "settle", "reader/newcomer")
        self.cli("register", "use", "reader/newcomer", "--mode", "applies",
                 "--actor", "agent:drafter", "--note", "opens for a newcomer")
        board = json.loads(self.cli(
            "register", "revise", "reader/newcomer", "--reading",
            "A newcomer, with no time limit.", "--json"))
        self.assertEqual(board["superseded"]["status"], "superseded")
        self.assertEqual([u["note"] for u in board["uses"]],
                         ["opens for a newcomer"])
        self.assertIn("next", board)
        reopened = json.loads(self.cli(
            "register", "reopen", "reader/newcomer", "--json"))
        self.assertEqual(reopened["entry"]["status"], "reopened")
        self.assertEqual(reopened["uses"], [])

    def test_list_and_uses_boards_parse_and_carry_next(self):
        self.cli("register", "add", "reader", "newcomer",
                 "--author-words", WORDS, "--reading", READING)
        listed = json.loads(self.cli("register", "list", "--json"))
        self.assertEqual([e["key"] for e in listed["entries"]], ["newcomer"])
        self.assertEqual(listed["counts"], {"proposed": 1})
        self.assertIn("next", listed)
        uses = json.loads(self.cli("register", "uses", "reader/newcomer",
                                   "--json"))
        self.assertEqual(uses["uses"], [])
        self.assertIn("next", uses)

    def test_an_unknown_entry_exits_with_an_error(self):
        message = self.cli_error("register", "show", "reader/nobody")
        self.assertIn("error:", message)


if __name__ == "__main__":
    unittest.main()
