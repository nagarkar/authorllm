"""The application API — the single logic layer beneath every surface.

P11 (Common Core §1.12A): CLI, MCP server, and conversational skills are
logic-free wrappers over this module. Functions take a `Database` plus a
manuscript row (dict) and return plain dicts/lists that serialize cleanly
to JSON; errors are raised as ValueError/LookupError with author-readable
messages for the surface to present.

Anything not yet routed through here still lives in the module layer
(concepts, policies, proposals, sessions, docs, extraction, analysis) —
this facade composes those; it never duplicates them.
"""

from __future__ import annotations

import difflib
import json
import re
import sys
from pathlib import Path
from typing import Any

from . import concepts as cg
from . import policies as pol
from . import proposals as prop
from . import sessions as ses
from .analysis import analyze_pending, find_precedents
from .briefing import build_briefing
from .db import Database, ko_fields, loads
from .guidance import (GUIDANCE_KINDS, _GUIDANCE_KINDS_SQL,
                       compute_prerequisite_gaps, generate_guidance,
                       intent_coverage_notes)
from . import hygiene
from . import triage as triage_service
from .illus import slot_report as illus_slot_report
from .llm import LLMClient
from .revisions import (
    collect_revision, detect_transitions, massive_deletions, read_manuscript_files,
)

__all__ = [
    "open_db", "load_config", "make_llm",
    "list_manuscripts", "get_manuscript", "resolve_file",
    "status", "ensure_session", "close_session", "expire_idle_session",
    "declare_intent", "intent_preview", "complete_intent", "abandon_intent",
    "list_intents", "collect", "guide", "review", "get_briefing",
    "analyze", "diff_versions", "get_version", "restore_version",
    "list_concepts", "show_concept",
    "add_concept", "link_concepts", "confirm_concept", "retire_concept",
    "confirm_edge", "reject_edge", "list_proposals", "resolve_proposal",
    "list_policies", "run_extraction", "get_plan", "get_doc_links",
    "write_start", "write_plan", "write_status", "write_propose",
    "write_accept", "write_reject", "write_learn", "write_complete",
    "write_abandon", "get_profile",
]


# ------------------------------------------------------------- foundations

def open_db(workspace: str | None = None) -> Database:
    base = Path(workspace).resolve() if workspace else Path.home()
    data_dir = base / ".authorlm"
    data_dir.mkdir(parents=True, exist_ok=True)
    return Database(data_dir / "authorlm.db")


def load_config(workspace: str | None = None) -> dict:
    base = Path(workspace).resolve() if workspace else Path.home()
    toml_path = base / ".authorlm" / "config.toml"
    if toml_path.exists():
        import tomllib
        try:
            return tomllib.loads(toml_path.read_text())
        except tomllib.TOMLDecodeError as err:
            print(f"warning: could not parse {toml_path} ({err}); ignoring.",
                  file=sys.stderr)
    return {}


def make_llm(workspace: str | None = None) -> LLMClient:
    return LLMClient(load_config(workspace))


def list_manuscripts(db: Database) -> list[dict]:
    return [
        {"id": r["id"], "name": r["name"], "path": r["path"]}
        for r in db.all("SELECT * FROM manuscripts ORDER BY name")
    ]


def get_manuscript(db: Database, name: str | None = None) -> dict:
    if name:
        row = db.one("SELECT * FROM manuscripts WHERE name = ?", (name,))
        if not row:
            raise LookupError(f"no manuscript named '{name}'")
        return dict(row)
    rows = db.all("SELECT * FROM manuscripts")
    if not rows:
        raise LookupError("no manuscript registered")
    if len(rows) > 1:
        names = ", ".join(r["name"] for r in rows)
        raise LookupError(f"multiple manuscripts ({names}) — specify one")
    return dict(rows[0])


def resolve_file(db: Database, path: str) -> dict | None:
    """The registered manuscript whose directory contains `path` (or whose
    files include the bare filename)."""
    target = Path(path)
    for manuscript in list_manuscripts(db):
        root = Path(manuscript["path"])
        try:
            target.resolve().relative_to(root.resolve())
            return manuscript
        except ValueError:
            pass
        if not target.is_absolute() and (root / target).exists():
            return manuscript
        if target.name and (root / target.name).exists():
            return manuscript
    return None


# ------------------------------------------------------------------ state

def status(db: Database, manuscript: dict) -> dict:
    mid = manuscript["id"]
    session = ses.active_session(db, mid)
    counts = {
        table: db.one(
            f"SELECT COUNT(*) AS n FROM {table} WHERE manuscript_id = ?", (mid,)
        )["n"]
        for table in (
            "manuscript_versions", "editorial_transitions", "editorial_episodes",
            "declared_intents", "concept_nodes", "concept_edges",
            "editorial_policies", "guidance_history", "editorial_reviews",
            "evidence", "knowledge_proposals",
        )
    }
    return {
        "manuscript": {"name": manuscript["name"], "path": manuscript["path"]},
        "session": dict(session) if session else None,
        "counts": counts,
    }


def ensure_session(db: Database, manuscript: dict,
                   client_id: str | None = None) -> tuple[dict, bool]:
    """Active session, creating one lazily if needed (conversational
    surfaces bind on first substantive call). `client_id` is stamped into
    the session metadata for the audit trail."""
    session = ses.active_session(db, manuscript["id"])
    if session:
        return dict(session), False
    session = ses.start_session(db, manuscript["id"])
    if client_id:
        meta = loads(session["metadata"], {})
        meta["client_session"] = client_id
        db.update("sessions", session["id"], {"metadata": json.dumps(meta)})
        session = {**session, "metadata": json.dumps(meta)}
    return session, True


def close_session(db: Database, manuscript: dict, ended_at: str | None = None) -> dict:
    return ses.end_session(db, manuscript["id"], ended_at=ended_at)


def expire_idle_session(db: Database, manuscript: dict, config: dict) -> dict | None:
    """Close the active session retroactively if idle past the configured
    threshold. Returns {session, last_activity, idle_hours} when expired."""
    from datetime import datetime, timezone

    session = ses.active_session(db, manuscript["id"])
    if not session:
        return None
    idle_hours = config.get("session", {}).get("idle_hours", 3)
    candidates = [session["started_at"]]
    for table in ("manuscript_versions", "guidance_history", "declared_intents"):
        row = db.one(
            f"SELECT MAX(created_at) AS latest FROM {table} WHERE session_id = ?",
            (session["id"],),
        )
        if row and row["latest"]:
            candidates.append(row["latest"])
    last = max(candidates)
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            last_dt = datetime.strptime(last, fmt).replace(tzinfo=timezone.utc)
            break
        except ValueError:
            continue
    else:
        return None
    idle = (datetime.now(timezone.utc) - last_dt).total_seconds() / 3600
    if idle < idle_hours:
        return None
    ses.end_session(db, manuscript["id"], ended_at=last)
    return {"session": dict(session), "last_activity": last, "idle_hours": idle}


# ---------------------------------------------------------------- intents

def intent_preview(db: Database, manuscript: dict, statement: str) -> dict:
    """Deterministic, zero-token feedback for a declared intent: matched
    concepts with state, or fuzzy suggestions when nothing matches."""
    mid = manuscript["id"]
    nodes = [dict(n) for n in db.all(
        "SELECT * FROM concept_nodes WHERE manuscript_id = ? AND status != 'retired'",
        (mid,),
    )]
    if not nodes:
        return {"matched": [], "suggestions": [], "graph_empty": True}
    matched = []
    for node in nodes:
        if not any(cg.concept_pattern(nm).search(statement)
                   for nm in cg.node_names(node)):
            continue
        entry: dict[str, Any] = {
            "name": node["name"], "status": node["status"],
            "introduced_in": node["introduced_in"],
        }
        if node["status"] != "realized":
            neighbors = db.all(
                "SELECT id FROM concept_edges WHERE manuscript_id = ? "
                "AND status NOT IN ('rejected', 'retired') AND relation != 'co_occurs' "
                "AND (from_node = ? OR to_node = ?)",
                (mid, node["id"], node["id"]),
            )
            precedents = find_precedents(db, mid, node["id"], limit=1)
            entry["relationships"] = len(neighbors)
            entry["precedent"] = precedents[0]["label"] if precedents else None
        matched.append(entry)
    suggestions: list[str] = []
    if not matched:
        lower_map = {n["name"].lower(): n["name"] for n in nodes}
        for word in re.findall(r"[A-Za-z][A-Za-z-]{3,}", statement):
            for hit in difflib.get_close_matches(word.lower(), list(lower_map), n=1, cutoff=0.7):
                if lower_map[hit] not in suggestions:
                    suggestions.append(lower_map[hit])
    return {"matched": matched[:5], "suggestions": suggestions[:3], "graph_empty": False}


