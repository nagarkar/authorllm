"""`sweeps._changed_files` / `_claims_for` are the deterministic narrowing
step before the ontology sweep spends tokens.

Wrong change detection either audits the whole book on every collect or
skips a drifted essay. Wrong claim assembly either starves the model of
settled law or feeds relations the design excludes (`related_to`,
proposed edges). Readiness is covered in `test_api`; these helpers are not.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm import sweeps


MID = "m-1"


class FakeDB:
    """Enough of `db.all` for the two pure narrowing helpers."""

    def __init__(self, versions: list[dict] | None = None,
                 edges: list[dict] | None = None) -> None:
        self.versions = list(versions or [])
        self.edges = list(edges or [])

    def all(self, sql: str, params: tuple = ()) -> list[dict]:
        if "manuscript_versions" in sql:
            mid = params[0]
            rows = [r for r in self.versions if r["manuscript_id"] == mid]
            rows = sorted(rows, key=lambda r: r["version_no"], reverse=True)
            return rows[:2]
        if "concept_edges" in sql:
            mid, nid, _nid2 = params
            out = []
            for edge in self.edges:
                if edge["manuscript_id"] != mid:
                    continue
                if edge["relation"] not in (
                        "refutes", "depends_on", "contrasts_with"):
                    continue
                if edge["status"] not in ("declared", "confirmed"):
                    continue
                if edge["from_node"] == nid or edge["to_node"] == nid:
                    out.append(edge)
            return out
        raise AssertionError(f"unexpected SQL: {sql}")


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def _ver(no: int, files: dict[str, str], *, mid: str = MID) -> dict:
    return {
        "id": f"v{no}",
        "manuscript_id": mid,
        "version_no": no,
        "files": json.dumps(files),
    }


def test_changed_files_diff_and_deletes() -> None:
    db = FakeDB(versions=[
        _ver(2, {"a.md": "new", "b.md": "same"}),
        _ver(1, {"a.md": "old", "b.md": "same", "c.md": "gone"}),
    ])
    got = sweeps._changed_files(db, MID)
    check("edited and deleted paths are reported",
          got == {"a.md", "c.md"}, repr(got))


def test_changed_files_needs_two_versions() -> None:
    db = FakeDB(versions=[_ver(1, {"a.md": "only"})])
    check("a single version returns None (no prior baseline)",
          sweeps._changed_files(db, MID) is None)
    check("zero versions also returns None",
          sweeps._changed_files(FakeDB(), MID) is None)


def test_changed_files_ignores_other_manuscript() -> None:
    db = FakeDB(versions=[
        _ver(2, {"a.md": "new"}, mid="other"),
        _ver(1, {"a.md": "old"}, mid="other"),
    ])
    check("versions for another manuscript do not supply a baseline",
          sweeps._changed_files(db, MID) is None)


def test_claims_for_notes_and_relations() -> None:
    nodes = {
        "n1": {"id": "n1", "name": "Alpha"},
        "n2": {"id": "n2", "name": "Beta"},
        "n3": {"id": "n3", "name": "Gamma"},
    }
    node = {"id": "n1", "name": "Alpha", "notes": "is ground"}
    edges = [
        {"manuscript_id": MID, "from_node": "n1", "to_node": "n2",
         "relation": "depends_on", "status": "confirmed"},
        {"manuscript_id": MID, "from_node": "n3", "to_node": "n1",
         "relation": "refutes", "status": "declared"},
        # Excluded shapes:
        {"manuscript_id": MID, "from_node": "n1", "to_node": "n2",
         "relation": "related_to", "status": "confirmed"},
        {"manuscript_id": MID, "from_node": "n1", "to_node": "n2",
         "relation": "contrasts_with", "status": "proposed"},
        {"manuscript_id": "other", "from_node": "n1", "to_node": "n2",
         "relation": "depends_on", "status": "confirmed"},
    ]
    got = sweeps._claims_for(FakeDB(edges=edges), MID, node, nodes)
    check("notes become a named claim",
          "Alpha: is ground" in got, got)
    check("confirmed depends_on is included",
          "Alpha depends on Beta" in got, got)
    check("declared refutes is included (either endpoint)",
          "Gamma refutes Alpha" in got, got)
    check("related_to never becomes a claim",
          not any("related" in c for c in got), got)
    check("proposed contrasts_with is excluded",
          not any("contrasts" in c for c in got), got)
    check("exactly the three settled claims", len(got) == 3, got)


def test_claims_for_empty_notes() -> None:
    node = {"id": "n1", "name": "Alpha", "notes": ""}
    got = sweeps._claims_for(FakeDB(edges=[]), MID, node, {"n1": node})
    check("empty notes yield no note claim and no edges", got == [], got)


def main() -> None:
    for t in (test_changed_files_diff_and_deletes,
              test_changed_files_needs_two_versions,
              test_changed_files_ignores_other_manuscript,
              test_claims_for_notes_and_relations,
              test_claims_for_empty_notes):
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
