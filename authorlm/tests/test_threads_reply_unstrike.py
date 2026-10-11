"""Margin-thread reply law and Doc-export strike peel.

`classify_reply` is ratified keyword law: punctuation and spacing must not
turn an approve into conversation, and soft refusals ("no thanks") must
not decline. `_unstrike` undoes Google's markdown export, which closes and
reopens `~~` at every hard line break inside a struck OLD half — both the
backslash-break (`~~\\\n~~`) and two-space break (`~~  \n~~`) forms.
`pending_forms` already peels `\\\n` once in test_api; the two-space form
and the reply edges have no dedicated pins.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm import threads as th


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_classify_approve_edges() -> None:
    for text in ("okay.", "Okay!", "  LGTM  ", "yes", "apply"):
        got = th.classify_reply(text)
        check(f"{text!r} is approve", got == "approve", got)


def test_classify_decline_edges() -> None:
    for text in ("don't", "dont", "Don't.", "no", "revert"):
        got = th.classify_reply(text)
        check(f"{text!r} is decline", got == "decline", got)


def test_classify_conversation() -> None:
    for text in ("no thanks", "go ahead but soften it",
                 "maybe later", "what do you think?"):
        got = th.classify_reply(text)
        check(f"{text!r} stays conversation", got == "conversation", got)


def test_unstrike_backslash_break() -> None:
    old = "line one,~~\\\n~~line two"
    got = th._unstrike(old)
    check("backslash hard-break strike fragments peel to one break",
          got == "line one,\\\nline two", repr(got))


def test_unstrike_two_space_break() -> None:
    old = "line one,~~  \n~~line two"
    got = th._unstrike(old)
    check("two-space hard-break strike fragments peel to one break",
          got == "line one,  \nline two", repr(got))


def test_pending_forms_peels_two_space_strike() -> None:
    marked = "<<line one,~~  \n~~line two>>{{fresh line}}"
    forms = th.pending_forms(marked)
    check("one replace form", len(forms) == 1, forms)
    check("old half is unstruck",
          forms[0]["old"] == "line one,  \nline two", forms[0])
    check("new half is untouched",
          forms[0]["new"] == "fresh line", forms[0])


def main() -> None:
    tests = [
        test_classify_approve_edges,
        test_classify_decline_edges,
        test_classify_conversation,
        test_unstrike_backslash_break,
        test_unstrike_two_space_break,
        test_pending_forms_peels_two_space_strike,
    ]
    for t in tests:
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
