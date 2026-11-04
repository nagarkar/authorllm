"""`threads.has_broken_forms` is the pull/skip gate for marker corruption.

After `strip_pending`, a tab that still holds a stray `<<`/`>>` or a
paragraph-opening `{{` with no closer before the next form/EOF is left
alone and named in the pull's `unterminated` list (#331 / #332). Inline
author braces and TeX math must never trip it — those are manuscript
prose that survived the strip on purpose.

`test_doc_road_failures` scenario 3d covers the end-to-end pull path.
This file pins the pure detector the pull calls, including the edges
the failure fixture does not enumerate.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm import form_grammar as th


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_clean_and_author_braces() -> None:
    for label, text in (
        ("plain prose", "Just a paragraph.\n\nAnother."),
        ("inline author braces", "{{title}} is a template."),
        ("inline set notation", "The pair {{a, b}} is ordered."),
        ("closed own-line insertion already gone after strip",
         th.strip_pending("Lead.\n\n{{insert me}}\n\nTail.")[0]),
    ):
        check(f"{label}: not broken",
              not th.has_broken_forms(text), repr(text))


def test_math_spans_are_never_forms() -> None:
    inline = "Inline ${{a,b},{c}}$ stays math."
    display = "Before.\n\n$${{-q,+q},{-q-p,+q+p}}$$\n\nAfter."
    check("inline math with braces is not broken",
          not th.has_broken_forms(inline), inline)
    check("display math with braces is not broken",
          not th.has_broken_forms(display), display)
    # strip_pending must also leave them intact so the detector sees them.
    stripped, _ = th.strip_pending(inline + "\n\n" + display)
    check("strip_pending keeps both math spans",
          "${{a,b},{c}}$" in stripped
          and "$${{-q,+q},{-q-p,+q+p}}$$" in stripped, stripped)
    check("post-strip math still is not broken",
          not th.has_broken_forms(stripped), stripped)


def test_stray_replace_markers() -> None:
    check("stray << is broken", th.has_broken_forms("Lead << tail"))
    check("stray >> is broken", th.has_broken_forms("Lead >> tail"))
    # A well-formed replace collapses to the old half under strip_pending;
    # after that collapse there is no marker left to flag.
    clean, warns = th.strip_pending("A <<old>>{{new}} B")
    check("complete replace strips clean",
          clean == "A old B" and not warns, (clean, warns))
    check("stripped replace is not broken",
          not th.has_broken_forms(clean))


def test_unclosed_paragraph_insertion() -> None:
    # The #331 wipe: a paragraph `{{` that never closes before the next
    # form (or EOF). Detected on the post-strip text.
    lost = "Lead.\n\n{{lost its closer\n\nTail."
    check("unclosed paragraph insertion is broken",
          th.has_broken_forms(lost), lost)

    # Between two forms: strip collapses the complete ones; the unclosed
    # middle opener remains and must still flag.
    between = ("Intro.\n\n{{first ok}}\n\n"
               "Now consider the other side.\n\n"
               "{{second never closes\n\n"
               "<<old>>{{replacement}}\n\nOutro.")
    stripped, _ = th.strip_pending(between)
    check("strip keeps the middle prose",
          "Now consider the other side." in stripped, stripped)
    check("unclosed middle insertion still flags after strip",
          th.has_broken_forms(stripped), stripped)

    # A complete closed insertion at EOF of a paragraph is prose once
    # strip_pending has dropped own-line forms; leftover bare `{{ok}}`
    # inline is author braces and must not flag.
    check("inline closed braces are not broken",
          not th.has_broken_forms("see {{ok}} here"))


def test_start_of_text_unclosed() -> None:
    text = "{{opens the file and never closes"
    check("unclosed insertion at start of text is broken",
          th.has_broken_forms(text), text)


def main() -> None:
    for t in (test_clean_and_author_braces, test_math_spans_are_never_forms,
              test_stray_replace_markers, test_unclosed_paragraph_insertion,
              test_start_of_text_unclosed):
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
