"""`media.timestamp_seconds` turns a clip's `start:` / `end:` field into
seconds for the end-after-start check and still-vs-video gate.

A bad parse returns None (caller treats it as a frame path or errors);
an off-by-sixty on `h:mm:ss` lets an out-point land before the in-point
or silently accept a still with a fake timestamp.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.media import timestamp_seconds


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_mm_ss_and_h_mm_ss() -> None:
    check("m:ss → minutes and seconds",
          timestamp_seconds("2:10") == 130, timestamp_seconds("2:10"))
    check("h:mm:ss folds hours in",
          timestamp_seconds("1:02:03") == 3723, timestamp_seconds("1:02:03"))
    check("0:00 is zero, not None",
          timestamp_seconds("0:00") == 0)
    check("leading whitespace is stripped",
          timestamp_seconds("  0:30  ") == 30)


def test_invalid_shapes() -> None:
    # Seconds must be two digits; a bare number or a path is a frame
    # reference, not a timestamp. (mm:ss with ss=60 still matches the
    # grammar — callers compare numeric order, they do not range-check.)
    for bad in ("", "10", "1:2", "not-a-time",
                "_assets/frame.jpg", "2:10:00:00"):
        check(f"{bad!r} is not a timestamp",
              timestamp_seconds(bad) is None, timestamp_seconds(bad))


def main() -> None:
    tests = [
        test_mm_ss_and_h_mm_ss,
        test_invalid_shapes,
    ]
    for t in tests:
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
