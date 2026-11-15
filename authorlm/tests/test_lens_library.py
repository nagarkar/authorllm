"""The lens library: a lens promoted from one book is seen, listed and run
on another, joins a sweep only when named, and the book's own lens of the
same name wins. Also pins the shipped `plain-reader` library lens."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ["AUTHORLM_CONFIG"] = "/nonexistent/authorlm-test/config.toml"
os.environ["AUTHORLM_ENV"] = "/nonexistent/authorlm-test/.env"
os.environ["AUTHORLM_CLIENT"] = "none"
os.environ["AUTHORLM_STYLE_DEFAULTS"] = "/nonexistent/authorlm-test/style-defaults.toml"

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from authorlm import api, lenses  # noqa: E402
from authorlm.db import Database  # noqa: E402

LENS = ('---\nclass = "chapter"\ninputs = ["glossary"]\n---\n'
        "# Clear sentences\n\nFlag sentences a common reader trips on.\n")


def check(label: str, cond: bool, detail: object = "") -> None:
    print(f"  [{'ok' if cond else 'FAIL'}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def _book(root: Path, db: Database, name: str) -> dict:
    ms = root / name
    ms.mkdir()
    (ms / "toc.toml").write_text('[[chapter]]\nfile = "ch.md"\n')
    (ms / "ch.md").write_text("# Chapter\n\nA sentence that is plain.\n")
    return api.register_manuscript(db, name, str(ms))


def test_promote_list_and_use_on_second_book() -> None:
    root = Path(tempfile.mkdtemp(prefix="authorlm-lens-library-"))
    old = os.environ.get(lenses.LIBRARY_ENV)
    os.environ[lenses.LIBRARY_ENV] = str(root / "library")
    try:
        db = Database(root / "authorlm.db")
        first = _book(root, db, "first")
        second = _book(root, db, "second")
        lenses.add_lens(first, "plain", LENS)
        lenses.add_lens(first, "own-only", LENS)
        check("before promote the second book does not see it",
              "plain" not in [l["name"] for l in lenses.list_lenses(second)])

        path = lenses.promote_lens(first, "plain")
        check("promote writes the library file byte for byte",
              path.read_text(encoding="utf-8") == LENS.strip() + "\n")
        check("promote removes the book's own copy",
              not (Path(first["path"]) / "_lenses" / "plain.md").exists())
        try:
            lenses.add_lens(first, "plain", LENS)
            lenses.promote_lens(first, "plain")
            check("promote leaves an existing library lens alone", False)
        except ValueError:
            check("promote leaves an existing library lens alone", True)
        (Path(first["path"]) / "_lenses" / "plain.md").unlink()

        rows = {l["name"]: l for l in lenses.list_lenses(second)}
        check("second book lists the library lens",
              rows.get("plain", {}).get("source") == "library", rows)
        payload = lenses.assemble(db, second, "plain", "ch.md")
        check("second book runs it by name",
              "Flag sentences a common reader trips on" in str(payload.__dict__))

        check("a sweep leaves the library lens out unless named",
              lenses.sweep_order(second) == [])
        check("--only names it into the sweep",
              lenses.sweep_order(second, only=["plain"]) == ["plain"])
        check("the first book's own lens still sweeps",
              lenses.sweep_order(first) == ["own-only"])

        lenses.add_lens(second, "plain", LENS.replace("common", "local"))
        rows = {l["name"]: l for l in lenses.list_lenses(second)}
        check("the book's own lens of the same name wins",
              rows["plain"]["source"] == "manuscript"
              and lenses.load_lens(second, "plain")[1].find("local") >= 0)
    finally:
        if old is None:
            os.environ.pop(lenses.LIBRARY_ENV, None)
        else:
            os.environ[lenses.LIBRARY_ENV] = old
        shutil.rmtree(root, ignore_errors=True)


def test_shipped_plain_reader_parses() -> None:
    path = ROOT / "library" / "lenses" / "plain-reader.md"
    meta, body = lenses.parse_lens(path.read_text(encoding="utf-8"))
    check("plain-reader ships in the library and parses",
          meta["inputs"] == ["audience", "glossary", "registers", "passes"]
          and "common reader" in body)


if __name__ == "__main__":
    test_promote_list_and_use_on_second_book()
    test_shipped_plain_reader_parses()
