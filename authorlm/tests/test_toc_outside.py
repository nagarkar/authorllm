"""toc.toml `outside`: Doc tabs that are not part of the book.

Papers and side pieces are mirrored to the Doc but never enter the
reading order, the full-book export, or the TOC <-> tab-order sync.
Also: a pre-2026-10-19 `manifest.md` (local ghost or leftover Doc tab)
must not re-enter the book via reading order, adopt, or toc rewrite.
Run: python3 tests/test_toc_outside.py
"""
from __future__ import annotations

import os
os.environ["AUTHORLM_CONFIG"] = "/nonexistent/authorlm-outside/config.toml"
os.environ["AUTHORLM_ENV"] = "/nonexistent/authorlm-outside/.env"
os.environ["AUTHORLM_CLIENT"] = "none"

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from test_passes import _SurgicalDocFake  # noqa: E402
from authorlm import api, gdocs, structure  # noqa: E402
from authorlm.cli import main as cli_main  # noqa: E402

TOC = '''# Table of contents
outside = ["papers/", "side.md"]

[[chapter]]
file = "a.md"

[[chapter]]
file = "b.md"
parent = "a.md"
matter = "back"
'''


class ObsoleteManifestTests(unittest.TestCase):
    """Pre-2026-10-19 `manifest.md` must not re-enter the book."""

    def test_ghost_manifest_md_stays_out_of_reading_order(self) -> None:
        files = {"toc.toml": '[[chapter]]\nfile = "a.md"\n',
                 "a.md": "# A\n",
                 "manifest.md": "<!-- GENERATED -->\n# Reader load\n",
                 "pronunciations.md": ""}
        order, unlisted = structure.reading_order(files)
        self.assertEqual(order, ["a.md"])
        self.assertEqual(unlisted, [])
        self.assertTrue(structure.is_obsolete("manifest.md"))
        self.assertFalse(structure.is_sidecar("manifest.md"))
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for name, text in files.items():
                (root / name).write_text(text, encoding="utf-8")
            bridge = gdocs.DocBridge("gdocs", root, "book",
                                     toc_sync=True, rich_manifest=True)
            self.assertEqual(gdocs._reading_order_files(bridge),
                             ["a.md", "pronunciations.md"])

    def test_leftover_doc_tab_is_ignored_not_adopted(self) -> None:
        """Beyond a local ghost: a Doc that still has the old tab must
        not be adopted as an essay (which would then enter toc.toml on
        doc_moved)."""
        tabs = [("t0", "book"), ("t1", "a.md"),
                ("t2", "manifest.md"), ("t3", "manifest")]
        cls = gdocs.classify_tabs(tabs, {"a.md": {"tab_id": "t1"}}, {"a.md"})
        self.assertIn("manifest.md", cls["ignored"])
        self.assertNotIn(("manifest.md", "t2"), cls["adopted"])
        self.assertNotIn(("manifest.md", "t2"), cls["readopted"])

    def test_rewrite_toc_drops_obsolete_manifest_md(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "toc.toml").write_text(
                '[[chapter]]\nfile = "a.md"\n', encoding="utf-8")
            doc_pairs = [("a.md", None), ("manifest.md", None)]
            gdocs.rewrite_toc_from_doc(
                {"path": tmp}, doc_pairs, [("a.md", 0)])
            text = (Path(tmp) / "toc.toml").read_text(encoding="utf-8")
        self.assertNotIn("manifest.md", text)
        self.assertEqual([n for n, _ in structure.parse_toc_tree(text)],
                         ["a.md"])

    def test_pull_drops_stale_mapping_without_toc_or_file(self) -> None:
        """Upgrade path: mapping still names the old tab, Doc still has
        it, no local ghost yet. A full pull must not materialize the
        file or add it to toc.toml (the doc_moved + rewrite hole)."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            ws, ms = root / "ws", root / "ws" / "book"
            ms.mkdir(parents=True)
            (ms / "a.md").write_text("# A\n\nProse.\n", encoding="utf-8")
            (ms / "toc.toml").write_text(
                '[[chapter]]\nfile = "a.md"\n', encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                cli_main(["--workspace", str(ws), "init", "--name", "book",
                          "--path", str(ms), "--extraction", "off"])
            db = api.open_db(str(ws))
            manuscript = api.get_manuscript(db)
            fake = _SurgicalDocFake([
                ("book", ""), ("a.md", "# A\n\nProse.\n"),
                ("manifest.md", "# Reader load\nDEBT\n"),
                ("manifest", "Manuscript: book\n")])
            meta = gdocs._mapping(db, manuscript)
            entry = meta.setdefault("gdocs", {})
            entry["_master_id"] = fake.master_id
            entry["_container_tab"] = next(
                t["id"] for t in fake.tabs if t["title"] == "book")
            entry["a.md"] = {
                "tab_id": next(t["id"] for t in fake.tabs
                               if t["title"] == "a.md"),
                "pushed_hash": "x", "checked_out": False}
            entry["manifest.md"] = {
                "tab_id": next(t["id"] for t in fake.tabs
                               if t["title"] == "manifest.md"),
                "pushed_hash": "y", "checked_out": False}
            gdocs._save_mapping(db, manuscript, meta)
            report = gdocs.pull_doc(
                db, api.get_manuscript(db), None,
                service=fake, docs_service=fake, with_comments=False)
            self.assertFalse((ms / "manifest.md").exists())
            self.assertNotIn("manifest.md",
                             (ms / "toc.toml").read_text(encoding="utf-8"))
            self.assertNotIn(
                "manifest.md",
                gdocs._mapping(db, api.get_manuscript(db)).get("gdocs", {}))
            self.assertFalse(report.get("toc_updated"))
            self.assertNotIn("manifest.md", report.get("adopted") or [])

    def test_push_deletes_local_ghost_and_old_tab(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            ws, ms = root / "ws", root / "ws" / "book"
            ms.mkdir(parents=True)
            (ms / "a.md").write_text("# A\n\nProse.\n", encoding="utf-8")
            (ms / "manifest.md").write_text(
                "<!-- GENERATED -->\n# Reader load\n", encoding="utf-8")
            (ms / "toc.toml").write_text(
                '[[chapter]]\nfile = "a.md"\n', encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                cli_main(["--workspace", str(ws), "init", "--name", "book",
                          "--path", str(ms), "--extraction", "off"])
            db = api.open_db(str(ws))
            manuscript = api.get_manuscript(db)
            fake = _SurgicalDocFake([
                ("book", ""), ("a.md", "# A\n\nProse.\n"),
                ("manifest.md", "# Reader load\n")])
            meta = gdocs._mapping(db, manuscript)
            entry = meta.setdefault("gdocs", {})
            entry["_master_id"] = fake.master_id
            entry["_container_tab"] = next(
                t["id"] for t in fake.tabs if t["title"] == "book")
            entry["a.md"] = {
                "tab_id": next(t["id"] for t in fake.tabs
                               if t["title"] == "a.md"),
                "checked_out": True}
            entry["manifest.md"] = {
                "tab_id": next(t["id"] for t in fake.tabs
                               if t["title"] == "manifest.md"),
                "checked_out": True}
            gdocs._save_mapping(db, manuscript, meta)
            manuscript = api.get_manuscript(db)
            gdocs.push_doc(db, manuscript, "a.md",
                           service=fake, docs_service=fake)
            self.assertFalse((ms / "manifest.md").exists())
            self.assertNotIn("manifest.md",
                             [t["title"] for t in fake.tabs])
            self.assertNotIn(
                "manifest.md",
                gdocs._mapping(db, api.get_manuscript(db)).get("gdocs", {}))


class OutsideTests(unittest.TestCase):

    def files(self) -> dict[str, str]:
        return {"toc.toml": TOC, "a.md": "# A\n", "b.md": "# B\n",
                "side.md": "# Side\n", "papers/p0.md": "# P0\n",
                "stray.md": "# Stray\n"}

    def test_reading_order_leaves_outside_files_out(self) -> None:
        order, unlisted = structure.reading_order(self.files())
        self.assertEqual(order, ["a.md", "b.md", "stray.md"])
        self.assertEqual(unlisted, ["stray.md"])

    def test_naming_an_outside_file_still_builds_it(self) -> None:
        self.assertEqual(structure.select_chapters(self.files(), ["papers/p0.md"]),
                         ["papers/p0.md"])
        self.assertEqual(structure.select_chapters(self.files(), ["a"]),
                         ["a.md", "b.md"])

    def test_outside_entries(self) -> None:
        outside = structure.toc_outside(TOC)
        self.assertTrue(structure.is_outside("papers/agency.md", outside))
        self.assertTrue(structure.is_outside("side.md", outside))
        self.assertFalse(structure.is_outside("papers.md", outside))
        self.assertEqual(structure.toc_outside("not = [toml"), [])

    def test_doc_reorder_keeps_outside_list_and_excludes_its_tabs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "toc.toml").write_text(TOC, encoding="utf-8")
            doc_pairs = [("b.md", None), ("a.md", None), ("side.md", None),
                         ("papers/p0.md", None)]
            local_tree = structure.parse_toc_tree(TOC)
            gdocs.rewrite_toc_from_doc({"path": tmp}, doc_pairs, local_tree)
            text = (Path(tmp) / "toc.toml").read_text(encoding="utf-8")
        self.assertEqual(structure.toc_outside(text), ["papers/", "side.md"])
        self.assertEqual([n for n, _ in structure.parse_toc_tree(text)],
                         ["b.md", "a.md"])
        self.assertEqual(structure.toc_attrs(text)["b.md"], {"matter": "back"})



class OutsideTabs(unittest.TestCase):
    def test_outside_files_still_get_a_tab(self):
        """An outside file leaves the reading order but is mirrored: the
        bridge's tab list must carry it (nagarkar/authorllm#194)."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "toc.toml").write_text(TOC)
            (root / "a.md").write_text("# A\n")
            (root / "b.md").write_text("# B\n")
            (root / "side.md").write_text("# Side\n")
            (root / "papers").mkdir()
            (root / "papers" / "p.md").write_text("# P\n")
            bridge = gdocs.DocBridge("doc_links", root, "T", True, True)
            order = gdocs._reading_order_files(bridge)
            self.assertEqual(order[:2], ["a.md", "b.md"])
            self.assertIn("papers/p.md", order)
            self.assertIn("side.md", order)
            from authorlm.revisions import read_manuscript_files
            book, _ = structure.reading_order(read_manuscript_files(root))
            self.assertNotIn("papers/p.md", book)

if __name__ == "__main__":
    unittest.main(verbosity=2)
