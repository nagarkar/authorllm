"""`revive_concept` after both endpoints of one edge were retired.

Retire stamps `retired_from.by_node` on each live edge it takes down. When
the author retires both ends of a declared edge, only the first retire
owns that stamp (the second finds the edge already retired). Reviving the
first retirer while the other end is still retired correctly leaves the
edge down; reviving the second end must restore it once both nodes are
live again. Requiring `by_node == this node` alone leaves both concepts
declared and the relationship retired forever.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm import concepts as cg
from authorlm.db import Database, ko_fields


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def _db() -> tuple[Database, str]:
    root = Path(tempfile.mkdtemp(prefix="authorlm-revive-"))
    db = Database(root / "authorlm.db")
    ms = ko_fields("ms")
    ms.update(name="revive-ms", path=str(root / "ms"))
    db.insert("manuscripts", ms)
    return db, ms["id"]


def _declared(db: Database, mid: str, name: str) -> dict:
    node = cg.add_concept(db, mid, name)
    db.update("concept_nodes", node["id"], {"status": "declared"})
    return dict(cg.get_concept(db, mid, name))


def test_dual_retire_then_both_revives_restores_edge() -> None:
    db, mid = _db()
    alpha = _declared(db, mid, "Alpha")
    beta = _declared(db, mid, "Beta")
    edge = cg.link_concepts(db, mid, "Alpha", "depends_on", "Beta",
                            status="declared")

    cg.retire_concept(db, mid, alpha)
    cg.retire_concept(db, mid, dict(cg.get_concept(db, mid, "Beta")))
    stamped = db.one("SELECT status, metadata FROM concept_edges WHERE id = ?",
                     (edge["id"],))
    check("first retire owns by_node; second finds the edge already down",
          stamped["status"] == "retired" and alpha["id"] in stamped["metadata"])

    first = cg.revive_concept(
        db, mid, dict(db.one("SELECT * FROM concept_nodes WHERE id = ?",
                             (alpha["id"],))))
    check("revive of the first retirer leaves the edge down while Beta is "
          "still retired",
          first["edges_revived"] == 0
          and db.one("SELECT status FROM concept_edges WHERE id = ?",
                     (edge["id"],))["status"] == "retired")

    second = cg.revive_concept(
        db, mid, dict(db.one("SELECT * FROM concept_nodes WHERE id = ?",
                             (beta["id"],))))
    row = db.one("SELECT status, metadata FROM concept_edges WHERE id = ?",
                 (edge["id"],))
    check("revive of the second end restores the declared edge",
          second["edges_revived"] == 1 and row["status"] == "declared",
          (second, row["status"], row["metadata"]))
    check("both nodes are declared again",
          db.one("SELECT status FROM concept_nodes WHERE id = ?",
                 (alpha["id"],))["status"] == "declared"
          and db.one("SELECT status FROM concept_nodes WHERE id = ?",
                     (beta["id"],))["status"] == "declared")


def test_unrelated_endpoint_retire_stays_down_until_that_end_revives() -> None:
    """Pin the existing Gravity/Void contract: an edge retired by the far
    end must not return when only this end is revived."""
    db, mid = _db()
    gravity = _declared(db, mid, "Gravity")
    mass = _declared(db, mid, "Mass")
    void = _declared(db, mid, "Void")
    cg.link_concepts(db, mid, "Gravity", "depends_on", "Mass",
                     status="declared")
    contrast = cg.link_concepts(db, mid, "Gravity", "contrasts_with", "Void",
                                status="declared")

    cg.retire_concept(db, mid, void)
    cg.retire_concept(db, mid, gravity)
    cg.revive_concept(
        db, mid, dict(db.one("SELECT * FROM concept_nodes WHERE id = ?",
                             (gravity["id"],))))
    check("Void's collateral stays retired while Void is retired",
          db.one("SELECT status FROM concept_edges WHERE id = ?",
                 (contrast["id"],))["status"] == "retired")
    check("Gravity→Mass collateral comes back",
          db.one("SELECT status FROM concept_edges WHERE from_node = ? "
                 "AND relation = ?",
                 (gravity["id"], "depends_on"))["status"] == "declared")

    cg.revive_concept(
        db, mid, dict(db.one("SELECT * FROM concept_nodes WHERE id = ?",
                             (void["id"],))))
    check("once Void is live again, its collateral returns",
          db.one("SELECT status FROM concept_edges WHERE id = ?",
                 (contrast["id"],))["status"] == "declared")


def main() -> None:
    tests = [
        test_dual_retire_then_both_revives_restores_edge,
        test_unrelated_endpoint_retire_stays_down_until_that_end_revives,
    ]
    for t in tests:
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
