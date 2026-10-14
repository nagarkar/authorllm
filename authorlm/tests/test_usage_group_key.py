"""`usage._group_key` and `usage._money` shape every usage report row.

`_group_key` picks the aggregation bucket (purpose / model / verb / day /
client / raw key). `_money` renders None as an en-dash column and priced
floats to two decimals. A silent change either merges unrelated spend or
prints misleading blanks for unpriced calls.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.usage import _group_key, _money


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


KEY = "filter|claude-sonnet"
ENTRY = {
    "invocation": "authorlm filter",
    "ts": "2026-10-14T09:15:00.000Z",
    "client": {"engine": "claude-code", "session": "abc123def456"},
}


def test_by_purpose() -> None:
    got = _group_key("purpose", KEY, ENTRY)
    check("purpose grouping takes the left of purpose|model",
          got == "filter", repr(got))


def test_by_model() -> None:
    got = _group_key("model", KEY, ENTRY)
    check("model grouping takes the right of purpose|model",
          got == "claude-sonnet", repr(got))


def test_by_verb() -> None:
    got = _group_key("verb", KEY, ENTRY)
    check("verb grouping uses the invocation",
          got == "authorlm filter", repr(got))
    got = _group_key("verb", KEY, {})
    check("missing invocation becomes (unnamed)",
          got == "(unnamed)", repr(got))


def test_by_day() -> None:
    got = _group_key("day", KEY, ENTRY)
    check("day grouping is the ISO date prefix",
          got == "2026-10-14", repr(got))
    got = _group_key("day", KEY, {})
    check("missing ts yields an empty day key", got == "", repr(got))


def test_by_client() -> None:
    got = _group_key("client", KEY, ENTRY)
    check("client grouping names engine/session prefix",
          got == "claude-code/abc123def456", repr(got))
    got = _group_key("client", KEY, {})
    check("missing client becomes (unattributed)",
          got == "(unattributed)", repr(got))


def test_default_raw_key() -> None:
    got = _group_key("purpose|model", KEY, ENTRY)
    check("unknown by keeps the raw purpose|model key",
          got == KEY, repr(got))


def test_money_rendering() -> None:
    check("None renders as a right-aligned en-dash",
          _money(None) == f"{'–':>10}", repr(_money(None)))
    check("float renders to two decimals, width 10",
          _money(12.5) == f"{12.5:>10.2f}", repr(_money(12.5)))
    check("zero is still a priced column",
          _money(0.0) == f"{0.0:>10.2f}", repr(_money(0.0)))


def main() -> None:
    tests = [test_by_purpose, test_by_model, test_by_verb, test_by_day,
             test_by_client, test_default_raw_key, test_money_rendering]
    for t in tests:
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