def declare_intent(db: Database, manuscript: dict, statement: str) -> dict:
    row = ses.declare_intent(db, manuscript["id"], statement)
    return {
        "intent": dict(row),
        "preview": intent_preview(db, manuscript, statement),
        "in_session": ses.active_session(db, manuscript["id"]) is not None,
    }


def _find_intent(db: Database, manuscript: dict, prefix: str) -> dict:
    rows = db.all(
        "SELECT * FROM declared_intents WHERE manuscript_id = ? AND id LIKE ?",
        (manuscript["id"], f"%{prefix}%"),
    )
    if not rows:
        raise LookupError(f"no intent matching '{prefix}'")
    if len(rows) > 1:
        raise LookupError(f"'{prefix}' is ambiguous ({len(rows)} intents)")
    return dict(rows[0])


def complete_intent(db: Database, manuscript: dict, prefix: str,
                    outcome: str | None, llm: LLMClient | None = None) -> dict:
    intent = _find_intent(db, manuscript, prefix)
    ses.complete_intent(db, intent, outcome)
    analysis = analyze_pending(db, manuscript, llm) if llm and llm.enabled else []
    return {"intent": intent, "analysis": analysis}


def abandon_intent(db: Database, manuscript: dict, prefix: str,
                   reason: str | None) -> dict:
    intent = _find_intent(db, manuscript, prefix)
    if intent["status"] != "active":
        raise ValueError(f"intent is already {intent['status']}")
    ses.abandon_intent(db, intent, reason)
    return {"intent": intent}


def list_intents(db: Database, manuscript: dict) -> list[dict]:
    return [dict(r) for r in db.all(
        "SELECT * FROM declared_intents WHERE manuscript_id = ? ORDER BY created_at",
        (manuscript["id"],),
    )]


# ---------------------------------------------------------------- collect

def collect(db: Database, manuscript: dict, config: dict,
            auto: bool = False, source: str = "snapshot",
            analyze: bool | None = None) -> dict:
    """The observation pipeline: snapshot → transitions → episode →
    realization/co-occurrence scans → extract hint → prerequisite delta.
    Returns a structured report; {"staged": [...]} when the auto path held
    a suspicious deletion; {"unchanged": True} when nothing changed."""
    mid = manuscript["id"]
    if auto:
        flagged = massive_deletions(db, manuscript)
        if flagged:
            return {"staged": [{"file": f, "removed_percent": p} for f, p in flagged]}

    # Ref-tag excerpts are machine-maintained BEFORE the snapshot, so
    # the collected version always holds canonical excerpts and a
    # discarded excerpt edit never masquerades as an authorial change.
    try:
        from .illus import maintain_excerpts

        excerpt_fixes = maintain_excerpts(Path(manuscript["path"]))
    except OSError:
        excerpt_fixes = []

    session = ses.active_session(db, mid)
    before = db.one(
        "SELECT * FROM manuscript_versions WHERE manuscript_id = ? "
        "ORDER BY version_no DESC LIMIT 1",
        (mid,),
    )
    version = collect_revision(db, manuscript, session["id"] if session else None,
                               source=source)
    if version is None:
        return {"unchanged": True}

    transitions = detect_transitions(db, mid, dict(before) if before else None, version)
    attached = False
    if session:
        episode = ses.current_episode(db, mid, dict(session))
        ses.attach_transitions(db, episode, transitions)
        attached = True
    realized = cg.scan_realizations(db, mid, version)
    repointed, vanished = cg.rescan_primary_locations(db, mid, version)
    # Summaries: mark, don't cascade. A changed unit's own row is already
    # stale by source_hash; downstream rows get the tolerated flag.
    from . import summaries as sums

    changed_files = sorted({t["location"].split("#", 1)[0] for t in transitions})
    sums.mark_changed(db, manuscript, changed_files)
    vanished_proposals = []
    hypotheses_dropped = []
    for node in vanished:
        meta = loads(node.get("metadata"), {})
        if meta.get("origin") == "extracted" and not meta.get("confirmed"):
            # An unconfirmed hypothesis was never knowledge: when its text
            # vanishes it dies quietly — the author must not be asked to
            # adjudicate the disappearance of something they never
            # accepted (it-8f24c757d3c7).
            cg.retire_concept(db, mid, dict(node))
            hypotheses_dropped.append(node["name"])
        else:
            created = prop.create(
                db, mid, "vanished", node["id"],
                {"name": node["name"], "was_in": node["introduced_in"]},
            )
            if created:
                vanished_proposals.append(node["name"])

    new_paragraphs = 0
    for transition in transitions:
        detail = loads(transition["detail"], {})
        if transition["kind"] == "file_added":
            new_paragraphs += detail.get("paragraphs", 0)
        elif transition["kind"] in ("insert", "rewrite"):
            text = detail.get("new_text", "")
            new_paragraphs += len([p for p in re.split(r"\n\s*\n", text) if p.strip()])
    extract_hint = new_paragraphs >= 5 and LLMClient(config).enabled

    gaps_before = (
        compute_prerequisite_gaps(db, mid, loads(before["files"], {})) if before else []
    )
    gaps_after = compute_prerequisite_gaps(db, mid, loads(version["files"], {}))
    before_keys = {g["edge_id"] for g in gaps_before}
    after_keys = {g["edge_id"] for g in gaps_after}

    report = {
        "version_no": version["version_no"],
        "checksum": version["checksum"],
        "transitions": [
            {"kind": t["kind"], "location": t["location"], "summary": t["summary"]}
            for t in transitions
        ],
        "attached_to_episode": attached,
        "new_files": [t["location"] for t in transitions
                      if t["kind"] == "file_added"],
        "realized": [
            {"name": n["name"], "introduced_in": n["introduced_in"]} for n in realized
        ],
        "repointed": repointed,
        "vanished": vanished_proposals,
        "hypotheses_dropped": hypotheses_dropped,
        "new_paragraphs": new_paragraphs,
        "extract_hint": extract_hint,
        "gaps_before": gaps_before,
        "gaps_after": gaps_after,
        "gaps_resolved": [g for g in gaps_before if g["edge_id"] not in after_keys],
        "gaps_new": [g for g in gaps_after if g["edge_id"] not in before_keys],
    }

    # Hygiene: suggestions this revision made moot (the author wrote the
    # concept a bridge suggestion proposed; a prerequisite gap closed) are
    # marked stale so review never offers a no-op — a no-op the author
    # rejects would poison the policy evidence.
    staled = hygiene.stale_suggestions(
        db, mid,
        realized_ids={n["id"] for n in realized},
        resolved_edge_ids={g["edge_id"] for g in report["gaps_resolved"]},
    )
    if staled:
        report["suggestions_stale"] = staled

    # Illustration slots are a scan-derived registry; a collect is the
    # moment the author learns about unrendered tags ("new illustrations
    # found") without having to remember to ask. Reporting only — collect
    # never renders anything. Exception: ref-tag excerpts are machine-
    # maintained here (regenerated from their canonical prompts file, so
    # drift is impossible; a discarded excerpt edit is called out).
    try:
        from .illus import externalize_offers

        slots = illus_slot_report(Path(manuscript["path"]))
        offers = externalize_offers(Path(manuscript["path"]))
    except OSError:
        slots, offers = None, []
    if slots and (slots["unrendered"] or slots["orphaned"]):
        report["illustrations"] = slots
    if excerpt_fixes or offers:
        report.setdefault("illustrations", {})
        report["illustrations"]["excerpt_fixes"] = excerpt_fixes
        report["illustrations"]["externalize_offers"] = offers

    # The author never has to remember the analyzers: a collected change
    # runs incremental extraction (concepts, links, aliasing statements)
    # when an LLM is configured. Best-effort — analysis failure must never
    # block observation.
    if analyze is None:
        analyze = auto
    if analyze:
        llm = LLMClient(config)
        if llm.enabled:
            try:
                summary = run_extraction(db, manuscript, llm) or {}
            except Exception:
                summary = {}
            if summary and not summary.get("up_to_date"):
                report["auto_analysis"] = {
                    "new_concepts": len(summary.get("nodes", [])),
                    "new_edges": len(summary.get("edges", [])),
                    "proposed": summary.get("proposed", 0),
                }
    return report


# ------------------------------------------------------- guidance / review

def guide(db: Database, manuscript: dict, session: dict,
          llm: LLMClient | None = None, config: dict | None = None) -> dict:
    caught_up = _catch_up(db, manuscript, config) if config is not None else None
    notes = intent_coverage_notes(db, manuscript)
    rows = generate_guidance(db, manuscript, session, llm=llm)
    result = {
        "notes": notes,
        "abstained": rows[0]["kind"] == "abstention",
        "suggestions": [dict(r) for r in rows],
    }
    if caught_up:
        result["caught_up"] = caught_up
    return result


