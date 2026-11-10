"""`api._accounting` tallies kept / removed / unaccounted digest points.

`write_digest --show`, `write_status`, and `write_complete` all surface
this dict. A wrong split silently undercounts removals or invents
"unaccounted" points the author already disposed — the opposite of the
§13.2 gate `_validate_dispositions` enforces on the write path.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.api import _accounting


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def _writeup(*, digest=None, dispositions=None) -> dict:
    meta: dict = {}
    if digest is not None:
        meta["digest"] = digest
    if dispositions is not None:
        meta["dispositions"] = dispositions
    return {"metadata": json.dumps(meta)}


def test_none_without_digest() -> None:
    check("missing digest → None", _accounting({"metadata": "{}"}) is None)
    check("digest key present but falsy → None",
          _accounting(_writeup(digest={})) is None)
    check("null digest → None",
          _accounting({"metadata": json.dumps({"digest": None})}) is None)


def test_splits_kept_removed_unaccounted() -> None:
    digest = {
        "points": [
            {"id": "p1", "claim": "first"},
            {"id": "p2", "claim": "second"},
            {"id": "p3", "claim": "third"},
        ]
    }
    dispositions = {
        "p1": {"disposition": "kept"},
        "p2": {"disposition": "removed", "reason": "dup"},
    }
    got = _accounting(_writeup(digest=digest, dispositions=dispositions))
    check("kept lists only kept ids", got["kept"] == ["p1"], got)
    check("removed lists only removed ids", got["removed"] == ["p2"], got)
    check("unaccounted is the rest", got["unaccounted"] == ["p3"], got)
    check("point_count is the digest size", got["point_count"] == 3, got)
    check("claims map every point id to its claim",
          got["claims"] == {"p1": "first", "p2": "second", "p3": "third"},
          got)


def test_empty_dispositions_all_unaccounted() -> None:
    digest = {"points": [{"id": "a", "claim": "A"},
                         {"id": "b", "claim": "B"}]}
    got = _accounting(_writeup(digest=digest, dispositions={}))
    check("no dispositions → everything unaccounted",
          got["kept"] == [] and got["removed"] == []
          and got["unaccounted"] == ["a", "b"], got)
    # dispositions key missing entirely is the same as {}.
    got2 = _accounting(_writeup(digest=digest))
    check("missing dispositions key matches empty dict",
          got2["unaccounted"] == ["a", "b"], got2)


def test_ignores_non_kept_removed_labels() -> None:
    digest = {"points": [{"id": "p1", "claim": "c"}]}
    # A stale or hand-edited disposition that is neither kept nor removed
    # must not land in either tally — it stays out of both lists and out
    # of unaccounted (the id IS in dispositions).
    got = _accounting(_writeup(
        digest=digest,
        dispositions={"p1": {"disposition": "maybe"}}))
    check("unknown disposition is neither kept nor removed",
          got["kept"] == [] and got["removed"] == [], got)
    check("id present in dispositions is not unaccounted",
          got["unaccounted"] == [], got)


def main() -> None:
    for t in (test_none_without_digest,
              test_splits_kept_removed_unaccounted,
              test_empty_dispositions_all_unaccounted,
              test_ignores_non_kept_removed_labels):
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
