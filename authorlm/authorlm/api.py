"""The application API — the single logic layer beneath every surface.

P11 (Common Core §1.12A): CLI, MCP server, and conversational skills are
logic-free wrappers over this module. Functions take a `Database` plus a
manuscript row (dict) and return plain dicts/lists that serialize cleanly
to JSON; errors are raised as ValueError/LookupError with author-readable
messages for the surface to present.

Anything not yet routed through here still lives in the module layer
(concepts, beliefs, proposals, sessions, docs, extraction, analysis) —
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
from . import beliefs as bel
from . import proposals as prop
from . import sessions as ses
from .analysis import analyze_pending, find_precedents
from .briefing import build_briefing
from .db import Database, ko_fields, loads, now_iso
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
    "list_manuscripts", "get_manuscript", "register_manuscript",
    "manuscript_metadata",
    "update_manuscript_metadata", "resolve_file",
    "status", "ensure_session", "close_session", "expire_idle_session",
    "declare_intent", "intent_preview", "complete_intent", "abandon_intent",
    "list_intents", "collect", "guide", "review", "get_briefing",
    "analyze", "diff_versions", "get_version", "restore_version",
    "list_concepts", "show_concept",
    "add_concept", "link_concepts", "confirm_concept", "retire_concept",
    "confirm_edge", "reject_edge", "list_proposals", "reconcile_proposals", "screen_proposals",
    "resolve_proposal",
    "list_beliefs", "run_extraction", "get_plan", "get_doc_links",
    "write_start", "write_plan", "write_status", "write_propose",
    "write_accept", "write_reject", "write_learn", "write_complete",
    "write_abandon", "write_digest", "get_profile",
]


# ------------------------------------------------------------- foundations

def open_db(workspace: str | None = None) -> Database:
    base = Path(workspace).resolve() if workspace else Path.home()
    data_dir = base / ".authorlm"
    data_dir.mkdir(parents=True, exist_ok=True)
    return Database(data_dir / "authorlm.db")


def load_config(workspace: str | None = None) -> dict:
    """Load the project's config.toml, matching cli._load_config exactly.

    `workspace` is accepted for signature compatibility with callers that
    also pass it to `open_db` (workspace state and project config are two
    different homes — see authorlm/paths.py) but is otherwise unused:
    config resolution goes through `paths.config_path()` (the
    AUTHORLM_CONFIG override, else the project's config.toml) after
    `paths.load_env()` populates the environment from .env. This used to
    diverge from cli._load_config, which read this project config all
    along — the divergence silently left the LLM off on the MCP server
    and the triage app while the CLI had it on (ORCH-3)."""
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


def make_llm(workspace: str | None = None) -> LLMClient:
    return LLMClient(load_config(workspace))


def list_manuscripts(db: Database) -> list[dict]:
    return [
        manuscript_metadata(dict(r))
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


def register_manuscript(db: Database, name: str, path: str,
                        author: str = "",
                        copyright_owner: str = "",
                        paperback_isbn: str = "",
                        hardcover_isbn: str = "") -> dict:
    """Register one manuscript and its canonical publication identity."""
    root = Path(path).resolve()
    if not root.is_dir():
        raise ValueError(f"{root} is not a directory")
    if db.one("SELECT id FROM manuscripts WHERE name = ?", (name,)):
        raise ValueError(f"manuscript '{name}' is already registered")
    paperback_isbn = _normalize_isbn13(paperback_isbn)
    hardcover_isbn = _normalize_isbn13(hardcover_isbn)
    _validate_format_isbns(paperback_isbn, hardcover_isbn)
    row = ko_fields("ms")
    row.update(
        name=name, path=str(root), author=author.strip(),
        copyright_owner=copyright_owner.strip(),
        paperback_isbn=paperback_isbn,
        hardcover_isbn=hardcover_isbn)
    db.insert("manuscripts", row)
    return dict(row)


def manuscript_metadata(manuscript: dict) -> dict:
    """The manuscript identity shared by publication and every surface."""
    return {
        "id": manuscript["id"],
        "name": manuscript["name"],
        "path": manuscript["path"],
        "author": manuscript.get("author", ""),
        "copyright_owner": manuscript.get("copyright_owner", ""),
        "paperback_isbn": manuscript.get("paperback_isbn", ""),
        "hardcover_isbn": manuscript.get("hardcover_isbn", ""),
    }


def _normalize_isbn13(value: str) -> str:
    """Canonical ISBN-13 digits, with syntax and checksum validation."""
    value = value.strip()
    if not value:
        return ""
    if not re.fullmatch(r"[0-9 -]+", value):
        raise ValueError("ISBN must contain only digits, spaces, or hyphens")
    digits = re.sub(r"[ -]", "", value)
    if len(digits) != 13:
        raise ValueError("ISBN must contain exactly 13 digits")
    if not digits.startswith(("978", "979")):
        raise ValueError("ISBN-13 must begin with 978 or 979")
    total = sum(int(digit) * (1 if index % 2 == 0 else 3)
                for index, digit in enumerate(digits[:12]))
    if (10 - total % 10) % 10 != int(digits[-1]):
        raise ValueError("ISBN-13 checksum is invalid")
    return digits


def _validate_format_isbns(paperback_isbn: str,
                           hardcover_isbn: str) -> None:
    """Each physical format must have its own ISBN when both are present."""
    if paperback_isbn and paperback_isbn == hardcover_isbn:
        raise ValueError("paperback and hardcover ISBNs must be different")


def update_manuscript_metadata(db: Database, manuscript: dict,
                               author: str | None = None,
                               copyright_owner: str | None = None,
                               paperback_isbn: str | None = None,
                               hardcover_isbn: str | None = None) -> dict:
    """Update publication identity without exposing the internal KO metadata."""
    changes = {}
    if author is not None:
        changes["author"] = author.strip()
    if copyright_owner is not None:
        changes["copyright_owner"] = copyright_owner.strip()
    if paperback_isbn is not None:
        changes["paperback_isbn"] = _normalize_isbn13(paperback_isbn)
    if hardcover_isbn is not None:
        changes["hardcover_isbn"] = _normalize_isbn13(hardcover_isbn)
    if not changes:
        raise ValueError(
            "provide --author, --copyright-owner, --paperback-isbn, "
            "and/or --hardcover-isbn")
    _validate_format_isbns(
        changes.get("paperback_isbn", manuscript.get("paperback_isbn", "")),
        changes.get("hardcover_isbn", manuscript.get("hardcover_isbn", "")))
    db.update("manuscripts", manuscript["id"], changes)
    manuscript.update(changes)
    return manuscript_metadata(manuscript)


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
            "editorial_beliefs", "guidance_history", "editorial_reviews",
            "evidence", "knowledge_proposals",
        )
    }
    return {
        "manuscript": manuscript_metadata(manuscript),
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
    if intent["status"] != "active":
        raise ValueError(f"intent is already {intent['status']}")
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

def _deprecate_departed_summaries(db: Database, manuscript: dict, sums) -> list[str]:
    """A summary whose file has left the toc is never deleted — it is
    the record of an essay that existed — but it must not linger
    ambiguously as if it were still live. `essay_summaries.status`
    ('current' | 'deprecated') marks it so nothing mistakes it for a
    stale current summary.

    While the file stays gone, sums.status()/before_after() never see
    the row at all: both are driven off the CURRENT toc reading order.
    The mark earns its keep when the file COMES BACK — possibly
    byte-identical, so its source_hash still matches. Both functions now
    read this column (summaries._state) and report 'deprecated' rather
    than 'fresh'; the drafting and edit gates refuse it, and a rebuild
    resurrects it to 'current'. Idempotent: only rows not already
    'deprecated' are touched."""
    current = {f for f, _ in sums.units(manuscript)}
    deprecated = []
    for file, row in sums.all_summaries(db, manuscript["id"]).items():
        if file not in current and row["status"] != "deprecated":
            db.update("essay_summaries", row["id"], {"status": "deprecated"})
            deprecated.append(file)
    return deprecated


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
    deprecated_summaries = _deprecate_departed_summaries(db, manuscript, sums)
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
    # rejects would poison the belief evidence.
    staled = hygiene.stale_suggestions(
        db, mid,
        realized_ids={n["id"] for n in realized},
        resolved_edge_ids={g["edge_id"] for g in report["gaps_resolved"]},
    )
    if staled:
        report["suggestions_stale"] = staled
    if deprecated_summaries:
        report["summaries_deprecated"] = deprecated_summaries

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
    if slots and (slots["unrendered"] or slots["orphaned"]
                 or slots["malformed_refs"]):
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
            except Exception as err:
                summary = {}
                from . import tracelog

                tracelog.record(
                    "extraction", surface="api",
                    workspace=str(db.path.parent.parent),
                    manuscript=manuscript.get("name"),
                    ok=False, error=f"{type(err).__name__}: {err}")
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
    result = bel.record_review(
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
# belief reinforcement, and explanation-seeding — unchanged.

BEAT_KIND = "beat"

# The modeled-rewrite digest (UC-B, design §13.2): the four lists it may
# carry, mapped to each list's own required text field. The digest is
# METADATA, never a guidance kind — it is proposed by nobody, reviewed by
# nobody, superseded by nobody, so GUIDANCE_KINDS stays untouched.
DIGEST_LISTS = {"points": "claim", "examples": "example",
                "references": "reference", "inconsistencies": "note"}
# Provenance the verb writes and the skill may not forge. Accepted at the
# top level (a re-submitted digest carries them) and always overwritten.
DIGEST_PROVENANCE = ("source_version_id", "source_file", "recorded_at",
                     "point_count")
DIGEST_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,32}$")
# Two values only. A third ("moved", "deferred") is where enum-shaped
# schemas rot; the nuance belongs in the free-text reason, which is what
# the "What Was Removed and Why" section quotes anyway.
DISPOSITIONS = ("kept", "removed")


def _resolve_relpath(manuscript: dict, query: str) -> str:
    from .docs import _match
    from .revisions import iter_manuscript_paths

    candidates = iter_manuscript_paths(Path(manuscript["path"]))
    path = _match(candidates, query)
    return str(path.relative_to(Path(manuscript["path"])))


def _resolve_or_new(manuscript: dict, query: str, new: bool) -> str:
    """`_resolve_relpath`, except that `--new` targets a file that does
    NOT exist yet (design §13.1).

    `docs._match` resolves by exact relpath else unique case-insensitive
    substring — deliberately forgiving. If an unmatched name silently
    meant "create it", every typo would become a new essay file plus an
    active writeup plus a placement decision. So creation is explicit,
    and a name that already resolves is refused rather than rewritten.
    No disk write happens here."""
    from .docs import doc_filename

    if not new:
        return _resolve_relpath(manuscript, query)
    try:
        rel = _resolve_relpath(manuscript, query)
    except LookupError:
        # No single existing file answers to this name — which is exactly
        # what --new asserts. The name is normalized the way `doc add`
        # normalizes it, so a file the loop creates is named like a file
        # the author creates.
        rel = doc_filename(query)
        if (Path(manuscript["path"]) / rel).exists():
            raise ValueError(
                f"{rel} already exists — drop --new to rewrite it "
                f"(fresh mode truncates and rebuilds, §9.4).") from None
        return rel
    raise ValueError(f"{rel} already exists — drop --new to rewrite it "
                     f"(fresh mode truncates and rebuilds, §9.4).")


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


def _drafting_context(db: Database, manuscript: dict, writeup: dict) -> str:
    """The writeup's L1 book-frame (design §12.4 item 1): compressed
    summaries of the settled essays before this one and the upcoming
    ones after it. Recomputed on every read rather than stored — the
    summaries themselves are the source of truth and a rebuild between
    beats must show through. The declared placement, however, IS stored
    (§12.4 item 3), so a resumed writeup on a not-yet-placed essay
    recomputes the same split without the author repeating the flag."""
    from . import summaries as sums

    placement = loads(writeup["metadata"], {}).get("placement")
    return sums.drafting_context(db, manuscript, writeup["file"],
                                 placement=placement)


def _status_drafting_context(db: Database, manuscript: dict,
                             writeup: dict) -> str:
    """`write status` is the resume entry point, so it degrades rather
    than dies: if the file (or the placement target) has left the disk,
    the context is replaced by a one-line note and the plan, cursor,
    pending proposal and tallies still render. write_start does NOT get
    this treatment — its gate has already proved the context resolves."""
    from . import summaries as sums

    try:
        return _drafting_context(db, manuscript, writeup)
    except LookupError as err:
        return (f"{sums.WARN_PREFIX}DRAFTING CONTEXT unavailable — {err}. "
                f"Restore the file, or close the writeup with "
                f"'write abandon' (it restores the pinned source version).")


def _resolve_placement(manuscript: dict, after: str | None) -> str | None:
    """The `--after` argument as a placement for summaries.before_after:
    the sentinel passes through, anything else resolves to a real
    manuscript-relative path so the author can type a fragment."""
    from . import summaries as sums

    if not after or after == sums.PLACEMENT_START:
        return after or None
    return _resolve_relpath(manuscript, after)


def write_start(db: Database, manuscript: dict, config: dict,
                file: str, intent_prefix: str,
                after: str | None = None, brief: str | None = None,
                new: bool = False, style: str | None = None) -> dict:
    """Initiate a fresh-drafting writeup: gate, pin the current version as
    raw material, truncate the file, and collect the honest 'removed'
    transition. The old text is never at risk — it lives in the pinned
    version and restores on abandon.

    With `new` the target does not exist yet (UC-A, design §13.1): the
    file is created empty and `style` is attached to it — AFTER every
    gate has passed, because a blocked start must leave the disk exactly
    as it was (RISK K1). The pinned version therefore does not contain
    the file at all, which is what makes `write abandon`'s restore target
    (nonexistence) honest.

    `brief` is the author's one paragraph. Required with `new`: a
    brand-new file has no pinned raw material, so the brief is the only
    essay-specific grounding the beats have."""
    from . import styles as st

    mid = manuscript["id"]
    relpath = _resolve_or_new(manuscript, file, new)
    placement = _resolve_placement(manuscript, after)
    brief = (brief or "").strip()
    if style and not new:
        raise ValueError(
            f"--style is only for --new; an existing file's guide is "
            f"attached with 'style attach {relpath} {style}'.")
    intent = _find_intent(db, manuscript, intent_prefix)
    if intent["status"] != "active":
        raise ValueError(f"intent {intent['id']} is {intent['status']}, not active")
    guide = None
    if new:
        # `style attach` cannot run first for a file that does not exist:
        # attach_style -> _validate_file checks the name against disk, and
        # that integrity rule is not weakened here. So the guide is named
        # at start and validated before anything is created.
        if not style:
            raise ValueError(
                f"{relpath} has no attached style guide — the effective "
                f"guide is the drafting law. With --new, name it: "
                f"--style <guide>.")
        guide = st.get_guide(db, mid, style)
        if not guide:
            raise LookupError(f"no style guide named '{style}'")
    else:
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
    if new and not placement:
        # A file that does not exist is not in toc.toml, so once created it
        # is UNLISTED and summaries.before_after would refuse it — but with
        # a message advising a toc.toml entry for a file that is not on
        # disk. Say the true thing instead, and say it BEFORE the file is
        # created (RISK K1).
        from . import summaries as _sums

        raise ValueError(
            f"{relpath} does not exist yet, so it has no place in the "
            f"reading order — say where it goes: --after <file it follows> "
            f"| --after {_sums.PLACEMENT_START}.")
    if new and not brief:
        # LAST of the flag gates, deliberately. Every other refusal names a
        # flag the author can add to the command they just typed; this one
        # asks them to go and compose a paragraph. A gate parade typed at a
        # terminal has no stdin at all, so checking the brief first would
        # mask the placement and style refusals behind "give me a brief"
        # and the author would discover them one round trip at a time
        # (design §5.5, MT-5). Still before the pin: nothing has been
        # written yet, so K1 is untouched by the move.
        raise ValueError(
            "write start --new requires the one-paragraph brief on stdin — "
            "it is the only essay-specific ground a new file has "
            "(design §13.1).")
    # The summary freshness gate (design §12.4 item 2), on the same
    # predicate the critique pass uses: the before/after summaries ARE the
    # drafting context now, so a missing or stale one is a lie about the
    # text here exactly as it is there — and, as there, no --force. It runs
    # LAST among the gates but still before the pin/truncate/collect
    # sequence: a blocked start must leave the file untouched.
    from . import passes

    ready = passes.summaries_ready(db, manuscript, relpath,
                                   placement=placement)
    if not ready["ok"]:
        raise ValueError(passes.summaries_message(ready, "drafting pass"))

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
    # Create (--new) or truncate. Either way the file's pre-writeup state
    # is already inside the pinned version above.
    (Path(manuscript["path"]) / relpath).write_text("", encoding="utf-8")
    if new:
        attach_style(db, manuscript, relpath, guide["name"])
    collect(db, manuscript, config, source="write-start")

    row = ko_fields("wu")
    row.update(
        manuscript_id=mid, intent_id=intent["id"], file=relpath, mode="fresh",
        status="active", source_version_id=source["id"], plan="[]",
        cursor=0, learnings="[]",
        metadata=json.dumps({"next_n": 1, "placement": placement,
                             "created_file": bool(new),
                             "brief": brief or None}),
    )
    db.insert("writeups", row)
    source_text = loads(source["files"], {}).get(relpath, "")
    return {"writeup": row, "intent": intent,
            "source_version_no": source["version_no"],
            "source_chars": len(source_text),
            "created": bool(new), "brief": brief or None,
            "style": guide["name"] if guide else None,
            "drafting_context": _drafting_context(db, manuscript, row)}


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
    if replace:
        # Replacement beats get FRESH n's, so a draft still pending on a
        # beat about to be dropped would be orphaned: _beat_proposal
        # could never match it again, `write status` would never show it,
        # and it would count as 'proposed' in the tallies forever — while
        # the author's reason for replanning, the highest-value evidence
        # the loop can receive, went unrecorded (usability-analysis §2.7).
        # Refuse and name both exits. Deliberately NOT auto-superseded:
        # that would tidy the tally while still discarding the evidence.
        dropped = [b.get("n") for b in plan[writeup["cursor"]:]
                   if b.get("n") is not None]
        stranded = [
            row for row in db.all(
                "SELECT * FROM guidance_history WHERE batch_id = ? "
                "AND state = 'proposed'", (writeup["id"],))
            if row["batch_index"] in dropped
        ]
        if stranded:
            beats = ", ".join(f"n={row['batch_index']}" for row in stranded)
            raise ValueError(
                f"a draft is still awaiting your verdict on beat {beats} — "
                f"replacing the plan would strand it as 'proposed' forever "
                f"and lose the reason you are replanning for. Settle it "
                f"first: 'write reject --reason \"<why the plan is wrong>\"' "
                f"(that reason IS the evidence for the replan), or "
                f"'write accept' to keep the draft.")
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


def _validate_digest(db: Database, manuscript: dict, payload) -> dict:
    """The modeled-rewrite digest schema (design §13.2), validated
    deterministically and ALL-OR-NOTHING (RISK K8: a digest that
    half-persists on a mid-list error leaves the accounting silently
    wrong). Returns the normalized digest — missing lists defaulted to
    [], item order and unknown ITEM-level keys preserved verbatim, since
    the skill may carry structure the CLI has no opinion about.

    Unknown TOP-LEVEL keys are refused: a typo'd "referances" must not
    silently discard the references."""
    if not isinstance(payload, dict):
        raise ValueError(
            'the digest must be a JSON object with "points" (and optionally '
            '"examples", "references", "inconsistencies")')
    unknown = sorted(k for k in payload
                     if k not in DIGEST_LISTS and k not in DIGEST_PROVENANCE)
    if unknown:
        raise ValueError(
            f"unknown top-level key(s) in the digest: {', '.join(unknown)} — "
            f"expected only: {', '.join(DIGEST_LISTS)}")

    digest: dict = {}
    owner: dict[str, str] = {}          # id -> the list it came from
    for name, field in DIGEST_LISTS.items():
        items = payload.get(name, [])
        if name == "points":
            if not isinstance(items, list) or not items:
                raise ValueError(
                    '"points" must be a non-empty array — a digest with no '
                    "points cannot account for anything")
        elif not isinstance(items, list):
            raise ValueError(f'"{name}" must be an array')
        normalized = []
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                raise ValueError(f"{name}[{index}] must be a JSON object")
            item_id = item.get("id")
            if not isinstance(item_id, str) or not DIGEST_ID_RE.match(item_id):
                raise ValueError(
                    f'{name}[{index}] needs a string "id" matching '
                    f"[A-Za-z0-9_.-] and 1-32 characters long "
                    f"(got {item_id!r})")
            if item_id in owner:
                raise ValueError(
                    f"id '{item_id}' is used twice ({owner[item_id]} and "
                    f"{name}) — ids must be unique across the whole digest, "
                    f"because the removal accounting references them")
            owner[item_id] = name
            text = item.get(field)
            if not isinstance(text, str) or not text.strip():
                raise ValueError(
                    f'{name}[{index}] ({item_id}) needs a non-empty "{field}"')
            normalized.append(dict(item))
        digest[name] = normalized

    point_ids = {item["id"] for item in digest["points"]}

    def _check_refs(name: str, index: int, entry: dict, key: str) -> None:
        item_id = entry["id"]
        refs = entry.get(key)
        if refs is None:
            return
        if not isinstance(refs, list):
            raise ValueError(f'{name}[{index}] ({item_id}): "{key}" must be '
                             f"an array of point ids")
        for ref in refs:
            if not isinstance(ref, str) or ref not in owner:
                raise ValueError(
                    f"{name}[{index}] ({item_id}) references '{ref}', which "
                    f"is not in the digest — a dangling cross-reference "
                    f"makes the accounting decorative")
            if ref not in point_ids:
                raise ValueError(
                    f"{name}[{index}] ({item_id}) references '{ref}', which "
                    f"is a {owner[ref]} entry — cross-references must name "
                    f"point ids")

    for name in ("examples", "references"):
        for index, item in enumerate(digest[name]):
            _check_refs(name, index, item, "serves")
    for index, item in enumerate(digest["inconsistencies"]):
        item_id = item["id"]
        _check_refs("inconsistencies", index, item, "points")
        with_file = item.get("with")
        if not isinstance(with_file, str):
            raise ValueError(
                f'inconsistencies[{index}] ({item_id}) needs a string "with" '
                f"— the file it contradicts, or \"\" for an internal one")
        if with_file.strip():
            # A contradiction "with an essay that does not exist" is not a
            # finding, it is a typo.
            try:
                item["with"] = _resolve_relpath(manuscript, with_file)
            except LookupError as err:
                raise ValueError(
                    f"inconsistencies[{index}] ({item_id}): {err}") from None
    return digest


def _accounting(writeup: dict) -> dict | None:
    """kept / removed / unaccounted point ids for a writeup carrying a
    digest, or None when it carries none. Shared by write_digest --show,
    write_status and write_complete so the tally is spelled one way."""
    meta = loads(writeup["metadata"], {})
    digest = meta.get("digest")
    if not digest:
        return None
    dispositions = meta.get("dispositions") or {}
    ids = [item["id"] for item in digest.get("points", [])]
    return {
        "kept": [i for i in ids
                 if (dispositions.get(i) or {}).get("disposition") == "kept"],
        "removed": [i for i in ids
                    if (dispositions.get(i) or {}).get("disposition") == "removed"],
        "unaccounted": [i for i in ids if i not in dispositions],
        "point_count": len(ids),
        "claims": {item["id"]: item["claim"]
                   for item in digest.get("points", [])},
    }


def _validate_dispositions(writeup: dict, digest: dict, payload) -> dict:
    """Per-point dispositions (design §13.2). `removed` requires a reason
    for the same reason `write reject` does: an unexplained removal
    teaches nothing and cannot be written up."""
    if not isinstance(payload, dict) or not payload:
        raise ValueError(
            'expected a JSON object of {"<point id>": {"disposition": '
            '"kept"|"removed", "reason": "…", "beat": n}}')
    point_ids = {item["id"] for item in digest.get("points", [])}
    other_ids = {item["id"] for name in ("examples", "references",
                                         "inconsistencies")
                 for item in digest.get(name, [])}
    plan_ns = {beat.get("n") for beat in loads(writeup["plan"], [])}
    clean: dict = {}
    for point_id, record in payload.items():
        if point_id not in point_ids:
            if point_id in other_ids:
                raise ValueError(
                    f"'{point_id}' is not a point id — dispositions account "
                    f"for points only")
            raise ValueError(f"'{point_id}' is not a point in the stored "
                             f"digest")
        if not isinstance(record, dict):
            raise ValueError(f"{point_id}: expected an object with "
                             f'"disposition"')
        disposition = record.get("disposition")
        if disposition not in DISPOSITIONS:
            raise ValueError(
                f"{point_id}: disposition must be one of "
                f"{', '.join(DISPOSITIONS)} (got {disposition!r})")
        reason = record.get("reason")
        reason = reason.strip() if isinstance(reason, str) else ""
        if disposition == "removed" and not reason:
            raise ValueError(
                f"{point_id}: a removal needs a reason — an unexplained "
                f"removal teaches nothing and cannot be written up in "
                f"'What Was Removed and Why'")
        entry = {"disposition": disposition}
        if reason:
            entry["reason"] = reason
        beat = record.get("beat")
        if beat is not None:
            if isinstance(beat, bool) or not isinstance(beat, int) \
                    or beat not in plan_ns:
                raise ValueError(
                    f"{point_id}: beat {beat!r} is not an n in this "
                    f"writeup's plan")
            entry["beat"] = beat
        clean[point_id] = entry
    return clean


def write_digest(db: Database, manuscript: dict, payload=None,
                 dispositions=None, prefix: str | None = None,
                 replace: bool = False, show: bool = False) -> dict:
    """The deterministic channel to the pinned original (UC-B).

    Four shapes, one verb: bare it PRINTS the pinned source text (the file
    on disk was truncated at start; the old essay lives in
    `source_version_id`); with JSON on stdin it PERSISTS the digest; with
    dispositions it MERGES per-point verdicts; with `show` it reports what
    is stored. Refused outright on a writeup that created its file —
    there is no source essay to digest."""
    writeup = _writeup(db, manuscript, prefix)
    if writeup["status"] != "active":
        raise ValueError(f"writeup {writeup['id']} is {writeup['status']}")
    meta = loads(writeup["metadata"], {})
    source = db.one("SELECT * FROM manuscript_versions WHERE id = ?",
                    (writeup["source_version_id"],))
    source_text = (loads(source["files"], {}).get(writeup["file"], "")
                   if source else "")
    # Two conditions, two truths. Collapsing them into one message told a
    # rewrite over a blank source that "this writeup created" its file,
    # which it did not — a small lie about the writeup's own history, in
    # the one message whose whole job is to say what kind of writeup this
    # is. `created_file` is the discriminator, never emptiness.
    if meta.get("created_file"):
        raise ValueError(
            f"this writeup created {writeup['file']}; there is no source "
            f"essay to digest. A digest models a REWRITE of an existing "
            f"essay (design §13.2).")
    if not source_text.strip():
        raise ValueError(
            f"{writeup['file']}'s pinned source is empty; there is nothing "
            f"to digest. A digest models a REWRITE of an existing essay "
            f"(design §13.2).")

    if show:
        return {"mode": "show", "writeup_id": writeup["id"],
                "file": writeup["file"], "digest": meta.get("digest"),
                "accounting": _accounting(writeup)}

    if dispositions is not None:
        digest = meta.get("digest")
        if not digest:
            raise ValueError(
                "no digest recorded yet — record it first: write digest "
                "(JSON on stdin)")
        clean = _validate_dispositions(writeup, digest, dispositions)
        stored = meta.get("dispositions") or {}
        # Merge, not replace, later wins — and report the overwrite. A
        # re-recorded id is a real editorial change of mind, and hiding it
        # would be dishonest.
        overwritten = []
        for point_id, entry in clean.items():
            if point_id in stored and stored[point_id] != entry:
                overwritten.append(
                    f"{point_id}: {stored[point_id]['disposition']} → "
                    f"{entry['disposition']}")
            stored[point_id] = entry
        meta["dispositions"] = stored
        db.update("writeups", writeup["id"], {"metadata": json.dumps(meta)})
        writeup["metadata"] = json.dumps(meta)
        return {"mode": "dispositions", "writeup_id": writeup["id"],
                "recorded": len(clean),
                "kept": sum(1 for e in clean.values()
                            if e["disposition"] == "kept"),
                "removed": sum(1 for e in clean.values()
                               if e["disposition"] == "removed"),
                "overwritten": overwritten,
                "accounting": _accounting(writeup)}

    if payload is not None:
        digest = _validate_digest(db, manuscript, payload)
        existing = meta.get("digest")
        if existing and not replace:
            raise ValueError(
                "a digest is already recorded for this writeup — pass "
                "--replace to replace it")
        if existing:
            # RISK K7: --replace is the only way to lose recorded work.
            recorded = meta.get("dispositions") or {}
            new_ids = {item["id"] for item in digest["points"]}
            lost = sorted(i for i in recorded if i not in new_ids)
            if lost:
                raise ValueError(
                    f"refusing to replace: {', '.join(lost)} already have "
                    f"recorded dispositions and are absent from the new "
                    f"digest")
        digest["source_version_id"] = writeup["source_version_id"]
        digest["source_file"] = writeup["file"]
        digest["recorded_at"] = now_iso()
        digest["point_count"] = len(digest["points"])
        meta["digest"] = digest
        db.update("writeups", writeup["id"], {"metadata": json.dumps(meta)})
        writeup["metadata"] = json.dumps(meta)
        return {"mode": "recorded", "writeup_id": writeup["id"],
                "digest": digest, "replaced": bool(existing),
                "accounting": _accounting(writeup)}

    return {"mode": "source", "writeup_id": writeup["id"],
            "file": writeup["file"], "source_text": source_text,
            "source_version_no": source["version_no"],
            "source_chars": len(source_text)}


def write_status(db: Database, manuscript: dict, prefix: str | None = None) -> dict:
    """The resume entry point: where the writeup stands, what's next.

    The brief and the removal accounting are reprinted here on EVERY
    resume, which is what makes the tally at `write complete` unsurprising
    (design §13.2: an author who ignores it at completion has ignored it
    repeatedly, deliberately)."""
    writeup = _writeup(db, manuscript, prefix)
    plan = loads(writeup["plan"], [])
    cursor = writeup["cursor"]
    current = plan[cursor] if cursor < len(plan) else None
    pending = _beat_proposal(db, writeup, current["n"]) if current else None
    meta = loads(writeup["metadata"], {})
    return {
        "writeup": {k: writeup[k] for k in
                    ("id", "intent_id", "file", "mode", "status",
                     "source_version_id", "cursor")},
        "plan": plan,
        "current_beat": current,
        "pending_proposal": dict(pending) if pending else None,
        "learnings": loads(writeup["learnings"], []),
        "tallies": _beat_tallies(db, writeup),
        "brief": meta.get("brief"),
        "created_file": bool(meta.get("created_file")),
        "digest": meta.get("digest"),
        "accounting": _accounting(writeup),
        "drafting_context": _status_drafting_context(db, manuscript, writeup),
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
    review_result = bel.record_review(
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
    review_result = bel.record_review(
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
    Intent completion stays a separate, conversational complete_intent.

    Two things happen before that collect. A writeup that created its file
    registers it in toc.toml at the declared placement — without it the
    essay is finished, on disk, and structurally invisible, and the next
    pass in its neighbourhood is refused for a file the author believes is
    done. And a writeup carrying a digest gets its removal accounting
    computed and PERSISTED, so the number is in history rather than only
    in the author's scrollback. The accounting warns; it never blocks
    (design §13.2)."""
    from . import structure as struct

    writeup = _writeup(db, manuscript, prefix)
    if writeup["status"] != "active":
        raise ValueError(f"writeup {writeup['id']} is {writeup['status']}")
    meta = loads(writeup["metadata"], {})
    path = Path(manuscript["path"]) / writeup["file"]
    placement = meta.get("placement")
    toc_registered = None
    toc_stanza = None
    if (meta.get("created_file") and placement and path.exists()
            and path.read_text(encoding="utf-8").strip()):
        toc_path = Path(manuscript["path"]) / struct.TOC_FILENAME
        toc_text = (toc_path.read_text(encoding="utf-8")
                    if toc_path.exists() else "")
        outcome = struct.insert_toc_entry(toc_text, writeup["file"], placement)
        if outcome is None:
            toc_registered = False
            # No anchor found, so no parent to carry: the stanza the author
            # pastes is the minimal honest one.
            toc_stanza = f'[[chapter]]\nfile = "{writeup["file"]}"\n'
        else:
            new_text, toc_stanza = outcome
            # The toc write must precede the collect below, so the
            # structural change lands in the same version as the prose.
            if new_text != toc_text:
                toc_path.write_text(new_text, encoding="utf-8")
            toc_registered = True
    accounting = _accounting(writeup)
    if accounting is not None:
        meta["accounting_at_complete"] = {
            "kept": len(accounting["kept"]),
            "removed": len(accounting["removed"]),
            "unaccounted": accounting["unaccounted"],
        }
        db.update("writeups", writeup["id"], {"metadata": json.dumps(meta)})
    report = collect(db, manuscript, config, source="write-complete")
    extraction = None
    llm = LLMClient(config)
    if llm.enabled:
        try:
            extraction = run_extraction(db, manuscript, llm)
        except Exception as err:
            extraction = None
            from . import tracelog

            tracelog.record(
                "extraction", surface="api",
                workspace=str(db.path.parent.parent),
                manuscript=manuscript.get("name"),
                ok=False, error=f"{type(err).__name__}: {err}")
    plan = loads(writeup["plan"], [])
    remaining = max(0, len(plan) - writeup["cursor"])
    db.update("writeups", writeup["id"], {"status": "completed"})
    return {"writeup_id": writeup["id"], "intent_id": writeup["intent_id"],
            "beats_done": writeup["cursor"], "beats_unwritten": remaining,
            "tallies": _beat_tallies(db, writeup),
            "learnings": loads(writeup["learnings"], []),
            "accounting": accounting, "toc_registered": toc_registered,
            "toc_stanza": toc_stanza, "toc_placement": placement,
            "summary_hint": writeup["file"],
            "collect": report, "extraction": extraction}


def write_abandon(db: Database, manuscript: dict, config: dict,
                  prefix: str | None = None) -> dict:
    """Abandon the writeup and restore the file from the pinned source
    version — a truncated file with a dead writeup is the worst end state.

    For a writeup that CREATED its file (UC-A) the restore target is
    nonexistence, so the file is deleted. The pinned version does not
    contain the file at all; without this branch the restore silently
    did nothing and reported 'source version missing' — technically true,
    entirely misleading, and it left the file behind."""
    # Snapshot whatever the author has on disk right now — even a
    # half-typed, never-collected draft — before it gets overwritten by
    # the restored source text below (BUG-2 / A1: a destructive write
    # must never be the first thing that observes the current state).
    # RISK K2: this stays the FIRST statement. Deleting above it would
    # take an author's uncollected text with no recovery point at all.
    collect(db, manuscript, config, source="pre-write-abandon")
    writeup = _writeup(db, manuscript, prefix)
    if writeup["status"] != "active":
        raise ValueError(f"writeup {writeup['id']} is {writeup['status']}")
    path = Path(manuscript["path"]) / writeup["file"]
    if loads(writeup["metadata"], {}).get("created_file"):
        preserved = db.one(
            "SELECT version_no FROM manuscript_versions WHERE manuscript_id = ? "
            "ORDER BY version_no DESC LIMIT 1", (manuscript["id"],))
        had_text = path.exists() and bool(
            path.read_text(encoding="utf-8").strip())
        path.unlink(missing_ok=True)
        # "Restore to nonexistence" includes the attachment: `write start
        # --new` wrote that style_attachments row itself, so unwinding the
        # writeup unwrites it. Leaving it behind orphans a live reference
        # to a file that is not on disk — the exact integrity rule
        # api._validate_file exists to keep. ONLY for a created file; a
        # rewrite's attachment predates the writeup and is not its to
        # remove.
        removed = db.conn.execute(
            "DELETE FROM style_attachments WHERE manuscript_id = ? "
            "AND file = ?", (manuscript["id"], writeup["file"])).rowcount
        db.conn.commit()
        # This collect records the file_removed transition, and any concept
        # whose primary location was this file raises a `vanished` proposal.
        # That noise is the honest result — the same noise §9.4 accepts for
        # a truncate-and-rebuild.
        report = collect(db, manuscript, config, source="write-abandon")
        db.update("writeups", writeup["id"], {"status": "abandoned"})
        return {"writeup_id": writeup["id"], "restored": False,
                "deleted": True, "had_text": had_text,
                "attachment_removed": bool(removed),
                "preserved_in_version": (preserved["version_no"]
                                         if preserved else None),
                "file": writeup["file"], "collect": report}
    source = db.one("SELECT * FROM manuscript_versions WHERE id = ?",
                    (writeup["source_version_id"],))
    restored = False
    if source:
        content = loads(source["files"], {}).get(writeup["file"])
        if content is not None:
            path.write_text(content, encoding="utf-8")
            restored = True
    report = collect(db, manuscript, config, source="write-abandon")
    db.update("writeups", writeup["id"], {"status": "abandoned"})
    return {"writeup_id": writeup["id"], "restored": restored,
            "deleted": False, "had_text": False,
            "attachment_removed": False,
            "preserved_in_version": None, "file": writeup["file"],
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
            "new_files": report.get("new_files", []),
            # A catch-up runs with no human present (get_briefing /
            # get_guidance) — vanished concepts and quietly-retired
            # hypotheses must reach the author here or nowhere
            # (it-258752ea91f9).
            "vanished": report.get("vanished", []),
            "hypotheses_dropped": report.get("hypotheses_dropped", [])}


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
    # Snapshot the current disk state FIRST: the loop below unlinks and
    # overwrites files unconditionally, and any uncollected work sitting
    # on disk would otherwise be destroyed with no recovery point
    # (BUG-2 / A1). A no-op when there is nothing uncollected.
    collect(db, manuscript, config, source=f"pre-restore:v{version_no}")
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
    edge_filter = "" if include_all else " AND ce.status NOT IN ('rejected', 'retired')"
    edges = [dict(e) for e in db.all(
        "SELECT ce.*, fn.name AS from_name, tn.name AS to_name "
        "FROM concept_edges ce "
        "JOIN concept_nodes fn ON fn.id = ce.from_node "
        "JOIN concept_nodes tn ON tn.id = ce.to_node "
        f"WHERE ce.manuscript_id = ?{edge_filter}", (mid,)
    )]
    return {"nodes": nodes, "edges": edges}


def show_concept(db: Database, manuscript: dict, name: str) -> dict:
    node = cg.get_concept(db, manuscript["id"], name)
    if not node:
        raise LookupError(f"no concept named '{name}'")
    edges = [dict(e) for e in db.all(
        "SELECT ce.*, fn.name AS from_name, tn.name AS to_name "
        "FROM concept_edges ce "
        "JOIN concept_nodes fn ON fn.id = ce.from_node "
        "JOIN concept_nodes tn ON tn.id = ce.to_node "
        "WHERE ce.manuscript_id = ? "
        "AND ce.status NOT IN ('rejected', 'retired') "
        "AND (ce.from_node = ? OR ce.to_node = ?)",
        (manuscript["id"], node["id"], node["id"]),
    )]
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
    counts = {g["id"]: 0 for g in guides}
    counts.update({row["guide_id"]: row["n"] for row in db.all(
        "SELECT guide_id, COUNT(*) AS n FROM style_laws "
        "WHERE manuscript_id = ? AND status = 'active' AND guide_id IS NOT NULL "
        "GROUP BY guide_id",
        (mid,),
    )})
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


def _find_style_law(db: Database, manuscript: dict, prefix: str,
                        status_clause: str, label: str) -> dict:
    """Prefix lookup with the same ambiguity guard as _find_intent/_find_edge/
    _policy_by_prefix: a prefix matching more than one row must never let
    the caller silently act on whichever row SQLite returns first."""
    rows = db.all(
        f"SELECT * FROM style_laws WHERE manuscript_id = ? AND id LIKE ? "
        f"AND {status_clause}",
        (manuscript["id"], f"%{prefix}%"),
    )
    if not rows:
        raise LookupError(f"no {label} style element matching '{prefix}'")
    if len(rows) > 1:
        raise LookupError(f"'{prefix}' is ambiguous ({len(rows)} style laws)")
    return dict(rows[0])


def add_style_law(db: Database, manuscript: dict, aspect: str, statement: str,
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
        resolved = _find_style_law(
            db, manuscript, overrides, "status = 'active'", "active")["id"]
    return dict(st.add_element(
        db, manuscript["id"], aspect, statement,
        guide=dict(guide) if guide else None, file=file, notes=notes,
        overrides=resolved,
    ))


def retire_style_law(db: Database, manuscript: dict, prefix: str) -> dict:
    from . import styles as st

    row = _find_style_law(db, manuscript, prefix, "status = 'active'", "active")
    st.retire_element(db, row)
    return {"id": row["id"], "statement": row["statement"], "status": "retired"}


def move_style_law(db: Database, manuscript: dict, prefix: str,
                       guide_name: str | None = None,
                       file: str | None = None) -> dict:
    """Re-scope a style element to another guide or to a file (exactly
    one). The element keeps its id, status, provenance, and history."""
    if bool(guide_name) == bool(file):
        raise ValueError("move needs exactly one of guide_name / file")
    row = _find_style_law(db, manuscript, prefix, "status != 'retired'", "live")
    if guide_name:
        guide = db.one("SELECT * FROM style_guides WHERE manuscript_id = ? "
                       "AND name = ?", (manuscript["id"], guide_name))
        if not guide:
            raise LookupError(f"no style guide named '{guide_name}'")
        db.update("style_laws", row["id"],
                  {"guide_id": guide["id"], "file": None})
        return {"id": row["id"], "statement": row["statement"],
                "guide": guide["name"], "file": None}
    db.update("style_laws", row["id"], {"guide_id": None, "file": file})
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
        {"relation": relation} if action == "retype" else {})
    return {
        "from_name": cg.node_name(db, edge["from_node"]),
        "relation": relation or edge["relation"],
        "to_name": cg.node_name(db, edge["to_node"]),
    }


def reject_edge(db: Database, manuscript: dict, edge_prefix: str) -> dict:
    edge = _find_edge(db, manuscript, edge_prefix)
    triage_service.apply_one(db, manuscript, "edges", edge, "reject")
    return {
        "from_name": cg.node_name(db, edge["from_node"]),
        "relation": edge["relation"],
        "to_name": cg.node_name(db, edge["to_node"]),
    }


# --------------------------------------------------------------- proposals

def list_proposals(db: Database, manuscript: dict, kind: str | None = None,
                   proposal_id: str | None = None, belief: str | None = None,
                   limit: int | None = None,
                   verbose: bool = False) -> dict:
    """Open proposals plus a one-line-per-belief summary of what the screen
    folded away.

    Compact by default because the full queue is not survivable: 762 open
    proposals with full payloads serialise to ~580KB (~145K tokens), so the
    unfiltered verbose form was a tool no conversation could call. `belief`
    expands one fold — the only way contradicting evidence ever reaches a
    belief that is actively cutting (see loop.py, invariant 2)."""
    from . import loop

    mid = manuscript["id"]
    if belief:
        rows = [dict(r) for r in db.all(
            "SELECT * FROM knowledge_proposals WHERE manuscript_id = ? "
            "AND state = 'dismissed' "
            "AND json_extract(metadata, '$.law') = ?", (mid, belief))]
        return {"expanded": belief,
                "proposals": [_proposal_row(r, verbose=True) for r in rows],
                "count": len(rows)}

    rows = prop.open_proposals(db, mid)
    if proposal_id:
        rows = [r for r in rows if proposal_id in r["id"]]
        verbose = True
    if kind:
        rows = [r for r in rows if r["kind"] == kind]
    by_kind: dict[str, int] = {}
    for r in rows:
        by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + 1
    # Q/note-group: several open note_update rows on the same concept are
    # ONE decision with N candidates, not N separate list entries — grouped
    # here for display only (storage, ids, and `counts.open` above all stay
    # per-row). Skipped when looking up one specific id: that lookup wants
    # exactly the row asked for, not its siblings folded in around it.
    display_rows = rows if proposal_id else prop.group_open(rows)
    shown = display_rows if limit is None else display_rows[:limit]
    folds = []
    for spec in loop.REGISTRY.values():
        if spec.table == "knowledge_proposals":
            folds.extend(loop.folded(db, mid, spec))
    return {
        "open": [_proposal_row(r, verbose) for r in shown],
        "counts": {"open": len(rows), "shown": len(shown), "by_kind": by_kind},
        "truncated": len(shown) < len(display_rows),
        "folded": folds,
    }


def reconcile_proposals(db: Database, manuscript: dict,
                        apply: bool = True) -> dict:
    """Settle proposals the world has already answered, and re-base the ones
    it has moved under. Deterministic — no LLM, no author judgment. Run this
    before anything else in the queue: it shrinks the input to every later
    stage and, more importantly, stops the survivors misreporting their own
    'current' text."""
    from . import loop

    mid = manuscript["id"]
    report = {"satisfied": [], "orphan": [], "stale": [], "live": 0,
              "by_kind": {}}
    open_rows = prop.open_proposals(db, mid)
    for spec in loop.REGISTRY.values():
        if spec.table != "knowledge_proposals":
            continue
        kind = spec.key.split("/", 1)[1]
        rows = [r for r in open_rows if r["kind"] == kind]
        if not rows:
            continue
        out = loop.reconcile_queue(db, mid, spec, rows, apply=apply)
        report["by_kind"][kind] = {
            "considered": len(rows), "satisfied": len(out["satisfied"]),
            "orphan": len(out["orphan"]), "stale": len(out["stale"]),
            "live": out["live"]}
        for key in ("satisfied", "orphan", "stale"):
            report[key].extend(out[key])
        report["live"] += out["live"]
    report["applied"] = apply
    report["still_open"] = len(prop.open_proposals(db, mid))
    return report


def screen_proposals(db: Database, manuscript: dict, llm=None,
                     on_batch=None) -> dict:
    """Run the screen over every open proposal queue, cutting what violates
    the author's active law. Nothing is cut when there is no law yet, so
    this is inert until beliefs have been earned."""
    from . import loop

    mid = manuscript["id"]
    report: dict = {"cut": [], "by_queue": {}}
    for spec in loop.REGISTRY.values():
        if spec.table != "knowledge_proposals":
            continue
        kind = spec.key.split("/", 1)[1]
        rows = [r for r in prop.open_proposals(db, mid) if r["kind"] == kind]
        cuts = loop.screen(db, mid, spec, rows, llm, on_batch=on_batch)
        report["by_queue"][spec.key] = {"considered": len(rows),
                                        "cut": len(cuts)}
        report["cut"].extend(cuts)
    report["still_open"] = len(prop.open_proposals(db, mid))
    return report


def _proposal_row(row: dict, verbose: bool = False) -> dict:
    """Compact rows keep the detail LINES but preview each one: for a
    note_update the details carry the whole current and proposed note, so an
    untruncated 'compact' listing is the full payload wearing a disguise —
    762 open proposals still serialised to 287KB with them intact."""
    summary, details = prop.describe(row)
    if verbose:
        return {**row, "summary": summary, "details": details}
    return {"id": row["id"], "kind": row["kind"], "summary": summary,
            "details": [_preview(d) for d in details]}


def resolve_proposal(db: Database, manuscript: dict, prefix: str,
                     action: str, reason: str | None = None,
                     llm=None) -> dict:
    rows = [r for r in prop.open_proposals(db, manuscript["id"]) if prefix in r["id"]]
    folded_hit = False
    if not rows:
        # A screen-folded proposal is still actionable: acting on one is the
        # ONLY event that can contradict a belief which is actively cutting,
        # so it must be reachable by id or auto-promotion is irreversible in
        # practice while looking reversible in the schema (loop.py §2).
        rows = [dict(r) for r in db.all(
            "SELECT * FROM knowledge_proposals WHERE manuscript_id = ? "
            "AND state = 'dismissed' "
            "AND json_extract(metadata, '$.by') = 'screen'",
            (manuscript["id"],)) if prefix in r["id"]]
        folded_hit = bool(rows)
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
    result = {"message": message, "proposal_id": row["id"]}
    if llm is not None and reason:
        from . import loop

        key = f"proposals/{row['kind']}"
        if key in loop.REGISTRY:
            seeded = loop.distil_pending(db, manuscript, loop.REGISTRY[key], llm)
            if seeded and seeded.get("statement"):
                result["belief"] = {
                    "statement": seeded["statement"],
                    "status": seeded.get("status"),
                    "confidence": seeded.get("confidence")}
    if folded_hit:
        result.update(_contradict_folding_belief(db, row, action))
    return result


def _contradict_folding_belief(db: Database, row: dict, action: str) -> dict:
    """The author reached past the screen. Adopting what a belief cut is
    direct evidence against that belief; dismissing it agrees with the cut
    but is NOT counted as support, because the belief already acted — a
    belief that scored its own firings would ratchet its own confidence."""
    from . import beliefs as bel

    law_id = (loads(row.get("metadata"), {}) or {}).get("law")
    if not law_id or action == "dismiss":
        return {"folded": True}
    updated = bel.reinforce_belief(db, law_id, "rejected")
    if not updated:
        return {"folded": True}
    return {"folded": True, "contradicted": {
        "belief": law_id, "statement": updated.get("statement"),
        "confidence": updated.get("confidence"),
        "status": updated.get("status")}}


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


def list_beliefs(db: Database, manuscript: dict) -> list[dict]:
    rows = []
    for row in db.all(
        "SELECT * FROM editorial_beliefs WHERE manuscript_id = ? "
        "AND status != 'retired' ORDER BY confidence DESC",
        (manuscript["id"],),
    ):
        rows.append({**dict(row), "questions": loads(row["outstanding_questions"], [])})
    return rows


def _belief_by_prefix(db: Database, manuscript: dict, prefix: str) -> dict:
    rows = db.all(
        "SELECT * FROM editorial_beliefs WHERE manuscript_id = ? "
        "AND status != 'retired' AND id LIKE ?",
        (manuscript["id"], f"%{prefix}%"),
    )
    if not rows:
        raise LookupError(f"no live belief matching '{prefix}'")
    if len(rows) > 1:
        raise LookupError(f"'{prefix}' is ambiguous ({len(rows)} matches)")
    return dict(rows[0])


def retire_belief(db: Database, manuscript: dict, prefix: str,
                  reason: str) -> dict:
    from . import beliefs as bel

    belief = _belief_by_prefix(db, manuscript, prefix)
    return bel.retire_belief(db, manuscript["id"], belief, reason)


def demote_belief(db: Database, manuscript: dict, prefix: str,
                  reason: str) -> dict:
    """Send a validated belief back to 'candidate' (Sponsor override,
    contradicting evidence, etc.) — not retired: the statement is not
    banned, and real future evidence can re-validate it. See
    beliefs.demote_belief for why the validated-floor grandfather (X7-13)
    cannot resurrect it on the next reinforce_belief."""
    from . import beliefs as bel

    belief = _belief_by_prefix(db, manuscript, prefix)
    if belief["status"] != "validated":
        raise ValueError(
            f"belief {belief['id'][:8]} is '{belief['status']}', not "
            "validated — nothing to demote")
    return bel.demote_belief(db, manuscript["id"], belief, reason)


def merge_beliefs(db: Database, manuscript: dict, duplicate: str,
                   canonical: str, reason: str | None = None) -> dict:
    from . import beliefs as bel

    dup = _belief_by_prefix(db, manuscript, duplicate)
    canon = _belief_by_prefix(db, manuscript, canonical)
    if dup["id"] == canon["id"]:
        raise LookupError("duplicate and canonical are the same belief")
    return bel.merge_beliefs(db, manuscript["id"], dup, canon, reason)


def convert_belief(db: Database, manuscript: dict, prefix: str, aspect: str,
                   statement: str | None = None, guide: str | None = None,
                   file: str | None = None, notes: str | None = None,
                   reason: str | None = None) -> dict:
    from . import beliefs as bel

    belief = _belief_by_prefix(db, manuscript, prefix)
    element = add_style_law(
        db, manuscript, aspect, statement or belief["statement"],
        guide_name=guide, file=file, notes=notes)
    return bel.convert_belief(db, manuscript["id"], belief, element, reason)


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


def _compact_belief(belief: dict) -> dict:
    return {"id": belief["id"], "statement": belief["statement"],
            "status": belief["status"], "confidence": belief["confidence"],
            "support": f"{belief['supporting']}+/{belief['contradicting']}-"}


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
    out["belief_changes"] = briefing["belief_changes"]  # built compact
    out["new_beliefs"] = [_compact_belief(p) for p in briefing["new_beliefs"]]
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
    # Small and always whole: an open writeup means a truncated file, and
    # a count would be useless — the agent needs the name to say it aloud.
    out["active_writeups"] = briefing["active_writeups"]
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