def review(db: Database, manuscript: dict, session: dict, index: int,
           decision: str, explanation: str | None,
           llm: LLMClient | None = None,
           kinds: tuple[str, ...] | None = None) -> dict:
    if decision not in ("accepted", "rejected", "modified", "deferred"):
        raise ValueError(f"unknown decision '{decision}'")
    # Kind-scoped batches: guidance kinds by default; 'beat' rows from the
    # write loop and 'lens' findings live in this table too, each reviewed
    # through their own surface — an interleaved batch of another kind
    # must not hijack "the latest batch".
    kinds = kinds or GUIDANCE_KINDS
    kinds_sql = ", ".join("?" for _ in kinds)
    latest_batch = db.one(
        f"SELECT batch_id FROM guidance_history WHERE session_id = ? "
        f"AND kind IN ({kinds_sql}) "
        f"ORDER BY created_at DESC LIMIT 1",
        (session["id"], *kinds),
    )
    if not latest_batch:
        raise LookupError(
            "no lens findings in this session — run a lens first"
            if kinds == ("lens",) else
            "no guidance generated in this session — run guide first")
    guidance = db.one(
        "SELECT * FROM guidance_history WHERE batch_id = ? AND batch_index = ?",
        (latest_batch["batch_id"], index),
    )
    if not guidance:
        abstained = db.one(
            "SELECT id FROM guidance_history WHERE batch_id = ? AND kind = 'abstention'",
            (latest_batch["batch_id"],),
        )
        if abstained:
            raise LookupError("the latest output was an abstention; nothing to review")
        raise LookupError(f"no suggestion [{index}] in the latest batch")
    if guidance["state"] != "proposed":
        raise ValueError(f"suggestion [{index}] was already reviewed ({guidance['state']})")
    episode = db.one(
        "SELECT * FROM editorial_episodes WHERE session_id = ? AND status = 'open' "
        "ORDER BY created_at DESC LIMIT 1",
        (session["id"],),
    )
    result = pol.record_review(
        db, manuscript["id"], dict(guidance), decision, explanation,
        episode["id"] if episode else None, llm=llm,
    )
    return {"guidance": dict(guidance), **result}


# ------------------------------------------------------------- write loop
#
# The beat-by-beat co-writing loop (docs/autoregressive-writing-design.md).
# The author's collaborator (the skill) drafts; these verbs are the
# deterministic state machine and evidence channel: beat proposals are
# guidance_history rows (kind='beat', batch_id=writeup id, batch_index=the
# beat's stable n) so verdicts flow through record_review — evidence,
# policy reinforcement, and explanation-seeding — unchanged.

BEAT_KIND = "beat"


def _resolve_relpath(manuscript: dict, query: str) -> str:
    from .docs import _match
    from .revisions import iter_manuscript_paths

    candidates = iter_manuscript_paths(Path(manuscript["path"]))
    path = _match(candidates, query)
    return str(path.relative_to(Path(manuscript["path"])))


def _writeup(db: Database, manuscript: dict, prefix: str | None = None) -> dict:
    """The active writeup (or one matched by id prefix)."""
    if prefix:
        rows = db.all(
            "SELECT * FROM writeups WHERE manuscript_id = ? AND id LIKE ?",
            (manuscript["id"], f"%{prefix}%"),
        )
        if not rows:
            raise LookupError(f"no writeup matching '{prefix}'")
        if len(rows) > 1:
            raise LookupError(f"'{prefix}' is ambiguous ({len(rows)} writeups)")
        return dict(rows[0])
    rows = db.all(
        "SELECT * FROM writeups WHERE manuscript_id = ? AND status = 'active'",
        (manuscript["id"],),
    )
    if not rows:
        raise LookupError("no active writeup — start one: write start <file> --intent <id>")
    if len(rows) > 1:
        files = ", ".join(r["file"] for r in rows)
        raise LookupError(f"multiple active writeups ({files}) — pass --writeup")
    return dict(rows[0])


def _checkout_gate(db: Database, manuscript: dict, relpath: str) -> None:
    """The Google Doc is the working copy while a file is checked out —
    local writes during that window would be discarded by the next pull."""
    from .gdocs import doc_status

    entry = doc_status(db, manuscript).get(relpath)
    if isinstance(entry, dict) and entry.get("checked_out"):
        raise ValueError(
            f"{relpath} is checked out to Google Docs — the Doc is the "
            f"working copy. Run 'doc pull {relpath}' first."
        )


def _current_beat(writeup: dict) -> dict:
    plan = loads(writeup["plan"], [])
    if not plan:
        raise LookupError("no ratified beat plan — set one: write plan (JSON on stdin)")
    if writeup["cursor"] >= len(plan):
        raise LookupError("all planned beats are done — write complete (or "
                          "extend the plan: write plan --replace)")
    return plan[writeup["cursor"]]


def _beat_proposal(db: Database, writeup: dict, n: int):
    return db.one(
        "SELECT * FROM guidance_history WHERE batch_id = ? AND batch_index = ? "
        "AND state = 'proposed' ORDER BY created_at DESC LIMIT 1",
        (writeup["id"], n),
    )


def _beat_tallies(db: Database, writeup: dict) -> dict:
    rows = db.all(
        "SELECT state, COUNT(*) AS n FROM guidance_history "
        "WHERE batch_id = ? GROUP BY state",
        (writeup["id"],),
    )
    return {r["state"]: r["n"] for r in rows}


def write_start(db: Database, manuscript: dict, config: dict,
                file: str, intent_prefix: str) -> dict:
    """Initiate a fresh-drafting writeup: gate, pin the current version as
    raw material, truncate the file, and collect the honest 'removed'
    transition. The old text is never at risk — it lives in the pinned
    version and restores on abandon."""
    mid = manuscript["id"]
    relpath = _resolve_relpath(manuscript, file)
    intent = _find_intent(db, manuscript, intent_prefix)
    if intent["status"] != "active":
        raise ValueError(f"intent {intent['id']} is {intent['status']}, not active")
    attachment = db.one(
        "SELECT * FROM style_attachments WHERE manuscript_id = ? AND file = ?",
        (mid, relpath),
    )
    if not attachment:
        raise ValueError(
            f"{relpath} has no attached style guide — the effective guide is "
            f"the drafting law. Attach one first: style attach {relpath} <guide>."
        )
    _checkout_gate(db, manuscript, relpath)
    existing = db.one(
        "SELECT * FROM writeups WHERE manuscript_id = ? AND file = ? "
        "AND status = 'active'",
        (mid, relpath),
    )
    if existing:
        raise ValueError(f"writeup {existing['id']} is already active on {relpath}")

    ensure_session(db, manuscript)
    # Two collects: the first captures any uncollected edits so the pinned
    # source version is complete; the second records the truncation.
    collect(db, manuscript, config, source="write-start")
    source = db.one(
        "SELECT * FROM manuscript_versions WHERE manuscript_id = ? "
        "ORDER BY version_no DESC LIMIT 1",
        (mid,),
    )
    if source is None:
        raise LookupError("no collected version to pin — is the manuscript empty?")
    (Path(manuscript["path"]) / relpath).write_text("", encoding="utf-8")
    collect(db, manuscript, config, source="write-start")

    row = ko_fields("wu")
    row.update(
        manuscript_id=mid, intent_id=intent["id"], file=relpath, mode="fresh",
        status="active", source_version_id=source["id"], plan="[]",
        cursor=0, learnings="[]",
        metadata=json.dumps({"next_n": 1}),
    )
    db.insert("writeups", row)
    source_text = loads(source["files"], {}).get(relpath, "")
    return {"writeup": row, "intent": intent,
            "source_version_no": source["version_no"],
            "source_chars": len(source_text)}


def write_plan(db: Database, manuscript: dict, beats: list,
               prefix: str | None = None, replace: bool = False) -> dict:
    """Persist the ratified beat plan (ratification itself is
    conversational). Each spec gets a stable monotonic n, never reused, so
    replanning cannot corrupt recorded verdicts. With replace, beats before
    the cursor are kept and the remainder is replaced."""
    writeup = _writeup(db, manuscript, prefix)
    if writeup["status"] != "active":
        raise ValueError(f"writeup {writeup['id']} is {writeup['status']}")
    if not isinstance(beats, list) or not beats:
        raise ValueError("expected a non-empty JSON array of beat specs")
    plan = loads(writeup["plan"], [])
    if plan and not replace:
        raise ValueError("a plan exists — pass --replace to amend the "
                         "remaining (unwritten) beats")
    meta = loads(writeup["metadata"], {})
    next_n = meta.get("next_n", 1)
    fresh = []
    for spec in beats:
        if not isinstance(spec, dict):
            raise ValueError("each beat spec must be a JSON object "
                             '(e.g. {"role": "opener", "concepts": [...], '
                             '"budget": 120, "notes": "..."})')
        beat = {k: v for k, v in spec.items() if k != "n"}
        beat["n"] = next_n
        next_n += 1
        fresh.append(beat)
    kept = plan[:writeup["cursor"]] if replace else []
    new_plan = kept + fresh
    meta["next_n"] = next_n
    db.update("writeups", writeup["id"],
              {"plan": json.dumps(new_plan), "metadata": json.dumps(meta)})
    return {"writeup_id": writeup["id"], "kept": len(kept),
            "added": len(fresh), "plan": new_plan, "cursor": writeup["cursor"]}


