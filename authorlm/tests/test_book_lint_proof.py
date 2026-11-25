"""`book lint` with the proof checks (proof.py through changes.py and the
command line): the chapter report carries them beside the nine checks,
`--only` and `--skip` choose rules, `--all` reads the whole book, the
manuscript's own words and laws are read from where they are stored, and
`book lint-accept` is the only verb here that writes, and only its file.

Hermetic: a temporary workspace, no network, no model.
Run: python3 tests/test_book_lint_proof.py
"""
from __future__ import annotations

import os

os.environ["AUTHORLM_CONFIG"] = "/nonexistent/authorlm-lint-proof/config.toml"
os.environ["AUTHORLM_ENV"] = "/nonexistent/authorlm-lint-proof/.env"
os.environ["AUTHORLM_CLIENT"] = "none"
os.environ["AUTHORLM_STYLE_DEFAULTS"] = \
    "/nonexistent/authorlm-lint-proof/style-defaults.toml"

import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import authorlm  # noqa: E402

assert Path(authorlm.__file__).resolve().parent == HERE.parent / "authorlm", (
    f"testing the wrong package: {authorlm.__file__}")

from authorlm import (api, changes, concepts, proof, readings,  # noqa: E402
                      styles)

from test_book_changes import Book  # noqa: E402

# One fault per proof rule, each on a line of its own paragraph.
ONE = "\n\n".join([
    "# One",                                                    # line 1
    "The river ran teh long way round the hill.",               # 3
    "The miller walked to the the market.",                     # 5
    "The colour of the water was plain.",                       # 7
    "He said \"wait\" and she said “go.”",                      # 9
    "A claim with no source.[^N1]",                             # 11
    "See https://example.com/mill?utm_source=news for more.",   # 13
    "The upaya of Zorblatt was known to the darshanas.",        # 15
    "What the miller wanted was a shorter road.",               # 17
]) + "\n"
TWO = "# Two\n\nThe second winter came early. Nobody recieved the news.\n"


class ProofBook(Book):
    def setUp(self) -> None:
        super().setUp()
        (self.root / "one.md").write_text(ONE, encoding="utf-8")
        (self.root / "two.md").write_text(TWO, encoding="utf-8")

    def proof(self, file: str = "one.md", **kw) -> dict:
        return changes.lint_chapter(self.db, self.manuscript, file, **kw)

    def refs(self, board: dict, code: str) -> list:
        return [(f["line"], f["ref"]) for f in board["findings"]
                if f["code"] == code]

    def snapshot(self) -> dict[str, bytes]:
        return {str(p.relative_to(self.root)): p.read_bytes()
                for p in sorted(self.root.rglob("*")) if p.is_file()}


class ChapterTests(ProofBook):
    def test_each_proof_rule_reports_its_line_and_words(self):
        board = self.proof()
        self.assertEqual(self.refs(board, "spelling"),
                         [(3, "teh"), (15, "upaya"), (15, "darshanas")])
        self.assertEqual(self.refs(board, "doubled-word"), [(5, "the the")])
        self.assertEqual(self.refs(board, "spelling-variant"),
                         [(7, "colour")])
        self.assertEqual(self.refs(board, "quote-style"), [(9, "straight")])
        self.assertEqual(self.refs(board, "footnote"), [(11, "[^N1]")])
        self.assertEqual([f["line"] for f in board["findings"]
                          if f["code"] == "link-tracking"], [13])
        self.assertEqual(self.refs(board, "banned-word"), [])
        typo = next(f for f in board["findings"] if f["ref"] == "teh")
        self.assertIn("ran teh long way", typo["quote"])
        self.assertIn("the", typo["message"])

    def test_the_board_counts_each_rule(self):
        board = self.proof()
        for rule, n in (("spelling", 3), ("doubled-word", 1),
                        ("spelling-variant", 1), ("quote-style", 1),
                        ("footnote", 1), ("link-tracking", 1)):
            self.assertEqual(board["rules"][rule], n, rule)
        self.assertEqual(sum(board["rules"].values()),
                         len(board["findings"]))
        out = self.cli("book", "lint", "one.md", "-m", "book")
        self.assertIn("by rule: ", out)
        self.assertIn("3 spelling", out)
        self.assertIn("line 3 «", out)

    def test_the_nine_checks_still_report_as_before(self):
        board = self.proof()
        opener = [f for f in board["findings"]
                  if f["code"] == "what-opener"]
        self.assertEqual([(f["severity"], f["quote"]) for f in opener],
                         [("error", "What the miller wanted was a shorter "
                                    "road.")])
        self.assertNotIn("line", opener[0])
        self.assertIn("[error] what-opener: ",
                      self.cli("book", "lint", "one.md", "-m", "book"))

    def test_only_and_skip_choose_rules(self):
        only = self.proof(only=["footnote", "doubled-word"])
        self.assertEqual(set(only["rules"]), {"footnote", "doubled-word"})
        skipped = self.proof(skip=["spelling", "what-opener"])
        self.assertNotIn("spelling", skipped["rules"])
        self.assertNotIn("what-opener", skipped["rules"])
        self.assertIn("footnote", skipped["rules"])
        nine = self.proof(only=["what-opener"])
        self.assertEqual(nine["rules"], {"what-opener": 1})
        out = self.cli("book", "lint", "one.md", "--only",
                       "footnote,doubled-word", "--skip", "footnote",
                       "-m", "book", "--json")
        self.assertEqual(set(json.loads(out)["rules"]), {"doubled-word"})

    def test_an_unknown_rule_names_the_rules(self):
        with self.assertRaisesRegex(ValueError, "spelling.*banned-word"):
            self.proof(only=["speling"])
        self.assertIn("unknown lint rule: speling",
                      self.cli_error("book", "lint", "one.md", "--only",
                                     "speling", "-m", "book"))

    def test_lint_writes_nothing(self):
        before = self.snapshot()
        self.proof()
        changes.lint_book(self.db, self.manuscript)
        self.cli("book", "lint", "--all", "-m", "book")
        self.assertEqual(self.snapshot(), before)


