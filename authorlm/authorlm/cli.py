"""AuthorLM CLI — the single orchestrator (RFC AuthorLM §23.9).

Linear capability sequence, pure functions between stages, every stage
producing an inspectable artifact in SQLite:

    collect() → build_transitions() → build_episodes() → retrieve()
             → infer_intent() → learn() → generate_guidance()
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
from pathlib import Path

from . import api
from . import concepts as cg
from . import policies as pol
from . import sessions as ses
from . import ui
from .briefing import build_briefing
from .db import Database, ko_fields, loads
from .extraction import VALID_KINDS, extract_concepts
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
        + "."
    )
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


def _session_last_activity(db: Database, session: dict) -> str:
    candidates = [session["started_at"]]
    for table in ("manuscript_versions", "guidance_history", "declared_intents"):
        row = db.one(
            f"SELECT MAX(created_at) AS latest FROM {table} WHERE session_id = ?",
            (session["id"],),
        )
        if row and row["latest"]:
            candidates.append(row["latest"])
    return max(candidates)


def _expire_idle_session(db: Database, manuscript: dict, args) -> None:
    """Lazy idle expiry: if the active session has been quiet longer than the
    threshold, close it retroactively at its last activity time — announced,
    with the normal closing pipeline (episode analysis) run."""
    from datetime import datetime, timezone

    session = ses.active_session(db, manuscript["id"])
    if not session:
        return
    idle_hours = _load_config(args).get("session", {}).get("idle_hours", 3)
    last = _session_last_activity(db, dict(session))
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            last_dt = datetime.strptime(last, fmt).replace(tzinfo=timezone.utc)
            break
        except ValueError:
            continue
    else:
        return
    idle = (datetime.now(timezone.utc) - last_dt).total_seconds() / 3600
    if idle < idle_hours:
        return
    ses.end_session(db, manuscript["id"], ended_at=last)
    print(ui.yellow(
        f"Session {session['id']} had been idle for {idle:.1f}h — closed it "
        f"retroactively at its last activity ({last[:16].replace('T', ' ')} UTC)."
    ))
    _analyze_closed_episodes(db, manuscript, args)


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
    report = gdocs.reconcile(db, manuscript, service, docs_service=docs_service)
    if report["in_sync"]:
        print(ui.dim("Google Docs in sync: " + ", ".join(report["in_sync"])))
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


def cmd_session(args):
    db = _open_db(args)
    manuscript = _manuscript(db, args)
    if args.action == "start":
        _expire_idle_session(db, manuscript, args)
        _reconcile_gdocs(db, manuscript, args)
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
            "\nFiles missing from toc.md (reading order falls back to "
            "alphabetical for them): " + ", ".join(briefing["toc_unlisted"])
        ))
        print(ui.dim("  → add them to toc.md in their true reading position."))

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
    "c": "concept", "d": "definition", "o": "objection", "e": "example",
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
    from .extraction import record_triage

    meta = loads(node["metadata"], {})
    extracted = meta.get("origin") == "extracted"
    meta["confirmed"] = True
    changes = {"metadata": json.dumps(meta)}
    if new_kind and new_kind != node["kind"]:
        changes["kind"] = new_kind
        if extracted:
            record_triage(db, mid, node, "retyped", new_kind)
    elif extracted:
        record_triage(db, mid, node, "confirmed")
    db.update("concept_nodes", node["id"], changes)


def _retire_node(db: Database, mid: str, node: dict) -> int:
    from .extraction import record_triage

    if loads(node["metadata"], {}).get("origin") == "extracted":
        record_triage(db, mid, node, "rejected")
    return cg.retire_concept(db, mid, node)


@contextlib.contextmanager
def _ephemeral_history():
    """Keystrokes typed at inner prompts (triage keys, notes rewording)
    are working input, not commands — pop whatever readline recorded
    inside the block so the shell's up-arrow history stays clean."""
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
    print(ui.dim("Keys: [k]eep  [r]etire  [s]kip (or Enter)  [n] reword notes  "
                 "[a] alias of another concept  "
                 "[x] quit — or retype: [c]oncept [d]efinition [o]bjection "
                 "[e]xample [m]etaphor [q]uestion [h]istorical_reference "
                 "[t] mathematical_construct"))
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
                from .extraction import record_triage
                result = cg.merge_concepts(db, mid, dict(canonical), dict(node))
                record_triage(db, mid, dict(node), "merged", canonical["name"])
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
                    db.update("concept_nodes", node["id"], {"notes": new_notes})
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
                kind, ambiguous = _prefix_choice(choice, VALID_KINDS)
                if ambiguous:
                    print(ui.dim("  ? ambiguous: " + " / ".join(ambiguous)))
                    clean = False
                    continue
            if kind:
                _confirm_node(db, mid, node, kind)
                retyped += 1
                echo(ui.cyan(f"(→ {kind})"), clean)
                break
            print(ui.dim("  ? use k / r / s / a / x, a kind key (c d o e m q h t), "
                         "or a kind prefix (e.g. syl)"))
            clean = False
    print(f"Triage: kept {kept}, retyped {retyped}, retired {retired}, "
          f"merged {merged}, skipped {skipped}.")