def write_status(db: Database, manuscript: dict, prefix: str | None = None) -> dict:
    """The resume entry point: where the writeup stands, what's next."""
    writeup = _writeup(db, manuscript, prefix)
    plan = loads(writeup["plan"], [])
    cursor = writeup["cursor"]
    current = plan[cursor] if cursor < len(plan) else None
    pending = _beat_proposal(db, writeup, current["n"]) if current else None
    return {
        "writeup": {k: writeup[k] for k in
                    ("id", "intent_id", "file", "mode", "status",
                     "source_version_id", "cursor")},
        "plan": plan,
        "current_beat": current,
        "pending_proposal": dict(pending) if pending else None,
        "learnings": loads(writeup["learnings"], []),
        "tallies": _beat_tallies(db, writeup),
    }


def write_propose(db: Database, manuscript: dict, text: str, explanation: str,
                  prefix: str | None = None) -> dict:
    """Register a draft for the current beat as a reviewable item. The
    explanation is mandatory — which concepts the draft realizes, what
    precedent it follows — because that is what verdict evidence hangs off."""
    writeup = _writeup(db, manuscript, prefix)
    if writeup["status"] != "active":
        raise ValueError(f"writeup {writeup['id']} is {writeup['status']}")
    _checkout_gate(db, manuscript, writeup["file"])
    if not text or not text.strip():
        raise ValueError("no draft text on stdin")
    if not explanation or not explanation.strip():
        raise ValueError("--why is required: which concepts this draft "
                         "realizes, which precedent it follows, length vs budget")
    beat = _current_beat(writeup)
    session, _ = ensure_session(db, manuscript)
    # A redraft supersedes the pending proposal; reviewed rows are history.
    db.conn.execute(
        "UPDATE guidance_history SET state = 'superseded' "
        "WHERE batch_id = ? AND batch_index = ? AND state = 'proposed'",
        (writeup["id"], beat["n"]),
    )
    db.conn.commit()
    row = ko_fields("gd")
    row.update(
        manuscript_id=manuscript["id"], session_id=session["id"],
        intent_id=writeup["intent_id"], batch_id=writeup["id"],
        batch_index=beat["n"], kind=BEAT_KIND,
        suggestion=text.strip(), explanation=explanation.strip(),
        state="proposed",
    )
    db.insert("guidance_history", row)
    return {"writeup_id": writeup["id"], "beat": beat, "guidance_id": row["id"]}


def write_accept(db: Database, manuscript: dict, config: dict,
                 text: str | None = None, reason: str | None = None,
                 prefix: str | None = None,
                 llm: LLMClient | None = None) -> dict:
    """The atomic step 4: the accepted text is appended to the file, the
    revision is collected (transitions attach to the episode), the verdict
    is recorded through the review pathway, and the cursor advances. With
    reworded text on stdin the decision is 'modified' and the draft→final
    diff is recoverable (draft in the guidance row, final in the version)."""
    writeup = _writeup(db, manuscript, prefix)
    if writeup["status"] != "active":
        raise ValueError(f"writeup {writeup['id']} is {writeup['status']}")
    _checkout_gate(db, manuscript, writeup["file"])
    beat = _current_beat(writeup)
    proposal = _beat_proposal(db, writeup, beat["n"])
    if not proposal:
        raise LookupError(f"nothing proposed for beat {beat['n']} — "
                          "write propose first")
    accepted = (text if text and text.strip() else proposal["suggestion"]).strip()
    decision = "accepted" if accepted == proposal["suggestion"].strip() else "modified"

    path = Path(manuscript["path"]) / writeup["file"]
    existing = path.read_text(encoding="utf-8")
    prefix_text = existing.rstrip("\n") + "\n\n" if existing.strip() else ""
    path.write_text(prefix_text + accepted + "\n", encoding="utf-8")

    session, _ = ensure_session(db, manuscript)
    report = collect(db, manuscript, config, source="write-accept")
    if decision == "modified":
        meta = loads(proposal["metadata"], {})
        meta["accepted_text"] = accepted
        db.update("guidance_history", proposal["id"],
                  {"metadata": json.dumps(meta)})
    episode = ses.current_episode(db, manuscript["id"], session)
    review_result = pol.record_review(
        db, manuscript["id"], dict(proposal), decision, reason,
        episode["id"], llm=llm,
    )
    plan = loads(writeup["plan"], [])
    db.update("writeups", writeup["id"], {"cursor": writeup["cursor"] + 1})
    done = writeup["cursor"] + 1 >= len(plan)
    next_beat = plan[writeup["cursor"] + 1] if not done else None
    return {"decision": decision, "beat": beat, "collect": report,
            "review": review_result, "next_beat": next_beat,
            "plan_complete": done}


def write_reject(db: Database, manuscript: dict, reason: str,
                 prefix: str | None = None,
                 llm: LLMClient | None = None) -> dict:
    """Reject the pending draft. The reason is required at the tool level:
    an explained rejection is the highest-value evidence the system can
    receive, and beat rejections without a why teach nothing."""
    if not reason or not reason.strip():
        raise ValueError("--reason is required: the author's why, verbatim")
    writeup = _writeup(db, manuscript, prefix)
    if writeup["status"] != "active":
        raise ValueError(f"writeup {writeup['id']} is {writeup['status']}")
    beat = _current_beat(writeup)
    proposal = _beat_proposal(db, writeup, beat["n"])
    if not proposal:
        raise LookupError(f"nothing proposed for beat {beat['n']} — "
                          "write propose first")
    session, _ = ensure_session(db, manuscript)
    episode = ses.current_episode(db, manuscript["id"], session)
    review_result = pol.record_review(
        db, manuscript["id"], dict(proposal), "rejected", reason.strip(),
        episode["id"], llm=llm,
    )
    return {"beat": beat, "review": review_result,
            "note": "cursor unchanged — redraft with the reason in context"}


def write_learn(db: Database, manuscript: dict, lesson: str,
                prefix: str | None = None) -> dict:
    """Append a distilled session lesson (recorded only when a pattern
    recurs across verdicts). Session-local scaffolding: episode analysis at
    completion is what promotes recurring lessons through ratification."""
    if not lesson or not lesson.strip():
        raise ValueError("empty lesson")
    writeup = _writeup(db, manuscript, prefix)
    learnings = loads(writeup["learnings"], [])
    learnings.append(lesson.strip())
    db.update("writeups", writeup["id"], {"learnings": json.dumps(learnings)})
    return {"writeup_id": writeup["id"], "learnings": learnings}


def write_complete(db: Database, manuscript: dict, config: dict,
                   prefix: str | None = None) -> dict:
    """Close the writeup: final collect, then the extraction pass that was
    deferred during the loop (per-beat collects are deterministic-only).
    Intent completion stays a separate, conversational complete_intent."""
    writeup = _writeup(db, manuscript, prefix)
    if writeup["status"] != "active":
        raise ValueError(f"writeup {writeup['id']} is {writeup['status']}")
    report = collect(db, manuscript, config, source="write-complete")
    extraction = None
    llm = LLMClient(config)
    if llm.enabled:
        try:
            extraction = run_extraction(db, manuscript, llm)
        except Exception:
            extraction = None
    plan = loads(writeup["plan"], [])
    remaining = max(0, len(plan) - writeup["cursor"])
    db.update("writeups", writeup["id"], {"status": "completed"})
    return {"writeup_id": writeup["id"], "intent_id": writeup["intent_id"],
            "beats_done": writeup["cursor"], "beats_unwritten": remaining,
            "tallies": _beat_tallies(db, writeup),
            "learnings": loads(writeup["learnings"], []),
            "collect": report, "extraction": extraction}


def write_abandon(db: Database, manuscript: dict, config: dict,
                  prefix: str | None = None) -> dict:
    """Abandon the writeup and restore the file from the pinned source
    version — a truncated file with a dead writeup is the worst end state."""
    writeup = _writeup(db, manuscript, prefix)
    if writeup["status"] != "active":
        raise ValueError(f"writeup {writeup['id']} is {writeup['status']}")
    source = db.one("SELECT * FROM manuscript_versions WHERE id = ?",
                    (writeup["source_version_id"],))
    restored = False
    if source:
        content = loads(source["files"], {}).get(writeup["file"])
        if content is not None:
            (Path(manuscript["path"]) / writeup["file"]).write_text(
                content, encoding="utf-8")
            restored = True
    report = collect(db, manuscript, config, source="write-abandon")
    db.update("writeups", writeup["id"], {"status": "abandoned"})
    return {"writeup_id": writeup["id"], "restored": restored,
            "collect": report}


