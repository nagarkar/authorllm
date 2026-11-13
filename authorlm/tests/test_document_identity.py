"""A document's identity is its `dc-` id; its relpath is a display name
with history (docs.py). These tests enforce the owner's ruling that a
chapter can be renamed cheaply: every record keyed by the file's name
follows it, whether the rename is the `doc rename` verb or a rename in
Finder or Obsidian that the next collect detects.

Hermetic: a temporary workspace, the `_SurgicalDocFake` Google double
from test_passes.py, no network, no model.
Run: python3 tests/test_document_identity.py
"""
from __future__ import annotations

import os

os.environ["AUTHORLM_CONFIG"] = "/nonexistent/authorlm-docidentity/config.toml"
os.environ["AUTHORLM_ENV"] = "/nonexistent/authorlm-docidentity/.env"
os.environ["AUTHORLM_CLIENT"] = "none"
os.environ["AUTHORLM_STYLE_DEFAULTS"] = \
    "/nonexistent/authorlm-docidentity/style-defaults.toml"

import contextlib
import io
import json
import re
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

from test_passes import _SurgicalDocFake  # noqa: E402

from authorlm import (api, backup, devedit, docs, gdocs,  # noqa: E402
                      generation, revisions, structure, summaries)
from authorlm.cli import main as cli_main  # noqa: E402
from authorlm.db import SCHEMA, ko_fields  # noqa: E402


def essay(title: str, seed: str, paragraphs: int = 8) -> str:
    """A chapter long enough (over 500 characters) for the auto-collect
    deletion guard to notice when it disappears."""
    body = "\n\n".join(
        f"{seed} paragraph {n} says something particular about {seed} "
        f"that no other paragraph of this essay says, number {n}."
        for n in range(1, paragraphs + 1))
    return f"# {title}\n\n{body}\n"


OLD_TEXT = essay("The Old Name", "alpha")
OTHER_TEXT = essay("Another Essay", "omega")

TOC = '''[[chapter]]
file = "old.md"

[[chapter]]
file = "child.md"
parent = "old.md"

[[chapter]]
file = "other.md"
'''


# A collect counts every inserted or rewritten paragraph as new, toc.toml's
# stanzas included (it always has). A rename rewrites the two stanzas above
# that name old.md, so those two are the whole count for a rename that
# edits nothing: none of the chapter's own paragraphs is counted.
TOC_STANZAS_NAMING_OLD = 2


def quiet(function, *args, **kwargs):
    with contextlib.redirect_stdout(io.StringIO()):
        return function(*args, **kwargs)


