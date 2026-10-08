"""Manuscript identity parsers for catalog / audiobook / export doors.

`parse_copyright_year`, `parse_language`, and `parse_bleed` refuse bad
input before it lands in manuscript metadata. CLI coverage exercises a
couple of happy paths; this file pins the refuse/normalize matrix
directly so a looser regex cannot ship bad catalog years or tags.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.api import parse_bleed, parse_copyright_year, parse_language


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def _raises(fn, value) -> bool:
    try:
        fn(value)
        return False
    except ValueError:
        return True


def test_copyright_year() -> None:
    check("empty copyright year clears to 0",
          parse_copyright_year("") == 0
          and parse_copyright_year(None) == 0)
    check("four-digit year parses",
          parse_copyright_year("2026") == 2026
          and parse_copyright_year(" 1999 ") == 1999)
    check("short or non-digit year refuses",
          _raises(parse_copyright_year, "26")
          and _raises(parse_copyright_year, "abcd")
          and _raises(parse_copyright_year, "20264"))


def test_language() -> None:
    check("empty language clears",
          parse_language("") == ""
          and parse_language(None) == "")
    check("primary subtag lowercases; region kept",
          parse_language("EN") == "en"
          and parse_language("EN-US") == "en-US"
          and parse_language("en-US") == "en-US")
    check("malformed language refuses",
          _raises(parse_language, "e")
          and _raises(parse_language, "123")
          and _raises(parse_language, "en_US"))


def test_bleed() -> None:
    check("bool bleed passes through",
          parse_bleed(True) is True and parse_bleed(False) is False)
    check("yes/no spellings normalize",
          parse_bleed("yes") is True and parse_bleed("on") is True
          and parse_bleed("1") is True
          and parse_bleed("no") is False and parse_bleed("") is False
          and parse_bleed("off") is False)
    check("unknown bleed refuses",
          _raises(parse_bleed, "maybe")
          and _raises(parse_bleed, "y"))


def main() -> None:
    test_copyright_year()
    test_language()
    test_bleed()
    print("test_manuscript_metadata: all checks passed")


if __name__ == "__main__":
    main()
