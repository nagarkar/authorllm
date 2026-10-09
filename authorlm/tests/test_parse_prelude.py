"""`filtering.parse_prelude` freezes the motif registry for a global
filter run. Every later unit is judged against that opaque string.

The passes suite (F11) only feeds a well-formed registry. This file pins
the refusal edges: non-object reply, missing/blank registry, reserved
markers inside the text, and the happy strip of surrounding whitespace.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.filtering import ReplyError, parse_prelude


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def _raises(raw):
    try:
        parse_prelude(raw)
    except ReplyError as err:
        return err
    return None


def test_happy_strip() -> None:
    got = parse_prelude({"registry": "  motif A → motif B  \n"})
    check("surrounding whitespace is stripped",
          got == "motif A → motif B", repr(got))


def test_not_object() -> None:
    for label, raw in (("list", []), ("string", "nope"), ("None", None),
                       ("int", 1)):
        err = _raises(raw)
        check(f"{label} raises ReplyError", err is not None)
        check(f"{label} message names 'not a JSON object'",
              "not a JSON object" in str(err), str(err))


def test_missing_or_blank_registry() -> None:
    for label, raw in (("missing key", {}),
                       ("None registry", {"registry": None}),
                       ("empty string", {"registry": ""}),
                       ("whitespace only", {"registry": "  \n\t  "})):
        err = _raises(raw)
        check(f"{label} raises ReplyError", err is not None)
        check(f"{label} message demands a non-empty registry",
              "non-empty `registry`" in str(err), str(err))


def test_reserved_markers() -> None:
    for marker in ("<<", ">>", "{{", "}}"):
        err = _raises({"registry": f"keep {marker} out"})
        check(f"registry containing {marker!r} raises", err is not None)
        check(f"{marker!r} is named in the refusal",
              repr(marker) in str(err), str(err))


def main() -> None:
    tests = [test_happy_strip, test_not_object,
             test_missing_or_blank_registry, test_reserved_markers]
    for t in tests:
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
