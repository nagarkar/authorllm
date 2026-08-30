"""Critique-pass tests (docs/critique-pass-design.md).

Stage 1: schema foundation + provenance — the sources registry, source_id
columns with deterministic backfill, intent proposed/rejected lifecycle and
scope, and the new essay_summaries / critique_passes tables.

Run: python3 tests/test_critique.py
"""

from __future__ import annotations

import os

# Offline suite: pin the project-config and .env lookups away from the
# real ones. Without this a checkout's config.toml (llm enabled, keys in
# .env) is picked up by every test process and the suite makes live,
# billed model calls — and asserts against whatever they return.
os.environ["AUTHORLM_CONFIG"] = "/nonexistent/authorlm-test/config.toml"
os.environ["AUTHORLM_ENV"] = "/nonexistent/authorlm-test/.env"
# And pin client provenance OFF. The suites run INSIDE a Claude Code
# Bash call, so CLAUDE_CODE_SESSION_ID is in their own environment and
# the claude-code adapter would stamp the developer's live chat into
# every fixture row — tests passing for the wrong reason. Same failure
# mode as a leaked config, so it gets the same treatment: pin it.
os.environ["AUTHORLM_CLIENT"] = "none"


import json
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from authorlm import concepts, critique, sessions, styles  # noqa: E402
from authorlm.db import (Database, ko_fields, loads, PROVENANCE_TABLES)  # noqa: E402

PASSED = 0


def check(label: str, condition: bool, context: str = "") -> None:
    global PASSED
    assert condition, f"FAIL: {label}\n{context}"
    PASSED += 1
    print(f"  ok: {label}")


# Pre-provenance table shapes, as they exist in a live database created
# before this migration. The migration path under test is: open with the
# current Database → columns added → backfill applied.
OLD_DDL = """
CREATE TABLE declared_intents (
    id TEXT PRIMARY KEY, version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL, created_by TEXT NOT NULL,
    schema_version TEXT NOT NULL, metadata TEXT NOT NULL DEFAULT '{}',
    manuscript_id TEXT NOT NULL, session_id TEXT, statement TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active', outcome TEXT
);
CREATE TABLE concept_nodes (
    id TEXT PRIMARY KEY, version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL, created_by TEXT NOT NULL,
    schema_version TEXT NOT NULL, metadata TEXT NOT NULL DEFAULT '{}',
    manuscript_id TEXT NOT NULL, name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'concept',
    status TEXT NOT NULL DEFAULT 'declared',
    introduced_in TEXT, notes TEXT, aliases TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE concept_edges (
    id TEXT PRIMARY KEY, version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL, created_by TEXT NOT NULL,
    schema_version TEXT NOT NULL, metadata TEXT NOT NULL DEFAULT '{}',
    manuscript_id TEXT NOT NULL, from_node TEXT NOT NULL,
    relation TEXT NOT NULL, to_node TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'declared',
    support INTEGER NOT NULL DEFAULT 0, evidence TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE style_laws (
    id TEXT PRIMARY KEY, version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL, created_by TEXT NOT NULL,
    schema_version TEXT NOT NULL, metadata TEXT NOT NULL DEFAULT '{}',
    manuscript_id TEXT NOT NULL, guide_id TEXT, file TEXT,
    aspect TEXT NOT NULL, statement TEXT NOT NULL, notes TEXT,
    status TEXT NOT NULL DEFAULT 'active', overrides TEXT,
    CHECK ((guide_id IS NULL) != (file IS NULL))
);
CREATE TABLE editorial_beliefs (
    id TEXT PRIMARY KEY, version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL, created_by TEXT NOT NULL,
    schema_version TEXT NOT NULL, metadata TEXT NOT NULL DEFAULT '{}',
    manuscript_id TEXT NOT NULL, statement TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'candidate',
    confidence REAL NOT NULL DEFAULT 0.3,
    supporting INTEGER NOT NULL DEFAULT 0,
    contradicting INTEGER NOT NULL DEFAULT 0,
    outstanding_questions TEXT NOT NULL DEFAULT '[]',
    source TEXT NOT NULL DEFAULT 'review-explanation'
);
CREATE TABLE evidence (
    id TEXT PRIMARY KEY, version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL, created_by TEXT NOT NULL,
    schema_version TEXT NOT NULL, metadata TEXT NOT NULL DEFAULT '{}',
    manuscript_id TEXT NOT NULL, episode_id TEXT,
    evidence_type TEXT NOT NULL, signal TEXT NOT NULL, target TEXT NOT NULL,
    supports_belief TEXT, weight TEXT NOT NULL DEFAULT 'medium'
);
"""


