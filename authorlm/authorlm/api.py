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
from .guidance import compute_prerequisite_gaps, generate_guidance, intent_coverage_notes
from .llm import LLMClient
from .revisions import collect_revision, detect_transitions, massive_deletions

__all__ = [
    "open_db", "load_config", "make_llm",
    "list_manuscripts", "get_manuscript", "resolve_file",
    "status", "ensure_session", "close_session", "expire_idle_session",
    "declare_intent", "intent_preview", "complete_intent", "abandon_intent",
    "list_intents", "collect", "guide", "review", "get_briefing",
    "analyze", "diff_versions", "list_concepts", "show_concept",
    "add_concept", "link_concepts", "confirm_concept", "retire_concept",
    "confirm_edge", "reject_edge", "list_proposals", "resolve_proposal",
    "list_policies", "run_extraction", "get_plan", "get_doc_links",
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
    vanished_proposals = []
    for node in vanished:
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
        "realized": [
            {"name": n["name"], "introduced_in": n["introduced_in"]} for n in realized
        ],
        "repointed": repointed,
        "vanished": vanished_proposals,
        "new_paragraphs": new_paragraphs,
        "extract_hint": extract_hint,
        "gaps_before": gaps_before,
        "gaps_after": gaps_after,
        "gaps_resolved": [g for g in gaps_before if g["edge_id"] not in after_keys],
        "gaps_new": [g for g in gaps_after if g["edge_id"] not in before_keys],
    }

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
           llm: LLMClient | None = None) -> dict:
    if decision not in ("accepted", "rejected", "modified", "deferred"):
        raise ValueError(f"unknown decision '{decision}'")
    latest_batch = db.one(
        "SELECT batch_id FROM guidance_history WHERE session_id = ? "
        "ORDER BY created_at DESC LIMIT 1",
        (session["id"],),
    )
    if not latest_batch:
        raise LookupError("no guidance generated in this session — run guide first")
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


# ------------------------------------------------------------ other reads

def _catch_up(db: Database, manuscript: dict, config: dict) -> dict | None:
    """Lazy catch-up collection: state-reading entry points must never serve
    stale text. The shell has a live watcher; every other surface collects
    on read. Returns a small report when something was collected."""
    report = collect(db, manuscript, config, source="catch-up")
    if report.get("unchanged") or report.get("staged"):
        return None
    return {"version_no": report.get("version_no"),
            "transitions": report.get("transitions", [])}


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
        row = db.one(
            "SELECT * FROM style_elements WHERE manuscript_id = ? AND id LIKE ? "
            "AND status = 'active'",
            (manuscript["id"], f"%{overrides}%"),
        )
        if not row:
            raise LookupError(f"no active style element matching '{overrides}'")
        resolved = row["id"]
    return dict(st.add_element(
        db, manuscript["id"], aspect, statement,
        guide=dict(guide) if guide else None, file=file, notes=notes,
        overrides=resolved,
    ))


def retire_style_element(db: Database, manuscript: dict, prefix: str) -> dict:
    from . import styles as st

    row = db.one(
        "SELECT * FROM style_elements WHERE manuscript_id = ? AND id LIKE ? "
        "AND status = 'active'",
        (manuscript["id"], f"%{prefix}%"),
    )
    if not row:
        raise LookupError(f"no active style element matching '{prefix}'")
    st.retire_element(db, dict(row))
    return {"id": row["id"], "statement": row["statement"], "status": "retired"}


def merge_concepts(db: Database, manuscript: dict,
                   canonical_name: str, duplicate_name: str) -> dict:
    canonical = cg.get_concept(db, manuscript["id"], canonical_name)
    if not canonical:
        raise LookupError(f"no concept named '{canonical_name}'")
    duplicate = cg.get_concept(db, manuscript["id"], duplicate_name)
    if not duplicate:
        raise LookupError(f"no concept named '{duplicate_name}'")
    result = cg.merge_concepts(db, manuscript["id"], dict(canonical), dict(duplicate))
    from .extraction import record_triage

    record_triage(db, manuscript["id"], dict(duplicate), "merged", canonical["name"])
    return result


def link_concepts(db: Database, manuscript: dict, from_name: str,
                  relation: str, to_name: str) -> dict:
    return dict(cg.link_concepts(db, manuscript["id"], from_name, relation, to_name))


def confirm_concept(db: Database, manuscript: dict, name: str,
                    kind: str | None = None) -> dict:
    node = cg.get_concept(db, manuscript["id"], name)
    if not node:
        raise LookupError(f"no concept named '{name}'")
    from .extraction import record_triage

    meta = loads(node["metadata"], {})
    extracted = meta.get("origin") == "extracted"
    meta["confirmed"] = True
    changes: dict[str, Any] = {"metadata": json.dumps(meta)}
    if kind and kind != node["kind"]:
        changes["kind"] = kind
        if extracted:
            record_triage(db, manuscript["id"], dict(node), "retyped", kind)
    elif extracted:
        record_triage(db, manuscript["id"], dict(node), "confirmed")
    db.update("concept_nodes", node["id"], changes)
    return {"name": node["name"], "kind": kind or node["kind"]}


def retire_concept(db: Database, manuscript: dict, name: str) -> dict:
    node = cg.get_concept(db, manuscript["id"], name)
    if not node:
        raise LookupError(f"no concept named '{name}'")
    if node["status"] == "retired":
        raise ValueError(f"'{node['name']}' is already retired")
    from .extraction import record_triage

    if loads(node["metadata"], {}).get("origin") == "extracted":
        record_triage(db, manuscript["id"], dict(node), "rejected")
    edges = cg.retire_concept(db, manuscript["id"], dict(node))
    return {"name": node["name"], "edges_retired": edges}


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
    db.update(
        "concept_edges", edge["id"],
        {"relation": relation or edge["relation"], "status": "declared"},
    )
    return {
        "from_name": cg.node_name(db, edge["from_node"]),
        "relation": relation or edge["relation"],
        "to_name": cg.node_name(db, edge["to_node"]),
    }


def reject_edge(db: Database, manuscript: dict, edge_prefix: str) -> dict:
    edge = _find_edge(db, manuscript, edge_prefix)
    db.update("concept_edges", edge["id"], {"status": "rejected"})
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
        "SELECT * FROM editorial_policies WHERE manuscript_id = ? ORDER BY confidence DESC",
        (manuscript["id"],),
    ):
        rows.append({**dict(row), "questions": loads(row["outstanding_questions"], [])})
    return rows


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