class EmDashTests(ProofBook):
    """An em-dash is a warning for the session to judge, with the place
    and the words the dash sets off; only a stack is an error."""

    TEXT = ("# Two\n\nThe mill stood idle. The miller — a patient man — "
            "waited for the thaw.\n\nHe walked — slowly — to market — "
            "alone — that spring.\n")

    def setUp(self) -> None:
        super().setUp()
        (self.root / "two.md").write_text(self.TEXT, encoding="utf-8")

    def test_a_dash_is_a_warning_with_line_sentence_and_aside(self):
        board = self.proof("two.md", only=["em-dash", "em-dash-stack"])
        self.assertEqual(
            [(f["code"], f["severity"], f["line"], f["ref"])
             for f in board["findings"]],
            [("em-dash", "warning", 3, "a patient man"),
             ("em-dash-stack", "error", 5, "slowly | alone")])
        warning = board["findings"][0]
        self.assertEqual(warning["quote"], "The miller — a patient man — "
                                           "waited for the thaw.")
        self.assertIn("Em dashes are welcome as long as they are used to "
                      "explain something inline and the explanation is "
                      "expendable. Never stack multiple em-dash pairs in "
                      "one sentence.", warning["message"])
        self.assertIn("Never stack multiple em-dash pairs in one sentence.",
                      board["findings"][1]["message"])
        out = self.cli("book", "lint", "two.md", "--only", "em-dash",
                       "-m", "book")
        self.assertIn("[warning] em-dash: em-dash sets off «a patient man»",
                      out)
        self.assertIn("line 3 «The miller — a patient man — waited", out)

    def test_the_whole_book_run_lists_dashes_when_asked(self):
        board = changes.lint_book(self.db, self.manuscript,
                                  only=["em-dash"])
        self.assertEqual([(f["file"], f["line"]) for f in board["findings"]],
                         [("two.md", 3)])
        self.assertNotIn("em-dash", changes.lint_book(
            self.db, self.manuscript)["rules"])


class OwnWordsTests(ProofBook):
    """What the spell check accepts beyond the dictionary, each read
    from where the manuscript keeps it."""

    def spelling(self) -> list[str]:
        return [ref for _line, ref in self.refs(self.proof(), "spelling")]

    def test_a_concept_name_and_its_alias_are_accepted(self):
        node = concepts.add_concept(self.db, self.manuscript["id"], "Upaya")
        concepts.add_alias(self.db, self.manuscript["id"], node,
                           "darshana")
        self.assertEqual(self.spelling(), ["teh"])

    def test_a_pronunciation_entry_is_accepted(self):
        (self.root / "pronunciations.md").write_text(
            "# **Pronunciations**\n\n| Term | Say it | Note |\n"
            "| --- | --- | --- |\n| upaya | oo-PAH-ya | Sanskrit |\n",
            encoding="utf-8")
        self.assertEqual(self.spelling(), ["teh", "darshanas"])

    def test_a_name_in_the_current_reading_is_accepted(self):
        readings.record(self.db, self.manuscript["id"], "house", "one.md",
                        "terms of the field: Upaya, Darshana, Zorblatt.",
                        "fr-none")
        self.assertEqual(self.spelling(), ["teh"])

    def test_an_accepted_word_is_accepted(self):
        board = changes.lint_accept(self.db, self.manuscript,
                                    ["upaya", "darshana"])
        self.assertEqual(board["added"], ["upaya", "darshana"])
        self.assertEqual(self.spelling(), ["teh"])

    def test_the_variant_is_the_manuscripts_language(self):
        api.update_manuscript_metadata(self.db, self.manuscript,
                                       language="en-GB")
        self.manuscript = api.get_manuscript(self.db, "book")
        (self.root / "one.md").write_text(
            ONE + "\nThe center held.\n", encoding="utf-8")
        self.assertEqual(self.refs(self.proof(), "spelling-variant"),
                         [(19, "center")])