def _old_row(prefix: str, **fields) -> tuple[str, dict]:
    row = ko_fields(prefix)
    row.update(fields)
    return row["id"], row


def _raw_insert(conn: sqlite3.Connection, table: str, row: dict) -> None:
    cols = ", ".join(row)
    ph = ", ".join("?" for _ in row)
    conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({ph})",
                 list(row.values()))


def test_migration_backfill(root: Path) -> None:
    print("migration + backfill on a pre-provenance database:")
    db_path = root / "old.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(OLD_DDL)
    mid = "ms-test"
    extracted_id, row = _old_row(
        "cn", manuscript_id=mid, name="Extracted", kind="concept",
        status="declared", introduced_in=None, notes=None, aliases="[]")
    row["metadata"] = json.dumps({"origin": "extracted", "confirmed": False})
    _raw_insert(conn, "concept_nodes", row)
    declared_id, row = _old_row(
        "cn", manuscript_id=mid, name="Declared", kind="concept",
        status="realized", introduced_in="a.md", notes="n", aliases="[]")
    _raw_insert(conn, "concept_nodes", row)
    inferred_edge, row = _old_row(
        "ce", manuscript_id=mid, from_node=extracted_id, relation="elaborates",
        to_node=declared_id, status="inferred", support=0, evidence="[]")
    _raw_insert(conn, "concept_edges", row)
    declared_edge, row = _old_row(
        "ce", manuscript_id=mid, from_node=declared_id, relation="depends_on",
        to_node=extracted_id, status="declared", support=0, evidence="[]")
    _raw_insert(conn, "concept_edges", row)
    intent_id, row = _old_row(
        "di", manuscript_id=mid, session_id=None, statement="Fix the essay",
        status="active", outcome=None)
    _raw_insert(conn, "declared_intents", row)
    element_id, row = _old_row(
        "se", manuscript_id=mid, guide_id="sg-x", file=None,
        aspect="tone", statement="Be unflinching", notes=None,
        status="active", overrides=None)
    _raw_insert(conn, "style_laws", row)
    belief_id, row = _old_row(
        "pol", manuscript_id=mid, statement="Prune redundancy",
        status="candidate", confidence=0.5, supporting=1, contradicting=0,
        outstanding_questions="[]", source="review-explanation")
    _raw_insert(conn, "editorial_beliefs", row)
    review_ev, row = _old_row(
        "ev", manuscript_id=mid, episode_id=None, evidence_type="author_review",
        signal="accepted", target="t", supports_belief=None, weight="high")
    _raw_insert(conn, "evidence", row)
    analysis_ev, row = _old_row(
        "ev", manuscript_id=mid, episode_id=None,
        evidence_type="episode_analysis", signal="pattern", target="t",
        supports_belief=None, weight="medium")
    _raw_insert(conn, "evidence", row)
    conn.commit()
    conn.close()

    db = Database(db_path)
    author = db.source("author")
    system = db.source("system")
    check("author and system sources are distinct rows", author != system)
    for table in PROVENANCE_TABLES:
        holes = db.one(
            f"SELECT COUNT(*) AS n FROM {table} WHERE source_id IS NULL")["n"]
        check(f"{table}: no NULL source_id after backfill", holes == 0)
    check("extracted concept backfills to system",
          db.one("SELECT source_id FROM concept_nodes WHERE id = ?",
                 (extracted_id,))["source_id"] == system)
    check("author-declared concept backfills to author",
          db.one("SELECT source_id FROM concept_nodes WHERE id = ?",
                 (declared_id,))["source_id"] == author)
    check("inferred edge backfills to system",
          db.one("SELECT source_id FROM concept_edges WHERE id = ?",
                 (inferred_edge,))["source_id"] == system)
    check("declared edge backfills to author",
          db.one("SELECT source_id FROM concept_edges WHERE id = ?",
                 (declared_edge,))["source_id"] == author)
    for table, obj_id in (("declared_intents", intent_id),
                          ("style_laws", element_id),
                          ("editorial_beliefs", belief_id)):
        check(f"{table} row backfills to author",
              db.one(f"SELECT source_id FROM {table} WHERE id = ?",
                     (obj_id,))["source_id"] == author)
    check("author_review evidence backfills to author",
          db.one("SELECT source_id FROM evidence WHERE id = ?",
                 (review_ev,))["source_id"] == author)
    check("episode_analysis evidence backfills to system",
          db.one("SELECT source_id FROM evidence WHERE id = ?",
                 (analysis_ev,))["source_id"] == system)
    check("scope column added to declared_intents",
          "scope" in db._columns("declared_intents"))
    check("reopening the database does not re-run backfill or duplicate sources",
          Database(db_path).one("SELECT COUNT(*) AS n FROM sources")["n"] == 2)


