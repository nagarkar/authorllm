"""The migration verbs (docs/extraction-redesign.md §5, §9 phase 5):
backup, migrate (plan, apply, dry-run), undo as a round trip.

Standalone, hermetic, zero LLM calls:  python3 tests/test_extraction_migration.py
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _offline_prices  # noqa: E402,F401

from authorlm import extraction, proposals  # noqa: E402
from authorlm import extraction_migration as mig  # noqa: E402
from authorlm.concepts import link_concepts, retire_concept  # noqa: E402
from authorlm.db import loads  # noqa: E402
from authorlm.revisions import collect_revision  # noqa: E402

import _extraction_fixture as fx  # noqa: E402

PASSED = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASSED
    if not ok:
        raise AssertionError(f"FAILED: {label}\n{detail}")
    PASSED += 1
    print(f"  ok: {label}")


class Judge:
    """A verdict per candidate label, matched off the CANDIDATES block."""

    enabled = True

    def __init__(self, verdicts: dict[str, dict], model="stub/judge"):
        self.model, self.verdicts = model, verdicts
        self.input_tokens = self.output_tokens = 0
        self.cache_read_tokens = self.cache_write_tokens = 0
        self.calls = 0

    def stats_line(self):
        return None

    def complete_json(self, system, user, thinking_budget=None):
        self.calls += 1
        out = []
        for line in user.splitlines():
            m = re.match(r"\[([cl]\d+)\] (?:CONCEPT|RELATION) (.*?) — score",
                         line)
            if m and m.group(2) in self.verdicts:
                out.append({"id": m.group(1), **self.verdicts[m.group(2)]})
        return {"verdicts": out}


EDGE = {row["n"]: row for row in fx.EDGES}


def node(db, name):
    return dict(db.one("SELECT * FROM concept_nodes WHERE name = ?", (name,)))


def edge(db, src, rel, dst):
    return db.one(
        "SELECT e.* FROM concept_edges e JOIN concept_nodes a ON a.id = "
        "e.from_node JOIN concept_nodes b ON b.id = e.to_node WHERE a.name "
        "= ? AND e.relation = ? AND b.name = ?", (src, rel, dst))


def inferred(db, ms, n: int):
    row = EDGE[n]
    return link_concepts(db, ms["id"], row["src"], row["rel"], row["dst"],
                         status="inferred", source_id=db.source("system"))


def proposal(db, ms, kind, target, payload):
    return proposals.create(db, ms["id"], kind, target, payload,
                            firewall=False)


def fixture(root: Path, *, workspace: bool = False):
    """The §2.4 graph with: two pending concepts (one grounded, one a
    ghost), five inferred edges (#4, #5, #16, #1, and one on the ghost),
    and one open proposal of every kind — including a revival that the
    text written after the retirement makes due."""
    db, ms = fx.build(root, workspace=workspace)
    mid = ms["id"]
    for name in ("ideological population",):
        row = node(db, name)
        meta = loads(row["metadata"], {})
        meta["confirmed"] = False
        db.update("concept_nodes", row["id"], {"metadata": json.dumps(meta)})
    ghost = fx.add_node(db, ms, "Ghost Term", confirmed=False)
    # A CONFIRMED extracted concept the text no longer carries (ruled
    # 2026-10-25: retired by the migration, tagged, undoable).
    fx.add_node(db, ms, "Question about the old order", confirmed=True,
                introduced_in="hierarchy.md")
    for n in (4, 5, 16, 1):
        inferred(db, ms, n)
    link_concepts(db, mid, "Ghost Term", "depends_on", "population",
                  status="inferred", source_id=db.source("system"))
    ranking = node(db, "ranking")
    proposal(db, ms, "note_update", ranking["id"],
             {"name": "ranking", "current_note": None,
              "proposed_note": "An ordering by a metric.",
              "current_kind": "concept", "proposed_kind": "concept"})
    proposal(db, ms, "variant_of_retired", ranking["id"],
             {"proposed_name": "rankings", "kind": "concept", "notes": None,
              "retired_name": "ranked"})
    proposal(db, ms, "alias", ranking["id"],
             {"alias": "the ordering", "canonical": "ranking",
              "sentence": "A hierarchy is the ordering", "alias_is_new": True})
    proposal(db, ms, "vanished", ghost["id"],
             {"name": "Ghost Term", "was_in": "hierarchy.md"})
    # A revival that is NOT due: the text has not changed since retirement.
    caste = node(db, "Caste")
    retire_concept(db, mid, caste)
    proposal(db, ms, "revival", caste["id"], {"name": "Caste", "kind": "concept"})
    # A revival that IS due: 'Race' retired, then written about again.
    race = node(db, "Race")
    retire_concept(db, mid, race)
    proposal(db, ms, "revival", race["id"], {"name": "Race", "kind": "concept"})
    source = Path(ms["path"])
    text = (source / "hierarchy.md").read_text(encoding="utf-8")
    text += ("\n## **Race again**\n\nRace returns here as a deme the essay "
             "cannot do without.\n\n## **And again**\n\nRace is named once "
             "more, in a second section.\n")
    (source / "hierarchy.md").write_text(text, encoding="utf-8")
    collect_revision(db, ms, session_id=None, source="test")
    return db, ms


def snapshot(db) -> dict:
    nodes = {r["name"]: r["status"] for r in db.all(
        "SELECT name, status FROM concept_nodes")}
    edges = {r["id"]: (r["from_node"], r["relation"], r["to_node"],
                       r["status"], r["support"]) for r in db.all(
        "SELECT * FROM concept_edges")}
    props = {r["id"]: r["state"] for r in db.all(
        "SELECT id, state FROM knowledge_proposals")}
    return {"nodes": nodes, "edges": edges, "proposals": props}


def check_backup(root: Path) -> None:
    print("backup:")
    db, ms = fixture(root / "backup")
    path = mig.backup(db)
    check("the backup lands in backups/ as pre-extraction-v2-<stamp>.db",
          path.exists() and path.parent.name == "backups"
          and path.name.startswith("pre-extraction-v2-"), str(path))
    copy = sqlite3.connect(str(path))
    try:
        n = copy.execute("SELECT count(*) FROM concept_nodes").fetchone()[0]
    finally:
        copy.close()
    live = db.one("SELECT count(*) AS n FROM concept_nodes")["n"]
    check("the copy holds every node the live database holds", n == live,
          f"{n} vs {live}")


def check_plan_and_apply(root: Path) -> None:
    print("plan and apply, gates only:")
    db, ms = fixture(root / "apply")
    knob = extraction.knobs({})
    before = snapshot(db)
    p = mig.plan(db, ms, knob)
    fate = {extraction.candidate_label(c): c for c in p.candidates}
    check("the grounded pending concept is kept",
          mig.kept(fate["ideological population (concept)"]),
          json.dumps({k: v.get("signal") for k, v in fate.items()}))
    check("the ghost concept fails gate 1-2 (ungrounded)",
          fate["Ghost Term (concept)"].get("signal") == "ungrounded",
          fate["Ghost Term (concept)"].get("reason", ""))
    check("#4 (population depends_on accord) is kept",
          mig.kept(fate["population —depends_on→ accord"]))
    check("#16 (the mirror of #5) folds at gate 6",
          fate["ranking —leads_to→ rank"].get("signal") == "duplicate"
          and mig.kept(fate["rank —depends_on→ ranking"]),
          fate["ranking —leads_to→ rank"].get("reason", ""))
    check("#1 (Boas elaborates relativism, no shared paragraph) fails gate 2",
          fate["Franz Boas —elaborates→ Cultural relativism"].get("signal")
          == "ungrounded")
    check("the edge on the ghost fails as unknown_endpoint",
          fate["Ghost Term —depends_on→ population"].get("signal")
          == "unknown_endpoint")
    actions = {(i["kind"], (loads(i["row"]["payload"], {}) or {}).get("name")):
               i["action"] for i in p.proposals}
    check("note_update and variant_of_retired are superseded; alias and "
          "vanished kept",
          actions[("note_update", "ranking")] == "supersede"
          and actions[("variant_of_retired", None)] == "supersede"
          and actions[("alias", None)] == "keep"
          and actions[("vanished", "Ghost Term")] == "keep", str(actions))
    check("the revival below the bar is superseded, the one the new text "
          "makes due is re-asked",
          actions[("revival", "Caste")] == "supersede"
          and actions[("revival", "Race")] == "re-ask", str(actions))
    check("every confirmed extracted concept absent from the text is planned "
          "for retirement (the fixture's two paraphrase names too); one "
          "with text is not",
          sorted(n["name"] for n in p.absent)
          == ["Deception born to perish", "Question about the old order",
              "Sustaining deception"],
          str([n["name"] for n in p.absent]))
    check("plan writes nothing", snapshot(db) == before)
    text = mig.render_plan(p)
    check("the report names every candidate with its fate",
          "Ghost Term (concept)" in text and "out (ungrounded" in text
          and "kept" in text and "revival" in text, text)

    report = mig.apply(db, ms, p)
    check("apply reports the counts",
          report["concepts"] == {"kept": 1, "out": 1}
          and report["links"]["kept"] == 2 and report["links"]["out"] == 3
          and report["proposals"]["superseded"] == 4
          and report["proposals"]["re-asked"] == 1
          and report["proposals"]["raised"] == 1
          and report["proposals"]["kept"] == 2
          and report["absent"] == 3, json.dumps(report))
    gone = node(db, "Question about the old order")
    check("the absent confirmed concept is retired, tagged by the run, with "
          "an evidence row naming the gate",
          gone["status"] == "retired"
          and loads(gone["metadata"], {})["retired_from"]["by_run"] == "migration-1"
          and any(r["target"].startswith("Question about the old order")
                  and loads(r["metadata"], {}).get("gate") == "absent"
                  for r in db.all("SELECT * FROM evidence WHERE evidence_type "
                                  "= 'extraction_adjudication'")),
          json.dumps(loads(gone["metadata"], {})))
    ghost = node(db, "Ghost Term")
    gmeta = loads(ghost["metadata"], {})
    check("the ghost is retired with retired_from tagged by the run",
          ghost["status"] == "retired"
          and gmeta["retired_from"]["by_run"] == "migration-1"
          and gmeta["retired_from"]["status"] == "realized", json.dumps(gmeta))
    kept_node = node(db, "ideological population")
    kmeta = loads(kept_node["metadata"], {})
    check("the kept concept stays pending with metadata.extraction",
          kept_node["status"] != "retired" and not kmeta.get("confirmed")
          and kmeta["extraction"]["run"] == "migration-1"
          and kmeta["extraction"]["span"], json.dumps(kmeta))
    e4 = edge(db, "population", "depends_on", "accord")
    ev = loads(e4["evidence"], [])
    check("the kept edge stays inferred, its evidence gains the span and "
          "support counts it",
          e4["status"] == "inferred" and e4["support"] == 1
          and ev[0]["run"] == "migration-1" and ev[0]["span"], json.dumps(ev))
    e16 = edge(db, "ranking", "leads_to", "rank")
    m16 = loads(e16["metadata"], {})
    check("the folded mirror is retired, tagged by the run",
          e16["status"] == "retired"
          and m16["retired_from"] == {"status": "inferred",
                                      "by_run": "migration-1"}, json.dumps(m16))
    rows = [r for r in db.all(
        "SELECT * FROM evidence WHERE evidence_type = 'extraction_adjudication'")
        if (loads(r["metadata"], {}) or {}).get("run") == "migration-1"]
    signals = sorted(r["signal"] for r in rows)
    check("one evidence row per candidate, migrated_out naming the gate",
          signals == ["migrated_out"] * 7 + ["new"] * 3
          and all((loads(r["metadata"], {}) or {}).get("gate")
                  for r in rows if r["signal"] == "migrated_out"),
          str([(r["signal"], r["target"], loads(r["metadata"], {}).get("gate"))
               for r in rows]))
    states = {r["kind"]: r["state"] for r in db.all(
        "SELECT kind, state FROM knowledge_proposals WHERE kind IN "
        "('note_update', 'variant_of_retired', 'alias', 'vanished')")}
    check("proposal states follow §5.3",
          states == {"note_update": "superseded",
                     "variant_of_retired": "superseded",
                     "alias": "open", "vanished": "open"}, str(states))
    raised = db.all("SELECT * FROM knowledge_proposals WHERE kind = 'revival' "
                    "AND state = 'open'")
    payload = loads(raised[0]["payload"], {}) if raised else {}
    check("the due revival is re-asked once, with spans, from the run",
          len(raised) == 1 and payload["name"] == "Race"
          and payload["spans"] and raised[0]["source"] == "extraction/migration-1",
          json.dumps(payload))
    runs = extraction.runs(db, ms)
    check("the run is recorded on the manuscript",
          runs and runs[-1]["kind"] == "migrate"
          and runs[-1]["id"] == "migration-1", json.dumps(runs))

    print("undo, round trip:")
    report = mig.undo(db, ms, "migration-1")
    after = snapshot(db)
    stray = {k for k, v in after["proposals"].items()
             if k not in before["proposals"]}
    for k in stray:
        after["proposals"].pop(k)
    check("undo restores every node, edge and proposal state",
          after == before,
          json.dumps({"report": report,
                      "nodes": {k: (before["nodes"].get(k), after["nodes"].get(k))
                                for k in set(before["nodes"]) | set(after["nodes"])
                                if before["nodes"].get(k) != after["nodes"].get(k)},
                      "edges": [k for k in before["edges"]
                                if before["edges"][k] != after["edges"].get(k)]}))
    withdrawn = db.all("SELECT state, metadata FROM knowledge_proposals WHERE "
                       "source = 'extraction/migration-1'")
    check("the revival the migration raised is withdrawn, not deleted",
          len(withdrawn) == 1 and withdrawn[0]["state"] == "superseded"
          and loads(withdrawn[0]["metadata"], {}).get("undone") == "migration-1")
    kmeta = loads(node(db, "ideological population")["metadata"], {})
    check("undo clears the kept concept's extraction tag",
          "extraction" not in kmeta, json.dumps(kmeta))
    check("the evidence rows stay as the audit trail",
          len([r for r in db.all(
              "SELECT metadata FROM evidence WHERE evidence_type = "
              "'extraction_adjudication'")
              if (loads(r["metadata"], {}) or {}).get("run") == "migration-1"])
          == 10)
    check("the undo is recorded too",
          extraction.runs(db, ms)[-1]["kind"] == "migrate-undo")


def check_adjudicated(root: Path) -> None:
    print("with an adjudicator:")
    db, ms = fixture(root / "judged")
    knob = extraction.knobs({})
    judge = Judge({
        "ideological population (concept)": {"verdict": "drop",
                                   "reason": "a kind, not a load-bearing term"},
        "population —depends_on→ accord": {"verdict": "new",
                                           "reason": "asserted in the span"},
        "rank —depends_on→ ranking": {"verdict": "retype", "relation":
                                      "specializes", "reason": "a rank is a "
                                      "kind of place"},
    })
    p = mig.plan(db, ms, knob, judge_llm=judge)
    check("one adjudicator call per essay with survivors", judge.calls == 1
          and p.judged and p.model == "stub/judge")
    report = mig.apply(db, ms, p)
    dropped = node(db, "ideological population")
    row = next(r for r in db.all(
        "SELECT * FROM evidence WHERE evidence_type = 'extraction_adjudication'")
        if r["target"] == "ideological population (concept)")
    check("a dropped survivor is migrated out with the adjudicator's reason",
          dropped["status"] == "retired" and row["signal"] == "migrated_out"
          and "drop: a kind" in loads(row["metadata"], {})["reason"]
          and loads(row["metadata"], {})["adjudicator"] == "stub/judge",
          json.dumps(loads(row["metadata"], {})))
    e5 = edge(db, "rank", "specializes", "ranking")
    m5 = loads(e5["metadata"], {}) if e5 else {}
    check("a retyped edge changes relation in place and remembers what it was",
          e5 is not None and e5["status"] == "inferred"
          and m5["extraction"]["migrated_from"]["relation"] == "depends_on"
          and report["links"]["retyped"] == 1, json.dumps(m5))
    mig.undo(db, ms, "migration-1")
    check("undo puts the retyped edge back",
          edge(db, "rank", "depends_on", "ranking") is not None
          and edge(db, "rank", "specializes", "ranking") is None)
    check("undo revives the dropped concept",
          node(db, "ideological population")["status"] != "retired")
    check("undo revives the absent confirmed concept too",
          node(db, "Question about the old order")["status"] != "retired")


def check_cli(root: Path) -> None:
    print("the verbs:")
    from authorlm.cli import main

    ws = root / "cli"
    db, ms = fixture(ws, workspace=True)
    os.environ["AUTHORLM_CONFIG"] = str(ws / ".authorlm" / "config.toml")
    (ws / ".authorlm" / "config.toml").write_text(
        '[llm]\nenabled = true\nprovider = "openai"\n'
        'base_url = "http://127.0.0.1:9/v1"\nmodel = "stub"\n'
        '[extraction]\nenabled = false\nadjudicator_choices = ["stub-judge"]\n')

    def cli(*argv: str) -> tuple[int, str]:
        buffer = io.StringIO()
        code = 0
        try:
            with contextlib.redirect_stdout(buffer):
                main(["--workspace", str(ws), *argv])
        except SystemExit as err:
            code = 0 if err.code in (0, None) else 1
            if isinstance(err.code, str):
                buffer.write(err.code)
        return code, buffer.getvalue()

    code, out = cli("extract", "backup")
    check("`extract backup` writes the rollback copy",
          code == 0 and "pre-extraction-v2-" in out, out)
    before = snapshot(db)
    code, out = cli("extract", "migrate", "--dry-run", "--gates-only")
    report = ws / ".authorlm" / "extraction-migration-migration-1-dry-run.txt"
    check("`--dry-run` prints and files the report, writing nothing",
          code == 0 and report.exists() and "Dry run" in out
          and snapshot(db) == before, out)
    code, out = cli("extract", "migrate")
    check("without --gates-only and no terminal the migration stops before "
          "any call", code == 1 and "--adjudicator" in out, out)
    code, out = cli("extract", "migrate", "--gates-only")
    check("`extract migrate --gates-only` applies", code == 0
          and "applied (gates only)" in out, out)
    code, out = cli("extract", "migrate", "--gates-only")
    check("a second run with the same id is refused",
          code == 1 and "already recorded" in out, out)
    code, out = cli("extract", "migrate", "--undo", "migration-1")
    check("`--undo` reverses it", code == 0 and "undone" in out, out)
    code, out = cli("extract", "runs")
    check("`extract runs` lists the migration and its undo",
          code == 0 and "migrate" in out and "migrate-undo" in out, out)


def check_absent_apostrophe(root: Path) -> None:
    """2026-10-28: a confirmed extracted name stored with ASCII `'` must
    still match Doc-side curly `’` in the manuscript — else migrate
    retires it (and its edges) as 'absent' while the phrase is on the
    page."""
    from authorlm.concepts import concept_pattern

    print("absent gate vs curly apostrophe:")
    ascii_name = "God's good graces"
    curly_text = "a Question about God’s good graces in the essay"
    check("concept_pattern matches curly apostrophe against an ASCII name",
          bool(concept_pattern(ascii_name).search(curly_text)),
          concept_pattern(ascii_name).pattern)
    check("…and ASCII text against a curly name",
          bool(concept_pattern("God’s good graces").search(
              "about God's good graces here")),
          concept_pattern("God’s good graces").pattern)

    db, ms = fx.build(root / "apos", workspace=False)
    fx.add_node(db, ms, ascii_name, confirmed=True,
                introduced_in="hierarchy.md")
    # Truly gone paraphrase: still retired.
    fx.add_node(db, ms, "Written word as iron bond", confirmed=True,
                introduced_in="hierarchy.md")
    source = Path(ms["path"])
    text = (source / "hierarchy.md").read_text(encoding="utf-8")
    text += f"\n\n{curly_text}\n"
    (source / "hierarchy.md").write_text(text, encoding="utf-8")
    collect_revision(db, ms, session_id=None, source="test")
    p = mig.plan(db, ms, extraction.knobs({}))
    absent = sorted(n["name"] for n in p.absent)
    check("the still-present ASCII name is not planned absent",
          ascii_name not in absent, str(absent))
    check("a name truly missing from the text is still planned absent",
          "Written word as iron bond" in absent, str(absent))


def main_test() -> None:
    root = Path(tempfile.mkdtemp(prefix="authorlm-extract-mig-"))
    try:
        check_backup(root)
        check_plan_and_apply(root)
        check_adjudicated(root)
        check_cli(root)
        check_absent_apostrophe(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print(f"\nAll {PASSED} checks passed.")


if __name__ == "__main__":
    main_test()
