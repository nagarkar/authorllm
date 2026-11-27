"""Unit pins for changes._dash_rows: em-dash findings → file line numbers.

Lint reports em dashes by paragraph unit only; the session that judges
them needs the line. EmDashTests cover the happy path through
lint_chapter; these pin the edges — wrong unit, non-dash codes, opening
words that miss the line — so a drift cannot silently point at line 1
of the wrong paragraph.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm import changes  # noqa: E402
from authorlm.lint import Finding  # noqa: E402

PASSED = 0


def check(label: str, cond: bool, detail: object = "") -> None:
    global PASSED
    print(f"  [{'ok' if cond else 'FAIL'}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)
    PASSED += 1


def finding(code: str, unit: int, quote: str) -> Finding:
    return Finding(code=code, severity="warning", unit=unit, quote=quote,
                   message="dash", ref="aside")


def test_dash_rows() -> None:
    text = ("# Title\n\n"
            "First paragraph stays plain.\n\n"
            "The miller — a patient man — waited for the thaw.\n\n"
            "He walked — slowly — to market — alone — that spring.\n")

    # Units are 1-based non-empty blocks: title, plain, miller, walked.
    found = [
        finding("em-dash", 3, "The miller — a patient man — waited for the thaw."),
        finding("em-dash-stack", 4, "He walked — slowly — to market — alone — that spring."),
        finding("spelling", 3, "miller"),
        finding("em-dash", 99, "nowhere"),
    ]
    lines = changes._dash_rows(text, found)
    check("em-dash maps to the paragraph's line",
          lines.get(0) == 5, lines)
    check("em-dash-stack maps too",
          lines.get(1) == 7, lines)
    check("non-dash codes are skipped",
          2 not in lines, lines)
    check("unit past the last paragraph is skipped",
          3 not in lines, lines)

    # Opening words that do not appear → fall back to the paragraph's first line.
    miss = [finding("em-dash", 3, "ZZZ not in the paragraph at all")]
    lines = changes._dash_rows(text, miss)
    check("a missing opening falls back to the paragraph start",
          lines.get(0) == 5, lines)

    # Multi-line paragraph: opening on a later line (unit 2 = body).
    multi = ("# T\n\n"
             "Lead-in sentence stays above.\n"
             "The miller — a patient man — waited.\n")
    found = [finding("em-dash", 2,
                     "The miller — a patient man — waited.")]
    lines = changes._dash_rows(multi, found)
    check("opening on a later line of the paragraph is counted",
          lines.get(0) == 4, lines)


def main() -> None:
    test_dash_rows()
    print(f"\n{PASSED} checks passed")


if __name__ == "__main__":
    main()