def test_fresh_db_provenance(root: Path) -> None:
    print("fresh database — new tables and insert-time provenance:")
    db = Database(root / "fresh.db")
    mid = "ms-fresh"

    for table in ("sources", "essay_summaries", "critique_passes"):
        check(f"table {table} exists",
              db.one("SELECT name FROM sqlite_master WHERE name = ?",
                     (table,)) is not None)
    check("essay_summaries has the staleness columns",
          {"file", "summary", "source_hash", "upstream_hash",
           "upstream_stale"} <= db._columns("essay_summaries"))
    check("critique_passes has the pass-state columns",
          {"source_id", "cursor", "essay_state", "pinned_versions",
           "status"} <= db._columns("critique_passes"))

    author = db.source("author")
    check("source() is idempotent", db.source("author") == author)
    critic = db.source("critic", "SMSTTD Editorial Analysis",
                       "literary critic, docx, 2026-08-13")
    check("critic source registered with detail",
          db.one("SELECT detail FROM sources WHERE id = ?",
                 (critic,))["detail"] == "literary critic, docx, 2026-08-13")

    intent = sessions.declare_intent(db, mid, "Author's own goal")
    check("author-declared intent stamps author provenance",
          intent["source_id"] == author and intent["status"] == "active")

    proposed = sessions.declare_intent(
        db, mid, "Cut the section by at least half",
        scope="recapitulation.md", status="proposed", source_id=critic)
    check("imported intent is born proposed, scoped, critic-sourced",
          proposed["status"] == "proposed"
          and proposed["scope"] == "recapitulation.md"
          and proposed["source_id"] == critic)
    check("proposed intent opens no episode",
          db.one("SELECT COUNT(*) AS n FROM editorial_episodes "
                 "WHERE intent_id = ?", (proposed["id"],))["n"] == 0)

    node = concepts.add_concept(db, mid, "Choice")
    check("author-added concept stamps author", node["source_id"] == author)
    system_node = concepts.add_concept(
        db, mid, "Extracted thing", source_id=db.source("system"))
    check("extractor-added concept stamps system",
          system_node["source_id"] == db.source("system"))
    edge = concepts.link_concepts(db, mid, "Choice", "permits", "Becoming")
    check("author-linked edge stamps author", edge["source_id"] == author)

    element = styles.add_element(db, mid, "tone", "Stay spare", file="a.md")
    check("style element stamps author by default",
          element["source_id"] == author)
    prop_el = styles.add_element(
        db, mid, "syntax", "Separate commentary from proof",
        file="recapitulation.md", status="proposed", source_id=critic)
    check("critique style element is born proposed, critic-sourced",
          prop_el["status"] == "proposed" and prop_el["source_id"] == critic)

    ev = ko_fields("ev")
    ev.update(manuscript_id=mid, episode_id=None,
              evidence_type="author_review", signal="accepted", target="x",
              supports_belief=None, weight="high")
    db.insert("evidence", ev)
    check("evidence auto-stamps author for review types",
          db.one("SELECT source_id FROM evidence WHERE id = ?",
                 (ev["id"],))["source_id"] == author)
    ev2 = ko_fields("ev")
    ev2.update(manuscript_id=mid, episode_id=None,
               evidence_type="episode_analysis", signal="pattern", target="y",
               supports_belief=None, weight="medium")
    db.insert("evidence", ev2)
    check("evidence auto-stamps system for episode analysis",
          db.one("SELECT source_id FROM evidence WHERE id = ?",
                 (ev2["id"],))["source_id"] == db.source("system"))