# ------------------------------------------------------------ other reads

def _catch_up(db: Database, manuscript: dict, config: dict) -> dict | None:
    """Lazy catch-up collection: state-reading entry points must never serve
    stale text. The shell has a live watcher; every other surface collects
    on read. Returns a small report when something was collected."""
    report = collect(db, manuscript, config, source="catch-up")
    if report.get("unchanged") or report.get("staged"):
        return None
    return {"version_no": report.get("version_no"),
            "transitions": report.get("transitions", []),
            "new_files": report.get("new_files", [])}


def get_briefing(db: Database, manuscript: dict, since: str | None = None,
                 config: dict | None = None) -> dict:
    caught_up = _catch_up(db, manuscript, config) if config is not None else None
    briefing = build_briefing(db, manuscript["id"], since=since)
    if caught_up:
        briefing["caught_up"] = caught_up
    return briefing


def analyze(db: Database, manuscript: dict, llm: LLMClient) -> list[dict]:
    return analyze_pending(db, manuscript, llm)


def diff_versions(db: Database, manuscript: dict, older: int | None = None,
                  newer: int | None = None, file_filter: str | None = None) -> dict:
    versions = db.all(
        "SELECT * FROM manuscript_versions WHERE manuscript_id = ? ORDER BY version_no",
        (manuscript["id"],),
    )
    if not versions:
        raise LookupError("no versions collected yet")
    by_no = {v["version_no"]: v for v in versions}
    for number in (older, newer):
        if number is not None and number not in by_no:
            raise LookupError(
                f"no version v{number} (have v1..v{versions[-1]['version_no']})")
    if older is not None and newer is not None:
        old_v, new_v = by_no[min(older, newer)], by_no[max(older, newer)]
    elif newer is not None or older is not None:
        number = newer if newer is not None else older
        new_v = by_no[number]
        old_v = by_no.get(number - 1)
    else:
        new_v = versions[-1]
        old_v = versions[-2] if len(versions) > 1 else None

    old_files = loads(old_v["files"], {}) if old_v else {}
    new_files = loads(new_v["files"], {})
    old_label = f"v{old_v['version_no']}" if old_v else "∅"
    new_label = f"v{new_v['version_no']}"
    files: dict[str, list[str]] = {}
    for name in sorted(set(old_files) | set(new_files)):
        if file_filter and file_filter.lower() not in name.lower():
            continue
        old_text, new_text = old_files.get(name, ""), new_files.get(name, "")
        if old_text == new_text:
            continue
        files[name] = list(difflib.unified_diff(
            old_text.splitlines(), new_text.splitlines(),
            fromfile=f"{old_label}/{name}", tofile=f"{new_label}/{name}",
            lineterm="",
        ))
    return {"old": old_label, "new": new_label, "files": files}


def get_version(db: Database, manuscript: dict, version_no: int) -> dict:
    """A single collected revision snapshot, by version number."""
    version = db.one(
        "SELECT * FROM manuscript_versions WHERE manuscript_id = ? AND version_no = ?",
        (manuscript["id"], version_no),
    )
    if not version:
        last = db.one(
            "SELECT version_no FROM manuscript_versions WHERE manuscript_id = ? "
            "ORDER BY version_no DESC LIMIT 1", (manuscript["id"],),
        )
        raise LookupError(
            f"no version v{version_no} (have v1..v{last['version_no'] if last else 0})")
    return version


def restore_version(db: Database, manuscript: dict, version_no: int, config: dict) -> dict:
    """Write a past version's files to disk and collect them as a new
    revision (MVP.md 'Deliberately deferred': history is never rewound,
    only advanced — restoration persists forward as a new snapshot)."""
    version = get_version(db, manuscript, version_no)
    files = loads(version["files"], {})
    root = Path(manuscript["path"])
    for name in set(read_manuscript_files(root)) - set(files):
        (root / name).unlink()
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return collect(db, manuscript, config, source=f"restore:v{version_no}")


# ----------------------------------------------------------- concept graph

def list_concepts(db: Database, manuscript: dict, include_all: bool = False) -> dict:
    mid = manuscript["id"]
    node_filter = "" if include_all else " AND status != 'retired'"
    nodes = [dict(n) for n in db.all(
        f"SELECT * FROM concept_nodes WHERE manuscript_id = ?{node_filter}", (mid,)
    )]
    edge_filter = "" if include_all else " AND status NOT IN ('rejected', 'retired')"
    edges = [
        {**dict(e),
         "from_name": cg.node_name(db, e["from_node"]),
         "to_name": cg.node_name(db, e["to_node"])}
        for e in db.all(
            f"SELECT * FROM concept_edges WHERE manuscript_id = ?{edge_filter}", (mid,)
        )
    ]
    return {"nodes": nodes, "edges": edges}


def show_concept(db: Database, manuscript: dict, name: str) -> dict:
    node = cg.get_concept(db, manuscript["id"], name)
    if not node:
        raise LookupError(f"no concept named '{name}'")
    edges = [
        {**dict(e),
         "from_name": cg.node_name(db, e["from_node"]),
         "to_name": cg.node_name(db, e["to_node"])}
        for e in db.all(
            "SELECT * FROM concept_edges WHERE manuscript_id = ? "
            "AND status NOT IN ('rejected', 'retired') "
            "AND (from_node = ? OR to_node = ?)",
            (manuscript["id"], node["id"], node["id"]),
        )
    ]
    return {"node": dict(node), "edges": edges}


def add_concept(db: Database, manuscript: dict, name: str,
                kind: str = "concept", notes: str | None = None) -> dict:
    return dict(cg.add_concept(db, manuscript["id"], name, kind=kind, notes=notes))


def alias_concept(db: Database, manuscript: dict, name: str,
                  aliases: list[str], remove: bool = False) -> dict:
    node = cg.get_concept(db, manuscript["id"], name)
    if not node:
        raise LookupError(f"no concept named '{name}'")
    node = dict(node)
    for alias in aliases:
        node = (cg.remove_alias(db, manuscript["id"], node, alias) if remove
                else cg.add_alias(db, manuscript["id"], node, alias))
    return {"name": node["name"], "aliases": cg.node_aliases(node)}


def style_overview(db: Database, manuscript: dict) -> dict:
    from . import styles as st

    mid = manuscript["id"]
    guides = [dict(g) for g in db.all(
        "SELECT * FROM style_guides WHERE manuscript_id = ? ORDER BY created_at", (mid,))]
    names = {g["id"]: g["name"] for g in guides}
    counts = {g["id"]: db.one(
        "SELECT COUNT(*) AS n FROM style_elements WHERE guide_id = ? AND status = 'active'",
        (g["id"],))["n"] for g in guides}
    attachments = [dict(a) for a in db.all(
        "SELECT * FROM style_attachments WHERE manuscript_id = ?", (mid,))]
    return {
        "guides": [{"id": g["id"], "name": g["name"],
                    "parent": names.get(g["parent"]),
                    "elements": counts[g["id"]]} for g in guides],
        "attachments": [{"file": a["file"], "guide": names.get(a["guide_id"])}
                        for a in attachments],
    }


def style_show(db: Database, manuscript: dict, file: str) -> dict:
    from . import styles as st

    elements = st.effective_style(db, manuscript["id"], file)
    return {"file": file, "elements": elements,
            "rendered": st.render(db, manuscript["id"], file)}


def define_style_guide(db: Database, manuscript: dict, name: str,
                       parent: str | None = None) -> dict:
    from . import styles as st

    return dict(st.create_guide(db, manuscript["id"], name, parent_name=parent))


def _validate_file(db: Database, manuscript: dict, file: str) -> None:
    """Live references (style attachments, file-local elements) must name a
    file the manuscript directory actually contains — there is no files
    table, so integrity is enforced at the point of entry, against disk."""
    from .revisions import read_manuscript_files

    known = sorted(read_manuscript_files(Path(manuscript["path"])).keys())
    if known and file not in known:
        hint = difflib.get_close_matches(file, known, n=1)
        raise LookupError(
            f"unknown file '{file}'"
            + (f" — did you mean '{hint[0]}'?" if hint else
               f" (known: {', '.join(known)})"))


def attach_style(db: Database, manuscript: dict, file: str, guide_name: str) -> dict:
    from . import styles as st

    guide = st.get_guide(db, manuscript["id"], guide_name)
    if not guide:
        raise LookupError(f"no style guide named '{guide_name}'")
    _validate_file(db, manuscript, file)
    st.attach_file(db, manuscript["id"], file, dict(guide))
    return {"file": file, "guide": guide["name"]}


