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
import os
import sys
import textwrap
from pathlib import Path

from . import api
from . import clients
from . import concepts as cg
from . import critique as crit
from . import beliefs as bel
from . import sessions as ses
from . import triage as triage_service
from . import ui
from .briefing import build_briefing
from . import beliefs as bel_mod
from . import paths as paths_mod
from .db import Database, ko_fields, loads
from .extraction import extract_concepts
from .guidance import INTENT_KINDS, generate_guidance, intent_coverage_notes
from .llm import LLMClient
from .revisions import collect_revision, detect_transitions


def _workspace(args) -> Path:
    return Path(args.workspace).resolve()


def _open_db(args) -> Database:
    """Open the workspace database, or decide what a missing one means.

    The database file is the tell, not the directory (macOS leaves an
    empty folder under /Volumes after an unclean unmount). What to do
    when it is absent depends on where the workspace came from:

    - `.env` pointer or exported variable: a standing claim that a
      database lives there. Missing means the volume is not mounted, or
      the pointer is stale. Refuse; never offer to create — saying yes
      one morning with the drive unplugged would fork the data.
    - explicit `-w`: `init` may bootstrap there (that is what `-w … init`
      means, and what every test fixture does); any other verb refuses.
    - default home, no pointer: a fresh checkout. On a terminal, ask
      permission and a location, record it in `.env`, create. Otherwise
      refuse and name `authorlm setup`.
    """
    source = getattr(args, "workspace_source", "flag")
    path = api.db_path(str(_workspace(args)))
    if path.exists():
        return api.open_db(str(_workspace(args)))
    if source == "env":
        sys.exit(f"error: .env points AUTHORLM_WORKSPACE at {_workspace(args)} "
                 f"but there is no database at {path}.\n"
                 "If it lives on an external volume, mount it and retry. "
                 "To start a new database elsewhere: authorlm setup --workspace DIR")
    if source == "flag":
        if args.command == "init":
            return api.open_db(str(_workspace(args)), create=True)
        sys.exit(f"error: no database at {path}.\n"
                 "Check the path, or run 'init' with this --workspace to start one.")
    if not sys.stdin.isatty():
        sys.exit(f"error: no database at {path} and no AUTHORLM_WORKSPACE in "
                 f"{paths_mod.env_path()}.\nRun: authorlm setup --workspace DIR")
    print(f"No AuthorLM database found (looked at {path}).")
    if not _ask_yes("Create a new one?"):
        sys.exit("Nothing created. Run 'authorlm setup --workspace DIR' when ready.")
    chosen = input(f"Where should it live? [{_workspace(args)}] ").strip() or str(_workspace(args))
    return _setup_workspace(Path(chosen).expanduser().resolve())


def _ask_yes(question: str) -> bool:
    return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")


def _setup_workspace(workspace: Path) -> Database:
    """Record `workspace` as this checkout's AUTHORLM_WORKSPACE and create
    its database. The pointer is written first so a crash between the
    two leaves a pointer to an empty directory (which the guard reports)
    rather than an orphan database nothing points at."""
    env_file = paths_mod.set_env_value(paths_mod.WORKSPACE_ENV, str(workspace))
    db = api.open_db(str(workspace), create=True)
    print(f"Database created at {db.path}")
    print(f"Recorded AUTHORLM_WORKSPACE={workspace} in {env_file}")
    return db


def cmd_setup(args):
    """Point this checkout at a workspace and create its database.

    Non-interactive when --workspace is given; asks otherwise. With a
    database already there, only the pointer is (re)written — so moving
    a workspace is: copy the directory, run setup with the new path."""
    workspace = _workspace(args) if args.workspace_source == "flag" else None
    if workspace is None:
        if not sys.stdin.isatty():
            sys.exit("usage: authorlm setup --workspace DIR")
        default = Path.home()
        chosen = input(f"Where should the workspace live? [{default}] ").strip()
        workspace = Path(chosen).expanduser().resolve() if chosen else default
    path = api.db_path(str(workspace))
    if path.exists():
        env_file = paths_mod.set_env_value(paths_mod.WORKSPACE_ENV, str(workspace))
        print(f"Using existing database at {path}")
        print(f"Recorded AUTHORLM_WORKSPACE={workspace} in {env_file}")
        return
    if not args.yes and sys.stdin.isatty() and not _ask_yes(
            f"No database at {path}. Create it?"):
        sys.exit("Nothing created.")
    _setup_workspace(workspace)


def _load_config(args) -> dict:
    """Load the project's config.toml (native # comments, stdlib tomllib)
    and populate the environment from .env first, so every key lookup
    downstream sees the same environment. Config holds decisions; .env
    holds secrets (authorlm/paths.py)."""
    from . import paths

    paths.load_env()
    toml_path = paths.config_path()
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
        + (f"; skipped {summary['skipped_malformed']} malformed item(s)"
           if summary.get("skipped_malformed") else "")
        + (f"; {summary['skipped_unknown_relation']} relationship(s) named an "
           "unrecognized relation" if summary.get("skipped_unknown_relation") else "")
        + (f"; {summary['skipped_unknown_endpoint']} relationship(s) named an "
           "unknown concept" if summary.get("skipped_unknown_endpoint") else "")
        + (f"; suppressed {summary['suppressed']} previously-rejected concept(s)"
           if summary.get("suppressed") else "")
        + (f"; dropped {summary['ungrounded_links']} ungrounded link(s) "
           "(endpoint not in the mined text)"
           if summary.get("ungrounded_links") else "")
        + (f"; {len(summary['below_bar'])} below the recurrence bar"
           if summary.get("below_bar") else "")
        + (f"; adjudication screened out {summary['screened']} candidate(s) "
           "before triage" if summary.get("screened") else "")
        + (f"; refused {summary['alias_folds_refused']} alias proposal(s) "
           "naming an already-live concept (would be a merge, not an "
           "alias — logged, not queued)"
           if summary.get("alias_folds_refused") else "")
        + (f"; refused {summary['alias_retired_refused']} alias proposal(s) "
           "naming a retired concept (would re-enter a banned name — "
           "logged, not queued)"
           if summary.get("alias_retired_refused") else "")
        + (f"; refused {len(summary['materiality_refused'])} note update(s) "
           "below the materiality floor"
           if summary.get("materiality_refused") else "")
        + "."
    )
    if summary.get("prompt_files"):
        print(ui.dim("Prompts: " + ", ".join(summary["prompt_files"])))
    if summary.get("adjudication_empty"):
        print(ui.yellow(
            "note: adjudication ran but the model returned nothing usable "
            "— worth checking authorlm/prompts/adjudication.md"))
    if summary.get("below_bar"):
        print(ui.dim(
            "Below the recurrence bar (single-context phrases, not "
            "admitted; self-healing on a future mention): "
            + ", ".join(f"'{n}'" for n in summary["below_bar"])))
    if summary.get("materiality_refused"):
        print(ui.dim(
            "Note updates below the materiality floor (case/whitespace-only "
            "or >=0.90 similarity — not queued): "
            + ", ".join(f"'{n}'" for n in summary["materiality_refused"])))
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
    try:
        row = api.register_manuscript(
            db, args.name, str(path), author=args.author,
            copyright_owner=args.copyright_owner,
            paperback_isbn=args.paperback_isbn,
            hardcover_isbn=args.hardcover_isbn)
    except ValueError as err:
        sys.exit(f"error: {err}.")
    print(f"Registered manuscript '{args.name}' at {path}")
    llm = LLMClient(_load_config(args))
    if llm.enabled and not args.no_extract:
        _run_extraction(db, row, llm)
    print("Next: 'session start', then 'intent declare \"...\"', then 'collect'.")


def cmd_manuscript(args):
    """Show or update the publication identity of a manuscript."""
    db = _open_db(args)
    manuscript = _manuscript(db, args)
    if args.action == "set":
        try:
            identity = api.update_manuscript_metadata(
                db, manuscript, author=args.author,
                copyright_owner=args.copyright_owner,
                paperback_isbn=args.paperback_isbn,
                hardcover_isbn=args.hardcover_isbn,
                trim_size=args.trim_size, bleed=args.bleed,
                narrator=args.narrator, publisher=args.publisher,
                copyright_year=args.copyright_year,
                language=args.language)
        except ValueError as err:
            raise SystemExit(f"error: {err}")
    else:
        identity = api.manuscript_metadata(manuscript)
    print(f"Manuscript: {identity['name']}")
    print(f"  path: {identity['path']}")
    print(f"  author: {identity['author'] or '(not set)'}")
    print("  copyright_owner: "
          f"{identity['copyright_owner'] or '(not set)'}")
    print(f"  copyright_year: {identity['copyright_year'] or '(not set)'}")
    print(f"  publisher: {identity['publisher'] or '(not set)'}")
    print(f"  narrator: {identity['narrator'] or '(not set)'}"
          + ("" if identity["narrator"] else "  — needed by 'audio export'"))
    print(f"  language: {identity['language'] or '(not set, en assumed)'}")
    print("  paperback_isbn: "
          f"{identity['paperback_isbn'] or '(not set)'}")
    print("  hardcover_isbn: "
          f"{identity['hardcover_isbn'] or '(not set)'}")
    print(f"  trim_size: {identity['trim_size'] or '(not set)'}"
          + ("" if identity["trim_size"] else
             "  — needed by 'export pdf --profile book'"))
    print(f"  bleed: {'yes' if identity['bleed'] else 'no'}")


def _manuscript_tables(db: Database) -> list[str]:
    """Every table that carries a manuscript_id column, plus 'manuscripts'
    itself (keyed by 'id' instead). Derived from the live schema rather
    than hand-maintained: the previous hand-written list had drifted 8
    tables behind the schema (critique_passes, doc_comments, doc_threads,
    essay_summaries, illus_proposals, improvement_tasks, knowledge_
    proposals, writeups) by the time it was audited — exactly the kind of
    silent gap a fixed list cannot help but accumulate (ORCH-2)."""
    scoped = sorted(t for t in db._tables()
                    if t != "manuscripts" and "manuscript_id" in db._columns(t))
    return scoped + ["manuscripts"]


def cmd_unregister(args):
    """Clean-slate removal. This is a workspace-management operation for
    prototyping and hermetic tests — it deletes the manuscript's entire
    record, unlike retire/reject which preserve history.

    Untransacted deletes with no backup were a deploy blocker
    (it-f661015221b0): an interruption partway through left a half-purged
    manuscript with no repair path. `backup.run()` gives a way back even
    if the transaction below somehow isn't enough; `db.transaction()`
    makes the purge itself all-or-nothing, so a raw `db.conn.execute`
    loop can no longer leave some tables deleted and others untouched."""
    from . import backup

    db = _open_db(args)
    name = args.name or getattr(args, "manuscript", None)
    if not name:
        sys.exit("usage: unregister <name>  (or: unregister -m <name>)")
    row = db.one("SELECT * FROM manuscripts WHERE name = ?", (name,))
    if not row:
        sys.exit(f"error: no manuscript named '{name}'.")
    mid = row["id"]
    backup.run(db)  # before the first delete — no repair path once these rows are gone
    deleted = {}
    with db.transaction():
        for table in _manuscript_tables(db):
            key = "id" if table == "manuscripts" else "manuscript_id"
            cursor = db.conn.execute(f"DELETE FROM {table} WHERE {key} = ?", (mid,))
            if cursor.rowcount:
                deleted[table] = cursor.rowcount
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
    reconciled = result.get("comments_reconciled") or []
    if reconciled:
        print(ui.dim(f"{len(reconciled)} comment(s) resolved in the Doc "
                     "since the last pull — local record caught up."))
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
    if report.get("ignored_tabs"):
        print(ui.dim("Ignoring non-manuscript tab(s): "
                     + ", ".join(report["ignored_tabs"])))
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
        # Session start must never open blind to work done between
        # sessions — collect BEFORE any Doc reconcile below, so an
        # uncollected local edit can never be destroyed by a base-less
        # Doc pull (BUG-1 / A2). Full report (new chapters, transitions,
        # realizations) so nothing arrives unacknowledged.
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
        _reconcile_gdocs(db, manuscript, args)
        try:
            session = ses.start_session(db, manuscript["id"])
        except ValueError as err:
            sys.exit(f"error: {err}")
        print(f"Session {session['id']} started for '{manuscript['name']}'.\n")
        backup_result = session.get("backup") or {}
        if not backup_result.get("ok", True):
            print(ui.yellow(f"warning: knowledge-store backup failed "
                             f"({backup_result.get('error')}) — no fresh "
                             "backup was taken this session."))
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
            "Learning this session — evidence: {new_evidence}, beliefs changed: "
            "{beliefs_changed}, seeded: {beliefs_seeded}, concepts realized: "
            "{concepts_realized}, edges inferred: {edges_inferred}".format(**velocity)
        )


def _print_briefing(db: Database, manuscript: dict):
    briefing = build_briefing(db, manuscript["id"])
    print(ui.header("═══ Session-Opening Learning Briefing ═══"))
    fresh = briefing["since"] == "1970-01-01T00:00:00Z"
    empty = not any([
        briefing["belief_changes"], briefing["new_beliefs"],
        briefing["realized_concepts"], briefing["unconfirmed_concepts"],
        briefing["inferred_edges"], briefing["outstanding_questions"],
        briefing["focus_areas"], briefing["active_intents"],
        briefing["active_writeups"],
    ])
    if fresh and empty:
        print(ui.dim("First session — nothing learned yet."))
    elif fresh:
        print(ui.dim("First look at this manuscript."))
    else:
        print(ui.dim(f"Since your last session ({briefing['since'][:16].replace('T', ' ')} UTC)."))

    if briefing["active_writeups"]:
        # First, and yellow: the file named here is truncated on disk and
        # half-rebuilt. Everything else in the briefing is knowledge; this
        # is the state of the manuscript itself.
        print(ui.yellow("\nOpen writeups — these files are truncated on disk "
                        "and rebuilding one accepted beat at a time:"))
        for writeup in briefing["active_writeups"]:
            pending = (" · a draft awaits your verdict"
                       if writeup["proposal_pending"] else "")
            print(f"  • {ui.bold(writeup['file'])} — {writeup['progress']}"
                  f"{pending} "
                  + ui.dim(f"[{writeup['id'][:8]} · intent "
                           f"{writeup['intent_id'][:8]}]"))
        print(ui.dim("  → resume: write status   ·   "
                     "restore the old text: write abandon"))

    if briefing["belief_changes"]:
        print(ui.bold("\nBeliefs strengthened/weakened:"))
        for change in briefing["belief_changes"]:
            print(
                f"  • \"{ui.bold(change['statement'])}\" [{change['status']}] "
                f"confidence {change['confidence']} "
                + ui.dim(f"(+{change['delta_supporting']}/-{change['delta_contradicting']} this period; "
                         f"{change['supporting']}+ / {change['contradicting']}- total)")
            )
    if briefing["new_beliefs"]:
        print(ui.bold("\nNewly seeded candidate beliefs (from your explanations):"))
        for belief in briefing["new_beliefs"]:
            print(f"  • \"{belief['statement']}\" "
                  + ui.dim(f"[{belief['status']}, confidence {belief['confidence']}]"))

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
                f"  {ui.dim('[' + q['belief_id'][:8] + ']')} "
                f"On \"{ui.shorten(q['statement'], 50)}\": {q['question']}"
            )
        print(ui.dim("  → answer: belief answer <id> \"...\""))

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


def _toc_openers(manuscript: dict) -> set:
    """Files that have essays beneath them in toc.toml — the ones a scope
    can name to mean a whole part. Best-effort: a missing or unparseable
    toc degrades to 'no openers', never raises."""
    from .revisions import read_manuscript_files
    from .structure import _toc_chapters, TOC_FILENAME

    try:
        files = read_manuscript_files(Path(manuscript["path"]))
    except OSError:
        return set()
    return {c["parent"] for c in _toc_chapters(files.get(TOC_FILENAME) or "")
            if c.get("parent")}


def _scope_sentence(scope: str | None) -> str:
    """What the scope MEANS, in words, at the moment the author sets it —
    an absent scope has a consequence now and they should see it once."""
    if scope:
        return (f"Scope {scope} — every rewrite of that essay (and of any "
                f"essay beneath it) serves this goal.")
    return ("Scope: the whole book — EVERY writeup, on every essay, will "
            "carry this goal. Place it instead with "
            "'intent scope <id> --scope <file>'.")


def _print_intent_set(result: dict, label: str) -> None:
    """The writeup's member set, grouped by tier, primary marked. One
    shape, printed by start, plan, intents and status."""
    view = result.get("intents_view") or []
    if not view:
        return
    block = result.get("intents") or {}
    state = (block.get("state") or "proposed").upper()
    tail = (" (frozen when you ratify the plan)" if state == "PROPOSED"
            else "")
    print(f"{label} — {len(view)}, {state}{tail}:")
    for member in view:
        scope = f"   ({member['scope']})" if member.get("scope") else ""
        mark = "  ← primary" if member.get("role") == "primary" else ""
        print(f"  {member['tier']:<11} [{member['id'][:8]}] "
              f"{member['statement']}{scope}{mark}")
    if block.get("tied"):
        print(ui.yellow(
            "  Two or more file-scoped intents are equally specific. The "
            "primary owns this writeup's episode: every beat verdict and "
            "every transition is recorded against it, and the others are "
            "cross-referenced at completion. Name it:"))
        print(ui.yellow(f"    write intents --primary "
                        f"{block['tied'][0][:11]}"))
    for member_id, entry in (block.get("deferred") or {}).items():
        print(ui.dim(f"  deferred [{member_id[:8]}] — \"{entry['reason']}\""))


def _print_newly_in_scope(result: dict) -> None:
    """The drift line, worded for the state the set is actually in.

    "newly in scope since RATIFICATION" is only true once there has been
    a ratification. Before the plan there has not been one, and the same
    words would tell the author their set was frozen when `--remove` is
    still open to them and nothing has been settled — the sentence would
    be describing the wrong half of the lifecycle."""
    frozen = (result.get("intents") or {}).get("state") == "frozen"
    heading = ("  newly in scope since ratification:" if frozen
               else "  in scope, but not on this writeup:")
    tail = (" (re-bills the cached prefix once)" if frozen else "")
    for member in result.get("newly_in_scope") or []:
        print(ui.yellow(heading))
        print(f"    [{member['id'][:8]}] {member['statement']}")
        print(ui.dim(
            f"    It has NOT joined. Join it: write intents --add "
            f"{member['id'][:11]}{tail} · "
            f"dismiss it: write intents --ignore {member['id'][:11]}"))
    for member in result.get("stale_members") or []:
        print(ui.dim(f"  note: member [{member['id'][:8]}] is now "
                     f"{member['status']} — the writeup still served it."))


def _print_intent_dispositions(dispositions: list) -> None:
    if not dispositions:
        return
    print("Intents:")
    unserved = False
    for entry in dispositions:
        role = "  (primary)" if entry.get("role") == "primary" else ""
        word = entry["disposition"]
        line = (f"  {word.upper() if word != 'served' else word:<9} "
                f"[{entry['id'][:8]}] {entry['statement']}{role}")
        if word == "served":
            print(line)
        else:
            print(ui.yellow(line))
        if word == "deferred":
            print(f"            — \"{entry.get('reason')}\"")
        if word == "unserved":
            unserved = True
            print(ui.yellow(
                f"            No accepted beat serves it and no deferral "
                f"was recorded. Either it was served (close it: intent "
                f"complete {entry['id'][:11]}) or it was not (write intents "
                f"--defer {entry['id'][:11]} --reason \"<why>\")."))
    if unserved:
        print(ui.yellow("            Completing anyway — the completion is "
                        "yours to make."))
    print(ui.dim("Recorded on each intent: served by this writeup."))


def _print_scope_triage(db, manuscript, prefix) -> None:
    """The one-time sitting's read (design-intent-scope §4). Evidence
    only — no ruling is made here, and no model is called."""
    rows = api.scope_evidence(db, manuscript, prefix or None)
    tally = api.scope_tally(db, manuscript)
    # The middle number is the whole point of the sitting: it is the one
    # the author cannot get any other way, and it only exists because a
    # book-wide RULING is distinguishable from a never-placed default.
    summary = (f"{tally['no_place']} with no place · "
               f"{tally['ruled_book_wide']} ruled book-wide · "
               f"{tally['scoped']} essay/chapter-scoped")
    if not rows:
        print(f"Nothing left to place — {summary}.")
        print(ui.dim("The middle number is the one that matters: those are "
                     "the goals every future writeup will carry, because "
                     "you decided they should."))
        return
    print(ui.bold(f"{len(rows)} active intent(s) with no place. Every one of "
                  f"them attaches to EVERY writeup until it has one."))
    print(ui.dim(f"  {summary}"))
    for row in rows:
        print()
        print(ui.bold(f"[{row['id'][:8]}] {row['statement']}"))
        print(ui.dim(f"  declared {row['created_at'][:10]}"
                     + (f" · {row['source']}" if row["source"] else "")))
        if row["files"]:
            print("  changes landed in: " + ", ".join(
                f"{f['file']} ({f['transitions']})" for f in row["files"][:6]))
        else:
            print(ui.dim("  no recorded changes hang off it"))
        for writeup in row["writeups"]:
            print(ui.dim(f"  writeup [{writeup['id'][:8]}] on "
                         f"{writeup['file']} ({writeup['status']})"))
        suggested = row["suggested"]
        where = (f"--scope {suggested['scope']}" if suggested["tier"] == "file"
                 else f"--chapter {suggested['scope']}"
                 if suggested["tier"] == "chapter" else "--book-wide")
        print(f"  suggested: {suggested['tier']} — {suggested['why']}")
        print(ui.dim(f"    intent scope {row['id'][:11]} {where}"))
    print()
    print(ui.dim(f"{summary}."))
    print(ui.dim("Rule each one from the evidence, not from memory. An "
                 "intent you cannot place is left alone and comes back next "
                 "sitting; one you rule book-wide is settled and will not "
                 "be offered again."))


def cmd_intent(args):
    db = _open_db(args)
    manuscript = _manuscript(db, args)
    if args.action == "show":
        if not args.statement:
            sys.exit("usage: authorlm intent show <id-prefix>")
        rows = [dict(r) for r in db.all(
            "SELECT * FROM declared_intents WHERE manuscript_id = ?",
            (manuscript["id"],)) if args.statement in r["id"]]
        if not rows:
            sys.exit(f"error: no intent matching '{args.statement}'.")
        for row in rows:
            print(ui.bold(f"[{row['id']}] {row['statement']}"))
            print(ui.dim(f"  {row['status']}"
                         + (f" · scope {row['scope']}" if row["scope"] else "")
                         + f" · declared {row['created_at'][:19]}"))
            if row["outcome"]:
                print(f"  outcome: {row['outcome']}")
            meta = loads(row.get("metadata"), {}) or {}
            crit = meta.get("critique") or {}
            if crit:
                print(ui.dim(f"  from critique: {crit.get('unit', '?')} "
                             f"#{crit.get('ordinal', '?')}"))
            threads = db.all(
                "SELECT * FROM doc_threads WHERE manuscript_id = ? "
                "AND json_extract(metadata, '$.intent_id') = ? LIMIT 6",
                (manuscript["id"], row["id"]))
            if threads:
                print(f"  {len(threads)} staged edit(s) serve this intent")
            ev = db.all(
                "SELECT * FROM evidence WHERE manuscript_id = ? "
                "AND target LIKE ? ORDER BY created_at DESC LIMIT 5",
                (manuscript["id"], f"%{row['statement'][:40]}%"))
            for e in ev:
                print(ui.dim(f"    [{e['evidence_type']}/{e['signal']}] "
                             f"{e['target'][:100]}"))
        return

    if args.action == "declare":
        scope = None
        if args.scope or args.chapter or args.manuscript_wide:
            try:
                scope = api._scope_target(manuscript, args.scope,
                                          args.chapter, args.manuscript_wide)
            except (ValueError, LookupError) as err:
                sys.exit(f"error: {err}")
        # `args.manuscript_wide` is the author saying "the whole book",
        # which is not the same act as saying nothing at all. Only the
        # explicit flag records a ruling.
        result = api.declare_intent(db, manuscript, args.statement,
                                    scope=scope,
                                    book_wide=bool(args.manuscript_wide))
        row = result["intent"]
        print(f"Declared intent [{row['id'][:8]}]: {args.statement}")
        print(ui.dim("  " + _scope_sentence(row["scope"])))
        _intent_preview(db, manuscript, args.statement)
        if not ses.active_session(db, manuscript["id"]):
            print("note: no active session — the intent is recorded but no episode was opened.")
    elif args.action == "scope":
        if args.triage:
            _print_scope_triage(db, manuscript, args.statement)
            return
        if not args.statement:
            sys.exit("usage: intent scope <id-prefix> --scope FILE | "
                     "--chapter OPENER | --book-wide  (or --triage)")
        try:
            result = api.scope_intent(db, manuscript, args.statement,
                                      scope=args.scope, chapter=args.chapter,
                                      manuscript_wide=args.manuscript_wide)
        except (ValueError, LookupError) as err:
            sys.exit(f"error: {err}")
        row = result["intent"]
        print(f"Scoped [{row['id'][:8]}]: {row['statement']}")
        print(ui.dim("  " + _scope_sentence(result["scope"])))
        for held in result["frozen_in"]:
            print(ui.dim(
                f"  writeup [{held['writeup'][:11]}] on {held['file']} "
                f"ratified this intent as [{held['tier']}] and is "
                f"UNAFFECTED — a re-scope routes future work, it never "
                f"rewrites what was already ratified."))
    elif args.action == "complete":
        intent = _find_by_prefix(db, "declared_intents", args.id, manuscript["id"])
        if intent["status"] != "active":
            sys.exit(f"error: intent is already {intent['status']}.")
        held = api._writeups_holding(db, manuscript, intent["id"])
        ses.complete_intent(db, intent, args.outcome)
        print(f"Intent completed: {intent['statement']}")
        for entry in held:
            print(ui.yellow(
                f"  note: writeup [{entry['writeup'][:11]}] on "
                f"{entry['file']} ratified this intent and is still open — "
                f"'write status' will now report it as a stale member."))
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
        openers = _toc_openers(manuscript)
        for row in rows:
            outcome = f" → {row['outcome']}" if row["outcome"] else ""
            # The tier, not just the column: a scope naming a part opener
            # covers every essay beneath it, and that is the whole
            # difference between "this essay" and "this part".
            if not row["scope"]:
                # "book-wide" alone reads the same for a goal the author
                # deliberately made book-wide and one nobody has placed
                # yet, and they are opposite states.
                where = ("book-wide (ruled)" if api._scope_ruled(dict(row))
                         else "book-wide (default)")
            elif row["scope"] in openers:
                where = f"chapter {row['scope']}"
            else:
                where = f"file {row['scope']}"
            print(f"[{row['id'][:8]}] ({row['status']} · {where}) "
                  f"{row['statement']}{outcome}")


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
              f"{result['style_laws']} style element(s) as proposed "
              f"({result['skipped']} already present, skipped).")
        for dup in result.get("duplicates", []):
            print(ui.dim(
                f"duplicate: {dup['unit']} #{dup['ordinal']} restates "
                f"{dup['existing_id']} ({dup['status']}, from "
                f"'{dup['source']}') — skipped, verdict stands"))
        for err in result["errors"]:
            print(ui.yellow(f"warning: {err}"))
        if result["intents"] or result["style_laws"]:
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
        if args.decided:
            return _critique_decided(db, manuscript, args)
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
                        ("element", "style_laws")):
        rows = db.all(
            f"SELECT * FROM {table} WHERE manuscript_id = ? AND id LIKE ? "
            f"AND {crit.FROM_CRITIC}", (mid, f"%{token}%"))
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
                            ("style_laws", "element")):
            for r in db.all(f"SELECT * FROM {table} WHERE manuscript_id = ? "
                            f"AND {crit.FROM_CRITIC}", (mid,)):
                if q in r["statement"].lower():
                    rows.append((kind, dict(r)))
        if not rows:
            print(f"No critique items match '{args.query}'.")
        for kind, item in rows:
            _, reason, _ = crit.verdict_of(kind, item)
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
    _, reason, original = crit.verdict_of(kind, item)
    if reason:
        print("  reason: " + reason)
    if original:
        print(ui.dim(ui.wrap("critic wrote: " + original, indent="  ")))


