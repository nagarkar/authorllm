"""`clients._clamped_delta` / `_scale_models` sit on the chat-usage
discontinuity path: when a transcript file is replaced or truncated, the
ledger must never invent negative spend and must never guess a per-model
split of a clamped total.

No dedicated unit coverage previously — only the broader usage sweep
exercised the happy path. A silent regression here corrupts usage reports
and budget accounting.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.clients import _clamped_delta, _scale_models


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


FULL = {"messages": 10, "in": 100, "out": 50,
        "cache_read": 20, "cache_write": 5}
MODELS = {"claude": [10, 100, 50, 20, 5]}


def test_plain_delta() -> None:
    stored = {"messages": 4, "in": 40, "out": 10,
              "cache_read": 5, "cache_write": 1}
    got = _clamped_delta(FULL, stored)
    check("subtracts stored from recomputed per key",
          got == {"messages": 6, "in": 60, "out": 40,
                  "cache_read": 15, "cache_write": 4}, got)


def test_never_negative() -> None:
    # Truncation / replace: stored counters can exceed the new file.
    stored = {"messages": 99, "in": 999, "out": 999,
              "cache_read": 999, "cache_write": 999}
    got = _clamped_delta(FULL, stored)
    check("every counter clamps at zero when stored exceeds recomputed",
          got == {k: 0 for k in FULL}, got)


def test_missing_stored_keys() -> None:
    got = _clamped_delta(FULL, {})
    check("absent stored keys read as zero — full recomputed passes through",
          got == FULL, got)


def test_keys_only_from_recomputed() -> None:
    got = _clamped_delta({"in": 5}, {"in": 1, "out": 99, "extra": 7})
    check("only recomputed keys appear in the delta",
          got == {"in": 4}, got)


def test_scale_passthrough_when_unclamped() -> None:
    delta = dict(FULL)
    got = _scale_models(MODELS, FULL, delta)
    check("full-file delta keeps the original per-model breakdown",
          got is MODELS or got == MODELS, got)


def test_scale_empty_when_zero_delta() -> None:
    delta = {k: 0 for k in FULL}
    got = _scale_models(MODELS, FULL, delta)
    check("zero delta drops the models dict rather than inventing zeros",
          got == {}, got)


def test_scale_recomputed_bucket_when_clamped() -> None:
    delta = {"messages": 3, "in": 30, "out": 15,
             "cache_read": 0, "cache_write": 0}
    got = _scale_models(MODELS, FULL, delta)
    check("clamped (partial) delta collapses to a single (recomputed) bucket",
          got == {"(recomputed)": [3, 30, 15, 0, 0]}, got)


def main() -> None:
    tests = [test_plain_delta, test_never_negative, test_missing_stored_keys,
             test_keys_only_from_recomputed, test_scale_passthrough_when_unclamped,
             test_scale_empty_when_zero_delta,
             test_scale_recomputed_bucket_when_clamped]
    for t in tests:
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
