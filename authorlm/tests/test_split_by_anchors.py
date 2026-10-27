"""`gdocs._plain` / `_split_by_anchors` cut a Doc export when tab titles
are missing from the markdown.

Google stopped writing tab titles as headings (2026-10-26); pull then
matches each tab's first paragraph, forward-only. A missed anchor must
stay missing (never guessed); an empty tab is an empty section; emphasis
and heading marks must not break the match.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.gdocs import _plain, _split_by_anchors


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_plain_strips_markup() -> None:
    check("heading marks go", _plain("## Hello there") == "Hello there")
    check("emphasis and escapes go",
          _plain(r"**Bold\_word** and *it*") == "Boldword and it")
    check("whitespace collapses",
          _plain("  two   spaces  ") == "two spaces")
    check("empty stays empty", _plain("") == "")


def test_split_forward_order_and_prefix() -> None:
    text = (
        "Opening of one.\n\nBody of one.\n\n"
        "Opening of two with a long first paragraph that continues.\n\n"
        "Body of two.\n"
    )
    order = ["one.md", "two.md"]
    anchors = {
        "one.md": "Opening of one.",
        "two.md": "Opening of two with a long first paragraph that continues.",
    }
    out = _split_by_anchors(text, order, anchors)
    check("both tabs found in order",
          set(out) == {"one.md", "two.md"}, out)
    check("first section ends before second anchor",
          out["one.md"].startswith("Opening of one.")
          and "Opening of two" not in out["one.md"], out["one.md"])
    check("second section starts at its anchor",
          out["two.md"].startswith("Opening of two"), out["two.md"])

    # Prefix match on the first 80 plain chars.
    short = {
        "one.md": "Opening of one.",
        "two.md": "Opening of two with a long first paragraph",
    }
    pref = _split_by_anchors(text, order, short)
    check("anchor prefix (≤80) still matches",
          "two.md" in pref and pref["two.md"].startswith("Opening of two"),
          pref)


def test_markup_empty_and_missing() -> None:
    text = "# **Marked open**\n\nBody.\n\nSecond open.\n\nMore.\n"
    order = ["a.md", "b.md", "c.md"]
    anchors = {
        "a.md": "Marked open",  # export has heading marks + bold
        "b.md": "",             # empty tab
        "c.md": "Never appears in the export at all.",
    }
    out = _split_by_anchors(text, order, anchors)
    check("markup-stripped anchor matches",
          "a.md" in out and out["a.md"].startswith("# **Marked open**"), out)
    check("empty anchor → empty section",
          out.get("b.md") == "\n", out)
    check("unfound anchor is absent (not guessed)",
          "c.md" not in out, out)

    # Out-of-order prose must not be taken as an earlier tab's later match.
    shuffled = "Second open.\n\nMore.\n\n# **Marked open**\n\nBody.\n"
    late = _split_by_anchors(shuffled, ["a.md", "d.md"],
                             {"a.md": "Marked open",
                              "d.md": "Second open."})
    check("forward-only: earlier prose cannot satisfy a later search position",
          "a.md" in late and "d.md" not in late, late)


if __name__ == "__main__":
    test_plain_strips_markup()
    test_split_forward_order_and_prefix()
    test_markup_empty_and_missing()
    print("ok")
