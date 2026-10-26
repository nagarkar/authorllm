"""Migration of the old pipeline's pending rows (docs/extraction-redesign.md
§5, phase 5 of §9).

Three verbs, each a report, nothing deleted:

  backup   `sqlite3` backup-API copy of the workspace database, WAL-safe,
           into the workspace's `backups/` as `pre-extraction-v2-<stamp>.db`
           (§5.1). The rollback is this file.
  migrate  the unconfirmed extracted nodes and the `inferred` edges go
           through gates 1-7 and the per-essay adjudicator against the
           CURRENT text, tagged `run` (§5.2): survivors stay pending with a
           fresh score and `metadata.extraction`; failures retire with
           `retired_from` set (so `revive_concept` still works) and an
           `extraction_adjudication` row `signal = migrated_out` naming the
           gate. Open proposals per §5.3: `note_update` and
           `variant_of_retired` → `superseded`; `revival` under §4.10 —
           below the bar superseded, above it superseded and re-asked once
           with spans; `alias` and `vanished` untouched.
  undo     the scripted inverse (§5.6): every write above carries the run
           id, so the migration is reversed row by row — nodes revived,
           edges restored, retypes undone, superseded proposals reopened,
           the proposals it raised superseded in turn. Evidence rows stay:
           they are the audit trail of both directions.

`plan` is pure (it reads, screens and — with a judge — adjudicates; it
writes nothing), `apply` writes the plan in one transaction, and
`--dry-run` is `plan` plus a report file.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from . import proposals
from .backup import backup_dir
from .concepts import retire_concept, revive_concept
from .db import Database, loads, now_iso
from .extraction import (_glossary_block, _note_update_blocked,
                         _precedent_block, _span_entry, _summary_block,
                         append_edge_evidence, candidate_label, decide,
                         gate_context, glossary_nodes, precedent_rows,
                         record_candidate, record_run, revival_due, screen)
from .extraction_calibration import _Versions, historical, relocate

DEFAULT_RUN = "migration-1"
SUPERSEDED_KINDS = ("note_update", "variant_of_retired")
KEPT_KINDS = ("alias", "vanished")


# ----------------------------------------------------------------- §5.1

def backup(db: Database) -> Path:
    """A WAL-safe copy of the live database in the workspace's `backups/`
    directory. Never skipped, never rotated: this is the rollback point
    the migration is measured against, not a daily snapshot."""
    bdir = backup_dir(db.path)
    bdir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    dest_path = bdir / f"pre-extraction-v2-{stamp}.db"
    db.conn.commit()
    dest = sqlite3.connect(str(dest_path))
    try:
        db.conn.backup(dest)
    finally:
        dest.close()
    return dest_path


# ------------------------------------------------------------- the plan

@dataclass
class Plan:
    run_id: str
    candidates: list[dict]                  # gate-ready, screened, judged
    proposals: list[dict]                   # {row, kind, action, due}
    model: str | None = None
    judged: bool = False
    failed: list[str] = field(default_factory=list)   # essays whose call failed
    absent: list[dict] = field(default_factory=list)  # confirmed nodes gone from the text

    @property
    def concepts(self) -> list[dict]:
        return [c for c in self.candidates if c["type"] == "concept"]

    @property
    def links(self) -> list[dict]:
        return [c for c in self.candidates if c["type"] == "link"]


def kept(c: dict) -> bool:
    """A candidate the migration keeps pending: it passed the gates and
    the adjudicator said `new` / `retype` (or no adjudicator ran)."""
    return c.get("signal") in (None, "new", "retype")


def pending(db: Database, manuscript: dict) -> dict:
    """The rows §5.2 migrates, in `extraction_calibration.historical`'s
    shape: unconfirmed live extracted nodes, and `inferred` edges."""
    hist = historical(db, manuscript)
    concepts = [h for h in hist["concepts"]
                if h["node"]["status"] != "retired"
                and not (loads(h["node"]["metadata"], {}) or {}).get("confirmed")]
    links = [h for h in hist["links"] if h["edge"]["status"] == "inferred"]
    return {"concepts": concepts, "links": links}


class _Latest(_Versions):
    """Every candidate relocated in the CURRENT text (the latest collected
    version), not the version it was created in."""

    def at(self, created_at: str) -> str | None:
        return self.rows[-1]["id"] if self.rows else None


def _leave_out(ctx, candidates: list[dict]) -> None:
    """The candidates under migration are not 'the graph' they are judged
    against: a pending node must not count as an existing concept, nor a
    pending edge as an existing relation (the leave-out rule of §6.1)."""
    own_nodes = {c["_hist"]["id"] for c in candidates if c["type"] == "concept"}
    own_edges = {c["_hist"]["id"] for c in candidates if c["type"] == "link"}
    ctx.known = {k: n for k, n in ctx.known.items() if n["id"] not in own_nodes}
    ctx.edges = {pair: [e for e in edges if e["id"] not in own_edges]
                 for pair, edges in ctx.edges.items()}
    ctx.edges = {pair: edges for pair, edges in ctx.edges.items() if edges}


def _essay_payload(db: Database, manuscript: dict, essay: str, text: str,
                   own: set[str], knob: dict):
    """The adjudicator's context for one essay: blocks G and P without the
    essay's own pending nodes, and the summary unit (block M's source)."""
    from .lenses import _title_of

    nodes, edges = glossary_nodes(db, manuscript, essay, text, exclude=own)
    prec = precedent_rows(db, manuscript, essay, text,
                          int(knob["precedents_nearest"]), exclude=own)
    _text, unit = _summary_block(db, manuscript, essay, text,
                                 knob["summaries"])
    return SimpleNamespace(file=essay, title=_title_of(text) or essay,
                           glossary=_glossary_block(nodes, edges),
                           precedents=_precedent_block(prec, essay),
                           summary_unit=unit)


def _proposal_plan(db: Database, manuscript: dict, files: dict[str, str]
                   ) -> list[dict]:
    """§5.3 over the open proposals: what happens to each."""
    mid = manuscript["id"]
    out = []
    for row in db.all("SELECT * FROM knowledge_proposals WHERE manuscript_id "
                      "= ? AND state = 'open' ORDER BY created_at", (mid,)):
        row = dict(row)
        if row["kind"] in KEPT_KINDS:
            out.append({"row": row, "kind": row["kind"], "action": "keep",
                        "due": None})
        elif row["kind"] in SUPERSEDED_KINDS:
            out.append({"row": row, "kind": row["kind"],
                        "action": "supersede", "due": None})
        elif row["kind"] == "revival":
            node = db.one("SELECT * FROM concept_nodes WHERE id = ?",
                          (row["target"],))
            due = (revival_due(db, manuscript, dict(node), files,
                               ignore_proposals={row["id"]})
                   if node is not None and node["status"] == "retired"
                   else None)
            out.append({"row": row, "kind": "revival",
                        "action": "re-ask" if due else "supersede",
                        "due": due})
        else:
            out.append({"row": row, "kind": row["kind"], "action": "keep",
                        "due": None})
    return out


def absent_confirmed(db: Database, manuscript: dict,
                     files: dict[str, str]) -> list[dict]:
    """Confirmed extracted concepts whose name and every alias occur
    nowhere in the current text (ruled 2026-10-25: on the author's record
    these are the paraphrases the old extractor coined, "Question about
    God's good graces", "Written word as iron bond"; the design's gate 1
    would never admit them now, and they are retired on the same terms as
    a pending row that fails it). Author-declared concepts are not
    touched: declaration is ratification."""
    from .concepts import concept_pattern, node_names

    out = []
    for row in db.all("SELECT * FROM concept_nodes WHERE manuscript_id = ? "
                      "AND status != 'retired'", (manuscript["id"],)):
        meta = loads(row["metadata"], {}) or {}
        if meta.get("origin") != "extracted" or not meta.get("confirmed"):
            continue
        pats = [concept_pattern(n) for n in node_names(row) if n]
        if any(p.search(text) for p in pats for text in files.values()):
            continue
        out.append(dict(row))
    return out


def plan(db: Database, manuscript: dict, knob: dict, *, judge_llm=None,
         run_id: str = DEFAULT_RUN) -> Plan:
    """Screen and (with a judge) adjudicate the pending rows against the
    current text; decide the open proposals. Pure: nothing is written."""
    from . import extraction_adjudicator as adj
    from .revisions import read_manuscript_files

    hist = pending(db, manuscript)
    versions = _Latest(db, manuscript["id"])
    files = (versions.files(versions.rows[-1]["id"]) if versions.rows
             else read_manuscript_files(Path(manuscript["path"])))
    ctx = gate_context(db, manuscript, files)
    candidates = relocate(hist, versions, ctx.known) if versions.rows else []
    _leave_out(ctx, candidates)
    screen(candidates, ctx, knob)
    from .extraction import eligible_files

    content = {n: files[n] for n in eligible_files(files)}
    out = Plan(run_id=run_id, candidates=candidates,
               proposals=_proposal_plan(db, manuscript, files),
               absent=absent_confirmed(db, manuscript, content))
    passed = [c for c in candidates if c["type"] != "alias"
              and c.get("signal") is None]
    if judge_llm is None or not passed:
        return out
    out.model = judge_llm.model
    out.judged = True
    by_essay: dict[str, list[dict]] = {}
    for c in passed:
        by_essay.setdefault(c["file"], []).append(c)
    for essay, mine in by_essay.items():
        text = files.get(essay, "")
        own = {c["_hist"]["id"] for c in mine if c["type"] == "concept"}
        payload = _essay_payload(db, manuscript, essay, text, own, knob)
        verdicts = adj.adjudicate(judge_llm, payload, mine,
                                  knob["proposable_relations"])
        if verdicts is None:
            out.failed.append(essay)
            continue
        decide(mine, verdicts, knob)
    return out


def estimate(p: Plan, knob: dict) -> dict:
    """The adjudication cost estimate for the plan's survivors, in
    `extraction_adjudicator.estimate_scope`'s shape."""
    from . import extraction_adjudicator as adj

    n = sum(1 for c in p.candidates if c["type"] != "alias"
            and c.get("signal") is None)
    essays = {c["file"] for c in p.candidates if c.get("signal") is None}
    system = len(adj.adjudicator_system(knob["proposable_relations"]))
    return {"essays": len(essays), "sections": 0, "candidates": n,
            "assumed": False, "extraction_in": 0, "extraction_out": 0,
            "adjudication_in": (system * len(essays)) // adj.CHARS_PER_TOKEN
            + adj.CANDIDATE_TOKENS_IN * n,
            "adjudication_out": adj.VERDICT_TOKENS_OUT * n}


