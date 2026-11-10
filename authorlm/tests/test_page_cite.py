"""`audio.speech_text` strips in-text page citations (`_PAGE_CITE`).

The happy path (`(*PR* 31)`, ranges, roman numerals) is pinned in
`test_audio.check_text_rules`. The regex also accepts `p.` / `pp.`, a
comma after the italic abbreviation, and comma-separated page lists —
forms that appear throughout the essays. If any of those edges drift,
the voice speaks the page numbers the reader alone was meant to see.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm import audio


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


CFG = {"text": {"inline_math": "warn"}}


def _spoken(md: str) -> str:
    text, _warnings = audio.speech_text(md, CFG)
    return text


def test_page_cite_pp_and_p_forms() -> None:
    text = _spoken(
        'He writes of novelty (*PR* pp. 32–33) and of feeling (*PR* p. 41).')
    check("pp./p. citations leave the surrounding prose",
          text.startswith("He writes of novelty")
          and "feeling" in text, text)
    check("pp./p. forms strip the abbreviation and the page numbers",
          "PR" not in text and "32" not in text and "41" not in text
          and "pp" not in text.lower(), text)


def test_page_cite_comma_after_title_and_page_lists() -> None:
    text = _spoken(
        'See the preface (*PR*, ix) and the pair (*PR* 4, 5–6) together.')
    check("comma-after-title and multi-page lists leave the prose",
          "See the preface" in text and "together" in text, text)
    check("comma forms strip title, roman, and listed pages",
          "PR" not in text and "ix" not in text and "4" not in text
          and "5" not in text and "6" not in text, text)


def test_page_cite_keeps_unrelated_parentheses() -> None:
    text = _spoken(
        'A plain aside (see below) stays, and so does (*toolong* 3).')
    # Italics strip before the cite pass, so a too-long abbr keeps its
    # parentheses and the spoken form of the italicised word.
    check("non-cite parentheses are kept",
          "(see below)" in text and "(toolong 3)" in text, text)


def main() -> None:
    tests = [test_page_cite_pp_and_p_forms,
             test_page_cite_comma_after_title_and_page_lists,
             test_page_cite_keeps_unrelated_parentheses]
    for fn in tests:
        print(fn.__name__)
        fn()
    print(f"OK — {len(tests)} page-cite cases")


if __name__ == "__main__":
    main()
