"""`gdocs.locate_quote` attributes a Doc margin comment to exactly one
local file (and its nearest heading). Missing, empty, or ambiguous
anchors stay unattributed — never guessed. A quote that spans paragraphs
still resolves via the flattened match, then falls back to the quote's
head for the paragraph index.

`test_e2e.scenario_doc_comments` covers the happy / ambiguous / missing
paths inside a larger scenario. This file pins the empty-needle door and
the cross-paragraph head fallback hermetically.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.gdocs import locate_quote


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


FILES = {
    "one.md": ("# **Essay One**\n\n"
               "Alpha paragraph about choice.\n\n"
               "## **The Middle**\n\n"
               "Beta paragraph, quite distinctive text here.\n"),
    "two.md": "# **Essay Two**\n\nGamma paragraph about choice.\n",
}


def test_empty_and_whitespace_stay_unattributed() -> None:
    for label, quoted in (("empty string", ""),
                          ("spaces only", "   "),
                          ("newlines and tabs", "\n\t  \n")):
        rel, heading = locate_quote(FILES, quoted)
        check(f"{label} → (None, None)",
              rel is None and heading is None, f"{rel!r}/{heading!r}")


def test_distinct_quote_lands_on_nearest_heading() -> None:
    rel, heading = locate_quote(FILES, "quite distinctive text")
    check("unique quote → file and nearest heading",
          rel == "one.md" and heading == "**The Middle**",
          f"{rel}#{heading}")


def test_ambiguous_and_missing_stay_unattributed() -> None:
    rel, heading = locate_quote(FILES, "about choice")
    check("phrase in two files → (None, None)",
          rel is None and heading is None, f"{rel!r}/{heading!r}")
    rel, heading = locate_quote(FILES, "no such text anywhere")
    check("missing phrase → (None, None)",
          rel is None and heading is None, f"{rel!r}/{heading!r}")


def test_whitespace_normalized_match() -> None:
    """Docs often send the quote with different line breaks than the
    local file; `_flat` collapses both sides before matching."""
    quoted = ("Beta   paragraph,\nquite\tdistinctive   "
              "text   here.")
    rel, heading = locate_quote(FILES, quoted)
    check("whitespace-divergent quote still locates",
          rel == "one.md" and heading == "**The Middle**",
          f"{rel}#{heading}")


def test_cross_paragraph_quote_uses_head_fallback() -> None:
    """A Doc selection can span paragraphs. The full flattened needle
    hits the file, but no single paragraph contains it — the index falls
    back to the quote's first 40 characters."""
    files = {
        "chap.md": ("# **Title**\n\n"
                    "AAAAAAAAAA BBBBBBBBBB CCCCCCCCCC DDDDDDDDDD.\n\n"
                    "EEEEEEEEEE FFFFFFFFFF continues here.\n"),
    }
    quoted = ("AAAAAAAAAA BBBBBBBBBB CCCCCCCCCC DDDDDDDDDD. "
              "EEEEEEEEEE FFFFFFFFFF continues here.")
    rel, heading = locate_quote(files, quoted)
    check("cross-paragraph quote → the file",
          rel == "chap.md", repr(rel))
    check("cross-paragraph quote → heading from the head paragraph",
          heading == "**Title**", repr(heading))

    # Same span with the Doc's newlines preserved in the quote.
    quoted_nl = ("AAAAAAAAAA BBBBBBBBBB CCCCCCCCCC DDDDDDDDDD.\n\n"
                 "EEEEEEEEEE FFFFFFFFFF continues here.")
    rel2, heading2 = locate_quote(files, quoted_nl)
    check("cross-paragraph quote with newlines still locates",
          (rel2, heading2) == ("chap.md", "**Title**"),
          f"{rel2}#{heading2}")


def main() -> None:
    tests = [
        test_empty_and_whitespace_stay_unattributed,
        test_distinct_quote_lands_on_nearest_heading,
        test_ambiguous_and_missing_stay_unattributed,
        test_whitespace_normalized_match,
        test_cross_paragraph_quote_uses_head_fallback,
    ]
    for fn in tests:
        print(fn.__name__)
        fn()
    print(f"OK — {len(tests)} locate_quote cases")


if __name__ == "__main__":
    main()