def _find_style_element(db: Database, manuscript: dict, prefix: str,
                        status_clause: str, label: str) -> dict:
    """Prefix lookup with the same ambiguity guard as _find_intent/_find_edge/
    _policy_by_prefix: a prefix matching more than one row must never let
    the caller silently act on whichever row SQLite returns first."""
    rows = db.all(
        f"SELECT * FROM style_elements WHERE manuscript_id = ? AND id LIKE ? "
        f"AND {status_clause}",
        (manuscript["id"], f"%{prefix}%"),
    )
    if not rows:
        raise LookupError(f"no {label} style element matching '{prefix}'")
    if len(rows) > 1:
        raise LookupError(f"'{prefix}' is ambiguous ({len(rows)} style elements)")
    return dict(rows[0])


def add_style_element(db: Database, manuscript: dict, aspect: str, statement: str,
                      guide_name: str | None = None, file: str | None = None,
                      notes: str | None = None,
                      overrides: str | None = None) -> dict:
    from . import styles as st

    guide = None
    if guide_name:
        guide = st.get_guide(db, manuscript["id"], guide_name)
        if not guide:
            raise LookupError(f"no style guide named '{guide_name}'")
    if file is not None:
        _validate_file(db, manuscript, file)
    resolved = None
    if overrides:
        resolved = _find_style_element(
            db, manuscript, overrides, "status = 'active'", "active")["id"]
    return dict(st.add_element(
        db, manuscript["id"], aspect, statement,
        guide=dict(guide) if guide else None, file=file, notes=notes,
        overrides=resolved,
    ))


def retire_style_element(db: Database, manuscript: dict, prefix: str) -> dict:
    from . import styles as st

    row = _find_style_element(db, manuscript, prefix, "status = 'active'", "active")
    st.retire_element(db, row)
    return {"id": row["id"], "statement": row["statement"], "status": "retired"}


def move_style_element(db: Database, manuscript: dict, prefix: str,
                       guide_name: str | None = None,
                       file: str | None = None) -> dict:
    """Re-scope a style element to another guide or to a file (exactly
    one). The element keeps its id, status, provenance, and history."""
    if bool(guide_name) == bool(file):
        raise ValueError("move needs exactly one of guide_name / file")
    row = _find_style_element(db, manuscript, prefix, "status != 'retired'", "live")
    if guide_name:
        guide = db.one("SELECT * FROM style_guides WHERE manuscript_id = ? "
                       "AND name = ?", (manuscript["id"], guide_name))
        if not guide:
            raise LookupError(f"no style guide named '{guide_name}'")
        db.update("style_elements", row["id"],
                  {"guide_id": guide["id"], "file": None})
        return {"id": row["id"], "statement": row["statement"],
                "guide": guide["name"], "file": None}
    db.update("style_elements", row["id"], {"guide_id": None, "file": file})
    return {"id": row["id"], "statement": row["statement"], "guide": None,
            "file": file}


def merge_concepts(db: Database, manuscript: dict,
                   canonical_name: str, duplicate_name: str) -> dict:
    canonical = cg.get_concept(db, manuscript["id"], canonical_name)
    if not canonical:
        raise LookupError(f"no concept named '{canonical_name}'")
    duplicate = cg.get_concept(db, manuscript["id"], duplicate_name)
    if not duplicate:
        raise LookupError(f"no concept named '{duplicate_name}'")
    result = triage_service.apply_one(
        db, manuscript, "concepts", dict(duplicate), "alias",
        {"canonical_id": canonical["id"]})
    return {key: result[key] for key in
            ("canonical", "aliases", "repointed", "dropped")}


def link_concepts(db: Database, manuscript: dict, from_name: str,
                  relation: str, to_name: str) -> dict:
    return dict(cg.link_concepts(db, manuscript["id"], from_name, relation, to_name))


def confirm_concept(db: Database, manuscript: dict, name: str,
                    kind: str | None = None) -> dict:
    node = cg.get_concept(db, manuscript["id"], name)
    if not node:
        raise LookupError(f"no concept named '{name}'")
    action = "retype" if kind and kind != node["kind"] else "keep"
    triage_service.apply_one(
        db, manuscript, "concepts", dict(node), action,
        {"kind": kind} if action == "retype" else {})
    return {"name": node["name"], "kind": kind or node["kind"]}


def retire_concept(db: Database, manuscript: dict, name: str) -> dict:
    node = cg.get_concept(db, manuscript["id"], name)
    if not node:
        raise LookupError(f"no concept named '{name}'")
    if node["status"] == "retired":
        raise ValueError(f"'{node['name']}' is already retired")
    result = triage_service.apply_one(
        db, manuscript, "concepts", dict(node), "retire")
    return {"name": node["name"], "edges_retired": result["edges_retired"]}


def _find_edge(db: Database, manuscript: dict, prefix: str) -> dict:
    rows = db.all(
        "SELECT * FROM concept_edges WHERE manuscript_id = ? AND id LIKE ?",
        (manuscript["id"], f"%{prefix}%"),
    )
    if not rows:
        raise LookupError(f"no edge matching '{prefix}'")
    if len(rows) > 1:
        raise LookupError(f"'{prefix}' is ambiguous ({len(rows)} edges)")
    return dict(rows[0])


def confirm_edge(db: Database, manuscript: dict, edge_prefix: str,
                 relation: str | None = None) -> dict:
    edge = _find_edge(db, manuscript, edge_prefix)
    action = "retype" if relation and relation != edge["relation"] else "keep"
    triage_service.apply_one(
        db, manuscript, "edges", edge, action,
        {"relation": relation} if action == "retype" else {},
        record_evidence=False)
    return {
        "from_name": cg.node_name(db, edge["from_node"]),
        "relation": relation or edge["relation"],
        "to_name": cg.node_name(db, edge["to_node"]),
    }


def reject_edge(db: Database, manuscript: dict, edge_prefix: str) -> dict:
    edge = _find_edge(db, manuscript, edge_prefix)
    triage_service.apply_one(
        db, manuscript, "edges", edge, "reject", record_evidence=False)
    return {
        "from_name": cg.node_name(db, edge["from_node"]),
        "relation": edge["relation"],
        "to_name": cg.node_name(db, edge["to_node"]),
    }


# --------------------------------------------------------------- proposals

def list_proposals(db: Database, manuscript: dict) -> list[dict]:
    rows = prop.open_proposals(db, manuscript["id"])
    return [{**row, "summary": prop.describe(row)[0],
             "details": prop.describe(row)[1]} for row in rows]


def resolve_proposal(db: Database, manuscript: dict, prefix: str,
                     action: str, reason: str | None = None) -> dict:
    rows = [r for r in prop.open_proposals(db, manuscript["id"]) if prefix in r["id"]]
    if not rows:
        raise LookupError(f"no open proposal matching '{prefix}'")
    if len(rows) > 1:
        raise LookupError(f"'{prefix}' is ambiguous ({len(rows)} proposals)")
    row = rows[0]
    if action == "accept":
        message = prop.adopt(db, manuscript["id"], row)
    elif action == "edge":
        message = prop.demote_to_edge(db, manuscript["id"], row)
    elif action == "dismiss":
        message = prop.dismiss(db, manuscript["id"], row, reason=reason)
    else:
        raise ValueError(f"unknown action '{action}' (accept|edge|dismiss)")
    return {"message": message, "proposal_id": row["id"]}


# ----------------------------------------------------------------- others

def get_plan(db: Database, manuscript: dict, llm: LLMClient | None = None,
             draft: bool = False) -> dict:
    """The writing plan: placements for unrealized concepts from the graph
    and TOC order; optionally drafts stubs into _drafts/ (Level 2)."""
    from .plan import build_plan, draft_stubs

    result = build_plan(db, manuscript)
    if draft and llm and llm.enabled and result["items"]:
        result["drafts"] = draft_stubs(db, manuscript, llm, result["items"])
    return result


def get_profile(manuscript: dict, key: str | None = None) -> dict:
    """Declared context about the project (market intelligence,
    positioning, …) from _profiles/ — author-authored reference, returned
    VERBATIM (never compacted: these are the author's words). Consult for
    positioning/audience/publisher questions; never drafting law. Without
    `key`, lists the profiles on record."""
    root = Path(manuscript["path"]) / "_profiles"
    files = sorted(root.glob("*.md")) if root.exists() else []
    if key is None:
        return {"profiles": [
            {"key": f.stem,
             "words": len(f.read_text(encoding="utf-8").split())}
            for f in files]}
    name = key if key.endswith(".md") else f"{key}.md"
    path = root / name
    if not path.exists():
        available = ", ".join(f.stem for f in files) or "none yet"
        raise LookupError(f"no profile '{key}' — available: {available}")
    return {"key": path.stem, "content": path.read_text(encoding="utf-8")}


