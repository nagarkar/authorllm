"""`audio.pasted_speech` is the Try-text spoken-text door (#381).

The HTTP CUJ in `test_audio` hits one happy paste plus invalid-input
codes. These pins cover the paste-specific rules that change what the
author hears: blank-line injection between glued blocks, skipped
non-speech blocks, and markdown stripping. A silent regression here
speaks footnotes/images or glues heading text into the next paragraph.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.audio import pasted_speech


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_glues_heading_list_and_quote() -> None:
    got = pasted_speech("# Title\nNext paragraph without blank.")
    check("heading glued to prose gets a spoken pause",
          got == "Title\n\nNext paragraph without blank.", got)

    got = pasted_speech("# Title\n- item one\n- item two")
    check("heading glued to a list speaks both with a pause",
          got == "Title\n\nitem one item two", got)

    got = pasted_speech("Intro\n> a quote")
    check("prose glued to a quote gets a spoken pause",
          got == "Intro\n\na quote", got)


def test_skips_non_speech_blocks() -> None:
    got = pasted_speech("Keep\n\n[^1]: footnote body\n\nMore")
    check("footnote definitions are dropped", got == "Keep\n\nMore", got)

    check("image-only paste is silent", pasted_speech("![alt](x.png)") == "")
    check("horizontal rule alone is silent", pasted_speech("---") == "")

    got = pasted_speech("[Voice: narrator]\nSaid aloud.")
    check("voice tag lines are dropped; following prose remains",
          got == "Said aloud.", got)


def test_strips_inline_markdown() -> None:
    got = pasted_speech("A **bold** [link](https://x) and `code`.")
    check("bold, link text, and code fences strip to spoken words",
          got == "A bold link and code.", got)


def test_empty_and_plain_paragraphs() -> None:
    check("empty paste is empty speech", pasted_speech("") == "")
    got = pasted_speech("Para one.\n\nPara two.")
    check("already-separated paragraphs keep their pause",
          got == "Para one.\n\nPara two.", got)


def main() -> None:
    for t in (test_glues_heading_list_and_quote, test_skips_non_speech_blocks,
              test_strips_inline_markdown, test_empty_and_plain_paragraphs):
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
