"""The role files of the book workflow (`.claude/agents/book-<role>.md`):
one Claude Code subagent per agent role of the default workflow
(`library/book-workflow.toml`), saying how that trade does its step with
AuthorLM's existing verbs. These tests enforce what book.py's docstring
says of them: every agent role has a file named for it; every agent step
is claimed by exactly one file, its role's; an independent role's file
carries the independence rule; and an agent step's pack names the role
file, so whoever starts the step knows which subagent to hand it to.

The files are checked by stable marker lines (`Step: <step id>`,
`Independent: yes`), never by their prose.

Hermetic: a temporary workspace, no network, no model.
Run: python3 tests/test_book_role_agents.py
"""
from __future__ import annotations

import os

os.environ["AUTHORLM_CONFIG"] = "/nonexistent/authorlm-roles/config.toml"
os.environ["AUTHORLM_ENV"] = "/nonexistent/authorlm-roles/.env"
os.environ["AUTHORLM_CLIENT"] = "none"
os.environ["AUTHORLM_STYLE_DEFAULTS"] = \
    "/nonexistent/authorlm-roles/style-defaults.toml"

import re
import sys
import tempfile
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

from authorlm import book  # noqa: E402

from test_book_workflow import Workspace  # noqa: E402

AGENTS = HERE.parent.parent / ".claude" / "agents"
STEP_MARKER = re.compile(r"^Step: (\S+)$", re.MULTILINE)
INDEPENDENT_MARKER = re.compile(r"^Independent: yes$", re.MULTILINE)
# Words the owner has ruled out of everything written for him.
RULED_OUT = re.compile(r"\brefus(e|es|ed|al|als|ing)\b", re.IGNORECASE)


def default_definition() -> book.Definition:
    path = book.default_definition_path()
    return book.parse_definition(path.read_text(encoding="utf-8"), path)


def agent_steps() -> list[book.Step]:
    return [s for s in default_definition().steps if s.actor == "agent"]


def frontmatter(text: str) -> dict[str, str]:
    """The `key: value` lines between the file's opening `---` fences."""
    assert text.startswith("---\n"), "a role file opens with frontmatter"
    block = text[4:text.index("\n---\n", 4)]
    return dict(line.split(": ", 1) for line in block.splitlines() if line)


class RoleSlugTests(unittest.TestCase):
    def test_a_role_name_becomes_one_slug(self):
        self.assertEqual(book.role_slug("copy editor"), "copy-editor")
        self.assertEqual(book.role_slug("critic"), "critic")
        self.assertEqual(book.role_agent_name("Manuscript  Assessor"),
                         "book-manuscript-assessor")

    def test_the_checkouts_role_files_are_found(self):
        self.assertEqual(book.role_files_dir(), AGENTS)
        self.assertEqual(book.role_file("copy editor"),
                         AGENTS / "book-copy-editor.md")

    def test_a_missing_file_or_folder_is_none_not_an_error(self):
        self.assertIsNone(book.role_file("a role nobody wrote a file for"))
        self.assertIsNone(book.role_file(""))
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
                book, "role_files_dir",
                return_value=Path(tmp) / "no" / "such" / "folder"):
            self.assertIsNone(book.role_file("copy editor"))


class RoleFilesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.steps = agent_steps()
        self.roles = sorted({s.role for s in self.steps})

    def text(self, role: str) -> str:
        return (AGENTS / f"{book.role_agent_name(role)}.md").read_text(
            encoding="utf-8")

    def test_every_agent_role_has_a_file_named_for_it(self):
        self.assertGreaterEqual(len(self.roles), 14)
        for role in self.roles:
            with self.subTest(role=role):
                path = book.role_file(role)
                self.assertIsNotNone(path, f"no role file for '{role}'")
                fields = frontmatter(path.read_text(encoding="utf-8"))
                self.assertEqual(fields["name"], book.role_agent_name(role))
                self.assertTrue(fields["description"].strip())
                # The model is left to whoever starts the agent.
                self.assertNotIn("model", fields)

    def test_every_book_role_file_belongs_to_an_agent_role(self):
        expected = {f"{book.role_agent_name(r)}.md" for r in self.roles}
        found = {p.name for p in AGENTS.glob(f"{book.ROLE_FILE_PREFIX}*.md")}
        self.assertEqual(found, expected)

    def test_every_agent_step_is_claimed_by_exactly_its_roles_file(self):
        claimed: dict[str, list[str]] = {}
        for role in self.roles:
            for step_id in STEP_MARKER.findall(self.text(role)):
                claimed.setdefault(step_id, []).append(role)
        self.assertEqual({s.id: [s.role] for s in self.steps}, claimed)

    def test_an_independent_roles_file_carries_the_independence_rule(self):
        independent = {s.role for s in self.steps if s.independent}
        self.assertTrue(independent)
        for role in self.roles:
            with self.subTest(role=role):
                self.assertEqual(
                    bool(INDEPENDENT_MARKER.search(self.text(role))),
                    role in independent)

    def test_every_role_file_states_what_the_role_hands_back(self):
        for role in self.roles:
            with self.subTest(role=role):
                text = self.text(role)
                self.assertIn("book done", text)
                self.assertIn(book.USES_KEY, text)
                self.assertIn("applies", text)
                self.assertIn("infers", text)
                self.assertIn("filed_back", text)

    def test_no_role_file_uses_a_word_the_owner_ruled_out(self):
        for role in self.roles:
            with self.subTest(role=role):
                self.assertIsNone(RULED_OUT.search(self.text(role)))


class PackNamesTheRoleFileTests(Workspace):
    """The five-step test workflow: `draft` is an agent's (role drafter,
    which has a role file), `interview` is the author's, and `assessment`
    is an agent's whose role, assessor, has no file."""

    def pack(self, step: str, scope: str | None = None) -> tuple[dict, str]:
        started = self.start(step, scope)
        return started, Path(started["pack_path"]).read_text(
            encoding="utf-8")

    def test_an_agent_steps_pack_names_its_role_file(self):
        self.run_step("interview")
        started, pack = self.pack("draft", "one.md")
        path = AGENTS / "book-drafter.md"
        self.assertIn(f"Role file: {path}", pack)
        self.assertIn("`book-drafter`", pack)
        # Near the top: before the contract.
        self.assertLess(pack.index("Role file: "),
                        pack.index("## Your contract"))
        self.assertEqual(started["role_file"], str(path))
        self.assertIn("book-drafter", started["next"])

    def test_a_humans_step_names_no_role_file(self):
        with mock.patch.object(book, "role_file",
                               return_value=AGENTS / "book-drafter.md"):
            started, pack = self.pack("interview")
        self.assertNotIn("Role file:", pack)
        self.assertIsNone(started["role_file"])

    def test_a_role_without_a_file_gets_a_pack_that_names_none(self):
        self.run_step("interview")
        for chapter in ("one.md", "two.md"):
            self.run_step("draft", chapter)
            self.run_step("critic", chapter)
        self.run_step("part-read", "part.md")
        started, pack = self.pack("assessment")
        self.assertNotIn("Role file:", pack)
        self.assertIsNone(started["role_file"])
        self.assertIn("## Your contract", pack)

    def test_a_checkout_without_role_files_changes_nothing_else(self):
        self.run_step("interview")
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
                book, "role_files_dir", return_value=Path(tmp) / "absent"):
            started, pack = self.pack("draft", "one.md")
        self.assertNotIn("Role file:", pack)
        self.assertIn("## Your contract", pack)
        self.assertIn("Read ", started["next"])


if __name__ == "__main__":
    unittest.main()
