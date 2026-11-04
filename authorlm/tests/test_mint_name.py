"""`media.parse_sections` / `brief_terms` / `_mint_name` turn clip tag
bodies into stable keys and search terms.

Wrong section splits drop `source:` / `moment:` from ytlm queries.
Wrong minting collides keys, ignores the moment a brief describes, or
fails to fall back to a YouTube id / use-path when the description is
empty — collect then mints colliding or opaque filenames under `_clips/`.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.media import (
    BRIEF_LABELS,
    SPEC_LABELS,
    _mint_name,
    brief_terms,
    parse_sections,
)


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_parse_sections_multiline_and_inline() -> None:
    multi = parse_sections(
        "lead-in\nsource: Matrix (1999)\nmoment: lobby\nquote: \"Choice.\"\n",
        BRIEF_LABELS,
    )
    check("preamble kept under empty key",
          multi.get("") == "lead-in", multi)
    check("labels lowercased; values collapsed",
          multi.get("source") == "Matrix (1999)"
          and multi.get("moment") == "lobby"
          and multi.get("quote") == '"Choice."', multi)

    inline = parse_sections("pre source: A moment: B", BRIEF_LABELS)
    check("inline label:value pairs parse with preamble",
          inline == {"": "pre", "source": "A", "moment": "B"}, inline)

    check("empty labels yield empty dict",
          parse_sections("source: X", ()) == {})
    check("no matching label → empty dict (preamble alone is not kept)",
          parse_sections("note: ignored", BRIEF_LABELS) == {}
          and parse_sections("hello world", BRIEF_LABELS) == {})


def test_brief_terms_query_and_quote() -> None:
    terms = brief_terms(
        'source: Matrix (1999)\n'
        'moment: lobby shootout\n'
        'quote: "The problem is choice."\n'
    )
    check("query joins source and moment",
          terms["query"] == "Matrix (1999) lobby shootout", terms)
    check("quote is unwrapped",
          terms["quote"] == "The problem is choice.", terms)

    source_only = brief_terms("source: Alone\n")
    check("missing moment still yields source-only query",
          source_only == {"query": "Alone", "quote": ""}, source_only)

    empty = brief_terms("")
    check("empty brief yields empty query and quote",
          empty == {"query": "", "quote": ""}, empty)


def test_mint_name_prefers_moment_quote_subject() -> None:
    check("moment wins over source for the key base",
          _mint_name("source: Film\nmoment: a beach at dusk\n", {}, set())
          == "a-beach-at-dusk.md")
    check("quote used when moment absent",
          _mint_name('source: Film\nquote: "He walks away."\n', {}, set())
          == "he-walks-away.md")
    check("subject used for generation specs",
          _mint_name("subject: a marble hall\nsetting: night\n", {}, set())
          == "a-marble-hall.md")
    check("collision suffixes -2, -3, …",
          _mint_name("moment: a beach at dusk", {},
                     {"a-beach-at-dusk.md"})
          == "a-beach-at-dusk-2.md")
    check("container prefix is prepended",
          _mint_name("title text here now more", {}, set(), "collage")
          == "collage-title-text-here-now.md")


def test_mint_name_fallbacks() -> None:
    check("plain description slugs when no sections",
          _mint_name("He Slumps at his Desk!", {}, set())
          == "he-slumps-at-his.md")
    yt = _mint_name("", {"youtube": "https://youtu.be/dQw4w9WgXcQ"}, set())
    check("empty description + youtube → yt-<id>",
          yt == "yt-dqw4w9wgxcq.md", yt)
    use = _mint_name("", {"use": "assets/My_Clip-One.mp4"}, set())
    check("empty description + use → slug of the stem",
          use == "my-clip-one.md", use)
    check("nothing usable → clip.md",
          _mint_name("", {}, set()) == "clip.md")
    check("punctuation-only description falls through to clip.md",
          _mint_name("!!!", {}, set()) == "clip.md")


def main() -> None:
    tests = [
        test_parse_sections_multiline_and_inline,
        test_brief_terms_query_and_quote,
        test_mint_name_prefers_moment_quote_subject,
        test_mint_name_fallbacks,
    ]
    for t in tests:
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