# ------------------------------------------------------------ the writes

def _tag_retired(db: Database, table: str, row_id: str, run_id: str,
                 status: str | None = None) -> None:
    """`metadata.retired_from.by_run = run`: how `undo` finds its own."""
    row = db.one(f"SELECT metadata FROM {table} WHERE id = ?", (row_id,))
    meta = loads(row["metadata"], {}) or {}
    prior = dict(meta.get("retired_from") or {})
    if status is not None:
        prior.setdefault("status", status)
    prior["by_run"] = run_id
    meta["retired_from"] = prior
    db.update(table, row_id, {"metadata": json.dumps(meta)})


def _migrated_out(c: dict) -> None:
    """Re-signal a failed candidate for its evidence row: the gate or
    verdict that failed it stays in `gate` and `reason`."""
    original = c.get("signal")
    c["reason"] = f"{original}: {c.get('reason') or ''}".rstrip(": ")
    c["signal"] = "migrated_out"


def apply(db: Database, manuscript: dict, p: Plan) -> dict:
    """Write the plan, in one transaction. Returns the report."""
    mid = manuscript["id"]
    report = {"run": p.run_id, "model": p.model, "judged": p.judged,
              "concepts": {"kept": 0, "out": 0}, "links": {"kept": 0, "out": 0,
                                                            "retyped": 0},
              "proposals": {"superseded": 0, "re-asked": 0, "kept": 0,
                            "raised": 0},
              "note_updates": 0, "absent": 0, "failed": list(p.failed)}
    with db.transaction():
        for c in p.concepts:
            node = db.one("SELECT * FROM concept_nodes WHERE id = ?",
                          (c["_hist"]["id"],))
            if node is None or node["status"] == "retired":
                continue
            node = dict(node)
            if kept(c):
                meta = loads(node["metadata"], {}) or {}
                meta["extraction"] = {
                    "run": p.run_id, "file": c["file"], "section": c["section"],
                    "paragraphs": list(c["_cited"]), "span": c["span"],
                    "score": c.get("score"), "adjudicator": p.model,
                    "reason": c.get("reason") or ("passed gates 1-7; not "
                                                  "adjudicated")}
                db.update("concept_nodes", node["id"],
                          {"metadata": json.dumps(meta)})
                c["node_id"] = node["id"]
                if c.get("signal") is None:
                    c.update(signal="new", gate="gates",
                             reason=meta["extraction"]["reason"])
                report["concepts"]["kept"] += 1
            else:
                if c.get("signal") == "improves" and c.get("target_name"):
                    target = db.one(
                        "SELECT * FROM concept_nodes WHERE manuscript_id = ? "
                        "AND lower(name) = lower(?) AND status != 'retired'",
                        (mid, c["target_name"]))
                    improved = c.get("improved_notes") or ""
                    if target is not None and improved and not \
                            _note_update_blocked(target["notes"], improved):
                        made = proposals.create(
                            db, mid, "note_update", target["id"],
                            {"name": target["name"],
                             "current_note": target["notes"],
                             "proposed_note": improved,
                             "current_kind": target["kind"],
                             "proposed_kind": target["kind"],
                             "span": c["span"], "file": c["file"],
                             "paragraphs": list(c["_cited"])},
                            source=f"extraction/{p.run_id}")
                        if made:
                            c["proposal_id"] = made["id"]
                            report["note_updates"] += 1
                retire_concept(db, mid, node)
                _tag_retired(db, "concept_nodes", node["id"], p.run_id)
                c["node_id"] = node["id"]
                _migrated_out(c)
                report["concepts"]["out"] += 1
            record_candidate(db, mid, c, p.run_id, p.model)
        for c in p.links:
            edge = db.one("SELECT * FROM concept_edges WHERE id = ?",
                          (c["_hist"]["id"],))
            if edge is None:
                continue
            edge = dict(edge)
            c["edge_id"] = edge["id"]
            if edge["status"] != "inferred":
                # Retired as the collateral of an endpoint migrated out
                # above (`retire_concept` tagged it `by_node`).
                c.update(signal="migrated_out", gate="endpoint",
                         reason="its endpoint was migrated out")
                report["links"]["out"] += 1
            elif kept(c):
                emeta = loads(edge["metadata"], {}) or {}
                changes: dict = {}
                if c.get("retyped_from"):
                    emeta.setdefault("extraction", {})
                    emeta["extraction"]["migrated_from"] = {
                        "relation": edge["relation"],
                        "from_node": edge["from_node"],
                        "to_node": edge["to_node"]}
                    src = c["_ends"][0][0]
                    dst = c["_ends"][1][0]
                    if src == edge["to_node"] and dst == edge["from_node"]:
                        changes.update(from_node=dst, to_node=src)
                    changes["relation"] = c["relation"]
                    report["links"]["retyped"] += 1
                emeta["extraction"] = {
                    **(emeta.get("extraction") or {}),
                    "run": p.run_id, "score": c.get("score"),
                    "adjudicator": p.model,
                    "reason": c.get("reason") or ("passed gates 1-7; not "
                                                  "adjudicated")}
                changes["metadata"] = json.dumps(emeta)
                db.update("concept_edges", edge["id"], changes)
                append_edge_evidence(db, edge["id"], _span_entry(c, p.run_id))
                if c.get("signal") is None:
                    c.update(signal="new", gate="gates",
                             reason=emeta["extraction"]["reason"])
                report["links"]["kept"] += 1
            else:
                db.update("concept_edges", edge["id"], {"status": "retired"})
                _tag_retired(db, "concept_edges", edge["id"], p.run_id,
                             status="inferred")
                _migrated_out(c)
                report["links"]["out"] += 1
            record_candidate(db, mid, c, p.run_id, p.model)
        for node in p.absent:
            row = db.one("SELECT * FROM concept_nodes WHERE id = ?",
                         (node["id"],))
            if row is None or row["status"] == "retired":
                continue
            retire_concept(db, mid, dict(row))
            _tag_retired(db, "concept_nodes", row["id"], p.run_id)
            c = {"type": "concept", "name": row["name"], "kind": row["kind"],
                 "notes": row["notes"], "file": row["introduced_in"] or "?",
                 "section": None, "unit": "?", "span": "", "_cited": {},
                 "confidence": None, "why": "confirmed, absent",
                 "signal": "migrated_out", "gate": "absent",
                 "reason": "absent: a confirmed extracted concept whose name "
                           "and aliases no longer occur in the text",
                 "node_id": row["id"]}
            record_candidate(db, mid, c, p.run_id, None)
            report["absent"] += 1
        for item in p.proposals:
            row = item["row"]
            if item["action"] == "keep":
                report["proposals"]["kept"] += 1
                continue
            meta = loads(row["metadata"], {}) or {}
            meta["superseded_by"] = p.run_id
            db.update("knowledge_proposals", row["id"],
                      {"state": "superseded", "metadata": json.dumps(meta)})
            report["proposals"]["superseded"] += 1
            if item["action"] == "re-ask":
                node = db.one("SELECT * FROM concept_nodes WHERE id = ?",
                              (row["target"],))
                payload = loads(row["payload"], {}) or {}
                due = item["due"]
                made = proposals.create(
                    db, mid, "revival", row["target"],
                    {"name": node["name"], "kind": payload.get("kind")
                     or node["kind"], "notes": payload.get("notes")
                     or node["notes"], "spans": due["contexts"],
                     "recurrence": due["recurrence"],
                     "re_asked_from": row["id"]},
                    source=f"extraction/{p.run_id}", firewall=False)
                report["proposals"]["re-asked"] += 1
                if made:
                    report["proposals"]["raised"] += 1
        record_run(db, manuscript, {
            "id": p.run_id, "started": now_iso(), "kind": "migrate",
            "model": None, "adjudicator": p.model,
            "units": len(p.candidates), "cost": None,
            "report": {k: v for k, v in report.items()
                       if k in ("concepts", "links", "proposals",
                                "note_updates", "absent")},
            "incomplete": bool(p.failed)})
    return report


