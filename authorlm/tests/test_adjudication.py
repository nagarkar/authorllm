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
from authorlm import concepts as concepts_mod  # noqa: E402
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


class NamedLLM(StubLLM):
    """Like StubLLM, but step one returns `candidates` (an explicit payload)
    instead of the fixed CANDIDATES — used by the X7-11 regression checks
    below to control the exact spelling/casing the 'model' proposes."""

    def __init__(self, adjudicate: bool, candidates: dict,
                 verdicts: dict | None = None):
        super().__init__(adjudicate, verdicts)
        self.candidates = candidates

    def complete_json(self, system, user, thinking_budget=None):
        step = "adjudication" if "ADJUDICATOR" in system else "extraction"
        self.calls.append((step, system, user))
        if step == "adjudication":
            return self.verdicts
        return json.loads(json.dumps(self.candidates))


# X7-11: SQLite's built-in lower() is ASCII-only; Python's str.lower() is
# not. A Sanskrit term is exactly where they used to disagree.
SANSKRIT_TEXT = (
    "# Śūnyatā\n\n"
    "Śūnyatā names the absence of any fixed, independent nature in "
    "things.\n\n"
    "## First reflection\n\n"
    "Śūnyatā is not mere absence; it is the ground of dependent "
    "origination.\n\n"
    "## Second reflection\n\n"
    "To sit with Śūnyatā is to release the grasping after a fixed "
    "essence.\n"
)

SANSKRIT_CANDIDATES = {
    "concepts": [{"name": "Śūnyatā", "kind": "concept",
                 "notes": "The absence of inherent nature."}],
    "links": [], "aliases": [],
}

# Same concept, proposed again in a different case — this is the shape of
# mismatch that used to defeat the SQL-side `lower(name) = lower(?)` finds
# (adjudication.py:379, extraction.py:709/734) even though the Python-side
# retrieval that led to them already knew it was the same concept.
SANSKRIT_CANDIDATES_LOWER = {
    "concepts": [{"name": "śūnyatā", "kind": "concept",
                 "notes": "The absence of inherent nature."}],
    "links": [], "aliases": [],
}


