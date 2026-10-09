"""`writing.parse_reply` is the beat-draft door. It accepts exactly two
shapes: ordered WHY / SELF-CHECK / DRAFT, or a leading BLOCKED (optional
QUESTION). Mis-ordered labels and a reply in neither shape must refuse;
a bare BLOCKED line AFTER DRAFT must stay manuscript prose.

e2e Scenario W covers BLOCKED-first, empty WHY/DRAFT, markers, and
BLOCKED-in-prose via the stub path — not out-of-order labels or the
direct neither-shape refusal. This file calls the parser directly.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.writing import Blocked, Draft, ReplyError, parse_reply


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def _raises(text: str):
    try:
        parse_reply(text)
    except ReplyError as err:
        return err
    return None


GOOD = ("WHY\nBecause Choice.\n\nSELF-CHECK\nbeat: ok\n\n"
        "DRAFT\nThe prose arrives here.\n")


def test_happy_draft() -> None:
    got = parse_reply(GOOD)
    check("ordered labels yield a Draft", isinstance(got, Draft), type(got))
    check("why is the WHY body", got.why == "Because Choice.", got.why)
    check("self_check is the SELF-CHECK body",
          got.self_check == "beat: ok", got.self_check)
    check("text is everything after DRAFT",
          got.text == "The prose arrives here.", got.text)


def test_out_of_order() -> None:
    text = ("SELF-CHECK\nok\n\nWHY\nreason\n\nDRAFT\nprose\n")
    err = _raises(text)
    check("out-of-order labels raise ReplyError", err is not None)
    check("message names out of order", "out of order" in str(err), str(err))


def test_neither_shape() -> None:
    err = _raises("just some free prose with no labels")
    check("a label-less reply raises", err is not None)
    check("message says neither shape", "neither shape" in str(err), str(err))
    check("message lists the missing labels",
          "WHY" in str(err) and "SELF-CHECK" in str(err)
          and "DRAFT" in str(err), str(err))


def test_leading_blocked_with_question() -> None:
    text = ("BLOCKED\nNeed an attribution.\n\n"
            "QUESTION\nWhich source?\n")
    got = parse_reply(text)
    check("leading BLOCKED yields Blocked", isinstance(got, Blocked),
          type(got))
    check("reason is the BLOCKED body",
          got.reason == "Need an attribution.", got.reason)
    check("question is the QUESTION body",
          got.question == "Which source?", got.question)


def test_blocked_after_draft_is_prose() -> None:
    text = ("WHY\nreason\n\nSELF-CHECK\nok\n\nDRAFT\n"
            "A line of prose.\nBLOCKED\nand it continues.\n")
    got = parse_reply(text)
    check("BLOCKED after DRAFT is still a Draft", isinstance(got, Draft),
          type(got))
    check("the BLOCKED line is kept as prose",
          "BLOCKED\nand it continues." in got.text, got.text)


def test_reserved_markers_in_draft() -> None:
    # Only << / >> are reserved here (Doc-bridge grammar). {{ }} are
    # edit-staging markers elsewhere and are legal inside a beat draft.
    for marker in ("<<old>>", "keep >> out"):
        text = (f"WHY\nr\n\nSELF-CHECK\nok\n\nDRAFT\n{marker}\n")
        err = _raises(text)
        check(f"draft containing {marker!r} raises", err is not None)
        check(f"{marker!r}: message names reserved grammar",
              "reserved grammar" in str(err), str(err))
    ok = parse_reply("WHY\nr\n\nSELF-CHECK\nok\n\nDRAFT\na {{insert}}\n")
    check("{{ }} inside a draft is legal prose",
          isinstance(ok, Draft) and "{{insert}}" in ok.text, ok)


def test_empty_why_and_draft() -> None:
    err = _raises("WHY\n\nSELF-CHECK\nok\n\nDRAFT\nprose\n")
    check("empty WHY raises", err is not None)
    check("empty WHY message", "WHY is empty" in str(err), str(err))
    err = _raises("WHY\nr\n\nSELF-CHECK\nok\n\nDRAFT\n   \n")
    check("empty DRAFT raises", err is not None)
    check("empty DRAFT message", "nothing follows the DRAFT line" in str(err),
          str(err))


def main() -> None:
    tests = [test_happy_draft, test_out_of_order, test_neither_shape,
             test_leading_blocked_with_question,
             test_blocked_after_draft_is_prose,
             test_reserved_markers_in_draft, test_empty_why_and_draft]
    for t in tests:
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