SAMPLE_REPORT = """# Ten global changes before line editing

1.  **Choose the book's contract.** Use \\"visionary philosophy\\" on the
    title and jacket.

2.  **Separate claim levels.** Mark propositions as definition or
    inference.

# Revision dossier

## Recapitulation

1.  **Cut the section by at least half.** It currently restates nearly
    every sermon.

2.  **Repair the morality paragraph.** It says two contradictory things.

## Ecce Homo

1.  **Describe the crash concretely.** One precise physical scene.

## Bottom line

No numbered items here, just prose.
"""


def test_parse_units() -> None:
    print("markdown report parsing:")
    units = critique.parse_units(SAMPLE_REPORT)
    check("headings with numbered items are captured",
          len(units["Ten global changes before line editing"]) == 2
          and len(units["Recapitulation"]) == 2
          and len(units["Ecce Homo"]) == 1)
    check("prose-only sections yield no items",
          units["Bottom line"] == [])
    first = units["Recapitulation"][0]
    check("items unwrap, strip bold, and unescape",
          first.startswith("Cut the section by at least half. It currently")
          and "**" not in first and "\\" not in first)
    check("escaped quotes survive as plain quotes",
          '"visionary philosophy"' in
          units["Ten global changes before line editing"][0])


def _manifest() -> dict:
    return {
        "source": {"name": "Test Editorial Analysis",
                   "detail": "test critic, 2026-08-13"},
        "items": [
            {"kind": "intent", "unit": "Recapitulation", "ordinal": 1,
             "text": "Cut the section by at least half.",
             "scope": "recapitulation.md"},
            {"kind": "intent", "unit": "Global", "ordinal": 1,
             "text": "Cut repeated exposition by 20 to 25 percent.",
             "scope": None},
            {"kind": "style_element", "unit": "Global", "ordinal": 2,
             "text": "Cite every quotation and contestable claim.",
             "aspect": "citation", "guide": "house"},
            {"kind": "style_element", "unit": "Strengths", "ordinal": 1,
             "text": "The recurring images are load-bearing; edits preserve them.",
             "aspect": "figure", "guide": "house"},
        ],
    }


