"""The version store (revisions.py): each distinct file text kept once,
a version as the list of its files' names and text hashes, and the
conversion of a workspace that still keeps each version's whole text as
JSON in `manuscript_versions.files`.

What must hold: a collect stores only texts the store does not already
have; one text is shared by every version and manuscript that holds it;
an unchanged collect records nothing; a failure mid-record leaves no part
of a version; and every reader returns, after conversion, exactly what
reading the old JSON returned. The ids, numbers and checksums other
tables name versions by never change.
"""

from __future__ import annotations

import os

os.environ["AUTHORLM_CONFIG"] = "/nonexistent/authorlm-version-store/config.toml"
os.environ["AUTHORLM_ENV"] = "/nonexistent/authorlm-version-store/.env"
os.environ["AUTHORLM_CLIENT"] = "none"

import contextlib  # noqa: E402
import io  # noqa: E402
import json  # noqa: E402
import sqlite3  # noqa: E402
import sys  # noqa: E402
import tempfile  # noqa: E402
import unittest  # noqa: E402
from pathlib import Path  # noqa: E402
from unittest import mock  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import authorlm  # noqa: E402

assert Path(authorlm.__file__).resolve().parent == HERE.parent / "authorlm", (
    f"testing the wrong package: {authorlm.__file__}")

from authorlm import api, revisions  # noqa: E402
from authorlm.cli import main as cli_main  # noqa: E402
from authorlm.db import KNOWLEDGE_OBJECT_COLUMNS, Database, ko_fields  # noqa: E402

# The table as every workspace made before the version store has it.
LEGACY_VERSIONS = f"""
CREATE TABLE manuscript_versions (
    {KNOWLEDGE_OBJECT_COLUMNS},
    manuscript_id TEXT NOT NULL REFERENCES manuscripts(id),
    version_no INTEGER NOT NULL,
    checksum TEXT NOT NULL,
    files TEXT NOT NULL,
    source TEXT NOT NULL,
    session_id TEXT
);
"""


def count(db: Database, table: str) -> int:
    return db.one(f"SELECT COUNT(*) AS n FROM {table}")["n"]


class Fresh(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="authorlm-store-")
        self.db = Database(Path(self.tmp.name) / "authorlm.db")

    def tearDown(self) -> None:
        self.db.conn.close()
        self.tmp.cleanup()

    def record(self, files, mid="m-1", source="test"):
        return revisions.record_version(self.db, mid, files, None, source)


