"""`hygiene.stale_suggestions` marks pending bridge / prerequisite
guidance moot when collect later realizes the concept or closes the gap.

A no-op suggestion the author rejects poisons belief evidence (api.collect
commentary). The helper is deterministic on `metadata.dedupe_key` and
must no-op when neither set is given. No suite on main calls it directly.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.hygiene import stale_suggestions


MID = "m-1"


class FakeDB:
    """`stale_suggestions` only needs `all` + `update` on guidance_history."""

    def __init__(self, rows: list[dict]) -> None:
        self.rows = {r["id"]: dict(r) for r in rows}
        self.updates: list[tuple[str, str, dict]] = []

    def all(self, sql: str, params: tuple = ()) -> list[dict]:
        mid = params[0] if params else None
        out = []
        for row in self.rows.values():
            if row.get("manuscript_id") != mid:
                continue
            if row.get("state") != "proposed":
                continue
            if row.get("kind") not in ("bridge", "prerequisite"):
                continue
            out.append(dict(row))
        return out

    def update(self, table: str, row_id: str, fields: dict) -> None:
        self.updates.append((table, row_id, dict(fields)))
        self.rows[row_id].update(fields)


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def _row(rid: str, kind: str, dedupe_key: str, *,
         state: str = "proposed", suggestion: str = "write it",
         mid: str = MID) -> dict:
    return {
        "id": rid,
        "manuscript_id": mid,
        "kind": kind,
        "state": state,
        "suggestion": suggestion,
        "metadata": json.dumps({"dedupe_key": dedupe_key}),
    }


def test_empty_sets_are_noop() -> None:
    db = FakeDB([_row("g1", "bridge", "bridge:c-realized")])
    got = stale_suggestions(db, MID)
    check("no realized/resolved ids → empty list", got == [], repr(got))
    check("no realized/resolved ids → no updates", db.updates == [])


def test_bridge_realized() -> None:
    db = FakeDB([
        _row("g1", "bridge", "bridge:c-1", suggestion="Introduce Ground"),
        _row("g2", "bridge", "bridge:c-other", suggestion="Introduce Other"),
        _row("g3", "bridge", "bridge:c-1", state="accepted"),
        _row("g4", "focus", "bridge:c-1"),
    ])
    got = stale_suggestions(db, MID, realized_ids={"c-1"})
    check("only the proposed bridge for the realized id is returned",
          got == [{"kind": "bridge", "suggestion": "Introduce Ground"}],
          repr(got))
    check("its row is marked stale",
          db.rows["g1"]["state"] == "stale", db.rows["g1"]["state"])
    check("a different bridge is left proposed",
          db.rows["g2"]["state"] == "proposed")
    check("already-accepted rows are not rewritten",
          db.rows["g3"]["state"] == "accepted")
    check("non-bridge kinds are ignored even with a matching key",
          "g4" not in {u[1] for u in db.updates})


def test_prerequisite_resolved() -> None:
    db = FakeDB([
        _row("g1", "prerequisite", "prerequisite:e-9",
             suggestion="Close the gap"),
        _row("g2", "prerequisite", "prerequisite:e-keep"),
    ])
    got = stale_suggestions(db, MID, resolved_edge_ids={"e-9"})
    check("resolved prerequisite is staled",
          got == [{"kind": "prerequisite", "suggestion": "Close the gap"}],
          repr(got))
    check("unrelated prerequisite stays proposed",
          db.rows["g2"]["state"] == "proposed")


def test_wrong_manuscript_ignored() -> None:
    db = FakeDB([_row("g1", "bridge", "bridge:c-1", mid="other")])
    got = stale_suggestions(db, MID, realized_ids={"c-1"})
    check("rows for another manuscript are ignored", got == [], repr(got))
    check("no update fired", db.updates == [])


def test_missing_dedupe_key_is_not_moot() -> None:
    db = FakeDB([{
        "id": "g1", "manuscript_id": MID, "kind": "bridge",
        "state": "proposed", "suggestion": "x",
        "metadata": "{}",
    }])
    got = stale_suggestions(db, MID, realized_ids={"c-1"})
    check("empty dedupe_key never matches a realized id", got == [], repr(got))


def main() -> None:
    for t in (test_empty_sets_are_noop, test_bridge_realized,
              test_prerequisite_resolved, test_wrong_manuscript_ignored,
              test_missing_dedupe_key_is_not_moot):
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
