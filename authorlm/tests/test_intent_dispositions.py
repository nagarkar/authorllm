"""`api._intent_dispositions` assigns served / deferred / unserved to
every member of a writeup's intent block at completion.

An untagged accepted beat counts toward every member; a tagged beat
counts only for the listed ids. Deferred wins over served. Wrong math
mis-attributes goals and poisons each intent's `served_by` history.
Pure over the writeup row + block — no DB.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.api import _intent_dispositions


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def _writeup(plan: list, cursor: int) -> dict:
    return {"plan": json.dumps(plan), "cursor": cursor}


def _block(members: list[dict], deferred: dict | None = None) -> dict:
    return {"members": members, "deferred": deferred or {}}


def test_empty_block() -> None:
    check("empty block yields no dispositions",
          _intent_dispositions(_writeup([], 0), {}) == [])
    check("None-like empty block yields no dispositions",
          _intent_dispositions(_writeup([], 0), {"members": []}) == [])


def test_untagged_accepted_beat_serves_every_member() -> None:
    plan = [{"intents": []}, {"intents": ["only-second"]}]
    members = [{"id": "alpha", "role": "primary"},
               {"id": "beta", "role": "secondary"}]
    # Cursor 1: only the first (untagged) beat is accepted.
    got = _intent_dispositions(_writeup(plan, 1), _block(members))
    check("untagged accepted beat serves every member",
          [e["disposition"] for e in got] == ["served", "served"], got)
    check("beats count is the accepted prefix length",
          all(e["beats"] == 1 for e in got), got)


def test_tagged_beats_serve_only_listed_ids() -> None:
    plan = [{"intents": ["alpha"]}, {"intents": ["beta"]}]
    members = [{"id": "alpha"}, {"id": "beta"}, {"id": "gamma"}]
    # Cursor 1: only alpha's beat is accepted.
    got = _intent_dispositions(_writeup(plan, 1), _block(members))
    by_id = {e["id"]: e["disposition"] for e in got}
    check("tagged accepted beat serves only listed ids",
          by_id == {"alpha": "served", "beta": "unserved",
                    "gamma": "unserved"}, by_id)


def test_deferred_wins_over_served() -> None:
    plan = [{"intents": []}]  # untagged → would serve everyone
    members = [{"id": "alpha"}, {"id": "beta"}]
    deferred = {"beta": {"reason": "later chapter"}}
    got = _intent_dispositions(_writeup(plan, 1),
                               _block(members, deferred))
    by_id = {e["id"]: e for e in got}
    check("untagged beat still serves a non-deferred member",
          by_id["alpha"]["disposition"] == "served", by_id)
    check("deferred wins over the untagged serve",
          by_id["beta"]["disposition"] == "deferred", by_id)
    check("deferred reason is preserved",
          by_id["beta"]["reason"] == "later chapter", by_id)


def test_cursor_slices_the_plan() -> None:
    plan = [{"intents": ["alpha"]}, {"intents": ["beta"]}]
    members = [{"id": "alpha"}, {"id": "beta"}]
    none_yet = _intent_dispositions(_writeup(plan, 0), _block(members))
    check("cursor 0 accepts nothing — every member unserved",
          [e["disposition"] for e in none_yet] == ["unserved", "unserved"],
          none_yet)
    both = _intent_dispositions(_writeup(plan, 2), _block(members))
    check("cursor past both tagged beats serves each listed id",
          [e["disposition"] for e in both] == ["served", "served"], both)
    check("beats mirrors the full accepted prefix",
          all(e["beats"] == 2 for e in both), both)


def main() -> None:
    tests = [
        test_empty_block,
        test_untagged_accepted_beat_serves_every_member,
        test_tagged_beats_serve_only_listed_ids,
        test_deferred_wins_over_served,
        test_cursor_slices_the_plan,
    ]
    for t in tests:
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
