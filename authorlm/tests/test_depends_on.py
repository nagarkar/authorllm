"""`summaries._depends_on` turns model-named ordinals into earlier para hashes.

The paragraph-records pass only trusts dependencies on EARLIER paragraphs
of the same section. Same-or-later ordinals, non-ints, and unknown numbers
are dropped and counted — otherwise a hallucinated link would poison the
summary graph.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.summaries import _depends_on


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_earlier_ordinals_resolve() -> None:
    earlier = {1: "h1", 2: "h2", 4: "h4"}
    hashes, dropped = _depends_on([1, 2], 3, earlier)
    check("earlier ordinals become their hashes in order",
          hashes == ["h1", "h2"] and dropped == 0, (hashes, dropped))


def test_drops_same_later_unknown_non_int() -> None:
    earlier = {1: "h1", 2: "h2"}
    hashes, dropped = _depends_on(
        [1, 3, 2, "1", None, 1], 3, earlier)
    # 3 / "1" / None drop; the trailing duplicate 1 is deduped, not dropped.
    check("same-or-later and non-ints are dropped",
          hashes == ["h1", "h2"] and dropped == 3, (hashes, dropped))
    # ordinal 3 naming itself (n < ordinal fails for n == 3)
    hashes, dropped = _depends_on([3], 3, {3: "self"})
    check("self ordinal is dropped even if earlier map has it",
          hashes == [] and dropped == 1, (hashes, dropped))
    hashes, dropped = _depends_on([9], 3, earlier)
    check("unknown earlier ordinal is dropped",
          hashes == [] and dropped == 1, (hashes, dropped))


def test_empty_and_dedupe() -> None:
    check("None raw yields empty with no drops",
          _depends_on(None, 5, {1: "h"}) == ([], 0))
    check("empty list yields empty with no drops",
          _depends_on([], 5, {1: "h"}) == ([], 0))
    hashes, dropped = _depends_on([1, 1, 2, 1], 5, {1: "h1", 2: "h2"})
    check("duplicate ordinals keep the first hash once",
          hashes == ["h1", "h2"] and dropped == 0, (hashes, dropped))


if __name__ == "__main__":
    test_earlier_ordinals_resolve()
    test_drops_same_later_unknown_non_int()
    test_empty_and_dedupe()
    print("ok")
