"""`manifest.render_lines` is the plain-text Reader load that
`gdocs.manifest_text` appends under the chapter inventory (#243).

The old markdown table + `manifest.md` sidecar is gone; a drift here
shows up as a wrong or empty Reader load on every Doc push. Integration
checks only assert the section title appears — this file pins empty,
stale-`!!`, debt listing, and the none-debt sentence.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.manifest import render_lines


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_empty_rows() -> None:
    lines = render_lines([])
    text = "\n".join(lines)
    check("empty input still announces the Reader load",
          "Reader load" in text, text)
    check("empty input tells the author to rebuild summaries",
          "summarize rebuild" in text, text)
    check("empty input has no debt section",
          "Debts —" not in text, text)


def test_fresh_with_no_debt() -> None:
    lines = render_lines([
        {"file": "01.md", "words": 10, "introduces": 2, "debt": [],
         "state": "fresh"},
    ])
    text = "\n".join(lines)
    check("row names the essay and introduce/debt counts",
          "01.md — introduces 2 · debt 0" in text, text)
    check("no stale banner when every summary is fresh",
          "not fresh" not in text, text)
    check("none-debt sentence when no essay carries debt",
          "None. Every essay stands on ground an earlier one laid." in text,
          text)


def test_stale_debt_listing() -> None:
    lines = render_lines([
        {"file": "a.md", "words": 10, "introduces": 0,
         "debt": ["Undertow", "Habit"], "state": "stale"},
        {"file": "b.md", "words": 5, "introduces": 1, "debt": [],
         "state": "fresh"},
    ])
    text = "\n".join(lines)
    check("stale banner counts non-fresh summaries",
          "1 of 2 summaries are not fresh" in text, text)
    check("stale row carries the !! state mark",
          "!! stale" in text and "a.md" in text, text)
    check("debt list names the unmet terms",
          "a.md (!! stale) — Undertow, Habit" in text, text)
    check("fresh zero-debt row is listed without a !! mark",
          "b.md — introduces 1 · debt 0" in text
          and "b.md (!!" not in text, text)
    check("no markdown table markers in the plain-text report",
          "|---" not in text and "# Reader load" not in text, text)


def main() -> None:
    tests = [test_empty_rows, test_fresh_with_no_debt,
             test_stale_debt_listing]
    for t in tests:
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
