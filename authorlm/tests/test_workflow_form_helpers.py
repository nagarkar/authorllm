"""Unit pins for workflow_page's form-history and change-display helpers.

`test_workflow_page` and `test_workflow_changes` cover walks through the
page. These pin the pure doors that decide which turns count as a unit's
form history (discussion must not), how an author or engine text becomes
a change list, and what text a form shows after replace/insert/cut/move
— wrong answers here rewrite a unit from a talk turn, or show the wrong
prose after a change-kind ruling (#546 discuss, #548 change kinds).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ["AUTHORLM_CONFIG"] = "/nonexistent/authorlm-test/config.toml"
os.environ["AUTHORLM_ENV"] = "/nonexistent/authorlm-test/.env"
os.environ["AUTHORLM_CLIENT"] = "none"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm import workflow_page as wp  # noqa: E402

PASSED = 0


def check(label: str, cond: bool, detail: object = "") -> None:
    global PASSED
    print(f"  [{'ok' if cond else 'FAIL'}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)
    PASSED += 1


def turn(step: str, label: str, action: str = "propose", **extra) -> dict:
    row = {"step": step, "unit_label": label, "action": action}
    row.update(extra)
    return row


def change(kind: str, original: str = "", where: str = "", quote: str = "",
           text: str = "") -> dict:
    return {"kind": kind, "original": original, "where": where, "quote": quote,
            "text": text}


def test_by_label_excludes_discuss() -> None:
    turns = [
        turn("construction", "p1", "propose", seq=1),
        turn("construction", "p1", "discuss", seq=2),
        turn("construction", "p1", "edit", seq=3),
        turn("construction", "p2", "propose", seq=4),
        turn("filters", "p1", "propose", seq=5),
        turn("construction", "", "propose", seq=6),
        turn("construction", None, "propose", seq=7),
    ]
    by = wp._by_label(turns, "construction")
    check("discuss turns are not part of a unit's form history",
          [t["action"] for t in by["p1"]] == ["propose", "edit"], by)
    check("other units keep their own turns, in order",
          [t["action"] for t in by["p2"]] == ["propose"]
          and "p1" in by and list(by) == ["p1", "p2"], by)
    check("a missing or empty label is skipped; other steps stay out",
          "filters" not in str(by) and None not in by and "" not in by, by)


def test_turn_and_form_changes() -> None:
    check("no turn means no changes", wp._turn_changes(None) == [])
    check("edits.changes is the turn's own list",
          wp._turn_changes({"edits": {"changes": [change("cut", "x")]}})
          == [change("cut", "x")])

    base = [change("replace", text="old")]
    cut = [change("cut", original="gone")]
    kept = wp._with_text(cut, "kept\n")
    check("a cut with author text becomes a replace of what it cut",
          kept == [change("replace", original="gone", text="kept")], kept)
    one = wp._with_text([change("insert", where="start", text="old")], "new")
    check("a lone insert keeps its kind and anchor, swaps the text",
          one == [change("insert", where="start", text="new")], one)
    many = wp._with_text(
        [change("replace", "a", text="b"), change("cut", "c")], "all new")
    check("several changes collapse to one whole-unit replace",
          many == [change("replace", text="all new")], many)

    own = wp._form_changes({"edits": {"changes": [change("cut", "x")]}}, base)
    check("a turn with its own changes wins over proposed text",
          own == [change("cut", "x")])
    from_prop = wp._form_changes({"proposed": "  Hello  "}, base)
    check("proposed text becomes the base change's new text",
          from_prop == [change("replace", text="  Hello  ")], from_prop)
    check("blank proposed and no changes means a pass",
          wp._form_changes({"proposed": "  "}, base) == [])


def test_display_text() -> None:
    before = "prefix old bit suffix"
    repl = [change("replace", original="old bit", text="new bit")]
    check("a replace of a span splices the new text into the unit",
          wp._display_text(repl, before) == "prefix new bit suffix")
    check("a replace with no original is the new text alone",
          wp._display_text([change("replace", text="whole")], before)
          == "whole")
    check("an insert shows its new text",
          wp._display_text([change("insert", where="start", text="In.")],
                           before) == "In.")
    check("a cut of the whole unit shows empty",
          wp._display_text([change("cut", original="whole")], "whole") == "")
    check("a cut whose original is not the unit leaves the unit standing",
          wp._display_text([change("cut", original="part")], "whole unit")
          == "whole unit")
    check("a move of the whole unit still shows the unit (it lands elsewhere)",
          wp._display_text([change("move", original="whole")], "whole")
          == "whole")
    check("several changes leave the before text for the form to compose",
          wp._display_text([change("replace", text="a"),
                            change("cut", "b")], before) == before)


def test_label_blocks_placement() -> None:
    check("section and paragraph labels",
          wp._label("section", 2) == "s2"
          and wp._label("paragraph", 3) == "p3")
    check("blank-line blocks drop empty runs",
          wp._blocks("A\n\n\nB\n\n  \n\nC") == ["A", "B", "C"])
    check("the last block of an essay",
          wp._last_block("A\n\nB\n\nC") == "C"
          and wp._last_block("") == "")
    units = [
        {"label": "b1", "form": {"placement": {"where": "start"}}},
        {"label": "b2", "form": {}},
        {"label": "b3"},
    ]
    check("plan placement is the form's placement for that label",
          wp._plan_placement(units, "b1") == {"where": "start"}
          and wp._plan_placement(units, "b2") is None
          and wp._plan_placement(units, "gone") is None)


def main() -> None:
    for name, fn in sorted((k, v) for k, v in globals().items()
                           if k.startswith("test_")):
        print(name)
        fn()
    print(f"{PASSED} checks passed")


if __name__ == "__main__":
    main()
