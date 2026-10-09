"""proposal adopt on a concept revival: collateral edges + standing notes.

`retire_concept` retires every live edge touching the node and stamps
`retired_from.by_node`. CLI `concept revive` calls `revive_concept`, which
restores exactly those edges and leaves notes alone. `proposals.adopt`
for kind `revival` used to (a) flip the node to `declared` without that
call, so accepting an extraction revival left the neighborhood retired
while the concept looked live again, and (b) write
`notes: payload.get("notes")` unconditionally, so a revival proposal
that omitted notes (extraction's common case) wiped the author's
standing definition on accept.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

os.environ["AUTHORLM_CONFIG"] = "/nonexistent/authorlm-test/config.toml"
os.environ["AUTHORLM_ENV"] = "/nonexistent/authorlm-test/.env"
os.environ["AUTHORLM_CLIENT"] = "none"

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm import concepts as cg
from authorlm import proposals as prop
from authorlm.db import Database, ko_fields


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def _fixture():
    root = Path(tempfile.mkdtemp(prefix="authorlm-revival-edges-"))
    (root / ".authorlm").mkdir()
    db = Database(root / ".authorlm" / "authorlm.db")
    ms = ko_fields("ms")
    ms.update(name="t", path=str(root), metadata="{}")
    db.insert("manuscripts", ms)
    return root, db, ms["id"]


def test_adopt_revival_restores_collateral_edges() -> None:
    root, db, mid = _fixture()
    try:
        gravity = cg.add_concept(db, mid, "Gravity")
        mass = cg.add_concept(db, mid, "Mass")
        inertia = cg.add_concept(db, mid, "Inertia")
        out_edge = cg.link_concepts(db, mid, "Gravity", "depends_on", "Mass")
        in_edge = cg.link_concepts(db, mid, "Inertia", "depends_on", "Gravity")

        retired = cg.retire_concept(db, mid, dict(gravity))
        check("retire takes both edges down", retired == 2, retired)

        row = prop.create(db, mid, "revival", gravity["id"], {
            "name": "Gravity", "kind": "concept",
            "notes": "returns in new material",
        })
        check("revival proposal created", row is not None)

        msg = prop.adopt(db, mid, dict(row))
        check("adopt reports edges restored",
              "2 edge(s) restored" in msg, msg)

        node = db.one("SELECT * FROM concept_nodes WHERE id = ?",
                      (gravity["id"],))
        check("concept is declared again", node["status"] == "declared",
              node["status"])
        check("introduced_in cleared for re-realization",
              node["introduced_in"] is None, node["introduced_in"])

        for eid, label in ((out_edge["id"], "out"), (in_edge["id"], "in")):
            edge = db.one("SELECT * FROM concept_edges WHERE id = ?", (eid,))
            check(f"{label}-edge live again after adopt",
                  edge["status"] == "declared", edge["status"])
    finally:
        import shutil
        shutil.rmtree(root, ignore_errors=True)


def test_adopt_revival_preserves_notes_when_proposal_omits_them() -> None:
    root, db, mid = _fixture()
    try:
        standing = "the pull of mass toward mass"
        gravity = cg.add_concept(db, mid, "Gravity", notes=standing)
        cg.retire_concept(db, mid, dict(gravity))

        # Extraction often raises revival with notes=None / omitted.
        row = prop.create(db, mid, "revival", gravity["id"], {
            "name": "Gravity", "kind": "concept", "notes": None,
        })
        check("revival proposal created", row is not None)

        prop.adopt(db, mid, dict(row))
        node = db.one("SELECT notes, status FROM concept_nodes WHERE id = ?",
                      (gravity["id"],))
        check("concept is live again", node["status"] == "declared",
              node["status"])
        check("author notes survive a notes-less revival accept",
              node["notes"] == standing, node["notes"])

        # A proposal that DOES carry notes still overlays them.
        cg.retire_concept(db, mid, dict(node))
        row2 = prop.create(db, mid, "revival", gravity["id"], {
            "name": "Gravity", "kind": "concept",
            "notes": "returns with a sharper gloss",
        })
        prop.adopt(db, mid, dict(row2))
        node2 = db.one("SELECT notes FROM concept_nodes WHERE id = ?",
                       (gravity["id"],))
        check("proposal notes win when present",
              node2["notes"] == "returns with a sharper gloss",
              node2["notes"])
    finally:
        import shutil
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    test_adopt_revival_restores_collateral_edges()
    test_adopt_revival_preserves_notes_when_proposal_omits_them()
    print("all ok")