def _critique_decided(db: Database, manuscript: dict, args) -> None:
    """`critique list --decided` — every settled item with its verdict and
    the author's reason. The point is that nobody has to open sqlite to
    remember what was already answered, or why."""
    rows = crit.decided(db, manuscript["id"], scope=args.scope,
                        verdict=args.verdict, query=args.query,
                        manuscript=manuscript)
    if not rows:
        print("No decided critique items"
              + (f" scoped to {args.scope}" if args.scope else "")
              + (f" with verdict '{args.verdict}'" if args.verdict else "")
              + (f" matching '{args.query}'" if args.query else "") + ".")
        return
    counts = crit.tally(rows)
    print(ui.bold(f"{len(rows)} decided") + ui.dim(
        " (" + ", ".join(f"{n} {v}" for v, n in sorted(counts.items())) + ")"))
    for d in rows:
        item, verdict = d["item"], d["verdict"]
        color = {"accept": ui.green, "revise": ui.cyan,
                 "reject": ui.yellow}.get(verdict, ui.dim)
        print()
        print(f"{_critique_label(d['kind'], item)} {color(verdict)}")
        print(ui.wrap(item["statement"], indent="  "))
        if d["reason"]:
            print(ui.dim(ui.wrap("reason: " + d["reason"], indent="  ")))
        if d["revised_from"]:
            print(ui.dim(ui.wrap("critic wrote: " + d["revised_from"],
                                 indent="  ")))
    print(ui.dim("\nAmend a reason: critique reason <id> --text \"…\"  |  "
                 "send one back to proposed: critique reopen <id>"))


