"""`structure.select_chapters` is the part-build / interlocutor door that
turns a named parent into that part plus its TOC descendants.

Export `--only` and interlocutor file filters both call it. A silent
regression that drops descendants ships a partial part-build; one that
ignores depth walks ships the whole book under the wrong slug. Nothing
on main called it hermetically — reading_order / matter_map coverage
does not exercise the descendant walk or the LookupError refuse.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.structure import TOC_FILENAME, select_chapters


TOC = (
    '[[chapter]]\nfile = "title.md"\nmatter = "front"\n\n'
    '[[chapter]]\nfile = "part.md"\n\n'
    '[[chapter]]\nfile = "alpha.md"\nparent = "part.md"\n\n'
    '[[chapter]]\nfile = "beta.md"\nparent = "part.md"\n\n'
    '[[chapter]]\nfile = "loose.md"\n'
)

FILES = {
    TOC_FILENAME: TOC,
    "title.md": "# Title\n",
    "part.md": "# Part\n",
    "alpha.md": "# Alpha\n",
    "beta.md": "# Beta\n",
    "loose.md": "# Loose\n",
    "pronunciations.md": "| Term |\n",
}


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_parent_includes_descendants() -> None:
    got = select_chapters(FILES, ["part.md"])
    check("naming a parent includes it and its children, in reading order",
          got == ["part.md", "alpha.md", "beta.md"], repr(got))


def test_stem_without_extension() -> None:
    got = select_chapters(FILES, ["part"])
    check("a stem without .md still selects the part",
          got == ["part.md", "alpha.md", "beta.md"], repr(got))


def test_leaf_alone() -> None:
    got = select_chapters(FILES, ["alpha.md"])
    check("naming a leaf returns only that leaf",
          got == ["alpha.md"], repr(got))


def test_multiple_names_preserve_order() -> None:
    got = select_chapters(FILES, ["loose.md", "title.md"])
    check("multiple names come back in reading order, not query order",
          got == ["title.md", "loose.md"], repr(got))


def test_unknown_name_refuses() -> None:
    try:
        select_chapters(FILES, ["missing.md"])
    except LookupError as err:
        msg = str(err)
        check("unknown name raises LookupError", True)
        check("refusal names the missing chapter", "missing.md" in msg, msg)
        check("refusal lists known chapters", "part.md" in msg, msg)
    else:
        raise AssertionError("missing.md was not refused")


def main() -> None:
    for t in (test_parent_includes_descendants, test_stem_without_extension,
              test_leaf_alone, test_multiple_names_preserve_order,
              test_unknown_name_refuses):
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