class Workspace(unittest.TestCase):
    """One registered manuscript with three chapters and a toc.toml,
    collected once."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.workspace = base / "workspace"
        self.root = base / "book"
        self.root.mkdir()
        (self.root / "old.md").write_text(OLD_TEXT, encoding="utf-8")
        (self.root / "child.md").write_text(
            essay("A Child", "beta"), encoding="utf-8")
        (self.root / "other.md").write_text(OTHER_TEXT, encoding="utf-8")
        (self.root / "toc.toml").write_text(TOC, encoding="utf-8")
        self.db = api.open_db(str(self.workspace), create=True)
        api.register_manuscript(self.db, "book", str(self.root),
                                extraction=False)
        self.manuscript = api.get_manuscript(self.db)
        self.mid = self.manuscript["id"]
        quiet(api.collect, self.db, self.manuscript, {})

    def tearDown(self) -> None:
        self.db.conn.close()
        self.tmp.cleanup()

    # -- helpers -------------------------------------------------------
    def insert(self, table: str, prefix: str, **fields) -> str:
        row = ko_fields(prefix)
        row.update(manuscript_id=self.mid, **fields)
        return self.db.insert(table, row)

    def count(self, table: str, column: str, value: str) -> int:
        return self.db.one(
            f"SELECT COUNT(*) AS n FROM {table} WHERE manuscript_id = ? "
            f"AND {column} = ?", (self.mid, value))["n"]

    def links(self) -> dict:
        return gdocs.doc_status(self.db, api.get_manuscript(self.db))

    def link(self, relpath: str, **entry) -> None:
        meta = gdocs._mapping(self.db, self.manuscript)
        links = meta.setdefault("gdocs", {})
        links["_master_id"] = "doc-fake"
        links["_container_tab"] = "tab-1"
        links[relpath] = entry
        gdocs._save_mapping(self.db, self.manuscript, meta)

    def attached_guide(self, file: str) -> str | None:
        overview = api.style_overview(self.db, self.manuscript)
        return next((a["guide"] for a in overview["attachments"]
                     if a["file"] == file), None)

    def kinds(self, report: dict) -> list[str]:
        return [t["kind"] for t in report["transitions"]]

    def at(self, report: dict, file: str) -> list[str]:
        """The kinds of the transitions a collect recorded for one file."""
        return [t["kind"] for t in report["transitions"]
                if t["location"].split("#", 1)[0] == file]


class SeedingTests(Workspace):

    def test_every_manuscript_file_gets_one_id_and_seeding_twice_adds_nothing(self):
        first = docs.ensure_documents(self.db, self.manuscript)
        listed = docs.list_documents(self.db, self.manuscript)
        self.assertEqual(sorted(d["relpath"] for d in listed),
                         ["child.md", "old.md", "other.md"])
        self.assertTrue(all(d["id"].startswith("dc-") for d in listed))
        self.assertTrue(all(d["status"] == "active" for d in listed))
        second = docs.ensure_documents(self.db, self.manuscript)
        self.assertEqual(second, [])
        self.assertEqual(
            {d["relpath"]: d["id"] for d in listed},
            {d["relpath"]: d["id"]
             for d in docs.list_documents(self.db, self.manuscript)})
        # toc.toml is structure, not a document.
        self.assertNotIn("toc.toml", [d["relpath"] for d in listed])
        self.assertLessEqual(len(first), 3)

    def test_relpath_and_id_resolve_both_ways(self):
        document_id = docs.document_id(self.db, self.manuscript, "old.md")
        self.assertEqual(docs.document_relpath(self.db, document_id), "old.md")
        self.assertEqual(
            docs.document_id(self.db, self.manuscript, "old.md"), document_id)
        with self.assertRaises(LookupError):
            docs.document_id(self.db, self.manuscript, "never-existed.md")

    def test_a_retired_file_is_a_retired_document_with_the_same_id(self):
        before = docs.document_id(self.db, self.manuscript, "other.md")
        docs.retire_doc(self.manuscript, "other.md")
        quiet(api.collect, self.db, self.manuscript, {})
        rows = {d["relpath"]: d
                for d in docs.list_documents(self.db, self.manuscript)}
        self.assertEqual(rows["other.md"]["status"], "retired")
        self.assertEqual(rows["other.md"]["id"], before)
        docs.revive_doc(self.manuscript, "other.md")
        quiet(api.collect, self.db, self.manuscript, {})
        rows = {d["relpath"]: d
                for d in docs.list_documents(self.db, self.manuscript)}
        self.assertEqual(rows["other.md"]["status"], "active")
        self.assertEqual(rows["other.md"]["id"], before)

    def test_a_file_found_only_in_the_retired_folder_is_seeded_retired(self):
        retired = self.root / docs.RETIRED_DIR
        retired.mkdir()
        (retired / "ancient.md").write_text("# Ancient\n", encoding="utf-8")
        docs.ensure_documents(self.db, self.manuscript)
        rows = {d["relpath"]: d
                for d in docs.list_documents(self.db, self.manuscript)}
        self.assertEqual(rows["ancient.md"]["status"], "retired")


class RenameVerbTests(Workspace):

    def seed_records(self) -> dict:
        """One record of every kind that names old.md."""
        ids: dict = {}
        api.define_style_guide(self.db, self.manuscript, "essays")
        api.attach_style(self.db, self.manuscript, "old.md", "essays")
        api.add_style_law(self.db, self.manuscript, "tone", "Stay plain.",
                          file="old.md")
        ids["intent"] = self.insert(
            "declared_intents", "in", statement="Sharpen the opening",
            scope="old.md")
        self.insert("essay_summaries", "es", file="old.md", summary="S",
                    source_hash="h", upstream_hash="u")
        self.insert("section_summaries", "ss", file="old.md",
                    section_hash="sh", section_ordinal=1, heading="H",
                    para_hashes="[]", summary="S", model="m")
        self.insert("paragraph_summaries", "ps", file="old.md",
                    para_hash="ph", ordinal=1, section_ordinal=1,
                    kind="prose", record="{}", model="m")
        ids["thread"] = self.insert(
            "doc_threads", "dt", origin_type="filter",
            origin_id="fr-abc:old.md:1", file="old.md", proposed_old="a",
            proposed_new="b", state="proposed", scope_kind="file",
            scope_ref="old.md")
        self.insert("doc_comments", "dc", comment_id="AAA", file="old.md",
                    location="old.md#The Old Name", content="hm")
        self.insert("concept_nodes", "cn", name="alpha", status="realized",
                    introduced_in="old.md")
        self.insert("illus_proposals", "ip", file="old.md", anchor="a",
                    description="d")
        self.insert("filter_readings", "fd", reading="plain", file="old.md",
                    registry="r")
        version = self.db.one(
            "SELECT id FROM manuscript_versions WHERE manuscript_id = ? "
            "ORDER BY version_no DESC LIMIT 1", (self.mid,))["id"]
        ids["version"] = version
        ids["pass"] = self.insert(
            "critique_passes", "cp", source_id="src-x",
            essay_state=json.dumps({"old.md": "resolved",
                                    "other.md": "pending"}),
            pinned_versions=json.dumps({"old.md": version}),
            status="completed")
        row = ko_fields("gh")
        row.update(manuscript_id=self.mid, session_id="s", batch_id="b",
                   batch_index=1, kind="focus", suggestion="x",
                   explanation="y",
                   metadata=json.dumps({
                       "file": "old.md", "target_file": "old.md",
                       "dedupe_key": "judgment:lens:old.md:3"}))
        ids["guidance"] = self.db.insert("guidance_history", row)
        self.link("old.md", tab_id="tab-2", checked_out=False,
                  pushed_hash="abc")
        return ids

    def seed_audio(self) -> None:
        audio = self.root / "_audio"
        for folder in ("chapters", "state", "audio"):
            (audio / folder).mkdir(parents=True)
        (audio / "audio" / "sec1.mp3_44100_128.mp3").write_bytes(b"take")
        (audio / "audio" / "old.mp3_44100_128.mp3").write_bytes(b"stitched")
        (audio / "chapters" / "old.json").write_text(json.dumps(
            {"schema": 1, "file": "old.md", "stem": "old", "sections": []}))
        (audio / "state" / "old.json").write_text(json.dumps({
            "schema": 1,
            "sections": {"sec1": {"audioFiles": {
                "mp3_44100_128": "audio/sec1.mp3_44100_128.mp3"}}},
            "stitched": {"mp3_44100_128": "audio/old.mp3_44100_128.mp3"},
            "stitchKeys": {"mp3_44100_128": "k"}}))
        (audio / "audiobook.toml").write_text(
            '[text]\nvoice = "narrator"\n\n[[chapter]]\nfile = "old.md"\n'
            'voice = "guest"\n')
        (audio / "audiobook.json").write_text(json.dumps({
            "schema": 1, "chapters": [
                {"stem": "old", "file": "chapters/old.json", "title": "T"},
                {"stem": "other", "file": "chapters/other.json"}]}))
        (self.root / "_drafts").mkdir()
        (self.root / "_drafts" / "old.merged.md").write_text("merged")

    def test_rename_carries_every_record_to_the_new_name(self):
        ids = self.seed_records()
        self.seed_audio()
        document_id = docs.document_id(self.db, self.manuscript, "old.md")

        result = quiet(api.rename_document, self.db, self.manuscript, {},
                       "old.md", "new.md")

        self.assertEqual((result["from"], result["to"]), ("old.md", "new.md"))
        # The file moved; nothing else on disk carries the old name.
        self.assertFalse((self.root / "old.md").exists())
        self.assertEqual((self.root / "new.md").read_text(encoding="utf-8"),
                         OLD_TEXT)
        # Same document, new name, the old name in its history.
        self.assertEqual(
            docs.document_id(self.db, self.manuscript, "new.md"), document_id)
        self.assertEqual(docs.document_relpath(self.db, document_id), "new.md")
        self.assertEqual(docs.names_of(self.db, self.mid, "new.md"),
                         ["new.md", "old.md"])
        # Style attachment and the file-local law.
        self.assertEqual(self.attached_guide("new.md"), "essays")
        self.assertEqual(self.count("style_attachments", "file", "old.md"), 0)
        self.assertEqual(self.count("style_laws", "file", "new.md"), 1)
        # Summaries at all three levels, and the intent.
        for table in ("essay_summaries", "section_summaries",
                      "paragraph_summaries", "illus_proposals",
                      "filter_readings"):
            self.assertEqual(self.count(table, "file", "new.md"), 1, table)
            self.assertEqual(self.count(table, "file", "old.md"), 0, table)
        self.assertEqual(self.db.one(
            "SELECT scope FROM declared_intents WHERE id = ?",
            (ids["intent"],))["scope"], "new.md")
        # The thread is found by the prefix query the producers use.
        found = self.db.all(
            "SELECT * FROM doc_threads WHERE manuscript_id = ? AND "
            "origin_type = 'filter' AND file = ? AND origin_id LIKE ?",
            (self.mid, "new.md", "fr-abc:new.md:%"))
        self.assertEqual([r["id"] for r in found], [ids["thread"]])
        self.assertEqual(found[0]["scope_ref"], "new.md")
        comment = self.db.one(
            "SELECT file, location FROM doc_comments WHERE manuscript_id = ?",
            (self.mid,))
        self.assertEqual((comment["file"], comment["location"]),
                         ("new.md", "new.md#The Old Name"))
        self.assertEqual(self.count("concept_nodes", "introduced_in",
                                    "new.md"), 1)
        # JSON keyed by the name.
        critique = self.db.one("SELECT * FROM critique_passes WHERE id = ?",
                               (ids["pass"],))
        self.assertEqual(json.loads(critique["essay_state"]),
                         {"new.md": "resolved", "other.md": "pending"})
        self.assertEqual(json.loads(critique["pinned_versions"]),
                         {"new.md": ids["version"]})
        guidance = json.loads(self.db.one(
            "SELECT metadata FROM guidance_history WHERE id = ?",
            (ids["guidance"],))["metadata"])
        self.assertEqual(guidance["file"], "new.md")
        self.assertEqual(guidance["target_file"], "new.md")
        self.assertEqual(guidance["dedupe_key"], "judgment:lens:new.md:3")
        # The Doc link: same tab, re-keyed, its old title remembered.
        links = self.links()
        self.assertNotIn("old.md", links)
        self.assertEqual(links["new.md"]["tab_id"], "tab-2")
        self.assertEqual(links["new.md"]["pushed_hash"], "abc")
        self.assertEqual(links["new.md"]["tab_title"], "old.md")
        # toc.toml: the entry and the child's parent reference.
        toc = (self.root / "toc.toml").read_text(encoding="utf-8")
        self.assertEqual(
            structure.tree_to_parents(structure.parse_toc_tree(toc)),
            [("new.md", None), ("child.md", "new.md"), ("other.md", None)])
        self.assertNotIn("old.md", toc)
        # Audio: the takes still count as done under the new stem.
        audio = self.root / "_audio"
        state = generation.load_state(audio, "new")
        self.assertEqual(
            generation.take_of(audio, state, "sec1", "mp3_44100_128"),
            "audio/sec1.mp3_44100_128.mp3")
        self.assertEqual(state["stitched"]["mp3_44100_128"],
                         "audio/new.mp3_44100_128.mp3")
        self.assertTrue((audio / "audio" / "new.mp3_44100_128.mp3").exists())
        self.assertFalse((audio / "state" / "old.json").exists())
        chapter = json.loads((audio / "chapters" / "new.json").read_text())
        self.assertEqual((chapter["file"], chapter["stem"]),
                         ("new.md", "new"))
        book = json.loads((audio / "audiobook.json").read_text())
        self.assertEqual(book["chapters"][0],
                         {"stem": "new", "file": "chapters/new.json",
                          "title": "T"})
        self.assertIn('file = "new.md"',
                      (audio / "audiobook.toml").read_text())
        self.assertEqual(
            (self.root / "_drafts" / "new.merged.md").read_text(), "merged")
        # The collect that follows records one rename and nothing else.
        self.assertEqual(self.at(result["collect"], "new.md"),
                         ["file_renamed"])
        self.assertEqual(self.at(result["collect"], "old.md"), [])
        self.assertEqual(result["collect"]["new_paragraphs"],
                         TOC_STANZAS_NAMING_OLD)

    def test_old_snapshots_are_read_through_the_name_history(self):
        version = self.db.one(
            "SELECT id FROM manuscript_versions WHERE manuscript_id = ? "
            "ORDER BY version_no DESC LIMIT 1", (self.mid,))["id"]
        base_hash = gdocs.text_hash(OLD_TEXT)
        quiet(api.rename_document, self.db, self.manuscript, {},
              "old.md", "new.md")
        (self.root / "new.md").write_text(OLD_TEXT + "\nMore.\n",
                                          encoding="utf-8")
        quiet(api.collect, self.db, self.manuscript, {})
        self.assertEqual(revisions._version_text(self.db, version, "new.md"),
                         OLD_TEXT)
        self.assertEqual(
            gdocs.find_pushed_base(self.db, self.manuscript, "new.md",
                                   base_hash), OLD_TEXT)

    def test_the_next_push_retitles_the_tab_and_adds_no_second_one(self):
        self.link("old.md", tab_id="tab-2", checked_out=False)
        for name, tab in (("child.md", "tab-3"), ("other.md", "tab-4")):
            self.link(name, tab_id=tab, checked_out=False)
        fake = _SurgicalDocFake(
            [("book", ""), ("old.md", OLD_TEXT), ("child.md", "x"),
             ("other.md", OTHER_TEXT)])
        quiet(api.rename_document, self.db, self.manuscript, {},
              "old.md", "new.md")
        manuscript = api.get_manuscript(self.db)
        meta = gdocs._mapping(self.db, manuscript)
        gdocs.ensure_master(self.db, manuscript, meta, fake, fake)
        chapter_tabs = sorted(t["title"] for t in fake.tabs
                              if t["title"].endswith(".md"))
        self.assertEqual(chapter_tabs, ["child.md", "new.md", "other.md"])
        links = self.links()
        self.assertEqual(links["new.md"]["tab_id"], "tab-2")
        self.assertNotIn("tab_title", links["new.md"])

    def test_a_failed_rename_puts_every_file_and_row_back(self):
        api.define_style_guide(self.db, self.manuscript, "essays")
        api.attach_style(self.db, self.manuscript, "old.md", "essays")
        self.seed_audio()
        toc_before = (self.root / "toc.toml").read_text(encoding="utf-8")
        document_id = docs.document_id(self.db, self.manuscript, "old.md")
        saved = docs._carry_audio

        def fails(*args, **kwargs):
            saved(*args, **kwargs)
            raise OSError("disk full")

        docs._carry_audio = fails
        try:
            with self.assertRaises(OSError):
                docs.rename_doc(self.db, self.manuscript, "old.md", "new.md")
        finally:
            docs._carry_audio = saved
        self.assertTrue((self.root / "old.md").exists())
        self.assertFalse((self.root / "new.md").exists())
        self.assertEqual((self.root / "toc.toml").read_text(encoding="utf-8"),
                         toc_before)
        self.assertTrue((self.root / "_audio" / "state" / "old.json").exists())
        self.assertTrue((self.root / "_audio" / "audio"
                         / "old.mp3_44100_128.mp3").exists())
        self.assertEqual(self.attached_guide("old.md"), "essays")
        self.assertEqual(docs.document_relpath(self.db, document_id), "old.md")

    def test_force_renames_and_lists_what_stayed_behind(self):
        self.insert("essay_summaries", "es", file="old.md", summary="mine",
                    source_hash="h", upstream_hash="u")
        self.insert("essay_summaries", "es", file="gone.md", summary="theirs",
                    source_hash="h", upstream_hash="u", status="deprecated")
        result = quiet(api.rename_document, self.db, self.manuscript, {},
                       "old.md", "gone.md", force=True)
        self.assertTrue((self.root / "gone.md").exists())
        self.assertEqual(len(result["left"]), 1)
        self.assertIn("essay_summaries", result["left"][0])
        # The colliding row stays under the old name; nothing is merged.
        self.assertEqual(self.count("essay_summaries", "file", "old.md"), 1)
        self.assertEqual(self.db.one(
            "SELECT summary FROM essay_summaries WHERE manuscript_id = ? "
            "AND file = 'gone.md'", (self.mid,))["summary"], "theirs")

    def test_cli_verb_renames_and_dry_run_writes_nothing(self):
        argv = ["--workspace", str(self.workspace), "doc", "rename",
                "old.md", "new.md"]
        out = io.StringIO()
        versions = self.db.one(
            "SELECT COUNT(*) AS n FROM manuscript_versions")["n"]
        document_id = docs.document_id(self.db, self.manuscript, "old.md")
        with contextlib.redirect_stdout(out):
            cli_main(argv + ["--dry-run"])
        self.assertTrue((self.root / "old.md").exists())
        self.assertFalse((self.root / "new.md").exists())
        self.assertEqual(versions, self.db.one(
            "SELECT COUNT(*) AS n FROM manuscript_versions")["n"])
        self.assertEqual(docs.document_relpath(self.db, document_id), "old.md")
        self.assertIn('file = "old.md"',
                      (self.root / "toc.toml").read_text(encoding="utf-8"))
        self.assertIn("old.md", out.getvalue())
        self.assertIn("new.md", out.getvalue())
        self.assertIn("toc.toml", out.getvalue())
        quiet(cli_main, argv)
        self.assertTrue((self.root / "new.md").exists())
        self.assertFalse((self.root / "old.md").exists())


class RenameDeclinedTests(Workspace):

    def assert_declined(self, expected: str, new: str = "new.md",
                       old: str = "old.md") -> None:
        toc_before = (self.root / "toc.toml").read_text(encoding="utf-8")
        versions_before = self.db.one(
            "SELECT COUNT(*) AS n FROM manuscript_versions")["n"]
        with self.assertRaises((ValueError, LookupError,
                                FileExistsError)) as caught:
            quiet(api.rename_document, self.db, self.manuscript, {}, old, new)
        self.assertIn(expected, str(caught.exception))
        # A declined rename leaves the folder and the record as they were.
        self.assertTrue((self.root / "old.md").exists())
        self.assertEqual((self.root / "toc.toml").read_text(encoding="utf-8"),
                         toc_before)
        self.assertEqual(versions_before, self.db.one(
            "SELECT COUNT(*) AS n FROM manuscript_versions")["n"])

    def test_declined_while_a_filter_run_is_live(self):
        self.insert("filter_runs", "fr", filter="plain", file="old.md",
                    status="active", **{"class": "sequential"})
        self.assert_declined("filter run")

    def test_declined_while_a_writeup_is_in_flight(self):
        self.insert("writeups", "wu", intent_id="in-x", file="old.md",
                    status="active")
        self.assert_declined("writeup")

    def test_declined_while_checked_out_to_google_docs(self):
        self.link("old.md", tab_id="tab-2", checked_out=True)
        self.assert_declined("checked out")

    def test_declined_when_the_new_name_is_taken(self):
        self.assert_declined("already exists", new="other.md")

    def test_declined_when_the_new_name_left_records_behind(self):
        self.insert("essay_summaries", "es", file="gone.md", summary="S",
                    source_hash="h", upstream_hash="u", status="deprecated")
        self.assert_declined("--force", new="gone.md")

    def test_declined_when_the_new_name_still_has_a_doc_link(self):
        self.link("gone.md", tab_id="tab-9", checked_out=False)
        self.assert_declined("a Doc link", new="gone.md")

    def test_declined_for_the_table_of_contents(self):
        self.assert_declined("toc.toml", old="toc.toml", new="contents.toml")

    def test_declined_for_a_name_authorlm_reserves_or_cannot_see(self):
        self.assert_declined("reserves", new="toc.toml")
        self.assert_declined("reserves", new="pronunciations.md")
        self.assert_declined("invisible", new="_drafts/new.md")
        self.assert_declined("inside the manuscript", new="../new.md")
        self.assert_declined("already has that name", new="old.md")

    def test_a_name_without_an_extension_becomes_markdown(self):
        result = quiet(api.rename_document, self.db, self.manuscript, {},
                       "old.md", "papers/new")
        self.assertEqual(result["to"], "papers/new.md")
        self.assertTrue((self.root / "papers" / "new.md").exists())


class RenameOnDiskTests(Workspace):

    def test_a_rename_on_disk_heals_at_the_next_collect(self):
        api.define_style_guide(self.db, self.manuscript, "essays")
        api.attach_style(self.db, self.manuscript, "old.md", "essays")
        self.insert("essay_summaries", "es", file="old.md", summary="S",
                    source_hash="h", upstream_hash="u")
        self.link("old.md", tab_id="tab-2", checked_out=False)
        document_id = docs.document_id(self.db, self.manuscript, "old.md")
        (self.root / "old.md").rename(self.root / "new.md")

        report = quiet(api.collect, self.db, self.manuscript, {}, auto=True)

        # Not held as a suspicious deletion, one rename, nothing new.
        self.assertNotIn("staged", report)
        self.assertEqual(self.kinds(report).count("file_renamed"), 1)
        self.assertNotIn("file_added", self.kinds(report))
        self.assertNotIn("file_removed", self.kinds(report))
        self.assertEqual(self.at(report, "new.md"), ["file_renamed"])
        self.assertEqual(report["new_paragraphs"], TOC_STANZAS_NAMING_OLD)
        self.assertEqual(report["new_files"], [])
        self.assertEqual(report["renamed"],
                         [{"from": "old.md", "to": "new.md"}])
        # The same carry-over as the verb.
        self.assertEqual(
            docs.document_id(self.db, self.manuscript, "new.md"), document_id)
        self.assertEqual(self.attached_guide("new.md"), "essays")
        row = self.db.one(
            "SELECT status FROM essay_summaries WHERE manuscript_id = ? "
            "AND file = 'new.md'", (self.mid,))
        self.assertEqual(row["status"], "current")
        self.assertEqual(self.links()["new.md"]["tab_id"], "tab-2")
        toc = (self.root / "toc.toml").read_text(encoding="utf-8")
        self.assertIn('file = "new.md"', toc)
        self.assertIn('parent = "new.md"', toc)
        # And the collect after it has nothing left to do.
        self.assertEqual(quiet(api.collect, self.db, self.manuscript, {}),
                         {"unchanged": True})

    def test_a_writeup_in_flight_keeps_its_pinned_text_across_a_rename(self):
        """The verb declines this state; a rename in Finder cannot be
        declined, so the carry must leave the writeup whole: its pinned
        source is in a snapshot that knows the file by its old name."""
        version = self.db.one(
            "SELECT id FROM manuscript_versions WHERE manuscript_id = ? "
            "ORDER BY version_no DESC LIMIT 1", (self.mid,))["id"]
        self.insert("writeups", "wu", intent_id="in-x", file="old.md",
                    status="active", source_version_id=version)
        (self.root / "old.md").rename(self.root / "new.md")
        quiet(api.collect, self.db, self.manuscript, {})
        self.assertEqual(summaries.inflight_sources(self.db, self.manuscript),
                         {"new.md": OLD_TEXT})

    def test_a_diff_across_the_rename_shows_one_file_not_two(self):
        edited = OLD_TEXT.replace("number 5", "number five")
        (self.root / "old.md").unlink()
        (self.root / "new.md").write_text(edited, encoding="utf-8")
        quiet(api.collect, self.db, self.manuscript, {})
        diff = api.diff_versions(self.db, self.manuscript)
        self.assertNotIn("old.md", diff["files"])
        body = [line for line in diff["files"]["new.md"]
                if line[:1] in "+-" and line[:3] not in ("+++", "---")]
        self.assertEqual(len(body), 2)  # the one edited line, old and new
        self.assertTrue(diff["files"]["new.md"][0].endswith("/old.md"))

    def test_a_rename_with_small_edits_still_pairs(self):
        edited = OLD_TEXT.replace("paragraph 3 says something particular",
                                  "paragraph 3 says something quite specific")
        self.assertNotEqual(edited, OLD_TEXT)
        (self.root / "old.md").unlink()
        (self.root / "new.md").write_text(edited, encoding="utf-8")
        report = quiet(api.collect, self.db, self.manuscript, {}, auto=True)
        self.assertNotIn("staged", report)
        kinds = self.kinds(report)
        self.assertEqual(kinds.count("file_renamed"), 1)
        self.assertNotIn("file_added", kinds)
        self.assertNotIn("file_removed", kinds)
        edits = [t for t in report["transitions"]
                 if t["kind"] == "rewrite" and t["location"].startswith("new.md")]
        self.assertEqual(len(edits), 1)
        # One rewritten paragraph, not a file's worth of new ones.
        self.assertEqual(report["new_paragraphs"],
                         1 + TOC_STANZAS_NAMING_OLD)

    def test_two_unrelated_files_do_not_pair(self):
        (self.root / "old.md").unlink()
        (self.root / "fresh.md").write_text(
            essay("Something Else", "gamma"), encoding="utf-8")
        held = quiet(api.collect, self.db, self.manuscript, {}, auto=True)
        self.assertEqual([s["file"] for s in held["staged"]], ["old.md"])
        report = quiet(api.collect, self.db, self.manuscript, {})
        kinds = self.kinds(report)
        self.assertIn("file_removed", kinds)
        self.assertIn("file_added", kinds)
        self.assertNotIn("file_renamed", kinds)
        self.assertEqual(report["new_paragraphs"], 9)


class DocumentIdsSwitchTests(Workspace):
    """Document ids are a per-manuscript setting
    (`manuscripts.metadata.document_ids.enabled`): on for a manuscript
    registered from now on, off (absent) for one that already existed,
    which then behaves exactly as it did before documents had ids until
    the owner turns the setting on, after a backup."""

    def make_existing(self) -> None:
        """This manuscript as one registered before the setting existed:
        no `document_ids` key in its metadata, no document rows."""
        meta = json.loads(self.db.one(
            "SELECT metadata FROM manuscripts WHERE id = ?",
            (self.mid,))["metadata"])
        meta.pop("document_ids")
        self.db.update("manuscripts", self.mid,
                       {"metadata": json.dumps(meta)})
        self.db.conn.execute("DELETE FROM documents WHERE manuscript_id = ?",
                             (self.mid,))
        self.db.conn.commit()

    def document_rows(self) -> int:
        return self.db.one("SELECT COUNT(*) AS n FROM documents")["n"]

    def test_a_manuscript_registered_now_has_ids_from_its_first_collect(self):
        self.assertTrue(docs.document_ids_enabled(self.db, self.manuscript))
        self.assertEqual(self.document_rows(), 3)

    def test_a_manuscript_that_already_existed_has_them_off(self):
        self.make_existing()
        self.assertFalse(docs.document_ids_enabled(self.db, self.manuscript))

    def test_off_a_rename_on_disk_collects_exactly_as_it_did_before(self):
        self.make_existing()
        api.define_style_guide(self.db, self.manuscript, "essays")
        api.attach_style(self.db, self.manuscript, "old.md", "essays")
        self.link("old.md", tab_id="tab-2", checked_out=False)
        toc_before = (self.root / "toc.toml").read_text(encoding="utf-8")
        (self.root / "old.md").rename(self.root / "new.md")

        # The auto path holds it as a suspicious deletion, as before.
        held = quiet(api.collect, self.db, self.manuscript, {}, auto=True)
        self.assertEqual(
            held, {"staged": [{"file": "old.md", "removed_percent": 100}]})

        # A manual collect records a removal and an addition, as before:
        # the transitions and counts below are what the code on main
        # writes for this change, worked out from revisions.py there.
        report = quiet(api.collect, self.db, self.manuscript, {})
        self.assertEqual(report["transitions"], [
            {"kind": "file_added", "location": "new.md",
             "summary": "New file: new.md"},
            {"kind": "file_removed", "location": "old.md",
             "summary": "File removed: old.md"}])
        self.assertEqual(report["new_files"], ["new.md"])
        self.assertEqual(report["new_paragraphs"], 9)  # the title and 8
        for key in ("renamed", "renamed_left", "renamed_tabs"):
            self.assertNotIn(key, report)
        stored = [(r["kind"], r["location"], r["summary"], r["detail"])
                  for r in self.db.all(
                      "SELECT * FROM editorial_transitions WHERE "
                      "version_after = (SELECT id FROM manuscript_versions "
                      "ORDER BY version_no DESC LIMIT 1) ORDER BY location")]
        self.assertEqual(stored, [
            ("file_added", "new.md", "New file: new.md",
             '{"paragraphs": 9}'),
            ("file_removed", "old.md", "File removed: old.md", "{}")])
        # Nothing was carried, seeded or rewritten.
        self.assertEqual(self.document_rows(), 0)
        self.assertEqual(self.attached_guide("old.md"), "essays")
        self.assertIsNone(self.attached_guide("new.md"))
        self.assertEqual(self.links()["old.md"]["tab_id"], "tab-2")
        self.assertNotIn("new.md", self.links())
        self.assertEqual((self.root / "toc.toml").read_text(encoding="utf-8"),
                         toc_before)

    def test_off_doc_rename_is_declined_and_names_the_setting(self):
        self.make_existing()
        with self.assertRaises(ValueError) as caught:
            quiet(api.rename_document, self.db, self.manuscript, {},
                  "old.md", "new.md")
        self.assertIn("authorlm manuscript set --document-ids on -m book",
                      str(caught.exception))
        self.assertTrue((self.root / "old.md").exists())
        self.assertEqual(self.document_rows(), 0)

    def test_off_no_resolver_call_seeds_a_row(self):
        self.make_existing()
        self.assertEqual(docs.ensure_documents(self.db, self.manuscript), [])
        self.assertEqual(docs.list_documents(self.db, self.manuscript), [])
        with self.assertRaises(LookupError) as caught:
            docs.document_id(self.db, self.manuscript, "old.md")
        self.assertIn("--document-ids on", str(caught.exception))
        self.assertEqual(self.document_rows(), 0)

    def test_turning_it_on_backs_up_first_then_seeds_every_file(self):
        self.make_existing()
        folder = backup.backup_dir(self.db.path)
        before = set(folder.iterdir()) if folder.exists() else set()
        result = api.update_manuscript_metadata(
            self.db, self.manuscript, document_ids="on")["document_ids"]
        made = set(folder.iterdir()) - before
        self.assertEqual(len(made), 1)
        self.assertEqual(result["backup"], str(next(iter(made))))
        # The backup holds the state BEFORE the change: no rows, no key.
        import sqlite3
        copy = sqlite3.connect(result["backup"])
        try:
            self.assertEqual(copy.execute(
                "SELECT COUNT(*) FROM documents").fetchone()[0], 0)
            self.assertNotIn("document_ids", json.loads(copy.execute(
                "SELECT metadata FROM manuscripts").fetchone()[0]))
        finally:
            copy.close()
        self.assertEqual(result["enabled"], True)
        self.assertEqual(result["seeded"],
                         ["child.md", "old.md", "other.md"])
        self.assertTrue(docs.document_ids_enabled(self.db, self.manuscript))
        self.assertEqual(self.document_rows(), 3)
        # And from here a rename on disk heals.
        (self.root / "old.md").rename(self.root / "new.md")
        report = quiet(api.collect, self.db, self.manuscript, {}, auto=True)
        self.assertEqual(report["renamed"],
                         [{"from": "old.md", "to": "new.md"}])

    def test_it_stays_off_when_the_backup_fails(self):
        self.make_existing()
        saved = backup.perform_backup
        backup.perform_backup = lambda db: {"ok": False, "error": "disk full"}
        try:
            with self.assertRaises(ValueError) as caught, \
                    contextlib.redirect_stderr(io.StringIO()):
                api.update_manuscript_metadata(
                    self.db, self.manuscript, document_ids="on")
        finally:
            backup.perform_backup = saved
        self.assertIn("disk full", str(caught.exception))
        self.assertFalse(docs.document_ids_enabled(self.db, self.manuscript))
        self.assertEqual(self.document_rows(), 0)

    def test_turning_it_off_keeps_the_rows_and_stops_the_pairing(self):
        api.update_manuscript_metadata(self.db, self.manuscript,
                                       document_ids="off")
        self.assertFalse(docs.document_ids_enabled(self.db, self.manuscript))
        self.assertEqual(self.document_rows(), 3)
        (self.root / "old.md").rename(self.root / "new.md")
        report = quiet(api.collect, self.db, self.manuscript, {})
        self.assertEqual(sorted(self.kinds(report)),
                         ["file_added", "file_removed"])

    def test_the_manuscript_verb_shows_and_sets_it(self):
        self.make_existing()
        argv = ["--workspace", str(self.workspace), "manuscript"]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli_main(argv + ["show"])
        self.assertIn("document_ids: off", out.getvalue())
        self.assertIn("manuscript set --document-ids on", out.getvalue())
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli_main(argv + ["set", "--document-ids", "on"])
        self.assertIn("document_ids: on", out.getvalue())
        self.assertIn("3 document", out.getvalue())
        self.assertIn(str(backup.backup_dir(self.db.path)), out.getvalue())
        self.assertEqual(self.document_rows(), 3)


class TocRenameTests(unittest.TestCase):
    """`structure.rename_toc_file` is pure text in, text out."""

    def test_only_the_lines_that_name_the_file_change(self):
        toc = ('# The book\n'
               'outside = ["papers/", "old.md"]\n\n'
               '[[chapter]]\nfile = "old.md"   \nmatter = "front"\n\n'
               '[[chapter]]\nfile = "threshold.md"\nparent = \'old.md\'\n\n'
               '[[chapter]]\nfile = "cold.md"\n')
        self.assertEqual(
            structure.rename_toc_file(toc, "old.md", "new.md"),
            toc.replace('"old.md"', '"new.md"').replace(
                "'old.md'", "'new.md'"))
        self.assertIn('file = "cold.md"',
                      structure.rename_toc_file(toc, "old.md", "new.md"))
        self.assertEqual(structure.rename_toc_file(toc, "absent.md", "x.md"),
                         toc)


class PairingTests(unittest.TestCase):
    """`revisions.pair_renames` is pure: old and new snapshots in, pairs
    out."""

    def test_pairs_are_one_to_one_and_the_same_on_every_run(self):
        text = essay("Twin", "delta")
        old = {"b.md": text, "a.md": text, "keep.md": "k"}
        new = {"z.md": text, "keep.md": "k"}
        self.assertEqual(revisions.pair_renames(old, new), {"a.md": "z.md"})
        self.assertEqual(revisions.pair_renames(old, new), {"a.md": "z.md"})

    def test_the_closest_text_wins(self):
        text = essay("Twin", "delta")
        near = text.replace("number 2", "number two")
        old = {"a.md": text}
        new = {"m.md": near, "n.md": text}
        self.assertEqual(revisions.pair_renames(old, new), {"a.md": "n.md"})

    def test_text_below_the_floor_is_not_a_rename(self):
        old = {"a.md": essay("One", "delta")}
        new = {"b.md": essay("Two", "epsilon")}
        self.assertEqual(revisions.pair_renames(old, new), {})

    def test_empty_and_placeholder_files_never_pair(self):
        self.assertEqual(revisions.pair_renames({"a.md": ""}, {"b.md": ""}),
                         {})
        self.assertEqual(revisions.pair_renames(
            {"a.md": revisions.PLACEHOLDER}, {"b.md": revisions.PLACEHOLDER}),
            {})

    def test_the_table_of_contents_is_never_paired(self):
        text = essay("Twin", "delta")
        self.assertEqual(revisions.pair_renames(
            {"toc.toml": text}, {"b.md": text}), {})


class DeveloperEditRunTests(Workspace):

    def test_a_run_folder_follows_the_rename(self):
        folder = self.root / "_devedit" / "2026-11-13"
        (folder / "findings").mkdir(parents=True)
        (folder / "checks").mkdir()
        (folder / "stages" / "1" / "arcs").mkdir(parents=True)
        (folder / "state.json").write_text(json.dumps({
            "run": "2026-11-13", "coverage": {
                "edit": ["old.md", "other.md"], "reference": [],
                "confirmed": "now"},
            "stages": {"1": {"files": ["old.md"]}}}))
        (folder / "findings" / "old.json").write_text(json.dumps(
            {"findings": [{"finding": "f", "items": [
                {"tier": "must", "original": "a", "proposed": "b",
                 "file": "old.md"}]}]}))
        (folder / "findings" / "old--p2.json").write_text("[]")
        (folder / "checks" / "old-1.1.json").write_text(
            json.dumps({"result": "pass"}))
        (folder / "stages" / "1" / "arcs" / "old.json").write_text("{}")
        (folder / "notebook.md").write_text(
            "## old.md — pass 1\n\nnotes\n\n## other.md — pass 1\n\nmore\n")
        self.insert("devedit_items", "di", run="2026-11-13",
                    item_id="old-1.1", file="old.md", queued=1)
        self.insert("devedit_items", "di", run="2026-11-13",
                    item_id="other-1.1", file="other.md", queued=1)
        self.insert("edit_events", "ev", source="devedit", run="2026-11-13",
                    item_id="old-1.1", file="old.md", event="verdict",
                    at="now")

        quiet(api.rename_document, self.db, self.manuscript, {},
              "old.md", "new.md")

        state = json.loads((folder / "state.json").read_text())
        self.assertEqual(state["coverage"]["edit"], ["new.md", "other.md"])
        self.assertEqual(state["stages"]["1"]["files"], ["new.md"])
        self.assertEqual(sorted(p.name for p in
                                (folder / "findings").iterdir()),
                         ["new--p2.json", "new.json"])
        self.assertTrue((folder / "checks" / "new-1.1.json").exists())
        self.assertTrue(
            (folder / "stages" / "1" / "arcs" / "new.json").exists())
        self.assertEqual(devedit.finished(folder), ["new.md", "other.md"])
        items = sorted(r["item_id"] for r in self.db.all(
            "SELECT item_id FROM devedit_items WHERE manuscript_id = ? "
            "AND file = 'new.md'", (self.mid,)))
        self.assertEqual(items, ["new-1.1"])
        self.assertEqual(self.count("devedit_items", "item_id", "other-1.1"),
                         1)
        event = self.db.one("SELECT item_id, file FROM edit_events")
        self.assertEqual((event["item_id"], event["file"]),
                         ("new-1.1", "new.md"))
        # Gathering again mints the same ids, so verdicts keep their rows.
        gathered = [i["id"] for i in devedit.gather(self.db, folder)]
        self.assertEqual(gathered, ["new-1.1"])


class SchemaTripwireTests(unittest.TestCase):
    """Editing this test is the act that decides what a new name-bearing
    column does on a rename: carried (docs.NAME_COLUMNS and its JSON
    siblings) or kept as recorded history (docs.NAME_HISTORY_COLUMNS)."""

    def test_every_column_that_holds_a_file_name_is_classified(self):
        suspects = set()
        for table, body in re.findall(
                r"CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\n\);", SCHEMA,
                re.S):
            for line in body.splitlines():
                match = re.match(r"\s*(\w+)\s+TEXT", line)
                if match and (match.group(1) in (
                        "file", "files", "scope", "location",
                        "introduced_in", "relpath", "scope_ref", "origin_id")
                        or "relpath" in line or "file ->" in line):
                    suspects.add((table, match.group(1)))
        classified = (set(docs.NAME_COLUMNS) | set(docs.NAME_JSON_COLUMNS)
                      | set(docs.NAME_DERIVED_COLUMNS)
                      | set(docs.NAME_HISTORY_COLUMNS)
                      | {("documents", "relpath")})
        self.assertEqual(suspects - classified, set())


if __name__ == "__main__":
    unittest.main()
