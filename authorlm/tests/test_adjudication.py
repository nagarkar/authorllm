"""Step-two adjudication: opt-in, and inert when off.

Standalone, hermetic, zero LLM calls:  python3 tests/test_adjudication.py
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from authorlm import adjudication, api  # noqa: E402
from authorlm.db import Database, loads  # noqa: E402
from authorlm.extraction import extract_concepts  # noqa: E402
from authorlm.revisions import collect_revision  # noqa: E402

PASSED = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASSED
    if not ok:
        raise AssertionError(f"FAILED: {label}\n{detail}")
    PASSED += 1
    print(f"  ok: {label}")


CANDIDATES = {
    "concepts": [
        {"name": "The Herdsman", "kind": "concept",
         "notes": "The one who tends the flock of the dead."},
        {"name": "Herdsman's Rod", "kind": "concept",
         "notes": "The rod the Herdsman carries."},
        {"name": "The Flock", "kind": "concept",
         "notes": "Those the Herdsman tends, gathered and driven."},
        {"name": "Passing Fancy", "kind": "concept", "notes": "A whim."},
    ],
    "links": [
        {"from": "The Herdsman", "relation": "depends_on", "to": "The Flock"},
    ],
    "aliases": [],
}

TEXT = (
    "# The Herdsman\n\n"
    "The Herdsman tends the flock of the dead, and the Flock follows.\n\n"
    "## The rod\n\n"
    "The Herdsman carries a rod, and by the rod the Flock is driven. A "
    "passing fancy is not a thing the Herdsman heeds.\n\n"
    "## Again\n\n"
    "The Herdsman and the Flock walk on; the Herdsman's rod goes before "
    "them, and the Flock does not stray.\n"
)


class StubLLM:
    """Step one always returns CANDIDATES; step two returns `verdicts`."""

    enabled = True
    extraction_max_chars = 24000

    def __init__(self, adjudicate: bool, verdicts: dict | None = None):
        self.config = {"extraction": {"adjudicate": adjudicate}}
        self.verdicts = verdicts
        self.calls = []

    def stats_line(self):
        return None

    def complete_json(self, system, user, thinking_budget=None):
        step = "adjudication" if "ADJUDICATOR" in system else "extraction"
        self.calls.append((step, system, user))
        if step == "adjudication":
            return self.verdicts
        return json.loads(json.dumps(CANDIDATES))


def build(root: Path) -> tuple[Database, dict]:
    source = root / "manuscript"
    source.mkdir(parents=True)
    (source / "01-herdsman.md").write_text(TEXT, encoding="utf-8")
    db = Database(root / "authorlm.db")
    manuscript = api.register_manuscript(db, "stub", str(source))
    collect_revision(db, dict(manuscript), session_id=None, source="test")
    return db, dict(db.one("SELECT * FROM manuscripts WHERE id = ?",
                           (manuscript["id"],)))


def queue(db: Database, mid: str) -> tuple[int, int, int]:
    concepts = [
        r for r in db.all(
            "SELECT metadata FROM concept_nodes WHERE manuscript_id = ? "
            "AND status != 'retired'", (mid,))
        if loads(r["metadata"], {}).get("origin") == "extracted"
    ]
    edges = db.one("SELECT count(*) AS n FROM concept_edges "
                   "WHERE manuscript_id = ? AND status = 'inferred'",
                   (mid,))["n"]
    props = db.one("SELECT count(*) AS n FROM knowledge_proposals "
                   "WHERE manuscript_id = ? AND state = 'open'", (mid,))["n"]
    return len(concepts), edges, props


def run(root: Path, name: str, llm: StubLLM) -> tuple[int, int, int]:
    db, manuscript = build(root / name)
    with contextlib.redirect_stderr(io.StringIO()):
        extract_concepts(db, manuscript, llm, files=["01-herdsman.md"])
    return queue(db, manuscript["id"])


def main() -> None:
    root = Path(tempfile.mkdtemp(prefix="authorlm-adj-test-"))
    try:
        print("flag off — the default path is untouched:")
        off = StubLLM(adjudicate=False)
        base = run(root, "off", off)
        check("no adjudication call is made",
              all(step == "extraction" for step, _s, _u in off.calls),
              str([c[0] for c in off.calls]))
        check("candidates reach the graph as before", base[0] >= 2, str(base))

        print("flag on, every verdict 'new' — plumbing only:")
        passthrough = StubLLM(adjudicate=True, verdicts={
            "concepts": [{"name": c["name"], "verdict": "new"}
                         for c in CANDIDATES["concepts"]],
            "links": [{**l, "verdict": "new"} for l in CANDIDATES["links"]]})
        same = run(root, "pass", passthrough)
        check("exactly one adjudication call is added",
              sum(1 for step, _s, _u in passthrough.calls
                  if step == "adjudication") == 1,
              str([c[0] for c in passthrough.calls]))
        check("the queue is identical to the flag-off run", same == base,
              f"{same} != {base}")

        print("flag on, mixed verdicts:")
        mixed = StubLLM(adjudicate=True, verdicts={
            "concepts": [
                {"name": "The Herdsman", "verdict": "new"},
                {"name": "Herdsman's Rod", "verdict": "subsumed",
                 "existing": "The Herdsman"},
                {"name": "The Flock", "verdict": "new"},
                {"name": "Passing Fancy", "verdict": "drop"},
            ],
            "links": [{**CANDIDATES["links"][0], "verdict": "drop"}]})
        db, manuscript = build(root / "mixed")
        with contextlib.redirect_stderr(io.StringIO()):
            summary = extract_concepts(db, manuscript, mixed,
                                       files=["01-herdsman.md"])
        mid = manuscript["id"]
        concepts, edges, _props = queue(db, mid)
        check("subsumed and dropped candidates never reach the graph",
              concepts == 2, str(concepts))
        check("a dropped relationship is not inferred", edges == 0, str(edges))
        check("the summary reports what was screened",
              summary["screened"] == 3, str(summary.get("screened")))
        row = db.one("SELECT * FROM evidence WHERE manuscript_id = ? AND "
                     "evidence_type = 'extraction_adjudication'", (mid,))
        check("the screening is logged as evidence, not as triage",
              row is not None and row["signal"] == "screened"
              and row["supports_belief"] is None, str(dict(row) if row else None))
        detail = loads(row["metadata"], {})
        check("the log names what was suppressed and why",
              any("Passing Fancy" in d for d in detail["dropped"])
              and any("Herdsman's Rod" in d for d in detail["subsumed"]),
              json.dumps(detail))

        print("flag on, an 'improves' verdict becomes a proposal:")
        db, manuscript = build(root / "improves")
        first = StubLLM(adjudicate=True, verdicts={
            "concepts": [{"name": c["name"], "verdict": "new"}
                         for c in CANDIDATES["concepts"]], "links": []})
        with contextlib.redirect_stderr(io.StringIO()):
            extract_concepts(db, manuscript, first, files=["01-herdsman.md"])
        again = StubLLM(adjudicate=True, verdicts={
            "concepts": [
                {"name": "The Herdsman", "verdict": "improves",
                 "existing": "The Herdsman",
                 "notes": "The one who tends the flock of the dead, and "
                          "drives it with a rod."},
                {"name": "Herdsman's Rod", "verdict": "drop"},
                {"name": "The Flock", "verdict": "drop"},
                {"name": "Passing Fancy", "verdict": "drop"},
            ], "links": []})
        with contextlib.redirect_stderr(io.StringIO()):
            extract_concepts(db, manuscript, again, files=["01-herdsman.md"],
                             full=True)
        proposal = db.one(
            "SELECT * FROM knowledge_proposals WHERE manuscript_id = ? "
            "AND kind = 'note_update' AND state = 'open'",
            (manuscript["id"],))
        check("the improvement is a proposal against settled knowledge",
              proposal is not None, "no note_update proposal was raised")
        payload = loads(proposal["payload"], {})
        check("the proposal carries the improved definition",
              "drives it with a rod" in payload["proposed_note"],
              json.dumps(payload))
        node = db.one("SELECT notes FROM concept_nodes WHERE manuscript_id = ? "
                      "AND name = 'The Herdsman'", (manuscript["id"],))
        check("settled knowledge is not rewritten by the machine",
              "drives it with a rod" not in (node["notes"] or ""),
              str(node["notes"]))

        print("retrieval is deterministic and free:")
        near = adjudication.neighbours(
            {"name": "Herdsman's Rod", "notes": "The rod he carries."},
            [{"name": "The Herdsman", "kind": "concept", "aliases": [],
              "notes": "The one who tends the flock of the dead."},
             {"name": "Unrelated Thing", "kind": "concept", "aliases": [],
              "notes": "Nothing to do with any of this."}])
        check("the nearest existing concept is retrieved, the far one is not",
              [n["name"] for n in near] == ["The Herdsman"],
              str([n["name"] for n in near]))
        check("a client without config is opted out",
              not adjudication.enabled(object()))
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print(f"\nall checks passed ({PASSED})")


if __name__ == "__main__":
    main()
