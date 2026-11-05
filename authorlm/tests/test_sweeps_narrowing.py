"""`sweeps._changed_files` / `_claims_for` are the deterministic narrowing
step before the ontology sweep spends tokens.

Wrong change detection either audits the whole book on every collect or
skips a drifted essay. Wrong claim assembly either starves the model of
settled law or feeds relations the design excludes (`related_to`,
proposed edges). Readiness is covered in `test_api`; these helpers are not.
"""

from __future__ import annotations

import os

os.environ["AUTHORLM_CONFIG"] = "/nonexistent/authorlm-test/config.toml"
os.environ["AUTHORLM_ENV"] = "/nonexistent/authorlm-test/.env"

import sys  # noqa: E402
import tempfile  # noqa: E402
from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm import sweeps  # noqa: E402
from authorlm.db import Database  # noqa: E402
from authorlm.revisions import record_version  # noqa: E402


MID = "m-1"


class FakeDB:
    """Enough of `db.all` for `_claims_for`."""

    def __init__(self, edges: list[dict] | None = None) -> None:
        self.edges = list(edges or [])

    def all(self, sql: str, params: tuple = ()) -> list[dict]:
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


def _history(*versions: dict[str, str], mid: str = MID) -> Database:
    """A throwaway database holding `versions`, oldest first."""
    folder = tempfile.mkdtemp(prefix="authorlm-sweeps-")
    db = Database(Path(folder) / "authorlm.db")
    for files in versions:
        record_version(db, mid, files, None, source="test")
    return db


def test_changed_files_diff_and_deletes() -> None:
    db = _history(
        {"a.md": "old", "b.md": "same", "c.md": "gone"},
        {"a.md": "new", "b.md": "same"},
    )
    got = sweeps._changed_files(db, MID)
    check("edited and deleted paths are reported",
          got == {"a.md", "c.md"}, repr(got))


def test_changed_files_needs_two_versions() -> None:
    db = _history({"a.md": "only"})
    check("a single version returns None (no prior baseline)",
          sweeps._changed_files(db, MID) is None)
    check("zero versions also returns None",
          sweeps._changed_files(_history(), MID) is None)


def test_changed_files_ignores_other_manuscript() -> None:
    db = _history({"a.md": "old"}, {"a.md": "new"}, mid="other")
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