class LawTests(ProofBook):
    def test_a_ban_is_read_from_the_laws_that_govern_the_file(self):
        styles.add_element(self.db, self.manuscript["id"], "lexicon",
                           "Never plain 'the miller'; he has a name.",
                           file="one.md")
        self.assertEqual(self.refs(self.proof(), "banned-word"),
                         [(5, "the miller"), (17, "the miller")])
        (self.root / "two.md").write_text(
            TWO + "\nThe miller slept.\n", encoding="utf-8")
        self.assertEqual(self.refs(self.proof("two.md"), "banned-word"), [],
                         "a law of one.md does not govern two.md")

    def test_quote_marks_follow_the_law_that_states_them(self):
        styles.add_element(self.db, self.manuscript["id"], "formatting",
                           "Straight quotation marks throughout.",
                           file="one.md")
        self.assertEqual(self.refs(self.proof(), "quote-style"),
                         [(9, "curly")])


class WholeBookTests(ProofBook):
    def test_all_reads_every_file_and_counts_per_rule_and_file(self):
        board = changes.lint_book(self.db, self.manuscript)
        self.assertEqual(board["files"], 4)
        self.assertEqual(board["rules"]["spelling"], 4)
        self.assertEqual(board["by_file"]["two.md"], 1)
        slip = next(f for f in board["findings"] if f["ref"] == "recieved")
        self.assertEqual((slip["file"], slip["line"]), ("two.md", 3))
        self.assertEqual(board["variant"], "us")
        self.assertEqual(board["variant_words"], {"us": 0, "uk": 1})
        self.assertFalse(set(board["rules"]) - set(proof.RULES),
                         "the nine checks stay out unless named")

    def test_all_runs_a_named_check_of_the_nine(self):
        board = changes.lint_book(self.db, self.manuscript,
                                  only=["what-opener", "footnote"])
        self.assertEqual(board["rules"], {"what-opener": 1, "footnote": 1})

    def test_the_command_prints_file_line_rule_and_a_count_per_rule(self):
        out = self.cli("book", "lint", "--all", "-m", "book")
        self.assertIn("two.md:3  [warning] spelling: 'recieved'", out)
        self.assertIn("Nobody recieved the news.»", out)
        self.assertIn("4 files: ", out)
        self.assertIn("4 spelling", out)
        self.assertIn("1 footnote", out)
        board = json.loads(self.cli("book", "lint", "--all", "--only",
                                    "spelling", "-m", "book", "--json"))
        self.assertEqual(board["rules"], {"spelling": 4})
        self.assertIn("next", board)

    def test_a_file_or_all_and_never_both(self):
        self.assertIn("--all", self.cli_error("book", "lint", "-m", "book"))
        self.assertIn("--all", self.cli_error("book", "lint", "one.md",
                                              "--all", "-m", "book"))


class AcceptTests(ProofBook):
    def test_accept_appends_to_the_manuscripts_file_and_nothing_else(self):
        before = self.snapshot()
        out = self.cli("book", "lint-accept", "Zorblatt", "upaya",
                       "-m", "book")
        path = self.root / proof.ACCEPTED_FILE
        self.assertIn(str(path), out)
        after = self.snapshot()
        self.assertEqual(proof.read_accepted(self.root),
                         ["Zorblatt", "upaya"])
        after.pop(proof.ACCEPTED_FILE)
        self.assertEqual(after, before)
        again = json.loads(self.cli("book", "lint-accept", "upaya",
                                    "darshanas", "-m", "book", "--json"))
        self.assertEqual((again["added"], again["already"]),
                         (["darshanas"], ["upaya"]))

    def test_dry_run_writes_nothing(self):
        before = self.snapshot()
        out = self.cli("book", "lint-accept", "upaya", "--dry-run",
                       "-m", "book")
        self.assertIn("Would add", out)
        self.assertEqual(self.snapshot(), before)

    def test_a_phrase_is_declined(self):
        self.assertIn("not a single word",
                      self.cli_error("book", "lint-accept", "two words",
                                     "-m", "book"))
        self.assertFalse((self.root / proof.ACCEPTED_FILE).exists())

    def test_the_accepted_file_is_not_a_chapter(self):
        changes.lint_accept(self.db, self.manuscript, ["upaya"])
        self.assertEqual(
            changes.lint_book(self.db, self.manuscript)["files"], 4)


if __name__ == "__main__":
    unittest.main()
