"""`api._validate_dispositions` gates the digest's per-point verdicts.

An unexplained removal cannot be written up in "What Was Removed and
Why"; a non-point id or a beat outside the plan corrupts the digest
record. CLI e2e covers one happy path; these pins cover the helper's
refusals and the clean shape it returns.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.api import _validate_dispositions


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


WRITEUP = {"plan": json.dumps([{"n": 1}, {"n": 2}])}
DIGEST = {
    "points": [{"id": "p1", "claim": "c1"}, {"id": "p2", "claim": "c2"}],
    "examples": [{"id": "e1"}],
    "references": [{"id": "r1"}],
}


def test_happy_kept_and_removed() -> None:
    got = _validate_dispositions(WRITEUP, DIGEST, {
        "p1": {"disposition": "kept"},
        "p2": {"disposition": "removed", "reason": "dup", "beat": 1},
    })
    check("kept has no reason key; removed keeps reason and beat",
          got == {
              "p1": {"disposition": "kept"},
              "p2": {"disposition": "removed", "reason": "dup", "beat": 1},
          }, got)


def test_removed_needs_reason() -> None:
    try:
        _validate_dispositions(WRITEUP, DIGEST,
                               {"p2": {"disposition": "removed"}})
    except ValueError as err:
        check("removal without reason raises", "reason" in str(err), err)
    else:
        check("removal without reason raises", False)


def test_non_point_ids_refused() -> None:
    try:
        _validate_dispositions(WRITEUP, DIGEST,
                               {"e1": {"disposition": "kept"}})
    except ValueError as err:
        check("example id is refused as not a point",
              "not a point id" in str(err), err)
    else:
        check("example id is refused as not a point", False)

    try:
        _validate_dispositions(WRITEUP, DIGEST,
                               {"missing": {"disposition": "kept"}})
    except ValueError as err:
        check("unknown id is refused", "not a point" in str(err), err)
    else:
        check("unknown id is refused", False)


def test_bad_beat_and_payload() -> None:
    try:
        _validate_dispositions(WRITEUP, DIGEST,
                               {"p1": {"disposition": "kept", "beat": 9}})
    except ValueError as err:
        check("beat outside the plan raises", "beat" in str(err), err)
    else:
        check("beat outside the plan raises", False)

    try:
        _validate_dispositions(WRITEUP, DIGEST,
                               {"p1": {"disposition": "kept", "beat": True}})
    except ValueError as err:
        check("bool beat is refused", "beat" in str(err), err)
    else:
        check("bool beat is refused", False)

    try:
        _validate_dispositions(WRITEUP, DIGEST, None)
    except ValueError as err:
        check("empty/non-object payload raises", "JSON object" in str(err), err)
    else:
        check("empty/non-object payload raises", False)

    try:
        _validate_dispositions(WRITEUP, DIGEST,
                               {"p1": {"disposition": "maybe"}})
    except ValueError as err:
        check("unknown disposition raises", "disposition" in str(err), err)
    else:
        check("unknown disposition raises", False)


def main() -> None:
    for t in (test_happy_kept_and_removed, test_removed_needs_reason,
              test_non_point_ids_refused, test_bad_beat_and_payload):
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