class RecordTests(Fresh):
    def test_a_collect_stores_only_the_texts_that_changed(self):
        book = {f"{n:02}.md": f"Essay {n}.\n" for n in range(1, 11)}
        self.record(book)
        self.assertEqual(count(self.db, "version_texts"), 10)
        book["03.md"] = "Essay 3, revised.\n"
        v2 = self.record(book)
        self.assertEqual(count(self.db, "version_texts"), 11)
        self.assertEqual(count(self.db, "version_files"), 20)
        self.assertEqual(revisions.files_at(self.db, v2["id"]), book)

    def test_one_text_is_shared_across_versions_and_manuscripts(self):
        self.record({"a.md": "Same words.\n", "b.md": "Other.\n"})
        self.record({"a.md": "Same words.\n", "b.md": "Other, edited.\n"})
        self.record({"elsewhere.md": "Same words.\n"}, mid="m-2")
        self.assertEqual(count(self.db, "version_texts"), 3)
        digest = revisions.text_hash("Same words.\n")
        self.assertEqual(self.db.one(
            "SELECT COUNT(*) AS n FROM version_files WHERE text_hash = ?",
            (digest,))["n"], 3)

    def test_an_unchanged_collect_records_nothing(self):
        first = self.record({"a.md": "x\n"})
        self.assertIsNone(self.record({"a.md": "x\n"}))
        self.assertEqual(count(self.db, "manuscript_versions"), 1)
        self.assertEqual(revisions.latest_version(self.db, "m-1")["id"],
                         first["id"])

    def test_numbers_and_checksum_are_what_they_always_were(self):
        files = {"b.md": "two\n", "a.md": "one\n"}
        v1 = self.record(files)
        v2 = self.record({**files, "c.md": "three\n"})
        self.assertEqual((v1["version_no"], v2["version_no"]), (1, 2))
        self.assertEqual(v1["checksum"], revisions.checksum(files))
        self.assertTrue(v1["id"].startswith("mv-"))

    def test_a_failure_mid_record_leaves_no_part_of_a_version(self):
        self.record({"a.md": "kept\n"})
        real = revisions._store_text
        calls = []

        def failing(db, text):
            calls.append(text)
            if len(calls) == 2:
                raise OSError("disk went away")
            return real(db, text)

        with mock.patch.object(revisions, "_store_text", failing):
            with self.assertRaises(OSError):
                self.record({"a.md": "new one\n", "b.md": "new two\n"})
        self.assertEqual(count(self.db, "manuscript_versions"), 1)
        self.assertEqual(count(self.db, "version_files"), 1)
        self.assertEqual(count(self.db, "version_texts"), 1)
        self.assertEqual(revisions.latest_version(self.db, "m-1")["version_no"],
                         1)
        # And the next record goes on as if the failed one never started.
        v2 = self.record({"a.md": "new one\n", "b.md": "new two\n"})
        self.assertEqual(v2["version_no"], 2)

    def test_order_and_any_text_round_trip(self):
        files = {"z.md": "last name, first place\n",
                 "sub/a.md": "naïve — “quoted” ✓ �\n",
                 "toc.toml": "",
                 "odd.md": "tab\there, CR\r\n, NUL \x00 kept\n"}
        version = self.record(files)
        self.assertEqual(list(revisions.files_at(self.db, version["id"])
                              .items()), list(files.items()))
        self.assertEqual(revisions.file_at(self.db, version["id"], "odd.md"),
                         files["odd.md"])

    def test_collect_revision_reads_the_folder(self):
        root = Path(self.tmp.name) / "book"
        root.mkdir()
        (root / "one.md").write_text("Hello.\n", encoding="utf-8")
        manuscript = {"id": "m-9", "path": str(root)}
        version = revisions.collect_revision(self.db, manuscript, None)
        self.assertEqual(revisions.files_at(self.db, version["id"]),
                         {"one.md": "Hello.\n"})
        self.assertIsNone(revisions.collect_revision(self.db, manuscript,
                                                     None))


class ReaderTests(Fresh):
    def setUp(self) -> None:
        super().setUp()
        self.v1 = self.record({"a.md": "A1\n", "b.md": "B1\n"})
        self.v2 = self.record({"a.md": "A2\n", "b.md": "B1\n"})
        self.v3 = self.record({"a.md": "A2\n", "c.md": "C1\n"})

    def test_lookups(self):
        db = self.db
        self.assertEqual(revisions.latest_version(db, "m-1")["id"],
                         self.v3["id"])
        self.assertEqual([v["id"] for v in revisions.latest_versions(
            db, "m-1", 2)], [self.v3["id"], self.v2["id"]])
        self.assertEqual(revisions.version_by_number(db, "m-1", 2)["id"],
                         self.v2["id"])
        self.assertIsNone(revisions.version_by_number(db, "m-1", 9))
        self.assertEqual([v["version_no"] for v in revisions.list_versions(
            db, "m-1")], [1, 2, 3])
        self.assertEqual(revisions.version_by_id(db, self.v1["id"])["checksum"],
                         self.v1["checksum"])
        self.assertIsNone(revisions.version_by_id(db, "mv-nope"))
        self.assertIsNone(revisions.latest_version(db, "m-none"))
        self.assertEqual(revisions.count_versions(db, "m-1"), 3)

    def test_as_of_a_time(self):
        db = self.db
        self.assertEqual(revisions.version_as_of(
            db, "m-1", self.v2["created_at"])["id"], self.v2["id"])
        self.assertIsNone(revisions.version_as_of(db, "m-1", "2000-01-01"))
        self.assertEqual(revisions.version_as_of(
            db, "m-1", "9999-01-01")["id"], self.v3["id"])

    def test_texts(self):
        db = self.db
        self.assertEqual(revisions.files_at(db, self.v3["id"]),
                         {"a.md": "A2\n", "c.md": "C1\n"})
        self.assertEqual(revisions.files_at(db, None), {})
        self.assertEqual(revisions.file_at(db, self.v1["id"], "b.md"), "B1\n")
        self.assertIsNone(revisions.file_at(db, self.v3["id"], "b.md"))
        self.assertEqual(revisions.file_counts(db, "m-1"),
                         {self.v1["id"]: 2, self.v2["id"]: 2,
                          self.v3["id"]: 2})
        self.assertEqual(revisions.changed_names(db, self.v2["id"],
                                                 self.v3["id"]),
                         {"b.md", "c.md"})
        self.assertEqual(revisions.changed_names(db, None, self.v1["id"]),
                         {"a.md", "b.md"})
        history = revisions.file_history(db, "m-1", "a.md")
        self.assertEqual([n for n, _ in history], [3, 2, 1])
        self.assertEqual(revisions.text_of(db, history[-1][1]), "A1\n")

    def test_a_file_as_it_stood_at_a_time(self):
        """The shape the workflow session's `file_as_of` takes on top of
        this door: the version current at a time, then one file of it."""
        version = revisions.version_as_of(self.db, "m-1",
                                          self.v1["created_at"])
        self.assertEqual(revisions.file_at(self.db, version["id"], "a.md"),
                         "A1\n")

    def test_prune_drops_only_texts_no_version_holds(self):
        self.record({"only.md": "mine alone\n"}, mid="m-2")
        before = count(self.db, "version_texts")
        for table in ("version_files", "manuscript_versions"):
            self.db.conn.execute(f"DELETE FROM {table} WHERE manuscript_id "
                                 "= 'm-2'")
        self.assertEqual(revisions.prune_texts(self.db), 1)
        self.assertEqual(count(self.db, "version_texts"), before - 1)
        self.assertTrue(revisions.check_store(self.db)["ok"])