def _record_edge_triage(db: Database, mid: str, description: str, signal: str) -> None:
    ev = ko_fields("ev")
    ev.update(
        manuscript_id=mid, episode_id=None,
        evidence_type="edge_triage", signal=signal,
        target=description[:200], supports_policy=None, weight="high",
    )
    db.insert("evidence", ev)


def _run_edge_triage(db: Database, mid: str) -> None:
    from .extraction import VALID_RELATIONS, record_triage

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
    print(ui.dim("Keys: [k]onfirm as-is  [r]eject  [f]lip direction  "
                 "[a] these are one concept (alias-merge)  "
                 "[s]kip (or Enter)  [x] quit — or retype the relation "
                 "by number, name, or unique prefix (ans, cre, dep, ref…):"))
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

        def settle(relation: str) -> None:
            changes = {"status": "declared", "relation": relation}
            if flipped:
                changes["from_node"], changes["to_node"] = to_id, from_id
            db.update("concept_edges", edge["id"], changes)

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
                _record_edge_triage(db, mid, description, "confirmed")
                print(f"  {ui.green(f'(confirmed: {description})')}")
                break
            if choice in ("r", "reject"):
                db.update("concept_edges", edge["id"], {"status": "rejected"})
                rejected += 1
                _record_edge_triage(
                    db, mid, f"{from_name} —{edge['relation']}→ {to_name}", "rejected")
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
                result = cg.merge_concepts(db, mid, canonical, duplicate)
                record_triage(db, mid, duplicate, "merged", canonical["name"])
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
                settle(relation)
                retyped += 1
                description = f"{current_from} —{relation}→ {current_to}"
                _record_edge_triage(
                    db, mid,
                    f"{from_name} —{edge['relation']}→ {to_name} ⇒ {description}",
                    "retyped",
                )
                print(f"  {ui.cyan(f'(→ {description})')}")
                break
            print(ui.dim("  ? use k / r / f / a / s / x, a relation number, "
                         "its name, or a unique prefix (ans, cre, dep, ref…)"))
    print(f"Edge triage: confirmed {confirmed}, retyped {retyped}, "
          f"rejected {rejected}, merged {merged}, skipped {skipped}.")


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
                f"Kept for history; revive with: concept add {node['name']!r}"
            )
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
            "ORDER BY confidence DESC",
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
                           else gdocs._reading_order_files(manuscript))
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
                print(f"Master Doc: "
                      f"{ui.link(gdocs.tab_url(result['doc_id']))}")
                print(ui.yellow("Checked out to Google Docs — edit there, "
                                "then 'doc pull'. Avoid local edits meanwhile."))
                if args.open:
                    import webbrowser

                    webbrowser.open(gdocs.tab_url(result["doc_id"]))
            else:
                result = gdocs.pull_doc(db, manuscript, args.name or None,
                                        service=service, force=args.force)
                for relpath in result["changed"]:
                    print(f"Pulled {relpath} from its tab (normalized).")
                for relpath in result["missing"]:
                    print(ui.yellow(f"warning: no tab found for {relpath} — "
                                    "was its tab renamed? Tab titles must "
                                    "stay exact filenames."))
                for relpath in result["conflicts"]:
                    print(ui.yellow(
                        f"CONFLICT: {relpath} changed both locally and in its "
                        f"tab — untouched. 'doc pull {relpath} --force' takes "
                        "the Doc's side."))
                if result["changed"]:
                    print("Collecting:")
                    cmd_collect(args)
                elif not result["conflicts"] and not result["missing"]:
                    print("All tabs identical to local files — clean round "
                          "trip, nothing to collect.")
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
                "Files missing from toc.md were appended alphabetically: "
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
        print(ui.yellow("\nNote: files missing from toc.md: "
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
        print(ui.dim("Keys: [a]dopt  [e]dge (alias → generalizes)  [d]ismiss  "
                     "[s]kip (or Enter)  [x] quit"))
        adopted = dismissed = skipped = 0
        for index, row in enumerate(rows, start=1):
            summary, details = prop.describe(row)
            print(f"{ui.dim(f'[{index}/{len(rows)}]')} ({ui.cyan(row['kind'])}) "
                  f"{ui.bold(summary)}")
            for line in details:
                print(ui.dim(textwrap.fill(
                    line, width=76, initial_indent="    ", subsequent_indent="      ",
                )))
            while True:
                try:
                    choice = input("> ").strip().lower()
                except EOFError:
                    choice = "x"
                if choice in ("a", "adopt", "accept"):
                    print(f"  {ui.green(prop.adopt(db, mid, row))}")
                    adopted += 1
                    break
                if choice in ("e", "edge") and row["kind"] == "alias":
                    print(f"  {ui.cyan(prop.demote_to_edge(db, mid, row))}")
                    adopted += 1
                    break
                if choice in ("d", "dismiss"):
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
                print(ui.dim("  ? use a / d / s / x"))
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
    for row in db.all(
        "SELECT * FROM manuscript_versions WHERE manuscript_id = ? ORDER BY version_no",
        (manuscript["id"],),
    ):
        files = loads(row["files"], {})
        print(
            f"v{row['version_no']}  {row['created_at']}  {row['checksum'][:12]}  "
            f"{len(files)} file(s)  session={row['session_id'] or '-'}"
        )


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

    def add_parser(*a, **kw):
        kw.setdefault("parents", [common])
        return original_add_parser(*a, **kw)

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

    p = sub.add_parser("intent", help="declare/complete/abandon/list writing intents")
    p.add_argument("action", choices=["declare", "complete", "abandon", "retire", "list"])
    p.add_argument("statement", nargs="?",
                   help="intent statement (declare) or id prefix (complete/abandon)")
    p.add_argument("--outcome", help="outcome note (complete) or reason (abandon)")
    p.set_defaults(func=cmd_intent)

    p = sub.add_parser("concept", help="manage the Concept Graph")
    p.add_argument("action",
                   choices=["add", "link", "list", "show", "confirm", "unconfirm",
                            "reject-edge", "reject", "retire", "triage", "edit",
                            "alias", "merge"])
    p.add_argument("params", nargs="*",
                   help="add/show/confirm/unconfirm/reject-edge/edit: name or edge id; "
                        "link: <from> <relation> <to>; retire: one or more names; "
                        "alias: <name> <alias>…; merge: <canonical> <duplicate>")
    p.add_argument("--kind", default=None,
                   choices=["concept", "definition", "objection", "example", "metaphor",
                            "question", "historical_reference", "mathematical_construct",
                            "syllogism"],
                   help="node kind — used by add, and by confirm to retype")
    p.add_argument("--notes", help="free-text notes stored on the concept")
    p.add_argument("--all", action="store_true",
                   help="list: include retired/rejected; confirm: all unconfirmed")
    p.add_argument("--all-kind", metavar="KIND",
                   help="confirm/retire every unconfirmed extracted concept of this kind")
    p.add_argument("--nodes", action="store_true", help="triage: concepts only")
    p.add_argument("--edges", action="store_true", help="triage: relationships only")
    p.add_argument("--remove", nargs="+", metavar="ALIAS",
                   help="alias: withdraw these aliases instead of adding")
    p.set_defaults(func=cmd_concept)

    p = sub.add_parser("collect", help="snapshot the manuscript and detect transitions")
    p.add_argument("--auto", action="store_true", help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_collect)

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
                                      "create-manuscript"])
    p.add_argument("name", nargs="?", help="file name (add) or name fragment "
                                           "(retire/revive/push/pull)")
    p.add_argument("--title", help="heading for a new document / Doc title "
                                   "(push, create-manuscript)")
    p.add_argument("--open", action="store_true",
                   help="open the Google Doc in your browser after push")
    p.add_argument("--force", action="store_true",
                   help="pull: take the Doc's version even if the local file "
                        "changed since the push (local edits stay in history)")
    p.set_defaults(func=cmd_doc)

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
        "policy", help="list policies / answer outstanding questions",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="example:\n"
               "  policy list                     shows each policy's [id] and its\n"
               "                                  numbered open questions (Q1, Q2, …)\n"
               '  policy answer pol-d970 "Only inside worked examples" --index 2\n'
               "                                  closes Q2 of policy pol-d970; the\n"
               "                                  answer becomes declared evidence",
    )
    p.add_argument("action", choices=["list", "answer"])
    p.add_argument("id", nargs="?", help="policy id prefix, from 'policy list' (answer)")
    p.add_argument("answer", nargs="?", help="your answer")
    p.add_argument("--index", type=int, default=1,
                   help="which open question to answer — the Q number shown by "
                        "'policy list' (default 1)")
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
                   choices=["guides", "guide", "attach", "add", "show", "retire"])
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

    p = sub.add_parser("history", help="list collected manuscript versions")
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
        if args.action == "link":
            if len(params) != 3:
                sys.exit("usage: concept link <from> <relation> <to>")
            args.from_name = args.name
        if args.action in ("add", "reject-edge", "show", "unconfirm") and not args.name:
            sys.exit(f"usage: concept {args.action} <name-or-id-prefix>")
        if args.action == "confirm" and not (args.name or args.all or args.all_kind):
            sys.exit("usage: concept confirm <name-or-edge-prefix> | --all | --all-kind KIND")
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
        required = {"guide": 1, "attach": 2, "add": 2, "show": 1, "retire": 1}
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
    args.func(args)


if __name__ == "__main__":
    main()
