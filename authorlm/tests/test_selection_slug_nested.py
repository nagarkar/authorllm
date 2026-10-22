"""Nested-path part-builds must not share an `_exports` filename tag.

`intro.md` and `part2/intro.md` are both legal via toc/rglob. Stem-only
`selection_slug` made both `--chapters` exports write
`_exports/<title> - intro.md`, so the second silently overwrote the first.
Run: python3 tests/test_selection_slug_nested.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ["AUTHORLM_CONFIG"] = "/nonexistent/authorlm-test/config.toml"
os.environ["AUTHORLM_ENV"] = "/nonexistent/authorlm-test/.env"
os.environ["AUTHORLM_CLIENT"] = "none"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm import api  # noqa: E402
from authorlm.db import Database  # noqa: E402
from authorlm.export import (  # noqa: E402
    _selection_token, export_published, selection_slug,
)

PASSED = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASSED
    if not ok:
        raise AssertionError(f"FAIL: {label}"
                             + (f" — {detail}" if detail else ""))
    PASSED += 1
    print(f"  ok  {label}")


def test_tokens_and_slugs() -> None:
    check("flat files keep Path.stem",
          _selection_token("intro.md") == "intro"
          and _selection_token("ascending.md") == "ascending")
    check("nested paths include parent dirs",
          _selection_token("part2/intro.md") == "part2--intro"
          and _selection_token("a/b/c.md") == "a--b--c")
    check("nested same-basename part-builds get distinct slugs",
          selection_slug(["intro.md"]) != selection_slug(["part2/intro.md"])
          and selection_slug(["intro.md"]) == "intro"
          and selection_slug(["part2/intro.md"]) == "part2--intro")
    check("flat multi-chapter slugs are unchanged",
          selection_slug(["ascending.md", "discernment.md"])
          == "ascending+discernment")
    check("a selection that mixes flat and nested stays distinct from "
          "either alone",
          selection_slug(["intro.md", "part2/intro.md"])
          == "intro+part2--intro"
          and selection_slug(["intro.md", "part2/intro.md"])
          != selection_slug(["intro.md"]))


def test_export_paths_coexist() -> None:
    root = Path(tempfile.mkdtemp(prefix="authorlm-slug-nested-"))
    (root / "part2").mkdir()
    (root / "intro.md").write_text("# Root intro\n\nRoot body.\n",
                                   encoding="utf-8")
    (root / "part2" / "intro.md").write_text(
        "# Nested intro\n\nNested body.\n", encoding="utf-8")
    (root / "toc.toml").write_text(
        '[[chapter]]\nfile = "intro.md"\n\n'
        '[[chapter]]\nfile = "part2/intro.md"\n',
        encoding="utf-8")
    db = Database(root / "authorlm.db")
    manuscript = api.register_manuscript(db, "Nested", str(root),
                                         extraction=False)
    first = export_published(db, manuscript, fmt="md", only=["intro.md"])
    second = export_published(db, manuscript, fmt="md",
                              only=["part2/intro.md"])
    first_path = Path(first["markdown"])
    second_path = Path(second["markdown"])
    check("nested and flat part-builds write distinct _exports paths",
          first_path != second_path
          and first_path.exists() and second_path.exists(),
          f"{first_path!r} vs {second_path!r}")
    check("re-exporting the nested chapter does not wipe the flat one",
          "Root body." in first_path.read_text(encoding="utf-8")
          and "Nested body." in second_path.read_text(encoding="utf-8"))
    again = export_published(db, manuscript, fmt="md",
                             only=["part2/intro.md"])
    check("the flat artifact still holds root prose after a nested re-export",
          Path(again["markdown"]) == second_path
          and "Root body." in first_path.read_text(encoding="utf-8")
          and "Nested body." not in first_path.read_text(encoding="utf-8")
          and "Nested body." in second_path.read_text(encoding="utf-8"))


def main() -> None:
    print("selection_slug nested stems:")
    test_tokens_and_slugs()
    test_export_paths_coexist()
    print(f"all ok ({PASSED})")


if __name__ == "__main__":
    main()