def _critique_reason(db: Database, mid: str, args) -> None:
    """Amend a resolved item's recorded reason (stray keystrokes and
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
        db.update("style_laws", item["id"],
                  {"metadata": json.dumps(meta)})
    ev = ko_fields("ev")
    ev.update(manuscript_id=mid, episode_id=None,
              evidence_type="critique_triage", signal="reason_amended",
              target=f"{item['statement'][:120]} — {args.text}"[:200],
              supports_belief=None, weight="high")
    db.insert("evidence", ev)
    print(ui.green("reason amended") + f" [{item['id'][:8]}]")


def _critique_reopen(db: Database, mid: str, args) -> None:
    """Send a resolved item back to proposed."""
    if not args.target:
        sys.exit("usage: critique reopen <id-prefix>")
    kind, item = _critique_settled(db, mid, args.target)
    table = "declared_intents" if kind == "intent" else "style_laws"
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
    # critique run prints no drafting context, so the in-flight warning
    # that rides inside it for the write path needs its own line here.
    if ctx.get("inflight"):
        names = ", ".join(ctx["inflight"])
        plural = ("essays in this pass's book context are"
                  if len(ctx["inflight"]) > 1
                  else "essay in this pass's book context is")
        print(ui.yellow(
            f"!! {len(ctx['inflight'])} {plural} being written right now, "
            f"in another\n!! writeup: {names}. The BEFORE/AFTER summary you "
            "are editing against is\n!! the PRE-REWRITE essay, taken from "
            "that writeup's pinned source version.\n!! Slightly suboptimal: "
            "this pass cannot see what that rewrite is doing."))
    llm = passes.editor_llm(config)
    if not llm.enabled:
        sys.exit("error: the LLM is disabled ([llm] enabled = false).")
    print(ui.dim(f"editor model: {llm.model} — {len(ctx['paragraphs'])} "
                 f"paragraphs, {len(ctx['intents'])} intent(s), "
                 f"{len(ctx['beliefs'])} belief(s), "
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
    # `accepted`, not `threads`: the writer no longer filters by state,
    # because its two producers now disagree about which states go to
    # the tab. The critique pass's contract is unchanged — accepted
    # edits only — and it is stated HERE now, where it belongs.
    result = gdocs.write_pending_forms(db, manuscript, file, accepted,
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
    # Read the tab as MARKDOWN (the same export + order-aware
    # split_tabbed_export path 'doc pull' uses) instead of the textRun
    # walk critique_tab_text used — textRuns carry no heading/list/bold/
    # link markup, which is what flattened every essay to plain prose
    # (it-x7-1). Three-wayed against local so an edit made elsewhere in
    # the tab still lands, and a genuine two-sided edit is surfaced
    # instead of a side being picked silently.
    fetched = gdocs.tab_marked_markdown(db, manuscript, file,
                                          service, docs_service)
    if fetched["state"] == "missing":
        sys.exit(f"error: '{file}' has no matching section in the "
                 "master Doc export — 'doc push' first")
    if fetched["state"] == "conflict":
        sys.exit(f"error: '{file}' changed both locally and in the Doc "
                 "since the last sync — resolve refuses to guess which "
                 "wins. Compare the local file against the Doc tab by "
                 "hand, then re-run 'critique resolve'.")
    for warn in fetched["marker_warnings"]:
        print(ui.yellow(f"  {warn}"))
    marked = fetched["marked"]
    final, forms = passes.final_text_from_marked(marked, written=written)
    # Snapshot whatever is on disk right now — including any local edit
    # made outside this Doc/critique flow — before it's overwritten below
    # (BUG-2 / A1).
    with contextlib.redirect_stdout(io.StringIO()):
        api.collect(db, manuscript, config, source="pre-critique-resolve")
    # Apply locally: the author's post-edits win.
    path = Path(manuscript["path"]) / file
    normalized = gdocs.normalize_markdown(final)
    path.write_text(normalized if normalized.endswith("\n")
                    else normalized + "\n", encoding="utf-8")
    diffs = passes.record_resolution(db, mid, file, forms, final_text=final)
    # No push: the author settles these forms in the Doc themselves, in
    # most cases. The Doc keeps them until their next ordinary 'doc push'.
    print(ui.green(f"resolved {file}: {len(forms)} form(s) made final")
          + (ui.dim(f", {len(diffs)} modified acceptance(s) recorded")
             if diffs else ""))
    for d in diffs:
        print(ui.dim(f"  «{gdocs.clamp(d['proposal'])}» → "
                     f"«{gdocs.clamp(d['final'])}»"))
    # Collect, then rebuild this essay's summary at the gate.
    #
    # NO_EPISODE, not ambient (AQ; §15.17's disposition table, the
    # critique-resolve row). `episode=None` means "the session's most
    # recently created open episode, whatever goal it belongs to", so
    # this collect used to file the resolve's transitions against
    # whichever intent happened to be open — making a completion report
    # say a substantive goal was served by an edit sweep. Settling
    # staged edits is hygiene, not goal-work: the version history, the
    # `critique_edit` evidence rows and the pass row are the complete
    # record. The pre-resolve collect above stays AMBIENT, deliberately:
    # it snapshots the author's own uncollected local edits, which
    # predate this verb, exactly as `write_start`'s first collect does.
    with contextlib.redirect_stdout(io.StringIO()):
        api.collect(db, manuscript, config, source="critique-resolve",
                    episode=api.NO_EPISODE)
    llm = sums.summarizer_llm(config)
    if llm.enabled:
        try:
            new_row = sums.rebuild_one(db, manuscript, file, llm)
            print(ui.dim("summary rebuilt; downstream marked upstream_stale"))
            _print_coverage_note(file, new_row.get("paragraph_coverage"))
        except Exception as err:  # noqa: BLE001
            print(ui.yellow(f"summary rebuild failed ({err}) — run "
                            f"'summarize rebuild {file}'"))
    passes.set_essay_state(db, p, file, "resolved")
    order = [f for f, _ in sums.units(manuscript)]
    if file in order:
        idx = order.index(file)
        if idx >= p["cursor"]:
            db.update("critique_passes", p["id"], {"cursor": idx + 1})
    _print_pattern_candidate(
        passes.settle_learnings(db, manuscript, diffs, config))
    nxt = order[idx + 1] if file in order and idx + 1 < len(order) else None
    print(ui.dim(f"Next essay: {nxt} — 'critique run {nxt}' when you say so."
                 if nxt else "That was the last essay in reading order."))


def _print_pattern_candidate(candidate: dict | None) -> None:
    """The one line a resolve's pattern candidate earns. The distiller
    itself is `passes.settle_learnings`, shared by both passes and both
    filter transports; each CLI path owns its own output."""
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
    # Snapshot the current disk state before it's overwritten below — an
    # uncollected local edit made since the pin must not be silently
    # destroyed by the rollback (BUG-2 / A1). Config is loaded here,
    # above the write, so the pre-collect can run.
    config = _load_config(args)
    with contextlib.redirect_stdout(io.StringIO()):
        api.collect(db, manuscript, config, source="pre-critique-rollback")
    (Path(manuscript["path"]) / file).write_text(files[file], encoding="utf-8")
    for t in passes.staged_threads(db, mid, file,
                                   states=("written", "accepted", "proposed",
                                           "rejected")):
        db.update("doc_threads", t["id"], {"state": "withdrawn"})
    # The restore itself, recorded — and filed under NO episode (AQ;
    # §15.17's disposition table). Two things were wrong here. The
    # rollback was never collected at all, so the version history had the
    # pre-rollback snapshot and then a gap where the restore should be;
    # and had it been collected ambiently it would have filed the
    # UNWINDING of an edit pass against whatever goal happened to be
    # open, which is the same lie `critique resolve` was telling one
    # function away. `filter rollback` does exactly this, and the two
    # verbs now agree.
    #
    # The pre-rollback collect above stays AMBIENT, deliberately: it
    # snapshots the author's own uncollected edit, which is their work
    # and predates this verb.
    with contextlib.redirect_stdout(io.StringIO()):
        api.collect(db, manuscript, config, source="critique-rollback",
                    episode=api.NO_EPISODE)
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
                        ("element", "style_laws")):
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


def _print_coverage_note(file: str, cov: dict | None) -> None:
    """Report (never silently) when a rebuilt summary's MOVES didn't cite
    every paragraph number, or cited one that doesn't exist — verified
    coverage, not just requested."""
    if not cov or cov["complete"]:
        return
    parts = []
    if cov["missing"]:
        parts.append(f"missing paragraph(s) {cov['missing']}")
    if cov["out_of_range"]:
        parts.append(f"cited out-of-range paragraph(s) {cov['out_of_range']}")
    print(ui.yellow(f"  {file}: paragraph coverage incomplete — "
                    + "; ".join(parts)))


def cmd_summarize(args):
    from . import summaries as sums

    db = _open_db(args)
    manuscript = _manuscript(db, args)
    if args.action == "status":
        rows = sums.status(db, manuscript)
        counts: dict[str, int] = {}
        for r in rows:
            counts[r["state"]] = counts.get(r["state"], 0) + 1
            # cyan for the in-flight states: notable, but not "you must
            # act", which is what yellow means in this view.
            color = {"fresh": ui.green, "stale": ui.yellow,
                     "upstream_stale": ui.dim, "missing": ui.yellow,
                     "deprecated": ui.yellow, "rewriting": ui.cyan,
                     "unwritten": ui.cyan}.get(r["state"], ui.yellow)
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
        _print_coverage_note(args.file, row.get("paragraph_coverage"))
    else:
        result = sums.rebuild(
            db, manuscript, llm, only_missing_or_stale=not args.all,
            progress=lambda f: print(ui.dim(f"  summarizing {f}…")))
        print(ui.green(f"built {len(result['built'])}") + ui.dim(
            f", reused {len(result['reused'])} fresh"))
        for f in result.get("skipped_inflight", []):
            print(ui.cyan(
                f"  skipped {f} — a writeup is writing it for the first "
                "time, so there is no pre-rewrite text to summarize"))
        for f, cov in result.get("incomplete_coverage", {}).items():
            _print_coverage_note(f, cov)
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
    return report


def _print_open_directives(rows: list[dict]) -> None:
    """The open [Footnote: …] / [Explain: …] tags, per file — collect's
    report and `footnote list` / `explain list` alike."""
    by_file: dict[str, list[dict]] = {}
    for row in rows:
        by_file.setdefault(row["file"], []).append(row)
    for file, tags in by_file.items():
        kinds = {}
        for t in tags:
            kinds[t["kind"]] = kinds.get(t["kind"], 0) + 1
        summary = ", ".join(f"{n} open {k} tag{'s' if n != 1 else ''}"
                            for k, n in kinds.items())
        print(ui.yellow(f"{file}: {summary}"))
        for t in tags:
            label = f" | label: {t['label']}" if t.get("label") else ""
            print(f"  • #{t['n']} line {t['line']}  "
                  f"[{t['kind'].title()}: {t['gist']}{label}]")
    if rows:
        print(ui.dim("  Draft in chat; land with 'footnote apply' / "
                     "'explain apply <file> --tag N --text …'."))


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
    if report.get("summaries_deprecated"):
        print(ui.dim(
            "Essay summary(ies) deprecated (file left the toc; kept, "
            "not deleted): " + ", ".join(report["summaries_deprecated"])))
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
        if ill.get("malformed_refs"):
            print(ui.yellow(
                f"{len(ill['malformed_refs'])} illustration tag(s) carry "
                "a malformed ⇢ ref (arrow present but not a valid "
                "'name.md' reference) — read as plain inline text for "
                "now; fix the ref or delete it:"))
            for slot in ill["malformed_refs"]:
                print(f"  • {slot['file']}:{slot['line']}  "
                      f"[Illustration: {slot['prompt']}]")
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
    if report.get("directives"):
        _print_open_directives(report["directives"])
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


def cmd_directive(args):
    """`footnote|explain list|apply|resolve` — the inline directives'
    verbs (docs/footnote-directive-design.md §5/§8,
    docs/explain-directive-design.md). Drafting is chat. `apply` lands
    the agreed text directly when the file is local, or writes it into
    the Doc tab as <<tag>>{{new}} forms when the file is checked out —
    local stays pristine — and `resolve` finishes that road."""
    from pathlib import Path

    from . import directives as dv

    kind = args.command
    db = _open_db(args)
    manuscript = _manuscript(db, args)
    root = Path(manuscript["path"])
    if args.action == "list":
        rows = dv.open_report(root, (kind,), [args.file] if args.file else None)
        out = dv.pending(db, manuscript["id"], kind, args.file or None)
        if not rows and not out:
            print(f"No open [{kind.title()}: …] tags.")
            return
        _print_open_directives(rows)
        by_file: dict[str, int] = {}
        for t in out:
            by_file[t["file"]] = by_file.get(t["file"], 0) + 1
        for file, n in by_file.items():
            print(ui.cyan(f"{file}: {n} {kind} form(s) out in the Doc tab — "
                          f"'{kind} resolve {file}' when the author has "
                          "ruled there."))
        return
    if args.action not in ("apply", "resolve"):
        sys.exit(f"usage: {kind} list|apply|resolve [file]")
    if not args.file:
        sys.exit(f"usage: {kind} {args.action} <file>"
                 + (" --tag N --text '…' [--tag N --text '…']…"
                    if args.action == "apply" else ""))
    config = _load_config(args)

    def _services():
        from . import gdocs as _gd

        return (_gd.get_service(config, args.workspace, interactive=True),
                _gd.get_docs_service(config, args.workspace, interactive=True))

    try:
        if args.action == "apply":
            tags, texts = args.tag or [], args.text or []
            if not tags or len(tags) != len(texts):
                sys.exit(f"{kind} apply: give one --text for every --tag "
                         f"({len(tags)} --tag, {len(texts)} --text)")
            notes: dict[str, str] = {}
            for spec in args.footnote or []:
                handle, sep, note = spec.partition("=")
                if not sep or not handle.strip():
                    sys.exit(f"{kind} apply: --footnote takes TAG=TEXT, "
                             f"where TAG is one of the --tag handles")
                notes[handle.strip()] = note
            unknown = set(notes) - {str(t).strip() for t in tags}
            if unknown:
                sys.exit(f"{kind} apply: --footnote names a tag not given "
                         f"as --tag: {', '.join(sorted(unknown))}")
            pairs = [(t, x, notes.get(str(t).strip())) for t, x in zip(tags, texts)]
            result = dv.apply(db, manuscript, config, kind, args.file,
                              pairs, services=_services)
        else:
            result = dv.resolve(db, manuscript, config, kind, args.file,
                                services=_services)
    except (LookupError, ValueError) as err:
        sys.exit(f"error: {err}")
    if args.action == "resolve":
        print(ui.green(f"resolved {args.file}: {result['forms']} form(s) read "
                       f"back — {result['accepted']} landed, "
                       f"{result['declined']} declined (tag left open)"))
        for d in result["diffs"]:
            print(ui.dim(f"  «{d['proposal'][:60]}» → «{d['final'][:60]}»"))
        print(ui.dim(f"  Collected as v{result['version_no']} (no episode)."))
        if result["url"]:
            print(f"Pushed {args.file} → tab {ui.dim(ui.link(result['url']))}")
        for w in result["warnings"]:
            print(ui.yellow(f"warning: {w}"))
        return
    for row in result["landed"]:
        head = (f"[^{row['label']}]" if row["label"]
                else f"[{kind.title()}: {row['gist']}]")
        verb = "Landed" if result["mode"] == "applied" else "Staged"
        print(ui.green(f"{verb} {args.file}:{row['line']}  {head}"))
        print(ui.dim("    " + row["text"].replace("\n", "\n    ")))
        if row.get("footnote"):
            print(ui.dim(f"    [^{row['label']}]: " + row["footnote"]))
    for gist, why in result["failed"]:
        print(ui.yellow(f"Not written: [{kind.title()}: {gist}] — {why}"))
    if result["mode"] == "applied":
        print(ui.dim(f"  Collected as v{result['version_no']} (no episode); "
                     f"{len(result['landed'])} evidence row(s). 'doc push' "
                     "carries it to the Doc when you next push."))
    else:
        print(f"Forms written to the tab {ui.dim(ui.link(result['url']))} — "
              f"local file untouched. Edit or accept in the Doc, then "
              f"'{kind} resolve {args.file}'.")


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
        if report.get("prompt_files"):
            print(ui.dim("Prompts: " + ", ".join(report["prompt_files"])))
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

    if args.action == "show":
        if not args.name:
            raise SystemExit("usage: authorlm illus show <prompt-fragment>")
        rows = [r for r in illus.slot_status(db, manuscript)
                if args.name.lower() in (r.get("prompt") or "").lower()
                or args.name in f"{r['file']}:{r['line']}"]
        if not rows:
            raise SystemExit(f"error: no illustration slot matches "
                             f"'{args.name}' ('illus list' shows them).")
        for r in rows:
            print(ui.bold(f"{r['file']}:{r['line']}  [{r['state']}]"))
            print(f"  prompt: {r.get('prompt', '')}")
            if r.get("caption"):
                print(f"  caption: {r['caption']}")
            for key in ("candidates", "embedded", "desc_hash", "style_hash"):
                if r.get(key):
                    print(ui.dim(f"  {key}: {r[key]}"))
            print(ui.dim("\n  → 'illus prompt <fragment>' for the exact "
                         "composed prompt a render would send."))
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
        moved = [r for r in rows if r["state"] == "stale-desc"]
        if moved:
            print(ui.dim(
                f"{len(moved)} slot(s) hold art made for a description you "
                "have since rewritten. The art is KEPT — the key holds it "
                "— so this is a note, not a loss: 'illus render <fragment> "
                "--from N' redraws it against the new words."))
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
        illus.set_embed(root / slot["file"], slot["key"], newest)
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
                 "caption": None, "ref": s.get("ref"),
                 "key": s.get("key"),
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

    if args.action == "import":
        if not args.candidate:
            raise SystemExit("usage: authorlm illus import '<fragment>' "
                             "<image.png>")
        slot = resolve_slot()
        if args.caption:
            slot = {**slot, "caption": args.caption}
        try:
            result = illus.import_image(db, manuscript, slot, args.candidate)
        except (ValueError, OSError) as err:
            raise SystemExit(f"error: {err}")
        print(f"Imported {result['source']} → "
              f"_illustrations/{result['name']}")
        if result["ref"] != slot.get("ref"):
            print(ui.dim(f"  slot externalized first → "
                         f"_illustrations/prompts/{result['ref']} — the "
                         f"first image mints a slot's key, and the key is "
                         f"what keeps this image attached when you "
                         f"reword the description."))
        if not result["had_embed"]:
            print(f"  embedded → {result['name']}")
        else:
            print(ui.dim(f"  embed kept at {result['embedded']} — "
                         f"'illus pick {result['n']}' to stand this one "
                         f"up instead."))
        print(ui.dim(f"  'illus render <fragment> --from {result['n']}' "
                     f"evolves it under the current illustration law."))
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
        try:
            wanted = int(args.candidate)
        except ValueError:
            raise SystemExit(f"pick takes a candidate NUMBER, not "
                             f"{args.candidate!r}")
        cands = illus.slot_candidates(root, slot["key"])
        target = next((c for c in cands
                       if int(c["n"]) == wanted), None)
        if target is None:
            have = ", ".join(c["src"] + c["n"] for c in cands) or "none"
            raise SystemExit(f"no candidate {wanted:02d} "
                             f"(held: {have})")
        illus.set_embed(root / slot["file"], slot["key"], target["name"])
        # Render-side learning loop: which candidate won (and over what
        # field) is evidence for future illustration law.
        from .db import ko_fields as _ko

        ev = _ko("ev")
        ev.update(manuscript_id=manuscript["id"], episode_id=None,
                  evidence_type="illus_render", signal="picked",
                  target=(f"{slot['file']}: «{slot['prompt'][:80]}» — "
                          f"picked {target['name']} over "
                          f"{max(len(cands) - 1, 0)} other candidate(s)"),
                  supports_belief=None, weight="medium")
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

    if args.action == "versions":
        versions = illus.list_prompt_versions(db, manuscript)
        if not versions:
            print("No illustration-prompt snapshots yet — one is taken "
                  "automatically, before the fact, the first time a "
                  "'doc pull' or session-start reconcile touches "
                  "_illustrations/prompts/.")
            return
        print(f"Illustration-prompt snapshots ({len(versions)}):")
        for v in versions:
            print(f"  v{v['version_no']:<3} {v['created_at']}  "
                  f"{v['file_count']} file(s)  source={v['source']}")
        print(ui.dim("Restore one with 'illus restore --version N' "
                     "(omit --version for the latest)."))

    if args.action == "restore":
        try:
            plan = illus.preview_restore(db, manuscript, args.version)
        except LookupError as err:
            raise SystemExit(f"error: {err}")
        label = f"v{plan['version_no']}"
        print(f"Restoring illustration prompts from snapshot {label} "
              f"(taken {plan['created_at']}, before a "
              f"{plan['source']!r} write):")
        for name in plan["files"]:
            print(f"  _illustrations/prompts/{name}")
        print(ui.yellow(
            "This OVERWRITES the current content of the file(s) above "
            f"with the {label} text. It is additive, not a wholesale "
            "revert to that moment: any prompt file created since this "
            "snapshot is left untouched."))
        result = illus.restore_prompts(db, manuscript, args.version)
        print(ui.green(f"Restored {len(result['restored'])} file(s) "
                       f"from {label}."))


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


def _print_filter_payload(result: dict) -> None:
    """The payload, block by block, with hashes — `write draft
    --dry-run`'s shape, so a silent cache invalidator on the native path
    is findable and the author can audit what is actually sent."""
    payload = result["payload"]
    print(ui.dim(f"Prompt: {result['prompt_location']}"))
    for name, text in payload.blocks:
        print()
        print(ui.bold(f"───── block {name} — {len(text):,} chars — "
                      f"sha256 {payload.hashes[name]}"))
        print(text)
    print()
    print(ui.dim("Identical stored state gives byte-identical blocks S and "
                 "A. A hash that moved between two windows IS the cache "
                 "invalidator."))


def _print_filter_warnings(warnings) -> None:
    for line in warnings or []:
        print(ui.yellow(f"!! {line}"))


def cmd_filter(args):
    """`authorlm filter` — the filter pass (docs/filter-pass-design.md).

    A LENS reads one essay whole and reports findings; a FILTER reads one
    essay unit by unit and proposes an edit to each unit. Its output goes
    through the edit door, not the guidance queue."""
    from . import filters as flt

    db = _open_db(args)
    manuscript = _manuscript(db, args)
    config = _load_config(args)

    # Two positional shapes, one parser. `add`, `show`, `prelude` and
    # `run` are NAME-first; everything after the payload is staged is
    # FILE-first, because by then the author is talking about the essay
    # and not about the filter. The optional name stays legal on the
    # file-first verbs for the one case that needs it — two filters with
    # active runs on the same essay — so `filter resolve becker.md` and
    # `filter resolve duplicate-words becker.md` both read naturally.
    if args.action not in ("add", "show", "prelude", "run") and \
            args.file is None and args.name is not None:
        args.name, args.file = None, args.name

    try:
        if args.action == "add":
            text = _stdin_text()
            if not args.name:
                raise SystemExit("usage: authorlm filter add <name>  "
                                 "(the artifact, front matter and prompt, "
                                 "on stdin)")
            out = api.filter_add(manuscript, args.name, text or "")
            print(f"Filter '{args.name}' ratified → {out['path']}")
            print(ui.dim(f"class = {out['class']} — {out['class_help']}"))
            if out["state"]:
                print(ui.dim(f"state: {out['state']}"))
            if out.get("prelude"):
                print(ui.dim(f"prelude = {out['prelude']} — run it with "
                             f"'filter prelude {args.name} <essay.md>'; it "
                             f"proposes dictionary rows and judges no "
                             f"unit."))
            if out.get("summaries"):
                print(ui.dim("summaries = true — the payload carries "
                             "the neighbouring essays' summaries in "
                             "block A, stale ones marked and never "
                             "blocking. It is worth real tokens; do "
                             "not window a run that declares it."))
            return

        if args.action == "list":
            rows = flt.list_filters(manuscript)
            if not rows:
                print("No filters defined. Create one: authorlm filter add "
                      "<name>  (the artifact on stdin)")
                return
            for row in rows:
                if row["error"]:
                    print(ui.yellow(f"  {row['name']}: MALFORMED — "
                                    f"{row['error'].splitlines()[0]}"))
                    continue
                print(f"  {row['name']} [{row['class']}]: {row['summary']}")
            return

        if args.action == "show":
            if not args.name:
                raise SystemExit("usage: authorlm filter show <name>")
            shown = flt.show_filter(manuscript, args.name)
            print(ui.bold(f"Filter '{args.name}' — {shown['path']}"))
            print(ui.dim(f"class = {shown['class']} — {shown['class_help']}"))
            if shown["state"]:
                print(ui.dim(f"state: {shown['state']}"))
            if shown.get("prelude"):
                print(ui.dim(f"prelude = {shown['prelude']}"))
            if shown.get("summaries"):
                print(ui.dim("summaries = true — neighbouring essay summaries in block A"))
            print()
            print(shown["prompt"])
            return

        if args.action == "status":
            report = api.filter_status(db, manuscript, args.file)
            if not report["runs"]:
                print("No filter runs recorded.")
            for r in report["runs"]:
                head = (f"{r['filter']} on {r['file']} [{r['class']}] — "
                        f"{r['status']}, {r['date']}")
                print(ui.bold(head))
                print(f"  units {r['cursor']}/{r['unit_count']} processed; "
                      f"{r['proposed']} proposed, {r['accepted']} accepted, "
                      f"{r['rejected']} rejected, {r['open']} awaiting a "
                      f"verdict")
                print(ui.dim(
                    f"  transport: {r['mode'] or 'not chosen yet'}"
                    + (f"; {r['forms_out']} form(s) out"
                       if r["forms_out"] else "")
                    + (f" — {r['tab_url']}" if r.get("tab_url") else "")))
                if r["class_now"]:
                    print(ui.yellow(
                        f"  !! the artifact's class is now "
                        f"'{r['class_now']}'; this run is '{r['class']}' "
                        f"and will finish as one"))
            for rel in report["orphaned_marks"]:
                print(ui.yellow(
                    f"!! {rel} carries pending forms on disk with no "
                    f"matching staged edit — a crash between composing and "
                    f"writing the threads leaves exactly this. Recover with "
                    f"'filter unmark {rel}'."))
            if report.get("orphan_scan_note"):
                print(ui.dim(report["orphan_scan_note"]))
            return

        if args.action == "edits":
            if not args.file:
                raise SystemExit("usage: authorlm filter edits <essay.md>")
            report = api.filter_edits(db, manuscript, args.file)
            if not report["count"]:
                print(f"No staged filter edits on {report['file']}.")
                return
            for item in report["items"]:
                ref = f" ({item['ref']})" if item["ref"] else ""
                print(f"{ui.cyan(str(item['n']) + '.')} ¶{item['unit']} "
                      f"[{item['state']}]{ref} {item['why']}")
                print(ui.dim(f"    − {gdocs_clamp(item['old'])}"))
                print(f"    + {gdocs_clamp(item['new'])}")
            print(ui.dim("\nBulk verdicts by number: filter triage <essay> "
                         "--accept 1 2 5 | --reject 3 --reason \"…\" | "
                         "--revise 6 --text \"…\""))
            return

        if args.action == "triage":
            if not args.file:
                raise SystemExit("usage: authorlm filter triage <essay.md> "
                                 "--accept … | --reject … --reason \"…\"")
            ops = []
            for token in args.accept or []:
                ops.append({"item": token, "verdict": "accept"})
            for token in args.reject or []:
                ops.append({"item": token, "verdict": "reject",
                            "reason": args.reason})
            for token in args.revise or []:
                ops.append({"item": token, "verdict": "revise",
                            "text": args.text})
            for token in args.undo or []:
                ops.append({"item": token, "verdict": "undo"})
            if not ops:
                raise SystemExit("nothing to record — pass --accept / "
                                 "--reject / --revise / --undo with the "
                                 "numbers from 'filter edits'")
            report = api.filter_triage(db, manuscript, args.file, ops)
            for r in report["results"]:
                if r.get("ok"):
                    print(f"  [{r['item']}] {r['verdict']} → {r['state']}")
                else:
                    print(ui.yellow(f"  [{r['item']}] {r['error']}"))
            print(ui.dim(f"{report['still_proposed']} still awaiting a "
                         f"verdict."))
            return

        if args.action == "prelude":
            if not (args.name and args.file):
                raise SystemExit("usage: authorlm filter prelude <name> "
                                 "<essay.md>  (the registry JSON on stdin, "
                                 "or --native)")
            result = api.filter_prelude(
                db, manuscript, config, args.name, args.file,
                replace=args.replace, native=args.native,
                reply=_stdin_text())
            _print_filter_warnings(result.get("warnings"))
            if result.get("kind") == "pronunciations":
                if "proposed" not in result:
                    print(ui.dim(
                        f"Pronunciation prelude payload for "
                        f"{result['file']} ({result['unit_count']} units) "
                        f"— no call made. Draft the reply against it and "
                        f"pipe {{\"pronunciations\": [...]}} back into "
                        f"this same command. No unit is judged and the "
                        f"essay is not touched."))
                    _print_filter_payload(result)
                    return
                proposed, suppressed = (result["proposed"],
                                        result["suppressed"])
                print(ui.green(
                    f"{len(proposed)} pronunciation(s) proposed"
                    + (f", {len(suppressed)} suppressed" if suppressed
                       else "") + "."))
                for term in proposed:
                    print(f"  · {term}")
                for term in suppressed:
                    print(ui.dim(f"  (already settled, not re-asked: "
                                 f"{term})"))
                if proposed:
                    print(ui.dim("Rule on them with 'proposal review' — "
                                 "accepting one writes the row into "
                                 "pronunciations.md."))
                if result.get("usage_line"):
                    print(ui.dim(result["usage_line"]))
                return
            if result["registry"] is None:
                print(ui.dim(f"Prelude payload for {result['file']} "
                             f"({result['unit_count']} units) — no call "
                             f"made. Draft the registry against it and pipe "
                             f"{{\"registry\": \"…\"}} back into this same "
                             f"command."))
                _print_filter_payload(result)
                return
            print(ui.green(
                f"Registry {'replaced' if result['replaced'] else 'frozen'} "
                f"for this run ({len(result['registry']):,} chars)."))
            if result["replaced"]:
                print(ui.yellow("!! the run's cached prefix is invalidated "
                                "— on the native path the next unit call "
                                "re-bills blocks S and A."))
            if result.get("usage_line"):
                print(ui.dim(result["usage_line"]))
            return

        if args.action == "run":
            if not (args.name and args.file):
                raise SystemExit("usage: authorlm filter run <name> "
                                 "<essay.md>")
            result = api.filter_run(
                db, manuscript, config, args.name, args.file,
                window=args.window, from_unit=getattr(args, "from_unit", None),
                again=args.again, native=args.native)
            _print_filter_warnings(result["warnings"])
            start, end = result["window"]
            print(ui.bold(
                f"{result['filter']} [{result['class']}] on "
                f"{result['file']} — units {start}–{end} of "
                f"{result['unit_count']}"))
            if not result["native"]:
                print(ui.dim(
                    "No model call was made. This is the DEFAULT and it is "
                    "the opposite of 'write draft', which calls unless you "
                    "pass --dry-run: here the call is what needs the flag "
                    "(--native), and it needs [filtering] in config.toml "
                    "as well. Draft the reply against the payload below, "
                    "then pipe it into 'filter record'."))
                _print_filter_payload(result)
                return
            print(ui.dim(f"Native run on {result['model']}."))
            print(ui.dim(result["usage_line"] or ""))
            print(json.dumps({"units": result["reply"]["edits"],
                              "keeps": result["reply"]["keeps"]}, indent=2))
            print(ui.dim("Pipe the model's reply into 'filter record' to "
                         "stage it — nothing is staged by the call itself."))
            return

        if args.action == "record":
            if not args.file:
                raise SystemExit("usage: authorlm filter record <essay.md>  "
                                 "(the reply JSON on stdin)")
            reply = _stdin_text()
            if not reply:
                raise SystemExit("the reply JSON travels on stdin — nothing "
                                 "arrived. Nothing was staged and the "
                                 "cursor did not move.")
            result = api.filter_record(db, manuscript, config, args.file,
                                       reply, name=args.name)
            start, end = result["window"]
            print(ui.green(
                f"Recorded units {start}–{end}: {len(result['staged'])} "
                f"proposal(s), {len(result['keeps'])} kept."))
            _print_filter_warnings(result["warnings"])
            if result["remaining"] > 0:
                print(ui.dim(f"{result['remaining']} unit(s) left — "
                             f"'filter run {result['run']['filter']} "
                             f"{result['file']}' assembles the next window."))
            elif result["staged"]:
                print(ui.dim(f"Read them back to the author, then "
                             f"'filter triage {result['file']} --accept …'."))
            return

        if args.action == "apply":
            # RECORD then PUSH, under one name, because from the author's
            # seat those are one act: the proposals reach the tab they rule
            # them in. The DRAFTING step cannot be folded in here — in chat
            # mode `filter run` makes no model call at all, the assistant
            # answers the payload in the conversation, and no CLI process
            # can stand in for that. So this wraps the two verbs that ARE
            # mechanical and leaves the one that is judgment where it is.
            if not args.file:
                raise SystemExit(
                    "usage: authorlm filter apply [<name>] <essay.md>  "
                    "(the reply JSON on stdin)\n"
                    "It records the reply and pushes the proposals into the "
                    "essay's Doc tab in one step. Draft the reply against "
                    "'filter run <name> <essay>' first.")
            reply = _stdin_text()
            if not reply:
                raise SystemExit(
                    "the reply JSON travels on stdin — nothing arrived. "
                    "Nothing was staged, nothing was pushed, and the cursor "
                    "did not move.")
            result = api.filter_record(db, manuscript, config, args.file,
                                       reply, name=args.name)
            start, end = result["window"]
            print(ui.green(
                f"Recorded units {start}–{end}: {len(result['staged'])} "
                f"proposal(s), {len(result['keeps'])} kept."))
            _print_filter_warnings(result["warnings"])
            if result["remaining"] > 0:
                # Pushing a partial run would put half an essay's forms in
                # the tab and commit the run to the Doc road before the rest
                # of it has been judged. The road is chosen once, so it is
                # chosen when the run is complete.
                print(ui.dim(
                    f"{result['remaining']} unit(s) left — nothing pushed "
                    f"yet. 'filter run {result['run']['filter']} "
                    f"{result['file']}' assembles the next window; the "
                    f"apply that completes the run is the one that pushes."))
                return
            if not result["staged"]:
                print(ui.dim("No proposals, so there is nothing to push and "
                             "this run has taken no road."))
                return
            args.action = "push"          # fall through to the push branch

        if args.action == "push":
            if not args.file:
                raise SystemExit("usage: authorlm filter push <essay.md>")
            def _push_bridge():
                from . import gdocs as _gd

                try:
                    return (_gd.get_service(config, args.workspace,
                                            interactive=True),
                            _gd.get_docs_service(config, args.workspace,
                                                 interactive=True))
                except ValueError as err:
                    # The local road needs no credentials and is the
                    # DEFAULT and the recommendation — an author without
                    # a Doc bridge must never be left thinking the pass
                    # is unavailable.
                    raise ValueError(
                        f"{err}\nThe Doc road needs the [gdocs] bridge. "
                        f"The local road does not: 'filter resolve "
                        f"{args.file} --pause' writes the same "
                        f"<<old>>{{{{new}}}} forms into the file in your "
                        f"vault, and 'filter resolve {args.file}' "
                        f"finalizes them.") from err

            result = api.filter_push(db, manuscript, config, args.file,
                                     services=_push_bridge, name=args.name)
            _print_filter_warnings(result["warnings"])
            for t, why in result["failed"]:
                print(ui.yellow(f"  could not write [{t['id'][:8]}]: {why}"
                                " — left accepted"))
            if not result["written"]:
                print(ui.yellow(
                    "Nothing landed in the Doc, so this run has NOT taken "
                    "the Doc road — the local settle is still open to it."))
                return
            print(ui.green(f"{result['written']} change(s) written into "
                           f"{result['file']}'s tab")
                  + ui.dim(f" — {result['url']}"))
            print(ui.dim(
                "Old text struck through, the new text beside it in green. "
                "The tab is where you rule on these: leave a change alone "
                "to take it, empty its green half to turn it down, or "
                "reword the green half to make it yours — your wording "
                f"wins. Then 'filter resolve {result['file']}' reads the "
                "tab back and records every one of those verdicts."))
            if result["local_unchanged"]:
                print(ui.dim(
                    f"The file on your disk still holds {result['file']} "
                    "exactly as it was, and it will not push to Docs "
                    "until this is finished."))
            else:
                # The push levels the tab from the local file, and that
                # goes through `normalize_markdown` — so on a file that
                # was not already canonical the bytes DID move. Saying
                # "exactly as it was" there is false, and an author who
                # later finds a diff they were told did not exist has
                # been given a reason to distrust the whole road.
                print(ui.dim(
                    f"The file on your disk still holds the OLD "
                    f"{result['file']} — none of these changes is in it "
                    f"— but the push did rewrite it once, into canonical "
                    f"markdown (list markers, spacing, trailing "
                    f"whitespace; not a word of the prose). That is the "
                    f"same normalization every 'doc push' does. It will "
                    f"not push to Docs again until this is finished."))
            return

        if args.action == "resolve":
            if not args.file:
                raise SystemExit("usage: authorlm filter resolve <essay.md> "
                                 "[--pause]")
            def _doc_bridge():
                from . import gdocs as _gd

                return (_gd.get_service(config, args.workspace,
                                        interactive=True),
                        _gd.get_docs_service(config, args.workspace,
                                             interactive=True))

            # A CALLABLE, not two arguments: the local road must work
            # with no credentials, so nothing here can pop a consent
            # window unless the run actually took the Doc road.
            result = api.filter_resolve(db, manuscript, config, args.file,
                                       pause=args.pause, name=args.name,
                                       services=_doc_bridge)
            _print_filter_warnings(result["warnings"])
            if result["paused"]:
                print(ui.green(
                    f"Marked {result['file']} with {result['forms']} "
                    f"change(s) — open it and edit any of the {{{{new}}}} "
                    f"halves you want to reword, then 'filter resolve "
                    f"{result['file']}' with no flag to finalize."))
                print(ui.dim("The file will not push to Docs while it is "
                             "marked, and every observer still reads the "
                             "original text."))
                return
            made = result.get("accepted", result["forms"])
            if result.get("mode") == "doc":
                print(ui.green(
                    f"Finalized: {made} change(s) made final in "
                    f"{result['file']}"
                    + (f", {len(result['diffs'])} of them in your wording "
                       f"rather than mine" if result["diffs"] else "")
                    + "."))
            else:
                print(ui.green(f"Applied: {made} change(s) made "
                               f"final in {result['file']}."))
            for d in result["diffs"]:
                print(ui.dim(f"  «{gdocs_clamp(d['proposal'])}» → "
                             f"«{gdocs_clamp(d['final'])}»"))
            for row in result["falsified_prefix"]:
                units = ", ".join(str(u) for u in row["downstream"])
                print(ui.yellow(
                    f"!! n={row['n']} rejected. Units {units} were drafted "
                    f"after it and may have assumed it.\n"
                    f"   Re-run the tail if their edits depended on it:  "
                    f"filter run {result['run']['filter']} "
                    f"{result['file']} --from {row['n']}"))
            summary = result["summary"]
            if summary["rebuilt"]:
                print(ui.dim("summary rebuilt; downstream marked "
                             "upstream_stale"))
                if summary["usage"]:
                    print(ui.dim(summary["usage"]))
            elif summary["error"]:
                print(ui.yellow(f"summary rebuild failed "
                                f"({summary['error']}) — run 'summarize "
                                f"rebuild {result['file']}'"))
            _print_pattern_candidate(result.get("pattern_candidate"))
            if result.get("tab_still_marked"):
                print(ui.dim(
                    f"The Doc tab still shows the struck-and-green marks: "
                    f"a resolve that also re-pushed could fail halfway on "
                    f"the network after the evidence was recorded, so it "
                    f"does not. Your next 'doc push {result['file']}' "
                    f"clears them."))
            print(ui.dim("Nothing here was filed against any of your goals: "
                         "a filter pass is hygiene, not work toward a "
                         "declared aim, and recording it as if it were "
                         "would make that goal's completion report say "
                         "something untrue."))
            return

        if args.action == "unmark":
            if not args.file:
                raise SystemExit("usage: authorlm filter unmark <essay.md>")
            def _unmark_bridge():
                from . import gdocs as _gd

                return (_gd.get_service(config, args.workspace,
                                        interactive=True),
                        _gd.get_docs_service(config, args.workspace,
                                             interactive=True))

            result = api.filter_unmark(db, manuscript, args.file,
                                       force=args.force,
                                       services=_unmark_bridge)
            for warn in result["marker_warnings"]:
                print(ui.yellow(f"  {warn}"))
            if result["mode"] == "doc":
                print(ui.green(
                    f"{result['file']}'s tab rebuilt clean; "
                    f"{result['reopened']} form(s) returned to 'accepted' "
                    f"— 'filter resolve' applies them, 'filter triage "
                    f"--undo' reopens them."))
                print(ui.dim(
                    "The tab was rebuilt from the file on disk, so it now "
                    "shows the essay as it stands there — anything typed "
                    "into that tab since the push is gone. The file itself "
                    "was never touched: it has held the old text "
                    "throughout."))
                return
            print(ui.green(
                f"{result['file']} restored to its original text; "
                f"{result['reopened']} form(s) returned to 'accepted' — "
                f"'filter resolve' applies them, 'filter triage --undo' "
                f"reopens them."))
            return

        if args.action == "rollback":
            if not args.file:
                raise SystemExit("usage: authorlm filter rollback <essay.md>")
            result = api.filter_rollback(db, manuscript, config, args.file,
                                         name=args.name)
            print(ui.green(f"{result['file']} restored to the run's pinned "
                           f"version ({result['restored_chars']:,} chars)."))
            print(ui.dim("The verdicts stay: they are evidence, and "
                         "evidence is not undone by putting text back."))
            return

        if args.action == "abandon":
            if not args.file:
                raise SystemExit("usage: authorlm filter abandon <essay.md>")
            result = api.filter_abandon(db, manuscript, args.file,
                                        name=args.name)
            print(ui.green(f"Run dropped; {result['withdrawn']} open "
                           f"proposal(s) withdrawn."))
            return
    except (LookupError, ValueError, RuntimeError) as err:
        sys.exit(f"error: {err}")


def _lens_autopush(db, manuscript, args, target: str, staged: int) -> None:
    """The Doc road is the standing protocol (author ruling 2026-09-01:
    "always add these to the doc using the old/new syntax"): every lens
    verb that stages an edit pushes it in the same turn unless --no-push.
    A push failure is reported, never fatal — the edits are safe on disk
    and 'lens push <essay>' retries."""
    from . import api
    if not staged or getattr(args, "no_push", False):
        if staged:
            print(ui.dim(f"{staged} edit(s) staged and NOT pushed (--no-push); "
                         f"'lens push {target}' sends them to the tab."))
        return
    config = _load_config(args)

    def _doc_bridge():
        from . import gdocs as _gd
        return (_gd.get_service(config, args.workspace, interactive=True),
                _gd.get_docs_service(config, args.workspace, interactive=True))
    try:
        result = api.lens_push(db, manuscript, config, target,
                               services=_doc_bridge,
                               supersede=getattr(args, "supersede", False))
    except (LookupError, ValueError, RuntimeError) as err:
        print(ui.yellow(f"staged {staged} edit(s) but the push did not land: "
                        f"{err} — 'lens push {target}' retries."))
        return
    _print_filter_warnings(result["warnings"])
    for t, why in result["failed"]:
        print(ui.yellow(f"  failed to land: «{gdocs_clamp(t['proposed_old'])}» "
                        f"— {why}"))
    print(ui.green(f"Pushed {result['written']} lens form(s) into "
                   f"{result['file']}'s tab → {result['url']}"))
    print(ui.dim("Findings WITHOUT a rewrite do not reach the Doc — they are "
                 f"judgment findings; read them with 'lens findings {target}' "
                 "and rule with 'lens review <gd-id>'."))


def gdocs_clamp(text: str, limit: int = 90) -> str:
    from .gdocs import clamp

    return clamp(text or "", limit)


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
        try:
            path = lenses.add_lens(manuscript, args.name, prompt)
        except (lenses.LensError, ValueError) as err:
            raise SystemExit(f"error: {err}")
        print(f"Lens '{args.name}' ratified → {path}")
        return

    if args.action == "show":
        if not args.name:
            raise SystemExit("usage: authorlm lens show <name>")
        path = lenses._lens_path(manuscript, args.name)
        if not path.is_file():
            raise SystemExit(f"error: no lens '{args.name}' "
                             "('lens list' shows them).")
        print(ui.bold(f"Lens '{args.name}' — {path}"))
        try:
            meta, _body = lenses.parse_lens(path.read_text(encoding="utf-8"))
            print(ui.dim(f"class {meta['class']}; inputs "
                         f"{', '.join(meta['inputs'])}"
                         + (f"; targets {', '.join(meta['targets'])}"
                            if meta['targets'] else "")
                         + f"; examples {meta['examples']}"))
        except lenses.LensError as err:
            print(ui.yellow(f"MALFORMED front matter: {err}"))
        print(path.read_text(encoding="utf-8").rstrip())
        return

    if args.action == "list":
        rows = lenses.list_lenses(manuscript)
        if not rows:
            print("No lenses defined. Create one: authorlm lens add "
                  "<name>  (prompt on stdin)")
        for row in rows:
            if row.get("error"):
                print(f"  {row['name']}: MALFORMED — {row['error']}")
                continue
            tgt = f" → {','.join(row['targets'])}" if row["targets"] else ""
            print(f"  {row['name']} [{row['class']}{tgt}]: {row['summary']}")
        return
    if args.action == "findings":
        target = args.name or args.file
        if not target:
            raise SystemExit("usage: authorlm lens findings <essay.md> [--all]")
        try:
            rows = lenses.findings(db, manuscript, target, all_states=args.all)
        except LookupError as err:
            raise SystemExit(f"error: {err}")
        if not rows:
            print(f"No {'lens findings' if args.all else 'open lens findings'} "
                  f"on {target}.")
            return
        for r in rows:
            tags = []
            if r["edit_thread"]:
                tags.append("edit staged")
            if r["footnote"]:
                tags.append("footnote requested")
            if r.get("judgment"):
                tags.append("judgment")
            if r["also_flagged_by"]:
                tags.append("also: " + ", ".join(r["also_flagged_by"]))
            if r["target_file"]:
                tags.append(f"→ {r['target_file']}")
            if not r["present"]:
                tags.append("QUOTE NO LONGER IN FILE")
            print(f"{r['id'][:11]}  [{r['lens']}] "
                  + (f"{r['rule']} " if r["rule"] else "")
                  + (f"({', '.join(tags)})" if tags else "")
                  + ("" if r["state"] == "proposed" else f"  {r['state']}"))
            print(f"    On “{(r['quote'] or '')[:160]}”")
            note = r["note"].split("] ", 1)[-1]
            print(ui.dim(f"    {note[:400]}"))
        print(ui.dim("Verdicts: lens review <gd-id> --accept|--reject "
                     "[--explain \"why\"] (the id prefix printed above)."))
        return
    if args.action == "repair":
        target = args.name or args.file
        if not target:
            raise SystemExit("usage: authorlm lens repair <essay.md> [--native | "
                             "--reply <json> | --dismiss <ids> --reason …] "
                             "[--out PATH] [--no-push]")
        session, _ = api.ensure_session(db, manuscript)
        if args.dismiss:
            if not args.reason:
                raise SystemExit("--dismiss needs --reason, the author's words")
            ids = [x.strip() for x in args.dismiss.split(",") if x.strip()]
            try:
                res = lenses.dismiss_judgments(db, manuscript, target, ids,
                                               args.reason)
            except LookupError as err:
                raise SystemExit(f"error: {err}")
            print(f"Dismissed {len(res['dismissed'])} judgment(s)"
                  + (f"; not found: {', '.join(res['missing'])}"
                     if res["missing"] else "") + ".")
            _lens_autopush(db, manuscript, args, res["file"], len(res["staged"]))
            return
        try:
            payload = lenses.assemble_repair(db, manuscript, target)
        except (LookupError, ValueError) as err:
            raise SystemExit(f"error: {err}")
        reply = None
        if args.reply:
            from pathlib import Path as _P
            try:
                reply = _json.loads(_P(args.reply).read_text(encoding="utf-8"))
            except (OSError, _json.JSONDecodeError) as err:
                raise SystemExit(f"error: cannot read --reply: {err}")
        elif args.native:
            llm = api.LLMClient(_load_config(args))
            if not llm.enabled:
                raise SystemExit("--native needs the LLM enabled in config")
            reply = llm.complete_json(payload.system, payload.user)
            if not isinstance(reply, dict):
                raise SystemExit("the model returned no usable reply; re-run, "
                                 "or answer the printed payload and --reply")
        else:
            text = payload.render()
            if args.out:
                from pathlib import Path as _P
                _P(args.out).write_text(text, encoding="utf-8")
                print(f"repair payload → {args.out} ({len(text):,} chars; "
                      f"{len(payload.tags)} judgment(s)). No model call was "
                      f"made; answer it and 'lens repair {payload.file} "
                      "--reply <json>', or re-run with --native.")
            else:
                print(text)
            return
        res = lenses.record_repairs(db, manuscript, session, target, reply,
                                    payload=payload)
        print(f"Repair of {res['file']}: {len(res['staged'])} rewrite(s) staged, "
              f"{len(res['questions'])} question(s), {len(res['intents'])} "
              f"intent(s) filed, {len(res['refused'])} refused.")
        for q in res["questions"]:
            print(ui.yellow(f"  question on {q['id']} (unit {q['n']}): {q['text']}"))
        for i in res["intents"]:
            print(f"  intent {i['intent'][:9]} ← {i['id']}: {i['text']}")
        for r in res["refused"]:
            print(ui.yellow(f"  refused {r['id']}: {r['reason']}"))
        if args.native:
            print(llm.stats_line() or "")
        _lens_autopush(db, manuscript, args, res["file"], len(res["staged"]))
        return
    if args.action == "status":
        target = args.name or args.file
        if not target:
            raise SystemExit("usage: authorlm lens status <essay.md>")
        try:
            batches = lenses.status(db, manuscript, target)
        except LookupError as err:
            raise SystemExit(f"error: {err}")
        if not batches:
            print(f"No lens findings on {target}.")
            return
        for b in batches:
            stale = (" STALE: " + ", ".join(sorted(set(b["stale"])))
                     if b["stale"] else "")
            states = ", ".join(f"{k} {v}" for k, v in sorted(b["states"].items()))
            print(f"{b['lens']} ({b['source']}) {str(b['created_at'])[:16]} — "
                  f"{b['count']} finding(s): {states}"
                  + (f"; {b['unverified']} target quote(s) unverified"
                     if b["unverified"] else "") + ui.yellow(stale))
            for rule, n in sorted(b["rules"].items(), key=lambda kv: -kv[1]):
                print(ui.dim(f"    {n:2d} × {rule}"))
        return
    if args.action == "sweep":
        target = args.name or args.file
        if not target:
            raise SystemExit("usage: authorlm lens sweep <essay.md> "
                             "[--native] [--only a,b] [--skip a,b] [--out DIR]")
        only = [x.strip() for x in (args.only or "").split(",") if x.strip()]
        skip = [x.strip() for x in (args.skip or "").split(",") if x.strip()]
        try:
            names = lenses.sweep_order(manuscript, only or None, skip or None)
        except LookupError as err:
            raise SystemExit(f"error: {err}")
        if args.native and not args.no_push and not args.supersede:
            out_now = lenses.forms_out(db, manuscript, target)
            if out_now:
                raise SystemExit(
                    f"{out_now} lens form(s) are already out in {target}'s "
                    f"tab. Rule on them there and 'lens resolve {target}' "
                    f"first, or re-run with --supersede to withdraw them, "
                    f"before spending eight model calls on a sweep whose "
                    f"forms could not land.")
        from pathlib import Path as _P
        from .revisions import read_manuscript_files as _rmf
        files = _rmf(_P(manuscript["path"]))
        cache: dict = {}
        session, _ = api.ensure_session(db, manuscript)
        llm = api.LLMClient(_load_config(args)) if args.native else None
        if llm is not None and not llm.enabled:
            raise SystemExit("--native needs the LLM enabled in config")
        batch_ids = []
        staged_total = 0
        outdir = _P(args.out) if args.out else None
        if outdir:
            outdir.mkdir(parents=True, exist_ok=True)
        for name in names:
            try:
                payload = lenses.assemble(db, manuscript, name, target,
                                          files=files, inputs_cache=cache)
            except (LookupError, ValueError) as err:
                print(ui.yellow(f"{name}: skipped — {err}"))
                continue
            if llm is None:
                text = payload.render(name)
                if outdir:
                    dest = outdir / f"{name}.payload"
                    dest.write_text(text, encoding="utf-8")
                    print(f"{name}: payload → {dest} ({len(text):,} chars)")
                else:
                    print(text)
                continue
            result = lenses.run_lens(db, manuscript, session, name,
                                     payload.file, llm, payload=payload)
            batch_ids.append(result["batch_id"])
            staged_total += len(result["edits_staged"])
            print(f"{name}: {len(result['findings'])} finding(s)"
                  + (f", {len(result['edits_staged'])} edit(s) staged"
                     if result["edits_staged"] else "")
                  + (f", {result['dropped_ungrounded']} ungrounded dropped"
                     if result["dropped_ungrounded"] else "")
                  + (f", {len(result['refused_targets'])} refused (target "
                     "not declared)" if result["refused_targets"] else ""))
            for i, row in enumerate(result["findings"], start=1):
                print(f"  [{i}] {row['suggestion']}")
        if llm is not None:
            linked = lenses.link_overlaps(db, batch_ids)
            print(ui.dim(f"{linked} finding(s) cross-linked across lenses. "
                         f"Verdicts: 'lens findings {target}' then lens review "
                         f"<gd-id> --accept|--reject [--explain]."))
            print(llm.stats_line() or "")
            _lens_autopush(db, manuscript, args, target, staged_total)
        else:
            print(ui.dim("No model call was made (the default). Answer each "
                         "payload's contract and 'lens register <name> "
                         f"{target} --reply <json>'; or re-run with --native."))
        return

    if args.action == "review":
        decisions = [d for d, on in (("accepted", args.accept),
                                     ("rejected", args.reject),
                                     ("modified", args.modify),
                                     ("deferred", args.defer)) if on]
        by_id = bool(args.name) and args.name.startswith("gd-")
        if not args.name or not (args.name.isdigit() or by_id) \
                or len(decisions) != 1:
            raise SystemExit("usage: authorlm lens review <n | gd-id> "
                             "--accept|--reject|--modify|--defer "
                             '[--explain "why"]  (ids from lens findings)')
        session, _ = api.ensure_session(db, manuscript)
        result = api.review(db, manuscript, session,
                            args.name if by_id else int(args.name),
                            decisions[0], args.explain,
                            llm=api.LLMClient(_load_config(args)),
                            kinds=(lenses.LENS_KIND,))
        print(f"Recorded: [{args.name}] {decisions[0]}"
              + (f" — “{args.explain}”" if args.explain else ""))
        if result.get("seeded_belief"):
            print(ui.dim("Your explanation seeded a candidate belief: "
                         f"\"{result['seeded_belief']['statement']}\""))
        # §7.2's ruling: finding verdicts and staged edits are
        # INDEPENDENT — accepting the finding does not settle its edit,
        # and the author is told so rather than left to infer it.
        gmeta = _json.loads(result["guidance"].get("metadata") or "{}")
        tid = gmeta.get("edit_thread")
        if tid:
            thread = db.one("SELECT * FROM doc_threads WHERE id = ?",
                            (tid,))
            if thread and thread["state"] in ("proposed", "accepted",
                                              "written"):
                where = ("in the Doc tab" if thread["state"] == "written"
                         else "staged")
                print(ui.dim(
                    f"This finding's edit is still open ({where}) — the "
                    f"verdict on the finding does not settle it. "
                    f"'lens push {gmeta.get('file', '<file>')}' / "
                    f"'lens resolve {gmeta.get('file', '<file>')}' run "
                    f"the edit's own road."))
        return

    if args.action in ("push", "resolve"):
        target = args.name or args.file
        if not target:
            raise SystemExit(f"usage: authorlm lens {args.action} "
                             f"<essay.md>")
        config = _load_config(args)

        def _doc_bridge():
            from . import gdocs as _gd

            return (_gd.get_service(config, args.workspace,
                                    interactive=True),
                    _gd.get_docs_service(config, args.workspace,
                                         interactive=True))

        try:
            if args.action == "push":
                result = api.lens_push(db, manuscript, config, target,
                                       services=_doc_bridge,
                                       supersede=args.supersede)
                _print_filter_warnings(result["warnings"])
                print(ui.green(
                    f"Pushed {result['written']} lens form(s) into "
                    f"{result['file']}'s tab → {result['url']}"))
                for t, why in result["failed"]:
                    print(ui.yellow(f"  failed to land: «"
                                    f"{gdocs_clamp(t['proposed_old'])}» "
                                    f"— {why}"))
                print(ui.dim("Resolve them in the Doc, then "
                             f"'lens resolve {result['file']}'."))
            else:
                result = api.lens_resolve(db, manuscript, config, target,
                                         services=_doc_bridge)
                _print_filter_warnings(result["warnings"])
                print(ui.green(
                    f"Finalized: {result['accepted']} lens change(s) made "
                    f"final in {result['file']}"
                    + (f", {len(result['diffs'])} of them in your wording "
                       f"rather than mine" if result["diffs"] else "")
                    + (f"; {result['declined']} declined"
                       if result["declined"] else "") + "."))
                for d in result["diffs"]:
                    print(ui.dim(f"  «{gdocs_clamp(d['proposal'])}» → "
                                 f"«{gdocs_clamp(d['final'])}»"))
                fv = result.get("findings") or {}
                if fv.get("accepted") or fv.get("rejected"):
                    print(ui.dim(f"Findings ruled by the tab: {fv['accepted']} "
                                 f"accepted, {fv['rejected']} rejected. Accepted "
                                 f"judgments now stand as [Judgment: …] tags — "
                                 f"'lens repair {result['file']}' acts on them."))
                if result["summary"]["rebuilt"]:
                    print("summary rebuilt; downstream marked "
                          "upstream_stale")
                if result.get("pattern_candidate"):
                    print(ui.dim("Pattern candidate from your post-edits: "
                                 f"{result['pattern_candidate'][:80]}…"))
                if result["tab_still_marked"]:
                    print(ui.dim(
                        "The Doc tab keeps its struck-and-green marks "
                        f"until your next 'doc push {result['file']}'."))
        except (LookupError, ValueError) as err:
            raise SystemExit(f"error: {err}")
        return

    if not args.name or not args.file:
        raise SystemExit(f"usage: authorlm lens {args.action} <name> <file>")
    session, _ = api.ensure_session(db, manuscript)
    if args.action == "run":
        try:
            payload = lenses.assemble(db, manuscript, args.name, args.file)
        except (LookupError, ValueError) as err:
            raise SystemExit(f"error: {err}")
        if not args.native:
            # THE FLAG POLARITY (design §6, the ruled pattern): no call
            # without --native. The payload is printed for the chat agent
            # or a subagent to answer; 'lens register' stores the answer.
            text = payload.render(args.name)
            if args.out:
                from pathlib import Path as _P
                _P(args.out).write_text(text, encoding="utf-8")
                print(f"payload → {args.out} ({len(text):,} chars; blocks "
                      + ", ".join(f"{k} {len(v):,}" for k, v in payload.blocks)
                      + "). No model call was made; answer block S's contract "
                      f"and 'lens register {args.name} {payload.file} --reply "
                      "<json>', or re-run with --native.")
            else:
                print(text)
            return
        llm = api.LLMClient(_load_config(args))
        if not llm.enabled:
            raise SystemExit("--native needs the LLM enabled — without it, "
                             "'lens run' prints the payload for an external "
                             "(Claude) pass, then 'lens register'")
        try:
            result = lenses.run_lens(db, manuscript, session, args.name,
                                     payload.file, llm, payload=payload)
        except (LookupError, ValueError) as err:
            raise SystemExit(f"error: {err}")
        line = llm.stats_line()
    else:  # register — the door for externally produced findings
        if args.reply:
            from pathlib import Path as _P
            try:
                raw = _P(args.reply).read_text(encoding="utf-8")
            except OSError as err:
                raise SystemExit(f"error: cannot read --reply: {err}")
        else:
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
        try:
            result = lenses.register_findings(db, manuscript, session,
                                              args.name, args.file,
                                              findings)
        except (LookupError, ValueError) as err:
            raise SystemExit(f"error: {err}")
        line = None
    print(f"Lens '{result['lens']}' on {result['file']}: "
          f"{len(result['findings'])} finding(s)"
          + (f", {result['dropped_ungrounded']} ungrounded dropped"
             if result["dropped_ungrounded"] else "")
          + (f", {len(result['refused_targets'])} refused for naming a "
             "chapter the lens did not declare"
             if result.get("refused_targets") else "") + ".")
    for rt in result.get("refused_targets", []):
        print(ui.yellow(f"  refused: «{rt['quote']}» → {rt['target']}"))
    _lens_autopush(db, manuscript, args, result["file"],
                   len(result.get("edits_staged", [])))
    for i, row in enumerate(result["findings"], start=1):
        meta = _json.loads(row.get("metadata") or "{}")
        tag = " [edit staged]" if meta.get("edit_thread") else ""
        print(f"  [{i}] {row['suggestion']}{tag}")
        print(ui.dim(f"      {row['explanation']}"))
    for refusal in result.get("edits_refused", []):
        print(ui.yellow(f"  edit refused on «{refusal['quote']}» — "
                        f"{refusal['reason']} (the finding itself is "
                        f"kept)"))
    if result.get("edits_staged"):
        print(ui.dim(f"{len(result['edits_staged'])} edit(s) staged — "
                     f"'lens push {result['file']}' writes them into the "
                     f"Doc tab as <<old>>{{{{new}}}} forms; "
                     f"'lens resolve {result['file']}' reads the tab "
                     f"back. Ruling on a finding and settling its edit "
                     f"stay independent."))
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

    if args.action == "check":
        problems = ex.check_manuscript(manuscript)
        if not problems:
            print("Every region tag resolves and every math span "
                  "converts — the book is exportable.")
            return
        for problem in problems:
            print(ui.yellow(problem))
        raise SystemExit(1)

    try:
        chapters = [c for part in (args.chapters or [])
                    for c in part.split(",") if c.strip()]
        result = ex.export_published(db, manuscript, fmt=args.action,
                                     variant=args.variant,
                                     only=chapters or None,
                                     print_ready=args.print_ready,
                                     profile=args.profile)
    except (RuntimeError, LookupError, ValueError) as err:
        raise SystemExit(ui.yellow(f"export failed: {err}"))
    print(f"Wrote {result['markdown']} (variant: {result['variant']}).")
    if args.action == "pdf":
        print(ui.dim(f"  mode: {result['mode']}"))
        if result.get("pages"):
            print(ui.dim(f"  pages: {result['pages']}  "
                         f"geometry: {result.get('geometry', '')}"))
    if chapters:
        print(ui.dim(f"  chapters: {', '.join(result['files'])}"))
    if args.action in result:
        print(f"Wrote {result[args.action]}.")
    for warning in result["warnings"]:
        print(ui.yellow(f"warning: {warning}"))


def _confirm(prompt: str, yes: bool) -> bool:
    if yes:
        return True
    if not sys.stdin.isatty():
        raise SystemExit(ui.yellow(
            f"{prompt} — no terminal to ask on; pass --yes to proceed"))
    answer = input(f"{prompt} [y/N] ").strip().lower()
    return answer in ("y", "yes")


def cmd_audio(args):
    """The audiobook verbs (docs/audiobook-pipeline-design.md §11)."""
    from . import audio

    db = _open_db(args)
    manuscript = _manuscript(db, args)
    rest = list(args.rest or [])
    try:
        if args.action == "init":
            result = audio.init(manuscript)
            print(f"Wrote {result['config']} and {result['cast']}.")
            print(ui.dim("  Both are yours now: AuthorLM reads them and never "
                         "rewrites them (audio cast set writes one row)."))
            return

        if args.action == "export":
            chapters = [c for part in (args.chapters or [])
                        for c in part.split(",") if c.strip()]
            result = audio.export(db, manuscript, only=chapters or None,
                                  resolve_dictionary=not args.offline)
            for path in result["written"]:
                print(f"Wrote {path}.")
            if result["unchanged"]:
                print(ui.dim(f"  unchanged: {len(result['unchanged'])} file(s)"))
            print(ui.dim(f"  {len(result['chapters'])} chapter(s), "
                         f"{result['sections']} speech section(s), "
                         f"{result['characters']:,} characters"))
            for warning in result["warnings"]:
                print(ui.yellow(f"warning: {warning}"))
            return

        if args.action == "check":
            report = audio.check(db, manuscript)
            for problem in report["problems"]:
                print(ui.yellow(f"✗ {problem}"))
            for warning in report["warnings"]:
                print(ui.dim(f"  ! {warning}"))
            if report["state"]:
                print(f"Generated at {report.get('quality', '')} "
                      "(audiostation's state, read-only):")
                for stem, s in report["state"].items():
                    mark = "stitched" if s["stitched"] else "not stitched"
                    print(f"  {stem}: {s['generated']}/{s['total']} sections, "
                          f"{mark}")
            if report["ready"]:
                print(ui.green("The audiobook's sources are ready. "
                               "audiostation's ACX audit is the last gate."))
            else:
                raise SystemExit(1)

        if args.action == "voices":
            client = audio.ElevenLabs(audio.api_key())
            result = audio.voices_gallery(manuscript, client,
                                          search=args.search or "",
                                          library=args.library,
                                          limit=args.limit)
            for v in result["voices"]:
                labels = ", ".join(f"{k}: {val}" for k, val in
                                   (v.get("labels") or {}).items() if val)
                print(f"  {v['name']}  {v['voice_id']}"
                      + (f"  — {labels}" if labels else ""))
            print(f"{result['clips']} preview clip(s) → {result['page']}"
                  "  ← publish this as an artifact")
            return

        if args.action == "audition":
            if not args.cast:
                raise SystemExit("usage: audio audition --cast KEY,KEY "
                                 "(--text … | --file F --paragraphs N-M)")
            if args.text:
                text = args.text
            elif args.file and args.paragraphs:
                text = audio.audition_text(manuscript, args.file,
                                           args.paragraphs)
            else:
                raise SystemExit("give --text, or --file F --paragraphs N-M")
            candidates = [c.strip() for c in args.cast.split(",") if c.strip()]
            chars = len(audio.normalize(text)) * len(candidates)
            if not _confirm(f"This audition spends about {chars:,} "
                            f"ElevenLabs characters ({len(candidates)} "
                            f"voice(s) × {len(audio.normalize(text)):,}). "
                            "Proceed?", args.yes):
                print("Nothing rendered.")
                return
            overrides = {"stability": args.stability,
                         "similarity": args.similarity, "speed": args.speed,
                         "model": args.model}
            client = audio.ElevenLabs(audio.api_key())
            result = audio.audition(manuscript, client, candidates, text,
                                    overrides=overrides,
                                    quality=args.quality,
                                    label=args.label or "")
            for clip in result["clips"]:
                print(f"  {clip['cast']}: {clip['path']}")
            print(f"{result['characters']:,} characters → {result['page']}"
                  "  ← publish this as an artifact")
            return

        if args.action == "cast":
            if len(rest) < 2 or rest[0] != "set":
                raise SystemExit("usage: audio cast set <key> --voice-id ID "
                                 "[--voice NAME --model M --stability S "
                                 "--similarity S --speed S --note N]")
            key = rest[1]
            if not args.voice_id:
                raise SystemExit("audio cast set needs --voice-id")
            root = audio.audio_dir(manuscript)
            path = root / audio.CAST_FILENAME
            text = path.read_text(encoding="utf-8") if path.exists() else ""
            existing, _w = audio.parse_cast(text)
            row = next((r for r in existing if r["key"] == key), {})
            new = {"key": key,
                   "voice": args.voice if args.voice is not None else row.get("voice", ""),
                   "voice_id": args.voice_id,
                   "model": args.model if args.model is not None else row.get("model", ""),
                   "stability": (f"{args.stability:g}" if args.stability is not None
                                 else row.get("stability", "")),
                   "similarity": (f"{args.similarity:g}" if args.similarity is not None
                                  else row.get("similarity", "")),
                   "speed": (f"{args.speed:g}" if args.speed is not None
                             else row.get("speed", "")),
                   "note": args.note if args.note is not None else row.get("note", "")}
            root.mkdir(parents=True, exist_ok=True)
            path.write_text(audio.set_cast_row(text, new), encoding="utf-8")
            print(f"{'Updated' if row else 'Added'} cast '{key}' in "
                  f"{path.relative_to(Path(manuscript['path']))}.")
            return

        if args.action == "say":
            if not rest:
                raise SystemExit("usage: audio say <term> [--say S[,S…]] "
                                 "[--cast KEY] [--file F] [--dictionary] "
                                 "[--settle] [--no-play]")
            term = " ".join(rest)
            says = [s.strip() for s in (args.say or "").split(",") if s.strip()]
            client = audio.ElevenLabs(audio.api_key())
            result = audio.say(manuscript, client, term, says=says or None,
                               cast_key=args.cast, file=args.file,
                               use_dictionary=args.dictionary,
                               quality=args.quality)
            ctx = result["context"]
            print(f"{term} is said by {ctx['cast']} in {ctx['file']}: "
                  f"«{ctx['sentence'][:120]}»")
            for clip in result["clips"]:
                print(f"  {clip['say'] or '(dictionary)'}: {clip['path']}")
                if not args.no_play and sys.platform == "darwin":
                    import subprocess
                    subprocess.run(["afplay", clip["path"]], check=False)
            print(f"{result['characters']} characters → {result['page']}")
            if args.settle:
                if len(says) != 1:
                    raise SystemExit("--settle needs exactly one --say")
                done = audio.settle_say(manuscript, term, says[0], args.note)
                print(f"{'Added' if done['added'] else 'Updated'} "
                      f"'{done['term']}' → {done['say']} in "
                      f"{Path(done['path']).name}. Next: 'audio dictionary "
                      f"push', then 'audio export'.")
            elif says:
                print(ui.dim(f"  to keep one: audio say {term} --say \"…\" "
                             "--settle   (writes the pronunciations.md row)"))
            return

        if args.action == "dictionary":
            if rest[:1] != ["push"]:
                raise SystemExit("usage: audio dictionary push [--yes]")
            client = audio.ElevenLabs(audio.api_key())
            plan = audio.dictionary_plan(manuscript, client)
            for line in audio.describe_plan(plan):
                print(line)
            if not (plan["create"] or plan["add"] or plan["change"]):
                print(ui.green("Nothing to push."))
                return
            if not _confirm("Apply?", args.yes):
                print("Nothing pushed.")
                return
            result = audio.dictionary_apply(plan, client)
            print(f"Dictionary {result['id']} at version "
                  f"{result['version_id']}: {result['added']} added, "
                  f"{result['changed']} changed"
                  + (" (created)" if result["created"] else "") + ".")
            print(ui.dim("  Run 'audio export' to write the new version "
                         "into audiobook.json."))
            return
    except (audio.AudioError, LookupError, RuntimeError) as err:
        raise SystemExit(ui.yellow(f"audio {args.action}: {err}"))


def cmd_audiostation(args):
    """Open audiostation on the manuscript's `_audio/` folder."""
    import subprocess

    from . import audio

    db = _open_db(args)
    manuscript = _manuscript(db, args)
    try:
        launch = audio.audiostation_launch(manuscript)
    except audio.AudioError as err:
        raise SystemExit(ui.yellow(f"audiostation: {err}"))
    if args.dry_run:
        print(" ".join(launch["argv"]) + (f"   (in {launch['cwd']})"
                                          if launch["cwd"] else ""))
        return
    if launch["mode"] == "dev":
        print(ui.dim("No built audiostation found — starting the dev server "
                     "(the first run compiles for a few minutes)."))
    try:
        subprocess.Popen(launch["argv"], cwd=launch["cwd"],
                         stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL if launch["mode"] != "dev"
                         else None,
                         stderr=subprocess.DEVNULL if launch["mode"] != "dev"
                         else None,
                         start_new_session=True)
    except OSError as err:
        raise SystemExit(ui.yellow(f"audiostation: could not start "
                                   f"{launch['argv'][0]}: {err}"))
    print(f"audiostation ({launch['mode']}) → {launch['dir']}")


