"""Unit pins for workflow_page's letter-parse helpers.

`test_workflow_page.test_letter_parsing_edges` covers parse_letter as a
whole. These pin the small doors it calls: which earlier finding a
revision continues, how Keep/notes lines are stripped, which tier word
wins, and which JSON object a reply yields — wrong answers here attach a
finding to the wrong ancestor, drop a Keep item, or miss a structured
letter reply.
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
from authorlm.workflow import core  # noqa: E402

PASSED = 0


def check(label: str, cond: bool, detail: object = "") -> None:
    global PASSED
    print(f"  [{'ok' if cond else 'FAIL'}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)
    PASSED += 1


def test_continues() -> None:
    check("F-number form", wp._continues("continues F3") == "F3")
    check("Finding N form", wp._continues("Finding 12") == "F12")
    check("bare f-number is case-folded", wp._continues("f7") == "F7")
    check("none / empty / unrelated are blank",
          wp._continues("none") == ""
          and wp._continues("") == ""
          and wp._continues(None) == ""
          and wp._continues("new finding") == "")


def test_bullets_and_tier() -> None:
    lines = ["- a", "* b", "1. c", "2) d", "• e", "  ", "plain", "\t"]
    check("bullet and numbered prefixes strip; blanks drop; plain stays",
          wp._bullets(lines) == ["a", "b", "c", "d", "e", "plain"])
    check("must / should / could, case-insensitive; else blank",
          wp._tier("This MUST happen") == "must"
          and wp._tier("it Should") == "should"
          and wp._tier("could try") == "could"
          and wp._tier("optional only") == ""
          and wp._tier("") == ""
          and wp._tier(None) == "")


def test_json_in() -> None:
    check("a fenced json block is the object",
          wp._json_in('here\n```json\n{"a": 1}\n```\nmore') == {"a": 1})
    check("a bare object with trailing text still parses",
          wp._json_in('prefix {"a": 1, "b": [2]} trailing')
          == {"a": 1, "b": [2]})
    check("an unfenced fence-less object works",
          wp._json_in('{"x": true}') == {"x": True})
    check("invalid or missing JSON is None, not raised",
          wp._json_in("{bad") is None
          and wp._json_in("") is None
          and wp._json_in("no braces") is None
          and wp._json_in(None) is None)


def test_filters_base_latest() -> None:
    """core.filters_base: the filters walk the latest recorded base (#554)."""
    decisions = [
        {"kind": "skip", "detail": {}},
        {"kind": "filters-base", "detail": {"text": "first"}},
        {"kind": "principle", "detail": {}},
        {"kind": "filters-base", "detail": {"text": "second"}},
    ]
    check("the latest filters-base text wins",
          core.filters_base(decisions) == "second")
    check("no filters-base means None",
          core.filters_base([{"kind": "skip", "detail": {}}]) is None)
    check("a filters-base with no text detail is None",
          core.filters_base([{"kind": "filters-base", "detail": {}}]) is None)


def main() -> None:
    for name, fn in sorted((k, v) for k, v in globals().items()
                           if k.startswith("test_")):
        print(name)
        fn()
    print(f"{PASSED} checks passed")


if __name__ == "__main__":
    main()
