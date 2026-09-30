"""footnote_prefixes must stay injective when nested paths share a stem.

`iter_manuscript_paths` is rglob, so `intro.md` and `part2/intro.md` are
both legal content files. Stem-only uniquification exhausted both to
`intro`, and combined export emitted duplicate `[^intro-1]` labels —
pandoc then bound both refs to one definition (silent wrong footnotes).
"""

from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.export import combined_markdown, footnote_prefixes


def check(label: str, condition: bool, context: object = "") -> None:
    if not condition:
        raise AssertionError(f"FAIL: {label}: {context!r}")
    print(f"  ok: {label}")


def test_nested_stem_tokens_are_unique() -> None:
    toks = footnote_prefixes(["intro.md", "part2/intro.md"])
    check("nested identical stems get distinct tokens",
          toks["intro.md"] != toks["part2/intro.md"], toks)
    check("first keeps the exhausted stem",
          toks["intro.md"] == "intro", toks)
    check("second takes a numeric suffix",
          toks["part2/intro.md"] == "intro-2", toks)


def test_flat_prefixes_unchanged() -> None:
    toks = footnote_prefixes(
        ["recapitulation.md", "rebirth.md", "redemption.md",
         "god.md", "good-choice.md", "good-life.md", "kindness.md"])
    check("letterwise prefixes for distinct flat stems unchanged",
          toks == {"recapitulation.md": "rec", "rebirth.md": "reb",
                   "redemption.md": "red", "god.md": "god",
                   "good-choice.md": "good-c",
                   "good-life.md": "good-l", "kindness.md": "k"},
          toks)


def test_combined_markdown_nested_footnotes() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "intro.md").write_text(
            "Hello [^1].\n\n[^1]: note from root\n")
        part = root / "part2"
        part.mkdir()
        (part / "intro.md").write_text(
            "World [^1].\n\n[^1]: note from part2\n")
        (root / "toc.toml").write_text(
            '[[chapter]]\nfile = "intro.md"\n\n'
            '[[chapter]]\nfile = "part2/intro.md"\n')
        manuscript = {"path": str(root), "name": "book"}
        text, order, _ = combined_markdown(manuscript)
        check("reading order keeps both nested files",
              order == ["intro.md", "part2/intro.md"], order)
        labels = re.findall(r"\[\^([A-Za-z0-9_-]+)\]", text)
        check("combined export has no duplicate footnote labels",
              len(set(labels)) * 2 == len(labels), labels)
        check("each file's note kept its own namespaced label",
              "intro-1" in labels and "intro-2-1" in labels, labels)


if __name__ == "__main__":
    test_nested_stem_tokens_are_unique()
    test_flat_prefixes_unchanged()
    test_combined_markdown_nested_footnotes()
    print("all passed")
