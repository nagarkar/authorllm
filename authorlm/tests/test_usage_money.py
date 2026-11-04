"""`usage._money` formats the cost column on every usage report row.

None becomes an en-dash (unpriced), floats render to two decimals aligned
to width 10. A silent change either prints a misleading 0.00 for unknown
prices or misaligns the board.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.usage import _money


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_none_is_en_dash() -> None:
    got = _money(None)
    check("None renders as a right-aligned en-dash",
          got.strip() == "–" and len(got) == 10, repr(got))


def test_priced_float() -> None:
    got = _money(1.5)
    check("priced float is width-10 with two decimals",
          got == f"{1.5:>10.2f}", repr(got))
    check("zero is still a number, not a dash",
          _money(0.0).strip() == "0.00", repr(_money(0.0)))


def main() -> None:
    for t in (test_none_is_en_dash, test_priced_float):
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
