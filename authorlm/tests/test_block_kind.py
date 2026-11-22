"""`audio._block_kind` classifies one blank-line block for the audiobook
export. A drift here narrates a heading as a paragraph, drops a list into
prose, or speaks a footnote definition. Pure over strings; no workspace.
Run: python3 tests/test_block_kind.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.audio import _block_kind  # noqa: E402


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_kinds() -> None:
    check("voice tag", _block_kind("[Voice: narrator]") == ("voice", {}))
    check("footnote definition",
          _block_kind("[^F1]: a note") == ("footnote", {}))
    check("horizontal rule alone", _block_kind("---") == ("rule", {}))
    check("rule with trailing prose is a paragraph",
          _block_kind("---\nstill here")[0] == "paragraph")
    check("single-line heading",
          _block_kind("## Title here")
          == ("heading", {"level": 2, "text": "Title here"}))
    check("heading with a body line is a paragraph",
          _block_kind("# Title\nbody")[0] == "paragraph")
    check("illustration tag",
          _block_kind("[Illustration: a field]") == ("illustration", {}))
    check("image alone",
          _block_kind("![](_illustrations/x.jpg)") == ("illustration", {}))
    check("list", _block_kind("- a\n- b") == ("list", {}))
    check("list broken by a bare line is a paragraph",
          _block_kind("- a\nbare")[0] == "paragraph")
    check("quote", _block_kind("> one\n> two") == ("quote", {}))
    check("quote broken by bare prose is a paragraph",
          _block_kind("> one\nbare")[0] == "paragraph")
    check("table",
          _block_kind("| a | b |\n| --- | --- |\n| 1 | 2 |") == ("table", {}))
    check("plain prose", _block_kind("Just words.") == ("paragraph", {}))


def main() -> None:
    print("test_block_kind")
    test_kinds()
    print("OK — block_kind cases")


if __name__ == "__main__":
    main()
