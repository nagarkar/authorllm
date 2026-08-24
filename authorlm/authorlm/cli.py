"""AuthorLM CLI — the single orchestrator (RFC AuthorLM §23.9).

Linear capability sequence, pure functions between stages, every stage
producing an inspectable artifact in SQLite:

    collect() → build_transitions() → build_episodes() → retrieve()
             → infer_intent() → learn() → generate_guidance()
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
import textwrap
from pathlib import Path

from . import api
from . import concepts as cg
from . import critique as crit
from . import policies as pol
from . import sessions as ses
from . import triage as triage_service
from . import ui
from .briefing import build_briefing
from .db import Database, ko_fields, loads
from .extraction import extract_concepts
from .guidance import INTENT_KINDS, generate_guidance, intent_coverage_notes
from .llm import LLMClient
from .revisions import collect_revision, detect_transitions


def _workspace(args) -> Path:
    return Path(args.workspace).resolve()


def _open_db(args) -> Database:
    data_dir = _workspace(args) / ".authorlm"
    data_dir.mkdir(parents=True, exist_ok=True)
    return Database(data_dir / "authorlm.db")


def _load_config(args) -> dict:
    """Load .authorlm/config.toml (native # comments, stdlib tomllib)."""
    toml_path = _workspace(args) / ".authorlm" / "config.toml"
    if toml_path.exists():
        import tomllib
        try:
            return tomllib.loads(toml_path.read_text())
        except tomllib.TOMLDecodeError as err:
            print(f"warning: could not parse {toml_path} ({err}); ignoring.",
                  file=sys.stderr)
    return {}


def _manuscript(db: Database, args) -> dict:
    name = getattr(args, "manuscript", None)
    if name:
        row = db.one("SELECT * FROM manuscripts WHERE name = ?", (name,))
        if not row:
            sys.exit(f"error: no manuscript named '{name}'. Run 'init' first.")
        return dict(row)
    rows = db.all("SELECT * FROM manuscripts")
    if not rows:
        sys.exit("error: no manuscript registered. Run 'init --name <name> --path <dir>' first.")
    if len(rows) > 1:
        names = ", ".join(r["name"] for r in rows)
        command = getattr(args, "command", "<command>")
        sys.exit(
            f"error: multiple manuscripts ({names}).\n"
            f"Add -m <name>, e.g.: authorlm {command} -m {rows[0]['name']}"
        )
    return dict(rows[0])


def _require_session(db: Database, manuscript: dict) -> dict:
    session = ses.active_session(db, manuscript["id"])
    if not session:
        sys.exit("error: no active session. Run 'session start' first.")
    return dict(session)


# ---------------------------------------------------------------- commands

def _report_llm(llm: LLMClient) -> None:
    line = llm.stats_line()
    if line:
        print(ui.dim(line))


def _run_extraction(db: Database, manuscript: dict, llm: LLMClient,
                    files: list[str] | None = None, full: bool = False,
                    edges_only: bool = False, aliases_only: bool = False) -> None:
    """Collect the current text (if new) and bootstrap the Concept Graph."""
    before = db.one(
        "SELECT * FROM manuscript_versions WHERE manuscript_id = ? "
        "ORDER BY version_no DESC LIMIT 1",
        (manuscript["id"],),
    )
    version = collect_revision(db, manuscript, session_id=None, source="extract")
    if version:
        detect_transitions(db, manuscript["id"], dict(before) if before else None, version)
        print(f"Collected revision v{version['version_no']}.")
    print("Extracting concepts and relationships with the LLM…")
    summary = extract_concepts(db, manuscript, llm, files=files, full=full,
                               edges_only=edges_only, aliases_only=aliases_only)
    if summary is None:
        print("Extraction produced nothing (LLM unavailable or empty manuscript); "
              "the graph is unchanged.")
        _report_llm(llm)
        return
    if summary.get("up_to_date"):
        print("Nothing new since the last extraction — everything is already "
              "processed. Use 'extract --full' to reprocess the whole "
              "manuscript, or name files: extract <file>…")
        return
    print(ui.dim(f"Scope: {summary['scope']}."))
    if summary.get("truncated"):
        print(ui.yellow(
            "warning: text exceeded the extraction size limit and was "
            "truncated — run extraction file-by-file to cover everything: "
            "extract <file>"
        ))
    print(
        f"Extracted {len(summary['nodes'])} new concept(s) and "
        f"{len(summary['edges'])} inferred relationship(s)"
        + (f"; skipped {summary['skipped']} malformed item(s)" if summary["skipped"] else "")
        + (f"; suppressed {summary['suppressed']} previously-rejected concept(s)"
           if summary.get("suppressed") else "")
        + (f"; dropped {summary['ungrounded_links']} ungrounded link(s) "
           "(endpoint not in the mined text)"
           if summary.get("ungrounded_links") else "")
        + (f"; {len(summary['below_bar'])} below the recurrence bar"
           if summary.get("below_bar") else "")
        + "."
    )
    if summary.get("below_bar"):
        print(ui.dim(
            "Below the recurrence bar (single-context phrases, not "
            "admitted; self-healing on a future mention): "
            + ", ".join(f"'{n}'" for n in summary["below_bar"])))
    if summary.get("proposed"):
        print(ui.yellow(
            f"{summary['proposed']} proposal(s) against settled knowledge — "
            "new material conflicts with earlier judgments. Review: proposal review"
        ))
    for node in summary["nodes"]:
        print(f"  • {node['name']} ({node['kind']})")
    if summary["edges"]:
        print("Relationships are hypotheses awaiting your confirmation "
              "(see 'concept list' / next briefing).")
    _report_llm(llm)


def cmd_init(args):
    db = _open_db(args)
    path = Path(args.path).resolve()
    if not path.is_dir():
        sys.exit(f"error: {path} is not a directory.")
    existing = db.one("SELECT * FROM manuscripts WHERE name = ?", (args.name,))
    if existing:
        sys.exit(f"error: manuscript '{args.name}' already registered.")
    row = ko_fields("ms")
    row.update(name=args.name, path=str(path))
    db.insert("manuscripts", row)
    print(f"Registered manuscript '{args.name}' at {path}")
    llm = LLMClient(_load_config(args))
    if llm.enabled and not args.no_extract:
        _run_extraction(db, row, llm)
    print("Next: 'session start', then 'intent declare \"...\"', then 'collect'.")


# Every table that carries manuscript-scoped rows, children first.
MANUSCRIPT_TABLES = [
    "triage_assessments", "triage_runs", "triage_drafts",
    "evidence", "editorial_reviews", "guidance_history", "editorial_policies",
    "concept_edges", "concept_nodes", "editorial_episodes", "inferred_intents",
    "declared_intents", "sessions", "editorial_transitions",
    "style_elements", "style_attachments", "style_guides",
    "manuscript_versions", "manuscripts",
]


def cmd_unregister(args):
    """Clean-slate removal. This is a workspace-management operation for
    prototyping and hermetic tests — it deletes the manuscript's entire
    record, unlike retire/reject which preserve history."""
    db = _open_db(args)
    name = args.name or getattr(args, "manuscript", None)
    if not name:
        sys.exit("usage: unregister <name>  (or: unregister -m <name>)")
    row = db.one("SELECT * FROM manuscripts WHERE name = ?", (name,))
    if not row:
        sys.exit(f"error: no manuscript named '{name}'.")
    mid = row["id"]
    deleted = {}
    for table in MANUSCRIPT_TABLES:
        key = "id" if table == "manuscripts" else "manuscript_id"
        cursor = db.conn.execute(f"DELETE FROM {table} WHERE {key} = ?", (mid,))
        if cursor.rowcount:
            deleted[table] = cursor.rowcount
    db.conn.commit()
    total = sum(deleted.values())
    detail = ", ".join(f"{table}: {count}" for table, count in deleted.items())
    print(f"Unregistered '{name}' — removed {total} row(s) ({detail}).")
    print("The manuscript files on disk were not touched.")


def _expire_idle_session(db: Database, manuscript: dict, args) -> None:
    """Lazy idle expiry: if the active session has been quiet longer than the
    threshold, close it retroactively at its last activity time — announced,
    with the normal closing pipeline (episode analysis) run."""
    expired = api.expire_idle_session(db, manuscript, _load_config(args))
    if not expired:
        return
    session, last, idle = (
        expired["session"], expired["last_activity"], expired["idle_hours"])
    print(ui.yellow(
        f"Session {session['id']} had been idle for {idle:.1f}h — closed it "
        f"retroactively at its last activity ({last[:16].replace('T', ' ')} UTC)."
    ))
    _analyze_closed_episodes(db, manuscript, args)


def _print_comment_harvest(result: dict) -> None:
    """The comment half of a pull/reconcile report: thread actions, the
    to-address queue, and harvest failures. Shared by 'doc pull' and
    session-start reconcile (it-d469ecbf3999)."""
    for act in result.get("thread_actions", []):
        print(ui.green(
            f"Thread {act['action']}: {act['file']} "
            f"(comment {act['comment_id']})"))
        if act.get("diff"):
            print(ui.dim(f"  modified: {act['diff']}"))
    board = (result.get("threads") or {}).get("counts")
    if board:
        print(ui.dim("Margin threads: " + ", ".join(
            f"{n} {s}" for s, n in sorted(board.items()))))
    comments = result.get("comments") or []
    if comments:
        print(ui.bold(f"Comments to address ({len(comments)}):"))
        for c in comments:
            print(f"  • [{c['location']}] on \"{c['quote']}\"")
            print(f"    {c['content']}")
        print(ui.dim(
            f"Resolved {result.get('comments_resolved', 0)} in "
            "the Doc with ingestion receipts. Address every "
            "comment above; when acting on one, record the "
            "author's words verbatim as the evidence/reason."))
    if result.get("comments_error"):
        print(ui.yellow("warning: comment harvest failed "
                        f"({result['comments_error']}) — "
                        "completed without it."))


def _reconcile_gdocs(db: Database, manuscript: dict, args) -> None:
    """Session-start sync with Google Docs: safe one-sided changes are
    applied automatically; two-sided edits are called out, untouched."""
    from . import gdocs

    config = _load_config(args)
    if not config.get("gdocs", {}).get("reconcile_on_start", True):
        return
    links = gdocs.doc_status(db, manuscript)
    if not links.get("_master_id"):
        return
    try:
        service = gdocs.get_service(config, args.workspace, interactive=False)
        docs_service = gdocs.get_docs_service(config, args.workspace,
                                              interactive=False)
    except ValueError:
        print(ui.dim("A master Google Doc exists but Drive isn't authorized "
                     "here — run 'doc auth' to enable session-start sync."))
        return
    except ModuleNotFoundError as err:
        missing = err.name or str(err)
        print(ui.yellow(
            "warning: session-start Google Docs sync is unavailable "
            f"({missing}) — continuing without "
            "reconcile. Install the gdocs extra to enable it."
        ))
        return
    except Exception as err:
        print(ui.yellow(
            "warning: session-start Google Docs setup failed "
            f"({err}) — continuing without reconcile."
        ))
        return
    try:
        report = gdocs.reconcile(db, manuscript, service,
                                 docs_service=docs_service)
    except Exception as err:
        print(ui.yellow(
            "warning: session-start Google Docs reconcile failed "
            f"({err}) — continuing without reconcile."
        ))
        return
    if report["in_sync"]:
        # Prompt tabs sync in bulk; listing every slug would drown the
        # essay list at session start.
        essays = [f for f in report["in_sync"]
                  if not f.startswith(gdocs.ILLUS_DISPLAY_PREFIX)]
        n_prompts = len(report["in_sync"]) - len(essays)
        parts = essays + ([f"{n_prompts} illustration prompt(s)"]
                          if n_prompts else [])
        print(ui.dim("Google Docs in sync: " + ", ".join(parts)))
    if report["pulled"]:
        print(ui.green("Pulled Doc edits: " + ", ".join(report["pulled"])))
        cmd_collect(args)
    if report["pushed"]:
        print(ui.green("Pushed local edits to Docs: " + ", ".join(report["pushed"])))
    if report.get("pending_push"):
        print(ui.yellow("Local edits awaiting push (Docs API unavailable): "
                        + ", ".join(report["pending_push"])))
    for relpath in report["conflicts"]:
        print(ui.yellow(
            f"CONFLICT: {relpath} changed both locally and in its Google Doc "
            f"— untouched. Reconcile by hand (doc open {relpath} + local "
            f"editor), or 'doc pull {relpath} --force' to take the Doc's side."
        ))
    for item in report["errors"]:
        print(ui.dim(f"warning: could not reconcile {item['file']} "
                     f"({item['error'][:80]})"))
    _print_comment_harvest(report)


def _ensure_session(db, manuscript) -> None:
    """Editorial verbs open a session lazily (ratified 2026-08-11):
    render/rerender/scan and doc push/pull attribute their work to an
    episode without blocking on ceremony. Observation (collect, status,
    the watcher) stays deliberately sessionless — history must never
    have gaps."""
    from . import sessions as ses

    if ses.active_session(db, manuscript["id"]) is None:
        s = ses.start_session(db, manuscript["id"])
        print(ui.dim(f"(opened session {s['id']} — editorial work is "
                     "attributed to it; 'session end' closes it)"))


def cmd_session(args):
    db = _open_db(args)
    manuscript = _manuscript(db, args)
    if args.action == "start":
        _expire_idle_session(db, manuscript, args)
        _reconcile_gdocs(db, manuscript, args)
        # Session start must never open blind to work done between
        # sessions — collect first, with the full report (new chapters,
        # transitions, realizations) so nothing arrives unacknowledged.
        # Virgin manuscripts (no baseline version) keep the classic flow:
        # first collect runs after the author declares their concepts.
        has_baseline = db.one(
            "SELECT id FROM manuscript_versions WHERE manuscript_id = ? "
            "LIMIT 1", (manuscript["id"],))
        if has_baseline:
            try:
                opening = api.collect(db, manuscript, _load_config(args),
                                      source="session-open")
                if not opening.get("unchanged"):
                    _print_collect_report(opening)
            except Exception as err:
                print(ui.dim(f"note: opening collect skipped ({err})"))
        try:
            session = ses.start_session(db, manuscript["id"])
        except ValueError as err:
            sys.exit(f"error: {err}")
        print(f"Session {session['id']} started for '{manuscript['name']}'.\n")
        _print_briefing(db, manuscript)
        print("\nDeclare your objective: intent declare \"...\"")
    else:  # end
        try:
            session = ses.end_session(db, manuscript["id"])
        except ValueError as err:
            sys.exit(f"error: {err}")
        print(f"Session {session['id']} ended.")
        _analyze_closed_episodes(db, manuscript, args)
        briefing = build_briefing(db, manuscript["id"], since=session["started_at"])
        velocity = briefing["learning_velocity"]
        print(
            "Learning this session — evidence: {new_evidence}, policies changed: "
            "{policies_changed}, seeded: {policies_seeded}, concepts realized: "
            "{concepts_realized}, edges inferred: {edges_inferred}".format(**velocity)
        )


def _print_briefing(db: Database, manuscript: dict):
    briefing = build_briefing(db, manuscript["id"])
    print(ui.header("═══ Session-Opening Learning Briefing ═══"))
    fresh = briefing["since"] == "1970-01-01T00:00:00Z"
    empty = not any([
        briefing["policy_changes"], briefing["new_policies"],
        briefing["realized_concepts"], briefing["unconfirmed_concepts"],
        briefing["inferred_edges"], briefing["outstanding_questions"],
        briefing["focus_areas"], briefing["active_intents"],
    ])
    if fresh and empty:
        print(ui.dim("First session — nothing learned yet."))
    elif fresh:
        print(ui.dim("First look at this manuscript."))
    else:
        print(ui.dim(f"Since your last session ({briefing['since'][:16].replace('T', ' ')} UTC)."))

    if briefing["policy_changes"]:
        print(ui.bold("\nPolicies strengthened/weakened:"))
        for change in briefing["policy_changes"]:
            print(
                f"  • \"{ui.bold(change['statement'])}\" [{change['status']}] "
                f"confidence {change['confidence']} "
                + ui.dim(f"(+{change['delta_supporting']}/-{change['delta_contradicting']} this period; "
                         f"{change['supporting']}+ / {change['contradicting']}- total)")
            )
    if briefing["new_policies"]:
        print(ui.bold("\nNewly seeded candidate policies (from your explanations):"))
        for policy in briefing["new_policies"]:
            print(f"  • \"{policy['statement']}\" "
                  + ui.dim(f"[{policy['status']}, confidence {policy['confidence']}]"))

    if briefing["realized_concepts"]:
        nodes = briefing["realized_concepts"]
        print(ui.bold(f"\nConcept Graph — newly realized ({len(nodes)}):"))
        for node in nodes[:12]:
            print(f"  • {ui.green(node['name'])} (introduced in {node['introduced_in']})")
        if len(nodes) > 12:
            print(ui.dim(f"  … and {len(nodes) - 12} more (concept list)."))

    if briefing["unconfirmed_concepts"]:
        nodes = briefing["unconfirmed_concepts"]
        print(ui.bold(f"\nExtracted concepts awaiting your confirmation ({len(nodes)}):"))
        by_kind: dict[str, list[str]] = {}
        for node in nodes:
            by_kind.setdefault(node["kind"], []).append(node["name"])
        for kind, names in sorted(by_kind.items(), key=lambda kv: -len(kv[1])):
            shown = ", ".join(names[:8]) + (f", … +{len(names) - 8}" if len(names) > 8 else "")
            print(f"  {kind} ({len(names)}): {shown}")
        print(ui.dim("  → review quickly: concept triage   ·   "
                     "bulk: concept confirm --all / --all-kind <kind>"))

    if briefing["proposals"]:
        rows = briefing["proposals"]
        print(ui.bold(f"\nProposals against settled knowledge ({len(rows)}):"))
        for row in rows[:8]:
            print(f"  {ui.dim('[' + row['id'][:8] + ']')} "
                  f"({ui.cyan(row['kind'])}) {row['summary']}")
        if len(rows) > 8:
            print(ui.dim(f"  … and {len(rows) - 8} more."))
        print(ui.dim("  → proposal review · proposal accept|dismiss <id>"))

    if briefing["inferred_edges"]:
        edges = briefing["inferred_edges"]
        print(ui.bold(f"\nInferred relationships awaiting your confirmation ({len(edges)}):"))
        for edge in edges[:10]:
            support = f"  ×{edge['support']}" if edge["relation"] == "co_occurs" else ""
            print(
                f"  {ui.dim('[' + edge['id'][:8] + ']')} "
                f"{edge['from_name']} —{ui.cyan(edge['relation'])}→ {edge['to_name']}"
                + ui.dim(support)
            )
        if len(edges) > 10:
            print(ui.dim(f"  … and {len(edges) - 10} more (concept list shows all)."))
        print(ui.dim("  → review quickly: concept triage --edges   ·   "
                     "one-off: concept confirm <id> <relation> / concept reject-edge <id>"))

    if briefing["contradictions"]:
        print(ui.bold("\nContradictions (rejected suggestions since last session):"))
        for item in briefing["contradictions"]:
            note = f" — \"{item['explanation']}\"" if item["explanation"] else ""
            print(f"  • {ui.yellow('Rejected:')} {ui.shorten(item['suggestion'], 80)}{note}")

    if briefing["outstanding_questions"]:
        print(ui.bold("\nOutstanding questions:"))
        for q in briefing["outstanding_questions"]:
            print(
                f"  {ui.dim('[' + q['policy_id'][:8] + ']')} "
                f"On \"{ui.shorten(q['statement'], 50)}\": {q['question']}"
            )
        print(ui.dim("  → answer: policy answer <id> \"...\""))

    if briefing["active_intents"]:
        print(ui.bold("\nActive intents (carried over):"))
        for intent in briefing["active_intents"]:
            print(f"  {ui.dim('[' + intent['id'][:8] + ']')} {intent['statement']}")

    if briefing["toc_unlisted"]:
        print(ui.yellow(
            "\nFiles missing from toc.toml (reading order falls back to "
            "alphabetical for them): " + ", ".join(briefing["toc_unlisted"])
        ))
        print(ui.dim("  → add them to toc.toml in their true reading position."))

    if briefing["focus_areas"]:
        areas = briefing["focus_areas"]
        print(ui.bold(f"\nSuggested focus areas (declared but unrealized concepts):"))
        for area in areas[:8]:
            related = "; ".join(
                f"{r['name']} ({r['relation']})" for r in area["related"][:4]
            )
            extra = ui.dim(f" — related: {related}") if related else ""
            print(f"  • Introduce '{ui.bold(area['node']['name'])}'{extra}")
        if len(areas) > 8:
            print(ui.dim(f"  … and {len(areas) - 8} more."))


def cmd_briefing(args):
    db = _open_db(args)
    manuscript = _manuscript(db, args)
    _print_briefing(db, manuscript)


def _intent_preview(db: Database, manuscript: dict, statement: str) -> None:
    """Deterministic, zero-token feedback right after declaration: matched
    concepts with their state, or a fuzzy 'did you mean' when nothing
    matches. Never blocks the declaration (§9.2 — it is already recorded)."""
    import difflib
    import re as _re

    from .analysis import find_precedents

    mid = manuscript["id"]
    nodes = [dict(n) for n in db.all(
        "SELECT * FROM concept_nodes WHERE manuscript_id = ? AND status != 'retired'",
        (mid,),
    )]
    if not nodes:
        return
    matched = [n for n in nodes
               if any(cg.concept_pattern(nm).search(statement)
                      for nm in cg.node_names(n))]
    if not matched:
        names = [n["name"] for n in nodes]
        lower_map = {n.lower(): n for n in names}
        suggestions = []
        for word in _re.findall(r"[A-Za-z][A-Za-z-]{3,}", statement):
            for hit in difflib.get_close_matches(word.lower(), list(lower_map), n=1, cutoff=0.7):
                if lower_map[hit] not in suggestions:
                    suggestions.append(lower_map[hit])
        warning = "Note: this intent names no concept in the graph — guidance will offer only standing findings."
        if suggestions:
            warning += " Did you mean: " + ", ".join(f"'{s}'" for s in suggestions[:3]) + "?"
        print(ui.yellow(warning))
        return
    print(ui.bold("Preview:"))
    for node in matched[:5]:
        if node["status"] == "realized":
            print(f"  • {node['name']} — realized (in {node['introduced_in']})")
        else:
            neighbors = db.all(
                "SELECT * FROM concept_edges WHERE manuscript_id = ? "
                "AND status NOT IN ('rejected', 'retired') AND relation != 'co_occurs' "
                "AND (from_node = ? OR to_node = ?)",
                (mid, node["id"], node["id"]),
            )
            precedents = find_precedents(db, mid, node["id"], limit=1)
            extras = [f"{len(neighbors)} relationship(s)"]
            if precedents:
                extras.append(f"precedent available ({precedents[0]['label']})")
            print(f"  • {node['name']} — not yet written ({', '.join(extras)})")
    print(ui.dim("  → 'guide' for full suggestions with drafts."))


def cmd_intent(args):
    db = _open_db(args)
    manuscript = _manuscript(db, args)
    if args.action == "declare":
        row = ses.declare_intent(db, manuscript["id"], args.statement)
        print(f"Declared intent [{row['id'][:8]}]: {args.statement}")
        _intent_preview(db, manuscript, args.statement)
        if not ses.active_session(db, manuscript["id"]):
            print("note: no active session — the intent is recorded but no episode was opened.")
    elif args.action == "complete":
        intent = _find_by_prefix(db, "declared_intents", args.id, manuscript["id"])
        if intent["status"] != "active":
            sys.exit(f"error: intent is already {intent['status']}.")
        ses.complete_intent(db, intent, args.outcome)
        print(f"Intent completed: {intent['statement']}")
        if args.outcome:
            print(f"Outcome: {args.outcome}")
        _analyze_closed_episodes(db, manuscript, args)
    elif args.action == "abandon":
        intent = _find_by_prefix(db, "declared_intents", args.id, manuscript["id"])
        if intent["status"] != "active":
            sys.exit(f"error: intent is already {intent['status']}.")
        ses.abandon_intent(db, intent, args.outcome)
        print(f"Intent abandoned: {intent['statement']}")
        print(ui.dim("The declaration stays in history; it no longer drives guidance."))
    else:  # list
        rows = db.all(
            "SELECT * FROM declared_intents WHERE manuscript_id = ? ORDER BY created_at",
            (manuscript["id"],),
        )
        if not rows:
            print("No declared intents.")
        for row in rows:
            outcome = f" → {row['outcome']}" if row["outcome"] else ""
            print(f"[{row['id'][:8]}] ({row['status']}) {row['statement']}{outcome}")


def cmd_critique(args):
    db = _open_db(args)
    manuscript = _manuscript(db, args)
    mid = manuscript["id"]
    if args.action == "import":
        if not args.target:
            sys.exit("usage: critique import <manifest.json>")
        manifest = json.loads(Path(args.target).read_text())
        result = crit.import_manifest(db, mid, manifest)
        print(f"Imported {result['intents']} intent(s) and "
              f"{result['style_elements']} style element(s) as proposed "
              f"({result['skipped']} already present, skipped).")
        for err in result["errors"]:
            print(ui.yellow(f"warning: {err}"))
        if result["intents"] or result["style_elements"]:
            print(ui.dim("Nothing is law yet — triage with 'critique triage' "
                         "(the global sitting first, then per-essay)."))
    elif args.action == "status":
        rows = crit.status(db, mid)
        if not rows:
            print("No critique material imported.")
        for r in rows:
            i, e = r["intents"], r["elements"]
            print(f"{r['name']}:")
            print(f"  intents:  {i['proposed']} proposed | "
                  f"{i['accepted']} accepted | {i['rejected']} rejected")
            print(f"  elements: {e['proposed']} proposed | "
                  f"{e['accepted']} accepted | {e['rejected']} rejected")
        _critique_pass_status(db, manuscript)
    elif args.action == "show":
        _critique_show(db, mid, args)
    elif args.action == "reason":
        _critique_reason(db, mid, args)
    elif args.action == "reopen":
        _critique_reopen(db, mid, args)
    elif args.action == "run":
        _critique_run(db, manuscript, args)
    elif args.action == "edits":
        _critique_edits_list(db, manuscript, args)
    elif args.action == "write":
        _critique_write(db, manuscript, args)
    elif args.action == "resolve":
        _critique_resolve_essay(db, manuscript, args)
    elif args.action == "rollback":
        _critique_rollback(db, manuscript, args)
    elif args.action == "list":
        queue = _critique_queue(db, mid, args.scope)
        if not queue:
            print("No proposed critique items"
                  + (f" scoped to {args.scope}." if args.scope else "."))
            return
        for n, (kind, item) in enumerate(queue, 1):
            print(f"{ui.cyan(f'{n}.')} {_critique_item_line(kind, item)}")
        print(ui.dim("\nBulk verdicts by number (same --scope!) or id prefix: "
                     "critique triage --accept 1 2 5 | --reject 3 4 "
                     "--reason \"…\" | --revise 6 --text \"…\""))
    else:  # triage
        if args.edits:
            _critique_edits_triage(db, manuscript, args)
        else:
            _critique_triage(db, manuscript, args)


# ------------------------------------------------ settled-item verbs

def _critique_settled(db: Database, mid: str, token: str) -> tuple[str, dict]:
    """Any critique item, settled or not, by id prefix."""
    for kind, table in (("intent", "declared_intents"),
                        ("element", "style_elements")):
        rows = db.all(
            f"SELECT * FROM {table} WHERE manuscript_id = ? AND id LIKE ? "
            "AND source_id IS NOT NULL", (mid, f"%{token}%"))
        if len(rows) == 1:
            return kind, dict(rows[0])
        if len(rows) > 1:
            sys.exit(f"error: '{token}' is ambiguous ({len(rows)} matches).")
    sys.exit(f"error: no critique item matching '{token}'.")


def _critique_show(db: Database, mid: str, args) -> None:
    """A settled item's status, verdict, and reason — the 'what did I
    decide?' question, without SQL."""
    if not (args.target or args.query):
        sys.exit("usage: critique show <id-prefix> | critique show --query TEXT")
    if args.query:
        q = args.query.lower()
        rows = []
        for table, kind in (("declared_intents", "intent"),
                            ("style_elements", "element")):
            for r in db.all(f"SELECT * FROM {table} WHERE manuscript_id = ? "
                            "AND source_id IS NOT NULL", (mid,)):
                if q in r["statement"].lower():
                    rows.append((kind, dict(r)))
        if not rows:
            print(f"No critique items match '{args.query}'.")
        for kind, item in rows:
            reason = item.get("outcome") if kind == "intent" else \
                loads(item["metadata"], {}).get("rejection_reason")
            status = {"proposed": ui.dim, "active": ui.green,
                      "rejected": ui.yellow}.get(item["status"], str)(
                          item["status"])
            print(f"{_critique_label(kind, item)} {status}")
            print(ui.wrap(item["statement"], indent="  "))
            if reason:
                print(ui.dim(ui.wrap("reason: " + reason, indent="  ")))
        return
    kind, item = _critique_settled(db, mid, args.target)
    print(_critique_label(kind, item))
    print(ui.wrap(item["statement"], indent="  "))
    color = {"proposed": ui.dim, "active": ui.green, "completed": ui.green,
             "rejected": ui.yellow}.get(item["status"], str)
    print("  status: " + color(item["status"]))
    reason = item.get("outcome") if kind == "intent" else \
        loads(item["metadata"], {}).get("rejection_reason")
    if reason:
        print("  reason: " + reason)
    lineage = loads(item["metadata"], {}).get("lineage")
    if lineage:
        print(ui.dim(f"  lineage: {json.dumps(lineage)}"))


def _critique_reason(db: Database, mid: str, args) -> None:
    """Amend a settled item's recorded reason (stray keystrokes and
    wholesale markers happen; the evidence stream must be correctable)."""
    if not (args.target and args.text):
        sys.exit('usage: critique reason <id-prefix> --text "the real why"')
    kind, item = _critique_settled(db, mid, args.target)
    if item["status"] != "rejected":
        sys.exit(f"error: only rejected items carry a reason (this one is "
                 f"{item['status']}).")
    if kind == "intent":
        db.update("declared_intents", item["id"], {"outcome": args.text})
    else:
        meta = loads(item["metadata"], {})
        meta["rejection_reason"] = args.text
        db.update("style_elements", item["id"],
                  {"metadata": json.dumps(meta)})
    ev = ko_fields("ev")
    ev.update(manuscript_id=mid, episode_id=None,
              evidence_type="critique_triage", signal="reason_amended",
              target=f"{item['statement'][:120]} — {args.text}"[:200],
              supports_policy=None, weight="high")
    db.insert("evidence", ev)
    print(ui.green("reason amended") + f" [{item['id'][:8]}]")


def _critique_reopen(db: Database, mid: str, args) -> None:
    """Send a settled item back to proposed."""
    if not args.target:
        sys.exit("usage: critique reopen <id-prefix>")
    kind, item = _critique_settled(db, mid, args.target)
    table = "declared_intents" if kind == "intent" else "style_elements"
    db.update(table, item["id"], {"status": "proposed"})
    print(ui.cyan("reopened") + f" [{item['id'][:8]}] — proposed again")


# --------------------------------------------------- the edit pass

def _critique_pass_row(db: Database, manuscript: dict):
    from . import passes

    p = passes.active_pass(db, manuscript["id"])
    if not p:
        # The pass belongs to the (single) imported critique source.
        srcs = db.all("SELECT id FROM sources WHERE kind = 'critic'")
        src = srcs[0]["id"] if srcs else db.source("system")
        p = passes.ensure_pass(db, manuscript["id"], src)
    return p


def _critique_pass_status(db: Database, manuscript: dict) -> None:
    from . import passes

    st = passes.status(db, manuscript)
    if not st["pass"]:
        print(ui.dim("No edit pass yet — 'critique run <essay>' starts one."))
        return
    print(ui.bold("Edit pass") + ui.dim(f"  cursor → {st['cursor_file']}"))
    colors = {"pending": ui.dim, "proposed": ui.cyan, "triaged": ui.cyan,
              "written": ui.yellow, "resolved": ui.green, "skipped": ui.dim}
    for row in st["essays"]:
        counts = " ".join(f"{k}:{v}" for k, v in row["threads"].items())
        marker = "▶" if row["at_cursor"] else " "
        print(f"  {marker} {row['file']:<22} "
              f"{colors[row['state']](row['state'])}"
              + (ui.dim(f"  {counts}") if counts else ""))


def _critique_run(db: Database, manuscript: dict, args) -> None:
    from . import passes

    if not args.target:
        sys.exit("usage: critique run <essay.md> [--force]")
    file = args.target
    p = _critique_pass_row(db, manuscript)
    config = _load_config(args)
    try:
        ctx = passes.build_context(db, manuscript, file, p, force=args.force)
    except (RuntimeError, LookupError) as err:
        sys.exit(f"error: {err}")
    if ctx["forced"]:
        print(ui.yellow("--force: running WITHOUT the unconfirmed items "
                        "(they are not in the pass)."))
    llm = passes.editor_llm(config)
    if not llm.enabled:
        sys.exit("error: the LLM is disabled ([llm] enabled = false).")
    print(ui.dim(f"editor model: {llm.model} — {len(ctx['paragraphs'])} "
                 f"paragraphs, {len(ctx['intents'])} intent(s), "
                 f"{len(ctx['policies'])} polic(ies), "
                 f"{len(ctx['before'])} before / {len(ctx['after'])} after "
                 "summaries"))
    try:
        result = passes.run_editor(llm, ctx)
    except (passes.ContractError, RuntimeError) as err:
        sys.exit(f"error: {err}")
    out = passes.stage(db, manuscript, p, file, result)
    n_rep = sum(1 for t in out["staged"] if t["proposed_old"])
    n_ins = len(out["staged"]) - n_rep
    print(ui.green(f"staged {n_rep} replacement(s) and {n_ins} insertion(s)")
          + ui.dim(f"; {len(out['suggestions'])} suggestion(s) filed as "
                   "proposed intents"))
    line = llm.stats_line()
    if line:
        print(ui.dim(line))
    print(ui.dim(f"Next: critique edits {file}  |  critique triage --edits "
                 f"{file}"))


def _edit_threads(db: Database, manuscript: dict, file: str) -> list[dict]:
    from . import passes
    return passes.staged_threads(db, manuscript["id"], file)


def _render_edit(n: int, t: dict, total: int) -> None:
    """One staged edit: id/state header, red-struck old, green new, why."""
    meta = loads(t.get("metadata"), {}) or {}
    kind = meta.get("kind", "replace")
    anchor = meta.get("anchor_paragraph")
    state = {"proposed": ui.dim, "accepted": ui.green,
             "rejected": ui.yellow}.get(t["state"], str)(t["state"])
    where = (f"after ¶{anchor}" if kind == "insert" else f"¶{anchor}")
    print(f"{ui.cyan(f'{n}.')} {ui.dim('[' + t['id'][:8] + ']')} {ui.bold(where)} "
          f"{ui.dim(kind)}  {state}"
          + (ui.dim(f"  → {meta['intent_id'][:11]}") if meta.get("intent_id")
             else ""))
    if t["proposed_old"]:
        print(ui.red_strike(ui.wrap(t["proposed_old"], indent="    ")))
    print(ui.green(ui.wrap(t["proposed_new"], indent="    ")))
    if t["note"]:
        print(ui.dim(ui.wrap("why: " + t["note"], indent="    ")))


def _critique_edits_list(db: Database, manuscript: dict, args) -> None:
    if not args.target:
        sys.exit("usage: critique edits <essay.md>")
    threads = _edit_threads(db, manuscript, args.target)
    if not threads:
        print(f"No staged edits for {args.target} — 'critique run "
              f"{args.target}' proposes some.")
        return
    for n, t in enumerate(threads, 1):
        _render_edit(n, t, len(threads))
        print()
    print(ui.dim("Verdicts: critique triage --edits <essay> [--accept N… "
                 "--reject N… --reason … --revise N --text … --undo N…]"))


def _critique_edits_triage(db: Database, manuscript: dict, args) -> None:
    from . import passes

    file = args.edits
    mid = manuscript["id"]
    p = _critique_pass_row(db, manuscript)
    threads = _edit_threads(db, manuscript, file)
    if not threads:
        print(f"No staged edits for {file}.")
        return

    def resolve(token: str) -> dict:
        if token.isdigit():
            n = int(token)
            if not 1 <= n <= len(threads):
                sys.exit(f"error: no edit {n} (there are {len(threads)}).")
            return threads[n - 1]
        hits = [t for t in threads if token in t["id"]]
        if len(hits) != 1:
            sys.exit(f"error: '{token}' matches {len(hits)} edits.")
        return hits[0]

    if args.accept or args.reject or args.revise or args.undo:
        if args.reject and not args.reason:
            sys.exit("error: --reject requires --reason (verbatim evidence).")
        for tok in args.accept or []:
            passes.verdict(db, mid, resolve(tok), "accept")
            print(ui.green(f"accepted {tok}"))
        for tok in args.reject or []:
            passes.verdict(db, mid, resolve(tok), "reject", args.reason)
            print(ui.yellow(f"rejected {tok}"))
        if args.revise:
            if not args.text:
                sys.exit("error: --revise requires --text.")
            passes.verdict(db, mid, resolve(args.revise), "revise", args.text)
            print(ui.green(f"revised & accepted {args.revise}"))
        for tok in args.undo or []:
            passes.verdict(db, mid, resolve(tok), "undo")
            print(ui.cyan(f"undone {tok} → proposed"))
        passes.mark_triaged(db, p, file)
        return

    print(ui.bold(f"{len(threads)} staged edit(s) for {file}.") + "  " + ui.dim(
        "[k]eep/accept  [r]eject (asks why)  [e]dit+accept  [u]ndo  "
        "[s]kip  [x] quit"))
    for n, t in enumerate(threads, 1):
        print()
        _render_edit(n, t, len(threads))
        while True:
            try:
                choice = input("  k/r/e/u/s/x> ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print()
                choice = "x"
            if choice in ("k", "r", "e", "u", "s", "x", "q"):
                break
            if choice == "a":
                print(ui.dim("  ? 'k' accepts here"))
        if choice in ("x", "q"):
            break
        if choice == "s":
            continue
        try:
            if choice == "k":
                passes.verdict(db, mid, t, "accept")
                print(ui.green("  accepted"))
            elif choice == "u":
                passes.verdict(db, mid, t, "undo")
                print(ui.cyan("  undone → proposed"))
            elif choice == "r":
                reason = ui.input_text(ui.dim("  why (verbatim)> ")).strip()
                passes.verdict(db, mid, t, "reject", reason or None)
                print(ui.yellow("  rejected"))
            elif choice == "e":
                new = ui.input_text(ui.dim("  your wording> ")).strip()
                if not new:
                    print(ui.dim("  no text — skipped"))
                    continue
                passes.verdict(db, mid, t, "revise", new)
                print(ui.green("  revised & accepted") + ui.dim(
                    " (the diff is evidence)"))
        except (EOFError, KeyboardInterrupt):
            print()
            break
        except ValueError as err:
            print(ui.yellow(f"  {err}"))
    passes.mark_triaged(db, p, file)
    left = sum(1 for t in _edit_threads(db, manuscript, file)
               if t["state"] == "proposed")
    print(ui.dim(f"\n{left} still undecided. Next: critique write {file} "
                 "(accepted edits → Doc as pending forms)."))


def _critique_write(db: Database, manuscript: dict, args) -> None:
    """Diff-write: accepted edits → the essay's Doc tab as pending forms.
    Pins the pre-pass version. Local keeps OLD."""
    from . import gdocs, passes

    if not args.target:
        sys.exit("usage: critique write <essay.md>")
    file = args.target
    mid = manuscript["id"]
    p = _critique_pass_row(db, manuscript)
    threads = _edit_threads(db, manuscript, file)
    accepted = [t for t in threads if t["state"] == "accepted"]
    if not accepted:
        sys.exit(f"error: no accepted edits for {file} — triage first.")
    # Pristine check + pin: the pass row remembers what to roll back to.
    from .revisions import collect_revision
    version = collect_revision(db, manuscript, session_id=None,
                               source="critique-pin")
    if version is None:
        row = db.one("SELECT id FROM manuscript_versions WHERE manuscript_id "
                     "= ? ORDER BY version_no DESC LIMIT 1", (mid,))
        version = dict(row) if row else None
    if version:
        passes.pin_version(db, p, file, version["id"])
    config = _load_config(args)
    try:
        service = gdocs.get_service(config, args.workspace, interactive=True)
        docs_service = gdocs.get_docs_service(config, args.workspace,
                                              interactive=True)
    except ValueError as err:
        sys.exit(f"error: {err}")
    # Compose locally first — this is where a drifted paragraph fails
    # loudly, before any Doc write.
    text = (Path(manuscript["path"]) / file).read_text(encoding="utf-8")
    try:
        passes.compose_marked_text(text, threads)
    except ValueError as err:
        sys.exit(f"error: {err}")
    result = gdocs.critique_diff_write(db, manuscript, file, threads,
                                       service, docs_service)
    for t in result["written"]:
        db.update("doc_threads", t["id"], {"state": "written"})
    for t, why in result["failed"]:
        print(ui.yellow(f"  could not write [{t['id'][:8]}]: {why}"))
    if result["written"]:
        passes.set_essay_state(db, p, file, "written")
    print(ui.green(f"wrote {len(result['written'])} pending form(s)")
          + ui.dim(f" to {result['url']}"))
    print(ui.dim("Read and post-edit the {{new}} halves in the Doc; then "
                 f"'critique resolve {file}' makes them final. Local keeps "
                 "the old text until then."))


def _critique_resolve_essay(db: Database, manuscript: dict, args) -> None:
    """The confirmation gate (design §6.3 step 5). Explicit only."""
    from . import gdocs, passes, summaries as sums

    if not args.target:
        sys.exit("usage: critique resolve <essay.md>")
    file = args.target
    mid = manuscript["id"]
    p = _critique_pass_row(db, manuscript)
    written = passes.staged_threads(db, mid, file, states=("written",))
    if not written:
        sys.exit(f"error: nothing written to the Doc for {file} — "
                 "'critique write' first (or nothing to resolve).")
    config = _load_config(args)
    try:
        service = gdocs.get_service(config, args.workspace, interactive=True)
        docs_service = gdocs.get_docs_service(config, args.workspace,
                                              interactive=True)
    except ValueError as err:
        sys.exit(f"error: {err}")
    marked = gdocs.critique_tab_text(db, manuscript, file, docs_service)
    final, forms = passes.final_text_from_marked(marked, written=written)
    # Apply locally: the author's post-edits win.
    path = Path(manuscript["path"]) / file
    normalized = gdocs.normalize_markdown(final)
    path.write_text(normalized if normalized.endswith("\n")
                    else normalized + "\n", encoding="utf-8")
    diffs = passes.record_resolution(db, mid, file, forms)
    # Strip the forms from the tab: a plain push of the now-final local
    # file rebuilds the tab clean (read-back proof inside push).
    gdocs.push_doc(db, manuscript, file, service=service,
                   docs_service=docs_service)
    print(ui.green(f"resolved {file}: {len(forms)} form(s) made final")
          + (ui.dim(f", {len(diffs)} modified acceptance(s) recorded")
             if diffs else ""))
    for d in diffs:
        print(ui.dim(f"  «{gdocs.clamp(d['proposal'])}» → "
                     f"«{gdocs.clamp(d['final'])}»"))
    # Collect, then rebuild this essay's summary at the gate.
    with contextlib.redirect_stdout(io.StringIO()):
        api.collect(db, manuscript, config, source="critique-resolve")
    llm = sums.summarizer_llm(config)
    if llm.enabled:
        try:
            sums.rebuild_one(db, manuscript, file, llm)
            print(ui.dim("summary rebuilt; downstream marked upstream_stale"))
        except Exception as err:  # noqa: BLE001
            print(ui.yellow(f"summary rebuild failed ({err}) — run "
                            f"'summarize rebuild {file}'"))
    passes.set_essay_state(db, p, file, "resolved")
    order = [f for f, _ in sums.units(manuscript)]
    if file in order:
        idx = order.index(file)
        if idx >= p["cursor"]:
            db.update("critique_passes", p["id"], {"cursor": idx + 1})
    if diffs:
        _critique_learnings(db, manuscript, diffs, config)
    nxt = order[idx + 1] if file in order and idx + 1 < len(order) else None
    print(ui.dim(f"Next essay: {nxt} — 'critique run {nxt}' when you say so."
                 if nxt else "That was the last essay in reading order."))


def _critique_learnings(db: Database, manuscript: dict, diffs: list[dict],
                        config: dict) -> None:
    """Modified acceptances feed the margin-learnings duty: at ≥2 in one
    resolve, surface a pattern candidate through the scoped distiller."""
    if len(diffs) < 2:
        return
    from . import placement
    from .llm import LLMClient

    explanations = [(d["file"], f"proposal «{d['proposal'][:120]}» became "
                                f"«{d['final'][:120]}»") for d in diffs]
    llm = LLMClient(config)
    try:
        candidate = placement.distill_batch(db, manuscript, explanations, llm)
    except Exception:  # noqa: BLE001
        candidate = None
    if candidate:
        print(ui.yellow("Pattern candidate from your post-edits: "
                        + ui.shorten(candidate.get("statement", ""), 70)))


def _critique_rollback(db: Database, manuscript: dict, args) -> None:
    """Restore the pinned pre-pass state for one essay: local file from
    the pinned version, Doc tab re-pushed clean; verdicts stay as
    evidence; threads → withdrawn."""
    from . import gdocs, passes

    if not args.target:
        sys.exit("usage: critique rollback <essay.md>")
    file = args.target
    mid = manuscript["id"]
    p = _critique_pass_row(db, manuscript)
    pin = passes.pinned_version(p, file)
    if not pin:
        sys.exit(f"error: no pinned version for {file} — nothing was written.")
    row = db.one("SELECT files FROM manuscript_versions WHERE id = ?", (pin,))
    if not row:
        sys.exit("error: pinned version is missing from the record.")
    files = loads(row["files"], {})
    if file not in files:
        sys.exit(f"error: pinned version has no {file}.")
    (Path(manuscript["path"]) / file).write_text(files[file], encoding="utf-8")
    for t in passes.staged_threads(db, mid, file,
                                   states=("written", "accepted", "proposed",
                                           "rejected")):
        db.update("doc_threads", t["id"], {"state": "withdrawn"})
    config = _load_config(args)
    try:
        service = gdocs.get_service(config, args.workspace, interactive=True)
        docs_service = gdocs.get_docs_service(config, args.workspace,
                                              interactive=True)
        gdocs.push_doc(db, manuscript, file, service=service,
                       docs_service=docs_service)
        print(ui.dim("Doc tab restored."))
    except ValueError as err:
        print(ui.yellow(f"local restored; Doc not reachable ({err}) — "
                        f"'doc push {file}' when it is."))
    passes.set_essay_state(db, p, file, "pending")
    print(ui.green(f"rolled back {file}") + ui.dim(
        " — verdicts kept as evidence; re-run when ready."))


def _critique_label(kind: str, item: dict) -> str:
    """The colored identity row: cyan id, dim provenance label."""
    meta = loads(item["metadata"], {}).get("critique", {})
    where = item.get("scope") or item.get("file") or (
        "guide" if item.get("guide_id") else "manuscript")
    label = f"{meta.get('unit', '?')} #{meta.get('ordinal', '?')}"
    return (ui.cyan(f"[{item['id'][:8]}]")
            + ui.dim(f" ({label} | {where} | {kind})"))


def _critique_item_line(kind: str, item: dict) -> str:
    return f"{_critique_label(kind, item)} {item['statement']}"


def _critique_resolve(db: Database, mid: str, prefix: str) -> tuple[str, dict]:
    for kind, table in (("intent", "declared_intents"),
                        ("element", "style_elements")):
        rows = db.all(
            f"SELECT * FROM {table} WHERE manuscript_id = ? "
            "AND status = 'proposed' AND id LIKE ?",
            (mid, f"%{prefix}%"))
        if len(rows) == 1:
            return kind, dict(rows[0])
        if len(rows) > 1:
            sys.exit(f"error: '{prefix}' is ambiguous ({len(rows)} matches).")
    sys.exit(f"error: no proposed critique item matching '{prefix}'.")


def _critique_verdict(db: Database, mid: str, kind: str, item: dict,
                      verdict: str, text: str | None = None) -> None:
    fns = {
        ("intent", "accept"): crit.accept_intent,
        ("intent", "reject"): crit.reject_intent,
        ("intent", "revise"): crit.revise_intent,
        ("element", "accept"): crit.accept_element,
        ("element", "reject"): crit.reject_element,
        ("element", "revise"): crit.revise_element,
    }
    fn = fns[(kind, verdict)]
    if verdict == "accept":
        fn(db, mid, item)
    else:
        fn(db, mid, item, text)


def _critique_queue(db: Database, mid: str,
                    scope: str | None) -> list[tuple[str, dict]]:
    return crit.queue(db, mid, scope)


def _critique_triage(db: Database, manuscript: dict, args) -> None:
    mid = manuscript["id"]
    if args.accept or args.reject or args.revise:
        if args.reject and not args.reason:
            sys.exit("error: --reject requires --reason (the author's "
                     "words are the evidence).")
        if args.revise and not args.text:
            sys.exit("error: --revise requires --text (the author's "
                     "replacement wording).")
        # One snapshot for every number in this invocation: verdicts must
        # not renumber the queue out from under later tokens.
        queue = _critique_queue(db, mid, args.scope)

        def resolve(token: str) -> tuple[str, dict]:
            if token.isdigit():
                n = int(token)
                if not 1 <= n <= len(queue):
                    sys.exit(f"error: no item {n} — the pending list has "
                             f"{len(queue)} item(s) (did the --scope match "
                             "your 'critique list' run?).")
                return queue[n - 1]
            return _critique_resolve(db, mid, token)

        for token in args.accept or []:
            kind, item = resolve(token)
            _critique_verdict(db, mid, kind, item, "accept")
            print(ui.green(f"accepted [{item['id'][:8]}]")
                  + f" {item['statement'][:70]}")
        for token in args.reject or []:
            kind, item = resolve(token)
            _critique_verdict(db, mid, kind, item, "reject", args.reason)
            print(ui.yellow(f"rejected [{item['id'][:8]}]")
                  + f" {item['statement'][:70]}")
        if args.revise:
            kind, item = resolve(args.revise)
            _critique_verdict(db, mid, kind, item, "revise", args.text)
            print(ui.green(f"revised & accepted [{item['id'][:8]}]")
                  + ui.dim(" (provenance is now yours)"))
        return
    queue = _critique_queue(db, mid, args.scope)
    if not queue:
        print("Nothing to triage.")
        return
    # Keys match the concept/illustration loops: k = keep/accept ('a' is
    # reserved for 'alias' in concept triage — same finger, same meaning
    # everywhere), x/q both quit.
    print(ui.bold(f"{len(queue)} proposed item(s).") + "  " + ui.dim(
        "[k]eep/accept  [r]eject (asks why)  [e]dit+accept  [s]kip  "
        "[x] quit"))
    verdicts = 0
    for kind, item in queue:
        print()
        print(_critique_label(kind, item))
        print(ui.wrap(item["statement"], indent="  "))
        try:
            while True:
                # Plain prompt: ANSI codes inside an input() prompt make
                # readline miscount the line and overwrite on wrap.
                choice = input("  k/r/e/s/x> ").strip().lower()
                if choice in ("k", "r", "e", "s", "q", "x"):
                    break
                if choice == "a":
                    print(ui.dim("  ? 'k' accepts here (concept triage "
                                 "reserves 'a' for alias)"))
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if choice in ("q", "x"):
            break
        if choice == "s":
            continue
        if choice == "r":
            try:
                reason = ui.input_text(
                    ui.dim("  why (verbatim, required)> ")).strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not reason:
                print(ui.dim("  no reason given — skipped instead."))
                continue
            _critique_verdict(db, mid, kind, item, "reject", reason)
            print(ui.yellow("  rejected") + ui.dim(" (reason kept verbatim)"))
        elif choice == "e":
            try:
                new_text = ui.input_text(ui.dim("  your wording> ")).strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not new_text:
                print(ui.dim("  no text given — skipped instead."))
                continue
            _critique_verdict(db, mid, kind, item, "revise", new_text)
            print(ui.green("  revised & accepted")
                  + ui.dim(" (provenance is now yours; critic's original "
                           "kept as lineage)"))
        else:
            _critique_verdict(db, mid, kind, item, "accept")
            print(ui.green("  accepted"))
        verdicts += 1
    pend = crit.pending(db, mid, scope=args.scope)
    remaining = len(pend["intents"]) + len(pend["elements"])
    print(f"\n{verdicts} verdict(s) recorded; {remaining} still proposed.")


def cmd_summarize(args):
    from . import summaries as sums

    db = _open_db(args)
    manuscript = _manuscript(db, args)
    if args.action == "status":
        rows = sums.status(db, manuscript)
        counts: dict[str, int] = {}
        for r in rows:
            counts[r["state"]] = counts.get(r["state"], 0) + 1
            color = {"fresh": ui.green, "stale": ui.yellow,
                     "upstream_stale": ui.dim, "missing": ui.yellow}[r["state"]]
            words = f"  {r['words']}w" if r["words"] else ""
            print(f"  {r['file']:<22} {color(r['state'])}{ui.dim(words)}")
        print(ui.dim("  " + ", ".join(f"{n} {s}" for s, n in counts.items())))
        return
    if args.action == "show":
        if not args.file:
            sys.exit("usage: summarize show <file>")
        row = db.one("SELECT * FROM essay_summaries WHERE manuscript_id = ? "
                     "AND file = ?", (manuscript["id"], args.file))
        if not row:
            sys.exit(f"no summary for {args.file} — run 'summarize rebuild'.")
        print(ui.bold(args.file) + ui.dim(f"  ({row['created_at'][:19]}Z)"))
        print(row["summary"])
        return
    # rebuild
    llm = sums.summarizer_llm(_load_config(args))
    if not llm.enabled:
        sys.exit("error: the LLM is disabled ([llm] enabled = false) — "
                 "summaries need it.")
    print(ui.dim(f"summarizer model: {llm.model}"))
    if args.file:
        row = sums.rebuild_one(db, manuscript, args.file, llm)
        print(ui.green(f"rebuilt {args.file}") + ui.dim(
            f" ({len(row['summary'].split())} words); downstream summaries "
            "marked upstream_stale"))
    else:
        result = sums.rebuild(
            db, manuscript, llm, only_missing_or_stale=not args.all,
            progress=lambda f: print(ui.dim(f"  summarizing {f}…")))
        print(ui.green(f"built {len(result['built'])}") + ui.dim(
            f", reused {len(result['reused'])} fresh"))
    line = llm.stats_line()
    if line:
        print(ui.dim(line))


def cmd_prompts(args):
    from . import prompt_registry as pr

    if args.action == "show":
        if not args.name:
            sys.exit("usage: prompts show <name>")
        try:
            p = pr.by_name(args.name)
        except LookupError as err:
            sys.exit(f"error: {err}")
        print(ui.bold(p.name) + ui.dim(f"  — {p.location}"))
        print(ui.dim(f"used by: {p.verbs}"))
        print()
        print(p.text())
        return
    print(ui.bold("LLM prompts") + ui.dim(
        " — file prompts: edit the .md/JSON artifact, no code change; inline prompts: "
        "edit the module constant (legacy; each migrates to a file when "
        "its verb is next touched)."))
    print()
    width = max(len(p.name) for p in pr.REGISTRY)
    for p in pr.REGISTRY:
        kind = ui.green("file  ") if p.artifact_kind == "file" else ui.dim("inline")
        print(f"  {ui.cyan(p.name.ljust(width))}  {kind}  {p.location}")
        print(f"  {' ' * width}  {ui.dim(p.purpose)}")
        print(f"  {' ' * width}  {ui.dim('used by: ' + p.verbs)}")
    print(ui.dim("\n'prompts show <name>' prints one in full."))


def _find_by_prefix(db: Database, table: str, prefix: str, manuscript_id: str) -> dict:
    rows = db.all(
        f"SELECT * FROM {table} WHERE manuscript_id = ? AND id LIKE ?",
        (manuscript_id, f"%{prefix}%"),
    )
    if not rows:
        sys.exit(f"error: no {table} row matching '{prefix}'.")
    if len(rows) > 1:
        sys.exit(f"error: '{prefix}' is ambiguous ({len(rows)} matches).")
    return dict(rows[0])


TRIAGE_KINDS = {
    "c": "concept", "o": "objection", "e": "example",
    "m": "metaphor", "q": "question", "h": "historical_reference",
    "t": "mathematical_construct",
}


def _prefix_choice(choice: str, options) -> tuple[str | None, list[str]]:
    """Resolve typed input against option names by unambiguous prefix.
    Prefixes need ≥2 characters so the single-letter command keys
    (k/r/f/a/s/x…) stay reserved. Returns (match, ambiguous_matches)."""
    if choice in options:
        return choice, []
    if len(choice) < 2:
        return None, []
    matches = sorted(o for o in options if o.startswith(choice))
    if len(matches) == 1:
        return matches[0], []
    return None, matches


def _unconfirmed_nodes(db: Database, mid: str) -> list[dict]:
    return [
        dict(node) for node in db.all(
            "SELECT * FROM concept_nodes WHERE manuscript_id = ? AND status != 'retired' "
            "ORDER BY created_at",
            (mid,),
        )
        if (meta := loads(node["metadata"], {})).get("origin") == "extracted"
        and not meta.get("confirmed")
    ]


def _confirm_node(db: Database, mid: str, node: dict, new_kind: str | None) -> None:
    action = "retype" if new_kind and new_kind != node["kind"] else "keep"
    triage_service.apply_one(
        db, {"id": mid}, "concepts", node, action,
        {"kind": new_kind} if action == "retype" else {})


def _retire_node(db: Database, mid: str, node: dict) -> int:
    result = triage_service.apply_one(
        db, {"id": mid}, "concepts", node, "retire")
    return result["edges_retired"]


@contextlib.contextmanager
def _ephemeral_history():
    """Keystrokes typed at inner prompts (triage keys, notes rewording)
    are working input, not commands — pop whatever readline recorded
    inside the block so the shell's up-arrow history stays clean."""
    if not sys.stdin.isatty() and "readline" not in sys.modules:
        yield
        return
    try:
        import readline
    except ImportError:
        yield
        return
    start = readline.get_current_history_length()
    try:
        yield
    finally:
        while readline.get_current_history_length() > start:
            readline.remove_history_item(
                readline.get_current_history_length() - 1)


@contextlib.contextmanager
def _concept_name_completion(db: Database, mid: str):
    """Tab-completion over live concept names and aliases, for prompts
    that read a concept name (triage's 'alias of>'). Names contain
    spaces, so the whole line is the completion unit; matching is
    case-insensitive prefix. Completer state is restored on exit."""
    if not sys.stdin.isatty():
        yield
        return
    try:
        import readline
    except ImportError:
        yield
        return

    matches: list[str] = []

    def complete(text: str, state: int):
        if state == 0:
            lowered = text.lower()
            names: set[str] = set()
            for row in db.all(
                "SELECT name, aliases FROM concept_nodes "
                "WHERE manuscript_id = ? AND status != 'retired'",
                (mid,),
            ):
                names.add(row["name"])
                names.update(loads(row["aliases"], []))
            matches[:] = sorted(
                n for n in names if n.lower().startswith(lowered))
        return matches[state] if state < len(matches) else None

    old_completer = readline.get_completer()
    old_delims = readline.get_completer_delims()
    readline.set_completer(complete)
    readline.set_completer_delims("\n")
    if getattr(readline, "backend", "") == "editline" \
            or "libedit" in (readline.__doc__ or ""):
        readline.parse_and_bind("bind ^I rl_complete")
    else:
        readline.parse_and_bind("tab: complete")
    try:
        yield
    finally:
        readline.set_completer(old_completer)
        readline.set_completer_delims(old_delims)


def _run_triage(db: Database, mid: str) -> None:
    nodes = _unconfirmed_nodes(db, mid)
    if not nodes:
        print("Nothing to triage — no unconfirmed extracted concepts.")
        return
    print(f"{len(nodes)} unconfirmed concept(s).")
    print(ui.dim(triage_service.CONCEPT_TRIAGE_HELP))
    import textwrap

    kept = retyped = retired = skipped = merged = 0
    for index, node in enumerate(nodes, start=1):
        where = f", in {node['introduced_in']}" if node["introduced_in"] else ""
        name_line = (f"{ui.dim(f'[{index}/{len(nodes)}]')} {ui.bold(node['name'])} "
                     f"({node['kind']}{where})")
        print(name_line)
        # Full notes, never truncated — triage judgments need the whole
        # definition, not a preview.
        note_lines = 0
        if node["notes"]:
            wrapped = textwrap.fill(
                node["notes"], width=76,
                initial_indent="    ", subsequent_indent="    ",
            )
            note_lines = wrapped.count("\n") + 1
            print(ui.dim(wrapped))

        def echo(suffix: str, clean: bool, note_lines: int = note_lines,
                 name_line: str = name_line) -> None:
            """Confirm the decision on the name line itself (TTY: rewrite it,
            keep the notes block, erase the keystroke line; otherwise print
            below)."""
            if ui.is_tty() and clean:
                print(f"\033[{note_lines + 2}A\r\033[2K{name_line} {suffix}")
                if note_lines:
                    print(f"\033[{note_lines}B", end="")
                print("\033[2K\r", end="", flush=True)
            else:
                print(f"  {suffix}")

        clean = True  # no reprompt lines between item and answer yet
        while True:
            try:
                choice = input("> ").strip().lower()
            except EOFError:
                choice = "x"
            if choice in ("k", "keep"):
                _confirm_node(db, mid, node, None)
                kept += 1
                echo(ui.green("(kept)"), clean)
                break
            if choice in ("r", "retire"):
                _retire_node(db, mid, node)
                retired += 1
                echo(ui.yellow("(retired)"), clean)
                break
            if choice in ("s", "skip", ""):
                skipped += 1
                echo(ui.dim("(skipped)"), clean)
                break
            if choice in ("a", "alias"):
                try:
                    with _concept_name_completion(db, mid):
                        canonical_name = input("  alias of> ").strip()
                except EOFError:
                    canonical_name = ""
                canonical = cg.get_concept(db, mid, canonical_name) \
                    if canonical_name else None
                if not canonical or canonical["id"] == node["id"]:
                    print(ui.dim("  ? name an existing, different concept"))
                    clean = False
                    continue
                result = triage_service.apply_one(
                    db, {"id": mid}, "concepts", dict(node), "alias",
                    {"canonical_id": canonical["id"]})
                merged += 1
                echo(ui.cyan(
                    f"(→ alias of '{canonical['name']}', "
                    f"{result['repointed']} edge(s) re-pointed, "
                    f"{result['dropped']} retired)"), clean)
                break
            if choice in ("n", "notes", "reword"):
                try:
                    new_notes = input("  notes> ").strip()
                except EOFError:
                    new_notes = ""
                if new_notes:
                    triage_service.apply_one(
                        db, {"id": mid}, "concepts", node, "reword_notes",
                        {"notes": new_notes})
                    node["notes"] = new_notes
                    print(f"  {ui.cyan('(notes updated)')}")
                    print(ui.dim(textwrap.fill(
                        new_notes, width=76,
                        initial_indent="    ", subsequent_indent="    ",
                    )))
                else:
                    print(ui.dim("  (unchanged)"))
                clean = False
                continue  # still undecided — choose k/r/s/kind for this concept
            if choice in ("x", "quit"):
                skipped += len(nodes) - index + 1
                print(f"Triage: kept {kept}, retyped {retyped}, retired {retired}, "
                      f"merged {merged}, skipped {skipped}.")
                return
            kind = TRIAGE_KINDS.get(choice)
            if not kind:
                kind, ambiguous = _prefix_choice(choice, cg.NODE_KINDS)
                if ambiguous:
                    print(ui.dim("  ? ambiguous: " + " / ".join(ambiguous)))
                    clean = False
                    continue
            if kind:
                _confirm_node(db, mid, node, kind)
                retyped += 1
                echo(ui.cyan(f"(→ {kind})"), clean)
                break
            print(ui.dim("  ? use k / r / s / a / x, a kind key (c o e m q h t), "
                         "or a kind prefix (e.g. syl)"))
            clean = False
    print(f"Triage: kept {kept}, retyped {retyped}, retired {retired}, "
          f"merged {merged}, skipped {skipped}.")


def _run_edge_triage(db: Database, mid: str) -> None:
    from .extraction import VALID_RELATIONS

    relations = sorted(VALID_RELATIONS)
    edges = [dict(e) for e in db.all(
        "SELECT * FROM concept_edges WHERE manuscript_id = ? AND status = 'inferred' "
        "ORDER BY created_at",
        (mid,),
    )]
    if not edges:
        print("Nothing to triage — no inferred relationships.")
        return
    print(f"{len(edges)} inferred relationship(s).")
    print(ui.dim(triage_service.EDGE_TRIAGE_HELP + ":"))
    print(ui.dim("  " + "  ".join(
        f"[{number}]{relation}" for number, relation in enumerate(relations, start=1)
    )))
    confirmed = retyped = rejected = skipped = merged = 0
    for index, edge in enumerate(edges, start=1):
        current = db.one("SELECT * FROM concept_edges WHERE id = ?", (edge["id"],))
        if not current or current["status"] != "inferred":
            continue  # settled as a side effect of an earlier merge this run
        edge = dict(current)
        from_id, to_id = edge["from_node"], edge["to_node"]
        from_name, to_name = cg.node_name(db, from_id), cg.node_name(db, to_id)
        support = ui.dim(f"  ×{edge['support']}") if edge["relation"] == "co_occurs" else ""
        print(f"{ui.dim(f'[{index}/{len(edges)}]')} {ui.bold(from_name)} "
              f"—{ui.cyan(edge['relation'])}→ {ui.bold(to_name)}{support}")
        flipped = False

        def settle(relation: str, *, retyped: bool = False) -> None:
            action = "flip" if flipped else ("retype" if retyped else "keep")
            params = {"relation": relation} if retyped else {}
            triage_service.apply_one(
                db, {"id": mid}, "edges", edge, action, params)

        while True:
            try:
                choice = input("> ").strip().lower()
            except EOFError:
                choice = "x"
            current_from = to_name if flipped else from_name
            current_to = from_name if flipped else to_name
            if choice in ("k", "keep", "confirm"):
                settle(edge["relation"])
                confirmed += 1
                description = f"{current_from} —{edge['relation']}→ {current_to}"
                print(f"  {ui.green(f'(confirmed: {description})')}")
                break
            if choice in ("r", "reject"):
                triage_service.apply_one(
                    db, {"id": mid}, "edges", edge, "reject")
                rejected += 1
                print(f"  {ui.yellow('(rejected)')}")
                break
            if choice in ("f", "flip"):
                flipped = not flipped
                print(ui.dim(f"  direction now: {current_to} → {current_from}"))
                continue
            if choice in ("a", "alias"):
                keep = input(f"  canonical? [1] {current_from}  [2] {current_to} "
                             "> ").strip()
                if keep not in ("1", "2"):
                    print(ui.dim("  ? 1 or 2"))
                    continue
                cf_id = to_id if flipped else from_id
                ct_id = from_id if flipped else to_id
                canon_id, dup_id = (cf_id, ct_id) if keep == "1" else (ct_id, cf_id)
                canonical = dict(db.one(
                    "SELECT * FROM concept_nodes WHERE id = ?", (canon_id,)))
                duplicate = dict(db.one(
                    "SELECT * FROM concept_nodes WHERE id = ?", (dup_id,)))
                result = triage_service.apply_one(
                    db, {"id": mid}, "edges", edge, "alias",
                    {"canonical_id": canonical["id"]})
                merged += 1
                summary = (f"(merged '{duplicate['name']}' into "
                           f"'{canonical['name']}': {result['repointed']} "
                           f"edge(s) re-pointed, {result['dropped']} retired)")
                print(f"  {ui.cyan(summary)}")
                break
            if choice in ("s", "skip", ""):
                skipped += 1
                break
            if choice in ("x", "quit"):
                skipped += len(edges) - index + 1
                print(f"Edge triage: confirmed {confirmed}, retyped {retyped}, "
                      f"rejected {rejected}, merged {merged}, skipped {skipped}.")
                return
            relation = None
            if choice.isdigit() and 1 <= int(choice) <= len(relations):
                relation = relations[int(choice) - 1]
            else:
                relation, ambiguous = _prefix_choice(choice, VALID_RELATIONS)
                if ambiguous:
                    print(ui.dim("  ? ambiguous: " + " / ".join(ambiguous)))
                    continue
            if relation:
                settle(relation, retyped=True)
                retyped += 1
                description = f"{current_from} —{relation}→ {current_to}"
                print(f"  {ui.cyan(f'(→ {description})')}")
                break
            print(ui.dim("  ? use k / r / f / a / s / x, a relation number, "
                         "its name, or a unique prefix (ans, cre, dep, ref…)"))
    print(f"Edge triage: confirmed {confirmed}, retyped {retyped}, "
          f"rejected {rejected}, merged {merged}, skipped {skipped}.")


def _run_deterministic_triage(db: Database, manuscript: dict, *,
                              run_nodes: bool, run_edges: bool,
                              apply: bool) -> None:
    from . import triage_rules
    from .revisions import read_manuscript_files

    files = read_manuscript_files(Path(manuscript["path"]))
    result = triage_rules.plan(
        db, manuscript, files,
        include_concepts=run_nodes, include_edges=run_edges)
    decisions = result["decisions"]
    for triage_type, heading in (("concepts", "Concepts"), ("edges", "Edges")):
        rows = [item for item in decisions if item["triage_type"] == triage_type]
        if not rows:
            continue
        print(ui.bold(f"{heading} ({len(rows)}):"))
        for item in rows:
            print(f"  {item['action']} {item['object_label']}"
                  + ui.dim(f" - {item['rule_label']}: {item['reason']}"))
    for item in result["protected"]:
        print(ui.dim(f"protected {item['object_label']} - {item['reason']}"))
    if not decisions:
        print("Deterministic triage found no mechanically safe decisions.")
        return
    if not apply:
        print(ui.dim(
            f"Report only - {len(decisions)} decision(s). Re-run with "
            "--deterministic --apply to execute them atomically."))
        return

    applied = triage_service.apply_deterministic(db, manuscript, decisions)
    action_counts = {
        action: sum(item["action"] == action for item in decisions)
        for action in {item["action"] for item in decisions}
    }
    summary = ", ".join(
        f"{count} {action}{'' if count == 1 else 's'}"
        for action, count in sorted(action_counts.items()))
    print(ui.green(f"Applied {applied['count']} deterministic decision(s): "
                   f"{summary}."))


def cmd_concept(args):
    db = _open_db(args)
    manuscript = _manuscript(db, args)
    mid = manuscript["id"]
    if args.action == "add":
        row = cg.add_concept(db, mid, args.name, kind=args.kind or "concept", notes=args.notes)
        print(f"Concept [{row['id'][:8]}] '{row['name']}' ({row['kind']}, {row['status']})")
    elif args.action == "link":
        row = cg.link_concepts(db, mid, args.from_name, args.relation, args.to_name)
        print(
            f"Edge [{row['id'][:8]}]: {args.from_name} —{args.relation}→ {args.to_name} "
            f"({row['status']})"
        )
    elif args.action == "confirm":
        if args.all or args.all_kind:
            nodes = _unconfirmed_nodes(db, mid)
            if args.all_kind:
                nodes = [n for n in nodes if n["kind"] == args.all_kind]
            for node in nodes:
                _confirm_node(db, mid, node, None)
            print(f"Confirmed {len(nodes)} concept(s)"
                  + (f" of kind {args.all_kind}" if args.all_kind else "") + ".")
            return
        # A concept name confirms an extracted node; otherwise the argument
        # is an edge id prefix, confirming an inferred relationship.
        node = cg.get_concept(db, mid, args.name)
        if node:
            _confirm_node(db, mid, dict(node), args.kind)
            print(f"Confirmed concept '{node['name']}' ({args.kind or node['kind']}).")
        else:
            edge = _find_by_prefix(db, "concept_edges", args.name, mid)
            db.update(
                "concept_edges", edge["id"],
                {"relation": args.relation or edge["relation"], "status": "declared"},
            )
            print(
                f"Confirmed: {cg.node_name(db, edge['from_node'])} "
                f"—{args.relation or edge['relation']}→ {cg.node_name(db, edge['to_node'])}"
            )
    elif args.action == "unconfirm":
        # Undo a confirmation: back to hypothesis status, so it re-enters
        # triage for a fresh decision.
        node = cg.get_concept(db, mid, args.name)
        if node:
            meta = loads(node["metadata"], {})
            meta.update(origin=meta.get("origin", "extracted"), confirmed=False)
            db.update("concept_nodes", node["id"], {"metadata": json.dumps(meta)})
            print(f"'{node['name']}' is unconfirmed again — it will reappear in "
                  "concept triage.")
        else:
            edge = _find_by_prefix(db, "concept_edges", args.name, mid)
            db.update("concept_edges", edge["id"], {"status": "inferred"})
            print(
                f"Back to hypothesis: {cg.node_name(db, edge['from_node'])} "
                f"—{edge['relation']}→ {cg.node_name(db, edge['to_node'])} "
                "— it will reappear in 'concept triage --edges'."
            )
    elif args.action == "reject-edge":
        edge = _find_by_prefix(db, "concept_edges", args.name, mid)
        db.update("concept_edges", edge["id"], {"status": "rejected"})
        print("Edge rejected (kept for history).")
    elif args.action == "retire":
        if args.all_kind:
            # Bulk retire is deliberately limited to unconfirmed extracted
            # nodes — confirmed knowledge is retired one name at a time.
            nodes = [n for n in _unconfirmed_nodes(db, mid) if n["kind"] == args.all_kind]
            for node in nodes:
                _retire_node(db, mid, node)
            print(f"Retired {len(nodes)} unconfirmed concept(s) of kind {args.all_kind}.")
            return
        for name in args.names:
            node = cg.get_concept(db, mid, name)
            if not node:
                node = _find_by_prefix(db, "concept_nodes", name, mid)
            if node["status"] == "retired":
                sys.exit(f"error: '{node['name']}' is already retired.")
            edges_retired = _retire_node(db, mid, dict(node))
            print(
                f"Retired '{node['name']}' and {edges_retired} related edge(s). "
                f"Kept for history; undo with: concept revive {node['name']!r}"
            )
    elif args.action == "revive":
        # The inverse of retire (a stray 'r' in triage is a real failure
        # mode): restore status, location, and this retirement's collateral
        # edges — no hand surgery.
        for name in args.names:
            row = db.one("SELECT * FROM concept_nodes WHERE manuscript_id = ? "
                         "AND lower(name) = lower(?) AND status = 'retired'",
                         (mid, name))
            if not row:
                sys.exit(f"error: no retired concept named '{name}'.")
            result = cg.revive_concept(db, mid, dict(row))
            where = (f" in {result['introduced_in']}" if result["introduced_in"]
                     else "")
            print(ui.green(f"revived '{row['name']}'")
                  + ui.dim(f" → {result['status']}{where}; "
                           f"{result['edges_revived']} edge(s) restored"))
    elif args.action == "edit":
        node = cg.get_concept(db, mid, args.name)
        if not node:
            node = _find_by_prefix(db, "concept_nodes", args.name, mid)
        changes = {}
        if args.notes is not None:
            changes["notes"] = args.notes
        if args.kind:
            changes["kind"] = args.kind
        db.update("concept_nodes", node["id"], changes)
        print(f"Updated '{node['name']}'"
              + (f" — kind: {args.kind}" if args.kind else "")
              + (f" — notes: {ui.shorten(args.notes, 60)}" if args.notes is not None else "")
              + ".")
    elif args.action == "alias":
        node = cg.get_concept(db, mid, args.name)
        if not node:
            sys.exit(f"No concept named '{args.name}'.")
        node = dict(node)
        removing = getattr(args, "remove", None)
        try:
            for alias in (removing or args.params[1:]):
                node = (cg.remove_alias(db, mid, node, alias) if removing
                        else cg.add_alias(db, mid, node, alias))
        except ValueError as err:
            sys.exit(str(err))
        remaining = cg.node_aliases(node)
        if removing:
            print(f"'{node['name']}' aliases now: "
                  + (", ".join(remaining) if remaining else "(none)"))
        else:
            print(f"'{node['name']}' also answers to: {', '.join(remaining)}")
    elif args.action == "merge":
        try:
            result = api.merge_concepts(db, manuscript,
                                        args.params[0], args.params[1])
        except (LookupError, ValueError) as err:
            sys.exit(str(err))
        print(f"Merged '{args.params[1]}' into '{result['canonical']}' — "
              f"{result['repointed']} edge(s) re-pointed, "
              f"{result['dropped']} retired.")
        print(f"'{result['canonical']}' also answers to: "
              f"{', '.join(result['aliases'])}")
    elif args.action == "show":
        node = cg.get_concept(db, mid, args.name)
        if not node:
            node = _find_by_prefix(db, "concept_nodes", args.name, mid)
        meta = loads(node["metadata"], {})
        print(f"{ui.bold(node['name'])}  [{node['id']}]")
        print(f"  kind: {node['kind']}   status: {node['status']}"
              + (f"   introduced in: {node['introduced_in']}" if node["introduced_in"] else ""))
        if cg.node_aliases(node):
            print(f"  aliases: {', '.join(cg.node_aliases(node))}")
        if meta.get("origin") == "extracted":
            state = "confirmed" if meta.get("confirmed") else "unconfirmed"
            print(f"  origin: extracted ({state})")
        if node["notes"]:
            print(f"  notes: {node['notes']}")
        edges = db.all(
            "SELECT * FROM concept_edges WHERE manuscript_id = ? "
            "AND status NOT IN ('rejected', 'retired') "
            "AND (from_node = ? OR to_node = ?)",
            (mid, node["id"], node["id"]),
        )
        for edge in edges:
            support = f" ×{edge['support']}" if edge["relation"] == "co_occurs" else ""
            print(f"  {ui.dim('[' + edge['id'][:8] + ']')} "
                  f"{cg.node_name(db, edge['from_node'])} —{edge['relation']}→ "
                  f"{cg.node_name(db, edge['to_node'])} ({edge['status']}{support})")
    elif args.action == "triage":
        run_nodes = args.nodes or not args.edges
        run_edges = args.edges or not args.nodes
        if args.deterministic:
            _run_deterministic_triage(
                db, manuscript, run_nodes=run_nodes, run_edges=run_edges,
                apply=args.apply)
            return
        with _ephemeral_history():
            if run_nodes:
                _run_triage(db, mid)
            if run_edges:
                _run_edge_triage(db, mid)
    else:  # list / graph
        node_filter = "" if args.all else " AND status != 'retired'"
        nodes = db.all(
            f"SELECT * FROM concept_nodes WHERE manuscript_id = ?{node_filter}", (mid,)
        )
        if not nodes:
            print("Concept Graph is empty.")
            return
        print("Concepts:")
        for node in nodes:
            where = f", introduced in {node['introduced_in']}" if node["introduced_in"] else ""
            meta = loads(node["metadata"], {})
            unconfirmed = (
                ", unconfirmed"
                if meta.get("origin") == "extracted" and not meta.get("confirmed")
                else ""
            )
            notes = ui.dim(f" — {ui.shorten(node['notes'], 60)}") if node["notes"] else ""
            print(f"  [{node['id'][:8]}] {node['name']} "
                  f"({node['kind']}, {node['status']}{unconfirmed}{where}){notes}")
        edge_filter = "" if args.all else " AND status NOT IN ('rejected', 'retired')"
        edges = db.all(
            f"SELECT * FROM concept_edges WHERE manuscript_id = ?{edge_filter}",
            (mid,),
        )
        if edges:
            print("Relationships:")
            for edge in edges:
                support = f", support {edge['support']}" if edge["status"] == "inferred" else ""
                print(
                    f"  [{edge['id'][:8]}] {cg.node_name(db, edge['from_node'])} "
                    f"—{edge['relation']}→ {cg.node_name(db, edge['to_node'])} "
                    f"({edge['status']}{support})"
                )


def cmd_collect(args):
    from . import api

    db = _open_db(args)
    manuscript = _manuscript(db, args)
    report = api.collect(db, manuscript, _load_config(args),
                         auto=getattr(args, "auto", False))
    _print_collect_report(report)


def _print_collect_report(report):
    if report.get("staged"):
        for item in report["staged"]:
            print(ui.yellow(
                f"Large deletion detected in {item['file']} "
                f"(~{item['removed_percent']}% of the file). "
            ))
        print(ui.yellow(
            "Snapshot NOT collected. If intentional, run 'collect' to "
            "confirm; if accidental, restore the text and it will collect "
            "normally."
        ))
        return
    if report.get("unchanged"):
        print("No changes since the last collected revision.")
        return

    print(f"Collected revision v{report['version_no']} ({report['checksum'][:12]}).")
    if report.get("new_files"):
        print(ui.yellow("New chapter file(s) observed: "
                        + ", ".join(report["new_files"])))
    print(f"Detected {len(report['transitions'])} editorial transition(s).")
    for transition in report["transitions"][:10]:
        print(f"  • [{transition['kind']}] {transition['location']}: {transition['summary']}")
    if len(report["transitions"]) > 10:
        print(f"  … and {len(report['transitions']) - 10} more.")
    if report["attached_to_episode"]:
        print("Attached transitions to the current episode.")
    else:
        print("note: no active session — transitions recorded outside any episode.")
    for node in report["realized"]:
        print(f"Concept realized: '{node['name']}' (in {node['introduced_in']}).")
    for move in report["repointed"]:
        print(ui.dim(
            f"Primary location of '{move['name']}' moved: {move['old']} → "
            f"{move['new']} (its introducing text was removed)."
        ))
    if report["vanished"]:
        names = ", ".join(f"'{n}'" for n in report["vanished"])
        print(ui.yellow(
            f"Concept(s) {names} no longer appear anywhere in the text — "
            "decide their fate: proposal review (retire, or keep as placeholder)."
        ))
    if report.get("hypotheses_dropped"):
        names = ", ".join(f"'{n}'" for n in report["hypotheses_dropped"])
        print(ui.dim(
            f"Dropped unconfirmed hypothesis(es) whose text vanished: "
            f"{names} (never ratified — no verdict needed)."))
    for stale in report.get("suggestions_stale", []):
        print(ui.dim(f"Suggestion now moot (marked stale): "
                     f"{stale['suggestion']}"))
    if report.get("illustrations"):
        ill = report["illustrations"]
        if ill.get("unrendered"):
            print(ui.yellow(
                f"New illustrations found ({len(ill['unrendered'])} "
                "unrendered):"))
            for slot in ill["unrendered"]:
                print(f"  • {slot['file']}:{slot['line']}  "
                      f"[Illustration: {slot['prompt']}]")
        if ill.get("orphaned"):
            print(ui.dim(
                "Orphaned illustration file(s) — no tag matches their "
                "prompt any more: " + ", ".join(ill["orphaned"])))
        for fix in ill.get("excerpt_fixes", []):
            if fix.get("missing"):
                print(ui.yellow(
                    f"Illustration ref without a file: {fix['file']} "
                    f"⇢ {fix['ref']} — restore the .md or delete the ref."))
            elif fix.get("discarded_edit"):
                print(ui.yellow(
                    f"Excerpt edit discarded in {fix['file']}: the tag "
                    f"mirrors {fix['ref']} — edit that file instead."))
            elif "embed_synced" in fix:
                verb = "updated" if fix["embed_synced"] else "cleared"
                print(ui.dim(f"Preview embed {verb} in "
                             f"_illustrations/prompts/{fix['ref']}."))
            else:
                print(ui.dim(f"Excerpt refreshed in {fix['file']} "
                             f"(⇢ {fix['ref']})."))
        for offer in ill.get("externalize_offers", []):
            print(ui.dim(
                f"Externalize offer: {offer['file']}:{offer['line']} "
                f"«{offer['prompt']}…» is {offer['reason']} — "
                "'illus externalize <fragment>' moves it to a prompts "
                "file (renders survive)."))
    if report.get("auto_analysis"):
        aa = report["auto_analysis"]
        print(ui.dim(f"Auto-analysis: {aa['new_concepts']} new concept(s), "
                     f"{aa['new_edges']} new relationship(s), "
                     f"{aa['proposed']} proposal(s)."))
    elif report["extract_hint"]:
        print(ui.dim(f"{report['new_paragraphs']} new/rewritten paragraph(s) — mine them "
                     "for new concepts with 'extract'."))
    gaps_before, gaps_after = report["gaps_before"], report["gaps_after"]
    if gaps_before or gaps_after:
        line = f"Prerequisite gaps: {len(gaps_before)} → {len(gaps_after)}"
        notes = []
        if report["gaps_resolved"]:
            notes.append("resolved: " + "; ".join(
                f"'{g['first']}' before '{g['second']}'" for g in report["gaps_resolved"]))
        if report["gaps_new"]:
            notes.append("new: " + "; ".join(
                f"'{g['second']}' needs '{g['first']}'" for g in report["gaps_new"]))
        if notes:
            line += "  (" + " · ".join(notes) + ")"
        print(ui.green(line) if len(gaps_after) < len(gaps_before) else ui.dim(line))


def _input_prefilled(prompt: str, initial: str) -> str:
    """input() with the line pre-filled for in-place editing — revising
    a description must not require retyping (or copy-pasting text the
    terminal has wrapped). Falls back to plain input() where readline
    is unavailable."""
    try:
        import readline
    except ImportError:
        return input(prompt)

    def hook():
        readline.insert_text(initial)
        readline.redisplay()

    readline.set_startup_hook(hook)
    try:
        return input(prompt)
    finally:
        readline.set_startup_hook(None)


def cmd_illus(args):
    from pathlib import Path

    from . import illus

    db = _open_db(args)
    manuscript = _manuscript(db, args)
    root = Path(manuscript["path"])

    def resolve_slot(required=True):
        if not args.name:
            if required:
                raise SystemExit("name a slot by a fragment of its prompt")
            return None
        matches = illus.find_slot(root, args.name)
        if not matches:
            raise SystemExit(f"no illustration tag matches '{args.name}'")
        if len(matches) > 1:
            for m in matches:
                print(f"  {m['file']}:{m['line']}  [Illustration: {m['prompt']}]")
            raise SystemExit(f"'{args.name}' is ambiguous — narrow the fragment")
        return matches[0]

    if args.action == "scan":
        _ensure_session(db, manuscript)
        from . import placement
        from .llm import LLMClient as _LLM

        report = placement.scan(db, manuscript, _LLM(_load_config(args)),
                                files=[args.name] if args.name else None)
        print(f"Scanned {len(report['files'])} chapter(s) "
              f"({report['calls']} spot-finder call(s)).")
        if report.get("skipped_at_budget"):
            print(ui.dim("At pacing budget (not scanned): "
                         + ", ".join(report["skipped_at_budget"])))
        if report["dropped_unverifiable"]:
            print(ui.dim(f"{report['dropped_unverifiable']} proposal(s) "
                         "dropped — unverifiable anchor or past budget."))
        arb = report.get("arbitration") or {}
        for item in arb.get("arbiter_cut", []):
            print(ui.dim(f"arbiter cut {item['id']}: {item['reason']}"))
        if arb.get("guard_cut"):
            print(ui.dim(f"guard cut {len(arb['guard_cut'])} proposal(s) "
                         "on illustration-excluded files."))
        remaining = arb.get("open", len(report["staged"]))
        if not report["staged"]:
            print("Nothing new staged.")
        else:
            print(ui.yellow(f"{len(report['staged'])} staged, {remaining} "
                            "open after arbitration — review with: "
                            "illus triage"))
        return

    if args.action == "triage":
        from . import placement
        from .llm import LLMClient as _LLM

        for row in placement.sweep_stale(db, manuscript):
            print(ui.dim(f"stale (anchor changed): {row['file']} — "
                         f"{row['description'][:60]}"))
        rows = placement.open_proposals(db, manuscript["id"])
        verdicts: dict[int, tuple[str, str | None]] = {}
        for pair in (args.revise or []):
            verdicts[int(pair[0])] = ("accept", pair[1])
        for n in (args.reject or []):
            verdicts[n] = ("reject", None)
        for n in (args.accept or []):
            verdicts.setdefault(n, ("accept", None))
        if args.accept_all_except is not None:
            excepted = set(args.accept_all_except)
            for i in range(1, len(rows) + 1):
                if i not in excepted:
                    verdicts.setdefault(i, ("accept", None))
        if not verdicts and rows and ui.is_tty():
            # The interactive walk — the same one-by-one triage the
            # concept graph taught the author's hands.
            import textwrap

            llm = _LLM(_load_config(args))
            print(f"{len(rows)} open placement proposal(s).")
            print(ui.dim("Keys: [k]eep (accept)  [v] revise the "
                         "description  [r]eject (asks your reason — "
                         "recorded verbatim)  [s]kip (or Enter)  "
                         "[x] quit"))

            def block(text, dim=False):
                wrapped = ui.wrap(text)
                print(ui.dim(wrapped) if dim else wrapped)

            quit_walk = False
            # Verdicts record instantly (no LLM in the loop); the
            # distiller runs ONCE over the whole batch at the end.
            explanations: list[tuple[str, str]] = []
            for index, row in enumerate(rows, 1):
                if quit_walk:
                    break
                kind = ("revision of existing tag" if row["revises"]
                        else "new placement")
                print(f"{ui.dim(f'[{index}/{len(rows)}]')} "
                      f"{ui.bold(row['file'])} — {kind}")
                block(f"after: «{row['anchor']}»", dim=True)
                block(f"[Illustration: {row['description']}]")
                block(f"criterion {row['criterion']} — {row['rationale']}",
                      dim=True)
                while True:
                    try:
                        choice = input("> ").strip().lower()
                    except EOFError:
                        choice = "x"
                    try:
                        if choice in ("k", "keep", "accept"):
                            placement.decide(db, manuscript, row["id"],
                                             "accept", llm=None)
                            print("  " + ui.green("accepted")
                                  + ui.dim(f" → tag written into {row['file']}"))
                            break
                        if choice in ("v", "revise"):
                            new_desc = _input_prefilled(
                                "  new description> ",
                                row["description"]).strip()
                            if not new_desc:
                                continue
                            placement.decide(
                                db, manuscript, row["id"], "accept",
                                revised_description=new_desc, llm=None)
                            explanations.append((row["file"], (
                                f"description revised: "
                                f"«{row['description']}» became "
                                f"«{new_desc}»")))
                            print("  " + ui.green("accepted (modified)")
                                  + ui.dim(" — the diff is evidence"))
                            break
                        if choice in ("r", "reject"):
                            why = ui.input_text(
                                "  reason (verbatim evidence; "
                                "Enter for none)> ").strip() or None
                            placement.decide(db, manuscript, row["id"],
                                             "reject", reason=why,
                                             llm=None)
                            if why:
                                explanations.append(
                                    (row["file"], f"rejected: {why}"))
                            print("  " + ui.yellow("rejected"))
                            break
                    except (LookupError, ValueError) as err:
                        print(ui.yellow(f"  {err}"))
                        break
                    if choice in ("s", "", "skip"):
                        print("  " + ui.dim("skipped"))
                        break
                    if choice in ("x", "quit"):
                        quit_walk = True
                        break
                    print(ui.dim("  ? use k / v / r / s / x"))
            if explanations:
                print(ui.dim(f"Distilling {len(explanations)} "
                             "explanation(s) for reusable patterns…"))
                candidate = placement.distill_batch(
                    db, manuscript, explanations, llm)
                if candidate:
                    print(ui.yellow(
                        "Pattern candidate proposed for your review: "
                        + ui.shorten(candidate.get("statement", ""), 70)))
            print("Done. Accepted tags are in the local files — collect, "
                  "push, and render follow (no prompting needed).")
            return

        if not verdicts:
            if not rows:
                print("No open placement proposals. Stage some with: "
                      "illus scan")
                return
            for i, row in enumerate(rows, 1):
                kind = ("revision of existing tag" if row["revises"]
                        else "new placement")
                why = f"criterion {row['criterion']} — {row['rationale']}"
                print(f"{ui.cyan(f'{i}.')} {ui.bold(row['file'])}"
                      + ui.dim(f" — {kind}") + "\n"
                      + ui.dim(f"   after: «{row['anchor'][:90]}»") + "\n"
                      f"   [Illustration: {row['description']}]\n"
                      f"   {ui.dim(why)}")
            print("\nVerdicts: illus triage --accept-all-except N… | "
                  "--accept N… | --revise N \"desc\" | --reject N --reason …")
            return
        batch: list[tuple[str, str]] = []
        for n in sorted(verdicts):
            if not 1 <= n <= len(rows):
                print(ui.yellow(f"no proposal numbered {n} — skipped"))
                continue
            verdict, revised = verdicts[n]
            row = rows[n - 1]
            try:
                result = placement.decide(
                    db, manuscript, row["id"], verdict,
                    revised_description=revised, reason=args.reason,
                    llm=None)
            except (LookupError, ValueError) as err:
                print(ui.yellow(f"{n}. {err}"))
                continue
            if result.get("modified"):
                batch.append((row["file"],
                              f"description revised to «{revised}»"))
            elif verdict == "reject" and args.reason:
                batch.append((row["file"], f"rejected: {args.reason}"))
            note = (" (modified — diff recorded as evidence)"
                    if result.get("modified") else "")
            print(f"{n}. {result['state']}: {row['file']}{note}")
        if batch:
            candidate = placement.distill_batch(
                db, manuscript, batch, _LLM(_load_config(args)))
            if candidate:
                print(ui.yellow(
                    "Pattern candidate proposed for your review: "
                    + ui.shorten(candidate.get("statement", ""), 70)))
        print("Accepted tags are in the local files — collect, push, and "
              "render follow (skill: no prompting needed).")
        return

    if args.action == "prompt":
        slot = resolve_slot()
        assembled = illus.effective_prompt(db, manuscript, slot)
        print(f"# {assembled['file']}:{assembled['line']}  "
              f"desc {assembled['desc_hash']}  "
              f"style {assembled['style_hash']}")
        if assembled["caption"]:
            print(f"# caption (export only, not sent to the model): "
                  f"{assembled['caption']}")
        print(assembled["composed"])
        return

    if args.action == "list":
        rows = illus.slot_status(db, manuscript)
        if not rows:
            print("No illustration slots. Declare one in prose: "
                  "[Illustration: prompt | caption: optional caption]")
            return
        for r in rows:
            head = f"{r['file']}:{r['line']}  [{r['state']}]"
            if r["candidates"]:
                head += f"  {r['candidates']} candidate(s)"
            if r["embedded"]:
                head += f"  → {r['embedded']}"
            print(head)
            print(f"    [Illustration: {r['prompt']}"
                  + (f" | caption: {r['caption']}" if r["caption"] else "")
                  + "]")
        stale = [r for r in rows if r["state"] == "stale-style"]
        if stale:
            print(ui.dim(
                f"{len(stale)} slot(s) predate the current illustration law — "
                "'illus render <fragment>' for a fresh take, or "
                "'illus render <fragment> --from N' to evolve the image "
                "you approved."))
        return

    if args.action == "rerender":
        _ensure_session(db, manuscript)
        # One verb for the revise cycle (ratified 2026-08-10): collect
        # the description edit, render, pin the fresh candidate, prune
        # the orphans, push the owning tab — the five-command loop that
        # every illustration iteration otherwise walks by hand.
        if not args.name:
            raise SystemExit("rerender needs a fragment naming one slot")
        config = _load_config(args)
        cmd_collect(args)  # the edited description becomes canonical
        slot = resolve_slot()
        result = illus.render_slot(db, manuscript, slot, config,
                                   count=args.count)
        newest = result["written"][-1]
        illus.set_embed(root / slot["file"], slot["desc_hash"], newest,
                        illus.load_prompts(root))
        removed = illus.prune(manuscript)
        print(f"Rendered + pinned {newest}"
              + (f"; pruned {len(removed)} orphan(s)" if removed else ""))
        if not args.local:
            from . import gdocs

            try:
                service = gdocs.get_service(config, args.workspace,
                                            interactive=False)
                docs_service = gdocs.get_docs_service(
                    config, args.workspace, interactive=False)
                res = gdocs.push_doc(db, manuscript, slot["file"],
                                     service=service,
                                     docs_service=docs_service)
                print(f"Pushed {res['relpath']} → "
                      f"{ui.dim(ui.link(res['url']))}")
            except Exception as err:  # push is a convenience, never fatal
                print(ui.yellow(f"Push skipped ({err}) — "
                                f"'doc push {slot['file']}' when ready."))
        return

    if args.action == "render":
        _ensure_session(db, manuscript)
        config = _load_config(args)
        if args.name:
            targets = [resolve_slot()]
        else:
            targets = [
                {"file": s["file"], "line": s["line"], "prompt": s["prompt"],
                 "caption": None,
                 "desc_hash": illus.desc_hash(s["prompt"])}
                for s in illus.slot_report(root)["unrendered"]]
            if not targets:
                print("Every declared slot is rendered — name a fragment to "
                      "re-render one.")
                return
            if args.from_n is not None:
                raise SystemExit("--from needs a single slot — name a fragment")
        rendered = failed = 0
        for slot in targets:
            print(f"Rendering {slot['file']}:{slot['line']} "
                  f"[Illustration: {slot['prompt'][:60]}…]"
                  if len(slot["prompt"]) > 60 else
                  f"Rendering {slot['file']}:{slot['line']} "
                  f"[Illustration: {slot['prompt']}]")
            try:
                result = illus.render_slot(db, manuscript, slot, config,
                                           from_n=args.from_n,
                                           count=args.count)
            except (RuntimeError, LookupError, OSError) as err:
                # One slot's failure (a network timeout, a server hiccup)
                # must never sink the rest of the run.
                print(ui.yellow(f"  render failed "
                                f"({type(err).__name__}): {err}"))
                failed += 1
                continue
            rendered += 1
            for name in result["written"]:
                print(f"  wrote _illustrations/{name}")
            if not result["had_embed"]:
                print(f"  embedded → {result['embedded']}")
            else:
                print(ui.dim(
                    f"  embed kept at {result['embedded']} — "
                    "'illus pick' to switch"))
        if len(targets) > 1 or failed:
            line = f"Rendered {rendered} of {len(targets)} slot(s)."
            if failed:
                line += (f" {failed} failed — they stay unrendered; "
                         "re-run 'illus render' to retry just those.")
            print(ui.yellow(line) if failed else line)
        return

    if args.action == "externalize":
        slot = resolve_slot()
        try:
            result = illus.externalize(root, slot)
        except ValueError as err:
            raise SystemExit(f"error: {err}")
        print(f"Externalized → _illustrations/prompts/{result['ref']} "
              f"(desc_hash {result['desc_hash']} unchanged — renders "
              "survive). The inline excerpt is machine-maintained; edit "
              "the .md file from now on, or delete the ⇢ ref to go back "
              "inline.")
        return

    if args.action == "pick":
        slot = resolve_slot()
        if args.candidate is None:
            raise SystemExit("pick needs a candidate number, e.g. "
                             f"illus pick '{args.name}' 2")
        cands = illus.slot_candidates(root, slot["desc_hash"])
        target = next((c for c in cands
                       if int(c["n"]) == args.candidate), None)
        if target is None:
            have = ", ".join(c["n"] for c in cands) or "none"
            raise SystemExit(f"no candidate {args.candidate:02d} "
                             f"(rendered: {have})")
        illus.set_embed(root / slot["file"], slot["desc_hash"],
                        target["name"], illus.load_prompts(root))
        # Render-side learning loop: which candidate won (and over what
        # field) is evidence for future illustration law.
        from .db import ko_fields as _ko

        ev = _ko("ev")
        ev.update(manuscript_id=manuscript["id"], episode_id=None,
                  evidence_type="illus_render", signal="picked",
                  target=(f"{slot['file']}: «{slot['prompt'][:80]}» — "
                          f"picked {target['name']} over "
                          f"{max(len(cands) - 1, 0)} other candidate(s)"),
                  supports_policy=None, weight="medium")
        db.insert("evidence", ev)
        print(f"Picked candidate {args.candidate:02d} — embed now "
              f"{target['name']}. Picks are pinned: renders never move them.")
        return

    if args.action == "prune":
        removed = illus.prune(manuscript)
        if not removed:
            print("Nothing to prune — every candidate is embedded and "
                  "its prompt still exists.")
        for name in removed:
            print(f"removed _illustrations/{name}")


def cmd_sweep(args):
    from pathlib import Path

    from . import api, hygiene, sweeps, triage_rules
    from .revisions import read_manuscript_files

    db = _open_db(args)
    manuscript = _manuscript(db, args)

    if args.action == "readiness":
        report = sweeps.readiness(db, manuscript)
        for item in report["items"]:
            mark = ui.green("✓") if item["ok"] else ui.yellow("✗")
            print(f"  {mark} {item['check']}: {item['detail']}")
        print(ui.green("READY: no publication blockers.") if report["ready"]
              else ui.yellow("NOT READY — blockers: "
                             + ", ".join(report["blocking"])))
        return

    if args.action == "ontology":
        llm = api.LLMClient(_load_config(args))
        if not llm.enabled:
            raise SystemExit("the ontology sweep needs the LLM enabled "
                             "([llm] in config.toml)")
        summary = sweeps.ontology(db, manuscript, llm, file=args.file)
        print(f"Ontology sweep over {summary['scope']}: "
              f"{summary['pairs']} (paragraph, settled-claims) pair(s) "
              f"checked, {summary['findings']} incongruence proposal(s)"
              + (f", {summary['dropped_ungrounded']} ungrounded finding(s) "
                 "dropped" if summary["dropped_ungrounded"] else "")
              + ".")
        if summary.get("note"):
            print(ui.dim(summary["note"]))
        if summary["findings"]:
            print(ui.yellow("Review them: proposal review"))
        line = llm.stats_line()
        if line:
            print(ui.dim(line))
        return

    files = read_manuscript_files(Path(manuscript["path"]))
    report = hygiene.sweep(db, manuscript, files)

    concepts = report["ungrounded_concepts"]
    edges = report["ungrounded_edges"]
    below_bar = report.get("below_bar", [])
    for stale in report["suggestions_stale"]:
        print(ui.dim(f"Suggestion now moot (marked stale): "
                     f"{stale['suggestion']}"))
    if concepts:
        print(ui.yellow(
            f"{len(concepts)} unconfirmed extracted concept(s) appear "
            "nowhere in the text (name or alias):"))
        for c in concepts:
            print(f"  • {c['name']} ({c['kind']}, {c['status']})")
    if edges:
        print(ui.yellow(
            f"{len(edges)} inferred relationship(s) have an endpoint "
            "mentioned nowhere in the text:"))
        for e in edges:
            print(f"  • {e['edge']}  (unmentioned: "
                  f"{', '.join(e['unmentioned'])})")
    if below_bar:
        print(ui.yellow(
            f"{len(below_bar)} unconfirmed extracted node(s) fail the "
            "recurrence bar (your ratified criterion: used multiple "
            "times, spread beyond one section):"))
        for c in below_bar:
            print(f"  • {c['name']} ({c['kind']}: {c['contexts']} "
                  f"context(s), {c['sections']} section(s), "
                  f"{c['files']} file(s))")
    if (not concepts and not edges and not below_bar
            and not report["suggestions_stale"]):
        print("Hygiene clean: every unconfirmed concept and inferred edge "
              "is grounded and recurrent, no pending suggestion is moot.")
        return

    if args.apply:
        plan = triage_rules.plan(db, manuscript, files)
        report_ids = {item["id"] for item in concepts + edges + below_bar}
        decisions = [item for item in plan["decisions"]
                     if item["object_id"] in report_ids]
        triage_service.apply_deterministic(db, manuscript, decisions)
        for item in decisions:
            verb = {"retire": "retired", "reject": "rejected",
                    "alias": "merged"}.get(item["action"], item["action"])
            print(f"{verb} {item['object_label']} "
                  f"({item['rule_label']})")
        for item in plan["protected"]:
            if item["object_id"] in report_ids:
                print(ui.dim(f"protected {item['object_label']} - "
                             f"{item['reason']}"))
    elif concepts or edges or below_bar:
        print(ui.dim(
            "Report only — 'sweep hygiene --apply' retires the ungrounded "
            "and below-bar concepts and rejects the ungrounded edges (or "
            "triage them individually: concept triage)."))


def cmd_lens(args):
    import json as _json

    from . import api, lenses

    db = _open_db(args)
    manuscript = _manuscript(db, args)

    if args.action == "add":
        prompt = _stdin_text()
        if not args.name or not prompt:
            raise SystemExit("usage: authorlm lens add <name>  "
                             "(the lens prompt on stdin)")
        path = lenses.add_lens(manuscript, args.name, prompt)
        print(f"Lens '{args.name}' ratified → {path}")
        return

    if args.action == "list":
        rows = lenses.list_lenses(manuscript)
        if not rows:
            print("No lenses defined. Create one: authorlm lens add "
                  "<name>  (prompt on stdin)")
        for row in rows:
            print(f"  {row['name']}: {row['summary']}")
        return

    if args.action == "review":
        decisions = [d for d, on in (("accepted", args.accept),
                                     ("rejected", args.reject),
                                     ("modified", args.modify),
                                     ("deferred", args.defer)) if on]
        if not args.name or not args.name.isdigit() or len(decisions) != 1:
            raise SystemExit("usage: authorlm lens review <n> "
                             "--accept|--reject|--modify|--defer "
                             '[--explain "why"]')
        session, _ = api.ensure_session(db, manuscript)
        result = api.review(db, manuscript, session, int(args.name),
                            decisions[0], args.explain,
                            llm=api.LLMClient(_load_config(args)),
                            kinds=(lenses.LENS_KIND,))
        print(f"Recorded: [{args.name}] {decisions[0]}"
              + (f" — “{args.explain}”" if args.explain else ""))
        if result.get("seeded_policy"):
            print(ui.dim("Your explanation seeded a candidate policy: "
                         f"\"{result['seeded_policy']['statement']}\""))
        return

    if not args.name or not args.file:
        raise SystemExit(f"usage: authorlm lens {args.action} <name> <file>")
    session, _ = api.ensure_session(db, manuscript)
    if args.action == "run":
        llm = api.LLMClient(_load_config(args))
        if not llm.enabled:
            raise SystemExit("lens run needs the LLM enabled — for an "
                             "external (Claude) pass, use lens register")
        result = lenses.run_lens(db, manuscript, session, args.name,
                                 args.file, llm)
        line = llm.stats_line()
    else:  # register — the door for externally produced findings
        raw = _stdin_text()
        try:
            payload = _json.loads(raw) if raw else None
        except _json.JSONDecodeError as err:
            raise SystemExit(f"invalid findings JSON on stdin: {err}")
        findings = (payload or {}).get("findings") \
            if isinstance(payload, dict) else payload
        if not isinstance(findings, list):
            raise SystemExit('lens register expects JSON on stdin: '
                             '{"findings": [{"quote": "...", '
                             '"note": "..."}]}')
        result = lenses.register_findings(db, manuscript, session,
                                          args.name, args.file, findings)
        line = None
    print(f"Lens '{result['lens']}' on {result['file']}: "
          f"{len(result['findings'])} finding(s)"
          + (f", {result['dropped_ungrounded']} ungrounded dropped"
             if result["dropped_ungrounded"] else "") + ".")
    for i, row in enumerate(result["findings"], start=1):
        print(f"  [{i}] {row['suggestion']}")
        print(ui.dim(f"      {row['explanation']}"))
    if result["findings"]:
        print(ui.dim("Verdicts: authorlm lens review <n> "
                     "--accept|--reject [--explain \"why\"]"))
    if line:
        print(ui.dim(line))


def cmd_export(args):
    from . import export as ex

    db = _open_db(args)
    manuscript = _manuscript(db, args)

    if args.action == "show":
        settings = ex.load_settings(manuscript)
        print(f"Export settings ({ex._settings_path(manuscript)}):")
        for key, hint in sorted(ex.SETTINGS_KEYS.items()):
            value = settings[key] or ui.dim("(default)")
            print(f"  {key} = {value}")
            print(ui.dim(f"      {hint}"))
        return

    if args.action == "set":
        if not args.key or args.value is None:
            raise SystemExit("usage: authorlm export set <key> <value>")
        settings = ex.set_setting(manuscript, args.key, args.value)
        print(f"{args.key} = {settings[args.key]}")
        return

    try:
        result = ex.export_published(db, manuscript, fmt=args.action,
                                     variant=args.variant)
    except (RuntimeError, LookupError) as err:
        raise SystemExit(ui.yellow(f"export failed: {err}"))
    print(f"Wrote {result['markdown']} (variant: {result['variant']}).")
    if args.action in result:
        print(f"Wrote {result[args.action]}.")
    for warning in result["warnings"]:
        print(ui.yellow(f"warning: {warning}"))


def _stdin_text() -> str | None:
    """Piped stdin, or None on a TTY / when empty. Prose and plan JSON
    arrive this way — multi-paragraph text as an argument is a quoting
    disaster; heredocs are the natural idiom."""
    if sys.stdin.isatty():
        return None
    text = sys.stdin.read()
    return text if text.strip() else None


def _print_beat_collect(report: dict) -> None:
    """The compact per-beat collect summary (the anti-pattern is the full
    dump: gaps_before/after alone ran to ~68KB in the first manual trial)."""
    if report.get("unchanged"):
        return
    print(f"Collected v{report['version_no']} — "
          f"{len(report['transitions'])} transition(s).")
    for node in report.get("realized", []):
        print(f"Concept realized: '{node['name']}'.")
    new_gaps = report.get("gaps_new", [])
    if new_gaps:
        print(ui.yellow("New prerequisite gap(s): " + "; ".join(
            f"'{g['second']}' needs '{g['first']}'" for g in new_gaps)))
    resolved = report.get("gaps_resolved", [])
    if resolved:
        print(ui.green(f"{len(resolved)} prerequisite gap(s) resolved."))


def _print_beat_spec(beat: dict, label: str = "Beat") -> None:
    parts = [f"n={beat['n']}"]
    if beat.get("role"):
        parts.append(beat["role"])
    if beat.get("concepts"):
        parts.append("concepts: " + ", ".join(beat["concepts"]))
    if beat.get("budget"):
        parts.append(f"budget ~{beat['budget']}")
    print(f"{label} [{' | '.join(parts)}]" +
          (f": {beat['notes']}" if beat.get("notes") else ""))


def cmd_write(args):
    db = _open_db(args)
    manuscript = _manuscript(db, args)
    config = _load_config(args)
    prefix = getattr(args, "writeup", None)
    try:
        if args.action == "start":
            if not args.params:
                sys.exit("usage: write start <file> --intent <id>")
            if not args.intent:
                sys.exit("write start requires --intent <id> — declare one "
                         "first (the writeup is bound to it)")
            result = api.write_start(db, manuscript, config,
                                     args.params[0], args.intent)
            w = result["writeup"]
            print(f"Writeup [{w['id'][:11]}] on {w['file']} "
                  f"(intent {result['intent']['id'][:11]}).")
            print(f"Pinned v{result['source_version_no']} as raw material "
                  f"({result['source_chars']} chars); file truncated.")
            print("Next: ratify the beat plan — write plan (JSON on stdin).")
        elif args.action == "plan":
            raw = _stdin_text()
            if raw is None:
                sys.exit("write plan expects a JSON array of beat specs on "
                         "stdin (view the plan with write status)")
            try:
                beats = json.loads(raw)
            except json.JSONDecodeError as err:
                sys.exit(f"invalid plan JSON: {err}")
            result = api.write_plan(db, manuscript, beats, prefix=prefix,
                                    replace=args.replace)
            kept = f"kept {result['kept']} written, " if result["kept"] else ""
            print(f"Plan ratified: {kept}{result['added']} beat(s) ahead.")
            for beat in result["plan"][result["cursor"]:]:
                _print_beat_spec(beat, label="  •")
        elif args.action == "status":
            result = api.write_status(db, manuscript, prefix=prefix)
            w = result["writeup"]
            plan = result["plan"]
            print(f"Writeup [{w['id'][:11]}] on {w['file']} ({w['status']}) — "
                  f"beat {min(w['cursor'] + 1, len(plan))}/{len(plan)}."
                  if plan else
                  f"Writeup [{w['id'][:11]}] on {w['file']} ({w['status']}) — no plan yet.")
            if result["current_beat"]:
                _print_beat_spec(result["current_beat"], label="Current")
            if result["pending_proposal"]:
                print(f"Pending proposal [{result['pending_proposal']['id'][:11]}] "
                      "awaits a verdict (accept / reject --reason).")
            if result["tallies"]:
                print("Verdicts: " + ", ".join(
                    f"{k} {v}" for k, v in sorted(result["tallies"].items())))
            for lesson in result["learnings"]:
                print(ui.dim(f"  learning: {lesson}"))
        elif args.action == "propose":
            text = _stdin_text()
            result = api.write_propose(db, manuscript, text or "",
                                       args.why or "", prefix=prefix)
            _print_beat_spec(result["beat"])
            print(f"Draft registered [{result['guidance_id'][:11]}] — "
                  "author verdict: accept / accept with reworded stdin / "
                  "reject --reason.")
        elif args.action == "accept":
            llm = LLMClient(config)
            result = api.write_accept(db, manuscript, config,
                                      text=_stdin_text(), reason=args.reason,
                                      prefix=prefix, llm=llm)
            print(f"Beat n={result['beat']['n']} {result['decision']}.")
            _print_beat_collect(result["collect"])
            if result["plan_complete"]:
                print(ui.green("Plan complete — write complete when done."))
            elif result["next_beat"]:
                _print_beat_spec(result["next_beat"], label="Next")
        elif args.action == "reject":
            llm = LLMClient(config)
            result = api.write_reject(db, manuscript, args.reason or "",
                                      prefix=prefix, llm=llm)
            print(f"Beat n={result['beat']['n']} rejected — reason recorded "
                  "verbatim. Redraft with the reason in context.")
            if result["review"].get("seeded_policy"):
                print(ui.dim("Seeded candidate policy: "
                             f"{result['review']['seeded_policy']['statement']}"))
        elif args.action == "learn":
            # stdin only, like propose/accept/plan: a positional lesson after
            # -m trips argparse's greedy-empty nargs='*' and dies unrecognized.
            lesson = _stdin_text()
            if lesson is None:
                sys.exit("write learn expects the lesson on stdin "
                         "(e.g. a heredoc)")
            result = api.write_learn(db, manuscript, lesson, prefix=prefix)
            print(f"Learning recorded ({len(result['learnings'])} this writeup).")
        elif args.action == "complete":
            result = api.write_complete(db, manuscript, config, prefix=prefix)
            unwritten = (f", {result['beats_unwritten']} unwritten"
                         if result["beats_unwritten"] else "")
            print(f"Writeup [{result['writeup_id'][:11]}] completed — "
                  f"{result['beats_done']} beat(s){unwritten}.")
            if result["tallies"]:
                print("Verdicts: " + ", ".join(
                    f"{k} {v}" for k, v in sorted(result["tallies"].items())))
            for lesson in result["learnings"]:
                print(ui.dim(f"  learning: {lesson}"))
            print("Intent completion is separate: intent complete "
                  f"{result['intent_id'][:11]} (runs episode analysis).")
        elif args.action == "abandon":
            result = api.write_abandon(db, manuscript, config, prefix=prefix)
            restored = ("file restored from the pinned source version"
                        if result["restored"] else
                        ui.yellow("source version missing — file NOT restored"))
            print(f"Writeup [{result['writeup_id'][:11]}] abandoned; {restored}.")
    except (LookupError, ValueError) as err:
        sys.exit(str(err))


def cmd_profile(args):
    """Manuscript profiles: declared context (market, positioning, …) in
    _profiles/ — observation-invisible, synced with a separate workspace
    Doc. Consulted on demand; never drafting law."""
    from . import gdocs

    db = _open_db(args)
    manuscript = _manuscript(db, args)
    root = Path(manuscript["path"]) / "_profiles"
    key = getattr(args, "key", None)
    filename = (key if key and key.endswith(".md") else f"{key}.md") if key else None
    try:
        if args.action == "list":
            files = sorted(root.glob("*.md")) if root.exists() else []
            if not files:
                print("No profiles yet. Create one: profile set market "
                      "(content on stdin).")
                return
            for f in files:
                words = len(f.read_text(encoding="utf-8").split())
                print(f"  {f.stem} — {words:,} words")
        elif args.action == "show":
            if not filename:
                sys.exit("usage: profile show <key>")
            path = root / filename
            if not path.exists():
                sys.exit(f"no profile '{key}' — see 'profile list'")
            print(path.read_text(encoding="utf-8"))
        elif args.action == "set":
            if not filename:
                sys.exit("usage: profile set <key>  (content on stdin)")
            text = _stdin_text()
            if text is None:
                sys.exit("profile set expects the content on stdin")
            root.mkdir(parents=True, exist_ok=True)
            (root / filename).write_text(text.strip() + "\n", encoding="utf-8")
            print(f"Profile '{key}' stored "
                  f"({len(text.split()):,} words). Overwrites are whole — "
                  "profiles are declarations.")
        elif args.action in ("push", "pull"):
            config = _load_config(args)
            try:
                service = gdocs.get_service(config, args.workspace,
                                            interactive=False)
                docs_service = gdocs.get_docs_service(config, args.workspace,
                                                      interactive=False)
            except ValueError as err:
                sys.exit(f"error: {err}")
            bridge = gdocs.workspace_bridge(manuscript)
            if args.action == "push":
                targets = ([filename] if filename
                           else gdocs._reading_order_files(bridge))
                if not targets:
                    sys.exit("no profiles to push — 'profile set' first")
                for target in targets:
                    result = gdocs.push_doc(db, manuscript, target,
                                            service=service,
                                            docs_service=docs_service,
                                            bridge=bridge)
                    print(f"Pushed profile {result['relpath']} → tab "
                          f"{ui.dim(ui.link(result['url']))}")
                print(ui.yellow("Checked out to the workspace Doc — edit "
                                "there, then 'profile pull'."))
            else:
                result = gdocs.pull_doc(db, manuscript, filename,
                                        service=service,
                                        docs_service=docs_service,
                                        bridge=bridge)
                for relpath in result.get("adopted", []):
                    print(ui.green(f"New profile imported from Doc tab: "
                                   f"{relpath}"))
                for relpath in result.get("readopted", []):
                    print(f"Relinked workspace tab for {relpath}.")
                for old, new in result.get("renamed", []):
                    print(ui.yellow(f"warning: the tab for {old} is now "
                                    f"titled '{new}' — rename it back."))
                for t in result.get("ambiguous_tabs", []):
                    print(ui.yellow(f"warning: duplicate/ambiguous tab "
                                    f"'{t}' left untouched."))
                if result.get("ignored_tabs"):
                    print(ui.dim("Ignoring non-profile tab(s): "
                                 + ", ".join(result["ignored_tabs"])))
                for relpath in result["changed"]:
                    print(f"Pulled profile {relpath} (normalized).")
                for relpath in result["conflicts"]:
                    print(ui.yellow(f"CONFLICT: {relpath} changed both "
                                    "locally and in its tab — untouched."))
                comments = result.get("comments") or []
                if comments:
                    print(ui.bold(f"Comments to address ({len(comments)}):"))
                    for c in comments:
                        print(f"  • [{c['location']}] on \"{c['quote']}\"")
                        print(f"    {c['content']}")
                    print(ui.dim("Resolved in the Doc with ingestion "
                                 "receipts."))
                if not result["changed"] and not result["conflicts"] \
                        and not comments and not result.get("adopted"):
                    print("Profiles identical to the workspace Doc — clean "
                          "round trip.")
                print(ui.dim("Checkouts cleared — local editing is safe "
                             "again."))
    except (LookupError, ValueError) as err:
        sys.exit(str(err))


def cmd_extract(args):
    db = _open_db(args)
    manuscript = _manuscript(db, args)
    llm = LLMClient(_load_config(args))
    if not llm.enabled:
        sys.exit(
            "error: extraction needs an LLM. Enable one in .authorlm/config.toml, e.g.\n"
            "  [llm]\n"
            "  enabled = true\n"
            '  model = "gemini/gemini-2.5-flash"\n'
            "(provider 'litellm' is the default; export GEMINI_API_KEY first)."
        )
    _run_extraction(db, manuscript, llm, files=args.files or None, full=args.full,
                    edges_only=args.edges_only, aliases_only=args.aliases)


def cmd_guide(args):
    db = _open_db(args)
    manuscript = _manuscript(db, args)
    session = _require_session(db, manuscript)
    llm = LLMClient(_load_config(args))
    rows = generate_guidance(db, manuscript, session, llm=llm)
    for note in intent_coverage_notes(db, manuscript):
        print(ui.yellow("note: ") + note + "\n")
    if rows[0]["kind"] == "abstention":
        print("AuthorLM abstains — no editorially justified suggestion.")
        print(f"Why: {rows[0]['explanation']}")
        _report_llm(llm)
        return
    print(f"{len(rows)} suggestion(s):\n")
    structural_marked = False
    for row in rows:
        if not structural_marked and row["kind"] not in INTENT_KINDS and any(
            r["kind"] in INTENT_KINDS for r in rows
        ):
            print(ui.dim("— standing manuscript findings (independent of your intent) —\n"))
            structural_marked = True
        print(f"{ui.bold('[' + str(row['batch_index']) + ']')} "
              f"({ui.cyan(row['kind'])}) {row['suggestion']}")
        print(ui.dim(f"    Why: {row['explanation']}") + "\n")
    print(ui.dim("Review each: review <n> accept | reject | modify | defer [--explain \"...\"]"))
    _report_llm(llm)


def cmd_review(args):
    from . import api

    db = _open_db(args)
    manuscript = _manuscript(db, args)
    session = _require_session(db, manuscript)
    decisions = [d for d in ("accept", "reject", "modify", "defer") if getattr(args, d)]
    if len(decisions) != 1:
        sys.exit("error: pass exactly one of --accept / --reject / --modify / --defer.")
    decision = {"accept": "accepted", "reject": "rejected",
                "modify": "modified", "defer": "deferred"}[decisions[0]]
    llm = LLMClient(_load_config(args))
    try:
        result = api.review(db, manuscript, session, args.index, decision,
                            args.explain, llm=llm)
    except (LookupError, ValueError) as err:
        sys.exit(f"error: {err}.")
    print(f"Recorded: [{args.index}] {decision}.")
    for policy in result["policies"]:
        print(
            f"Policy \"{policy['statement']}\" → confidence {policy['confidence']} "
            f"[{policy['status']}] ({policy['supporting']}+ / {policy['contradicting']}-)"
        )
    if result["seeded_policy"]:
        seeded = result["seeded_policy"]
        if seeded.get("kind") == "revival_proposal":
            print(
                f"Your explanation supports the retired policy "
                f"\"{seeded['statement']}\" — a revival proposal was filed "
                f"(see 'proposal list')."
            )
        else:
            print(
                f"Your explanation seeded a candidate policy: \"{seeded['statement']}\" "
                f"(confidence {seeded['confidence']})"
            )
    if args.explain and not result["seeded_policy"]:
        print("Explanation recorded as high-weight evidence.")
    _report_llm(llm)


def cmd_policy(args):
    db = _open_db(args)
    manuscript = _manuscript(db, args)
    if args.action == "list":
        rows = db.all(
            "SELECT * FROM editorial_policies WHERE manuscript_id = ? "
            "AND status != 'retired' ORDER BY confidence DESC",
            (manuscript["id"],),
        )
        if not rows:
            print("No editorial policies learned yet. They emerge from your reviews.")
        any_questions = False
        for row in rows:
            questions = loads(row["outstanding_questions"], [])
            print(
                f"[{row['id'][:8]}] ({row['status']}, confidence {row['confidence']}) "
                f"\"{ui.bold(row['statement'])}\" — {row['supporting']}+ / {row['contradicting']}-"
            )
            for number, question in enumerate(questions, start=1):
                any_questions = True
                print(ui.dim(f"    Q{number}: {question}"))
        if any_questions:
            print(ui.dim('  → answer: policy answer <id> "your answer" '
                         "[--index N]  (N = the Q number, default 1)"))
    elif args.action == "retire":
        try:
            result = api.retire_policy(db, manuscript, args.id, args.reason)
        except (LookupError, ValueError) as err:
            sys.exit(str(err))
        print(f"Policy retired: \"{result['statement']}\" "
              "(statement stays banned from re-seeding).")
    elif args.action == "merge":
        try:
            result = api.merge_policies(db, manuscript, args.id, args.answer,
                                        reason=args.reason)
        except (LookupError, ValueError) as err:
            sys.exit(str(err))
        print(f"Merged \"{result['merged']}\" into \"{result['statement']}\" "
              f"— now {result['supporting']}+ / {result['contradicting']}- "
              f"[{result['status']}, confidence {result['confidence']}].")
    elif args.action == "convert":
        try:
            result = api.convert_policy(
                db, manuscript, args.id, args.aspect,
                statement=args.statement, guide=args.guide, file=args.file,
                notes=args.notes, reason=args.reason)
        except (LookupError, ValueError) as err:
            sys.exit(str(err))
        print(f"Policy \"{result['statement']}\" converted to style element "
              f"[{result['style_element'][:8]}] (policy retired).")
    else:  # answer
        policy = _find_by_prefix(db, "editorial_policies", args.id, manuscript["id"])
        questions = loads(policy["outstanding_questions"], [])
        if not questions:
            sys.exit("error: that policy has no outstanding questions.")
        index = args.index - 1
        if not 0 <= index < len(questions):
            sys.exit(f"error: question index out of range (1..{len(questions)}).")
        answered = questions.pop(index)
        db.update(
            "editorial_policies", policy["id"],
            {"outstanding_questions": json.dumps(questions)},
        )
        ev = ko_fields("ev")
        ev.update(
            manuscript_id=manuscript["id"], episode_id=None,
            evidence_type="briefing_answer", signal="declared",
            target=f"Q: {answered} — A: {args.answer}",
            supports_policy=policy["id"], weight="high",
        )
        db.insert("evidence", ev)
        print("Answer recorded as declared evidence. Question closed.")


def cmd_doc(args):
    from . import docs

    db = _open_db(args)
    if args.action == "auth":
        # Workspace-level: one token serves every manuscript — no -m needed.
        from . import gdocs

        try:
            gdocs.get_service(_load_config(args), args.workspace, interactive=True)
        except ValueError as err:
            sys.exit(f"error: {err}")
        print("Google Drive authorization OK — token cached in "
              "~/.authorlm/gdocs_token.json.")
        return
    manuscript = _manuscript(db, args)
    if args.action == "threads":
        from . import threads as th

        board = th.ledger(db, manuscript["id"])
        if not board["counts"]:
            print("No margin threads yet. They begin when a Doc comment "
                  "gets a proposal: doc propose <comment-id>.")
            return
        print("Margin threads: " + ", ".join(
            f"{n} {state}" for state, n in sorted(board["counts"].items())))
        for row in board["open"]:
            print(f"  [{row['state']}] {row['file']}  "
                  f"«{(row['anchor_quote'] or '')[:50]}»  — {row['note']}")
        return
    if args.action == "propose":
        import json as _json

        from . import gdocs as gd

        if not args.name:
            sys.exit("usage: doc propose <comment-id> "
                     '(stdin: {"old": …, "new": …, "note": …})')
        payload = _stdin_text()
        if not payload:
            sys.exit("propose reads the proposal from stdin as JSON: "
                     '{"old": "exact current text", "new": "replacement", '
                     '"note": "one-line why"}')
        spec = _json.loads(payload)
        service = gd.get_service(_load_config(args), args.workspace)
        docs_service = gd.get_docs_service(_load_config(args), args.workspace)
        result = gd.propose_change(
            db, manuscript, args.name, spec["old"], spec["new"],
            spec.get("note", ""), service=service, docs_service=docs_service)
        print(f"Proposed on {result['file']} — the change is visible "
              "in-context (old struck through, new in green); the author "
              "approves in-thread or in chat.")
        return
    if args.action == "decide":
        from . import gdocs as gd
        from .llm import LLMClient

        if not args.name or not (args.approve or args.decline):
            sys.exit("usage: doc decide <comment-id> --approve|--decline "
                     '[--reason "why"]')
        config = _load_config(args)
        result = gd.decide_thread(
            db, manuscript, args.name,
            "approve" if args.approve else "decline",
            reason=args.reason,
            service=gd.get_service(config, args.workspace),
            docs_service=gd.get_docs_service(config, args.workspace),
            llm=LLMClient(config))
        print(f"Thread {result['state']} ({result['signal']}).")
        return
    if args.action == "list":
        listing = docs.list_docs(db, manuscript)
        if not listing["active"] and not listing["retired"]:
            print("No documents in the manuscript yet. Create one: doc add <name>")
            return
        from .gdocs import doc_status

        links = doc_status(db, manuscript)
        print(ui.bold("Documents:"))
        for doc in listing["active"]:
            concepts = ui.dim(" — introduces: " + ", ".join(doc["concepts"][:6])
                              + (f", … +{len(doc['concepts']) - 6}" if len(doc["concepts"]) > 6 else "")) \
                if doc["concepts"] else ""
            link = links.get(doc["file"], {})
            cloud = ""
            master_id = links.get("_master_id")
            if isinstance(link, dict) and link.get("tab_id") and master_id:
                from .gdocs import tab_url

                marker = ui.yellow("[checked out to Google Docs]") if link.get("checked_out") \
                    else ui.dim("[tab]")
                cloud = f"  {marker} {ui.dim(ui.link(tab_url(master_id, link['tab_id'])))}"
            print(f"  {doc['file']}  ({doc['paragraphs']} paragraph(s)){concepts}{cloud}")
        from .gdocs import ILLUS_DISPLAY_PREFIX, prompt_links, tab_url

        prompts = prompt_links(links)
        if prompts and links.get("_master_id"):
            print(ui.bold("Illustration prompts:")
                  + ui.dim("  (editable in the Doc's 'illustrations' tabs)"))
            for name, entry in sorted(prompts.items()):
                print(f"  {ILLUS_DISPLAY_PREFIX}{name}  "
                      + ui.dim(ui.link(tab_url(links["_master_id"],
                                               entry["tab_id"]))))
        if listing["retired"]:
            print(ui.bold("Retired:") + ui.dim(f"  (in {docs.RETIRED_DIR}/, invisible to observation)"))
            for name in listing["retired"]:
                print(f"  {name}" + ui.dim(f"  [revive: doc revive {name}]"))
        return

    if args.action == "open":
        # Open the linked Google Doc directly — immune to terminal link
        # handling quirks (integrated terminals, dialogs, etc.).
        from .gdocs import doc_status

        if not args.name:
            sys.exit("usage: doc open <file>")
        links = doc_status(db, manuscript)
        matches = {rel: e for rel, e in links.items()
                   if not rel.startswith("_") and args.name.lower() in rel.lower()
                   and isinstance(e, dict) and e.get("tab_id")}
        from .gdocs import ILLUS_DISPLAY_PREFIX, prompt_links

        matches.update({ILLUS_DISPLAY_PREFIX + name: e
                        for name, e in prompt_links(links).items()
                        if args.name.lower() in name.lower()
                        and e.get("tab_id")})
        if not matches or not links.get("_master_id"):
            sys.exit(f"error: no tab matching '{args.name}' in the master "
                     "Doc — push it first.")
        if len(matches) > 1:
            sys.exit(f"error: '{args.name}' is ambiguous: {', '.join(matches)}")
        from .gdocs import tab_url

        relpath, entry = next(iter(matches.items()))
        url = tab_url(links["_master_id"], entry["tab_id"])
        import webbrowser

        webbrowser.open(url)
        print(f"Opening {relpath} in your browser: {url}")
        return

    if args.action in ("push", "pull"):
        from . import gdocs

        _ensure_session(db, manuscript)
        config = _load_config(args)
        try:
            # push/pull never open a consent browser — 'doc auth' is the
            # single interactive doorway.
            service = gdocs.get_service(config, args.workspace, interactive=False)
            docs_service = gdocs.get_docs_service(config, args.workspace,
                                                  interactive=False)
        except ValueError as err:
            sys.exit(f"error: {err}")
        try:
            if args.action == "push":
                # One tab per file in a single master Doc; no file argument
                # pushes the whole manuscript.
                targets = ([args.name] if args.name
                           else gdocs._reading_order_files(
                               gdocs.manuscript_bridge(manuscript)))
                normalized_any = False
                result = None
                for target in targets:
                    result = gdocs.push_doc(db, manuscript, target,
                                            title=args.title, service=service,
                                            docs_service=docs_service)
                    normalized_any = normalized_any or result["locally_normalized"]
                    print(f"Pushed {result['relpath']} → tab "
                          f"{ui.dim(ui.link(result['url']))}")
                if normalized_any:
                    print(ui.dim("Some files were normalized locally first "
                                 "(canonical markdown)."))
                    cmd_collect(args)
                if not args.name:
                    # Full push mirrors _illustrations/prompts/ into the
                    # reserved 'illustrations' tab tree (changed-only).
                    try:
                        prom = gdocs.push_prompt_tabs(db, manuscript,
                                                      service, docs_service)
                    except Exception as err:  # never fatal to a push
                        prom = {"error": str(err)}
                    if prom.get("error"):
                        print(ui.yellow("warning: illustration prompt sync "
                                        f"failed ({prom['error']})"))
                    else:
                        done = [f"{len(prom['created'])} tab(s) created"
                                if prom.get("created") else "",
                                f"{len(prom['updated'])} updated"
                                if prom.get("updated") else "",
                                f"{len(prom['pruned'])} pruned"
                                if prom.get("pruned") else ""]
                        done = [d for d in done if d]
                        if done:
                            print("Illustration prompts: " + ", ".join(done)
                                  + ".")
                        for name, actual in prom.get("renamed", []):
                            print(ui.yellow(
                                f"warning: the prompt tab for {name} is "
                                f"titled '{actual}' — rename it back (tab "
                                "titles must stay exact filenames)."))
                        if prom.get("unknown"):
                            print(ui.dim(
                                "Ignoring hand-made tab(s) under "
                                "'illustrations': "
                                + ", ".join(prom["unknown"])
                                + " (prompt files are born via "
                                "'illus externalize')."))
                try:
                    sync = gdocs.sync_tab_structure(db, manuscript,
                                                    docs_service)
                except Exception as err:  # sync must never break a push
                    sync = {"skipped": f"tab-structure sync failed ({err})"}
                if sync.get("moved"):
                    print(f"Repositioned {sync['moved']} tab(s) to match "
                          "toc.toml's order and hierarchy.")
                elif sync.get("skipped") and "pull first" in sync["skipped"]:
                    print(ui.yellow(f"Tab order not synced: {sync['skipped']}."))
                print(f"Master Doc: "
                      f"{ui.link(gdocs.tab_url(result['doc_id']))}")
                print(ui.yellow("Checked out to Google Docs — edit there, "
                                "then 'doc pull'. Avoid local edits meanwhile."))
                if args.open:
                    import webbrowser

                    webbrowser.open(gdocs.tab_url(result["doc_id"]))
            else:
                result = gdocs.pull_doc(db, manuscript, args.name or None,
                                        service=service, force=args.force,
                                        with_comments=not args.no_comments,
                                        docs_service=docs_service)
                for relpath in result.get("adopted", []):
                    print(ui.green(f"New essay imported from Doc tab: "
                                   f"{relpath} — add it to toc.toml."))
                for relpath in result.get("readopted", []):
                    print(f"Relinked Doc tab for {relpath} (tab was "
                          f"recreated or the mapping was lost).")
                for old, new in result.get("renamed", []):
                    print(ui.yellow(f"warning: the tab for {old} is now "
                                    f"titled '{new}' — tab titles must stay "
                                    "exact filenames; rename it back."))
                for title in result.get("ambiguous_tabs", []):
                    print(ui.yellow(f"warning: duplicate/ambiguous tab "
                                    f"'{title}' left untouched — resolve it "
                                    "by hand in the Doc."))
                if result.get("tabs_error"):
                    print(ui.yellow("warning: tab reconciliation skipped "
                                    f"({result['tabs_error']})"))
                if result.get("container_renamed"):
                    expected, actual = result["container_renamed"]
                    print(ui.yellow(f"warning: the container tab was renamed "
                                    f"'{actual}' — its name is protected; "
                                    f"rename it back to '{expected}'."))
                if result.get("container_pending"):
                    names = ", ".join(result["container_pending"])
                    print(ui.yellow(f"One-time drags needed: move {names} "
                                    "under the container tab in the Doc "
                                    "(the API cannot move root-level tabs)."))
                if result.get("ignored_tabs"):
                    print(ui.dim("Ignoring non-manuscript tab(s): "
                                 + ", ".join(result["ignored_tabs"])))
                for name, actual in result.get("prompt_renamed", []):
                    print(ui.yellow(f"warning: the prompt tab for {name} is "
                                    f"now titled '{actual}' — untouched; "
                                    "rename it back (tab titles must stay "
                                    "exact filenames)."))
                if result.get("prompt_unknown"):
                    print(ui.dim("Ignoring hand-made tab(s) under "
                                 "'illustrations': "
                                 + ", ".join(result["prompt_unknown"])
                                 + " (prompt files are born via "
                                 "'illus externalize')."))
                if result.get("toc_updated"):
                    print(ui.green("toc.toml updated from the Doc's tab "
                                   "order/hierarchy."))
                if result.get("toc_ahead"):
                    print("toc.toml is ahead of the Doc's tab order — the "
                          "next 'doc push' will reposition the tabs.")
                if result.get("toc_conflict"):
                    print(ui.yellow("TOC order conflict: toc.toml and the "
                                    "Doc's tab order both changed "
                                    f"({result['toc_conflict']}) — reorder "
                                    "one side to match the other, then "
                                    "pull or push."))
                for relpath in result["changed"]:
                    print(f"Pulled {relpath} from its tab (normalized).")
                for relpath, refs in (result.get("dangling_images")
                                      or {}).items():
                    print(ui.yellow(
                        f"warning: {relpath} carried {len(refs)} pasted "
                        "image(s) the Doc bridge cannot transport "
                        f"({', '.join(sorted(set(refs)))}) — stripped. "
                        "Images belong in _illustrations/ via "
                        "[Illustration: …] tags."))
                for relpath in result["missing"]:
                    print(ui.yellow(f"warning: no tab found for {relpath} — "
                                    "was its tab renamed? Tab titles must "
                                    "stay exact filenames."))
                for relpath in result["conflicts"]:
                    print(ui.yellow(
                        f"CONFLICT: {relpath} changed both locally and in its "
                        f"tab — untouched. 'doc pull {relpath} --force' takes "
                        "the Doc's side."))
                for relpath in result.get("local_ahead", []):
                    print(f"{relpath}: local file is ahead (its tab is "
                          f"unchanged since the last push) — kept local. "
                          f"'doc push {relpath}' refreshes the tab.")
                if result["changed"]:
                    print("Collecting:")
                    cmd_collect(args)
                elif (not result["conflicts"] and not result["missing"]
                      and not result.get("local_ahead")):
                    scope = (f"Tab for {args.name}" if args.name
                             else "All tabs")
                    print(f"{scope} identical to local files — clean round "
                          "trip, nothing to collect.")
                _print_comment_harvest(result)
                print(ui.dim("Checkouts cleared — local editing is safe again."))
        except (LookupError, FileExistsError, ValueError) as err:
            sys.exit(f"error: {err}")
        return

    if args.action == "create-manuscript":
        from . import export as mexport
        from . import gdocs

        service = None
        try:
            service = gdocs.get_service(_load_config(args), args.workspace,
                                        interactive=False)
        except ValueError:
            pass
        try:
            result = mexport.export_manuscript(db, manuscript, service=service,
                                               title=args.title)
        except LookupError as err:
            sys.exit(f"error: {err}")
        print(f"Combined {len(result['files'])} file(s) → {result['path']}")
        if result["unlisted"]:
            print(ui.yellow(
                "Files missing from toc.toml were appended alphabetically: "
                + ", ".join(result["unlisted"])
            ))
        if result["doc_id"]:
            verb = "Created" if result["created"] else "Updated"
            print(f"{verb} Google Doc '{result['doc_title']}': "
                  f"{ui.link(result['url'])}")
            if args.open:
                import webbrowser

                webbrowser.open(result["url"])
        else:
            print(ui.dim("Google Doc skipped — Drive isn't authorized here "
                         "(run 'doc auth', then re-run)."))
        print(ui.dim("Both artifacts are transient — regenerate anytime with "
                     "'doc create-manuscript'."))
        return

    if not args.name:
        sys.exit(f"usage: doc {args.action} <name>")
    try:
        if args.action == "add":
            path = docs.add_doc(manuscript, args.name, title=args.title)
            print(f"Created {path.relative_to(manuscript['path'])} "
                  + ui.dim("(scaffold only — the writing is yours)"))
        elif args.action == "retire":
            path = docs.retire_doc(manuscript, args.name)
            print(f"Retired to {docs.RETIRED_DIR}/{path.name} — history stays replayable; "
                  "concepts it introduced remain in the graph (retire those separately if wanted).")
        else:  # revive
            path = docs.revive_doc(manuscript, args.name)
            print(f"Revived {path.name}.")
    except (FileExistsError, LookupError, ValueError) as err:
        sys.exit(f"error: {err}")
    cmd_collect(args)  # record the mechanical change as an observation now


def _print_analysis(summaries: list[dict]) -> None:
    for summary in summaries:
        print(ui.bold(
            f"Analyzed episode '{ui.shorten(summary['intent'], 60)}' "
            f"({summary['transitions']} transition(s)):"
        ))
        for decision in summary["decisions"]:
            print(f"  • {decision['action']}")
            if decision["pattern"]:
                print(ui.dim(f"    ↳ pattern: \"{decision['pattern']}\""))
        for statement, note in summary["policies"]:
            print(f"  {ui.cyan('policy')} \"{ui.shorten(statement, 70)}\" — {note}")
        if summary["outcome"]:
            print(ui.dim(f"  outcome: {summary['outcome']}"))


def _analyze_closed_episodes(db: Database, manuscript: dict, args) -> None:
    """Run retrospective analysis over newly closed episodes (LLM optional:
    without one, episodes simply stay pending for a later 'analyze')."""
    from .analysis import _pending_episodes, analyze_pending

    llm = LLMClient(_load_config(args))
    pending = _pending_episodes(db, manuscript["id"])
    if not llm.enabled:
        if pending:
            print(ui.dim("Episode analysis needs an LLM — episodes kept pending "
                         "(run 'analyze' once one is configured)."))
        return
    if pending:
        print(ui.dim(
            f"Analyzing {len(pending)} closed episode(s) with the LLM — this "
            "can take a minute; Ctrl-C is safe (unfinished episodes stay "
            "pending)."
        ))
    summaries = analyze_pending(
        db, manuscript, llm,
        progress=lambda i, n, intent: print(
            ui.dim(f"  [{i}/{n}] {ui.shorten(intent, 60)}…"), flush=True),
    )
    if summaries:
        _print_analysis(summaries)
        _report_llm(llm)


def cmd_analyze(args):
    from .analysis import analyze_pending

    db = _open_db(args)
    manuscript = _manuscript(db, args)
    llm = LLMClient(_load_config(args))
    if not llm.enabled:
        sys.exit("error: episode analysis needs an LLM (see README, LLM setup).")
    from .analysis import _pending_episodes

    pending = _pending_episodes(db, manuscript["id"])
    if pending:
        print(ui.dim(f"Analyzing {len(pending)} closed episode(s) — Ctrl-C is "
                     "safe (unfinished episodes stay pending)."))
    summaries = analyze_pending(
        db, manuscript, llm,
        progress=lambda i, n, intent: print(
            ui.dim(f"  [{i}/{n}] {ui.shorten(intent, 60)}…"), flush=True),
    )
    if not summaries:
        print("No episodes awaiting analysis — episodes are analyzed when they "
              "close (intent complete / session end) and never re-analyzed.")
        return
    _print_analysis(summaries)
    _report_llm(llm)


def cmd_shell(args):
    from .shell import run_shell

    db = _open_db(args)
    # The shell is interactive — with several manuscripts, ask instead of
    # erroring like the one-shot commands do.
    if not getattr(args, "manuscript", None):
        rows = db.all("SELECT * FROM manuscripts ORDER BY name")
        if len(rows) > 1:
            print("Manuscripts:")
            for number, row in enumerate(rows, start=1):
                print(f"  [{number}] {row['name']}")
            try:
                choice = input("Which manuscript? > ").strip()
            except EOFError:
                choice = ""
            if choice.isdigit() and 1 <= int(choice) <= len(rows):
                args.manuscript = rows[int(choice) - 1]["name"]
            else:
                match = next((r for r in rows if r["name"] == choice), None)
                if not match:
                    sys.exit("error: pick a number from the list or an exact name.")
                args.manuscript = match["name"]
    manuscript = _manuscript(db, args)
    _expire_idle_session(db, manuscript, args)
    _reconcile_gdocs(db, manuscript, args)
    run_shell(args, db, manuscript)


def cmd_watch(args):
    from .shell import run_watch

    db = _open_db(args)
    manuscript = _manuscript(db, args)
    run_watch(args, manuscript)


def cmd_export_obsidian(args):
    from .obsidian import export_obsidian

    db = _open_db(args)
    manuscript = _manuscript(db, args)
    result = export_obsidian(db, manuscript, args.dir)
    print(
        f"Exported {result['notes']} concept note(s) with {result['links']} "
        f"link(s) to {result['dir']}"
        + (f" (replaced {result['removed']} stale note(s))" if result["removed"] else "")
        + "."
    )
    print("Open the manuscript folder as an Obsidian vault to see the graph. "
          "Folders starting with '_' or '.' are ignored by observation.")


def cmd_completion(args):
    from .completion import generate_bash_completion

    print(generate_bash_completion())


def cmd_manuscripts(args):
    """Hidden helper for tab-completion of -m values."""
    db = _open_db(args)
    for row in db.all("SELECT name FROM manuscripts ORDER BY name"):
        print(row["name"])


def list_ids(db: Database, manuscript: dict, kind: str) -> list[str]:
    """Ids/names offered by tab completion, by kind."""
    from . import docs

    mid = manuscript["id"]
    if kind == "intents":
        return [r["id"] for r in db.all(
            "SELECT id FROM declared_intents WHERE manuscript_id = ? AND status = 'active'",
            (mid,))]
    if kind == "policies":
        return [r["id"] for r in db.all(
            "SELECT id FROM editorial_policies WHERE manuscript_id = ? "
            "AND status != 'retired'", (mid,))]
    if kind in ("concepts", "confirmables"):
        names = [r["name"] for r in db.all(
            "SELECT name FROM concept_nodes WHERE manuscript_id = ? AND status != 'retired' "
            "ORDER BY name", (mid,))]
        if kind == "confirmables":
            names += [e["id"] for e in db.all(
                "SELECT id FROM concept_edges WHERE manuscript_id = ? "
                "AND status = 'inferred'", (mid,))]
        return names
    if kind == "edges":
        return [r["id"] for r in db.all(
            "SELECT id FROM concept_edges WHERE manuscript_id = ? "
            "AND status NOT IN ('rejected', 'retired')", (mid,))]
    if kind == "docs":
        return [d["file"] for d in docs.list_docs(db, manuscript)["active"]]
    if kind == "docs-retired":
        return docs.list_docs(db, manuscript)["retired"]
    if kind == "proposals":
        return [r["id"] for r in db.all(
            "SELECT id FROM knowledge_proposals WHERE manuscript_id = ? AND state = 'open'",
            (mid,))]
    return []


def cmd_ids(args):
    """Hidden helper for bash tab-completion of ids and names, one per line."""
    db = _open_db(args)
    try:
        manuscript = _manuscript(db, args)
    except SystemExit:
        return  # no manuscript resolvable → no candidates
    values = list_ids(db, dict(manuscript), args.kind)
    if values:
        print("\n".join(values))


def cmd_help(args):
    from .shell import print_command_help

    print_command_help(args.topic, in_shell=False)


def cmd_plan(args):
    from . import api

    db = _open_db(args)
    manuscript = _manuscript(db, args)
    llm = LLMClient(_load_config(args))
    result = api.get_plan(db, manuscript, llm=llm, draft=args.draft)
    if not result["items"]:
        print("Nothing to plan — every non-reference concept is realized. "
              "Declare new concepts or intents to grow the plan.")
        return
    print(ui.bold(f"Writing plan ({len(result['items'])} item(s), "
                  "prerequisites first):"))
    for number, item in enumerate(result["items"], start=1):
        print(f"\n{ui.bold(f'[{number}]')} Introduce '{ui.bold(item['concept'])}' "
              f"({item['kind']}) — {item['placement']}")
        for reason in item["reasons"]:
            print(ui.dim(f"    • {reason}"))
        if item["write_first"]:
            print(ui.yellow("    write first: " + ", ".join(item["write_first"])))
        if item["precedent"]:
            print(ui.dim(f"    precedent — {item['precedent']['label']}: "
                         + "; then ".join(item["precedent"]["actions"])))
    if result["toc_unlisted"]:
        print(ui.yellow("\nNote: files missing from toc.toml: "
                        + ", ".join(result["toc_unlisted"])))
    if args.draft:
        for draft in result.get("drafts", []):
            print(ui.green(f"\nDraft stub written: {draft['path']}"))
        if not result.get("drafts"):
            print(ui.dim("\n(no drafts produced — LLM unavailable?)"))
    else:
        print(ui.dim("\n→ 'plan --draft' writes opening stubs for the top "
                     "items into _drafts/ (never touches the manuscript)."))
    _report_llm(llm)


def cmd_proposal(args):
    from . import proposals as prop

    db = _open_db(args)
    manuscript = _manuscript(db, args)
    mid = manuscript["id"]

    if args.action == "list":
        rows = prop.open_proposals(db, mid)
        if not rows:
            print("No open proposals. They arise when new material conflicts "
                  "with settled knowledge (edited definitions, retired concepts "
                  "recurring, rejected relationships argued again).")
            return
        print(ui.bold(f"Open proposals ({len(rows)}):"))
        for row in rows:
            summary, _ = prop.describe(row)
            print(f"  [{row['id'][:8]}] ({row['kind']}) {summary}")
        print(ui.dim("  → proposal review (interactive) · "
                     "proposal accept|dismiss <id>"))
        return

    if args.action == "review":
        import textwrap

        rows = prop.open_proposals(db, mid)
        if not rows:
            print("No open proposals to review.")
            return
        print(f"{len(rows)} open proposal(s).")
        print(ui.dim("Keys: [k]eep (adopt)  [e]dge (alias → generalizes)  "
                     "[r]eject (dismiss)  [s]kip (or Enter)  [x] quit"))
        adopted = dismissed = skipped = 0
        for index, row in enumerate(rows, start=1):
            summary, details = prop.describe(row)
            print(f"{ui.dim(f'[{index}/{len(rows)}]')} ({ui.cyan(row['kind'])}) "
                  f"{ui.bold(summary)}")
            for line in details:
                print(ui.dim(textwrap.fill(
                    line, width=ui.term_width(),
                    initial_indent="    ", subsequent_indent="      ",
                )))
            while True:
                try:
                    choice = input("> ").strip().lower()
                except EOFError:
                    choice = "x"
                if choice in ("k", "keep", "adopt", "accept"):
                    print(f"  {ui.green(prop.adopt(db, mid, row))}")
                    adopted += 1
                    break
                if choice in ("e", "edge") and row["kind"] == "alias":
                    print(f"  {ui.cyan(prop.demote_to_edge(db, mid, row))}")
                    adopted += 1
                    break
                if choice in ("r", "reject", "dismiss"):
                    print(f"  {ui.yellow(prop.dismiss(db, mid, row))}")
                    dismissed += 1
                    break
                if choice in ("s", "skip", ""):
                    skipped += 1
                    break
                if choice in ("x", "quit"):
                    skipped += len(rows) - index + 1
                    print(f"Proposals: adopted {adopted}, dismissed {dismissed}, "
                          f"skipped {skipped}.")
                    return
                print(ui.dim("  ? use k / e / r / s / x"))
        print(f"Proposals: adopted {adopted}, dismissed {dismissed}, skipped {skipped}.")
        return

    # accept / dismiss by id
    row = _find_by_prefix(db, "knowledge_proposals", args.id, mid)
    if row["state"] != "open":
        sys.exit(f"error: proposal is already {row['state']}.")
    if args.action == "accept":
        print(prop.adopt(db, mid, dict(row)))
    elif args.action == "edge":
        print(prop.demote_to_edge(db, mid, dict(row)))
    else:
        print(prop.dismiss(db, mid, dict(row), reason=args.why))


def cmd_style(args):
    db = _open_db(args)
    manuscript = _manuscript(db, args)
    try:
        if args.action == "guides":
            overview = api.style_overview(db, manuscript)
            if not overview["guides"]:
                print("No style guides yet. Create one: "
                      "style guide \"House style\"")
                return
            print(ui.bold("Style guides:"))
            for guide in overview["guides"]:
                parent = f" ← {guide['parent']}" if guide["parent"] else " (root)"
                print(f"  [{guide['id'][:8]}] {guide['name']}{parent} — "
                      f"{guide['elements']} element(s)")
            if overview["attachments"]:
                print(ui.bold("Attachments:"))
                for a in overview["attachments"]:
                    print(f"  {a['file']} → {a['guide']}")
        elif args.action == "guide":
            row = api.define_style_guide(db, manuscript, args.params[0],
                                         parent=args.parent)
            parent = f" (parent: {args.parent})" if args.parent else " (root)"
            print(f"Style guide [{row['id'][:8]}] '{row['name']}'{parent}")
        elif args.action == "attach":
            result = api.attach_style(db, manuscript, args.params[0], args.params[1])
            print(f"{result['file']} now follows '{result['guide']}'.")
        elif args.action == "add":
            row = api.add_style_element(
                db, manuscript, args.params[0], args.params[1],
                guide_name=args.guide, file=args.file,
                notes=args.notes, overrides=args.overrides,
            )
            scope = args.guide or f"{args.file} (file-local)"
            print(f"Style element [{row['id'][:8]}] ({row['aspect']}, {scope}): "
                  f"{row['statement']}")
        elif args.action == "show":
            result = api.style_show(db, manuscript, args.params[0])
            if not result["elements"]:
                print(f"No style elements govern {args.params[0]}.")
                return
            print(ui.bold(f"Effective style for {result['file']} "
                          f"({len(result['elements'])} element(s), nearest first):"))
            for element in result["elements"]:
                if element["file"]:
                    where = f"{element['file']} (file-local)"
                else:
                    guide_row = db.one("SELECT name FROM style_guides WHERE id = ?",
                                       (element["guide_id"],))
                    where = guide_row["name"] if guide_row else "?"
                print(f"  [{element['id'][:8]}] ({element['aspect']}, {where}) "
                      f"{element['statement']}")
                if element["notes"]:
                    print(ui.dim(f"      {element['notes']}"))
        elif args.action == "retire":
            result = api.retire_style_element(db, manuscript, args.params[0])
            print(f"Retired style element: {result['statement']}")
        elif args.action == "move":
            # Re-scope an element to another guide (or a file). Elements
            # imported at house scope routinely belong to one part.
            if not (args.guide or args.file):
                sys.exit("usage: style move <id> --guide NAME | --file FILE")
            result = api.move_style_element(
                db, manuscript, args.params[0], guide_name=args.guide,
                file=args.file)
            print(ui.green("moved") + f" [{result['id'][:8]}] → "
                  + (f"guide '{result['guide']}'" if result.get("guide")
                     else f"file {result['file']}"))
    except (LookupError, ValueError) as err:
        sys.exit(str(err))


def cmd_status(args):
    db = _open_db(args)
    manuscript = _manuscript(db, args)
    mid = manuscript["id"]
    session = ses.active_session(db, mid)
    counts = {
        table: db.one(f"SELECT COUNT(*) AS n FROM {table} WHERE manuscript_id = ?", (mid,))["n"]
        for table in (
            "manuscript_versions", "editorial_transitions", "editorial_episodes",
            "declared_intents", "concept_nodes", "concept_edges",
            "editorial_policies", "guidance_history", "editorial_reviews", "evidence",
        )
    }
    print(f"Manuscript: {manuscript['name']} ({manuscript['path']})")
    print(f"Session: {'active — ' + session['id'] if session else 'none'}")
    print(
        f"Versions: {counts['manuscript_versions']} | "
        f"Transitions: {counts['editorial_transitions']} | "
        f"Episodes: {counts['editorial_episodes']} | "
        f"Intents: {counts['declared_intents']}"
    )
    print(
        f"Concepts: {counts['concept_nodes']} | Edges: {counts['concept_edges']} | "
        f"Policies: {counts['editorial_policies']}"
    )
    print(
        f"Guidance: {counts['guidance_history']} | Reviews: {counts['editorial_reviews']} | "
        f"Evidence: {counts['evidence']}"
    )
    from . import improvements as imp
    open_tasks = imp.open_count(db)
    if open_tasks:
        print(ui.dim(f"Improvement tasks: {open_tasks} open (authorlm improve list)"))


def cmd_improve(args):
    """Self-improvement tasks: confirmed tool defects with a structured
    repro. Tool-knowledge only — never surfaces in briefings/guidance."""
    db = _open_db(args)

    if args.action == "add":
        for flag in ("title", "evidence", "given", "observed", "expected"):
            if not getattr(args, flag, None):
                sys.exit(f"error: improve add requires --{flag}")
        manuscript = None
        if getattr(args, "manuscript", None):
            manuscript = _manuscript(db, args)
        else:
            rows = db.all("SELECT * FROM manuscripts")
            manuscript = dict(rows[0]) if len(rows) == 1 else None
        task = api.file_improvement(
            db, title=args.title, evidence=args.evidence, given=args.given,
            observed=args.observed, expected=args.expected,
            manuscript=manuscript,
        )
        print(f"Filed improvement task {task['id']}: {task['title']}")
        return

    if args.action == "list":
        tasks = api.list_improvements(db, args.status or "active")
        if not tasks:
            print("No improvement tasks"
                  + (f" with status '{args.status}'" if args.status else " open") + ".")
            return
        for task in tasks:
            print(f"  [{task['id']}] ({task['status']}) {task['title']}")
            if task["resolution"]:
                print(ui.dim(f"      resolution: {task['resolution']}"))
        return

    if not args.id:
        sys.exit(f"error: improve {args.action} needs a task id (see 'improve list')")

    try:
        if args.action == "show":
            from . import improvements as imp
            task = imp.find_task(db, args.id)
            print(f"[{task['id']}] ({task['status']}) {task['title']}")
            print(f"Filed: {task['created_at']}"
                  + (f"  session: {task['session_id']}" if task["session_id"] else ""))
            print(f"\nEvidence:\n{task['evidence']}")
            print(f"\nGiven: {task['given']}\nObserved: {task['observed']}"
                  f"\nExpected: {task['expected']}")
            if task["resolution"]:
                print(f"\nResolution: {task['resolution']}")
            return

        if args.action == "run":
            result = api.improvement_bundle(db, args.id)
            print(result["bundle"])
            print(ui.dim(f"\n[task {result['task_id']} is now {result['status']} — "
                         "paste the bundle into a Claude Code session in the repo]"),
                  file=sys.stderr)
            return

        # propose | close | dismiss
        result = api.resolve_improvement(db, args.id, args.action, args.note)
        print(f"Task {result['task_id']}: {result['message']}")
    except (LookupError, ValueError) as err:
        sys.exit(str(err))


def cmd_diff(args):
    """Colored unified diff between two collected versions (default: the
    last two), optionally restricted to one file."""
    import difflib

    db = _open_db(args)
    manuscript = _manuscript(db, args)
    versions = db.all(
        "SELECT * FROM manuscript_versions WHERE manuscript_id = ? ORDER BY version_no",
        (manuscript["id"],),
    )
    if not versions:
        sys.exit("error: no versions collected yet.")

    numbers, file_filter = [], None
    for param in args.params:
        token = param.lstrip("v")
        if token.isdigit():
            numbers.append(int(token))
        else:
            file_filter = param
    by_no = {v["version_no"]: v for v in versions}
    for number in numbers:
        if number not in by_no:
            sys.exit(f"error: no version v{number} (have v1..v{versions[-1]['version_no']}).")
    if len(numbers) >= 2:
        old_v, new_v = by_no[min(numbers)], by_no[max(numbers)]
    elif len(numbers) == 1:
        new_v = by_no[numbers[0]]
        old_v = by_no.get(numbers[0] - 1)
    else:
        new_v = versions[-1]
        old_v = versions[-2] if len(versions) > 1 else None

    old_files = loads(old_v["files"], {}) if old_v else {}
    new_files = loads(new_v["files"], {})
    old_label = f"v{old_v['version_no']}" if old_v else "∅"
    printed = False
    for name in sorted(set(old_files) | set(new_files)):
        if file_filter and file_filter.lower() not in name.lower():
            continue
        old_text, new_text = old_files.get(name, ""), new_files.get(name, "")
        if old_text == new_text:
            continue
        printed = True
        for line in difflib.unified_diff(
            old_text.splitlines(), new_text.splitlines(),
            fromfile=f"{old_label}/{name}", tofile=f"v{new_v['version_no']}/{name}",
            lineterm="",
        ):
            if line.startswith(("+++", "---")):
                print(ui.bold(line))
            elif line.startswith("@@"):
                print(ui.cyan(line))
            elif line.startswith("+"):
                print(ui.green(line))
            elif line.startswith("-"):
                print(ui.yellow(line))
            else:
                print(line)
    if not printed:
        print(f"No differences between {old_label} and v{new_v['version_no']}"
              + (f" for '{file_filter}'" if file_filter else "") + ".")


def cmd_log(args):
    db = _open_db(args)
    manuscript = _manuscript(db, args)
    rows = db.all(
        "SELECT * FROM editorial_transitions WHERE manuscript_id = ? "
        "ORDER BY created_at DESC LIMIT ?",
        (manuscript["id"], args.limit),
    )
    if not rows:
        print("No transitions recorded yet.")
    for row in reversed(rows):
        print(f"{row['created_at']}  [{row['kind']:>11}] {row['location']}: {row['summary']}")


def cmd_history(args):
    db = _open_db(args)
    manuscript = _manuscript(db, args)
    action = args.action or "list"

    if action == "list":
        for row in db.all(
            "SELECT * FROM manuscript_versions WHERE manuscript_id = ? ORDER BY version_no",
            (manuscript["id"],),
        ):
            files = loads(row["files"], {})
            print(
                f"v{row['version_no']}  {row['created_at']}  {row['checksum'][:12]}  "
                f"{len(files)} file(s)  session={row['session_id'] or '-'}"
            )
        return

    if not args.version:
        sys.exit(f"error: 'history {action}' needs a version number, e.g. 'history {action} 3'.")
    number = int(args.version.lstrip("v"))

    if action == "show":
        try:
            version = api.get_version(db, manuscript, number)
        except LookupError as err:
            sys.exit(f"error: {err}")
        for name, text in sorted(loads(version["files"], {}).items()):
            print(ui.dim(f"--- {name} ---"))
            print(text)
        return

    # restore
    try:
        report = api.restore_version(db, manuscript, number, _load_config(args))
    except LookupError as err:
        sys.exit(f"error: {err}")
    print(f"Restored v{number}'s content to disk and collected it as v{report['version_no']} "
          "— history advances, it is never rewound.")
    print(f"Detected {len(report['transitions'])} editorial transition(s) versus the prior revision.")


def cmd_triage_app(args):
    from .triage_server import run

    run(args.workspace, getattr(args, "manuscript", None), host=args.host,
        port=args.port, open_browser=not args.no_open)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="authorlm",
        description="AuthorLM — an editorial collaborator that learns your writing practice.",
    )
    parser.add_argument(
        "-w", "--workspace", default=None,
        help="directory holding AuthorLM's data — the .authorlm/ folder with "
             "the database and config. Defaults to your home directory, so "
             "all manuscripts share one database in ~/.authorlm regardless "
             "of where you run the command")
    parser.add_argument("-m", "--manuscript", help="manuscript name (needed only if several exist)")
    # Accept --workspace/--manuscript after the subcommand too (e.g.
    # 'session start -m X'). SUPPRESS keeps the subparser from
    # clobbering a value given before the subcommand.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-w", "--workspace", default=argparse.SUPPRESS,
                        help="data directory override (default: ~/.authorlm)")
    common.add_argument("-m", "--manuscript", default=argparse.SUPPRESS,
                        help="manuscript name (needed only if several exist)")
    sub = parser.add_subparsers(dest="command", required=True)
    original_add_parser = sub.add_parser

    # Verbs that send an LLM prompt announce it in --help and point at the
    # registry, so the words shaping the model are always one command away.
    from .prompt_registry import HELP_NOTE
    LLM_VERBS = {"init", "extract", "collect", "intent", "guide", "review",
                 "analyze", "lens", "sweep", "illus", "summarize", "doc",
                 "policy", "critique", "triage-app"}

    def add_parser(name, *a, **kw):
        kw.setdefault("parents", [common])
        if name in LLM_VERBS:
            desc = kw.get("description") or kw.get("help") or ""
            kw["description"] = (desc + "\n\n" + HELP_NOTE).strip()
            kw.setdefault("formatter_class", argparse.RawDescriptionHelpFormatter)
        return original_add_parser(name, *a, **kw)

    sub.add_parser = add_parser

    p = sub.add_parser("init", help="register a manuscript directory")
    p.add_argument("--name", required=True)
    p.add_argument("--path", required=True)
    p.add_argument("--no-extract", action="store_true",
                   help="skip automatic LLM concept extraction")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("unregister", help="remove a manuscript and ALL its data (clean slate)")
    p.add_argument("name", nargs="?", help="manuscript name to remove (or use -m)")
    p.set_defaults(func=cmd_unregister)

    p = sub.add_parser("session", help="start/end an authoring session")
    p.add_argument("action", choices=["start", "end"])
    p.set_defaults(func=cmd_session)

    p = sub.add_parser("briefing", help="show the learning briefing")
    p.set_defaults(func=cmd_briefing)

    p = sub.add_parser(
        "triage-app",
        help="open the interactive Concept and Edge Triage App",
        description="Open a local, schema-driven grid for concept and edge "
                    "analysis and triage. Analysis runs only when requested.")
    p.add_argument("--host", default="127.0.0.1",
                   help="local interface to bind (default: 127.0.0.1)")
    p.add_argument("--port", type=int, default=0,
                   help="local port (default: choose a free port)")
    p.add_argument("--no-open", action="store_true",
                   help="print the URL without opening a browser")
    p.set_defaults(func=cmd_triage_app)

    p = sub.add_parser("intent", help="declare/complete/abandon/list writing intents")
    p.add_argument("action", choices=["declare", "complete", "abandon", "retire", "list"])
    p.add_argument("statement", nargs="?",
                   help="intent statement (declare) or id prefix (complete/abandon)")
    p.add_argument("--outcome", help="outcome note (complete) or reason (abandon)")
    p.set_defaults(func=cmd_intent)

    p = sub.add_parser(
        "critique",
        help="external critique: import a report as proposed items, triage "
             "them, then the essay-by-essay edit pass — run → triage "
             "--edits → write → (read in Docs) → resolve "
             "(docs/critique-pass-design.md)")
    p.add_argument("action",
                   choices=["import", "status", "list", "triage",
                            "show", "reason", "reopen",
                            "run", "edits", "write", "resolve", "rollback"])
    p.add_argument("target", nargs="?",
                   help="import: manifest JSON | show/reason/reopen: item id "
                        "prefix | run/edits/write/resolve/rollback: essay file")
    p.add_argument("--edits", metavar="ESSAY",
                   help="triage: verdicts on the STAGED EDITS of this essay "
                        "(k/r/e/u/s/x) instead of critique items")
    p.add_argument("--undo", nargs="+", metavar="N|ID",
                   help="triage --edits: send edits back to proposed")
    p.add_argument("--force", action="store_true",
                   help="run: proceed past the preflight gate WITHOUT the "
                        "unconfirmed items (never with them)")
    p.add_argument("--query", help="show: search critique items by text")
    p.add_argument("--scope",
                   help="list/triage: only intents scoped to this file; "
                        "'manuscript' = only manuscript-wide items (the "
                        "global sitting)")
    p.add_argument("--accept", nargs="+", metavar="N|ID",
                   help="triage: accept items by 'critique list' number or "
                        "id prefix (non-interactive)")
    p.add_argument("--reject", nargs="+", metavar="N|ID",
                   help="triage: reject items by list number or id prefix "
                        "(requires --reason)")
    p.add_argument("--reason", help="triage: the author's verbatim reason for --reject")
    p.add_argument("--revise", metavar="N|ID",
                   help="triage: accept one item with the author's own "
                        "wording (requires --text; provenance flips to author)")
    p.add_argument("--text", help="triage: replacement wording for --revise")
    p.set_defaults(func=cmd_critique)

    p = sub.add_parser(
        "summarize",
        help="essay summaries — the editor's working memory of the whole "
             "book, built autoregressively in reading order "
             "(docs/critique-pass-design.md §4)")
    p.add_argument("action", choices=["status", "show", "rebuild"])
    p.add_argument("file", nargs="?",
                   help="show: the unit; rebuild: one unit only (against "
                        "current prior summaries; downstream marked stale)")
    p.add_argument("--all", action="store_true",
                   help="rebuild: every unit from scratch (default reuses "
                        "fresh summaries and rebuilds from the first "
                        "missing/stale one onward)")
    p.set_defaults(func=cmd_summarize)

    p = sub.add_parser(
        "prompts",
        help="every LLM prompt AuthorLM sends, and where to read/edit each "
             "(file prompts under authorlm/prompts/, inline ones in modules)")
    p.add_argument("action", nargs="?", default="list", choices=["list", "show"])
    p.add_argument("name", nargs="?", help="show: prompt name from the list")
    p.set_defaults(func=cmd_prompts)

    p = sub.add_parser("concept", help="manage the Concept Graph")
    p.add_argument("action",
                   choices=["add", "link", "list", "show", "confirm", "unconfirm",
                            "reject-edge", "reject", "retire", "revive",
                            "triage", "edit",
                            "alias", "merge"])
    p.add_argument("params", nargs="*",
                   help="add/show/confirm/unconfirm/reject-edge/edit: name or edge id; "
                        "link: <from> <relation> <to>; retire: one or more names; "
                        "alias: <name> <alias>…; merge: <canonical> <duplicate>")
    p.add_argument("--kind", default=None,
                   choices=sorted(cg.NODE_KINDS),
                   help="node kind — used by add, and by confirm to retype")
    p.add_argument("--notes", help="free-text notes stored on the concept")
    p.add_argument("--all", action="store_true",
                   help="list: include retired/rejected; confirm: all unconfirmed")
    p.add_argument("--all-kind", metavar="KIND", choices=sorted(cg.NODE_KINDS),
                   help="confirm/retire every unconfirmed extracted concept of this kind")
    p.add_argument("--nodes", action="store_true", help="triage: concepts only")
    p.add_argument("--edges", action="store_true", help="triage: relationships only")
    p.add_argument("--deterministic", action="store_true",
                   help="triage: " + triage_service.DETERMINISTIC_TRIAGE_HELP)
    p.add_argument("--apply", action="store_true",
                   help="triage --deterministic: apply every reported decision")
    p.add_argument("--remove", nargs="+", metavar="ALIAS",
                   help="alias: withdraw these aliases instead of adding")
    p.set_defaults(func=cmd_concept)

    p = sub.add_parser("collect", help="snapshot the manuscript and detect transitions")
    p.add_argument("--auto", action="store_true", help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_collect)

    p = sub.add_parser(
        "write",
        help="beat-by-beat co-writing loop: propose → author verdict → append "
             "(docs/autoregressive-writing-design.md)")
    p.add_argument("action",
                   choices=["start", "plan", "status", "propose", "accept",
                            "reject", "learn", "complete", "abandon"])
    p.add_argument("params", nargs="*", help="start: <file>")
    p.add_argument("--intent", help="start: intent id prefix (required)")
    p.add_argument("--writeup", help="writeup id prefix (default: the active writeup)")
    p.add_argument("--why", help="propose: which concepts the draft realizes, "
                                 "which precedent it follows (required)")
    p.add_argument("--reason", help="reject: the author's why, verbatim "
                                    "(required); accept: optional")
    p.add_argument("--replace", action="store_true",
                   help="plan: replace the remaining (unwritten) beats")
    p.set_defaults(func=cmd_write)

    p = sub.add_parser("diff", help="colored diff between collected versions "
                                    "(default: the last two)")
    p.add_argument("params", nargs="*",
                   help="versions and/or a file filter, e.g.: diff · diff v4 · "
                        "diff v3 v5 · diff v5 sermons")
    p.set_defaults(func=cmd_diff)

    p = sub.add_parser("doc", help="manage chapter files: list, add, retire, revive, "
                                   "push/pull (Google Docs), create-manuscript, auth")
    p.add_argument("action", choices=["list", "add", "retire", "revive",
                                      "push", "pull", "open", "auth",
                                      "create-manuscript", "threads",
                                      "propose", "decide"])
    p.add_argument("name", nargs="?", help="file name (add) or name fragment "
                                           "(retire/revive/push/pull)")
    p.add_argument("--title", help="heading for a new document / Doc title "
                                   "(push, create-manuscript)")
    p.add_argument("--open", action="store_true",
                   help="open the Google Doc in your browser after push")
    p.add_argument("--force", action="store_true",
                   help="pull: take the Doc's version even if the local file "
                        "changed since the push (local edits stay in history)")
    p.add_argument("--approve", action="store_true",
                   help="decide: approve the thread (apply and close)")
    p.add_argument("--decline", action="store_true",
                   help="decide: decline the thread (revert and close)")
    p.add_argument("--reason", help="decide: the author's reasoning, "
                                    "verbatim — feeds the scoped distiller")
    p.add_argument("--no-comments", action="store_true",
                   help="pull: skip harvesting open Doc comments (default: "
                        "ingest them and resolve each with a receipt)")
    p.set_defaults(func=cmd_doc)

    p = sub.add_parser(
        "sweep",
        help="sweep framework (docs/sweep-framework.md): hygiene + "
             "readiness (pure, zero tokens), ontology (narrowing auditor)")
    p.add_argument("action", choices=["hygiene", "readiness", "ontology"])
    p.add_argument("file", nargs="?",
                   help="ontology: audit this file (default: files changed "
                        "in the last collected revision)")
    p.add_argument("--apply", action="store_true",
                   help="hygiene: retire the ungrounded concepts and reject "
                        "the ungrounded edges (default: report only)")
    p.set_defaults(func=cmd_sweep)

    p = sub.add_parser(
        "lens",
        help="author-defined lenses (_lenses/*.md prompts): add, list, "
             "run <name> <file>, register (external findings on stdin), "
             "review <n>")
    p.add_argument("action",
                   choices=["add", "list", "run", "register", "review"])
    p.add_argument("name", nargs="?",
                   help="lens name (add/run/register) or finding index "
                        "(review)")
    p.add_argument("file", nargs="?", help="manuscript file (run/register)")
    p.add_argument("--accept", action="store_true")
    p.add_argument("--reject", action="store_true")
    p.add_argument("--modify", action="store_true")
    p.add_argument("--defer", action="store_true")
    p.add_argument("--explain", help="the author's reasoning, verbatim — "
                                     "the highest-value evidence")
    p.set_defaults(func=cmd_lens)

    p = sub.add_parser(
        "export",
        help="publishing exports: md/docx/epub built locally (pandoc) with "
             "picked illustrations embedded; settings in _exports/settings.toml")
    p.add_argument("action", choices=["show", "set", "md", "docx", "epub"])
    p.add_argument("key", nargs="?", help="setting name (set)")
    p.add_argument("value", nargs="?", help="setting value (set)")
    p.add_argument("--variant", choices=["images", "slots", "stripped"],
                   help="override the illustration variant for this export")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser(
        "illus",
        help="illustration slots ([Illustration: …] tags): list, render "
             "(LLM image → _illustrations/), pick a candidate, prune, "
             "prompt (the exact composed prompt a render would send)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="examples by action:\n"
               "  illus list                      slot status: rendered, "
               "picked, unrendered\n"
               "  illus render                    render every unrendered "
               "slot (one candidate each)\n"
               "  illus render pendulum -n 3      three candidates for the "
               "slot matching 'pendulum'\n"
               "  illus render pendulum --from 2  evolve candidate 2 under "
               "the current law\n"
               "  illus pick pendulum 2           pin candidate 2 (renders "
               "never move a pick)\n"
               "  illus prompt pendulum           show the exact composed "
               "prompt a render sends\n"
               "  illus prune                     delete unpicked "
               "candidates and orphaned files\n"
               "  illus scan [file]               spot-finder: stage "
               "placement proposals (all main matter)\n"
               "  illus triage                    walk proposals one by "
               "one: k / v / r / s / x\n"
               "  illus externalize dial          move a description to "
               "_illustrations/prompts/<slug>.md (tag keeps excerpt ⇢ ref)\n"
               "  illus triage --accept 1 2 --revise 3 \"…\" --reject 4 "
               "--reason \"…\"   bulk verdicts")
    p.add_argument("action",
                   choices=["list", "render", "rerender", "pick", "prune",
                            "prompt", "scan", "triage", "externalize"])
    p.add_argument("name", nargs="?",
                   help="prompt fragment selecting a slot (render/pick); "
                        "render without it does every unrendered slot; "
                        "scan: one file (default: all main matter)")
    p.add_argument("candidate", nargs="?", type=int,
                   help="candidate number (pick)")
    p.add_argument("-n", "--count", type=int, default=1,
                   help="render: how many candidates to generate")
    p.add_argument("--from", dest="from_n", type=int, metavar="N",
                   help="render: evolve candidate N under the current "
                        "illustration law (image-conditioned continuity)")
    p.add_argument("--local", action="store_true",
                   help="rerender: skip the Doc push at the end")
    p.add_argument("--accept", nargs="+", type=int, metavar="N",
                   help="triage: accept these proposal numbers")
    p.add_argument("--accept-all-except", nargs="*", type=int, metavar="N",
                   dest="accept_all_except",
                   help="triage: accept every open proposal except these")
    p.add_argument("--revise", nargs=2, metavar=("N", "DESC"),
                   action="append",
                   help="triage: accept N with a revised description "
                        "(modified acceptance — the diff is evidence)")
    p.add_argument("--reject", nargs="+", type=int, metavar="N",
                   help="triage: reject these proposal numbers")
    p.add_argument("--reason",
                   help="triage: the author's words for a rejection "
                        "(recorded verbatim as evidence)")
    p.set_defaults(func=cmd_illus)

    p = sub.add_parser(
        "profile",
        help="manuscript profiles: declared context (market intelligence, "
             "positioning) in _profiles/ — synced with a separate workspace Doc")
    p.add_argument("action", choices=["list", "show", "set", "push", "pull"])
    p.add_argument("key", nargs="?",
                   help="profile key, e.g. 'market' (set/show; optional for "
                        "push/pull)")
    p.set_defaults(func=cmd_profile)

    p = sub.add_parser("extract",
                       help="LLM-extract concepts/links (incremental: only files "
                            "changed since the last extraction)")
    p.add_argument("files", nargs="*",
                   help="restrict extraction to these manuscript files")
    p.add_argument("--full", action="store_true",
                   help="reprocess the whole manuscript, not just changes")
    p.add_argument("--edges-only", action="store_true",
                   help="extract only relationships among existing concepts "
                        "(no new concepts; does not advance the watermark)")
    p.add_argument("--aliases", action="store_true",
                   help="sweep the full text for aliasing statements "
                        "(naming/defining sentences) and file merge "
                        "proposals; extracts nothing else and does not "
                        "advance the watermark")
    p.set_defaults(func=cmd_extract)

    p = sub.add_parser("guide", help="generate editorial guidance (or abstain)")
    p.set_defaults(func=cmd_guide)

    p = sub.add_parser("review", help="review a suggestion from the latest batch")
    p.add_argument("index", type=int)
    p.add_argument("--accept", action="store_true")
    p.add_argument("--reject", action="store_true")
    p.add_argument("--modify", action="store_true")
    p.add_argument("--defer", action="store_true")
    p.add_argument("--explain", help="why — becomes the highest-quality evidence")
    p.set_defaults(func=cmd_review)

    p = sub.add_parser(
        "policy", help="list / answer questions / curate (retire, merge, convert)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="example:\n"
               "  policy list                     shows each policy's [id] and its\n"
               "                                  numbered open questions (Q1, Q2, …)\n"
               '  policy answer pol-d970 "Only inside worked examples" --index 2\n'
               "                                  closes Q2 of policy pol-d970; the\n"
               "                                  answer becomes declared evidence\n"
               '  policy retire pol-4c5d --reason "garbled inference"\n'
               "  policy merge pol-8e69 pol-2c85  fold the duplicate (first) into\n"
               "                                  the canonical (second)\n"
               '  policy convert pol-3b85 --aspect formatting --guide "Essays"\n'
               "                                  policy becomes a ratified style\n"
               "                                  element; the policy is retired",
    )
    p.add_argument("action", choices=["list", "answer", "retire", "merge",
                                      "convert"])
    p.add_argument("id", nargs="?",
                   help="policy id prefix, from 'policy list'")
    p.add_argument("answer", nargs="?",
                   help="answer text (answer) / canonical policy id (merge)")
    p.add_argument("--index", type=int, default=1,
                   help="which open question to answer — the Q number shown by "
                        "'policy list' (default 1)")
    p.add_argument("--reason", help="why (retire: required)")
    p.add_argument("--aspect", help="convert: style aspect for the new element")
    p.add_argument("--statement", help="convert: reworded statement "
                                       "(default: the policy statement)")
    p.add_argument("--guide", help="convert: owning style guide name")
    p.add_argument("--file", help="convert: file for a file-local element")
    p.add_argument("--notes", help="convert: free-text notes (inspect/avoid hints)")
    p.set_defaults(func=cmd_policy)

    p = sub.add_parser(
        "improve", help="self-improvement tasks: confirmed tool defects with repro",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="lifecycle: open → in_progress (run) → proposed (fixing agent)\n"
               "           → resolved (author closes) | dismissed (reason required)\n"
               "example:\n"
               "  improve add --title 'Gap false positive' --evidence '…' \\\n"
               "      --given '…' --observed '…' --expected '…'\n"
               "  improve run it-abc123          print the self-contained fix bundle\n"
               "  improve propose it-abc123 --note 'fix summary; test name'\n"
               "  improve close it-abc123        author confirms the proposed fix",
    )
    p.add_argument("action", choices=["add", "list", "show", "run",
                                      "propose", "close", "dismiss"])
    p.add_argument("id", nargs="?", help="task id prefix (from 'improve list')")
    p.add_argument("--title", help="one-line defect title (add)")
    p.add_argument("--evidence", help="what happened, with file:line specifics (add)")
    p.add_argument("--given", help="repro: input/state (add)")
    p.add_argument("--observed", help="repro: wrong behavior (add)")
    p.add_argument("--expected", help="repro: author's verdict, verbatim (add)")
    p.add_argument("--status", help="filter for list "
                                    "(open|in_progress|proposed|resolved|dismissed|active)")
    p.add_argument("--note", help="fix summary + test name (propose), confirmation "
                                  "note (close), or reason (dismiss)")
    p.set_defaults(func=cmd_improve)

    p = sub.add_parser("shell", help="interactive authoring session (briefing + auto-collect)")
    p.add_argument("--no-watch", action="store_true", help="disable the file watcher")
    p.add_argument("--debounce", type=float, default=3.0,
                   help="seconds of quiet before auto-collect (default 3)")
    p.set_defaults(func=cmd_shell)

    p = sub.add_parser("watch", help="auto-collect on manuscript changes (Ctrl-C to stop)")
    p.add_argument("--debounce", type=float, default=3.0)
    p.add_argument("--interval", type=float, default=1.0)
    p.set_defaults(func=cmd_watch)

    p = sub.add_parser("export-obsidian",
                       help="export the Concept Graph as wikilinked Obsidian notes")
    p.add_argument("--dir", help="output directory (default: <manuscript>/_concepts)")
    p.set_defaults(func=cmd_export_obsidian)

    p = sub.add_parser("help", help="list commands, or show one command's options")
    p.add_argument("topic", nargs="?", help="command name")
    p.set_defaults(func=cmd_help)

    p = sub.add_parser("completion",
                       help="print the bash completion script (eval it in ~/.bash_profile)")
    p.add_argument("shell_name", nargs="?", default="bash", choices=["bash"])
    p.set_defaults(func=cmd_completion)

    p = sub.add_parser("_manuscripts")  # hidden: completion helper
    p.set_defaults(func=cmd_manuscripts)

    p = sub.add_parser("_ids")  # hidden: completion helper
    p.add_argument("kind", choices=["intents", "policies", "concepts",
                                    "confirmables", "edges", "docs", "docs-retired",
                                    "proposals"])
    p.set_defaults(func=cmd_ids)

    p = sub.add_parser("analyze",
                       help="infer editorial decisions/patterns from closed episodes "
                            "(runs automatically at intent complete / session end)")
    p.set_defaults(func=cmd_analyze)

    p = sub.add_parser("plan",
                       help="writing plan: what to write next and where it "
                            "belongs (graph + TOC placement)")
    p.add_argument("--draft", action="store_true",
                   help="LLM-draft opening stubs for the top items into _drafts/")
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("proposal",
                       help="review conflicts between new material and settled knowledge")
    p.add_argument("action", choices=["list", "review", "accept", "edge", "dismiss"])
    p.add_argument("id", nargs="?", help="proposal id prefix (accept/edge/dismiss)")
    p.add_argument("--why", help="reason when dismissing (recorded as evidence)")
    p.set_defaults(func=cmd_proposal)

    p = sub.add_parser("style", help="style guides: ratified prose law per file")
    p.add_argument("action",
                   choices=["guides", "guide", "attach", "add", "show", "retire",
                            "move"])
    p.add_argument("params", nargs="*",
                   help="guide: <name>; attach: <file> <guide>; "
                        "add: <aspect> \"statement\"; show: <file>; retire: <id>")
    p.add_argument("--parent", help="guide: parent guide name")
    p.add_argument("--guide", help="add: owning guide name")
    p.add_argument("--file", help="add: file for a file-local element")
    p.add_argument("--notes", help="add: free-text notes (inspect/avoid hints)")
    p.add_argument("--overrides",
                   help="add: id (prefix) of the inherited element this displaces")
    p.set_defaults(func=cmd_style)

    p = sub.add_parser("status", help="show current state")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("log", help="narrative log of recent transitions")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_log)

    p = sub.add_parser("history", help="list/show/restore collected manuscript versions")
    p.add_argument("action", nargs="?", default="list", choices=["list", "show", "restore"])
    p.add_argument("version", nargs="?", help="version number, e.g. '3' or 'v3' (show/restore)")
    p.set_defaults(func=cmd_history)

    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    # Global home database: unless -w is given, data lives in ~/.authorlm so
    # every command works from any directory and manuscripts can live anywhere.
    if getattr(args, "workspace", None) is None:
        args.workspace = str(Path.home())
    # Destructure the concept subcommand's free-form positionals per action.
    if args.command == "concept":
        if args.action == "reject":
            args.action = "reject-edge"  # alias
        params = args.params
        args.name = params[0] if params else None
        args.relation = params[1] if len(params) > 1 else None
        args.to_name = params[2] if len(params) > 2 else None
        args.names = params
        if (args.deterministic or args.apply) and args.action != "triage":
            sys.exit("usage: concept triage --deterministic [--apply] "
                     "[--nodes|--edges]")
        if args.apply and not args.deterministic:
            sys.exit("error: concept triage --apply requires --deterministic")
        if args.action == "link":
            if len(params) != 3:
                sys.exit("usage: concept link <from> <relation> <to>")
            args.from_name = args.name
        if args.action in ("add", "reject-edge", "show", "unconfirm") and not args.name:
            sys.exit(f"usage: concept {args.action} <name-or-id-prefix>")
        if args.action == "confirm" and not (args.name or args.all or args.all_kind):
            sys.exit("usage: concept confirm <name-or-edge-prefix> | --all | --all-kind KIND")
        if args.action == "revive" and not args.names:
            sys.exit("usage: concept revive <name>…")
        if args.action == "retire" and not (args.names or args.all_kind):
            sys.exit("usage: concept retire <name>… | --all-kind KIND")
        if args.action == "edit" and not (args.name and (args.notes is not None or args.kind)):
            sys.exit("usage: concept edit <name> --notes \"...\" [--kind KIND]")
        if args.action == "alias" and len(params) < 2 \
                and not getattr(args, "remove", None):
            sys.exit("usage: concept alias <name> <alias>… "
                     "| concept alias <name> --remove <alias>…")
        if args.action == "merge" and len(params) != 2:
            sys.exit("usage: concept merge <canonical> <duplicate>")
    if args.command == "style":
        required = {"guide": 1, "attach": 2, "add": 2, "show": 1, "retire": 1,
                    "move": 1}
        if len(args.params) < required.get(args.action, 0):
            sys.exit(f"usage: style {args.action} — see 'style --help'")
        if args.action == "add" and bool(args.guide) == bool(args.file):
            sys.exit('usage: style add <aspect> "statement" '
                     "--guide NAME | --file FILE")
    if args.command == "proposal" and args.action in ("accept", "edge", "dismiss") and not args.id:
        sys.exit(f"usage: proposal {args.action} <id-prefix>")
    if args.command == "intent" and args.action == "retire":
        args.action = "abandon"  # alias, matching 'concept retire' muscle memory
    if args.command == "intent" and args.action in ("declare", "complete", "abandon") and not args.statement:
        sys.exit(f"usage: intent {args.action} <statement-or-id>")
    if args.command == "intent" and args.action in ("complete", "abandon"):
        args.id = args.statement
    if args.command == "policy" and args.action == "answer" and not (args.id and args.answer):
        sys.exit("usage: policy answer <id-prefix> \"answer text\"")
    if args.command == "policy" and args.action == "retire" and not (args.id and args.reason):
        sys.exit('usage: policy retire <id-prefix> --reason "why"')
    if args.command == "policy" and args.action == "merge" and not (args.id and args.answer):
        sys.exit("usage: policy merge <duplicate-id-prefix> <canonical-id-prefix>")
    if args.command == "policy" and args.action == "convert" and not (args.id and args.aspect):
        sys.exit("usage: policy convert <id-prefix> --aspect ASPECT "
                 "[--guide NAME | --file FILE]")
    import time as _time

    from . import tracelog

    def _trace(ok: bool, error: str | None = None) -> None:
        tracelog.record(
            args.command, surface="cli",
            workspace=getattr(args, "workspace", None),
            action=getattr(args, "action", None),
            manuscript=getattr(args, "manuscript", None),
            duration_ms=int((_time.monotonic() - t0) * 1000),
            ok=ok, error=error)

    t0 = _time.monotonic()
    try:
        args.func(args)
    except BaseException as err:
        clean_exit = isinstance(err, SystemExit) and not err.code
        _trace(clean_exit, None if clean_exit
               else f"{type(err).__name__}: {err}")
        raise
    _trace(True)


if __name__ == "__main__":
    main()
