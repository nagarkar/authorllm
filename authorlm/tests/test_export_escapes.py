"""PDF running heads and settings.toml round-trips depend on two tiny
escapers in `export.py`. Neither had a direct test: a title with `%` or
`_` would break LaTeX mid-build, and a setting value with `"` or `\\`
would corrupt `_exports/settings.toml` on the next `export set`.

Also pins the pure KDP geometry helpers that choose gutters and warn
before a print upload — wrong bands silently mis-margin the book.
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.export import (
    KDP_MAX_PAGES,
    KDP_MIN_PAGES,
    _latex_escape,
    _toml_escape,
    book_geometry,
    kdp_checks,
    kdp_gutter,
)


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_latex_escape_specials() -> None:
    got = _latex_escape(r"A_b%c$d#e&f{g}h~i^j\end")
    check("backslash becomes textbackslash",
          r"\textbackslash{}" in got, got)
    check("brace pair escaped", r"\{" in got and r"\}" in got, got)
    for raw, esc in (("%", r"\%"), ("$", r"\$"), ("#", r"\#"),
                     ("&", r"\&"), ("_", r"\_")):
        check(f"{raw!r} → {esc!r}", esc in got, got)
    check("tilde and caret use textascii forms",
          r"\textasciitilde{}" in got
          and r"\textasciicircum{}" in got, got)
    check("ordinary letters pass through",
          _latex_escape("Sermons") == "Sermons")
    check("empty string stays empty", _latex_escape("") == "")


def test_toml_escape_round_trip() -> None:
    raw = r'he said "hi" \ then more'
    escaped = _toml_escape(raw)
    check("quotes are escaped", '\\"' in escaped, escaped)
    check("backslashes are doubled", r'\\' in escaped, escaped)
    parsed = tomllib.loads(f'x = "{escaped}"')
    check("escaped value round-trips through tomllib",
          parsed["x"] == raw, repr(parsed))
    check("plain value is unchanged",
          _toml_escape("plain") == "plain")


def test_kdp_gutter_bands() -> None:
    check("unknown pages assume 151–300 band", kdp_gutter(None) == 0.5)
    check("≤150 → 0.375", kdp_gutter(150) == 0.375)
    check("151 → 0.5", kdp_gutter(151) == 0.5)
    check("300 → 0.5", kdp_gutter(300) == 0.5)
    check("301 → 0.625", kdp_gutter(301) == 0.625)
    check("over last band stays at last gutter",
          kdp_gutter(900) == 0.875)


def test_book_geometry_bleed() -> None:
    plain = book_geometry(6.0, 9.0, False, 200)
    bleed = book_geometry(6.0, 9.0, True, 200)
    check("no-bleed paper matches trim",
          "paperwidth=6in" in plain and "paperheight=9in" in plain, plain)
    check("bleed grows three outer sides by 0.125in",
          "paperwidth=6.125in" in bleed
          and "paperheight=9.25in" in bleed, bleed)
    check("bleed widens the outer margin",
          "outer=0.75in" in bleed and "outer=0.625in" in plain,
          (plain, bleed))
    check("inner uses gutter+0.375 for the page band",
          "inner=0.875in" in plain, plain)


def test_kdp_checks_warnings() -> None:
    # 5x8 is a standard KDP size; 4.5x7 is inside the accepted range
    # but not on the standard list. pages=10 is under KDP's minimum.
    odd = kdp_checks({"trim_width": 4.5, "trim_height": 7.0}, 10)
    check("non-standard trim is named",
          any("not a standard KDP size" in n for n in odd), odd)
    check("missing paperback ISBN is named",
          any("paperback ISBN" in n for n in odd), odd)
    check("short book is named",
          any(f"{KDP_MIN_PAGES}" in n and "at least" in n for n in odd),
          odd)

    okish = kdp_checks({
        "trim_width": 6.0, "trim_height": 9.0,
        "paperback_isbn": "9781234567890",
    }, 200)
    check("standard trim + ISBN + mid pages → no notes",
          okish == [], okish)

    unread = kdp_checks({
        "trim_width": 6.0, "trim_height": 9.0,
        "paperback_isbn": "9781234567890",
    }, None)
    check("unreadable page count is named",
          any("unreadable" in n for n in unread), unread)

    huge = kdp_checks({
        "trim_width": 6.0, "trim_height": 9.0,
        "paperback_isbn": "9781234567890",
    }, KDP_MAX_PAGES + 1)
    check("over-max pages is named",
          any(str(KDP_MAX_PAGES) in n for n in huge), huge)


def main() -> None:
    for t in (test_latex_escape_specials, test_toml_escape_round_trip,
              test_kdp_gutter_bands, test_book_geometry_bleed,
              test_kdp_checks_warnings):
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