# Verbs that send an LLM prompt announce it in --help and point at the
# registry, so the words shaping the model are always one command away.
#
# Module level so the suite can iterate the REAL set. It lived inside the
# parser builder, and the test that asserts the note is present listed
# five of these by hand — so a new LLM verb was unchecked by
# construction.
LLM_VERBS = {"init", "extract", "collect", "intent", "guide", "review",
             "analyze", "lens", "sweep", "illus", "summarize", "doc",
             "belief", "critique", "triage-app", "workbench",
             # `filter run --native` is the filter pass's one call, and
             # it is off by default — the verb still announces the prompt
             # it would send, because the CHAT path drafts under exactly
             # those rules.
             "filter",
             # `interlocutor run` makes no call either — a subagent reads
             # the payload — and announces the prompt for the same reason.
             "interlocutor",
             # `write draft` is the write path's one LLM call.
             "write"}


def cmd_interlocutor(args):
    from pathlib import Path as _Path

    from . import interlocutor as il

    db = _open_db(args)
    manuscript = _manuscript(db, args)

    try:
        if args.action == "add":
            text = _stdin_text()
            if not args.name or not text:
                raise SystemExit("usage: authorlm interlocutor add <name>  "
                                 "(the artifact — front matter and prose — "
                                 "on stdin)")
            path, meta = il.add_interlocutor(manuscript, args.name, text)
            print(f"Interlocutor '{args.name}' ratified → {path}")
            print(ui.dim(f"  {len(meta['terms'])} term(s); engaged: "
                         f"{', '.join(meta['engaged']) or 'none'}; position: "
                         f"{', '.join(meta['position']) or 'none'}"))
            return

        if args.action == "show":
            if not args.name:
                raise SystemExit("usage: authorlm interlocutor show <name>")
            path = il._path(manuscript, args.name)
            if not path.is_file():
                raise SystemExit(f"error: no interlocutor '{args.name}' "
                                 "('interlocutor list' shows them).")
            print(ui.bold(f"Interlocutor '{args.name}' — {path}"))
            print(path.read_text(encoding="utf-8").rstrip())
            return

        if args.action == "list":
            rows = il.list_interlocutors(manuscript)
            if not rows:
                print("No interlocutors defined. Create one: authorlm "
                      "interlocutor add <name>  (artifact on stdin)")
            for row in rows:
                extra = []
                if row["engaged"]:
                    extra.append(f"engaged: {', '.join(row['engaged'])}")
                if row["position"]:
                    extra.append(f"position: {', '.join(row['position'])}")
                tail = f" — {'; '.join(extra)}" if extra else ""
                print(f"  {row['name']}: {row['summary']} "
                      f"({row['terms']} terms){tail}")
            return

        if args.action == "draft":
            if not args.name or not args.out:
                raise SystemExit("usage: authorlm interlocutor draft <name> "
                                 "--engaged <essay> --out <payload>")
            result = il.build_draft_payload(db, manuscript, args.name,
                                            args.engaged, _Path(args.out))
            print(f"Draft payload for '{args.name}' → {result['out']} "
                  f"({result['size']:,} chars). No model call.")
            print(ui.dim(f"A subagent writes the artifact to "
                         f"{result['artifact_path']}; the author reads it; "
                         f"then 'interlocutor add {args.name}' with it on "
                         "stdin."))
            return

        if args.action == "run":
            if not args.name or not args.out:
                raise SystemExit("usage: authorlm interlocutor run <name> "
                                 "[--file <chapter>]... [--engaged <essay>]"
                                 "... [--position <essay>]... --out <payload>")
            result = il.build_payload(db, manuscript, args.name,
                                      args.file or None, args.engaged,
                                      args.position, _Path(args.out),
                                      max_terms=args.max_terms,
                                      max_per_term=args.max_per_term,
                                      seed=args.seed)
            print(f"Payload for '{args.name}' → {result['out']} "
                  f"({result['size']:,} chars; scope: {result['scope']}; "
                  f"{len(result['all_hits'])} unit(s) mention a term, "
                  f"{len(result['hits'])} shown — "
                  f"{len(result['sampled_terms'])} of "
                  f"{len(result['all_terms'])} term(s), seed "
                  f"{result['seed']}). No model call.")
            print(ui.dim(f"  carried whole — position: "
                         f"{', '.join(result['position']) or 'none'}; "
                         f"engaged: {', '.join(result['engaged']) or 'none'}"))
            print(ui.dim(f"A subagent writes {result['report_path']} and "
                         f"{result['manifest_path']}; then 'interlocutor "
                         f"import {args.name} {result['out']}'."))
            return

        # import
        if not args.name or not args.target:
            raise SystemExit("usage: authorlm interlocutor import <name> "
                             "<payload>")
        result = il.import_run(db, manuscript, args.name, _Path(args.target))
        v = result["verdicts"]
        kept_m = sum(1 for f in result["kept"]
                     if f["kind"] == "misattribution")
        print(f"Interlocutor '{args.name}': {sum(v.values())} objection(s) "
              f"kept (addressed {v['addressed']}, partial {v['partial']}, "
              f"unaddressed {v['unaddressed']}), {kept_m} "
              f"misattribution(s); {len(result['dropped'])} finding(s) and "
              f"{len(result['items_dropped'])} manifest item(s) dropped at "
              "the gate.")
        for d in result["dropped"]:
            print(ui.dim(f"  dropped {d['id']} {d['title']}: {d['reason']}"))
        for d in result["items_dropped"]:
            print(ui.dim(f"  dropped item {d['unit'][:40]!r}: {d['reason']}"))
        imp = result["imported"]
        print(f"Report → {result['report']}\nManifest → {result['manifest']}")
        print(f"Imported {imp['intents']} proposed intent(s) "
              f"({imp['skipped']} already present, skipped).")
        for dup in imp.get("duplicates", []):
            print(ui.dim(f"  duplicate: {dup['unit']} restates "
                         f"{dup['existing_id']} ({dup['status']}, from "
                         f"'{dup['source']}') — skipped, verdict stands"))
        for err in imp.get("errors", []):
            print(ui.yellow(f"warning: {err}"))
        if imp["intents"]:
            print(ui.dim("Nothing is law yet — triage with 'critique triage "
                         f"--scope <essay>' or list_critique_items."))
    except (il.InterlocutorError, LookupError) as err:
        raise SystemExit(f"error: {err}")


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