def get_doc_links(db: Database, manuscript: dict) -> dict:
    """Google Docs link state per file: doc id, URL, and whether the file
    is currently checked out (the Doc is the working copy). Pure DB read —
    no Google credentials touched."""
    from .gdocs import doc_status

    links = {}
    for relpath, entry in doc_status(db, manuscript).items():
        if relpath.startswith("_") or not isinstance(entry, dict):
            continue
        if entry.get("doc_id"):
            links[relpath] = {
                "doc_id": entry["doc_id"],
                "url": f"https://docs.google.com/document/d/{entry['doc_id']}/edit",
                "checked_out": bool(entry.get("checked_out")),
            }
    return {"links": links,
            "checked_out": [r for r, e in links.items() if e["checked_out"]]}


def list_policies(db: Database, manuscript: dict) -> list[dict]:
    rows = []
    for row in db.all(
        "SELECT * FROM editorial_policies WHERE manuscript_id = ? "
        "AND status != 'retired' ORDER BY confidence DESC",
        (manuscript["id"],),
    ):
        rows.append({**dict(row), "questions": loads(row["outstanding_questions"], [])})
    return rows


def _policy_by_prefix(db: Database, manuscript: dict, prefix: str) -> dict:
    rows = db.all(
        "SELECT * FROM editorial_policies WHERE manuscript_id = ? "
        "AND status != 'retired' AND id LIKE ?",
        (manuscript["id"], f"%{prefix}%"),
    )
    if not rows:
        raise LookupError(f"no live policy matching '{prefix}'")
    if len(rows) > 1:
        raise LookupError(f"'{prefix}' is ambiguous ({len(rows)} matches)")
    return dict(rows[0])


def retire_policy(db: Database, manuscript: dict, prefix: str,
                  reason: str) -> dict:
    from . import policies as pol

    policy = _policy_by_prefix(db, manuscript, prefix)
    return pol.retire_policy(db, manuscript["id"], policy, reason)


def merge_policies(db: Database, manuscript: dict, duplicate: str,
                   canonical: str, reason: str | None = None) -> dict:
    from . import policies as pol

    dup = _policy_by_prefix(db, manuscript, duplicate)
    canon = _policy_by_prefix(db, manuscript, canonical)
    if dup["id"] == canon["id"]:
        raise LookupError("duplicate and canonical are the same policy")
    return pol.merge_policies(db, manuscript["id"], dup, canon, reason)


def convert_policy(db: Database, manuscript: dict, prefix: str, aspect: str,
                   statement: str | None = None, guide: str | None = None,
                   file: str | None = None, notes: str | None = None,
                   reason: str | None = None) -> dict:
    from . import policies as pol

    policy = _policy_by_prefix(db, manuscript, prefix)
    element = add_style_element(
        db, manuscript, aspect, statement or policy["statement"],
        guide_name=guide, file=file, notes=notes)
    return pol.convert_policy(db, manuscript["id"], policy, element, reason)


# ---------------------------------------- compact projections (MCP surface)
# The conversational surface pays per token; the CLI renders full rows
# itself. These projections drop row boilerplate (version, created_at,
# created_by, schema_version, metadata, manuscript_id) and keep what a
# conversation can act on: names, kinds, statuses, and the ids the
# follow-up tools need (proposal / edge resolution). Full rows remain one
# verbose=True away.

_NOTE_PREVIEW = 160


def _preview(text: str | None) -> str | None:
    if not text:
        return None
    return text if len(text) <= _NOTE_PREVIEW else text[:_NOTE_PREVIEW - 1] + "…"


def _compact_concept(row: dict) -> dict:
    out = {"name": row["name"], "kind": row["kind"], "status": row["status"]}
    if row.get("introduced_in"):
        out["introduced_in"] = row["introduced_in"]
    notes = _preview(row.get("notes"))
    if notes:
        out["notes"] = notes
    aliases = loads(row.get("aliases"), [])
    if aliases:
        out["aliases"] = aliases
    return out


def _compact_edge(edge: dict) -> dict:
    return {"id": edge["id"],
            "edge": f"{edge['from_name']} —{edge['relation']}→ {edge['to_name']}",
            "status": edge["status"]}


def _compact_gap(gap: dict) -> dict:
    return {"edge_id": gap["edge_id"], "text": gap["text"],
            "status": gap["status"]}


def _compact_policy(policy: dict) -> dict:
    return {"id": policy["id"], "statement": policy["statement"],
            "status": policy["status"], "confidence": policy["confidence"],
            "support": f"{policy['supporting']}+/{policy['contradicting']}-"}


def compact_collect(report: dict) -> dict:
    """Delta-only projection of a collect report: gap counts plus the
    resolved/new gaps, never the full before/after lists."""
    if "version_no" not in report:
        return report  # {"unchanged": True} or {"staged": [...]}
    out = {key: report[key] for key in (
        "version_no", "checksum", "transitions", "attached_to_episode",
        "realized", "repointed", "vanished", "hypotheses_dropped",
        "new_paragraphs", "extract_hint",
    ) if key in report}
    out["gaps"] = {"before": len(report["gaps_before"]),
                   "after": len(report["gaps_after"])}
    out["gaps_resolved"] = [_compact_gap(g) for g in report["gaps_resolved"]]
    out["gaps_new"] = [_compact_gap(g) for g in report["gaps_new"]]
    if "auto_analysis" in report:
        out["auto_analysis"] = report["auto_analysis"]
    if "illustrations" in report:
        out["illustrations"] = report["illustrations"]
    if "suggestions_stale" in report:
        out["suggestions_stale"] = report["suggestions_stale"]
    return out


# A "compact" briefing must stay readable in one MCP response even when the
# triage backlog is huge (271 unconfirmed concepts once produced a 144KB
# "compact" result — the R6.3 anti-pattern). Counts stay exact; item lists
# carry at most this many rows plus a pointer to the bulk surface.
BRIEFING_HEAD = 15


def _head(items: list, more_hint: str) -> dict:
    """Bounded head of a briefing list: exact count, first BRIEFING_HEAD
    items, and where to see the rest when truncated."""
    out = {"count": len(items), "items": items[:BRIEFING_HEAD]}
    if len(items) > BRIEFING_HEAD:
        out["more"] = f"{len(items) - BRIEFING_HEAD} more — {more_hint}"
    return out


def compact_briefing(briefing: dict) -> dict:
    """Compact projection of the session briefing: counts + a bounded head
    of actionable items, no raw rows (bulk piles live on their dedicated
    surfaces: triage, list_proposals, get_plan)."""
    from collections import Counter

    out = {"since": briefing["since"]}
    if "caught_up" in briefing:
        out["caught_up"] = briefing["caught_up"]
    if briefing.get("profiles"):
        out["profiles"] = briefing["profiles"]
    out["policy_changes"] = briefing["policy_changes"]  # built compact
    out["new_policies"] = [_compact_policy(p) for p in briefing["new_policies"]]
    out["realized_concepts"] = {
        "count": len(briefing["realized_concepts"]),
        "names": [n["name"] for n in briefing["realized_concepts"]],
    }
    out["unconfirmed_concepts"] = _head(
        [_compact_concept(n) for n in briefing["unconfirmed_concepts"]],
        "triage with 'authorlm concept triage'")
    out["proposals"] = {
        **_head([{"id": p["id"], "kind": p["kind"], "summary": p["summary"]}
                 for p in briefing["proposals"]],
                "review via list_proposals"),
        "by_kind": dict(Counter(p["kind"] for p in briefing["proposals"])),
    }
    out["inferred_edges"] = _head(
        [_compact_edge(e) for e in briefing["inferred_edges"]],
        "triage with 'authorlm concept triage --edges'")
    out["contradictions"] = [
        {"suggestion": c["suggestion"], "explanation": c["explanation"]}
        for c in briefing["contradictions"]
    ]
    out["outstanding_questions"] = briefing["outstanding_questions"]
    out["active_intents"] = [
        {"id": i["id"], "statement": i["statement"], "status": i["status"]}
        for i in briefing["active_intents"]
    ]
    out["focus_areas"] = _head(
        [{"name": f["node"]["name"],
          "related": [f"{r['name']} ({r['relation']})" for r in f["related"]]}
         for f in briefing["focus_areas"]],
        "full plan via get_plan")
    out["toc_unlisted"] = briefing["toc_unlisted"]
    out["learning_velocity"] = briefing["learning_velocity"]
    return out


def compact_concepts(result: dict) -> dict:
    """Compact projection of the whole-graph listing."""
    return {
        "node_count": len(result["nodes"]),
        "nodes": [_compact_concept(dict(n)) for n in result["nodes"]],
        "edge_count": len(result["edges"]),
        "edges": [_compact_edge(e) for e in result["edges"]],
    }


