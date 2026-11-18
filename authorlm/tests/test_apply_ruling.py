"""`forms_page._apply_ruling` is the Merge-forms card's live ruling
gate: a ruling whose `seen` half no longer matches the Doc is stale,
cleared for merge, and the card's word-diff is rebuilt from what would
land now.

A missed stale clear merges a dead accept; a wrong shown text paints
the wrong diff. Pure over dicts — no DB, no Google, no model.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.forms_page import _apply_ruling  # noqa: E402


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def _row(**overrides):
    base = {"old": "red cat", "proposed": "blue cat", "current": "blue cat",
            "ruling": None, "ruling_stale": None, "segments": []}
    base.update(overrides)
    return base


def test_no_ruling_keeps_card_to_rule() -> None:
    row = _apply_ruling(_row(), None)
    check("ruling stays None", row["ruling"] is None, row)
    check("ruling_stale stays None", row["ruling_stale"] is None, row)
    check("diff is old → current (or proposed)",
          row["segments"]
          and any(s["op"] == "delete" and "red" in s["text"]
                  for s in row["segments"])
          and any(s["op"] == "insert" and "blue" in s["text"]
                  for s in row["segments"]), row["segments"])


def test_fresh_accept_keeps_ruling() -> None:
    ruling = {"verdict": "accept", "seen": "blue cat", "text": None}
    row = _apply_ruling(_row(current="blue cat"), ruling)
    check("fresh ruling is kept", row["ruling"] is ruling, row)
    check("not flagged stale", row["ruling_stale"] is None, row)
    check("diff still old → current",
          any(s["op"] == "insert" and "blue" in s["text"]
              for s in row["segments"]), row["segments"])


def test_stale_ruling_is_cleared_but_remembered() -> None:
    ruling = {"verdict": "accept", "seen": "blue cat", "text": None}
    row = _apply_ruling(_row(current="green cat"), ruling)
    check("stale ruling is cleared for merge", row["ruling"] is None, row)
    check("held ruling is kept under ruling_stale",
          row["ruling_stale"] is ruling, row)
    check("diff is against the Doc as it stands now",
          any(s["op"] == "insert" and "green" in s["text"]
              for s in row["segments"]), row["segments"])


def test_edit_verdict_diffs_against_ruling_text() -> None:
    ruling = {"verdict": "edit", "seen": "blue cat", "text": "teal cat"}
    row = _apply_ruling(_row(current="blue cat"), ruling)
    check("edit ruling stays", row["ruling"] is ruling, row)
    check("diff lands the edited text, not current",
          any(s["op"] == "insert" and "teal" in s["text"]
              for s in row["segments"])
          and not any(s["op"] == "insert" and "blue" in s["text"]
                      for s in row["segments"]), row["segments"])


def test_no_current_falls_back_to_proposed() -> None:
    row = _apply_ruling(_row(current=None, proposed="navy cat"), None)
    check("with no Doc half, diff uses proposed",
          any(s["op"] == "insert" and "navy" in s["text"]
              for s in row["segments"]), row["segments"])
    check("a ruling with no current cannot be stale",
          _apply_ruling(
              _row(current=None),
              {"verdict": "accept", "seen": "anything", "text": None}
          )["ruling_stale"] is None)


def test_empty_old_yields_no_segments() -> None:
    row = _apply_ruling(_row(old=""), None)
    check("no old text → empty segments", row["segments"] == [], row)


def main() -> None:
    for test in (
        test_no_ruling_keeps_card_to_rule,
        test_fresh_accept_keeps_ruling,
        test_stale_ruling_is_cleared_but_remembered,
        test_edit_verdict_diffs_against_ruling_text,
        test_no_current_falls_back_to_proposed,
        test_empty_old_yields_no_segments,
    ):
        print(f"{test.__name__}:")
        test()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