def build(root: Path, filename: str = "01-herdsman.md",
          text: str = TEXT) -> tuple[Database, dict]:
    source = root / "manuscript"
    source.mkdir(parents=True)
    (source / filename).write_text(text, encoding="utf-8")
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

        print("pass-2 payload — located quotes, not the whole source text "
              "(TrackA/1):")
        probe_candidates = {
            "concepts": [
                {"name": "Passing Fancy", "kind": "concept", "notes": "A whim."},
                {"name": "Nonexistent Ghost Concept", "kind": "concept",
                 "notes": "Does not occur anywhere in the manuscript."},
            ],
            "links": [{**CANDIDATES["links"][0]}],
            "aliases": [],
        }

        class PayloadProbeLLM(StubLLM):
            def complete_json(self, system, user, thinking_budget=None):
                step = "adjudication" if "ADJUDICATOR" in system else "extraction"
                self.calls.append((step, system, user))
                if step == "adjudication":
                    return {
                        "concepts": [{"name": c["name"], "verdict": "new"}
                                    for c in probe_candidates["concepts"]],
                        "links": [{**l, "verdict": "new"}
                                 for l in probe_candidates["links"]]}
                return json.loads(json.dumps(probe_candidates))

        probe = PayloadProbeLLM(adjudicate=True)
        db, manuscript = build(root / "payload-probe")
        with contextlib.redirect_stderr(io.StringIO()):
            extract_concepts(db, manuscript, probe, files=["01-herdsman.md"])
        adjudication_payloads = [u for step, _s, u in probe.calls
                                 if step == "adjudication"]
        check("exactly one adjudication call was made",
              len(adjudication_payloads) == 1, str(len(adjudication_payloads)))
        payload = adjudication_payloads[0]

        check("the whole source text is not re-sent verbatim",
              TEXT not in payload, payload)
        check("the old full-text marker is gone",
              "THE TEXT THE CANDIDATES CAME FROM" not in payload, payload)
        check("a locatable candidate is shown its sentence in context",
              "A passing fancy is not a thing the Herdsman heeds" in payload,
              payload)
        check("the section heading travels with the located quote",
              "## The rod" in payload, payload)
        ghost_idx = payload.find("Nonexistent Ghost Concept")
        check("an unlocatable candidate is flagged, not silently omitted",
              ghost_idx != -1
              and "UNLOCATABLE" in payload[ghost_idx:ghost_idx + 250],
              payload)
        check("an edge candidate is shown context for both endpoints",
              "located, The Herdsman:" in payload
              and "located, The Flock:" in payload, payload)

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

        print("X7-11: non-ASCII names resolve to ONE node regardless of "
              "case:")
        db, manuscript = build(root / "unicode-lookup", "01-sunyata.md",
                               SANSKRIT_TEXT)
        mid = manuscript["id"]
        added = concepts_mod.add_concept(db, mid, "Śūnyatā")
        via_lower = concepts_mod.get_concept(db, mid, "śūnyatā")
        via_upper = concepts_mod.get_concept(db, mid, "ŚŪNYATĀ")
        check("a differently-cased non-ASCII lookup resolves to the same "
              "node the mixed-case name created",
              via_lower is not None and via_upper is not None
              and via_lower["id"] == via_upper["id"] == added["id"],
              f"{added}, {via_lower}, {via_upper}")
        rows = db.all(
            "SELECT id FROM concept_nodes WHERE manuscript_id = ? "
            "AND lower(name) = lower(?)", (mid, "ŚŪNYATĀ"))
        check("exactly one row answers to the name, case aside",
              len(rows) == 1, str([dict(r) for r in rows]))

        print("X7-11: ASCII names resolve exactly as they did before "
              "(unchanged behaviour):")
        dharma = concepts_mod.add_concept(db, mid, "Dharma")
        ascii_lower = concepts_mod.get_concept(db, mid, "dharma")
        ascii_upper = concepts_mod.get_concept(db, mid, "DHARMA")
        check("an ASCII lookup still resolves case-insensitively",
              ascii_lower is not None and ascii_upper is not None
              and ascii_lower["id"] == ascii_upper["id"] == dharma["id"],
              f"{dharma}, {ascii_lower}, {ascii_upper}")
        ascii_rows = db.all(
            "SELECT id FROM concept_nodes WHERE manuscript_id = ? "
            "AND lower(name) = lower(?)", (mid, "DHARMA"))
        check("exactly one row answers to the ASCII name, case aside",
              len(ascii_rows) == 1, str([dict(r) for r in ascii_rows]))

        print("X7-11: a differently-cased 'improves' verdict resolves to "
              "the existing node instead of forking one (adjudication.py):")
        db, manuscript = build(root / "unicode-improves", "01-sunyata.md",
                               SANSKRIT_TEXT)
        mid = manuscript["id"]
        first = NamedLLM(adjudicate=True, candidates=SANSKRIT_CANDIDATES,
                         verdicts={
                             "concepts": [{"name": "Śūnyatā",
                                          "verdict": "new"}],
                             "links": []})
        with contextlib.redirect_stderr(io.StringIO()):
            extract_concepts(db, manuscript, first, files=["01-sunyata.md"])
        created = db.one("SELECT * FROM concept_nodes WHERE "
                         "manuscript_id = ? AND status != 'retired'", (mid,))
        check("setup: the Sanskrit concept was created",
              created is not None and created["name"] == "Śūnyatā",
              str(dict(created) if created else None))

        again = NamedLLM(adjudicate=True, candidates=SANSKRIT_CANDIDATES,
                         verdicts={
                             "concepts": [
                                 {"name": "Śūnyatā", "verdict": "improves",
                                  "existing": "śūnyatā",  # deliberately
                                  # different case than the stored name
                                  "notes": "The absence of inherent "
                                           "nature — the ground of "
                                           "dependent origination."},
                             ],
                             "links": []})
        with contextlib.redirect_stderr(io.StringIO()):
            extract_concepts(db, manuscript, again,
                             files=["01-sunyata.md"], full=True)
        nodes_after = db.all(
            "SELECT * FROM concept_nodes WHERE manuscript_id = ? "
            "AND status != 'retired'", (mid,))
        check("no duplicate node was forked by the differently-cased "
              "'improves' resolution",
              len(nodes_after) == 1, str([dict(n) for n in nodes_after]))
        proposal = db.one(
            "SELECT * FROM knowledge_proposals WHERE manuscript_id = ? "
            "AND kind = 'note_update' AND state = 'open'", (mid,))
        check("the SQL resolve at adjudication.py:379 found the row "
              "despite the case mismatch, so 'improves' became a "
              "note_update proposal instead of silently dropping",
              proposal is not None, "no note_update proposal was raised")

        print("X7-11: a retired non-ASCII name is correctly banned even "
              "when it recurs in a different case (extraction.py):")
        db, manuscript = build(root / "unicode-retire", "01-sunyata.md",
                               SANSKRIT_TEXT)
        mid = manuscript["id"]
        with contextlib.redirect_stderr(io.StringIO()):
            extract_concepts(
                db, manuscript,
                NamedLLM(adjudicate=False, candidates=SANSKRIT_CANDIDATES),
                files=["01-sunyata.md"])
        node = db.one("SELECT * FROM concept_nodes WHERE "
                      "manuscript_id = ? AND status != 'retired'", (mid,))
        check("setup: the Sanskrit concept exists before retiring",
              node is not None, str(node))
        db.update("concept_nodes", node["id"], {"status": "retired"})

        # Re-extracting: the model proposes the same concept again, but
        # spelled all-lowercase — the retired-name ban (extraction.py:
        # 709/734) has to find the retired row via the same SQL lookup
        # this fix repairs, or the retired name slips back in as a live
        # duplicate instead of raising the revival proposal.
        with contextlib.redirect_stderr(io.StringIO()):
            extract_concepts(
                db, manuscript,
                NamedLLM(adjudicate=False,
                        candidates=SANSKRIT_CANDIDATES_LOWER),
                files=["01-sunyata.md"], full=True)
        live_after = db.all(
            "SELECT * FROM concept_nodes WHERE manuscript_id = ? "
            "AND status != 'retired'", (mid,))
        check("the retired name did not slip back in as a new live node",
              len(live_after) == 0, str([dict(n) for n in live_after]))
        revival = db.one(
            "SELECT * FROM knowledge_proposals WHERE manuscript_id = ? "
            "AND kind = 'revival' AND state = 'open'", (mid,))
        check("the recurrence instead raised the revival proposal the "
              "design promises",
              revival is not None, "no revival proposal was raised")
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print(f"\nall checks passed ({PASSED})")


if __name__ == "__main__":
    main()
