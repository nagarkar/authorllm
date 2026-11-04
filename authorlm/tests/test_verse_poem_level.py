"""`verse.poem_level` and `verse.heading_levels` decide where poems sit
in a verse file and what the collect report warns about.

`poem_level` is declared in toc.toml (1–6), default 2; invalid or
out-of-range values must fall back rather than guess. `heading_levels`
feeds the \"no poems at declared level\" warning. Happy `poem_level = 3`
is already in `test_api`; this file pins the fallback edges and the
count helper directly.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm import verse


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def _files(poem_level_line: str) -> dict[str, str]:
    return {
        "toc.toml": (
            '[[chapter]]\nfile = "poems.md"\nform = "verse"\n'
            + poem_level_line
        ),
        "poems.md": "## One\n\n*thesis*\n\nline\n\n### Nested\n\n## Two\n",
    }


def test_default_and_valid() -> None:
    files = _files("")
    check("missing poem_level defaults to 2",
          verse.poem_level(files, "poems.md") == 2)
    files = _files("poem_level = 3\n")
    check("declared 3 is honored",
          verse.poem_level(files, "poems.md") == 3)


def test_invalid_falls_back() -> None:
    for label, line in (
        ("non-integer string", 'poem_level = "x"\n'),
        ("zero", "poem_level = 0\n"),
        ("seven", "poem_level = 7\n"),
        ("negative", "poem_level = -1\n"),
    ):
        got = verse.poem_level(_files(line), "poems.md")
        check(f"{label} falls back to default 2", got == 2, got)


def test_heading_levels_counts() -> None:
    text = (
        "# Book\n\n"
        "## Poem A\n\nbody\n\n"
        "### Note\n\n"
        "## Poem B\n\n"
        "#### Deep\n"
    )
    got = verse.heading_levels(text)
    check("counts each level",
          got == {1: 1, 2: 2, 3: 1, 4: 1}, got)


def test_heading_levels_ignores_non_headings() -> None:
    text = "Not a heading\n#Realish but no space\n## Real\n"
    got = verse.heading_levels(text)
    check("only real headings count", got == {2: 1}, got)


def main() -> None:
    tests = [
        test_default_and_valid,
        test_invalid_falls_back,
        test_heading_levels_counts,
        test_heading_levels_ignores_non_headings,
    ]
    for t in tests:
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