def test_import_and_triage(root: Path) -> None:
    print("manifest import + triage lifecycle:")
    db = Database(root / "intake.db")
    mid = "ms-intake"
    guide = ko_fields("sg")
    guide.update(manuscript_id=mid, name="house", parent=None)
    db.insert("style_guides", guide)

    result = critique.import_manifest(db, mid, _manifest())
    check("import lands intents and elements as counted",
          result["intents"] == 2 and result["style_laws"] == 2
          and not result["errors"])
    check("re-import is idempotent (everything skipped)",
          critique.import_manifest(db, mid, _manifest())["skipped"] == 4)

    pend = critique.pending(db, mid)
    check("all imported items are proposed",
          len(pend["intents"]) == 2 and len(pend["elements"]) == 2)
    scoped = critique.pending(db, mid, scope="recapitulation.md")
    check("an essay scope is the gate's chain: the essay's own items AND "
          "manuscript-wide ones, plus global style elements",
          len(scoped["intents"]) == 2 and len(scoped["elements"]) == 2)
    other = critique.pending(db, mid, scope="preface.md")
    check("a sibling essay's items are not in another essay's sitting",
          [i["statement"] for i in other["intents"]]
          == ["Cut repeated exposition by 20 to 25 percent."])
    global_sitting = critique.pending(db, mid, scope="manuscript")
    check("'manuscript' scope selects only manuscript-wide items plus elements",
          len(global_sitting["intents"]) == 1
          and len(global_sitting["elements"]) == 2)
    item = scoped["intents"][0]
    check("imported intent carries unit/ordinal metadata and critic source",
          loads(item["metadata"], {})["critique"]["ordinal"] == 1
          and item["source_id"] == result["source_id"])
    check("guide-scoped element attached to the named guide",
          all(el["guide_id"] == guide["id"] for el in pend["elements"]))

    critique.accept_intent(db, mid, item)
    check("accepted intent becomes active",
          db.one("SELECT status FROM declared_intents WHERE id = ?",
                 (item["id"],))["status"] == "active")
    other = [i for i in pend["intents"] if i["id"] != item["id"]][0]
    critique.reject_intent(db, mid, other,
                           "The exposition is deliberate; readers need it.")
    row = db.one("SELECT status, outcome FROM declared_intents WHERE id = ?",
                 (other["id"],))
    check("rejected intent keeps the author's verbatim reason",
          row["status"] == "rejected"
          and row["outcome"] == "The exposition is deliberate; readers need it.")
    el = pend["elements"][0]
    critique.accept_element(db, mid, el)
    el2 = pend["elements"][1]
    critique.reject_element(db, mid, el2, "Too broad as law.")
    check("element verdicts land (active / rejected with reason kept)",
          db.one("SELECT status FROM style_laws WHERE id = ?",
                 (el["id"],))["status"] == "active"
          and loads(db.one("SELECT metadata FROM style_laws WHERE id = ?",
                           (el2["id"],))["metadata"], {})["rejection_reason"]
          == "Too broad as law.")

    evs = db.all("SELECT * FROM evidence WHERE manuscript_id = ? "
                 "AND evidence_type = 'critique_triage'", (mid,))
    author = db.source("author")
    check("every triage verdict is author-sourced evidence",
          len(evs) == 4 and all(e["source_id"] == author for e in evs))

    extra = critique.import_manifest(db, mid, {
        "source": {"name": "Test Editorial Analysis"},
        "items": [{"kind": "intent", "unit": "Recapitulation", "ordinal": 3,
                   "text": "Explain the Anagramma materially.",
                   "scope": "recapitulation.md"}]})
    revisable = critique.pending(db, mid, scope="recapitulation.md")["intents"][0]
    critique.revise_intent(db, mid, revisable,
                           "Explain the Anagramma in the Recapitulation only.")
    revised = db.one("SELECT * FROM declared_intents WHERE id = ?",
                     (revisable["id"],))
    lineage = loads(revised["metadata"], {})["critique"]
    check("revised intent is active with the author's wording",
          revised["status"] == "active"
          and revised["statement"]
          == "Explain the Anagramma in the Recapitulation only.")
    check("revision flips provenance to the author",
          revised["source_id"] == author)
    check("critic's original text and source survive as lineage",
          lineage["original_text"] == "Explain the Anagramma materially."
          and lineage["original_source_id"] == extra["source_id"]
          and lineage["ordinal"] == 3)
    check("revision records the original→final pair as modified evidence",
          db.one("SELECT COUNT(*) AS n FROM evidence WHERE manuscript_id = ? "
                 "AND evidence_type = 'critique_triage' AND signal = 'modified' "
                 "AND target LIKE '%→%'", (mid,))["n"] == 1)

    tallies = critique.status(db, mid)[0]
    check("status tallies reflect the verdicts",
          tallies["intents"] == {"proposed": 0, "accepted": 1, "rejected": 1}
          and tallies["elements"] == {"proposed": 0, "accepted": 1,
                                      "rejected": 1})

    bad = critique.import_manifest(db, mid, {
        "source": {"name": "Test Editorial Analysis"},
        "items": [{"kind": "style_element", "unit": "X", "ordinal": 9,
                   "text": "y", "aspect": "tone", "guide": "nonexistent"}]})
    check("unknown guide is reported, not silently dropped",
          bad["errors"] and "nonexistent" in bad["errors"][0])


def test_shell_history(root: Path) -> None:
    print("persistent shell history:")
    try:
        import readline
    except ImportError:
        print("  (readline unavailable — skipped)")
        return
    from authorlm.shell import (_install_history, _save_history,
                                _trim_nested_history)
    ws = root / "history-ws"
    (ws / ".authorlm").mkdir(parents=True)

    readline.clear_history()
    mod = _install_history(str(ws))
    check("install returns readline with empty history for a fresh workspace",
          mod is not None and mod.get_current_history_length() == 0)
    mod.add_history("critique status")
    mod.add_history("critique triage --scope manuscript")
    baseline = mod.get_current_history_length()
    mod.add_history("a")           # nested triage keystrokes...
    mod.add_history("r")
    mod.add_history("too vague to act on")
    _trim_nested_history(mod, baseline)
    check("nested prompt entries are trimmed back to the command",
          mod.get_current_history_length() == baseline
          and mod.get_history_item(baseline)
          == "critique triage --scope manuscript")
    _save_history(mod, str(ws))
    check("history file written",
          (ws / ".authorlm" / "shell_history").exists())

    readline.clear_history()
    mod = _install_history(str(ws))
    check("history survives a shell restart, commands only",
          mod.get_current_history_length() == 2
          and mod.get_history_item(1) == "critique status"
          and mod.get_history_item(2)
          == "critique triage --scope manuscript")
    readline.clear_history()


