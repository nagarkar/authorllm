"""`forms_page._ruled_neighbours` rewrites each card's prefix/suffix so an
edit beside an already-accepted (or edited) form builds on what that form
will land as, not on its old words (#487).

A wrong skip (widened `context`, decline, insert with no `old`) leaves the
author editing against stale surroundings; a wrong self-replace or a
missing `prefix_old` stash corrupts the next overlay pass. Hermetic —
pure dicts, no DB, no Google, no model.
Run: python3 tests/test_ruled_neighbours.py
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.forms_page import _ruled_neighbours  # noqa: E402


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def _card(cid: str, *, old: str, prefix: str = "", suffix: str = "",
          ruling: dict | None = None) -> dict:
    return {"id": cid, "old": old, "prefix": prefix, "suffix": suffix,
            "ruling": ruling}


def test_accept_rewrites_a_neighbour_prefix() -> None:
    forms = [
        _card("a", old="Alpha old.",
              ruling={"verdict": "accept", "seen": "Alpha new."}),
        _card("b", old="Beta.",
              prefix="Alpha old. ", suffix=" after."),
    ]
    got = _ruled_neighbours(forms)
    check("neighbour prefix reads the accepted landing",
          got[1]["prefix"] == "Alpha new. ", got[1])
    check("accepted card's own prefix is left alone",
          got[0]["prefix"] == "", got[0])
    check("old halves are stashed so a later overlay can re-run",
          got[1]["prefix_old"] == "Alpha old. "
          and got[1]["suffix_old"] == " after.", got[1])


def test_edit_uses_ruling_text_not_seen() -> None:
    forms = [
        _card("a", old="Alpha old.",
              ruling={"verdict": "edit", "text": "Alpha edited.",
                      "seen": "Alpha green."}),
        _card("b", old="Beta.", prefix="Lead Alpha old. Tail"),
    ]
    got = _ruled_neighbours(forms)
    check("edit lands its text, not the seen green half",
          got[1]["prefix"] == "Lead Alpha edited. Tail", got[1])


def test_decline_and_insert_do_not_land() -> None:
    forms = [
        _card("decline", old="Gone.",
              ruling={"verdict": "decline", "seen": "Gone."}),
        {"id": "insert", "old": "", "prefix": "", "suffix": "Gone. rest",
         "ruling": {"verdict": "accept", "seen": "Inserted."}},
        _card("peer", old="Peer.", prefix="Gone. ", suffix=""),
    ]
    got = _ruled_neighbours(forms)
    check("a decline does not rewrite neighbours",
          got[2]["prefix"] == "Gone. ", got[2])
    check("an insert (no old) never enters the landing map",
          "Gone." in got[2]["prefix"] and "Inserted." not in got[2]["prefix"],
          got[2])


def test_widened_context_is_skipped() -> None:
    # A widened edit already carries the whole passage; folding its old
    # into a neighbour would double-apply the surroundings (#501 edge).
    forms = [
        _card("wide", old="Alpha.",
              ruling={"verdict": "edit", "text": "Whole passage now.",
                      "context": {"head": "Before ", "tail": " after."}}),
        _card("peer", old="Beta.", prefix="Before Alpha. after."),
    ]
    got = _ruled_neighbours(forms)
    check("widened ruling does not rewrite a neighbour",
          got[1]["prefix"] == "Before Alpha. after.", got[1])


def test_self_is_not_rewritten_and_first_only() -> None:
    forms = [
        _card("a", old="X",
              prefix="X and X again",
              ruling={"verdict": "accept", "seen": "Y"}),
        _card("b", old="Z", prefix="X then X"),
    ]
    got = _ruled_neighbours(forms)
    check("a card does not rewrite its own prefix from its landing",
          got[0]["prefix"] == "X and X again", got[0])
    check("only the first occurrence in a neighbour is replaced",
          got[1]["prefix"] == "Y then X", got[1])


def test_idempotent_on_stashed_olds() -> None:
    forms = [
        _card("a", old="Old.",
              ruling={"verdict": "accept", "seen": "New."}),
        _card("b", old="Peer.", prefix="Old. ", suffix=""),
    ]
    once = _ruled_neighbours(copy.deepcopy(forms))
    twice = _ruled_neighbours(once)
    check("a second pass keeps the landed neighbour text",
          twice[1]["prefix"] == "New. " and twice[1]["prefix_old"] == "Old. ",
          twice[1])


def main() -> None:
    for test in (test_accept_rewrites_a_neighbour_prefix,
                 test_edit_uses_ruling_text_not_seen,
                 test_decline_and_insert_do_not_land,
                 test_widened_context_is_skipped,
                 test_self_is_not_rewritten_and_first_only,
                 test_idempotent_on_stashed_olds):
        print(f"{test.__name__}:")
        test()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