def _print_brief(brief: str | None) -> None:
    if not brief:
        return
    print()
    print(ui.bold("BRIEF — the author's, verbatim:"))
    print(brief)


def _digest_counts(digest: dict) -> str:
    return (f"{len(digest.get('points', []))} point(s), "
            f"{len(digest.get('examples', []))} example(s), "
            f"{len(digest.get('references', []))} reference(s), "
            f"{len(digest.get('inconsistencies', []))} inconsistency(ies)")


def _print_accounting(accounting: dict | None) -> None:
    """The running removal tally (design §13.2). Yellow whenever anything
    is unaccounted, on every surface that shows it — the report at
    `write complete` must never be the first time the author sees it."""
    if not accounting:
        return
    unaccounted = accounting["unaccounted"]
    line = (f"Accounting: {len(accounting['kept'])} kept, "
            f"{len(accounting['removed'])} removed, "
            f"{len(unaccounted)} UNACCOUNTED"
            + (f" ({', '.join(unaccounted)})" if unaccounted else "") + ".")
    print(ui.yellow(line) if unaccounted else line)


def _print_marker_warning(result: dict, file: str) -> None:
    """The mid-rewrite marker survived inside text the author has edited.
    Reported, never repaired: it is their file and their words now."""
    from . import api

    if not result.get("marker_present"):
        return
    print(ui.yellow(
        f"{file} still contains the mid-rewrite marker "
        f"'{api.MARKER}' inside text you have edited. It is never part of "
        "a finished essay — delete that line before this essay is "
        "published (nothing here will remove it for you)."))


def _print_drafting_context(text: str) -> None:
    """The L1 book-frame (summaries.drafting_context). Printed verbatim —
    it is the drafting payload, and the skill reads it out of this
    output. Every line the reader must not skim past (a stale, missing
    or deprecated summary; a summary that quietly dropped a paragraph)
    arrives already prefixed by summaries.WARN_PREFIX, so one branch
    colours them all."""
    from . import summaries as sums

    print()
    for line in text.splitlines():
        if line.startswith(sums.WARN_PREFIX):
            print(ui.yellow(line))
        elif line.startswith(("DRAFTING CONTEXT", "BEFORE —", "AFTER —")):
            print(ui.bold(line))
        else:
            print(line)
    print()


def _print_drafting_models(models: list) -> None:
    """What drafted this writeup. One name is the ordinary case; two or
    more is the voice seam §8 warns about, and it stays visible on every
    resume and at completion rather than only in the scrollback of the
    beat where it happened."""
    if not models:
        return
    if len(models) == 1:
        print(ui.dim(f"Drafted by {models[0]}."))
        return
    print(ui.yellow("Drafted by MORE THAN ONE model — the voice has a seam: "
                    + ", ".join(models) + "."))


def _print_model_change(info: dict) -> None:
    """One model drafts one writeup (design §8). It warns; it never
    blocks — the author may have a reason (an outage, a deliberate
    experiment), and an unblockable truth gets said loudly rather than
    enforced quietly."""
    if not info.get("model_changed_from"):
        return
    done = info.get("beats_drafted") or 0
    print(ui.yellow(
        f"!! DRAFTING MODEL CHANGED — {done} beat(s) of this writeup were "
        f"drafted by\n"
        f"!!   {info['model_changed_from']}, and this beat will be drafted "
        f"by\n"
        f"!!   {info['model']}. Two costs: the prompt cache is model-scoped, "
        f"so the\n"
        f"!!   stable layers are re-billed at write price from here on; and "
        f"the essay's\n"
        f"!!   voice will have a seam at beat n={info['beat']['n']}. Finish "
        f"the writeup on one\n"
        f"!!   model, or accept the seam deliberately."))


def _print_draft_usage(usage: dict) -> None:
    if usage.get("line"):
        print(ui.dim(usage["line"]))
    if usage.get("cache_cold"):
        print(ui.yellow(
            "!! cache: 0 tokens read on this beat, on a model that supports "
            "prompt\n"
            "!! caching. The stable layers (STYLE LAW, DRAFTING CONTEXT, "
            "PLAN, CONCEPTS)\n"
            "!! are being re-billed every beat — something in them changed "
            "since the last\n"
            "!! draft. Compare 'write draft --dry-run' block hashes across "
            "two beats to find it."))


def _write_draft(db, manuscript, config, prefix, dry_run: bool,
                 out: str | None = None) -> None:
    """`write draft` — the beat-drafting verb's whole surface."""
    from .llm import DraftError

    def announce(info: dict) -> None:
        _print_beat_spec(info["beat"])
        print(ui.dim(f"Drafting beat n={info['beat']['n']} on "
                     f"{info['model']} — this can take a minute…"))
        _print_model_change(info)

    try:
        result = api.write_draft(db, manuscript, config, prefix=prefix,
                                 dry_run=dry_run, on_start=announce)
    except DraftError as err:
        # Nothing was registered: write_propose is the last statement of
        # the success path, and every failure raises before it.
        sys.exit(f"{err}")
    if dry_run:
        payload = result["payload"]
        _print_beat_spec(result["beat"])
        target = result["model"] or (
            "no [writing] model configured — hand this payload to a "
            "drafter subagent with an empty context, then 'write critique' "
            "and 'write propose --critique …'")
        print(ui.dim(f"Payload for {target} — no call made."))
        # Provenance, on the dry run too: the conversational drafting flow
        # drafts under this prompt's rules, so it has to be named where the
        # payload is printed and not only after a billed call.
        print(ui.dim(f"Prompt: {result['prompt_location']}"))
        if out:
            # The clean-context road: the payload goes to a file that a
            # drafter with an empty context reads whole. The terminal gets
            # the hashes only, so nothing of the payload lands in the
            # orchestrating conversation.
            body = "\n\n".join(f"───── block {name}\n{text}"
                                for name, text in payload.blocks)
            Path(out).write_text(body + "\n", encoding="utf-8")
            for name, text in payload.blocks:
                print(ui.dim(f"block {name} — {len(text):,} chars — "
                             f"sha256 {payload.hashes[name][:12]}"))
            print(f"Payload written to {out} ({sum(payload.sizes.values()):,} "
                  f"chars). Assembly recorded for beat n={result['beat']['n']}.")
            return
        for name, text in payload.blocks:
            print()
            print(ui.bold(f"───── block {name} — {len(text):,} chars — "
                          f"sha256 {payload.hashes[name]}"))
            print(text)
        print()
        print(ui.dim("Identical stored state gives byte-identical blocks S "
                     "and A. A hash that moved between two beats IS the "
                     "cache invalidator."))
        return
    if result["blocked"]:
        print(ui.yellow(f"BLOCKED — {result['reason']}"))
        if result["question"]:
            print(ui.yellow(f"QUESTION — {result['question']}"))
        _print_draft_usage(result["usage"])
        sys.exit("nothing was registered and the cursor did not move — put "
                 "the question to the author. The answer usually becomes "
                 "'write plan --replace' on this beat.")
    print()
    print(ui.bold("WHY"))
    print(result["why"])
    print()
    print(ui.bold("SELF-CHECK"))
    print(result["self_check"])
    print()
    print(ui.bold("DRAFT"))
    print(result["draft"])
    print()
    print(ui.dim(f"Prompt: {result['prompt_location']}"))
    _print_draft_usage(result["usage"])
    print(f"Draft registered [{result['guidance_id'][:11]}] — "
          "author verdict: accept / accept with reworded stdin / "
          "reject --reason.")


def cmd_write(args):
    db = _open_db(args)
    manuscript = _manuscript(db, args)
    config = _load_config(args)
    prefix = getattr(args, "writeup", None)
    try:
        if args.action == "start":
            if not args.params:
                sys.exit("usage: write start <file> [--intent <id>]")
            result = api.write_start(db, manuscript, config,
                                     args.params[0], args.intent,
                                     after=args.after, brief=_stdin_text(),
                                     new=args.new, style=args.style)
            w = result["writeup"]
            print(f"Writeup [{w['id'][:11]}] on {w['file']}.")
            if result["created"]:
                print(f"Created {w['file']} (empty); style guide "
                      f"'{result['style']}' attached.")
                print(f"Pinned v{result['source_version_no']} as the "
                      f"pre-writeup state ({result['source_chars']} chars) — "
                      f"abandon deletes {w['file']}.")
            else:
                print(f"Pinned v{result['source_version_no']} as raw material "
                      f"({result['source_chars']} chars); file truncated.")
            print()
            _print_intent_set(
                result,
                "Intents named" if result["intents"].get("manual")
                else "Intents in scope")
            if result["intents"].get("manual"):
                if result["outside_scope"]:
                    print(ui.dim(
                        "  note: an intent above is scoped outside this "
                        "essay — you reached for it explicitly, which is "
                        "yours to do."))
                print(ui.dim("  Derivation was skipped: --intent names the "
                             "set. The first flag is the primary."))
            else:
                print(ui.dim("  Adjust before the plan: write intents "
                             "--add <id> | --remove <id> | --primary <id>"))
            if result["many_manuscript_wide"]:
                print(ui.yellow(
                    f"  ⚠ {result['manuscript_wide']} book-wide intents are "
                    f"in scope and will attach to EVERY writeup. They are "
                    f"unscoped, not book-wide by decision. Run the scope "
                    f"triage — ask the assistant, or: "
                    f"authorlm intent scope --triage"))
            if result["chapter_undecidable"]:
                print(ui.dim(
                    "  note: this file has no toc entry yet, so no "
                    "chapter-scoped intent can be derived for it. Its entry "
                    "lands at 'write complete'; until then name any by hand "
                    "with --intent."))
            for row in result["proposed_in_scope"]:
                print(ui.dim(
                    f"  note: proposed (untriaged) intent [{row['id'][:8]}] "
                    f"also covers this essay — 'critique triage' settles it. "
                    f"It has NOT joined."))
            print()
            _print_brief(result["brief"])
            _print_drafting_context(result["drafting_context"])
            if not result["created"] and result["source_chars"]:
                print("Raw material: write digest prints the pinned original.")
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
            _print_intent_set(result, "Intents")
            for beat in result["plan"][result["cursor"]:]:
                _print_beat_spec(beat, label="  •")
        elif args.action == "intents":
            result = api.write_intents(
                db, manuscript, add=args.add or (),
                remove=args.remove_intent or (), primary=args.primary,
                defer=args.defer, ignore=args.ignore or (),
                reason=args.reason, prefix=prefix)
            for change in result["changed"]:
                print(f"Intent set: {change}.")
            _print_intent_set(result, "Intents")
            if result.get("rebills_prefix"):
                print(ui.yellow(
                    "  Block A changed, so the next beat re-bills the cached "
                    "prefix once. A join is cheapest at a replan, which "
                    "invalidates block A anyway."))
        elif args.action == "status":
            result = api.write_status(db, manuscript, prefix=prefix)
            w = result["writeup"]
            plan = result["plan"]
            print(f"Writeup [{w['id'][:11]}] on {w['file']} ({w['status']}) — "
                  f"beat {min(w['cursor'] + 1, len(plan))}/{len(plan)}."
                  if plan else
                  f"Writeup [{w['id'][:11]}] on {w['file']} ({w['status']}) — no plan yet.")
            _print_intent_set(result, "Intents")
            _print_newly_in_scope(result)
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
            _print_drafting_models(result["drafting_models"])
            _print_brief(result["brief"])
            if result["digest"]:
                print()
                print(f"Digest: {_digest_counts(result['digest'])}.")
            _print_accounting(result["accounting"])
            _print_marker_warning(result, w["file"])
            _print_drafting_context(result["drafting_context"])
        elif args.action == "digest":
            raw = _stdin_text()
            if args.dispositions and args.show:
                sys.exit("write digest: --dispositions and --show are "
                         "different verbs — pick one")
            payload = dispositions = None
            if args.show:
                if raw is not None:
                    sys.exit("write digest --show takes no stdin — it prints "
                             "what is already stored")
            elif raw is not None:
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError as err:
                    sys.exit(f"invalid digest JSON: {err}")
                if args.dispositions:
                    dispositions = parsed
                else:
                    payload = parsed
            elif args.dispositions:
                sys.exit("write digest --dispositions expects a JSON object "
                         'of {"<point id>": {"disposition": …}} on stdin')
            result = api.write_digest(db, manuscript, payload=payload,
                                      dispositions=dispositions,
                                      prefix=prefix, replace=args.replace,
                                      show=args.show)
            if result["mode"] == "source":
                print(ui.bold(f"PINNED SOURCE — {result['file']} "
                              f"(v{result['source_version_no']}, "
                              f"{result['source_chars']} chars)"))
                print()
                print(result["source_text"])
                print()
                print("Next: extract the digest and have the author review "
                      "it, then: write digest (JSON on stdin).")
            elif result["mode"] == "recorded":
                digest = result["digest"]
                verb = "replaced" if result["replaced"] else "recorded"
                print(f"Digest {verb}: {_digest_counts(digest)}.")
                for item in digest["inconsistencies"]:
                    against = (f"vs {item['with']}" if item.get("with")
                               else "internal")
                    print(ui.yellow(f"  {item['id']} · {against} — "
                                    f"{item['note']}"))
                accounted = (result["accounting"]["point_count"]
                             - len(result["accounting"]["unaccounted"]))
                print(f"Accounting: {accounted}/"
                      f"{result['accounting']['point_count']} points "
                      f"dispositioned.")
            elif result["mode"] == "dispositions":
                overwritten = (f"; {len(result['overwritten'])} overwritten"
                               if result["overwritten"] else "")
                print(f"Dispositions recorded: {result['recorded']} "
                      f"({result['kept']} kept, {result['removed']} "
                      f"removed){overwritten}.")
                for change in result["overwritten"]:
                    print(ui.dim(f"  {change} (overwritten)"))
                _print_accounting(result["accounting"])
            else:
                if not result["digest"]:
                    print("No digest recorded for this writeup yet — "
                          "write digest (JSON on stdin).")
                else:
                    print(json.dumps(result["digest"], indent=2))
                _print_accounting(result["accounting"])
        elif args.action == "draft":
            _write_draft(db, manuscript, config, prefix,
                         dry_run=getattr(args, "dry_run", False),
                         out=getattr(args, "out", None))
        elif args.action == "show":
            from . import lint as lint_mod

            result = api.write_show(db, manuscript, prefix=prefix)
            marked = result["marked"]
            omitted = 0
            if getattr(args, "changed", False):
                kept, omitted = lint_mod.changed_only(marked)
                marked = "\n\n".join(kept)
            if getattr(args, "html", False):
                if getattr(args, "changed", False) and not kept:
                    print('<p class="chg">No change against the original in '
                          'this beat.</p>')
                else:
                    print(lint_mod.marked_html(marked))
                    if omitted:
                        print(f'<p class="note">{omitted} unchanged '
                              f'paragraph(s) not shown.</p>')
                return
            _print_beat_spec(result["beat"])
            if getattr(args, "plain", False):
                print(result["draft"])
            elif getattr(args, "changed", False) and not kept:
                print("**No change against the original in this beat.**")
            else:
                print(marked)
                if omitted:
                    print(f"({omitted} unchanged paragraph(s) not shown.)")
            if not getattr(args, "plain", False):
                print()
                print(ui.dim(
                    "Bold = changed against the pinned original; a "
                    "paragraph bold whole is new material. The essay's own "
                    "bolding is not shown in this view."
                    if result["rewrite"] else
                    "Not a rewrite: the whole beat is new material."))
        elif args.action == "landed":
            result = api.write_landed(db, manuscript, prefix=prefix)
            t = result["totals"]
            print(ui.bold(f"{result['file']}: {t['exact']} paragraph(s) landed "
                          f"exactly, {t['changed']} reworded, "
                          f"{t['missing']} MISSING."))
            for beat in result["beats"]:
                flag = ui.yellow if beat["missing"] else ui.dim
                print(flag(f"  beat n={beat['n']}: {beat['exact']} exact, "
                           f"{beat['changed']} reworded, "
                           f"{beat['missing']} missing"))
                for para in beat["paragraphs"]:
                    if para["verdict"] == "missing":
                        print(ui.yellow(f"    MISSING: «{para['head']}…»"))
            if result["extra"]:
                print(ui.dim(f"  {len(result['extra'])} paragraph(s) in the "
                             f"file match no accepted beat — the author's "
                             f"own additions:"))
                for para in result["extra"][:12]:
                    print(ui.dim(f"    + «{para['head']}…»"))
        elif args.action == "critique":
            from . import lint as lint_mod

            text = _stdin_text()
            if not text:
                sys.exit("write critique expects the draft on stdin")
            result = api.write_critique(db, manuscript, text, prefix=prefix)
            report = result["lint"]
            if args.out:
                Path(args.out).write_text(result["payload"] + "\n",
                                          encoding="utf-8")
                print(f"Critic payload written to {args.out} "
                      f"({len(result['payload']):,} chars) for beat "
                      f"n={result['beat']['n']}.")
                print(ui.dim(f"Prompt: {result['prompt_location']}"))
            else:
                print(result["payload"])
            summary = lint_mod.render(report, "LINT (deterministic)")
            if report.errors:
                print(ui.yellow(summary))
                print(ui.yellow(
                    f"{len(report.errors)} lint ERROR(s): write propose will "
                    f"refuse this draft as it stands. Redraft, or "
                    f"--lint-override \"<why>\"."))
            elif report.warnings:
                print(ui.dim(summary))
            else:
                print(ui.dim("Lint: clean."))
        elif args.action == "propose":
            text = _stdin_text()
            critique = None
            if args.critique:
                critique = Path(args.critique).read_text(encoding="utf-8")
            result = api.write_propose(
                db, manuscript, text or "", args.why or "", prefix=prefix,
                critique=critique, no_critic=args.no_critic,
                lint_override=args.lint_override)
            _print_beat_spec(result["beat"])
            gates = result.get("gates") or {}
            if gates:
                print(ui.dim("Gates: assembly fresh; lint "
                             + (", ".join(gates.get("lint") or []) or "clean")
                             + (f" (override: {gates['lint_override']})"
                                if gates.get("lint_override") else "")
                             + f"; critic {gates.get('critic')}."))
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
            if result["review"].get("seeded_belief"):
                print(ui.dim("Seeded candidate belief: "
                             f"{result['review']['seeded_belief']['statement']}"))
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
            _print_marker_warning(result, result["summary_hint"])
            _print_intent_dispositions(result["intent_dispositions"])
            if result["tallies"]:
                print("Verdicts: " + ", ".join(
                    f"{k} {v}" for k, v in sorted(result["tallies"].items())))
            for lesson in result["learnings"]:
                print(ui.dim(f"  learning: {lesson}"))
            _print_drafting_models(result["drafting_models"])
            accounting = result["accounting"]
            if accounting:
                unaccounted = accounting["unaccounted"]
                headline = (f"Removal accounting: {len(accounting['kept'])} "
                            f"point(s) kept, {len(accounting['removed'])} "
                            f"removed, {len(unaccounted)} UNACCOUNTED.")
                print(ui.yellow(headline) if unaccounted else headline)
                if unaccounted:
                    print(ui.yellow(
                        "  UNACCOUNTED — no disposition recorded, and no "
                        "'What Was Removed and Why' entry can be trusted "
                        "while these are open:"))
                    for point_id in unaccounted:
                        claim = accounting["claims"].get(point_id, "")
                        print(ui.yellow(f'    {point_id}  "{claim}"'))
                    print(ui.yellow("  Record them: write digest "
                                    "--dispositions  (JSON on stdin)."))
            # `--after start` is a SENTINEL, not a file: "after start"
            # reads as a file named start, which is the one thing the
            # sentinel exists because there is no file for.
            from . import summaries as _sums

            at_start = result["toc_placement"] == _sums.PLACEMENT_START
            where = ("at the start" if at_start
                     else f"after {result['toc_placement']}")
            if result["toc_registered"]:
                print(f"Registered {result['summary_hint']} in toc.toml "
                      f"{where}.")
            elif result["toc_registered"] is False:
                print(ui.yellow(
                    f"could not place {result['summary_hint']} in toc.toml "
                    f"automatically — add this stanza "
                    + ("at the top, before the first [[chapter]]:" if at_start
                       else f"after the {result['toc_placement']} entry:")))
                print(result["toc_stanza"])
            print(ui.dim(
                f"{result['summary_hint']}'s summary is now stale — run "
                f"'summarize rebuild' before the next writeup or critique "
                f"pass on its neighbours."))
            print("Intent completion is separate: intent complete "
                  f"{result['intent_id'][:11]} (runs episode analysis).")
        elif args.action == "abandon":
            result = api.write_abandon(db, manuscript, config, prefix=prefix)
            if result["deleted"]:
                print(f"Writeup [{result['writeup_id'][:11]}] abandoned; "
                      f"{result['file']} deleted — the writeup created it, "
                      f"so the restore target is nonexistence.")
                if result["had_text"] and result["preserved_in_version"]:
                    print(f"Its text is preserved in "
                          f"v{result['preserved_in_version']} (nothing typed "
                          f"into it was lost).")
            else:
                restored = ("file restored from the pinned source version"
                            if result["restored"] else
                            ui.yellow("source version missing — file NOT restored"))
                print(f"Writeup [{result['writeup_id'][:11]}] abandoned; "
                      f"{restored}.")
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
                # Snapshot before the pull can overwrite anything
                # (BUG-1 / A2): a base-less mapped file's pull is
                # destructive, and this is the recovery point.
                with contextlib.redirect_stdout(io.StringIO()):
                    api.collect(db, manuscript, config, source="pre-doc-pull")
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
    for belief in result["beliefs"]:
        print(
            f"Belief \"{belief['statement']}\" → confidence {belief['confidence']} "
            f"[{belief['status']}] ({belief['supporting']}+ / {belief['contradicting']}-)"
        )
    if result["seeded_belief"]:
        seeded = result["seeded_belief"]
        if seeded.get("kind") == "revival_proposal":
            print(
                f"Your explanation supports the retired belief "
                f"\"{seeded['statement']}\" — a revival proposal was filed "
                f"(see 'proposal list')."
            )
        else:
            print(
                f"Your explanation seeded a candidate belief: \"{seeded['statement']}\" "
                f"(confidence {seeded['confidence']})"
            )
    if args.explain and not result["seeded_belief"]:
        print("Explanation recorded as high-weight evidence.")
    _report_llm(llm)


