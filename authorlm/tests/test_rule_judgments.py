"""`api._rule_judgments` decides accept vs reject for door INSERTION
judgment tags by presence alone, then folds an accepted tag onto the
paragraph it follows.

Wrong braced/unbraced matching silently flips a verdict, or leaves a
judgment as its own paragraph where `lens repair` never looks. Threads
with a non-empty `proposed_old` are the author's own `{{…}}` and must
never be ruled here. Pure: no DB writes.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.api import _rule_judgments


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def _thread(proposed_new: str, *, proposed_old: str = "", tid: str = "t1"
            ) -> dict:
    return {"id": tid, "proposed_new": proposed_new,
            "proposed_old": proposed_old}


def test_braced_tag_accepts_and_folds() -> None:
    tag = "[Judgment: soft opener]"
    marked = f"The claim stands.\n\n{{{{{tag}}}}}\n"
    text, rulings = _rule_judgments(marked, [_thread(tag)])
    check("braced tag still present accepts",
          rulings == [(_thread(tag), True)], rulings)
    check("accepted tag folds onto the prior paragraph",
          text == f"The claim stands.{tag}\n", repr(text))


def test_bare_tag_accepts() -> None:
    tag = "[Judgment: soft opener]"
    marked = f"Prose.\n\n{tag}\n"
    text, rulings = _rule_judgments(marked, [_thread(tag)])
    check("unbraced tag still present accepts",
          rulings == [(_thread(tag), True)], rulings)
    check("bare tag folds onto the prior paragraph",
          text == f"Prose.{tag}\n", repr(text))


def test_gone_tag_rejects() -> None:
    tag = "[Judgment: soft opener]"
    marked = "Prose alone.\n"
    text, rulings = _rule_judgments(marked, [_thread(tag)])
    check("missing tag rejects",
          rulings == [(_thread(tag), False)], rulings)
    check("rejected ruling leaves the marked text untouched",
          text == marked, repr(text))


def test_author_braces_are_skipped() -> None:
    door = _thread("[Judgment: machine]", tid="door")
    author = _thread("{{author note}}", proposed_old="kept", tid="author")
    marked = ("Prose.\n\n{{[Judgment: machine]}}\n\n{{author note}}\n")
    text, rulings = _rule_judgments(marked, [door, author])
    check("only the door insert thread is ruled",
          rulings == [(door, True)], rulings)
    check("author {{…}} with proposed_old stays in place",
          "{{author note}}" in text, repr(text))


def test_multi_thread_order() -> None:
    a = _thread("[Judgment: A]", tid="a")
    b = _thread("[Judgment: B]", tid="b")
    marked = ("First.\n\n{{[Judgment: A]}}\n\n"
              "Second.\n\n{{[Judgment: B]}}\n")
    text, rulings = _rule_judgments(marked, [a, b])
    check("rulings keep written order",
          [t["id"] for t, _ in rulings] == ["a", "b"], rulings)
    check("both present tags accept",
          [ok for _, ok in rulings] == [True, True], rulings)
    check("each tag folds onto its own prior paragraph",
          text == ("First.[Judgment: A]\n\n"
                   "Second.[Judgment: B]\n"), repr(text))


def main() -> None:
    tests = [
        test_braced_tag_accepts_and_folds,
        test_bare_tag_accepts,
        test_gone_tag_rejects,
        test_author_braces_are_skipped,
        test_multi_thread_order,
    ]
    for t in tests:
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