# -------------------------------------------------------------- the undo

def undo(db: Database, manuscript: dict, run_id: str) -> dict:
    """§5.6: reverse every write tagged `run_id`. Evidence rows stay."""
    mid = manuscript["id"]
    report = {"run": run_id, "nodes_revived": 0, "edges_restored": 0,
              "retypes_undone": 0, "tags_cleared": 0,
              "proposals_reopened": 0, "proposals_withdrawn": 0}
    with db.transaction():
        for row in db.all("SELECT * FROM concept_nodes WHERE manuscript_id = ?",
                          (mid,)):
            meta = loads(row["metadata"], {}) or {}
            if (meta.get("retired_from") or {}).get("by_run") == run_id \
                    and row["status"] == "retired":
                revive_concept(db, mid, dict(row))
                report["nodes_revived"] += 1
            elif (meta.get("extraction") or {}).get("run") == run_id:
                meta.pop("extraction", None)
                db.update("concept_nodes", row["id"],
                          {"metadata": json.dumps(meta)})
                report["tags_cleared"] += 1
        for row in db.all("SELECT * FROM concept_edges WHERE manuscript_id = ?",
                          (mid,)):
            meta = loads(row["metadata"], {}) or {}
            prior = meta.get("retired_from") or {}
            if prior.get("by_run") == run_id and row["status"] == "retired":
                meta.pop("retired_from", None)
                db.update("concept_edges", row["id"],
                          {"status": prior.get("status") or "inferred",
                           "metadata": json.dumps(meta)})
                report["edges_restored"] += 1
                continue
            ext = meta.get("extraction") or {}
            if ext.get("run") != run_id:
                continue
            changes: dict = {}
            undone = ext.get("migrated_from")
            if undone:
                changes.update(relation=undone["relation"],
                               from_node=undone["from_node"],
                               to_node=undone["to_node"])
                report["retypes_undone"] += 1
            meta.pop("extraction", None)
            evidence = [e for e in (loads(row["evidence"], []) or [])
                        if isinstance(e, dict) and e.get("run") != run_id]
            changes.update(metadata=json.dumps(meta),
                           evidence=json.dumps(evidence),
                           support=len(evidence))
            db.update("concept_edges", row["id"], changes)
            report["tags_cleared"] += 1
        for row in db.all("SELECT * FROM knowledge_proposals WHERE "
                          "manuscript_id = ?", (mid,)):
            meta = loads(row["metadata"], {}) or {}
            if meta.get("superseded_by") == run_id \
                    and row["state"] == "superseded":
                meta.pop("superseded_by", None)
                db.update("knowledge_proposals", row["id"],
                          {"state": "open", "metadata": json.dumps(meta)})
                report["proposals_reopened"] += 1
            elif row["source"] == f"extraction/{run_id}" \
                    and row["state"] == "open":
                meta["undone"] = run_id
                db.update("knowledge_proposals", row["id"],
                          {"state": "superseded", "metadata": json.dumps(meta)})
                report["proposals_withdrawn"] += 1
        record_run(db, manuscript, {
            "id": f"{run_id}-undo", "started": now_iso(), "kind": "migrate-undo",
            "model": None, "adjudicator": None, "units": 0, "cost": None,
            "report": {k: v for k, v in report.items() if k != "run"},
            "incomplete": False})
    return report


