"""`style_page._parse` / `_target`: the Style laws page's level string is
the only door that says where a law is listed, added, moved or copied.

A wrong kind would edit the workspace defaults, drop a guide name, or
send an add to `all` instead of a guide/file. Happy paths go through
`dispatch`; these pins are the parser edges alone.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm import style_page


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_parse_kinds() -> None:
    check("bare / empty is the whole-manuscript level",
          style_page._parse("") == ("all", "")
          and style_page._parse(None) == ("all", ""))
    check("all needs no name", style_page._parse("all") == ("all", ""))
    check("defaults needs no name",
          style_page._parse("defaults") == ("defaults", ""))
    check("guide:Name keeps the name after the colon",
          style_page._parse("guide:Papers") == ("guide", "Papers"))
    check("file:a.md keeps the file name",
          style_page._parse("file:a.md") == ("file", "a.md"))
    check("a guide name may itself contain a colon",
          style_page._parse("guide:Part:One") == ("guide", "Part:One"))


def test_parse_rejects() -> None:
    for bad in ("workspace", "guide", "file", "guide:", "file:"):
        try:
            style_page._parse(bad)
        except ValueError as e:
            check(f"declines '{bad}'", True, e)
            continue
        check(f"declines '{bad}'", False)


def test_target_for_writes() -> None:
    check("a guide level becomes guide_name for add/move/copy",
          style_page._target("guide:House") == {"guide_name": "House"})
    check("a file level becomes file for add/move/copy",
          style_page._target("file:two.md") == {"file": "two.md"})
    for bad in ("all", "defaults", "guide:", "file:"):
        try:
            style_page._target(bad)
        except ValueError as e:
            check(f"a write cannot target '{bad}'", True, e)
            continue
        check(f"a write cannot target '{bad}'", False)


def main() -> None:
    for t in (test_parse_kinds, test_parse_rejects, test_target_for_writes):
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
