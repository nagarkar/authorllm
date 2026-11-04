"""`clients._four` coerces every JSONL usage object into the four counters
the chat-usage sweep bills against.

ALL FOUR are required: a cached turn can show `input_tokens: 2` beside
tens of thousands of cache tokens, so reading only input/output measures
almost nothing. Non-numeric values (including instruction-shaped strings)
must coerce to 0 silently so one bad line cannot abort the sweep. No suite
on main calls `_four` directly.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.clients import _four


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_full_cached_turn() -> None:
    # Shape called out in the docstring: nearly-empty uncached prompt.
    got = _four({
        "input_tokens": 2,
        "output_tokens": 40,
        "cache_read_input_tokens": 100,
        "cache_creation_input_tokens": 24652,
    })
    check("all four counters round-trip as ints",
          got == (2, 40, 100, 24652), repr(got))


def test_missing_keys_are_zero() -> None:
    check("empty usage is four zeros", _four({}) == (0, 0, 0, 0))
    check("partial usage zeros the rest",
          _four({"input_tokens": 9, "output_tokens": 1}) == (9, 1, 0, 0))


def test_non_numeric_coerces_to_zero() -> None:
    got = _four({
        "input_tokens": "nope",
        "output_tokens": None,
        "cache_read_input_tokens": ["x"],
        "cache_creation_input_tokens": {"n": 1},
    })
    check("strings/None/containers coerce to 0 without raising",
          got == (0, 0, 0, 0), repr(got))


def test_float_truncates_via_int() -> None:
    got = _four({"input_tokens": 1.9, "output_tokens": 2.1,
                 "cache_read_input_tokens": 0.0,
                 "cache_creation_input_tokens": 3.0})
    check("floats coerce through int()", got == (1, 2, 0, 3), repr(got))


def test_bool_is_int() -> None:
    # bool is a subclass of int; keep the documented int() path honest.
    got = _four({"input_tokens": True, "output_tokens": False,
                 "cache_read_input_tokens": False,
                 "cache_creation_input_tokens": True})
    check("bools become 1/0 via int()", got == (1, 0, 0, 1), repr(got))


def main() -> None:
    for t in (test_full_cached_turn, test_missing_keys_are_zero,
              test_non_numeric_coerces_to_zero, test_float_truncates_via_int,
              test_bool_is_int):
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