def test_terminal_wrap() -> None:
    print("live terminal-width wrapping:")
    import os

    from authorlm import ui

    old = os.environ.get("COLUMNS")
    try:
        os.environ["COLUMNS"] = "40"
        check("narrow terminal: width follows the terminal minus margin",
              ui.term_width() == 38)
        text = ("Give the Dead differentiated voices. One spokesperson makes "
                "them a chorus-shaped prop. Seed at least three dispositions.")
        lines = ui.wrap(text, indent="  ").splitlines()
        check("wrapped lines never exceed the live width",
              all(len(line) <= 38 for line in lines) and len(lines) > 1)
        os.environ["COLUMNS"] = "300"
        check("wide terminal: width capped for readability",
              ui.term_width() == 78)
        os.environ["COLUMNS"] = "10"
        check("degenerate width clamps to a sane floor", ui.term_width() == 20)
    finally:
        if old is None:
            os.environ.pop("COLUMNS", None)
        else:
            os.environ["COLUMNS"] = old


def test_numbered_bulk_triage(root: Path) -> None:
    print("numbered bulk triage via the CLI:")
    import contextlib
    import io
    import json as json_mod

    from authorlm.cli import main as cli_main
    from authorlm import api

    ws = root / "bulk-ws"
    ms = ws / "manuscript"
    ms.mkdir(parents=True)
    (ms / "essay.md").write_text("# One\n\nA paragraph.\n")
    with contextlib.redirect_stdout(io.StringIO()):
        cli_main(["--workspace", str(ws), "init", "--name", "bulk",
                  "--path", str(ms)])
    manifest = {
        "source": {"name": "Bulk Critic"},
        "items": [
            {"kind": "intent", "unit": "U", "ordinal": n,
             "text": f"Directive number {n}.", "scope": None}
            for n in (1, 2, 3)
        ],
    }
    mpath = ws / "manifest.json"
    mpath.write_text(json_mod.dumps(manifest))
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        cli_main(["--workspace", str(ws), "critique", "import", str(mpath)])
        cli_main(["--workspace", str(ws), "critique", "list"])
    listing = out.getvalue()
    check("list is numbered 1..N",
          "1." in listing and "2." in listing and "3." in listing)
    with contextlib.redirect_stdout(io.StringIO()):
        cli_main(["--workspace", str(ws), "critique", "triage",
                  "--accept", "1", "3", "--reject", "2",
                  "--reason", "Not this book."])
    db = api.open_db(str(ws))
    rows = db.all("SELECT statement, status, outcome FROM declared_intents "
                  "WHERE status != 'active' OR source_id IN "
                  "(SELECT id FROM sources WHERE kind='critic') "
                  "ORDER BY created_at, id")
    by_stmt = {r["statement"]: r for r in rows}
    check("numbers resolve in list order: 1 and 3 accepted, 2 rejected",
          by_stmt["Directive number 1."]["status"] == "active"
          and by_stmt["Directive number 3."]["status"] == "active"
          and by_stmt["Directive number 2."]["status"] == "rejected"
          and by_stmt["Directive number 2."]["outcome"] == "Not this book.")
    err = io.StringIO()
    ok = True
    try:
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(err):
            cli_main(["--workspace", str(ws), "critique", "triage",
                      "--accept", "7"])
        ok = False
    except SystemExit:
        pass
    check("out-of-range number errors instead of guessing", ok)


def main_test() -> None:
    root = Path(tempfile.mkdtemp(prefix="authorlm-critique-"))
    try:
        test_migration_backfill(root)
        test_fresh_db_provenance(root)
        test_parse_units()
        test_import_and_triage(root)
        test_shell_history(root)
        test_terminal_wrap()
        test_numbered_bulk_triage(root)
    finally:
        import shutil
        shutil.rmtree(root, ignore_errors=True)
    print(f"\nall checks passed ({PASSED})")


if __name__ == "__main__":
    main_test()
