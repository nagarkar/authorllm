"""`form_grammar.strip_pending` / `approved_text` must clear every
own-line insertion `pending_forms` lists — including footnote
definitions stacked with a single newline (Docs export shape).

A blank-line-only collapse dropped the first of a blank-then-stack and
left later siblings, and never matched a single-newline stack at all.
`doc pull` / session-start reconcile then wrote `{{[^2]: …}}` into the
local manuscript while `has_broken_forms` stayed false.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.form_grammar import (
    approved_text,
    has_broken_forms,
    pending_forms,
    strip_pending,
)


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_single_newline_stack_strips_clean() -> None:
    text = "Body.\n{{[^1]: First.}}\n{{[^2]: Second.}}\n"
    forms = [f["new"] for f in pending_forms(text) if f["kind"] == "insert"]
    check("pending_forms sees both stacked defs",
          forms == ["[^1]: First.", "[^2]: Second."], forms)
    stripped, warns = strip_pending(text)
    check("strip_pending drops both single-nl stacked inserts",
          stripped == "Body.\n" and warns == [], repr((stripped, warns)))
    check("has_broken_forms stays false after a clean strip",
          has_broken_forms(stripped) is False)
    check("approved_text unwraps both stacked defs",
          approved_text(text)
          == "Body.\n[^1]: First.\n[^2]: Second.\n",
          repr(approved_text(text)))


def test_blank_then_stack_leaves_no_sibling() -> None:
    text = "Body.\n\n{{[^1]: First.}}\n{{[^2]: Second.}}\n"
    stripped, warns = strip_pending(text)
    check("strip_pending drops the blank-prefixed def and its sibling",
          stripped == "Body.\n" and warns == [], repr((stripped, warns)))
    check("approved_text unwraps both after a blank-then-stack",
          approved_text(text)
          == "Body.\n\n[^1]: First.\n[^2]: Second.\n",
          repr(approved_text(text)))


def test_author_inline_and_math_still_intact() -> None:
    text = (
        "Mentions {{title}} and ${{a,b}}$.\n\n"
        "$$\n{{a,b}}\n$$\n\n"
        "{{A real insertion.}}\n")
    stripped, warns = strip_pending(text)
    check("strip drops the planted insert only",
          stripped == ("Mentions {{title}} and ${{a,b}}$.\n\n"
                       "$$\n{{a,b}}\n$$\n")
          and warns == [], repr((stripped, warns)))


def test_blank_separated_inserts_still_byte_clean() -> None:
    text = "Para one.\n\n{{inserted}}\n\nPara three.\n"
    stripped, _ = strip_pending(text)
    check("blank-line-bounded insert still strips byte-clean",
          stripped == "Para one.\n\nPara three.\n", repr(stripped))
    check("approved_text still unwraps with a paragraph break",
          approved_text(text) == "Para one.\n\ninserted\n\nPara three.\n",
          repr(approved_text(text)))


if __name__ == "__main__":
    for fn in (
        test_single_newline_stack_strips_clean,
        test_blank_then_stack_leaves_no_sibling,
        test_author_inline_and_math_still_intact,
        test_blank_separated_inserts_still_byte_clean,
    ):
        print(fn.__name__)
        fn()
    print("OK")