def legacy_workspace(root: Path) -> tuple[Path, list[dict]]:
    """A workspace as it stood before the version store: two manuscripts,
    versions written as JSON the way the old collect wrote them, with a
    file order that is not sorted and texts shared across versions and
    manuscripts. Returns the database path and the rows written."""
    path = root / ".authorlm" / "authorlm.db"
    path.parent.mkdir(parents=True)
    raw = sqlite3.connect(str(path))
    raw.executescript(LEGACY_VERSIONS)
    raw.close()
    db = Database(path)
    rows = []
    histories = {
        "m-1": [{"toc.toml": "order\n", "b.md": "B1\n", "a.md": "A1\n"},
                {"toc.toml": "order\n", "b.md": "B2 — “é”\n", "a.md": "A1\n"},
                {"toc.toml": "order\n", "a.md": "A1\n"},
                {}],
        "m-2": [{"x.md": "A1\n", "y.md": "\x00 odd\r\n"}],
    }
    for mid, versions in histories.items():
        for number, files in enumerate(versions, 1):
            row = ko_fields("mv")
            row.update(manuscript_id=mid, version_no=number,
                       checksum=revisions.checksum(files),
                       files=json.dumps(files), source="legacy",
                       session_id=None)
            db.insert("manuscript_versions", row)
            rows.append(row)
    db.conn.close()
    return path, rows


class ConversionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="authorlm-convert-")
        self.root = Path(self.tmp.name)
        self.path, self.rows = legacy_workspace(self.root)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_an_unconverted_workspace_is_never_read_as_empty(self):
        db = Database(self.path)
        with self.assertRaises(revisions.VersionStoreNotConverted):
            revisions.latest_version(db, "m-1")
        with self.assertRaises(revisions.VersionStoreNotConverted):
            revisions.record_version(db, "m-1", {"a.md": "x"}, None)

    def test_every_reader_returns_what_the_json_held(self):
        db = Database(self.path)
        report = revisions.convert_store(db)
        self.assertEqual(report["versions"], 5)
        self.assertEqual(report["file_copies"], 10)
        # A1, B1, B2, order, odd: shared texts are kept once.
        self.assertEqual(report["texts_added"], 5)
        self.assertEqual(report["checksum_mismatches"], [])
        self.assertTrue(revisions.store_converted(db))
        for row in self.rows:
            want = json.loads(row["files"])
            got = revisions.files_at(db, row["id"])
            self.assertEqual(list(got.items()), list(want.items()))
            self.assertEqual(revisions.manifest_at(db, row["id"]).keys(),
                             want.keys())
            for name, text in want.items():
                self.assertEqual(revisions.file_at(db, row["id"], name), text)
            kept = revisions.version_by_id(db, row["id"])
            for key in ("id", "manuscript_id", "version_no", "checksum",
                        "source", "created_at", "metadata"):
                self.assertEqual(kept[key], row[key], key)
            self.assertEqual(revisions.checksum(got), kept["checksum"])
        self.assertEqual(revisions.latest_version(db, "m-1")["version_no"], 4)
        self.assertEqual(revisions.files_at(
            db, revisions.latest_version(db, "m-1")["id"]), {})
        self.assertTrue(revisions.check_store(db)["ok"])
        self.assertEqual(revisions.convert_store(db), {"already": True})

    def test_a_difference_on_read_back_converts_nothing(self):
        db = Database(self.path)
        real = revisions._decode
        with mock.patch.object(revisions, "_decode",
                               lambda body: real(body) + "!"):
            with self.assertRaises(RuntimeError):
                revisions.convert_store(db)
        self.assertFalse(revisions.store_converted(db))
        self.assertEqual(count(db, "version_files"), 0)
        self.assertEqual(count(db, "version_texts"), 0)
        self.assertEqual(json.loads(db.one(
            "SELECT files FROM manuscript_versions WHERE id = ?",
            (self.rows[0]["id"],))["files"]), json.loads(self.rows[0]["files"]))

    def test_the_verb_keeps_a_copy_converts_and_checks(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli_main(["--workspace", str(self.root), "version-store",
                      "convert", "--vacuum"])
        copies = list((self.path.parent / "backups").glob(
            "pre-version-store-*.db"))
        self.assertEqual(len(copies), 1, out.getvalue())
        kept = sqlite3.connect(str(copies[0]))
        self.assertIn("files", [r[1] for r in kept.execute(
            "PRAGMA table_info(manuscript_versions)")])
        kept.close()
        with contextlib.redirect_stdout(out):
            cli_main(["--workspace", str(self.root), "version-store",
                      "check"])
            cli_main(["--workspace", str(self.root), "version-store",
                      "convert"])
        self.assertIn("Every version reads back whole", out.getvalue())
        self.assertIn("already in the version store", out.getvalue())


class OneDoorTests(unittest.TestCase):
    def test_no_other_module_queries_the_history_tables(self):
        import re

        pattern = re.compile(r"(FROM|JOIN|INTO|UPDATE)\s+(manuscript_versions"
                             r"|version_files|version_texts)\b", re.I)
        package = HERE.parent / "authorlm"
        offenders = sorted(
            str(path.relative_to(HERE.parent))
            for path in [*package.rglob("*.py"),
                         *(HERE.parent / "tools").glob("*.py")]
            if path.name not in ("revisions.py", "db.py")
            and pattern.search(path.read_text(encoding="utf-8")))
        self.assertEqual(offenders, [], "read version history through "
                         "authorlm/revisions.py")


class SurfaceTests(unittest.TestCase):
    """The verbs that print history, on a converted workspace."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="authorlm-surface-")
        ws = Path(self.tmp.name)
        self.book = ws / "book"
        self.book.mkdir()
        (self.book / "one.md").write_text("# One\n\nFirst.\n", encoding="utf-8")
        self.ws = ws
        db = api.open_db(str(ws), create=True)
        self.manuscript = api.register_manuscript(db, "book", str(self.book))
        with contextlib.redirect_stdout(io.StringIO()):
            api.collect(db, self.manuscript, {})
        (self.book / "one.md").write_text("# One\n\nFirst, edited.\n",
                                          encoding="utf-8")
        (self.book / "two.md").write_text("# Two\n", encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            api.collect(db, self.manuscript, {})
        self.db = db

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_cli(self, *argv) -> str:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli_main(["--workspace", str(self.ws), *argv, "-m", "book"])
        return out.getvalue()

    def test_history_diff_and_restore(self):
        listing = self.run_cli("history")
        self.assertIn("v1 ", listing)
        self.assertIn("1 file(s)", listing)
        self.assertIn("2 file(s)", listing)
        self.assertIn("First.", self.run_cli("history", "show", "1"))
        diff = api.diff_versions(self.db, self.manuscript)
        self.assertEqual((diff["old"], diff["new"]), ("v1", "v2"))
        self.assertEqual(sorted(diff["files"]), ["one.md", "two.md"])
        with contextlib.redirect_stdout(io.StringIO()):
            api.restore_version(self.db, self.manuscript, 1, {})
        self.assertFalse((self.book / "two.md").exists())
        self.assertEqual((self.book / "one.md").read_text(encoding="utf-8"),
                         "# One\n\nFirst.\n")
        self.assertEqual(revisions.latest_version(
            self.db, self.manuscript["id"])["version_no"], 3)


if __name__ == "__main__":
    unittest.main()
