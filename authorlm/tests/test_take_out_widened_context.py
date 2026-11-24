"""`forms_page._take_out_widened_context` peels a widened Edit's
surroundings back out of the local file after Merge lands the whole
passage (#485 / #501).

If the peel misses, the old head/tail stay in the manuscript; if it peels
a non-widened edit, it would strip real prose. Hermetic — temp file and
thread dicts only; no DB, no Google, no model.
Run: python3 tests/test_take_out_widened_context.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("AUTHORLM_CONFIG",
                      "/nonexistent/authorlm-widened-peel/config.toml")
os.environ.setdefault("AUTHORLM_ENV",
                      "/nonexistent/authorlm-widened-peel/.env")
os.environ.setdefault("AUTHORLM_CLIENT", "none")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm import forms_page  # noqa: E402
from authorlm.forms_page import RULING_KEY  # noqa: E402


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def _thread(tid: str, ruling: dict) -> dict:
    return {"id": tid, "metadata": json.dumps({RULING_KEY: ruling})}


def test_peels_head_and_tail_leaving_the_passage() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "solo.md"
        path.write_text(
            "Lead.\n\nHEAD whole new passage TAIL\n\nClose.\n",
            encoding="utf-8")
        ruling = {
            "verdict": "edit",
            "text": "whole new passage",
            "context": {"head": "HEAD ", "tail": " TAIL"},
            "seen": "old", "at": "t",
        }
        lost = forms_page._take_out_widened_context(
            path, [_thread("t1", ruling)], {"t1": "span"})
        text = path.read_text(encoding="utf-8")
    check("no passages lost", lost == [], lost)
    check("file is head+passage+tail peeled to the passage alone",
          text == "Lead.\n\nwhole new passage\n\nClose.\n", text)


def test_reports_lost_when_surroundings_are_gone() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "solo.md"
        path.write_text("Lead.\n\njust the passage\n\nClose.\n",
                        encoding="utf-8")
        ruling = {
            "verdict": "edit",
            "text": "just the passage",
            "context": {"head": "HEAD ", "tail": " TAIL"},
            "seen": "old", "at": "t",
        }
        before = path.read_text(encoding="utf-8")
        lost = forms_page._take_out_widened_context(
            path, [_thread("t1", ruling)], {"t1": "span"})
        after = path.read_text(encoding="utf-8")
    check("missing needle is named in lost",
          lost == ["just the passage"], lost)
    check("file is left alone when the peel cannot find the span",
          after == before, after)


def test_skips_non_widened_and_missing_pairs() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "solo.md"
        original = "HEAD keep this TAIL\n"
        path.write_text(original, encoding="utf-8")
        accept = _thread("acc", {
            "verdict": "accept", "text": "", "seen": "keep this",
            "context": {"head": "HEAD ", "tail": " TAIL"}, "at": "t"})
        plain_edit = _thread("ed", {
            "verdict": "edit", "text": "rewritten", "seen": "keep this",
            "at": "t"})  # no context → not widened
        widened = _thread("wide", {
            "verdict": "edit", "text": "keep this",
            "context": {"head": "HEAD ", "tail": " TAIL"}, "at": "t"})
        # accept + plain edit are in pairs but must not peel; widened is
        # not in pairs, so also left alone.
        lost = forms_page._take_out_widened_context(
            path, [accept, plain_edit, widened],
            {"acc": "x", "ed": "y"})
        text = path.read_text(encoding="utf-8")
    check("nothing peeled", lost == [] and text == original, (lost, text))


def test_missing_file_is_a_quiet_no_op() -> None:
    missing = Path("/nonexistent/authorlm-widened-peel/no-file.md")
    ruling = {
        "verdict": "edit", "text": "x",
        "context": {"head": "a", "tail": "b"}, "at": "t",
    }
    lost = forms_page._take_out_widened_context(
        missing, [_thread("t1", ruling)], {"t1": "span"})
    check("absent path → empty lost list", lost == [], lost)


def main() -> None:
    for test in (
        test_peels_head_and_tail_leaving_the_passage,
        test_reports_lost_when_surroundings_are_gone,
        test_skips_non_widened_and_missing_pairs,
        test_missing_file_is_a_quiet_no_op,
    ):
        print(f"{test.__name__}:")
        test()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
