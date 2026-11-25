"""`normalize._lift_math` / `_lift_marked_math` keep TeX opaque while
prose rules run. push_doc writes normalize back to disk, so a silent
regression guts `\\` row breaks and `\\{` braces permanently.

Pins: closed display/inline spans lift to sentinels; an unclosed `$$`
remnant stays opaque through normalize; forms that cut through math lift
each reading's pieces without swallowing the markers. Hermetic.
Run: python3 tests/test_lift_math.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("AUTHORLM_CONFIG",
                      "/nonexistent/authorlm-lift-math/config.toml")
os.environ.setdefault("AUTHORLM_ENV",
                      "/nonexistent/authorlm-lift-math/.env")
os.environ.setdefault("AUTHORLM_CLIENT", "none")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.normalize import (  # noqa: E402
    _MATH_SENTINEL, _lift_marked_math, _lift_math, normalize_markdown,
)


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_closed_spans_lift_to_sentinels() -> None:
    text = "Prose $a+b$ and $$ x = 1 $$ end."
    lifted, spans = _lift_math(text)
    check("display span is recorded first (DISPLAY before INLINE)",
          spans[0] == "$$ x = 1 $$", spans)
    check("inline span is recorded",
          spans[1] == "$a+b$", spans)
    check("lifted text carries only sentinels for the math",
          lifted == (f"Prose {_MATH_SENTINEL % 1} and "
                     f"{_MATH_SENTINEL % 0} end."),
          lifted)
    check("no raw $ left in the prose stream",
          "$" not in lifted, lifted)


def test_unclosed_display_stays_opaque_through_normalize() -> None:
    # Docstring risk: an unclosed $$ left in the prose stream lets
    # _ESCAPE strip \\ and \{, and push_doc would persist that damage.
    raw = "Keep $\\{a\\}$ and unclosed $$\n remnant \\{\n"
    lifted, spans = _lift_math(raw)
    check("inline before the opener still lifts",
          spans[0] == "$\\{a\\}$", spans)
    check("from the unclosed $$ through EOF is one opaque span",
          spans[1] == "$$\n remnant \\{", spans)
    check("prose stream has no leftover $$",
          "$$" not in lifted, lifted)

    out = normalize_markdown(raw)
    check("normalize round-trip keeps the TeX braces and row-break bytes",
          "$\\{a\\}$" in out and "$$" in out and "\\{" in out, repr(out))
    check("normalize is idempotent on the unclosed remnant",
          normalize_markdown(out) == out, repr(out))


def test_marked_math_lifts_each_half_around_form_markers() -> None:
    marked = "See <<$a+1$>>{{$b+1$}} now."
    spans: list[str] = []
    lifted = _lift_marked_math(marked, spans)
    check("old-half and new-half math each become a span",
          spans == ["$a+1$", "$b+1$"], spans)
    check("form markers stay in the prose stream between the pieces",
          lifted == (f"See <<{_MATH_SENTINEL % 0}>>"
                     f"{{{{{_MATH_SENTINEL % 1}}}}} now."),
          lifted)
    check("no raw math delimiters leak into the prose stream",
          "$" not in lifted, lifted)


def main() -> None:
    for fn in (test_closed_spans_lift_to_sentinels,
               test_unclosed_display_stays_opaque_through_normalize,
               test_marked_math_lifts_each_half_around_form_markers):
        print(f"{fn.__name__}:")
        fn()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