# ------------------------------------------------------------ the report

def render_plan(p: Plan) -> str:
    """The migration report (dry-run or applied): every candidate with its
    fate, then the proposals."""
    lines = [f"migration run {p.run_id}"
             + (f", adjudicated by {p.model}" if p.judged else
                ", gates only (no adjudicator)")]
    for kind, label in (("concept", "CONCEPTS"), ("link", "RELATIONS")):
        mine = [c for c in p.candidates if c["type"] == kind]
        keep = [c for c in mine if kept(c)]
        lines.append(f"\n{label}: {len(mine)} pending — {len(keep)} kept, "
                     f"{len(mine) - len(keep)} migrated out")
        for c in mine:
            fate = ("kept" if kept(c) else
                    f"out ({c.get('signal')}, gate {c.get('gate')})")
            where = f"{c['file']}" + (f" §{c['unit']}" if c.get("unit") else "")
            score = f" {c['score']:.2f}" if c.get("score") is not None else ""
            lines.append(f"  {fate:40s} {candidate_label(c)}{score}  [{where}]")
            if c.get("reason"):
                lines.append(f"      {c['reason']}")
    lines.append(f"\nABSENT CONFIRMED CONCEPTS: {len(p.absent)} retired — "
                 "name and aliases occur nowhere in the text")
    for node in p.absent:
        lines.append(f"  {node['name']} ({node['kind']})  "
                     f"[{node['introduced_in'] or '?'}]")
    counts: dict[tuple[str, str], int] = {}
    for item in p.proposals:
        key = (item["kind"], item["action"])
        counts[key] = counts.get(key, 0) + 1
    lines.append(f"\nPROPOSALS: {len(p.proposals)} open")
    for (kind, action), n in sorted(counts.items()):
        lines.append(f"  {kind:20s} {action:10s} {n}")
    if p.failed:
        lines.append("\nadjudicator call FAILED for: " + ", ".join(p.failed)
                     + " — their survivors are kept unjudged")
    return "\n".join(lines)


def render_report(report: dict) -> str:
    c, l, pr = report["concepts"], report["links"], report["proposals"]
    return (f"migration {report['run']} applied"
            + (f" (adjudicated by {report['model']})" if report["judged"]
               else " (gates only)")
            + f": concepts kept {c['kept']}, out {c['out']}; relations kept "
            f"{l['kept']} ({l['retyped']} retyped), out {l['out']}; "
            f"note updates raised {report['note_updates']}; absent confirmed "
            f"concepts retired {report['absent']}; proposals "
            f"superseded {pr['superseded']}, re-asked {pr['re-asked']} "
            f"({pr['raised']} raised), kept {pr['kept']}."
            + (" Adjudicator failed for: " + ", ".join(report["failed"])
               if report["failed"] else ""))


def render_undo(report: dict) -> str:
    return (f"migration {report['run']} undone: {report['nodes_revived']} "
            f"node(s) revived, {report['edges_restored']} edge(s) restored, "
            f"{report['retypes_undone']} retype(s) undone, "
            f"{report['tags_cleared']} tag(s) cleared, "
            f"{report['proposals_reopened']} proposal(s) reopened, "
            f"{report['proposals_withdrawn']} raised proposal(s) withdrawn. "
            "Evidence rows stay as the audit trail.")
