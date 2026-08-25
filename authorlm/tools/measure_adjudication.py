"""Measure the triage burden with and without step-two adjudication.

Zero LLM calls. Everything the model would have said is replayed from the
author's own record, so the numbers below are measurements of this
codebase running on this manuscript, not estimates.

    python3 tools/measure_adjudication.py [path/to/authorlm.db]

The default source is the read-only pre-campaign snapshot. The snapshot is
opened read-only and copied nowhere; every run builds a throwaway
workspace in a temp directory.

WHAT IS REPLAYED
  * The manuscript: the real text of the latest collected version.
  * Step one: the 914 concepts and the relationships the extractor really
    produced against this manuscript, replayed per file — so the "before"
    number is the pipeline's own output, not a guess about it.
  * Step two: three adjudicator policies, run over the same step-one
    output through the same code path:
      passthrough — every candidate ruled "new". Isolates the plumbing:
                    the result must equal the flag-off run exactly.
      author      — the verdict the AUTHOR actually gave (a candidate they
                    later retired is "drop"). A perfect adjudicator; the
                    CEILING of the design, not a prediction.
      precedent   — no model judgment at all: drop only when the candidate
                    is within `loop.NEAR_DUPLICATE` of a name the author
                    already rejected. The FLOOR — what the deterministic
                    half alone is worth.
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from authorlm import api, loop  # noqa: E402
from authorlm.revisions import collect_revision  # noqa: E402
from authorlm.concepts import concept_pattern  # noqa: E402
from authorlm.db import Database, loads  # noqa: E402
from authorlm.extraction import extract_concepts  # noqa: E402

SNAPSHOT = Path(
    "/private/tmp/claude-501/-Users-nagarkar-workspace-repos-authorllm/"
    "5ec5baba-db88-43c2-aca2-cbde204391c6/scratchpad/dbbackup/"
    "authorlm.db.pre-campaign")


# ----------------------------------------------------------- the real corpus


def read_snapshot(path: Path) -> dict:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    ms = conn.execute(
        "SELECT id, name FROM manuscripts ORDER BY ("
        "SELECT count(*) FROM concept_nodes WHERE manuscript_id = manuscripts.id"
        ") DESC LIMIT 1").fetchone()
    mid = ms["id"]
    version = conn.execute(
        "SELECT files FROM manuscript_versions WHERE manuscript_id = ? "
        "ORDER BY version_no DESC LIMIT 1", (mid,)).fetchone()
    files = json.loads(version["files"])
    nodes = []
    for row in conn.execute(
        "SELECT name, kind, notes, status, metadata FROM concept_nodes "
        "WHERE manuscript_id = ? ORDER BY created_at", (mid,)
    ):
        meta = json.loads(row["metadata"] or "{}")
        if meta.get("origin") != "extracted":
            continue
        nodes.append({"name": row["name"], "kind": row["kind"],
                      "notes": row["notes"] or "",
                      "kept": row["status"] != "retired"})
    links = []
    for row in conn.execute(
        "SELECT f.name AS a, e.relation AS relation, t.name AS b, e.status "
        "FROM concept_edges e "
        "JOIN concept_nodes f ON f.id = e.from_node "
        "JOIN concept_nodes t ON t.id = e.to_node "
        "WHERE e.manuscript_id = ? AND e.status IN "
        "('inferred', 'rejected', 'declared') ORDER BY e.created_at", (mid,)
    ):
        links.append({"from": row["a"], "relation": row["relation"],
                      "to": row["b"], "kept": row["status"] != "rejected"})
    conn.close()
    return {"name": ms["name"], "files": files, "nodes": nodes, "links": links}


def attribute(corpus: dict) -> tuple[dict, dict]:
    """Which file each candidate is replayed against: the first file in
    reading order whose text mentions it (the file the extractor would have
    been reading when it proposed the candidate)."""
    files = corpus["files"]
    order = sorted(n for n in files if n.endswith(".md"))
    by_file: dict[str, list] = {n: [] for n in order}
    link_by_file: dict[str, list] = {n: [] for n in order}
    for node in corpus["nodes"]:
        pattern = concept_pattern(node["name"])
        home = next((n for n in order if pattern.search(files[n])), order[-1])
        by_file[home].append(node)
    for link in corpus["links"]:
        a, b = concept_pattern(link["from"]), concept_pattern(link["to"])
        home = next((n for n in order
                     if a.search(files[n]) and b.search(files[n])), None)
        if home:
            link_by_file[home].append(link)
    return by_file, link_by_file


# --------------------------------------------------------------- the replay


class ReplayLLM:
    """Step one is replayed from the record; step two is a named policy.
    Never touches a network."""

    enabled = True
    extraction_max_chars = 24000

    def __init__(self, by_file, link_by_file, config, policy):
        self.by_file = by_file
        self.link_by_file = link_by_file
        self.config = config
        self.policy = policy
        self.served: set[str] = set()
        self.rejected_so_far: list[str] = []
        self.extraction_calls = 0
        self.adjudication_calls = 0
        self.extraction_chars = 0
        self.adjudication_chars = 0

    def stats_line(self):
        return None

    def complete_json(self, system, user, thinking_budget=None):
        if "ADJUDICATOR" in system:
            self.adjudication_calls += 1
            self.adjudication_chars += len(system) + len(user)
            return self._adjudicate(user)
        self.extraction_calls += 1
        self.extraction_chars += len(system) + len(user)
        name = self._payload_file(user)
        if name is None or name in self.served:
            return {"concepts": [], "links": [], "aliases": []}
        self.served.add(name)
        concepts = [{"name": n["name"], "kind": n["kind"], "notes": n["notes"]}
                    for n in self.by_file.get(name, [])]
        links = [{"from": l["from"], "relation": l["relation"], "to": l["to"]}
                 for l in self.link_by_file.get(name, [])]
        return {"concepts": concepts, "links": links, "aliases": []}

    @staticmethod
    def _payload_file(user: str) -> str | None:
        for line in user.split("\n"):
            if line.startswith("=== ") and line.endswith(" ==="):
                return line[4:-4]
        return None

    # -- the three step-two policies ---------------------------------------

    def _adjudicate(self, user: str) -> dict:
        verdicts = {"concepts": [], "links": []}
        for line in user.split("\n"):
            stripped = line.strip()
            if not stripped.startswith("- candidate: "):
                continue
            label = stripped[len("- candidate: "):]
            if "—" in label and "→" in label:
                verdicts["links"].append(self._link_verdict(label))
            else:
                verdicts["concepts"].append(self._concept_verdict(label))
        return verdicts

    def _concept_verdict(self, label: str) -> dict:
        name = label.rsplit(" (", 1)[0]
        truth = self._truth(name)
        if self.policy == "passthrough":
            return {"name": name, "verdict": "new"}
        if self.policy == "author":
            return {"name": name,
                    "verdict": "new" if truth is not False else "drop"}
        # precedent: deterministic only, zero model judgment
        hit = any(loop.similarity(name, prior) >= loop.NEAR_DUPLICATE
                  for prior in self.rejected_so_far)
        if truth is False:
            self.rejected_so_far.append(name)
        return {"name": name, "verdict": "drop" if hit else "new"}

    def _link_verdict(self, label: str) -> dict:
        head, _, to = label.partition("→")
        src, _, relation = head.partition("—")
        src, relation, to = src.strip(), relation.strip(), to.strip()
        verdict = "new"
        if self.policy == "author":
            for link in self.link_truth:
                if (link["from"] == src and link["relation"] == relation
                        and link["to"] == to and not link["kept"]):
                    verdict = "drop"
                    break
        return {"from": src, "relation": relation, "to": to,
                "verdict": verdict}

    def _truth(self, name: str):
        return self.node_truth.get(name.lower())


# ------------------------------------------------------------------ the runs


def run(corpus: dict, by_file, link_by_file, adjudicate: bool,
        policy: str) -> dict:
    root = Path(tempfile.mkdtemp(prefix="authorlm-measure-"))
    try:
        source = root / "manuscript"
        source.mkdir()
        for name, text in corpus["files"].items():
            (source / name).write_text(text, encoding="utf-8")
        db = Database(root / "authorlm.db")
        manuscript = api.register_manuscript(db, corpus["name"], str(source))
        collect_revision(db, dict(manuscript), session_id=None,
                         source="measure")
        manuscript = dict(db.one("SELECT * FROM manuscripts WHERE id = ?",
                                 (manuscript["id"],)))
        config = {"extraction": {"adjudicate": adjudicate}}
        llm = ReplayLLM(by_file, link_by_file, config, policy)
        llm.node_truth = {n["name"].lower(): n["kept"] for n in corpus["nodes"]}
        llm.link_truth = corpus["links"]
        for name in sorted(n for n in corpus["files"] if n.endswith(".md")):
            with contextlib.redirect_stderr(io.StringIO()):
                extract_concepts(db, manuscript, llm, files=[name])
        mid = manuscript["id"]
        concepts = [
            r for r in db.all(
                "SELECT metadata FROM concept_nodes WHERE manuscript_id = ? "
                "AND status != 'retired'", (mid,))
            if loads(r["metadata"], {}).get("origin") == "extracted"
            and not loads(r["metadata"], {}).get("confirmed")
        ]
        edges = db.one(
            "SELECT count(*) AS n FROM concept_edges WHERE manuscript_id = ? "
            "AND status = 'inferred'", (mid,))["n"]
        proposals = db.one(
            "SELECT count(*) AS n FROM knowledge_proposals "
            "WHERE manuscript_id = ? AND state = 'open'", (mid,))["n"]
        return {"concepts": len(concepts), "edges": edges,
                "proposals": proposals,
                "queue": len(concepts) + edges + proposals,
                "extraction_calls": llm.extraction_calls,
                "adjudication_calls": llm.adjudication_calls,
                "extraction_chars": llm.extraction_chars,
                "adjudication_chars": llm.adjudication_chars}
    finally:
        shutil.rmtree(root, ignore_errors=True)


def retrieval_coverage(corpus: dict) -> dict:
    """What the deterministic half actually hands the adjudicator, measured
    over the real candidates in the order they were created."""
    from authorlm import adjudication

    live: list[dict] = []
    rejected: list[str] = []
    with_neighbour = with_precedent = 0
    rejected_total = 0
    for node in corpus["nodes"]:
        candidate = {"name": node["name"], "notes": node["notes"]}
        if not node["kept"]:
            rejected_total += 1
            if adjudication.neighbours(candidate, live):
                with_neighbour += 1
            if adjudication._rank(node["name"], [(r, r) for r in rejected],
                                  adjudication.MAX_PRECEDENTS):
                with_precedent += 1
        if node["kept"]:
            live.append({"name": node["name"], "kind": node["kind"],
                         "notes": node["notes"], "aliases": []})
        else:
            rejected.append(node["name"])
    return {"rejected": rejected_total, "with_neighbour": with_neighbour,
            "with_precedent": with_precedent}


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else SNAPSHOT
    if not path.exists():
        sys.exit(f"no such database: {path}")
    corpus = read_snapshot(path)
    by_file, link_by_file = attribute(corpus)
    print(f"corpus: {corpus['name']} — {len(corpus['files'])} files, "
          f"{sum(len(t) for t in corpus['files'].values()):,} chars; "
          f"{len(corpus['nodes'])} extracted concepts "
          f"({sum(1 for n in corpus['nodes'] if not n['kept'])} the author "
          f"later rejected), {len(corpus['links'])} relationships "
          f"({sum(1 for l in corpus['links'] if not l['kept'])} rejected)\n")

    runs = [
        ("BEFORE  flag off (today's pipeline)", False, "passthrough"),
        ("AFTER   flag on, adjudicator passes everything", True, "passthrough"),
        ("AFTER   flag on, deterministic precedent only (FLOOR)",
         True, "precedent"),
        ("AFTER   flag on, author's own verdicts (CEILING)", True, "author"),
    ]
    baseline = None
    print(f"{'run':<52} {'concepts':>9} {'edges':>6} {'props':>6} "
          f"{'QUEUE':>7} {'calls':>6}")
    for label, flag, policy in runs:
        out = run(corpus, by_file, link_by_file, flag, policy)
        if baseline is None:
            baseline = out["queue"]
        delta = "" if out["queue"] == baseline else \
            f"  ({100 * (baseline - out['queue']) / baseline:.0f}% fewer)"
        calls = out["extraction_calls"] + out["adjudication_calls"]
        print(f"{label:<52} {out['concepts']:>9} {out['edges']:>6} "
              f"{out['proposals']:>6} {out['queue']:>7} {calls:>6}{delta}")
        if flag:
            print(f"{'':<52} extraction {out['extraction_calls']} call(s) / "
                  f"{out['extraction_chars']:,} chars; adjudication "
                  f"{out['adjudication_calls']} call(s) / "
                  f"{out['adjudication_chars']:,} chars")

    cover = retrieval_coverage(corpus)
    print(f"\ndeterministic retrieval, over the {cover['rejected']} candidates "
          f"the author rejected:\n"
          f"  {cover['with_neighbour']} were shown an existing concept close "
          f"enough to be a subsumption candidate\n"
          f"  {cover['with_precedent']} were shown a name the author had "
          f"already rejected")


if __name__ == "__main__":
    main()