def cmd_belief(args):
    db = _open_db(args)
    manuscript = _manuscript(db, args)
    if args.action == "show":
        if not args.id:
            sys.exit("usage: authorlm belief show <id-prefix>")
        rows = [dict(r) for r in db.all(
            "SELECT * FROM editorial_beliefs WHERE manuscript_id = ?",
            (manuscript["id"],)) if r["id"].startswith(args.id)
            or args.id in r["id"]]
        if not rows:
            sys.exit(f"error: no belief matching '{args.id}'.")
        for row in rows:
            meta = loads(row.get("metadata"), {}) or {}
            bar = f"{row['supporting']}+ / {row['contradicting']}-"
            print(ui.bold(f"[{row['id']}] \"{row['statement']}\""))
            promotes = bel_mod.validate_min_support(row["source"])
            print(ui.dim(
                f"  {row['status']} · confidence {row['confidence']} · {bar}"
                f" · source {row['source']} · raised {row['created_at'][:19]}"))
            if row["status"] == "candidate":
                need = max(0, promotes - row["supporting"])
                print(ui.dim(f"  needs {need} more supporting observation(s) "
                             f"to act (bar for {row['source']}: {promotes})"))
            elif row["status"] == "validated":
                # Only sources a LoopSpec claims are consulted by the screen;
                # every other validated belief surfaces as guidance instead.
                from . import loop as _loop

                queues = [sp.key for sp in _loop.REGISTRY.values()
                          if sp.source == row["source"]]
                if queues:
                    print(ui.yellow(
                        "  ACTING: screens new proposals in "
                        + ", ".join(queues) + ". Not ratified by you — "
                        f"'belief convert {row['id'][:8]} --aspect <aspect>' "
                        "makes it law."))
                else:
                    print(ui.dim(
                        "  validated: surfaces as a reminder in guidance. It "
                        f"does not screen proposals — no queue reads "
                        f"'{row['source']}' beliefs."))
            if meta.get("scope_kind"):
                print(ui.dim(f"  scope: {meta['scope_kind']} "
                             f"{meta.get('scope_ref', '')}"))
            if meta.get("example"):
                print(f"  e.g. {meta['example'][:300]}")
            if meta.get("original_explanation"):
                print("  distilled from your words:")
                print(ui.dim(f"    “{meta['original_explanation'][:600]}”"))
            for number, question in enumerate(
                    loads(row["outstanding_questions"], []), start=1):
                print(ui.dim(f"  Q{number}: {question}"))
            ev = db.all(
                "SELECT * FROM evidence WHERE supports_belief = ? "
                "ORDER BY created_at DESC LIMIT 8", (row["id"],))
            if ev:
                print("  evidence on record:")
                for e in ev:
                    print(ui.dim(f"    [{e['signal']}] {e['target'][:110]}"))
            folded = db.one(
                "SELECT COUNT(*) AS n FROM knowledge_proposals "
                "WHERE manuscript_id = ? AND state = 'dismissed' "
                "AND json_extract(metadata, '$.law') = ?",
                (manuscript["id"], row["id"]))["n"]
            if folded:
                print(ui.yellow(f"  has folded {folded} proposal(s) — "
                                f"'proposal list --belief {row['id'][:8]}' "
                                "to check its work"))
        return

    if args.action == "list":
        rows = db.all(
            "SELECT * FROM editorial_beliefs WHERE manuscript_id = ? "
            "AND status != 'retired' ORDER BY confidence DESC",
            (manuscript["id"],),
        )
        if not rows:
            print("No editorial beliefs learned yet. They emerge from your reviews.")
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
            print(ui.dim('  → answer: belief answer <id> "your answer" '
                         "[--index N]  (N = the Q number, default 1)"))
    elif args.action == "retire":
        try:
            result = api.retire_belief(db, manuscript, args.id, args.reason)
        except (LookupError, ValueError) as err:
            sys.exit(str(err))
        print(f"Belief retired: \"{result['statement']}\" "
              "(statement stays banned from re-seeding).")
    elif args.action == "demote":
        try:
            result = api.demote_belief(db, manuscript, args.id, args.reason)
        except (LookupError, ValueError) as err:
            sys.exit(str(err))
        print(f"Belief demoted to candidate: \"{result['statement']}\" "
              "(not retired — may re-validate on real evidence).")
    elif args.action == "merge":
        try:
            result = api.merge_beliefs(db, manuscript, args.id, args.answer,
                                        reason=args.reason)
        except (LookupError, ValueError) as err:
            sys.exit(str(err))
        print(f"Merged \"{result['merged']}\" into \"{result['statement']}\" "
              f"— now {result['supporting']}+ / {result['contradicting']}- "
              f"[{result['status']}, confidence {result['confidence']}].")
    elif args.action == "convert":
        try:
            result = api.convert_belief(
                db, manuscript, args.id, args.aspect,
                statement=args.statement, guide=args.guide, file=args.file,
                notes=args.notes, reason=args.reason)
        except (LookupError, ValueError) as err:
            sys.exit(str(err))
        print(f"Belief \"{result['statement']}\" converted to style element "
              f"[{result['style_element'][:8]}] (belief retired).")
    else:  # answer
        belief = _find_by_prefix(db, "editorial_beliefs", args.id, manuscript["id"])
        questions = loads(belief["outstanding_questions"], [])
        if not questions:
            sys.exit("error: that belief has no outstanding questions.")
        index = args.index - 1
        if not 0 <= index < len(questions):
            sys.exit(f"error: question index out of range (1..{len(questions)}).")
        answered = questions.pop(index)
        db.update(
            "editorial_beliefs", belief["id"],
            {"outstanding_questions": json.dumps(questions)},
        )
        ev = ko_fields("ev")
        ev.update(
            manuscript_id=manuscript["id"], episode_id=None,
            evidence_type="briefing_answer", signal="declared",
            target=f"Q: {answered} — A: {args.answer}",
            supports_belief=belief["id"], weight="high",
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

    if args.action == "show":
        from .gdocs import doc_status, tab_url

        if not args.name:
            raise SystemExit("usage: authorlm doc show <file>")
        status = doc_status(db, manuscript)
        master = status.get("_master_id")
        hits = {k: v for k, v in status.items()
                if not k.startswith("_") and isinstance(v, dict)
                and args.name.lower() in k.lower()}
        if not hits:
            raise SystemExit(f"error: no file matching '{args.name}' "
                             "('doc list' shows them).")
        for rel, entry in sorted(hits.items()):
            print(ui.bold(rel))
            checked_out = bool(entry.get("checked_out"))
            state = ("CHECKED OUT — the Doc is the working copy" if checked_out
                     else "local file is the working copy")
            print(ui.dim(f"  {state}"))
            if entry.get("tab_id") and master:
                print(f"  url: {tab_url(master, entry['tab_id'])}")
            elif entry.get("doc_id"):
                print("  url: https://docs.google.com/document/d/"
                      f"{entry['doc_id']}/edit")
            else:
                print(ui.dim("  not linked to a Doc yet — 'doc push' links it"))
            if entry.get("pushed_hash"):
                print(ui.dim(f"  pushed_hash: {entry['pushed_hash']}"))
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
                # The reader-load manifest is DERIVED, so it is recomputed
                # here rather than stored: every push writes the current
                # book, and a push is the only thing that writes it. It
                # rides along on a single-file push too — one essay changing
                # can move the whole curve, and a manifest tab that is only
                # correct after a full push is a tab nobody can trust.
                from . import manifest as mf

                try:
                    _mpath, _mrows = mf.refresh(db, manuscript)
                    print(ui.dim(
                        f"Manifest recomputed: {len(_mrows)} units, "
                        f"{sum(len(r['debt']) for r in _mrows)} unmet "
                        f"assumptions → {mf.FILENAME}"))
                except Exception as err:   # never fatal to a push
                    print(ui.dim(f"Manifest not recomputed: {err}"))
                # One tab per file in a single master Doc; no file argument
                # pushes the whole manuscript.
                targets = ([args.name] if args.name
                           else gdocs._reading_order_files(
                               gdocs.manuscript_bridge(manuscript)))
                if args.name and mf.FILENAME not in targets:
                    targets.append(mf.FILENAME)
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
                # Snapshot before the pull can overwrite anything
                # (BUG-1 / A2): a base-less mapped file's pull is
                # destructive, and this is the recovery point.
                with contextlib.redirect_stdout(io.StringIO()):
                    api.collect(db, manuscript, config, source="pre-doc-pull")
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
                for relpath in result.get("marked", []):
                    print(ui.yellow(
                        f"MID-SETTLE: {relpath} carries staged "
                        f"<<old>>{{{{new}}}} forms on disk — untouched. "
                        f"Pulling would discard the resolve. Finalize it "
                        f"('filter resolve {relpath}') or put the original "
                        f"text back ('filter unmark {relpath}')."))
                for relpath in result.get("sidecar_unparsable", []):
                    print(ui.yellow(
                        f"REFUSED: the tab for {relpath} came back with no "
                        f"table rows at all, and the local file has some — "
                        f"a Docs export that mangled the table would "
                        f"destroy the dictionary. Untouched, and --force "
                        f"does not reach past this. Fix the table in the "
                        f"Doc (it must stay a table) and pull again."))
                if result["changed"]:
                    print("Collecting:")
                    cmd_collect(args)
                elif (not result["conflicts"] and not result["missing"]
                      and not result.get("local_ahead")
                      and not result.get("marked")
                      and not result.get("sidecar_unparsable")):
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
        for statement, note in summary["beliefs"]:
            print(f"  {ui.cyan('belief')} \"{ui.shorten(statement, 70)}\" — {note}")
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
    if kind == "beliefs":
        return [r["id"] for r in db.all(
            "SELECT id FROM editorial_beliefs WHERE manuscript_id = ? "
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
    result = api.get_plan(db, manuscript)
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


def cmd_proposal(args):
    from . import proposals as prop

    db = _open_db(args)
    manuscript = _manuscript(db, args)
    mid = manuscript["id"]

    if args.action == "show":
        if not args.id:
            sys.exit("usage: authorlm proposal show <id-prefix>")
        found = api.list_proposals(db, manuscript, proposal_id=args.id)["open"]
        if not found:
            sys.exit(f"error: no open proposal matching '{args.id}'.")
        for row in found:
            summary, details = prop.describe(row)
            print(ui.bold(f"[{row['id']}] ({row['kind']}) {summary}"))
            print(ui.dim(f"  target: {row['target']}  ·  state: {row['state']}"
                         f"  ·  raised: {row['created_at'][:19]}"
                         f"  ·  source: {row['source']}"))
            for line in details:
                print(f"  {line}")
            print(ui.dim("\n  → proposal accept|dismiss "
                         f"{row['id'][:8]} [--why \"...\"]"))
        return

    if args.action == "dedupe":
        from . import loop as _loop
        from collections import defaultdict

        rows = prop.open_proposals(db, mid)
        groups = defaultdict(list)
        for r in rows:
            groups[(r["kind"], r["target"])].append(r)
        folded_now, shown = 0, 0
        for (kind, _t), items in groups.items():
            spec = _loop.REGISTRY.get(f"proposals/{kind}")
            if spec is None:
                continue
            kept = []
            for r in items:
                text = spec.dedupe_text(r)
                hit = _loop.near_duplicate(text, kept)
                if not hit:
                    kept.append((r["id"], text))
                    continue
                folded_now += 1
                if shown < 12:
                    prior = next(x for i, x in kept if i == hit[0])
                    print(f"  [{hit[1]:.2f}] keep [{hit[0][:8]}] "
                          f"{prior[:88]}")
                    print(ui.dim(f"         fold [{r['id'][:8]}] {text[:88]}"))
                    shown += 1
                if args.apply:
                    meta = loads(r.get("metadata"), {}) or {}
                    meta.update(by="firewall", law=None,
                                reason=f"restates {hit[0]} ({hit[1]:.2f})")
                    db.update("knowledge_proposals", r["id"],
                              {"state": "dismissed",
                               "metadata": json.dumps(meta)})
        if folded_now > shown:
            print(ui.dim(f"  … and {folded_now - shown} more"))
        verb = "folded" if args.apply else "would fold"
        print(f"\n{verb} {folded_now} of {len(rows)} open proposal(s) as "
              f"restatements (threshold {_loop.NEAR_DUPLICATE}).")
        if not args.apply and folded_now:
            print(ui.dim("  → 'proposal dedupe --apply' to fold them. This is "
                         "the firewall applied to the EXISTING backlog; new "
                         "proposals are screened at creation."))
        return

    if args.action == "reconcile":
        report = api.reconcile_proposals(db, manuscript,
                                         apply=not args.dry_run)
        head = f"{'would settle' if args.dry_run else 'settled'}"
        for kind, st in sorted(report["by_kind"].items()):
            print(f"  {kind:<20} considered {st['considered']:>4}  "
                  f"satisfied {st['satisfied']:>4}  orphan {st['orphan']:>3}  "
                  f"re-based {st['stale']:>4}  live {st['live']:>4}")
        n = len(report["satisfied"]) + len(report["orphan"])
        print(f"\n{head} {n} proposal(s) the world had already answered; "
              f"re-based {len(report['stale'])} against the note as it now "
              f"stands.")
        if report["stale"]:
            print(ui.dim("  re-based proposals are NOT dismissed — some are "
                         "regressions that only look like improvements "
                         "against the base they carried."))
        print(f"still open: {report['still_open']}")
        return

    if args.action == "screen":
        llm = LLMClient(_load_config(args))
        def _progress(done, total, cuts):
            print(ui.dim(f"    …{done}/{total} screened, {cuts} cut"),
                  flush=True)

        report = api.screen_proposals(db, manuscript, llm,
                                      on_batch=_progress)
        for key, stat in report["by_queue"].items():
            print(f"  {key}: considered {stat['considered']}, "
                  f"cut {stat['cut']}")
        if not report["cut"]:
            print(ui.dim("Nothing cut — the screen only acts on law it can "
                         "name, and none applies yet. Beliefs reach "
                         "'validated' after two supporting explanations "
                         "('belief list')."))
        else:
            print(ui.yellow(f"\n{len(report['cut'])} proposal(s) folded away. "
                            "'proposal list' shows them grouped by the belief "
                            "that cut them; accepting one there tells the "
                            "system that belief is wrong."))
        print(f"still open: {report['still_open']}")
        _report_llm(llm)
        return

    if args.action == "list":
        if getattr(args, "belief", None):
            expanded = api.list_proposals(db, manuscript, belief=args.belief)
            if not expanded["proposals"]:
                print(f"No proposals folded by '{args.belief}'.")
                return
            print(ui.bold(f"{expanded['count']} proposal(s) folded by "
                          f"{args.belief}:"))
            for row in expanded["proposals"]:
                summary, details = prop.describe(row)
                print(f"  [{row['id'][:8]}] {summary}")
                for line in details:
                    print(ui.dim(f"      {line[:150]}"))
            print(ui.dim("\n  → 'proposal accept <id>' if the screen was "
                         "wrong; that is what retracts the belief."))
            return
        rows = prop.open_proposals(db, mid)
        folds = []
        from . import loop as _loop

        for spec in _loop.REGISTRY.values():
            if spec.table == "knowledge_proposals":
                folds.extend(_loop.folded(db, mid, spec))
        if folds:
            print(ui.bold("Folded by the screen (not shown above):"))
            for f in folds:
                blessed = "ratified" if f["accepted"] else "unblessed belief"
                print(ui.dim(f"  [{f['law'][:8]}] {f['count']:>3} × "
                             f"\"{f['statement'][:70]}\" ({blessed})"))
            print(ui.dim("  → 'proposal list --belief <id>' to expand · "
                         "accepting one contradicts that belief"))
            print()
        if not rows:
            print("No open proposals. They arise when new material conflicts "
                  "with settled knowledge (edited definitions, retired concepts "
                  "recurring, rejected relationships argued again).")
            return
        print(ui.bold(f"Open proposals ({len(rows)}):"))
        # Q/note-group: several open note_update rows on the same concept
        # are shown together as one entry with every candidate note, not as
        # N separate lines — see proposals.group_open. Storage is untouched;
        # accept/dismiss still take an individual candidate's own id.
        for row in prop.group_open(rows):
            summary, _ = prop.describe(row)
            label = (f"{len(row['members'])} candidates" if row["kind"] == "note_update_group"
                     else row["id"][:8])
            print(f"  [{label}] ({row['kind']}) {summary}")
        print(ui.dim("  → proposal review (interactive) · "
                     "proposal accept|dismiss <id>"))
        return

    if args.action == "review":
        import textwrap

        raw_rows = prop.open_proposals(db, mid)
        if not raw_rows:
            print("No open proposals to review.")
            return
        # Q/note-group: several open note_update rows on the same concept
        # review as ONE turn with every candidate note on the table, not as
        # N separate turns — see proposals.group_open. Nothing is auto-
        # picked: the author chooses a candidate by number.
        rows = prop.group_open(raw_rows)
        print(f"{len(raw_rows)} open proposal(s).")
        print(ui.dim("Keys: [k]eep (adopt)  [e]dge (alias → generalizes)  "
                     "[r]eject (dismiss)  [s]kip (or Enter)  [x] quit  "
                     "(grouped notes: type a candidate number to adopt it)"))
        adopted = dismissed = skipped = 0
        for index, row in enumerate(rows, start=1):
            summary, details = prop.describe(row)
            members = row["members"] if row["kind"] == "note_update_group" else None
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
                if members and choice.isdigit() and 1 <= int(choice) <= len(members):
                    chosen = members[int(choice) - 1]
                    print(f"  {ui.green(prop.adopt(db, mid, chosen))}")
                    adopted += 1
                    break
                if choice in ("k", "keep", "adopt", "accept"):
                    if members:
                        print(ui.dim(f"  ? {len(members)} candidates on the table — "
                                     "type a number (1-"
                                     f"{len(members)}) to adopt one"))
                        continue
                    print(f"  {ui.green(prop.adopt(db, mid, row))}")
                    adopted += 1
                    break
                if choice in ("e", "edge") and row["kind"] == "alias":
                    print(f"  {ui.cyan(prop.demote_to_edge(db, mid, row))}")
                    adopted += 1
                    break
                if choice in ("r", "reject", "dismiss"):
                    if members:
                        for member in members:
                            print(f"  {ui.yellow(prop.dismiss(db, mid, member))}")
                        dismissed += len(members)
                    else:
                        print(f"  {ui.yellow(prop.dismiss(db, mid, row))}")
                        dismissed += 1
                    break
                if choice in ("s", "skip", ""):
                    skipped += len(members) if members else 1
                    break
                if choice in ("x", "quit"):
                    for remaining in rows[index - 1:]:
                        skipped += (len(remaining["members"])
                                    if remaining["kind"] == "note_update_group" else 1)
                    print(f"Proposals: adopted {adopted}, dismissed {dismissed}, "
                          f"skipped {skipped}.")
                    return
                hint = "  ? use k / e / r / s / x"
                if members:
                    hint += f" — or a number 1-{len(members)}"
                print(ui.dim(hint))
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
            row = api.add_style_law(
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
            result = api.retire_style_law(db, manuscript, args.params[0])
            print(f"Retired style element: {result['statement']}")
        elif args.action == "move":
            # Re-scope an element to another guide (or a file). Elements
            # imported at house scope routinely belong to one part.
            if not (args.guide or args.file):
                sys.exit("usage: style move <id> --guide NAME | --file FILE")
            result = api.move_style_law(
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
            "editorial_beliefs", "guidance_history", "editorial_reviews", "evidence",
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
        f"Beliefs: {counts['editorial_beliefs']}"
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


# --------------------------------------------------- client provenance

# Which tables `provenance <id-prefix>` will look in, and what to call
# the thing it finds. Forensic and deliberately unscoped by manuscript:
# the author has an id and wants to know which chat produced it.
_PROVENANCE_TABLES = (
    ("writeups", "writeup"),
    ("declared_intents", "intent"),
    ("sessions", "session"),
    ("guidance_history", "guidance"),
    ("editorial_reviews", "review"),
    ("editorial_episodes", "episode"),
    ("editorial_transitions", "transition"),
    ("evidence", "evidence"),
    ("concept_nodes", "concept"),
    ("concept_edges", "edge"),
    ("editorial_beliefs", "belief"),
    ("knowledge_proposals", "proposal"),
    ("improvement_tasks", "improvement"),
    ("manuscript_versions", "version"),
)


def _client_name(entry: dict) -> str:
    """`engine/session`, with a `~` when the claim is only ambient."""
    session = entry.get("session") or entry.get("session_id")
    mark = "~" if entry.get("precision") == "ambient" else ""
    return f"{mark}{entry.get('engine') or 'unknown'}/{session or '(unclaimed)'}"


def _client_index(db) -> dict:
    """`(engine, session)` → the rich payload, gathered from every
    session's `metadata.clients`. This is the join §1.1 is built around:
    the ~70-byte key sits on every row, the ~250-byte payload once."""
    index: dict = {}
    try:
        rows = db.all("SELECT id, created_at, metadata FROM sessions "
                      "ORDER BY created_at")
    except Exception:
        return index
    for row in rows:
        for entry in (loads(row["metadata"], {}) or {}).get("clients") or []:
            if not isinstance(entry, dict):
                continue
            key = (entry.get("engine"), entry.get("session"))
            merged = dict(index.get(key) or {})
            merged.update({k: v for k, v in entry.items() if v})
            sessions = list(merged.get("sessions") or [])
            if row["id"] not in sessions:
                sessions.append(row["id"])
            merged["sessions"] = sessions
            index[key] = merged
    return index


def _row_clients(meta: dict) -> list[dict]:
    """Both stamp shapes on one row: the birth key and the touched-by
    list, merged so the renderer has a single thing to print."""
    out: list[dict] = []
    born = meta.get("client")
    if isinstance(born, dict):
        out.append({**born, "born": True})
    for entry in meta.get("clients") or []:
        if not isinstance(entry, dict):
            continue
        existing = next((e for e in out
                         if e.get("engine") == entry.get("engine")
                         and e.get("session") == entry.get("session")), None)
        if existing is not None:
            existing.update({k: v for k, v in entry.items() if v})
        else:
            out.append(dict(entry))
    return out


def _provenance_hits(db, prefix: str) -> list[tuple]:
    hits = []
    for table, label in _PROVENANCE_TABLES:
        try:
            rows = db.all(f"SELECT * FROM {table} WHERE id LIKE ?",
                          (f"%{prefix}%",))
        except Exception:
            continue
        hits.extend((table, label, dict(r)) for r in rows)
    return hits


def _trace_lines(args):
    from . import tracelog

    path = tracelog.log_dir(getattr(args, "workspace", None)) / "trace.jsonl"
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return []
    out = []
    for line in text.splitlines():
        try:
            entry = json.loads(line)
        except Exception:
            continue
        if isinstance(entry, dict):
            out.append(entry)
    return out


def _backfill_note(created_at: str | None) -> None:
    """R5. Old rows carry no stamp, and the verb SAYS so rather than
    rendering them as `unknown` and letting the reader infer a client
    that was never recorded. Nothing is backfilled: correlating old rows
    to old transcripts by timestamp is a confident guess across parallel
    chats, which is the one failure this whole mechanism exists to
    prevent."""
    if created_at and created_at >= clients.STAMPING_SINCE:
        return
    print()
    print(f"note  rows created before {clients.STAMPING_SINCE} carry no "
          f"client stamp; for those,")
    print("      attribution is by timestamp correlation only. The method "
          "that still works:")
    print("      grep the chat transcripts for the id — the filename is "
          "the session.")
    print("      grep -l '<id>' ~/.claude/projects/<cwd-with-dashes>/*.jsonl")


def _print_client_body(entry: dict, rich: dict, indent: str = "      ") -> None:
    label = rich.get("label") or entry.get("label")
    if label:
        print(f"{indent}{label}")
    verbs = entry.get("verbs") or []
    if verbs:
        print(f"{indent}{', '.join(str(v) for v in verbs)}")
    note = entry.get("note") or rich.get("note")
    if note:
        print(f"{indent}{note}")
    hint = rich.get("transcript_hint") or entry.get("transcript_hint")
    if hint:
        print(f"{indent}transcript {hint}")


def _client_listing(db, index: dict) -> dict:
    """Every chat we have seen. The rich payload only exists for chats
    that opened an AuthorLM session — a conversational surface does, bare
    CLI use does not — so this also sweeps the row stamps, or a CLI-only
    workspace would report no clients while every row named one."""
    listing = {k: dict(v) for k, v in index.items()}
    for table, _label in _PROVENANCE_TABLES:
        try:
            rows = db.all(f"SELECT created_at, metadata FROM {table}")
        except Exception:
            continue
        for row in rows:
            for entry in _row_clients(loads(row["metadata"], {}) or {}):
                key = (entry.get("engine"), entry.get("session"))
                known = listing.setdefault(key, {
                    "engine": key[0], "session": key[1],
                    "precision": entry.get("precision")})
                seen = entry.get("last") or row["created_at"]
                if seen and seen > (known.get("last_seen") or ""):
                    known["last_seen"] = seen
                first = entry.get("first") or row["created_at"]
                if first and first < (known.get("first_seen") or first):
                    known["first_seen"] = first
                known.setdefault("first_seen", first)
    return listing


def _print_clients_listing(listing: dict) -> None:
    if not listing:
        print("No clients recorded yet. Rows carry a client stamp only from "
              f"{clients.STAMPING_SINCE}.")
        return
    ordered = sorted(listing.values(),
                     key=lambda e: e.get("last_seen") or "", reverse=True)
    for entry in ordered:
        span = (f"{entry.get('first_seen', '?')} → {entry.get('last_seen', '?')}"
                if entry.get("first_seen") else "")
        print(f"{_client_name(entry):<44} {entry.get('precision', '?'):<8} {span}")
        _print_client_body(entry, entry, indent="    ")


def _print_beats(db, writeup: dict, index: dict) -> None:
    rows = db.all(
        "SELECT * FROM guidance_history WHERE batch_id = ? "
        "ORDER BY batch_index", (writeup["id"],))
    if not rows:
        return
    print()
    print("beats")
    crossed = False
    for row in rows:
        meta = loads(row["metadata"], {}) or {}
        proposed = meta.get("client") if isinstance(meta.get("client"), dict) else None
        accepted = meta.get("accepted_by") if isinstance(
            meta.get("accepted_by"), dict) else None
        if accepted is None:
            review = db.one(
                "SELECT metadata FROM editorial_reviews WHERE guidance_id = ? "
                "ORDER BY created_at DESC LIMIT 1", (row["id"],))
            if review is not None:
                candidate = (loads(review["metadata"], {}) or {}).get("client")
                if isinstance(candidate, dict):
                    accepted = candidate
        line = (f"  n{row['batch_index']}  {row['id'][:8]}  "
                f"proposed by {_client_name(proposed) if proposed else '(unstamped)'}")
        if accepted:
            line += f"   {row['state']} by {_client_name(accepted)}"
            if proposed and (accepted.get("engine"), accepted.get("session")) != (
                    proposed.get("engine"), proposed.get("session")):
                line += "  ←"
                crossed = True
        print(line)
    if crossed:
        print()
        print("  ← a beat drafted by one chat and settled by another")


def cmd_dbperf(args):
    """Read the always-on database performance log. Read-only, no
    network, no LLM, no writes — and it does not open the database, so it
    is safe against a live workspace mid-flight."""
    from . import dbperf

    for line in dbperf.report(dbperf.log_dir(getattr(args, "workspace", None)),
                              days=args.days, top=args.top):
        print(line)


def cmd_usage(args):
    """Read the always-on usage ledger. Read-only, no network, no LLM, no
    writes — and it does not open the database, so it is safe against a
    live workspace mid-flight.

    `--sweep` is the ONE exception and is explicit: the plain reader never
    sweeps, because a report that silently changes what it is reporting on
    is not a report, and an implicit sweep would put a transcript parse on
    a read-only verb."""
    from . import usage

    workspace = getattr(args, "workspace", None)
    if args.sweep:
        swept = usage.sweep_all(workspace, force=True)
        print(f"swept {len(swept)} live chat session(s)")
        print()
    directory = usage.log_dir(workspace)
    if getattr(args, "json", False):
        print(json.dumps(usage.read_entries(directory), indent=1))
        return
    for line in usage.report(directory, days=args.days, top=args.top,
                             by=args.by, workspace=workspace):
        print(line)


def cmd_provenance(args):
    """Which chat did this. Read-only, no network, no LLM, no writes —
    safe to run against a live workspace mid-flight."""
    db = _open_db(args)
    index = _client_index(db)

    if args.clients or (not args.id and not args.client):
        _print_clients_listing(_client_listing(db, index))
        return

    if args.client:
        matches = [e for k, e in _client_listing(db, index).items()
                   if k[1] and args.client in str(k[1])]
        if not matches:
            print(f"No client matching '{args.client}'. "
                  f"Try `authorlm provenance --clients`.")
            return
        for entry in matches:
            print(f"{_client_name(entry)}   {entry.get('label') or ''}".rstrip())
            print(f"  {entry.get('first_seen', '?')} → {entry.get('last_seen', '?')}")
            if entry.get("transcript_hint"):
                print(f"  transcript {entry['transcript_hint']}")
            print(f"  sessions   {' '.join(entry.get('sessions') or []) or '-'}")
            target = (entry.get("engine"), entry.get("session"))
            for table, label in _PROVENANCE_TABLES:
                if table == "sessions":
                    continue
                try:
                    rows = db.all(f"SELECT id, metadata FROM {table}")
                except Exception:
                    continue
                ids = [r["id"] for r in rows
                       if any((c.get("engine"), c.get("session")) == target
                              for c in _row_clients(loads(r["metadata"], {}) or {}))]
                if not ids:
                    continue
                shown = " ".join(i[:8] for i in ids[:6])
                more = f" (+{len(ids) - 6})" if len(ids) > 6 else ""
                print(f"  {label:<10} {shown}{more}")
            traced = [t for t in _trace_lines(args)
                      if isinstance(t.get("client"), dict)
                      and t["client"].get("session") == entry.get("session")]
            if traced:
                errors = sum(1 for t in traced if not t.get("ok", True))
                print(f"  verbs      {len(traced)} invocations, {errors} errors"
                      f"    (trace.jsonl)")
            print()
        return

    hits = _provenance_hits(db, args.id)
    if not hits:
        sys.exit(f"error: no object matching '{args.id}'.")
    if len({h[2]["id"] for h in hits}) > 1:
        names = ", ".join(sorted(h[2]["id"] for h in hits)[:6])
        sys.exit(f"error: '{args.id}' is ambiguous ({len(hits)} objects): {names}")
    table, label, row = hits[0]

    print(f"{row['id']}  {label}  created {row['created_at']}")
    meta = loads(row.get("metadata"), {}) or {}
    entries = _row_clients(meta)
    print()
    if not entries:
        print(f"No client stamp on this {label}.")
    else:
        print(f"clients that touched this {label}")
        for entry in entries:
            rich = index.get((entry.get("engine"), entry.get("session")), {})
            span = ""
            if entry.get("first") or entry.get("last"):
                span = f"{entry.get('first', '?')} → {entry.get('last', '?')}"
            elif entry.get("born"):
                span = "created it"
            print(f"  {_client_name(entry):<44} "
                  f"{entry.get('precision', '?'):<8} {span}".rstrip())
            _print_client_body(entry, rich)
    if table == "writeups":
        _print_beats(db, row, index)
    _backfill_note(row.get("created_at"))


def cmd_client_hook(args):
    """Hidden. The `SessionStart` / `SessionEnd` hook target: reads the
    engine's documented JSON payload on stdin and writes (or removes) one
    per-session marker file.

    Exits 0 always and prints NOTHING to stdout — a SessionStart hook's
    stdout is injected into the model's context and we have nothing to
    say to it. Every failure is swallowed: a provenance hook must never
    be the reason a chat fails to start."""
    from .adapters import claude_code

    if args.print_setup:
        print(claude_code.setup_snippet())
        print()
        print("# Paste into .claude/settings.json in this repo (committable, "
              "binds every")
        print("# chat started here) or ~/.claude/settings.json (all "
              "projects, personal).")
        print("# The hook only ENRICHES: a chat without it is still "
              "attributed exactly.")
        return
    try:
        raw = sys.stdin.read()
    except Exception:
        return
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        return
    if not isinstance(payload, dict):
        return
    try:
        workspace = getattr(args, "workspace", None)
        if args.end:
            # One forced sweep BEFORE the unlink. This is the only place a
            # chat's TAIL — everything after its last AuthorLM verb — is
            # ever counted, and the tail of a long session is often its
            # largest part. `allow_recompute=False`: the discontinuity path
            # is a full file read and the hook's documented shared
            # SessionEnd budget is 1.5 s, so this sweep declines that work
            # and takes the `restart` loss. Nothing is lost permanently —
            # the checkpoint is left untouched, so the next opportunistic
            # sweep of this session (a resume) still sees the
            # discontinuity and does the recompute off the hook's clock.
            from . import clients as _clients, usage as _usage

            _usage.sweep_session(
                _clients.Client(engine=claude_code.ENGINE,
                                session_id=payload.get("session_id"),
                                transcript_hint=payload.get("transcript_path"),
                                precision="exact", adapter="claude-code"),
                workspace=workspace, force=True, allow_recompute=False,
                final=True)
            _usage.flush("client-hook session-end")
            claude_code.clear_marker(payload.get("session_id"),
                                     workspace=workspace)
        else:
            claude_code.write_marker(payload, os.environ, workspace=workspace)
    except Exception:
        pass


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


def cmd_workbench(args):
    from .workbench_server import run

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
             "the database. Defaults to AUTHORLM_WORKSPACE from the "
             "checkout's .env ('authorlm setup' writes it), else your home "
             "directory. A missing database is an error, not an invitation: "
             "only 'init' with -w, or 'setup', creates one")
    parser.add_argument("-m", "--manuscript", help="manuscript name (needed only if several exist)")
    # Accept --workspace/--manuscript after the subcommand too (e.g.
    # 'session start -m X'). SUPPRESS keeps the subparser from
    # clobbering a value given before the subcommand.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-w", "--workspace", default=argparse.SUPPRESS,
                        help="workspace override (default: AUTHORLM_WORKSPACE from .env, else ~)")
    common.add_argument("-m", "--manuscript", default=argparse.SUPPRESS,
                        help="manuscript name (needed only if several exist)")
    sub = parser.add_subparsers(dest="command", required=True)
    original_add_parser = sub.add_parser

    from .prompt_registry import HELP_NOTE

    def add_parser(name, *a, **kw):
        kw.setdefault("parents", [common])
        if name in LLM_VERBS:
            desc = kw.get("description") or kw.get("help") or ""
            kw["description"] = (desc + "\n\n" + HELP_NOTE).strip()
            kw.setdefault("formatter_class", argparse.RawDescriptionHelpFormatter)
        return original_add_parser(name, *a, **kw)

    sub.add_parser = add_parser

    p = sub.add_parser(
        "setup", help="point this checkout at a workspace and create its database")
    p.add_argument("--yes", "-y", action="store_true",
                   help="create without asking (with --workspace)")
    p.set_defaults(func=cmd_setup)

    p = sub.add_parser("init", help="register a manuscript directory")
    p.add_argument("--name", required=True)
    p.add_argument("--path", required=True)
    p.add_argument("--author", default="",
                   help="author or pen name used for publication")
    p.add_argument("--copyright-owner", default="",
                   help="legal or organizational copyright owner")
    p.add_argument("--paperback-isbn", default="",
                   help="ISBN-13 assigned to the paperback edition")
    p.add_argument("--hardcover-isbn", default="",
                   help="ISBN-13 assigned to the hardcover edition")
    p.add_argument("--no-extract", action="store_true",
                   help="skip automatic LLM concept extraction")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser(
        "manuscript", help="show or set manuscript publication metadata")
    p.add_argument("action", choices=["show", "set"])
    p.add_argument("--author", default=None,
                   help="author or pen name used for publication")
    p.add_argument("--copyright-owner", default=None,
                   help="legal or organizational copyright owner")
    p.add_argument("--paperback-isbn", default=None,
                   help="ISBN-13 assigned to the paperback edition")
    p.add_argument("--hardcover-isbn", default=None,
                   help="ISBN-13 assigned to the hardcover edition")
    p.add_argument("--trim-size", default=None, metavar="WxH",
                   help="print trim size in inches, e.g. 6x9 (the book "
                        "profile refuses to build without one)")
    p.add_argument("--narrator", default=None,
                   help="the audiobook's narrator, for the credits and tags")
    p.add_argument("--publisher", default=None,
                   help="the publisher named in the closing credits")
    p.add_argument("--copyright-year", default=None, metavar="YYYY",
                   help="four-digit copyright year (empty clears)")
    p.add_argument("--language", default=None, metavar="CODE",
                   help="publication language, e.g. en or en-US")
    p.add_argument("--bleed", default=None, choices=["yes", "no"],
                   help="whether the print interior bleeds to the page "
                        "edge (adds 0.125in to the page on three sides)")
    p.set_defaults(func=cmd_manuscript)

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

    p = sub.add_parser(
        "workbench",
        help="open the pronunciation workbench (audiobook)",
        description="Serve the pronunciation workbench on a local port and "
                    "open it: every word in pronunciations.md, every voice "
                    "that says it, Regenerate and listen, Save, Push, Export. "
                    "Nothing spends credits until Regenerate is pressed.")
    p.add_argument("--host", default="127.0.0.1",
                   help="local interface to bind (default: 127.0.0.1)")
    p.add_argument("--port", type=int, default=0,
                   help="local port (default: choose a free port)")
    p.add_argument("--no-open", action="store_true",
                   help="print the URL without opening a browser")
    p.set_defaults(func=cmd_workbench)

    p = sub.add_parser("intent", help="declare/scope/complete/abandon/list "
                                      "writing intents")
    p.add_argument("action", choices=["declare", "show", "scope", "complete",
                                      "abandon", "retire", "list"])
    p.add_argument("statement", nargs="?",
                   help="intent statement (declare) or id prefix "
                        "(show/scope/complete/abandon)")
    p.add_argument("--outcome", help="outcome note (complete) or reason (abandon)")
    p.add_argument("--scope", metavar="FILE",
                   help="declare/scope: the goal applies to this essay only")
    p.add_argument("--chapter", metavar="OPENER",
                   help="declare/scope: the goal applies to every essay "
                        "beneath this toc opener")
    # NOT `--manuscript`: `-m/--manuscript` is already on every subparser
    # (the `common` parent, above), and a second registration is an
    # argparse conflict at parser-build time. `--book-wide` says the same
    # thing and cannot be mistaken for the manuscript SELECTOR.
    p.add_argument("--book-wide", dest="manuscript_wide",
                   action="store_true",
                   help="declare/scope: the goal applies to the whole book "
                        "(what an absent scope already means)")
    p.add_argument("--triage", action="store_true",
                   help="scope: the one-time sitting — every unscoped active "
                        "intent with the files its episodes actually touched")
    p.set_defaults(func=cmd_intent)

    p = sub.add_parser(
        "critique",
        help="external critique: import a report as proposed items, triage "
             "them, then the essay-by-essay edit pass — run → triage "
             "--edits → write → (read in Docs) → resolve; "
             "'list --decided' reads the past verdicts back "
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
    p.add_argument("--query",
                   help="show / list --decided: search critique items by "
                        "text (statement, reason, or the critic's original)")
    p.add_argument("--decided", action="store_true",
                   help="list: the resolved items instead of the pending "
                        "queue — every past verdict with its reason "
                        "(narrow with --scope/--verdict/--query)")
    p.add_argument("--verdict",
                   choices=["accept", "reject", "revise", "retired"],
                   help="list --decided: only items answered this way "
                        "('retired' = accepted once, retired since)")
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
        help="beat-by-beat co-writing loop: draft → author verdict → append "
             "(docs/autoregressive-writing-design.md)")
    p.add_argument("action",
                   choices=["start", "plan", "intents", "status", "draft",
                            "critique", "propose", "show", "landed", "accept",
                            "reject", "learn", "complete", "abandon",
                            "digest"])
    p.add_argument("params", nargs="*", help="start: <file>")
    p.add_argument("--intent", action="append", metavar="ID",
                   help="start: reach for these intents BY NAME, skipping "
                        "derivation (repeatable; the first is the primary). "
                        "Without it the set is derived from the scopes "
                        "covering the essay")
    p.add_argument("--add", action="append", metavar="ID",
                   help="intents: join this intent to the writeup")
    p.add_argument("--remove", dest="remove_intent", action="append",
                   metavar="ID",
                   help="intents: drop it (only while the set is proposed)")
    p.add_argument("--primary", metavar="ID",
                   help="intents: the member whose episode carries this "
                        "writeup's transitions and verdicts")
    p.add_argument("--defer", metavar="ID",
                   help="intents: a ratified member this writeup will not "
                        "serve (--reason required)")
    p.add_argument("--ignore", action="append", metavar="ID",
                   help="intents: dismiss a newly-in-scope intent so the "
                        "flag stops recurring")
    p.add_argument("--new", action="store_true",
                   help="start: create <file>; it does not exist yet "
                        "(requires --after and --style; the one-paragraph "
                        "brief travels on stdin)")
    p.add_argument("--style", metavar="GUIDE",
                   help="start --new: the style guide to attach to the new "
                        "file (style attach cannot run before the file exists)")
    p.add_argument("--dispositions", action="store_true",
                   help="digest: merge point dispositions (JSON on stdin)")
    p.add_argument("--show", action="store_true",
                   help="digest: print the stored digest and the accounting "
                        "tally")
    p.add_argument("--after",
                   help="start: placement for an essay with no toc entry "
                        "yet — the file it follows, or 'start' to open the "
                        "book. Without it the reading order's own position "
                        "is used (unlisted files sort to the end)")
    p.add_argument("--writeup", help="which writeup: an id prefix or the "
                                     "essay's file name (default: the active "
                                     "writeup, when there is only one)")
    p.add_argument("--why", help="propose: which concepts the draft realizes, "
                                 "which precedent it follows (required)")
    p.add_argument("--reason", help="reject: the author's why, verbatim "
                                    "(required); intents --defer: required; "
                                    "accept: optional")
    p.add_argument("--replace", action="store_true",
                   help="plan: replace the remaining (unwritten) beats; "
                        "digest: replace the stored digest")
    p.add_argument("--dry-run", action="store_true", dest="dry_run",
                   help="draft: assemble and print the payload with a "
                        "sha256 per block, and make NO model call — the way "
                        "to audit what is sent and to find a silent cache "
                        "invalidator between two beats")
    p.add_argument("--out", metavar="PATH",
                   help="draft --dry-run / critique: write the payload to "
                        "this file (for a clean-context drafter or critic) "
                        "and print only its sizes and hashes")
    p.add_argument("--critique", metavar="PATH",
                   help="propose: the critic's report for this exact draft "
                        "(VERDICT PASS required)")
    p.add_argument("--no-critic", metavar="REASON", dest="no_critic",
                   help="propose: skip the critic gate with a reason that is "
                        "recorded (e.g. the author dictated the text)")
    p.add_argument("--plain", action="store_true",
                   help="show: the pending draft as registered, without the "
                        "change marks")
    p.add_argument("--changed", action="store_true",
                   help="show: only the paragraphs that changed against the "
                        "original, with a count of the ones left out")
    p.add_argument("--html", action="store_true",
                   help="show: the marked view as an HTML fragment "
                        "(<span class=\"chg\"> on changed spans) for a "
                        "colored rendering")
    p.add_argument("--lint-override", metavar="REASON", dest="lint_override",
                   help="propose: register despite lint ERRORs, with a reason "
                        "that is recorded (a refrain, a quotation)")
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
                                      "show", "push", "pull", "open", "auth",
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
             "review <n>, push <file> (staged lens edits → Doc forms), "
             "resolve <file> (read the tab back)")
    p.add_argument("action",
                   choices=["add", "list", "show", "run", "register",
                            "review", "push", "resolve", "status", "sweep",
                            "findings", "repair"])
    p.add_argument("--dismiss", metavar="IDS",
                   help="repair: comma-separated judgment finding ids to "
                        "dismiss (with --reason); their tags are removed")
    p.add_argument("--reason", help="repair --dismiss: the author's reason, "
                                    "verbatim")
    p.add_argument("--all", action="store_true",
                   help="findings: include reviewed findings, not only open ones")
    p.add_argument("name", nargs="?",
                   help="lens name (add/show/run/register), finding index "
                        "(review), or manuscript file (push/resolve/"
                        "status/sweep)")
    p.add_argument("file", nargs="?", help="manuscript file (run/register)")
    p.add_argument("--native", action="store_true",
                   help="run/sweep: make the model call on the configured "
                        "[llm] model. WITHOUT it 'lens run' makes NO call: "
                        "it prints the payload for the chat agent or a "
                        "subagent to answer, then 'lens register'.")
    p.add_argument("--dry-run", action="store_true",
                   help="no-op alias: 'lens run' already makes no call")
    p.add_argument("--out", metavar="PATH",
                   help="run/sweep: write the printed payload(s) to this "
                        "path (sweep: a directory) instead of stdout")
    p.add_argument("--reply", metavar="PATH",
                   help="register: read the findings JSON from this file "
                        "instead of stdin")
    p.add_argument("--supersede", action="store_true",
                   help="push/sweep/repair: withdraw lens forms already out "
                        "in the tab (any rewording typed inside them is "
                        "lost) and push the new ones")
    p.add_argument("--no-push", action="store_true",
                   help="run --native / register / sweep --native: record the "
                        "staged edits but do not push them to the Doc tab "
                        "(the local road; the Doc road is the default)")
    p.add_argument("--only", help="sweep: comma-separated lens names to run")
    p.add_argument("--skip", help="sweep: comma-separated lens names to skip")
    p.add_argument("--accept", action="store_true")
    p.add_argument("--reject", action="store_true")
    p.add_argument("--modify", action="store_true")
    p.add_argument("--defer", action="store_true")
    p.add_argument("--explain", help="the author's reasoning, verbatim — "
                                     "the highest-value evidence")
    p.set_defaults(func=cmd_lens)

    p = sub.add_parser(
        "interlocutor",
        help="a third-person critical reading of the whole book from inside "
             "one tradition (_interlocutors/<name>.md, TOML front matter): "
             "add, list, show <name>, draft <name> --engaged <essay> --out "
             "<p> (bootstrap payload for a new critic), run <name> [--file "
             "…] [--engaged …] [--position …] --out <p> (scan + payload, NO "
             "model call — a subagent reads it), import <name> <p> (verify "
             "the report beside the payload, land it in _critiques/, import "
             "the manifest as proposed intents)")
    p.add_argument("action",
                   choices=["add", "list", "show", "draft", "run", "import"])
    p.add_argument("name", nargs="?", help="interlocutor name")
    p.add_argument("target", nargs="?",
                   help="import: the payload path the run wrote")
    p.add_argument("--file", action="append", default=[],
                   help="run: narrow the scan to this chapter or part opener "
                        "(repeatable)")
    p.add_argument("--engaged", action="append", default=[],
                   help="an essay that already engages this interlocutor, "
                        "carried whole (repeatable; adds to the artifact's)")
    p.add_argument("--position", action="append", default=[],
                   help="an essay carried whole as the author's "
                        "authoritative position (repeatable; adds to the "
                        "artifact's)")
    p.add_argument("--out", help="draft/run: where to write the payload")
    p.add_argument("--max-terms", type=int, default=12,
                   help="run: terms shown per run, sampled when more have "
                        "hits (default 12; 0 = all)")
    p.add_argument("--max-per-term", type=int, default=6,
                   help="run: locations shown per term, sampled when more "
                        "exist (default 6; 0 = all)")
    p.add_argument("--seed", type=int,
                   help="run: reproduce an earlier run's sample (the seed "
                        "is printed and recorded in the report)")
    p.set_defaults(func=cmd_interlocutor)

    p = sub.add_parser(
        "filter",
        help="author-defined filters (_filters/*.md, TOML front matter): "
             "add, list, show, run <name> <file> (NO model call by "
             "default), record, edits, triage, push, settle, status, "
             "unmark, rollback, abandon",
        description=(
            "The filter pass. A LENS reads one essay whole and reports "
            "findings; a FILTER reads one essay UNIT BY UNIT and proposes "
            "an edit to each unit, conditioned on what came before.\n\n"
            "NOTE THE FLAG POLARITY, which is the OPPOSITE of 'write "
            "draft'. `filter run` makes NO model call: it prints the "
            "payload for the conversation to draft against. The billed "
            "path is `--native`, and it needs a [filtering] section that "
            "the shipped config deliberately does not have. `--dry-run` "
            "is accepted as a no-op alias for muscle memory.\n\n"
            "TWO ROADS TO THE AUTHOR'S EYES, and the run picks one by "
            "which verb it meets first. `filter resolve <essay>` is the "
            "LOCAL road and the default: the changes are applied (or, "
            "with --pause, written into the file as <<old>>{{new}} forms "
            "to read in Obsidian). `filter push <essay>` is the DOC road: "
            "the same forms go into the essay's tab of the master Doc, "
            "struck-through and green, the file on disk keeps the OLD "
            "text, and `filter resolve <essay>` later reads the tab back. "
            "Local is recommended — its whole state is described by the "
            "bytes on disk, and the Doc road's is not (a crash mid-recovery "
            "can leave forms in a tab that nothing on disk records; "
            "'doc push <essay>' rebuilds the tab from the pristine file). "
            "Once a run takes a road it keeps it; switching is "
            "resolve-then-rerun, never a flag. `filter apply [<name>] "
            "<essay>` is `record` and `push` under one name — the reply "
            "JSON on stdin, the proposals in the tab, one step. It does "
            "NOT draft: in chat mode `filter run` makes no model call, the "
            "assistant answers the payload in the conversation, and no CLI "
            "process can stand in for that. A partial run records and does "
            "not push, because the road is chosen once and a half-pushed "
            "essay has chosen it early."),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("action",
                   choices=["add", "list", "show", "prelude", "run",
                            "record", "apply", "edits", "triage", "push",
                            "resolve", "status", "unmark", "rollback",
                            "abandon"])
    p.add_argument("name", nargs="?",
                   help="filter name (add/show/prelude/run); optional "
                        "elsewhere, to disambiguate two runs on one file")
    p.add_argument("file", nargs="?", help="the essay (one file per run)")
    p.add_argument("--window", type=int, default=None,
                   help="units per reply (default: all remaining)")
    p.add_argument("--from", type=int, default=None, dest="from_unit",
                   metavar="N", help="re-open the window at unit N")
    p.add_argument("--again", action="store_true",
                   help="run even though this filter already ran on this "
                        "exact text")
    p.add_argument("--native", action="store_true",
                   help="make the billed model call ([filtering] required; "
                        "absent by design)")
    p.add_argument("--dry-run", action="store_true", dest="dry_run",
                   help="no-op alias: 'filter run' already makes no call")
    p.add_argument("--replace", action="store_true",
                   help="prelude: rewrite the frozen registry (invalidates "
                        "this run's cached prefix)")
    p.add_argument("--pause", action="store_true",
                   help="settle: write the <<old>>{{new}} forms into the "
                        "file to read in Obsidian instead of applying")
    p.add_argument("--force", action="store_true",
                   help="unmark: required on the DOC road, where taking "
                        "the forms out of the tab destroys any rewording "
                        "the author did there and nothing else holds it")
    p.add_argument("--accept", nargs="*", metavar="N")
    p.add_argument("--reject", nargs="*", metavar="N")
    p.add_argument("--revise", nargs="*", metavar="N")
    p.add_argument("--undo", nargs="*", metavar="N")
    p.add_argument("--reason", help="the author's verbatim words for a "
                                    "rejection — the next run reads it")
    p.add_argument("--text", help="the author's own wording for a revision")
    p.set_defaults(func=cmd_filter)

    p = sub.add_parser(
        "export",
        help="publishing exports: md/docx/epub/pdf built locally (pandoc) "
             "with picked illustrations embedded; settings in "
             "_exports/settings.toml")
    p.add_argument("action",
                   choices=["show", "set", "check", "md", "docx", "epub",
                            "pdf"])
    p.add_argument("key", nargs="?", help="setting name (set)")
    p.add_argument("value", nargs="?", help="setting value (set)")
    p.add_argument("--variant", choices=["images", "slots", "stripped"],
                   help="override the illustration variant for this export")
    p.add_argument("--chapters", action="append", metavar="FILE,FILE",
                   help="build only these chapters and everything filed "
                        "under them in the TOC (repeatable; .md optional)")
    p.add_argument("--print-ready", action="store_true",
                   help="PDF only: omit the default confidential-review "
                        "notice page, footer, and watermark")
    p.add_argument("--profile", choices=["book"], default=None,
                   help="PDF only: 'book' builds a print interior at the "
                        "manuscript's trim size (book class, mirrored "
                        "margins with a KDP gutter, running heads, "
                        "captions under the plates) — no review marks")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser(
        "audio",
        help="the audiobook: export the manifests audiostation generates "
             "from, audition voices, keep the cast and the ElevenLabs "
             "dictionary, check readiness (docs/audiobook-pipeline-design.md)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="examples by action:\n"
               "  audio init                        seed _audio/audiobook.toml "
               "and cast.md (once)\n"
               "  audio export [--chapters F,F]     write audiobook.json and "
               "chapters/*.json (only what changed)\n"
               "  audio check                       is the audiobook ready, "
               "from the sources to audiostation's state\n"
               "  audio voices [--search WORDS] [--library]   free preview "
               "clips into the audition gallery\n"
               "  audio audition --cast narrator,3MTv… --file sermons.md "
               "--paragraphs 3-5   paid clips on the book's words\n"
               "  audio cast set herdsman --voice-id ID --voice 'Adam Stone' "
               "--stability 0.65\n"
               "  audio say Basilides --say \"buh-SIL-ih-deez,ba-SIL-i-deez\"   "
               "hear each respelling in a real sentence, in its voice, "
               "no dictionary push; --settle keeps one\n"
               "  audio dictionary push             pronunciations.md → the "
               "named ElevenLabs dictionary, after showing the diff")
    p.add_argument("action",
                   choices=["init", "export", "check", "voices", "audition",
                            "cast", "dictionary", "say"])
    p.add_argument("rest", nargs="*",
                   help="cast set <key>; dictionary push; say <term>")
    p.add_argument("--say", help="say: respelling(s) to try, comma-separated "
                                 "(default: the table's)")
    p.add_argument("--dictionary", action="store_true",
                   help="say: render the untouched sentence with the pushed "
                        "dictionary attached, to prove the rule fires")
    p.add_argument("--settle", action="store_true",
                   help="say: write the one --say into pronunciations.md")
    p.add_argument("--no-play", action="store_true", dest="no_play",
                   help="say: do not play the clips (afplay)")
    p.add_argument("--chapters", action="append", metavar="FILE,FILE",
                   help="export: rewrite only these chapter files")
    p.add_argument("--offline", action="store_true",
                   help="export: skip the ElevenLabs dictionary lookup")
    p.add_argument("--search", help="voices: words to match (name, labels; "
                                    "the library search with --library)")
    p.add_argument("--library", action="store_true",
                   help="voices: search the shared voice library instead "
                        "of the account")
    p.add_argument("--limit", type=int, default=12,
                   help="voices: at most this many (default 12)")
    p.add_argument("--cast", help="audition: cast keys or voice ids, "
                                  "comma-separated")
    p.add_argument("--text", help="audition: the words to render")
    p.add_argument("--file", help="audition: take the words from this file")
    p.add_argument("--paragraphs", help="audition: N or N-M paragraph "
                                        "sections of --file")
    p.add_argument("--label", help="audition: a word for the caption")
    p.add_argument("--quality", default="mp3_44100_64",
                   help="audition: output format (default mp3_44100_64)")
    p.add_argument("--voice-id", dest="voice_id",
                   help="cast set: the ElevenLabs voice id")
    p.add_argument("--voice", help="cast set: the voice's display name")
    p.add_argument("--model", help="cast set / audition: the model id")
    p.add_argument("--stability", type=float)
    p.add_argument("--similarity", type=float)
    p.add_argument("--speed", type=float)
    p.add_argument("--note", help="cast set: the role note")
    p.add_argument("--yes", action="store_true",
                   help="audition / dictionary push: skip the confirmation")
    p.set_defaults(func=cmd_audio)

    p = sub.add_parser(
        "audiostation",
        help="open the audiostation studio on this manuscript's _audio/ "
             "folder (a built .app, else the dev server; AUDIOSTATION_APP "
             "overrides)")
    p.add_argument("--dry-run", action="store_true", dest="dry_run",
                   help="print the launch command instead of running it")
    p.set_defaults(func=cmd_audiostation)

    for kind, what, example in (
            ("footnote",
             "[Footnote: gist | label: XX] tags — inline requests for a "
             "footnote, drafted in chat, landed here: the tag becomes "
             "[^label] and the definition joins the file's block",
             "  footnote apply becker.md --tag 1 --text 'Ernest Becker, "
             "*The Denial of Death* (1973), ch. 2.'"),
            ("explain",
             "[Explain: gist] tags — inline requests for an explanatory "
             "passage, drafted in chat, landed here in place of the tag",
             "  explain apply becker.md --tag 1 --text 'A vital lie is …'")):
        p = sub.add_parser(
            kind, help=what,
            formatter_class=argparse.RawDescriptionHelpFormatter,
            epilog="examples by action:\n"
                   f"  {kind} list [file]              open tags, per file "
                   "(collect and doc pull report them too)\n"
                   f"{example}\n"
                   f"  {kind} apply <file> --tag 'gist excerpt' --text …  "
                   "name the tag by an excerpt instead of its ordinal\n"
                   "  several --tag/--text pairs ride one command\n"
                   f"  {kind} resolve <file>            file checked out: "
                   "apply wrote <<tag>>{{new}} forms into the tab; this "
                   "reads the author's ruling back and lands it\n\n"
                   f"docs/{kind}-directive-design.md")
        p.add_argument("action", choices=["list", "apply", "resolve"])
        p.add_argument("file", nargs="?",
                       help="list: one file (default: all); apply/resolve: "
                            "the file")
        p.add_argument("--tag", action="append", metavar="N|EXCERPT",
                       help="apply: the tag's ordinal in the file (first "
                            "open tag is 1) or an unambiguous excerpt of "
                            "its gist (repeatable, paired with --text)")
        p.add_argument("--text", action="append", metavar="TEXT",
                       help="apply: the agreed text for the preceding --tag")
        p.add_argument("--footnote", action="append", metavar="TAG=TEXT",
                       help="explain apply only: a companion footnote for "
                            "the tag named by TAG (a --tag handle) — the "
                            "passage lands with its superscript, the "
                            "definition joins the file's block, same review")
        p.set_defaults(func=cmd_directive)

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
               "--reason \"…\"   bulk verdicts\n"
               "  illus import '<fragment>' path.png   bring an image "
               "made outside AuthorLM into that slot as a candidate\n"
               "  illus versions                  list snapshots of "
               "_illustrations/prompts/ (recovery points before a pull)\n"
               "  illus restore                   restore prompts/ from "
               "the latest snapshot (prints the plan first)\n"
               "  illus restore --version 2       restore from a specific "
               "snapshot instead of the latest")
    p.add_argument("action",
                   choices=["list", "show", "render", "rerender", "pick",
                            "import", "prune", "prompt", "scan", "triage",
                            "externalize", "versions", "restore"])
    p.add_argument("name", nargs="?",
                   help="prompt fragment selecting a slot (render/pick); "
                        "render without it does every unrendered slot; "
                        "scan: one file (default: all main matter)")
    p.add_argument("candidate", nargs="?",
                   help="candidate number (pick), or the image file to "
                        "bring in (import)")
    p.add_argument("--caption",
                   help="import: set the slot's reader-facing caption")
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
    p.add_argument("--version", type=int, metavar="N",
                   help="restore: snapshot version to restore from "
                        "(default: the latest); versions: ignored")
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
        "belief", help="list / answer questions / curate (retire, merge, convert)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="example:\n"
               "  belief list                     shows each belief's [id] and its\n"
               "                                  numbered open questions (Q1, Q2, …)\n"
               '  belief answer pol-d970 "Only inside worked examples" --index 2\n'
               "                                  closes Q2 of belief pol-d970; the\n"
               "                                  answer becomes declared evidence\n"
               '  belief retire pol-4c5d --reason "garbled inference"\n'
               '  belief demote pol-4c5d --reason "no longer stands behind it"\n'
               "                                  back to candidate — unlike retire, "
               "may re-validate\n"
               "  belief merge pol-8e69 pol-2c85  fold the duplicate (first) into\n"
               "                                  the canonical (second)\n"
               '  belief convert pol-3b85 --aspect formatting --guide "Essays"\n'
               "                                  belief becomes a ratified style\n"
               "                                  element; the belief is retired",
    )
    p.add_argument("action", choices=["list", "show", "answer", "retire",
                                      "demote", "merge", "convert"])
    p.add_argument("id", nargs="?",
                   help="belief id prefix, from 'belief list'")
    p.add_argument("answer", nargs="?",
                   help="answer text (answer) / canonical belief id (merge)")
    p.add_argument("--index", type=int, default=1,
                   help="which open question to answer — the Q number shown by "
                        "'belief list' (default 1)")
    p.add_argument("--reason", help="why (retire: required)")
    p.add_argument("--aspect", help="convert: style aspect for the new element")
    p.add_argument("--statement", help="convert: reworded statement "
                                       "(default: the belief statement)")
    p.add_argument("--guide", help="convert: owning style guide name")
    p.add_argument("--file", help="convert: file for a file-local element")
    p.add_argument("--notes", help="convert: free-text notes (inspect/avoid hints)")
    p.set_defaults(func=cmd_belief)

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
    p.add_argument("kind", choices=["intents", "beliefs", "concepts",
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
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("proposal",
                       help="review conflicts between new material and settled knowledge")
    p.add_argument("action",
                   choices=["list", "show", "review", "reconcile", "screen",
                            "dedupe", "accept", "edge", "dismiss"])
    p.add_argument("id", nargs="?", help="proposal id prefix (accept/edge/dismiss)")
    p.add_argument("--belief", help="list: expand the fold this belief cut — "
                                    "accepting one of them contradicts it")
    p.add_argument("--apply", action="store_true",
                   help="dedupe: actually fold the restatements (default is "
                        "a dry run that changes nothing)")
    p.add_argument("--dry-run", action="store_true",
                   help="reconcile: report without changing anything")
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

    p = sub.add_parser("dbperf",
                       help="database performance log: top queries, the "
                            "per-day trend, the slow log")
    p.add_argument("--days", type=int, default=7,
                   help="window to report over (default 7)")
    p.add_argument("--top", type=int, default=10,
                   help="how many query shapes per table (default 10)")
    p.set_defaults(func=cmd_dbperf)

    p = sub.add_parser("usage",
                       help="the usage ledger: estimated API spend, chat "
                            "consumption, the per-day trend")
    p.add_argument("--days", type=int, default=7,
                   help="window to report over (default 7)")
    p.add_argument("--top", type=int, default=10,
                   help="how many rows per table (default 10)")
    p.add_argument("--by", default="purpose|model",
                   choices=["purpose|model", "client", "purpose", "model",
                            "day", "verb"],
                   help="how to partition the API spend table")
    p.add_argument("--sweep", action="store_true",
                   help="close the books first: force a chat sweep of "
                        "every live session before reporting")
    p.add_argument("--json", action="store_true",
                   help="emit the raw ledger entries instead of the "
                        "rendering")
    p.set_defaults(func=cmd_usage)

    p = sub.add_parser("provenance",
                       help="which chat session touched an object")
    p.add_argument("id", nargs="?",
                   help="id prefix: wu- / di- / gd- / s- / ev- / concept id")
    p.add_argument("--client", help="invert: what did this chat touch")
    p.add_argument("--clients", action="store_true",
                   help="list known clients, newest first")
    p.set_defaults(func=cmd_provenance)

    # Hidden: the target of a chat engine's own session hooks, not a verb
    # the author types. Hooks execute code, so registration stays
    # explicit — `--print-setup` prints the snippet to paste.
    p = sub.add_parser("client-hook")  # hidden: a chat engine's hook target
    p.add_argument("--end", action="store_true")
    p.add_argument("--print-setup", dest="print_setup", action="store_true")
    p.set_defaults(func=cmd_client_hook)

    p = sub.add_parser("history", help="list/show/restore collected manuscript versions")
    p.add_argument("action", nargs="?", default="list", choices=["list", "show", "restore"])
    p.add_argument("version", nargs="?", help="version number, e.g. '3' or 'v3' (show/restore)")
    p.set_defaults(func=cmd_history)

    return parser


def _dispatch(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    # Global home database: unless -w is given, data lives in ~/.authorlm so
    # every command works from any directory and manuscripts can live anywhere.
    workspace, args.workspace_source = api.resolve_workspace(
        getattr(args, "workspace", None))
    args.workspace = str(workspace)
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
    if (args.command == "intent" and args.action == "scope"
            and not args.triage
            and not (args.scope or args.chapter or args.manuscript_wide)):
        sys.exit("usage: intent scope <id-prefix> --scope FILE | "
                 "--chapter OPENER | --book-wide  (or --triage to see the "
                 "evidence for every unplaced intent)")
    if args.command == "belief" and args.action == "answer" and not (args.id and args.answer):
        sys.exit("usage: belief answer <id-prefix> \"answer text\"")
    if args.command == "belief" and args.action == "retire" and not (args.id and args.reason):
        sys.exit('usage: belief retire <id-prefix> --reason "why"')
    if args.command == "belief" and args.action == "demote" and not (args.id and args.reason):
        sys.exit('usage: belief demote <id-prefix> --reason "why"')
    if args.command == "belief" and args.action == "merge" and not (args.id and args.answer):
        sys.exit("usage: belief merge <duplicate-id-prefix> <canonical-id-prefix>")
    if args.command == "belief" and args.action == "convert" and not (args.id and args.aspect):
        sys.exit("usage: belief convert <id-prefix> --aspect ASPECT "
                 "[--guide NAME | --file FILE]")
    import time as _time

    from . import dbperf, tracelog, usage

    def _trace(ok: bool, error: str | None = None) -> None:
        tracelog.record(
            args.command, surface="cli",
            workspace=getattr(args, "workspace", None),
            action=getattr(args, "action", None),
            manuscript=getattr(args, "manuscript", None),
            duration_ms=int((_time.monotonic() - t0) * 1000),
            ok=ok, error=error)
        # The database perf log's aggregate line for this invocation.
        # Here rather than in `main`, because every exit path out of the
        # verb — including the `sys.exit`s this module leans on — passes
        # through `_trace`. `dbperf`'s own atexit hook is the backstop.
        # Named by command+action, never raw argv: an intent statement is
        # a positional argument and does not belong in a telemetry log.
        label = " ".join(
            str(p) for p in (args.command, getattr(args, "action", None)) if p)
        dbperf.flush(label)
        # The usage ledger's aggregate line, on the same terms and named
        # by the same label.
        usage.flush(label)
        # And the chat sweep: opportunistic, rate-limited to one sweep per
        # session per [usage] sweep_interval_seconds, and scoped to the
        # chat that is right now driving AuthorLM. No daemon, no thread,
        # no cron — between sweeps the cost is one `stat`.
        usage.sweep_opportunistic(getattr(args, "workspace", None))

    # Resolve the client ONCE for this invocation, before the verb runs —
    # the per-invocation stamp, never a per-"current client" ambient read.
    clients.configure(
        surface="cli", workspace=getattr(args, "workspace", None),
        verb=" ".join(str(p) for p in (args.command,
                                       getattr(args, "action", None)) if p))

    t0 = _time.monotonic()
    try:
        args.func(args)
    except BrokenPipeError:
        # A downstream reader closed the pipe — `| head`, or quitting `less`
        # early. Ordinary use of a listing command, not an error.
        _trace(True)
        raise
    except BaseException as err:
        clean_exit = isinstance(err, SystemExit) and not err.code
        _trace(clean_exit, None if clean_exit
               else f"{type(err).__name__}: {err}")
        raise
    _trace(True)


def main(argv: list[str] | None = None) -> None:
    """Entry point. Wraps dispatch so a closed downstream pipe (`| head`,
    quitting `less`) exits quietly.

    BrokenPipeError has to be handled in TWO places, which is why the naive
    try/except around the command is not enough: it can surface while the
    command writes, but for short output it stays buffered and only surfaces
    when the interpreter flushes stdout at shutdown — printing "Exception
    ignored in: <_io.TextIOWrapper>" with no traceback and no way to catch
    it. Flushing here, inside our own guard, gives that flush nothing left
    to fail on; pointing the fd at devnull covers anything written later."""
    try:
        _dispatch(argv)
    except BrokenPipeError:
        _silence_stdout()
        sys.exit(0)
    try:
        sys.stdout.flush()
    except BrokenPipeError:
        _silence_stdout()
        sys.exit(0)


def _silence_stdout() -> None:
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
    except Exception:  # noqa: BLE001 - a captured stdout has no fileno()
        pass


if __name__ == "__main__":
    main()
