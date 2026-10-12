"""link_concepts revive-after-retire / reject, and merge vs non-live twins.

Critical correctness: author `concept link` after retire (or after an
earlier reject) must restore a live edge; merge must not drop a live
duplicate edge merely because the canonical still carries a rejected or
retired twin of the same triple.

Run: python3 tests/test_concept_link_merge.py
"""

from __future__ import annotations

import os

os.environ["AUTHORLM_CONFIG"] = "/nonexistent/authorlm-test/config.toml"
os.environ["AUTHORLM_ENV"] = "/nonexistent/authorlm-test/.env"
os.environ["AUTHORLM_CLIENT"] = "none"

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from authorlm import concepts as cg  # noqa: E402
from authorlm.db import Database, ko_fields  # noqa: E402

PASSED = 0


def check(label: str, condition: bool, context: str = "") -> None:
    global PASSED
    assert condition, f"FAIL: {label}\n{context}"
    PASSED += 1
    print(f"  ok: {label}")


def fixture():
    root = Path(tempfile.mkdtemp(prefix="authorlm-concept-"))
    db = Database(root / "t.db")
    ms = ko_fields("ms")
    ms.update(name="t", path=str(root))
    db.insert("manuscripts", ms)
    return db, ms["id"]


def live_edges(db, mid):
    return [dict(r) for r in db.all(
        "SELECT * FROM concept_edges WHERE manuscript_id = ? "
        "AND status NOT IN ('rejected', 'retired')", (mid,))]


def test_link_after_retire_restores_edge():
    db, mid = fixture()
    cg.add_concept(db, mid, "Alpha")
    cg.add_concept(db, mid, "Beta")
    first = cg.link_concepts(db, mid, "Alpha", "depends_on", "Beta")
    check("initial link is declared", first["status"] == "declared")
    cg.retire_concept(db, mid, dict(cg.get_concept(db, mid, "Alpha")))
    check("retire took the edge down", len(live_edges(db, mid)) == 0)

    # Fresh declaration path (add_concept), then re-link — the author
    # rebuilding after retire, without going through revive_concept.
    revived = cg.add_concept(db, mid, "Alpha")
    check("add after retire yields declared", revived["status"] == "declared")
    again = cg.link_concepts(db, mid, "Alpha", "depends_on", "Beta")
    check("author re-link restores declared edge",
          again["status"] == "declared", str(again))
    check("same edge row was revived, not duplicated",
          again["id"] == first["id"])
    check("graph has exactly one live edge",
          len(live_edges(db, mid)) == 1)


def test_link_alone_after_retire_restores_edge():
    """link_concepts itself calls add_concept — no explicit add needed."""
    db, mid = fixture()
    cg.add_concept(db, mid, "Alpha")
    cg.add_concept(db, mid, "Beta")
    first = cg.link_concepts(db, mid, "Alpha", "depends_on", "Beta")
    cg.retire_concept(db, mid, dict(cg.get_concept(db, mid, "Alpha")))
    again = cg.link_concepts(db, mid, "Alpha", "depends_on", "Beta")
    check("link after retire restores declared",
          again["status"] == "declared" and again["id"] == first["id"])
    check("Alpha is live again",
          cg.get_concept(db, mid, "Alpha")["status"] == "declared")


def test_author_link_unrejects_edge():
    db, mid = fixture()
    cg.add_concept(db, mid, "A")
    cg.add_concept(db, mid, "B")
    edge = cg.link_concepts(db, mid, "A", "depends_on", "B")
    db.update("concept_edges", edge["id"], {"status": "rejected"})
    again = cg.link_concepts(db, mid, "A", "depends_on", "B",
                             status="declared")
    check("author declared re-link un-rejects",
          again["status"] == "declared" and again["id"] == edge["id"])


def test_inferred_link_leaves_rejected_for_reproposal():
    """Extraction must still see the rejected row unchanged."""
    db, mid = fixture()
    cg.add_concept(db, mid, "A")
    cg.add_concept(db, mid, "B")
    edge = cg.link_concepts(db, mid, "A", "depends_on", "B")
    db.update("concept_edges", edge["id"], {"status": "rejected"})
    again = cg.link_concepts(db, mid, "A", "depends_on", "B",
                             status="inferred")
    check("inferred link leaves rejected edge alone",
          again["status"] == "rejected" and again["id"] == edge["id"])
    check("no live edge invented for inferred path",
          len(live_edges(db, mid)) == 0)


def test_merge_keeps_live_edge_despite_rejected_twin():
    db, mid = fixture()
    canon = cg.add_concept(db, mid, "Canon")
    dup = cg.add_concept(db, mid, "Dup")
    other = cg.add_concept(db, mid, "Other")
    rejected = cg.link_concepts(db, mid, "Canon", "depends_on", "Other")
    db.update("concept_edges", rejected["id"], {"status": "rejected"})
    live = cg.link_concepts(db, mid, "Dup", "depends_on", "Other",
                            status="declared")
    unique = cg.link_concepts(db, mid, "Dup", "contrasts_with", "Other",
                              status="declared")
    result = cg.merge_concepts(db, mid, dict(canon), dict(dup))
    check("unique edge was re-pointed",
          result["repointed"] >= 1, str(result))
    check("live depends_on was not dropped as a twin of the reject",
          result["dropped"] == 0, str(result))
    edges = live_edges(db, mid)
    triples = {(cg.node_name(db, e["from_node"]), e["relation"],
                cg.node_name(db, e["to_node"])) for e in edges}
    check("Canon→depends_on→Other is live after merge",
          ("Canon", "depends_on", "Other") in triples, str(triples))
    check("Canon→contrasts_with→Other is live after merge",
          ("Canon", "contrasts_with", "Other") in triples, str(triples))
    still_rejected = db.one(
        "SELECT status FROM concept_edges WHERE id = ?", (rejected["id"],))
    check("historical reject row remains rejected",
          still_rejected["status"] == "rejected")
    # The live row should still be the duplicate's edge, now re-pointed.
    fresh = db.one("SELECT * FROM concept_edges WHERE id = ?", (live["id"],))
    check("live edge re-pointed onto canonical",
          fresh["from_node"] == canon["id"] and fresh["status"] == "declared",
          str(dict(fresh)))
    _ = unique  # used for graph shape above


def test_merge_keeps_live_edge_despite_retired_twin():
    db, mid = fixture()
    canon = cg.add_concept(db, mid, "Canon")
    dup = cg.add_concept(db, mid, "Dup")
    other = cg.add_concept(db, mid, "Other")
    old = cg.link_concepts(db, mid, "Canon", "depends_on", "Other")
    db.update("concept_edges", old["id"], {"status": "retired"})
    cg.link_concepts(db, mid, "Dup", "depends_on", "Other", status="declared")
    result = cg.merge_concepts(db, mid, dict(canon), dict(dup))
    check("retired twin did not force a drop",
          result["dropped"] == 0 and result["repointed"] == 1, str(result))
    check("one live depends_on remains",
          len(live_edges(db, mid)) == 1)


if __name__ == "__main__":
    test_link_after_retire_restores_edge()
    test_link_alone_after_retire_restores_edge()
    test_author_link_unrejects_edge()
    test_inferred_link_leaves_rejected_for_reproposal()
    test_merge_keeps_live_edge_despite_rejected_twin()
    test_merge_keeps_live_edge_despite_retired_twin()
    print(f"\n{PASSED} checks passed.")