def concept_overview(db: Database, manuscript: dict) -> dict:
    """The graph at a glance — counts by kind and status, edge count,
    recently added names — never the rows themselves. This is what an
    unscoped chat fetch gets: the full compact dump measured ~59K tokens
    on a mature graph, a third of a context window (ratified 2026-08-10:
    dumps impossible by construction; narrow with name=, file=, query=)."""
    mid = manuscript["id"]
    by_kind: dict[str, int] = {}
    by_status: dict[str, int] = {}
    for row in db.all(
            "SELECT kind, status, COUNT(*) AS n FROM concept_nodes "
            "WHERE manuscript_id = ? AND status != 'retired' "
            "GROUP BY kind, status", (mid,)):
        by_kind[row["kind"]] = by_kind.get(row["kind"], 0) + row["n"]
        by_status[row["status"]] = by_status.get(row["status"], 0) + row["n"]
    recent = [r["name"] for r in db.all(
        "SELECT name FROM concept_nodes WHERE manuscript_id = ? "
        "AND status != 'retired' ORDER BY created_at DESC LIMIT 12",
        (mid,))]
    edge_count = db.one(
        "SELECT COUNT(*) AS n FROM concept_edges WHERE manuscript_id = ? "
        "AND status NOT IN ('rejected', 'retired')", (mid,))["n"]
    return {
        "node_count": sum(by_kind.values()),
        "edge_count": edge_count,
        "by_kind": by_kind,
        "by_status": by_status,
        "recently_added": recent,
        "guidance": ("The graph is too large to list whole. Narrow with "
                     "name= (one concept, full notes), query= (name/notes/"
                     "alias match), or file= (concepts realized in an "
                     "essay)."),
    }


def scoped_concepts(db: Database, manuscript: dict,
                    file: str | None = None,
                    query: str | None = None) -> dict:
    """The relevant slice of the graph, compact. `query` matches name,
    notes, or aliases (case-insensitive substring). `file` selects
    concepts realized in that essay: introduced there, or whose name or
    alias appears in its text. Edges are restricted to the selected
    nodes."""
    mid = manuscript["id"]
    nodes = [dict(n) for n in db.all(
        "SELECT * FROM concept_nodes WHERE manuscript_id = ? "
        "AND status != 'retired'", (mid,))]
    if query:
        q = query.lower()
        nodes = [n for n in nodes
                 if q in n["name"].lower()
                 or q in (n.get("notes") or "").lower()
                 or any(q in a.lower()
                        for a in loads(n.get("aliases"), []))]
    if file:
        from .revisions import read_manuscript_files
        files = read_manuscript_files(Path(manuscript["path"]))
        rel = next((r for r in files
                    if r == file or r.endswith("/" + file)
                    or Path(r).name == file), None)
        if rel is None:
            raise LookupError(f"no manuscript file matching '{file}'")
        text = files[rel].lower()
        nodes = [n for n in nodes
                 if n.get("introduced_in") == rel
                 or n["name"].lower() in text
                 or any(a.lower() in text
                        for a in loads(n.get("aliases"), []))]
    ids = {n["id"] for n in nodes}
    edges = [
        {**dict(e),
         "from_name": cg.node_name(db, e["from_node"]),
         "to_name": cg.node_name(db, e["to_node"])}
        for e in db.all(
            "SELECT * FROM concept_edges WHERE manuscript_id = ? "
            "AND status NOT IN ('rejected', 'retired')", (mid,))
        if e["from_node"] in ids and e["to_node"] in ids]
    return compact_concepts({"nodes": nodes, "edges": edges})


def curate_concepts(db: Database, manuscript: dict,
                    operations: list[dict]) -> dict:
    """Batch graph curation for naturally-plural moments: an operations
    array of confirm / retire / link / reject_edge / alias, applied in
    order with per-op status — one failed op never blocks the rest.
    review_suggestion is deliberately NOT batchable: per-verdict
    explanations are the evidence stream (ratified 2026-08-10)."""
    results = []
    for op in operations:
        kind = op.get("op")
        try:
            if kind == "confirm":
                out = confirm_concept(db, manuscript, op["name"],
                                      kind=op.get("kind"))
            elif kind == "retire":
                out = retire_concept(db, manuscript, op["name"])
            elif kind == "revive":
                row = db.one(
                    "SELECT * FROM concept_nodes WHERE manuscript_id = ? AND "
                    "lower(name) = lower(?) AND status = 'retired'",
                    (manuscript["id"], op["name"]))
                if not row:
                    raise LookupError(f"no retired concept named '{op['name']}'")
                out = {"name": row["name"],
                       **cg.revive_concept(db, manuscript["id"], dict(row))}
            elif kind == "link":
                out = link_concepts(db, manuscript, op["from"],
                                    op["relation"], op["to"])
            elif kind == "reject_edge":
                out = reject_edge(db, manuscript, op["edge_id"])
            elif kind == "alias":
                out = alias_concept(db, manuscript, op["name"],
                                    op.get("aliases", []),
                                    remove=bool(op.get("remove")))
            else:
                raise ValueError(f"unknown op '{kind}' — expected confirm"
                                 " | retire | link | reject_edge | alias")
            results.append({"op": kind, "ok": True, "result": out})
        except Exception as err:
            results.append({"op": kind, "ok": False, "error": str(err)})
    return {"applied": sum(1 for r in results if r["ok"]),
            "failed": sum(1 for r in results if not r["ok"]),
            "results": results}


def compact_show_concept(result: dict) -> dict:
    """Single-concept detail: the node keeps full notes (they are the
    ratified definition); only row boilerplate and edge rows compact."""
    node = dict(result["node"])
    keep = {key: node[key]
            for key in ("id", "name", "kind", "status", "introduced_in", "notes")
            if node.get(key) is not None}
    aliases = loads(node.get("aliases"), [])
    if aliases:
        keep["aliases"] = aliases
    return {"node": keep, "edges": [_compact_edge(e) for e in result["edges"]]}


def run_extraction(db: Database, manuscript: dict, llm: LLMClient,
                   files: list[str] | None = None, full: bool = False,
                   edges_only: bool = False,
                   aliases_only: bool = False) -> dict | None:
    from .extraction import extract_concepts

    return extract_concepts(db, manuscript, llm, files=files, full=full,
                            edges_only=edges_only, aliases_only=aliases_only)


# ----------------------------------------------- self-improvement tasks

def file_improvement(db: Database, title: str, evidence: str, given: str,
                     observed: str, expected: str,
                     manuscript: dict | None = None) -> dict:
    """Record a confirmed AuthorLM defect as an improvement task, stamped
    with filing-time context (Time Machine): active session, current
    manuscript version, active intent, and the source tree's git commit.
    Tool-knowledge only: never surfaces in briefings or guidance."""
    from . import improvements as imp

    session = None
    context: dict = {}
    if manuscript:
        session = ses.active_session(db, manuscript["id"])
        version = db.one(
            "SELECT id, version_no FROM manuscript_versions WHERE "
            "manuscript_id = ? ORDER BY version_no DESC LIMIT 1",
            (manuscript["id"],))
        if version:
            context["manuscript_version_id"] = version["id"]
            context["manuscript_version_no"] = version["version_no"]
        intent = db.one(
            "SELECT id FROM declared_intents WHERE manuscript_id = ? AND "
            "status = 'active' ORDER BY created_at DESC LIMIT 1",
            (manuscript["id"],))
        if intent:
            context["intent_id"] = intent["id"]
    return imp.file_task(
        db, title=title, evidence=evidence, given=given, observed=observed,
        expected=expected,
        manuscript_id=manuscript["id"] if manuscript else None,
        session_id=session["id"] if session else None,
        context=context,
    )


def list_improvements(db: Database, status: str | None = None) -> list[dict]:
    from . import improvements as imp

    return imp.list_tasks(db, status)


def improvement_bundle(db: Database, prefix: str, mark: bool = True) -> dict:
    """The self-contained fix prompt for a task; `mark` advances an open
    task to in_progress (re-emitting a bundle never regresses status)."""
    from . import improvements as imp

    task = imp.find_task(db, prefix)
    if mark and task["status"] == "open":
        imp.transition(db, task, "run")
        task = imp.find_task(db, task["id"])
    return {"task_id": task["id"], "status": task["status"],
            "bundle": imp.bundle(db, task)}


def resolve_improvement(db: Database, prefix: str, action: str,
                        note: str | None = None) -> dict:
    """propose (fixing agent: fix summary + encoded test) | close (author
    confirms) | dismiss (reason mandatory, recorded as evidence)."""
    from . import improvements as imp

    if action not in ("propose", "close", "dismiss"):
        raise ValueError(f"unknown action '{action}' (propose|close|dismiss)")
    return imp.transition(db, imp.find_task(db, prefix), action, note)
