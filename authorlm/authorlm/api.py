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

import contextlib
import difflib
import io
import json
import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from . import clients
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
# The filter pass's two collaborators, at module scope because its
# verbs are ordinary members of this module rather than a lazily
# reached corner. `passes` imports `api` only from inside function
# bodies and `staging` imports neither, so no cycle is created.
from . import passes
from . import staging
from . import structure

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
    "scope_intent", "scope_evidence", "scope_tally",
    "write_start", "write_intents", "write_plan", "write_status",
    "write_propose",
    "write_draft",
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
                        hardcover_isbn: str = "",
                        trim_size: str = "",
                        bleed: bool | str = False) -> dict:
    """Register one manuscript and its canonical publication identity."""
    root = Path(path).resolve()
    if not root.is_dir():
        raise ValueError(f"{root} is not a directory")
    if db.one("SELECT id FROM manuscripts WHERE name = ?", (name,)):
        raise ValueError(f"manuscript '{name}' is already registered")
    paperback_isbn = _normalize_isbn13(paperback_isbn)
    hardcover_isbn = _normalize_isbn13(hardcover_isbn)
    _validate_format_isbns(paperback_isbn, hardcover_isbn)
    trim_width, trim_height = parse_trim_size(trim_size)
    row = ko_fields("ms")
    row.update(
        name=name, path=str(root), author=author.strip(),
        copyright_owner=copyright_owner.strip(),
        paperback_isbn=paperback_isbn,
        hardcover_isbn=hardcover_isbn,
        trim_width=trim_width, trim_height=trim_height,
        bleed=int(parse_bleed(bleed)))
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
        "trim_size": format_trim_size(manuscript.get("trim_width", 0),
                                      manuscript.get("trim_height", 0)),
        "trim_width": float(manuscript.get("trim_width", 0) or 0),
        "trim_height": float(manuscript.get("trim_height", 0) or 0),
        "bleed": bool(manuscript.get("bleed", 0)),
    }


# KDP's standard paperback trim sizes, inches (width, height). A custom
# size inside the accepted range still builds; the export only warns
# that it is off the standard list, because a standard size is what
# keeps the print run cheapest and the cover template available.
KDP_TRIM_SIZES = (
    (5, 8), (5.06, 7.81), (5.25, 8), (5.5, 8.5), (6, 9), (6.14, 9.21),
    (6.69, 9.61), (7, 10), (7.44, 9.69), (7.5, 9.25), (8, 10),
    (8.25, 6), (8.25, 8.25), (8.5, 8.5), (8.5, 11), (8.27, 11.69),
)
KDP_TRIM_MIN = (4.0, 6.0)
KDP_TRIM_MAX = (8.5, 11.69)


def parse_trim_size(value: str | None) -> tuple[float, float]:
    """'6x9', '6 x 9', '6in x 9in', '6×9' → (6.0, 9.0) inches. Empty
    clears (0, 0). Refuses anything outside KDP's accepted range."""
    text = (value or "").strip().lower()
    if not text:
        return 0.0, 0.0
    m = re.fullmatch(
        r"(\d+(?:\.\d+)?)\s*(?:in|\"|inch(?:es)?)?\s*[x×by]+\s*"
        r"(\d+(?:\.\d+)?)\s*(?:in|\"|inch(?:es)?)?", text)
    if not m:
        raise ValueError("trim size is WIDTHxHEIGHT in inches, e.g. 6x9")
    width, height = float(m.group(1)), float(m.group(2))
    if not (KDP_TRIM_MIN[0] <= width <= KDP_TRIM_MAX[0]
            and KDP_TRIM_MIN[1] <= height <= KDP_TRIM_MAX[1]):
        raise ValueError(
            f"trim size {width:g}x{height:g} is outside the print range "
            f"({KDP_TRIM_MIN[0]:g}x{KDP_TRIM_MIN[1]:g} to "
            f"{KDP_TRIM_MAX[0]:g}x{KDP_TRIM_MAX[1]:g} inches)")
    return width, height


def format_trim_size(width, height) -> str:
    width, height = float(width or 0), float(height or 0)
    return f"{width:g}x{height:g}" if width and height else ""


def parse_bleed(value) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in ("", "0", "no", "false", "off"):
        return False
    if text in ("1", "yes", "true", "on"):
        return True
    raise ValueError("bleed is yes or no")


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
                               hardcover_isbn: str | None = None,
                               trim_size: str | None = None,
                               bleed: bool | str | None = None) -> dict:
    """Update publication identity without exposing the internal KO metadata."""
    changes = {}
    if trim_size is not None:
        changes["trim_width"], changes["trim_height"] = parse_trim_size(
            trim_size)
    if bleed is not None:
        changes["bleed"] = int(parse_bleed(bleed))
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
            "--hardcover-isbn, --trim-size, and/or --bleed")
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


def ensure_session(db: Database, manuscript: dict) -> tuple[dict, bool]:
    """Active session, creating one lazily if needed (conversational
    surfaces bind on first substantive call).

    The chat that is speaking is resolved internally (`clients.current()`)
    and appended to `metadata.clients`. A LIST, not a single id: an
    AuthorLM session is a shared ambient work period, so several chats
    legitimately share one, and the old single `client_session` string
    could only ever record the first. This is where the reconstruction
    payload lives — label, start time, transcript path — one copy per
    client per session rather than one per row."""
    session = ses.active_session(db, manuscript["id"])
    if session:
        return clients.record_session_client(db, session), False
    session = ses.start_session(db, manuscript["id"])
    return clients.record_session_client(db, session), True


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


def _scope_ruling(before: str | None, after: str | None) -> dict:
    """One entry of `metadata.scope_history` — the record that the author
    DECIDED where a goal lives. Spelled once, because `_scope_ruled`
    reads it and the two must not drift: for a book-wide ruling both ends
    are null, and the entry's existence is the only thing that
    distinguishes the decision from the default."""
    return {"from": before, "to": after, "at": now_iso(),
            "by": clients.current().key()}


def declare_intent(db: Database, manuscript: dict, statement: str,
                   scope: str | None = None, book_wide: bool = False) -> dict:
    """`scope` is where the goal applies: a file, a toc opener (which
    covers every essay beneath it), or None for manuscript-wide. It is
    what `write start` derives the writeup's intents from, so an absent
    scope now has a consequence — every future writeup carries the goal
    — and both surfaces say so at declaration time (design §15.17, Q2).

    `book_wide` is the author EXPLICITLY choosing the whole book, which
    is a different act from not naming a place at all. Both leave `scope`
    NULL, so the ruling is recorded in `metadata.scope_history` exactly as
    `intent scope --book-wide` records it — otherwise every deliberate
    book-wide declaration would land straight back on the triage sheet as
    "no place", and the author's sitting would refill as fast as they
    emptied it. An ABSENT flag records nothing: a default is not a
    decision, and the sheet is right to ask about it."""
    if scope is not None:
        scope = _resolve_relpath(manuscript, scope)
    row = ses.declare_intent(db, manuscript["id"], statement, scope=scope)
    if book_wide and scope is None:
        meta = loads(row["metadata"], {}) or {}
        meta["scope_history"] = [_scope_ruling(None, None)]
        db.update("declared_intents", row["id"],
                  {"metadata": json.dumps(meta)})
        row = dict(row)
        row["metadata"] = json.dumps(meta)
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
    # Q4: closing an intent closes every open episode for it, and a
    # frozen member whose intent completes becomes a stale_member on
    # every open writeup that ratified it. One query stops the author
    # discovering that one `write status` at a time. A warning: the
    # completion is theirs to make.
    held = _writeups_holding(db, manuscript, intent["id"])
    ses.complete_intent(db, intent, outcome)
    analysis = analyze_pending(db, manuscript, llm) if llm and llm.enabled else []
    return {"intent": intent, "analysis": analysis, "held_by": held}


def _writeups_holding(db: Database, manuscript: dict, intent_id: str) -> list:
    """Active writeups carrying this intent as a ratified member."""
    out = []
    for row in db.all(
            "SELECT * FROM writeups WHERE manuscript_id = ? AND status = 'active'",
            (manuscript["id"],)):
        block = loads(row["metadata"], {}).get("intents") or {}
        if (block.get("state") == "frozen"
                and any(m["id"] == intent_id
                        for m in block.get("members") or [])):
            out.append({"writeup": row["id"], "file": row["file"]})
    return out


def abandon_intent(db: Database, manuscript: dict, prefix: str,
                   reason: str | None) -> dict:
    intent = _find_intent(db, manuscript, prefix)
    if intent["status"] != "active":
        raise ValueError(f"intent is already {intent['status']}")
    ses.abandon_intent(db, intent, reason)
    return {"intent": intent}


def _scope_target(manuscript: dict, scope: str | None,
                  chapter: str | None, manuscript_wide: bool) -> str | None:
    """Exactly one of the three, resolved and validated. A `--chapter`
    target must be a toc OPENER — a file with children — or the word
    means nothing: scoping to a leaf essay is `--scope`."""
    from .revisions import read_manuscript_files
    from .structure import _toc_chapters, TOC_FILENAME

    given = [bool(scope), bool(chapter), bool(manuscript_wide)]
    if sum(given) != 1:
        raise ValueError(
            "name exactly one place: --scope <file> (this essay), "
            "--chapter <opener> (every essay beneath it), or --manuscript "
            "(the whole book).")
    if manuscript_wide:
        return None
    target = _resolve_relpath(manuscript, scope or chapter)
    # §2.5 row 31: an intent scoped to the dictionary means nothing —
    # there is no writing to route there. Same helper as the filter and
    # lens refusals, so the three cannot drift.
    structure.refuse_sidecar(Path(target).name)
    if chapter:
        files = read_manuscript_files(Path(manuscript["path"]))
        children = [c["file"] for c in
                    _toc_chapters(files.get(TOC_FILENAME) or "")
                    if c.get("parent") == target]
        if not children:
            raise ValueError(
                f"{target} has no essays beneath it in toc.toml, so "
                f"--chapter names nothing. Scope it to the essay itself "
                f"(--scope {target}), or make it a part opener first.")
    return target


def scope_intent(db: Database, manuscript: dict, prefix: str,
                 scope: str | None = None, chapter: str | None = None,
                 manuscript_wide: bool = False) -> dict:
    """(Re)place an existing intent. Metadata-forward, never rewriting:
    episodes keep their transitions, completed writeups keep their frozen
    member records with the tier as it stood, and `metadata.scope_history`
    records the move with the client that made it. Active writeups
    holding the intent as a frozen member are returned so the caller can
    say they are unaffected — which is exactly why the member record
    freezes the tier (design-intent-scope §4.3)."""
    intent = _find_intent(db, manuscript, prefix)
    if intent["status"] != "active":
        raise ValueError(
            f"intent {intent['id']} is {intent['status']} — scope places "
            f"where FUTURE work routes, and there is none for a "
            f"{intent['status']} goal.")
    target = _scope_target(manuscript, scope, chapter, manuscript_wide)
    meta = loads(intent["metadata"], {}) or {}
    history = meta.get("scope_history") or []
    history.append(_scope_ruling(intent["scope"], target))
    meta["scope_history"] = history
    db.update("declared_intents", intent["id"],
              {"scope": target, "metadata": json.dumps(meta)})
    holding = []
    for row in db.all(
            "SELECT * FROM writeups WHERE manuscript_id = ? AND status = 'active'",
            (manuscript["id"],)):
        block = loads(row["metadata"], {}).get("intents") or {}
        member = next((m for m in block.get("members") or []
                       if m["id"] == intent["id"]), None)
        if member and block.get("state") == "frozen":
            holding.append({"writeup": row["id"], "file": row["file"],
                            "tier": member["tier"], "scope": member["scope"]})
    return {"intent": {**intent, "scope": target}, "was": intent["scope"],
            "scope": target, "frozen_in": holding}


def _scope_ruled(intent: dict) -> bool:
    """Whether the author has RULED on where this goal lives.

    Not the same question as "does it have a scope". `intent scope <id>
    --book-wide` writes NULL over NULL — correctly, because book-wide IS
    the absent scope — and records the move in `metadata.scope_history`.
    So the history entry is the only trace a ruling leaves, and without
    reading it a goal the author deliberately made book-wide is
    indistinguishable from one nobody has ever placed. Those two mean
    opposite things: the first is a decision the author wants to see
    counted, the second is work still to do.

    Any entry counts, including the null → null one. Found live: after
    the author ruled 23 intents book-wide, the triage sheet offered all
    23 back to them and the closing number could not be computed."""
    if intent.get("scope"):
        return True
    return bool((loads(intent.get("metadata"), {}) or {}).get("scope_history"))


def scope_tally(db: Database, manuscript: dict) -> dict:
    """The scope triage's closing numbers. `ruled_book_wide` is the one
    that matters: those are the goals every future writeup will carry, by
    the author's decision rather than by default."""
    rows = [dict(r) for r in db.all(
        "SELECT * FROM declared_intents WHERE manuscript_id = ? "
        "AND status = 'active'", (manuscript["id"],))]
    scoped = [r for r in rows if r["scope"]]
    unscoped = [r for r in rows if not r["scope"]]
    ruled = [r for r in unscoped if _scope_ruled(r)]
    return {"active": len(rows), "scoped": len(scoped),
            "ruled_book_wide": len(ruled),
            "no_place": len(unscoped) - len(ruled)}


def scope_evidence(db: Database, manuscript: dict,
                   prefix: str | None = None) -> list[dict]:
    """The one-time scope triage's read (design-intent-scope §4.1): for
    each active unscoped intent, the files its episodes ACTUALLY touched,
    the writeups bound to it, and a deterministic suggestion with the
    reason for it in words. ZERO LLM calls; the author rules from
    evidence rather than memory."""
    from . import passes

    if prefix:
        rows = [_find_intent(db, manuscript, prefix)]
    else:
        # "No place" is `scope IS NULL` AND no ruling on record. An intent
        # the author has already declared book-wide is settled, and the
        # sitting has to be able to END — a sheet that keeps offering back
        # what was already ruled on is a sitting with no last page.
        rows = [row for row in (dict(r) for r in db.all(
            "SELECT * FROM declared_intents WHERE manuscript_id = ? "
            "AND status = 'active' AND scope IS NULL ORDER BY created_at",
            (manuscript["id"],))) if not _scope_ruled(row)]
    out = []
    for intent in rows:
        counts: dict[str, int] = {}
        for episode in db.all(
                "SELECT transition_ids FROM editorial_episodes "
                "WHERE intent_id = ?", (intent["id"],)):
            for tid in loads(episode["transition_ids"], []):
                row = db.one(
                    "SELECT location FROM editorial_transitions WHERE id = ?",
                    (tid,))
                if row:
                    file = row["location"].split("#", 1)[0]
                    counts[file] = counts.get(file, 0) + 1
        writeups = [{"id": w["id"], "file": w["file"], "status": w["status"]}
                    for w in db.all(
                        "SELECT * FROM writeups WHERE manuscript_id = ? "
                        "AND intent_id = ? ORDER BY created_at",
                        (manuscript["id"], intent["id"]))]
        files = sorted(counts, key=lambda f: (-counts[f], f))
        total = sum(counts.values())
        if len(files) == 1:
            suggested = {"tier": "file", "scope": files[0],
                         "why": f"every change under it — {total} of them — "
                                f"landed in {files[0]}"}
        elif len(files) > 1:
            chains = [set(passes.toc_ancestors(manuscript, f)) for f in files]
            common = set.intersection(*chains) if chains else set()
            nearest = None
            for candidate in passes.toc_ancestors(manuscript, files[0]):
                if candidate in common:
                    nearest = candidate
                    break
            if nearest:
                suggested = {"tier": "chapter", "scope": nearest,
                             "why": f"{len(files)} essays, all under "
                                    f"{nearest}"}
            else:
                suggested = {"tier": "manuscript", "scope": None,
                             "why": "changes across the book, with no one "
                                    "part holding them"}
        else:
            suggested = {"tier": "manuscript", "scope": None,
                         "why": "no recorded work yet — your call"}
        source = db.one("SELECT * FROM sources WHERE id = ?",
                        (intent["source_id"],))
        out.append({
            "id": intent["id"], "statement": intent["statement"],
            "created_at": intent["created_at"],
            "source": dict(source)["kind"] if source else None,
            "files": [{"file": f, "transitions": counts[f]} for f in files],
            "writeups": writeups, "suggested": suggested})
    return out


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
            analyze: bool | None = None, episode=None) -> dict:
    """The observation pipeline: snapshot → transitions → episode →
    realization/co-occurrence scans → extract hint → prerequisite delta.
    Returns a structured report; {"staged": [...]} when the auto path held
    a suspicious deletion; {"unchanged": True} when nothing changed.

    `episode` names the episode the transitions attach to. The default —
    the session's most recently created open episode — is what every
    ambient caller wants and is byte for byte what this function did
    before the parameter existed. The beat loop passes the PRIMARY
    intent's episode instead: with two writeups open at once, the default
    attached both writeups' beats to whichever intent was declared last
    (design-intent-scope §0.3).

    `episode=NO_EPISODE` is the third case and it is not the same as
    `None`: it attaches the transitions to NOTHING. A settle that is
    hygiene rather than goal-work — a filter pass, a critique-pass
    resolve — has no goal to be filed under, and filing it under the
    open episode of whatever the author happened to be doing would make
    §15.17's completion analysis report that goal as served by an edit
    sweep. Refusing to guess is the doctrine (§15.19 rule 2); this is
    that refusal made mechanical."""
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
    if episode is NO_EPISODE:
        # Filed under NO goal, deliberately (filter-pass design §1.8).
        # `episode=None` means AMBIENT — the session's most recently
        # created open episode, whatever goal it belongs to — which is
        # §15.17's mis-attribution. Hygiene work has no goal to be filed
        # under, so it is filed under none: the version history, the
        # evidence rows and the run row are the complete record.
        episode = None
    elif episode is not None:
        ses.attach_transitions(db, episode, transitions)
        attached = True
    elif session:
        episode = ses.current_episode(db, mid, dict(session))
        ses.attach_transitions(db, episode, transitions)
        attached = True
    # The concept scans are shown a BLINDED version: any file that holds
    # only the mid-rewrite placeholder is presented as empty, exactly as
    # it was before the placeholder existed. concepts._word_pattern is
    # case-insensitive, so a concept named "Being" — entirely plausible
    # in this manuscript — would otherwise match the placeholder's "being
    # presently rewritten" and be marked realized with introduced_in
    # pointing at a truncated file, silently rewriting the graph. Picking
    # "safe" words is not a fix: the author owns the concept vocabulary
    # and can add the colliding word tomorrow. The version ROW is
    # untouched; only the scans' input is blinded.
    def _blinded(files: dict) -> dict:
        return {f: ("" if is_placeholder(t) else t) for f, t in files.items()}

    _blind = _blinded(loads(version["files"], {}))
    _scan_version = {**dict(version), "files": json.dumps(_blind)}
    realized = cg.scan_realizations(db, mid, _scan_version)
    repointed, vanished = cg.rescan_primary_locations(db, mid, _scan_version)
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

    # BOTH sides are blinded, for the same reason the concept scans are
    # (`_blind`, above): this walks the same case-insensitive concept
    # patterns over the same file texts, so an unblinded placeholder could
    # close a prerequisite gap — "the concept now appears in this essay" —
    # on the strength of a word inside a marker.
    #
    # `before` needs it as much as `after` does, and it is easy to miss:
    # every collect DURING a writeup has a before-version that already
    # contains the placeholder (the writeup's own start collect put it
    # there). Blinding only one side would leave the marker deciding the
    # DELTA — gaps_resolved and gaps_new are set differences of these two
    # — which is the same defect wearing a subtraction sign.
    gaps_before = (
        compute_prerequisite_gaps(db, mid, _blinded(loads(before["files"], {})))
        if before else []
    )
    gaps_after = compute_prerequisite_gaps(db, mid, _blind)
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

# The third value of `collect(episode=…)` (filter-pass design §1.8).
#
# `None` already means AMBIENT — attach to the session's most recently
# created open episode — so there was no way to say "attach to nothing"
# without inventing a value that is neither a row nor None. A sentinel
# object is that value: it can never collide with an episode dict, it
# cannot be produced by a database read, and `is` comparison makes the
# branch unmistakable at the call site.
NO_EPISODE = object()

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

# The mid-rewrite placeholder (design §14.3). `write start` on an
# EXISTING file leaves this where the essay was, instead of leaving the
# file empty.
#
# Visible text, not an HTML comment: the one path where a hidden marker
# fails is the one that matters. `export.publish_markdown` used to drop a
# mid-rewrite essay from the exported book in silence, and an HTML
# comment is stripped on the way to PDF/EPUB, so the hole would remain.
# Visible text cannot be shipped by accident, only loudly. The author
# also opens these files in Obsidian, where a comment is invisible.
#
# Bracketed `[AuthorLM: …]`, not the author's literal `<<…>>`: `<<` and
# `>>` are reserved by the pending-change grammar (threads.PENDING,
# gdocs.diff_push), so a literal `<<…>>` in a manuscript file would make
# the next surgical push of it fail with a message about a margin thread
# that does not exist. The author's WORDS are kept exactly.
#
# Deterministic by construction — no id, no timestamp, no file name — so
# it is written once per writeup and never churns a version or
# invalidates the prompt-cache prefix on its own.
MARKER = "[AuthorLM: being presently rewritten]"

PLACEHOLDER = (
    MARKER + "\n"
    "\n"
    "This essay is mid-rewrite and is being drafted one beat at a time. The\n"
    "previous text is not lost — it is pinned in the database. Run\n"
    "`authorlm write status` to see the writeup, `authorlm write digest` to\n"
    "print the pinned original, or `authorlm write abandon` to put the old\n"
    "essay back. The first accepted beat replaces this placeholder, which is\n"
    "never part of a finished essay.\n"
)


def is_placeholder(text: str) -> bool:
    """Whole-file exact match (whitespace-tolerant at the edges). A file
    the author has HAND-EDITED is not a placeholder — their text is never
    silently discarded, which is the stronger rule."""
    return text.strip() == PLACEHOLDER.strip()


def marker_present(text: str) -> bool:
    """The weaker check: the marker line survived inside a file that is
    no longer just the placeholder. Warned about, never auto-repaired."""
    return MARKER in text
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


def _touched(db: Database, row: dict) -> dict:
    """Record the touching chat on the writeup and return the POST-touch
    row. Both halves are load-bearing.

    Every one of `_writeup`'s ten callers does
    `meta = loads(writeup["metadata"], {})` after `_writeup` returns, and
    later dumps that dict back with `db.update`. If the touch ran after
    the caller's load — or if we handed back the pre-touch row — the
    caller's stale `meta` would silently clobber the clients list on its
    next write. Touching first, and re-reading, makes the existing
    read-modify-write sequence correct with no edits to any of the ten
    verbs."""
    clients.touch(db, "writeups", row, verb=clients.current_verb())
    fresh = db.one("SELECT * FROM writeups WHERE id = ?", (row["id"],))
    return dict(fresh) if fresh is not None else row


def _writeup(db: Database, manuscript: dict, prefix: str | None = None) -> dict:
    """The active writeup — matched by id prefix, or by FILE NAME.

    Two writeups at once is now an ordinary way to work (design §14), and
    it makes `--writeup` a constant companion. The author knows which
    essay they mean; asking them to go and fetch an opaque `wu-…` id to
    say so is friction for nothing. So the disambiguator also accepts the
    file: exact relpath first, then a unique case-insensitive substring —
    the same forgiveness `docs._match` gives every other file argument.

    Id matching is tried FIRST and unchanged, so nothing that worked
    before resolves differently now. Ambiguity is still refused rather
    than guessed at, in both directions, and an id-shaped miss still
    reports itself as one."""
    if prefix:
        rows = db.all(
            "SELECT * FROM writeups WHERE manuscript_id = ? AND id LIKE ?",
            (manuscript["id"], f"%{prefix}%"),
        )
        if len(rows) > 1:
            raise LookupError(f"'{prefix}' is ambiguous ({len(rows)} writeups)")
        if rows:
            return _touched(db, dict(rows[0]))
        # Not an id. Try it as the essay's name, ACTIVE writeups first —
        # a finished writeup on the same file must not shadow the open
        # one the author is obviously talking about.
        by_file = db.all(
            "SELECT * FROM writeups WHERE manuscript_id = ? "
            "ORDER BY (status = 'active') DESC, created_at DESC",
            (manuscript["id"],))
        exact = [r for r in by_file if r["file"] == prefix]
        hits = exact or [r for r in by_file
                         if prefix.lower() in r["file"].lower()]
        if not hits:
            raise LookupError(
                f"no writeup matching '{prefix}' — it is neither a writeup "
                f"id prefix nor the name of a file with a writeup on it "
                f"(open ones: "
                f"{', '.join(r['file'] for r in by_file if r['status'] == 'active') or 'none'})")
        names = {r["file"] for r in hits}
        if len(names) > 1:
            raise LookupError(
                f"'{prefix}' is ambiguous — it matches "
                f"{', '.join(sorted(names))}; name the file exactly, or "
                f"pass the writeup id")
        return _touched(db, dict(hits[0]))
    rows = db.all(
        "SELECT * FROM writeups WHERE manuscript_id = ? AND status = 'active'",
        (manuscript["id"],),
    )
    if not rows:
        raise LookupError("no active writeup — start one: write start <file> --intent <id>")
    if len(rows) > 1:
        files = ", ".join(r["file"] for r in rows)
        raise LookupError(
            f"multiple active writeups ({files}) — pass --writeup with the "
            f"file name (e.g. --writeup {rows[0]['file']}) or a writeup id")
    return _touched(db, dict(rows[0]))


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


def _primary_intent_id(writeup: dict) -> str | None:
    """The intent this writeup's transitions and verdicts belong to.

    `writeups.intent_id` still holds it — the frozen member block names
    the same row — so this reads the block first (it is authoritative
    once the set is ratified) and falls back to the column, which is what
    every writeup written before the block existed carries."""
    block = loads(writeup["metadata"], {}).get("intents") or {}
    return block.get("primary") or writeup["intent_id"]


def _writeup_episode(db: Database, manuscript: dict, writeup: dict,
                     session: dict) -> dict:
    """The episode every beat of this writeup attaches to: the PRIMARY
    intent's own, resolved once per verb and handed to both `collect` and
    `record_review` so the transitions and the verdict can never land in
    two different places (design-intent-scope §1.7)."""
    intent_id = _primary_intent_id(writeup)
    if not intent_id:
        return ses.current_episode(db, manuscript["id"], session)
    return ses.episode_for_intent(db, manuscript["id"], session, intent_id)


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


def _drafting_context(db: Database, manuscript: dict, writeup: dict,
                      capture=None) -> str:
    """The writeup's L1 book-frame (design §12.4 item 1): compressed
    summaries of the resolved essays before this one and the upcoming
    ones after it. Recomputed on every read rather than stored — the
    summaries themselves are the source of truth and a rebuild between
    beats must show through. The declared placement, however, IS stored
    (§12.4 item 3), so a resumed writeup on a not-yet-placed essay
    recomputes the same split without the author repeating the flag.

    `capture` is `write_start`'s own snapshot: the context it prints must
    be the same text its gate just passed, not a second read that a
    parallel session could have changed underneath it. Every other caller
    (the resume view) is a fresh invocation and takes a fresh one."""
    from . import summaries as sums

    placement = loads(writeup["metadata"], {}).get("placement")
    return sums.drafting_context(db, manuscript, writeup["file"],
                                 placement=placement, capture=capture)


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


def _style_candidates(db: Database, manuscript_id: str,
                      placement: str | None) -> str:
    """The tail of the missing-`--style` refusal: the guide names on
    record, and — when the placement anchor has one attached — the guide
    that anchor uses, which is almost always the answer. Read-only and
    best-effort; a refusal must never fail while composing its own
    message."""
    from . import summaries as sums

    guides = [row["name"] for row in db.all(
        "SELECT name FROM style_guides WHERE manuscript_id = ? ORDER BY name",
        (manuscript_id,))]
    if not guides:
        return ("\n  No style guides exist yet — define one first: "
                "style guide <name>.")
    parts = ["\n  Guides on record: "
             + ", ".join(f"'{name}'" for name in guides) + "."]
    if placement and placement != sums.PLACEMENT_START:
        anchor = db.one(
            "SELECT sg.name AS name FROM style_attachments sa "
            "JOIN style_guides sg ON sg.id = sa.guide_id "
            "WHERE sa.manuscript_id = ? AND sa.file = ?",
            (manuscript_id, placement))
        if anchor:
            parts.append(f"\n  {placement} uses '{anchor['name']}' — "
                         f"likely the one you want.")
    return "".join(parts)


# --------------------------------------------- scope-derived intent sets
#
# A rewrite serves every ACTIVE intent whose scope covers the essay
# (design-intent-scope). There is no group entity — the scope tier IS the
# group — and no schema change: membership lives in
# `writeups.metadata.intents`, on the dedup-append idiom
# `metadata.drafting_models` and `metadata.clients` already use.
#
# The member record freezes `statement`, `scope` and `tier` rather than
# joining them at read time. `declared_intents.scope` is mutable now, and
# a live join would let a re-scope in another chat silently change block
# A between beat n and beat n+1 — design F4's silent cache invalidator
# arriving by a new road. Freezing also makes the record history-safe: a
# re-scope after ratification cannot retro-narrate what a finished
# writeup was serving.

# Above this many manuscript-wide intents in one derived set, `write
# start` says so. They are unscoped, not book-wide by decision, and every
# one of them will ride along with every writeup until the author's scope
# triage. A warning, never a gate: they may want to write today.
MANY_MANUSCRIPT_WIDE = 5


def _intent_block(writeup: dict) -> dict:
    """The stored membership block, or {} for a writeup that predates it."""
    return loads(writeup["metadata"], {}).get("intents") or {}


def _member_record(manuscript: dict, relpath: str, intent: dict,
                   role: str = "secondary") -> dict:
    from . import passes

    tier = passes.scope_tier(manuscript, relpath, intent["scope"])
    return {"id": intent["id"], "tier": tier or "outside",
            "scope": intent["scope"], "statement": intent["statement"],
            "role": role}


def _member_order(manuscript: dict, relpath: str, member: dict) -> tuple:
    from . import passes

    return (passes.scope_specificity(manuscript, relpath, member["scope"]),
            member["id"])


def _pick_primary(manuscript: dict, relpath: str,
                  members: list[dict]) -> tuple[str | None, list[str]]:
    """The most specific member wins — file over chapter over manuscript,
    and within chapter the NEAREST toc ancestor. More than one candidate
    at the winning tier is a tie the machine must not break: oldest-first
    ("the standing goal") and newest-first ("the one they are working on
    now") are both plausible, which is the proof (§1.7). Returns
    (primary or None, the candidates)."""
    if not members:
        return None, []
    ranked = [(_member_order(manuscript, relpath, m)[0], m["id"])
              for m in members]
    best = min(rank for rank, _ in ranked)
    candidates = sorted(mid for rank, mid in ranked if rank == best)
    return (candidates[0] if len(candidates) == 1 else None), candidates


def _with_roles(members: list[dict], primary: str | None) -> list[dict]:
    return [{**m, "role": "primary" if m["id"] == primary else "secondary"}
            for m in members]


def _new_intent_block(manuscript: dict, relpath: str, members: list[dict],
                      manual: bool) -> dict:
    primary, candidates = _pick_primary(manuscript, relpath, members)
    members = _with_roles(members, primary)
    return {"state": "proposed", "manual": bool(manual), "primary": primary,
            "members": members, "derived": [m["id"] for m in members],
            "adds": [], "removes": [], "deferred": {}, "ignored": [],
            "frozen_at": None, "tied": [] if primary else candidates}


def _store_intent_block(db: Database, writeup: dict, block: dict) -> None:
    """Read-modify-write on the FRESH row. `_writeup` has already run
    `_touched`, so `metadata.clients` is on the row this reloads
    (`_touched`'s docstring, above) and nothing here can clobber it."""
    row = db.one("SELECT metadata FROM writeups WHERE id = ?",
                 (writeup["id"],))
    meta = loads(row["metadata"] if row else writeup["metadata"], {})
    meta["intents"] = block
    db.update("writeups", writeup["id"], {"metadata": json.dumps(meta)})


def _members_view(block: dict) -> list[dict]:
    """Members in render order: primary first, then tier rank, then id.
    One ordering, spelled once, for the CLI and for block A.

    Takes the stored block and nothing else — no manuscript, no file, no
    `db`. The order must be a pure function of what was ratified, for
    exactly the reason the member record freezes its fields."""
    from .passes import INTENT_TIER_RANK, UNKNOWN_TIER_RANK

    members = block.get("members") or []
    return sorted(members,
                  key=lambda m: (0 if m.get("role") == "primary" else 1,
                                 INTENT_TIER_RANK.get(m["tier"],
                                                      UNKNOWN_TIER_RANK),
                                 m["id"]))


def _resolve_member_prefix(block: dict, prefix: str) -> str | None:
    hits = [m["id"] for m in block.get("members") or [] if prefix in m["id"]]
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        raise LookupError(f"'{prefix}' is ambiguous ({len(hits)} members)")
    return None


def write_start(db: Database, manuscript: dict, config: dict,
                file: str, intent_prefix: str | Sequence[str] | None = None,
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
    essay-specific grounding the beats have.

    `intent_prefix` is now zero, one, or many. With none, the intent set
    is DERIVED from the scopes covering this essay and persisted as
    PROPOSED, to be frozen at `write plan`. With one or more, derivation
    is skipped entirely and the FIRST flag is the primary — so the
    single-flag command that existed before this design produces
    bit-for-bit the same writeup row."""
    from . import styles as st

    mid = manuscript["id"]
    relpath = _resolve_or_new(manuscript, file, new)
    placement = _resolve_placement(manuscript, after)
    brief = (brief or "").strip()
    if style and not new:
        raise ValueError(
            f"--style is only for --new; an existing file's guide is "
            f"attached with 'style attach {relpath} {style}'.")
    prefixes = ([intent_prefix] if isinstance(intent_prefix, str)
                else list(intent_prefix or []))
    # Named intents resolve HERE, at the head of the gate parade, exactly
    # where the single `--intent` always did: an author who names a dead
    # intent must still learn that first, not after four other refusals
    # (design §5.5, MT-5).
    named = []
    for prefix in prefixes:
        intent = _find_intent(db, manuscript, prefix)
        if intent["status"] != "active":
            raise ValueError(f"intent {intent['id']} is {intent['status']}, not active")
        if intent["id"] not in {i["id"] for i in named}:
            named.append(intent)
    guide = None
    if new:
        # `style attach` cannot run first for a file that does not exist:
        # attach_style -> _validate_file checks the name against disk, and
        # that integrity rule is not weakened here. So the guide is named
        # at start and validated before anything is created.
        if not style:
            # Enrich the message; never silently default. The guide IS
            # the drafting law, so attaching it stays the author's
            # explicit act — but the refusal can at least stop sending
            # them away to `style guides` mid-flow (usability-analysis
            # §2.2): name the guides on record, and the anchor's own
            # guide as the likely choice.
            raise ValueError(
                f"{relpath} has no attached style guide — the effective "
                f"guide is the drafting law. With --new, name it: "
                f"--style <guide>."
                + _style_candidates(db, mid, placement))
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
    from . import summaries as _sums

    # ONE capture for this invocation, shared by the gate below and by
    # the drafting context printed at the end. Two reads would leave a
    # window in which a parallel session truncates a neighbour AFTER the
    # gate approved it and BEFORE the context was assembled from it — and
    # the author would then be handed, as approved context, exactly the
    # text the gate exists to refuse. The target file itself never
    # appears in its own context, so the truncation this verb is about to
    # perform does not make the capture stale.
    capture = _sums.capture(db, manuscript)
    ready = passes.summaries_ready(db, manuscript, relpath,
                                   placement=placement, capture=capture)
    if not ready["ok"]:
        raise ValueError(passes.summaries_message(ready, "drafting pass"))

    # Derivation, LAST among the gates and still before the pin/truncate/
    # collect sequence (RISK K1). It can refuse — an essay no active
    # intent covers is a writeup with no goal — and a refusal here must
    # leave the disk exactly as it was.
    if named:
        members = [_member_record(manuscript, relpath, i) for i in named]
        block = _new_intent_block(manuscript, relpath, members, manual=True)
        # The author's FIRST flag is the primary, whatever the tiers say.
        # They just reached for it by name; the machine does not overrule
        # that with a specificity rule.
        block["primary"] = named[0]["id"]
        block["tied"] = []
        block["members"] = _with_roles(block["members"], block["primary"])
        outside = [m for m in block["members"] if m["tier"] == "outside"]
    else:
        members = sorted(
            (_member_record(manuscript, relpath, i)
             for i in passes.intents_in_scope(db, manuscript, relpath, "active")),
            key=lambda m: _member_order(manuscript, relpath, m))
        if not members:
            raise ValueError(
                f"no active intent covers {relpath}, so this rewrite would "
                f"have no goal. Declare one for it — "
                f"'intent declare \"<what this rewrite is for>\" --scope "
                f"{relpath}' — or place an existing one: "
                f"'intent scope <id> --scope {relpath}'. "
                f"To reach for an intent by name instead: "
                f"--intent <id>.")
        block = _new_intent_block(manuscript, relpath, members, manual=False)
        outside = []
    primary_id = block["primary"] or (block["tied"][0] if block["tied"]
                                      else block["members"][0]["id"])
    # `writeups.intent_id` keeps holding the primary (no schema change).
    # While a tie is unsettled it holds the lowest-id candidate as a
    # placeholder — no beat can be accepted before `write plan`, and
    # `write plan` refuses until the author settles it, so nothing is
    # ever attributed to the placeholder.
    intent = next((i for i in named if i["id"] == primary_id), None) \
        or _find_intent(db, manuscript, primary_id)
    manuscript_wide = [m for m in block["members"] if m["tier"] == "manuscript"]
    proposed_in_scope = passes.intents_in_scope(db, manuscript, relpath,
                                                "proposed")

    session, _ = ensure_session(db, manuscript)
    # Two collects, and they are NOT the same kind of act.
    #
    # The FIRST captures whatever the author typed and never collected
    # before this verb ran, so the pinned source version is complete. That
    # work PREDATES the writeup: it was done under whatever the session
    # was already doing, and filing it against a goal declared a moment
    # later would be back-dating. It stays ambient, deliberately.
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
    #
    # A truncation leaves the PLACEHOLDER, so another session reading this
    # file mid-rewrite finds an explanation rather than a blank essay
    # (design §14.3). A creation leaves it empty: the placeholder marks a
    # truncation, `--new` truncates nothing, its restore target is
    # nonexistence, and a created file's empty window is deliberately
    # structurally invisible (K6). Other sessions see a created file
    # through its `writeups` row instead, as the `unwritten` state.
    #
    # RISK K1: same statement, same position — after the last refusal.
    (Path(manuscript["path"]) / relpath).write_text(
        "" if new else PLACEHOLDER, encoding="utf-8")
    if new:
        attach_style(db, manuscript, relpath, guide["name"])
    # The SECOND collect records the TRUNCATION — this writeup's own first
    # act, and the largest single transition it will ever produce. It is
    # filed against the primary exactly as every beat is.
    #
    # ONLY when the primary is settled. While a tie is unsettled there is
    # no primary yet: `writeups.intent_id` below holds the lowest-id
    # candidate as a placeholder, and real work must never be attributed
    # to a candidate the author has not chosen. So the truncation stays
    # ambient in that case — the same refuse-to-guess doctrine the
    # tiebreak itself rests on, applied to the episode rather than to the
    # column. `write plan` is what resolves it, and every act after that
    # point is routed.
    collect(db, manuscript, config, source="write-start",
            episode=(ses.episode_for_intent(db, mid, session, block["primary"])
                     if block["primary"] else None))

    row = ko_fields("wu")
    row.update(
        manuscript_id=mid, intent_id=intent["id"], file=relpath, mode="fresh",
        status="active", source_version_id=source["id"], plan="[]",
        cursor=0, learnings="[]",
        metadata=json.dumps({"next_n": 1, "placement": placement,
                             "created_file": bool(new),
                             "brief": brief or None,
                             "intents": block}),
    )
    db.insert("writeups", row)
    source_text = loads(source["files"], {}).get(relpath, "")
    # Q3: a `--new` file has no toc entry yet, so `scope_chain` is
    # [file, None] and no chapter-scoped intent can be derived. Say so
    # at the start rather than letting the author discover the absence.
    unplaced = bool(new) and not named
    return {"writeup": row, "intent": intent,
            "intents": block,
            "intents_view": _members_view(block),
            "manuscript_wide": len(manuscript_wide),
            "many_manuscript_wide": len(manuscript_wide) >= MANY_MANUSCRIPT_WIDE,
            "outside_scope": [m["id"] for m in outside],
            "proposed_in_scope": proposed_in_scope,
            "chapter_undecidable": unplaced,
            "source_version_no": source["version_no"],
            "source_chars": len(source_text),
            "created": bool(new), "brief": brief or None,
            "style": guide["name"] if guide else None,
            "drafting_context": _drafting_context(db, manuscript, row,
                                                  capture=capture)}


def write_intents(db: Database, manuscript: dict,
                  add: Sequence[str] = (), remove: Sequence[str] = (),
                  primary: str | None = None, defer: str | None = None,
                  ignore: Sequence[str] = (), reason: str | None = None,
                  prefix: str | None = None) -> dict:
    """Adjust the writeup's intent membership. One verb, five
    adjustments, each with its own window (design-intent-scope §1.5).

    PROPOSED is the editable state: --add, --remove and --primary all
    work. FROZEN is a ratification, so it is narrower — --add stays legal
    as an explicit JOIN (it re-bills the cached prefix once, because
    block A changed), --remove is refused in favour of --defer (removing
    a ratified member would make the completion report a fiction), and
    --primary is legal only while `cursor == 0`, since re-pointing after
    the first accepted beat would split one writeup's transitions across
    two episodes — the exact fan-out the design forbids, arriving through
    the back door."""
    writeup = _writeup(db, manuscript, prefix)
    if writeup["status"] != "active":
        raise ValueError(f"writeup {writeup['id']} is {writeup['status']}")
    block = _intent_block(writeup)
    if not block:
        raise ValueError(
            f"writeup {writeup['id']} predates scope-derived intents and "
            f"carries no member set — it serves {writeup['intent_id'][:11]} "
            f"alone.")
    if not (add or remove or primary or defer or ignore):
        return {"writeup_id": writeup["id"], "intents": block,
                "intents_view": _members_view(block),
                "changed": []}
    frozen = block.get("state") == "frozen"
    relpath = writeup["file"]
    changed: list[str] = []

    for target in remove:
        member_id = _resolve_member_prefix(block, target)
        if member_id is None:
            raise LookupError(f"'{target}' is not a member of this writeup")
        if frozen:
            raise ValueError(
                f"the intent set was ratified at 'write plan'; removing "
                f"{member_id[:11]} now would make the completion report a "
                f"fiction. Record what actually happened instead: "
                f"'write intents --defer {member_id[:11]} --reason \"<why>\"'.")
        if member_id == block.get("primary"):
            # Legal while proposed: nothing has been attributed yet. The
            # primary is simply re-derived below, and a fresh tie there
            # is refused at the plan like any other.
            block["primary"] = None
        block["members"] = [m for m in block["members"] if m["id"] != member_id]
        block.setdefault("removes", []).append(
            {"id": member_id, "at": now_iso()})
        changed.append(f"removed {member_id[:11]}")

    for target in add:
        if _resolve_member_prefix(block, target):
            raise ValueError(f"'{target}' is already a member")
        intent = _find_intent(db, manuscript, target)
        if intent["status"] != "active":
            raise ValueError(
                f"intent {intent['id']} is {intent['status']}, not active")
        block["members"].append(_member_record(manuscript, relpath, intent))
        entry = {"id": intent["id"], "at": now_iso()}
        if frozen:
            entry["beat"] = writeup["cursor"]
        block.setdefault("adds", []).append(entry)
        block["ignored"] = [i for i in block.get("ignored") or []
                            if i != intent["id"]]
        changed.append(f"added {intent['id'][:11]}")

    if primary:
        member_id = _resolve_member_prefix(block, primary)
        if member_id is None:
            raise LookupError(
                f"'{primary}' is not a member of this writeup — join it "
                f"first: 'write intents --add {primary}'")
        if frozen and writeup["cursor"] > 0:
            raise ValueError(
                f"beat {writeup['cursor']} is already accepted and its "
                f"transitions are recorded against "
                f"{block['primary'][:11]}'s episode. Re-pointing the "
                f"primary now would split one writeup's transitions across "
                f"two episodes — one authorial act mined twice. The exit is "
                f"'write abandon' (it restores the pinned source version).")
        block["primary"] = member_id
        block["tied"] = []
        changed.append(f"primary {member_id[:11]}")

    if defer:
        if not frozen:
            raise ValueError(
                "--defer records a disposition on a RATIFIED set; while the "
                "set is still proposed the honest adjustment is "
                "'write intents --remove <id>'.")
        if not (reason or "").strip():
            raise ValueError(
                "--reason is required with --defer: an unexplained deferral "
                "teaches nothing (the same ground as 'write reject --reason').")
        member_id = _resolve_member_prefix(block, defer)
        if member_id is None:
            raise LookupError(f"'{defer}' is not a member of this writeup")
        if member_id == block.get("primary"):
            raise ValueError(
                f"{member_id[:11]} is the primary — it owns the attribution "
                f"for every beat already accepted, so it cannot be deferred.")
        block.setdefault("deferred", {})[member_id] = {
            "reason": reason.strip(), "at": now_iso(),
            "beat": writeup["cursor"]}
        changed.append(f"deferred {member_id[:11]}")

    for target in ignore:
        if _resolve_member_prefix(block, target):
            raise ValueError(
                f"'{target}' is already a member — --ignore is for an intent "
                f"that has come into scope and has NOT joined.")
        intent = _find_intent(db, manuscript, target)
        ignored = block.setdefault("ignored", [])
        if intent["id"] not in ignored:
            ignored.append(intent["id"])
        changed.append(f"ignored {intent['id'][:11]}")

    block["members"] = _with_roles(block["members"], block.get("primary"))
    if not block.get("primary") and block["members"]:
        _, candidates = _pick_primary(manuscript, relpath, block["members"])
        block["tied"] = candidates if len(candidates) > 1 else []
        if len(candidates) == 1:
            block["primary"] = candidates[0]
            block["members"] = _with_roles(block["members"], candidates[0])
    if not block["members"]:
        raise ValueError(
            "that would leave the writeup with no intent at all — a rewrite "
            "serves a goal. Add another first, or 'write abandon'.")
    _store_intent_block(db, writeup, block)
    if block.get("primary") and block["primary"] != writeup["intent_id"]:
        db.update("writeups", writeup["id"],
                  {"intent_id": block["primary"]})
    return {"writeup_id": writeup["id"], "intents": block,
            "intents_view": _members_view(block),
            "changed": changed, "state": block.get("state"),
            "rebills_prefix": bool(frozen and any(
                c.startswith("added") for c in changed))}


def _resolve_beat_tags(block: dict, beats: list) -> None:
    """A beat spec MAY name the intents it serves — prefixes, resolved to
    full ids in place and stored resolved, on the digest's `serves`
    precedent. A tag naming a non-member is refused by the same rule the
    digest's dangling cross-reference is: a dangling tag makes the
    disposition report decorative."""
    member_ids = {m["id"] for m in block.get("members") or []}
    for spec in beats:
        tags = spec.get("intents") if isinstance(spec, dict) else None
        if not tags:
            continue
        if not isinstance(tags, list):
            raise ValueError(
                'a beat\'s "intents" must be an array of intent id prefixes')
        resolved = []
        for tag in tags:
            hits = [i for i in member_ids if isinstance(tag, str) and tag in i]
            if len(hits) != 1:
                names = ", ".join(sorted(i[:11] for i in member_ids))
                raise ValueError(
                    f"a beat is tagged for '{tag}', which is not a member of "
                    f"this writeup — a dangling tag makes the disposition "
                    f"report decorative. Members: {names}. Join it "
                    f"('write intents --add {tag}') or drop the tag.")
            resolved.append(hits[0])
        spec["intents"] = resolved


def _freeze_intents(db: Database, manuscript: dict, writeup: dict,
                    beats: list) -> dict:
    """The ratification. `write plan` is already the author's gate
    (design §13.3), so the set rides it: three refusals, each naming the
    one command that clears it, and then the set is frozen and block A's
    INTENTS section is fixed for the rest of the writeup."""
    block = _intent_block(writeup)
    if not block:
        return {}
    # Beat tags are resolved and validated on EVERY plan, including a
    # replan of a frozen writeup: a tag left as an unresolved prefix
    # would never match a member id, and the disposition report would
    # quietly count the beat as untagged.
    _resolve_beat_tags(block, beats)
    if block.get("state") == "frozen":
        return block
    if not block.get("primary"):
        lines = []
        for member_id in block.get("tied") or []:
            member = next((m for m in block["members"]
                           if m["id"] == member_id), None)
            if member:
                lines.append(f"  [{member_id[:11]}] {member['statement']}")
        first = (block.get("tied") or [""])[0][:11]
        raise ValueError(
            "two or more intents are tied for primary and the writeup's "
            "episode hangs off it —\n" + "\n".join(lines)
            + f"\nName it, then re-run: write intents --primary {first}")
    for member in block["members"]:
        row = db.one("SELECT * FROM declared_intents WHERE id = ?",
                     (member["id"],))
        status = row["status"] if row else "gone"
        if status != "active":
            raise ValueError(
                f"member [{member['id'][:11]}] is {status}, not active — "
                f"\"{member['statement']}\". A silently changed set is not a "
                f"ratified set: drop it yourself, then re-run: "
                f"write intents --remove {member['id'][:11]}")
    block["state"] = "frozen"
    block["frozen_at"] = now_iso()
    block["members"] = _with_roles(block["members"], block["primary"])
    return block


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
            stranded_beats = ", ".join(
                f"n={row['batch_index']}" for row in stranded)
            raise ValueError(
                f"a draft is still awaiting your verdict on beat "
                f"{stranded_beats} — "
                f"replacing the plan would strand it as 'proposed' forever "
                f"and lose the reason you are replanning for. Settle it "
                f"first: 'write reject --reason \"<why the plan is wrong>\"' "
                f"(that reason IS the evidence for the replan), or "
                f"'write accept' to keep the draft.")
    # The intent set FREEZES here, before any plan mutation. `write plan`
    # is already the author's ratification gate (design §13.3), so the
    # set rides it and the success line prints it: ratifying the plan
    # knowingly ratifies the intents, with no second confirmation verb.
    # A replan of a frozen writeup leaves membership and frozen_at alone.
    block = _freeze_intents(db, manuscript, writeup, beats)
    meta = loads(writeup["metadata"], {})
    if block:
        meta["intents"] = block
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
            "added": len(fresh), "plan": new_plan, "cursor": writeup["cursor"],
            "intents": block,
            "intents_view": (_members_view(block)
                             if block else [])}


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


def _scope_drift(db: Database, manuscript: dict, writeup: dict,
                 block: dict) -> tuple[list[dict], list[dict]]:
    """What has moved under a ratified set (design-intent-scope §1.6).

    `newly_in_scope` is derived-now minus members minus ignored: an
    intent DECLARED or RE-SCOPED after ratification. It is reported and
    never joined — membership after the freeze changes only by an
    authorial verb. `stale_members` are members whose intent has since
    completed or been abandoned: a note, not a block, because the writeup
    is still honest about having served them."""
    from . import passes

    if not block:
        return [], []
    member_ids = {m["id"] for m in block.get("members") or []}
    ignored = set(block.get("ignored") or [])
    newly = [
        {"id": row["id"], "statement": row["statement"],
         "scope": row["scope"],
         "tier": passes.scope_tier(manuscript, writeup["file"], row["scope"])
         or "outside"}
        for row in passes.intents_in_scope(db, manuscript, writeup["file"],
                                           "active")
        if row["id"] not in member_ids and row["id"] not in ignored]
    stale = []
    for member in block.get("members") or []:
        row = db.one("SELECT status FROM declared_intents WHERE id = ?",
                     (member["id"],))
        status = row["status"] if row else "gone"
        if status != "active":
            stale.append({**member, "status": status})
    return sorted(newly, key=lambda m: m["id"]), stale


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
    # The resume view warns about a marker the author has edited around;
    # it never repairs one. (A file that is STILL only the placeholder is
    # the ordinary mid-rewrite state and is not flagged here — the
    # drafting context already says the essay is in flight.)
    _path = Path(manuscript["path"]) / writeup["file"]
    _disk = _path.read_text(encoding="utf-8") if _path.exists() else ""
    block = _intent_block(writeup)
    newly, stale_members = _scope_drift(db, manuscript, writeup, block)
    return {
        "writeup": {k: writeup[k] for k in
                    ("id", "intent_id", "file", "mode", "status",
                     "source_version_id", "cursor")},
        "intents": block,
        "intents_view": (_members_view(block)
                         if block else []),
        "newly_in_scope": newly,
        "stale_members": stale_members,
        "plan": plan,
        "current_beat": current,
        "pending_proposal": dict(pending) if pending else None,
        "learnings": loads(writeup["learnings"], []),
        "tallies": _beat_tallies(db, writeup),
        "brief": meta.get("brief"),
        "created_file": bool(meta.get("created_file")),
        "digest": meta.get("digest"),
        # One model drafts one writeup (design §8): the cache is
        # model-scoped and the voice should not have a seam. Both are
        # metadata, so a resumed writeup can show the seam with no schema
        # change and no index.
        "drafting_model": meta.get("drafting_model"),
        "drafting_models": meta.get("drafting_models") or [],
        "marker_present": marker_present(_disk) and not is_placeholder(_disk),
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


def cache_cold(client, first_draft: bool, cache_read: int) -> bool:
    """Whether this draft should print §4's loud zero-cache-reads warning.

    The whole verification of the cache layer, in one predicate — no test
    can watch `cache_control` in flight (the hermetic suite is a raw-HTTP
    stub, the live-optional suite runs on the cheap tier), so the usage
    line is the only instrument the author has, and this is what decides
    whether it shouts.

    All four conditions are load-bearing. Caching must be ON (`cache =
    false` is a deliberate retreat, not a fault). It must not be the
    writeup's FIRST draft (there is nothing yet to hit). The model must
    actually advertise prompt caching, or the warning fires on every
    offline run and trains the author to ignore the one time it matters.
    And the read must be zero — a beat loop showing zero cache reads has
    a silent invalidator and, per §4, should say so loudly."""
    from . import llm as llm_mod

    return bool(client.cache and not first_draft and cache_read == 0
                and llm_mod.caching_available(client.model, client.provider))


def write_draft(db: Database, manuscript: dict, config: dict,
                prefix: str | None = None, dry_run: bool = False,
                on_start=None) -> dict:
    """Draft the current beat programmatically and register it.

    The one LLM call on the write path (design-write-draft.md §1). Every
    gate runs BEFORE the call, so a refused draft costs nothing and leaves
    no row; and `write_propose` is the LAST statement of the success path,
    so no failed, refused or unparseable draft can ever leave a pending
    proposal (§1.6's ordering invariant).

    `on_start(info)` is called once the gates have passed and the payload
    is assembled, immediately before the model call — a beat with adaptive
    thinking runs minutes, and a terminal that says nothing first looks
    hung (risk R-d).

    Deliberately NOT a gate: summary freshness. `write start` already
    gated it; a neighbour that goes stale mid-chapter is warned about, not
    blocked, and the `!! ` lines travel into the payload so the model sees
    the warning too (Q1, default = warn)."""
    from . import llm as llm_mod
    from . import summaries as sums
    from . import writing

    writeup = _writeup(db, manuscript, prefix)
    if writeup["status"] != "active":
        raise ValueError(f"writeup {writeup['id']} is {writeup['status']}")
    _checkout_gate(db, manuscript, writeup["file"])
    beat = _current_beat(writeup)
    if not (config.get("llm", {}) or {}).get("enabled"):
        raise ValueError(
            "the LLM is disabled ([llm] enabled = false) — beat drafting is "
            "the one call that cannot degrade to a heuristic. Enable it, or "
            "draft the beat yourself and register it with "
            "'write propose --why …'.")
    # ONE capture for this invocation (Stream AC's convention): the
    # context the model is sent is the context assembled here, not a
    # second read a parallel session could have changed underneath it.
    capture = sums.capture(db, manuscript)
    payload = writing.assemble(db, manuscript, writeup, beat,
                               capture=capture)
    meta = loads(writeup["metadata"], {})
    previous_model = meta.get("drafting_model")
    first_draft = not previous_model
    info = {"writeup": writeup, "beat": beat,
            "beats_drafted": len(meta.get("drafting_beats", [])),
            "payload_hashes": payload.hashes,
            "payload_sizes": payload.sizes,
            "prompt_location": writing.prompt_location()}
    if dry_run:
        # The `[writing]`-model and vendor-key gates are BELOW this return,
        # deliberately (author ruling 2026-08-30; design §15.10). Assembling
        # and printing the payload needs neither a model nor a key, and with
        # `[writing]` absent from the shipped config the dry run IS the
        # drafting flow: it hands the conversation the exact deterministic
        # payload and the registered prompt to draft against. Gating it on
        # the billed path's configuration withheld the audit instrument
        # along with the call it was meant to stop.
        #
        # Every gate ABOVE this line still runs on a dry run — writeup
        # status, the checkout gate, a ratified plan and a beat to draft,
        # `[llm] enabled` — so a dry run on an unplanned writeup refuses
        # rather than printing an empty payload. Nothing mutating precedes
        # it: on_start, the call, and write_propose are all below.
        #
        # The model is REPORTED when `[writing]` names one (reading a name
        # is not a gate) and is None when it does not.
        named = ((config.get("writing", {}) or {}).get("model") or "").strip()
        return {**info, "model": named or None, "model_changed_from": None,
                "dry_run": True, "payload": payload}
    client = llm_mod.writing_llm(config)   # refuses on [writing]; key in draft()
    changed_from = (previous_model
                    if previous_model and previous_model != client.model
                    else None)
    info = {**info, "model": client.model, "model_changed_from": changed_from}
    if on_start:
        on_start(info)
    result = client.draft(payload.system_blocks, payload.user_blocks)
    parsed = writing.parse_reply(result.text)
    usage = {"line": client.stats_line(),
             "cache_read": result.cache_read,
             "cache_write": result.cache_write,
             "input_tokens": result.prompt_tokens,
             "output_tokens": result.completion_tokens}
    usage["cache_cold"] = cache_cold(client, first_draft, result.cache_read)
    if isinstance(parsed, writing.Blocked):
        # §13.3 made mechanical: a beat that needs an ungrounded fact
        # becomes a question, never an invention. Nothing is registered
        # and the cursor does not move.
        return {**info, "blocked": True, "reason": parsed.reason,
                "question": parsed.question, "usage": usage}

    # Registration is the last thing that can fail, so it goes first among
    # the writes. Unmodified, unwrapped, un-parameterized: the row, the
    # mandatory --why, supersede-on-redraft, the verdict evidence and the
    # policy reinforcement are byte-for-byte what `write propose` makes.
    proposal = write_propose(db, manuscript, text=parsed.text,
                             explanation=parsed.why, prefix=prefix)

    # Bookkeeping AFTER the row exists. Recording it first would leave a
    # writeup claiming to have been drafted by a model on a beat that was
    # never registered — a raced checkout, or any late refusal inside
    # write_propose, and the metadata would be residue about work that
    # did not happen. The model-change warning is unaffected: it was
    # computed from the metadata as it stood before this call.
    meta["drafting_model"] = client.model
    models = meta.get("drafting_models") or []
    if client.model not in models:
        models.append(client.model)
    meta["drafting_models"] = models
    beats = meta.get("drafting_beats") or []
    if beat["n"] not in beats:
        beats.append(beat["n"])
    meta["drafting_beats"] = beats
    db.update("writeups", writeup["id"], {"metadata": json.dumps(meta)})
    return {**info, "blocked": False, "why": parsed.why,
            "self_check": parsed.self_check, "draft": parsed.text,
            "guidance_id": proposal["guidance_id"], "usage": usage}


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
    if is_placeholder(existing):
        # The FIRST accepted beat REPLACES the placeholder rather than
        # appending after it. Without this the marker would be prepended
        # to the essay and would ship — the "never in a finished essay"
        # leak. (write_complete is the second, independent check.)
        existing = ""
    prefix_text = existing.rstrip("\n") + "\n\n" if existing.strip() else ""
    path.write_text(prefix_text + accepted + "\n", encoding="utf-8")

    session, _ = ensure_session(db, manuscript)
    # ONE episode for the whole verb, resolved BEFORE the collect: the
    # transitions and the verdict must not be able to disagree about
    # which intent this beat served.
    episode = _writeup_episode(db, manuscript, writeup, session)
    report = collect(db, manuscript, config, source="write-accept",
                     episode=episode)
    if decision == "modified":
        meta = loads(proposal["metadata"], {})
        meta["accepted_text"] = accepted
        # The beat's birth stamp names the chat that PROPOSED it; this
        # names the one that accepted it. When they differ — the resumable
        # writeup case — that seam is the thing the author most wants to
        # see, and it is one line.
        meta["accepted_by"] = clients.current().key()
        db.update("guidance_history", proposal["id"],
                  {"metadata": json.dumps(meta)})
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
    episode = _writeup_episode(db, manuscript, writeup, session)
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


def _intent_dispositions(writeup: dict, block: dict) -> list[dict]:
    """Per member: served, deferred, or UNSERVED (design §1.8).

    A beat spec MAY tag the intents it serves, like the digest's point
    ids; an UNTAGGED beat counts toward every member. "Accepted" means
    the cursor advanced past the beat — `write_accept` is the only thing
    that advances it."""
    if not block:
        return []
    plan = loads(writeup["plan"], [])
    accepted = plan[:writeup["cursor"]]
    untagged = any(not (b.get("intents") or []) for b in accepted)
    tagged: set[str] = set()
    for beat in accepted:
        tagged.update(beat.get("intents") or [])
    deferred = block.get("deferred") or {}
    out = []
    for member in block.get("members") or []:
        entry = dict(member)
        if member["id"] in deferred:
            entry["disposition"] = "deferred"
            entry["reason"] = deferred[member["id"]].get("reason")
        elif untagged or member["id"] in tagged:
            entry["disposition"] = "served"
        else:
            entry["disposition"] = "unserved"
        entry["beats"] = len(accepted)
        out.append(entry)
    return out


def _record_served_by(db: Database, writeup: dict,
                      dispositions: list[dict]) -> None:
    """One dedup-append entry on each member intent's OWN row, so a
    chapter-wide intent's completion analysis can see the three essays
    rewritten under it without a group object ever existing. Deduped by
    writeup id — re-completing never doubles a row."""
    stamp = now_iso()
    for entry in dispositions:
        row = db.one("SELECT * FROM declared_intents WHERE id = ?",
                     (entry["id"],))
        if row is None:
            continue
        meta = loads(row["metadata"], {}) or {}
        served = [s for s in (meta.get("served_by") or [])
                  if s.get("writeup") != writeup["id"]]
        served.append({"writeup": writeup["id"], "file": writeup["file"],
                       "role": entry.get("role", "secondary"),
                       "disposition": entry["disposition"],
                       "beats": entry.get("beats", 0), "at": stamp})
        meta["served_by"] = served
        db.update("declared_intents", row["id"],
                  {"metadata": json.dumps(meta)})


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
    # BEFORE anything mutates. A file that is still ONLY the placeholder
    # has had no beat accepted: completing here would record the marker
    # as the finished essay, register a toc entry for it, and leave the
    # author's real essay only in the database. That is structural
    # integrity, not editorial judgment, so it refuses rather than warns
    # (§13.2's report-don't-block is about unaccounted digest points).
    # `write abandon` is what the author means when zero beats landed.
    disk_text = path.read_text(encoding="utf-8") if path.exists() else ""
    if is_placeholder(disk_text):
        raise ValueError(
            f"{writeup['file']} still holds only the mid-rewrite "
            f"placeholder — no beat has been accepted, so completing would "
            f"record the marker as the finished essay and leave the old one "
            f"only in the database. Put the old essay back with "
            f"'write abandon', or draft a beat first.")
    # The weaker case: the author hand-edited around the marker. That is
    # AUTHORED content, so it is reported and never silently removed.
    marker_survives = marker_present(disk_text)
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
    block = _intent_block(writeup)
    dispositions = _intent_dispositions(writeup, block)
    if accounting is not None:
        meta["accounting_at_complete"] = {
            "kept": len(accounting["kept"]),
            "removed": len(accounting["removed"]),
            "unaccounted": accounting["unaccounted"],
        }
    if dispositions:
        # The same metadata pass that persists the removal accounting.
        meta["intent_dispositions"] = {
            e["id"]: e["disposition"] for e in dispositions}
    if accounting is not None or dispositions:
        db.update("writeups", writeup["id"], {"metadata": json.dumps(meta)})
    _record_served_by(db, writeup, dispositions)
    # The writeup's LAST collect, and it belongs to the primary exactly as
    # every per-beat one does. It is usually a no-op — nothing has moved
    # since the final accept — but "usually" is not "never": a doc pull,
    # or the author's own hand edit between the last beat and this verb,
    # makes it a real collect with real transitions, and those would land
    # on the session's most recently created open episode, whatever intent
    # it belonged to. Same defect, same fix, one site later.
    session, _ = ensure_session(db, manuscript)
    report = collect(db, manuscript, config, source="write-complete",
                     episode=_writeup_episode(db, manuscript, writeup,
                                              session))
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
            "intent_dispositions": dispositions,
            "beats_done": writeup["cursor"], "beats_unwritten": remaining,
            "tallies": _beat_tallies(db, writeup),
            "learnings": loads(writeup["learnings"], []),
            # A completed essay's record should say what wrote it (Q7).
            "drafting_models": meta.get("drafting_models") or [],
            "accounting": accounting, "toc_registered": toc_registered,
            "toc_stanza": toc_stanza, "toc_placement": placement,
            "marker_present": marker_survives,
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

def get_plan(db: Database, manuscript: dict) -> dict:
    """The writing plan: placements for unrealized concepts from the graph
    and TOC order."""
    from .plan import build_plan

    return build_plan(db, manuscript)


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
                    query: str | None = None,
                    text: str | None = None) -> dict:
    """The relevant slice of the graph, compact. `query` matches name,
    notes, or aliases (case-insensitive substring). `file` selects
    concepts realized in that essay: introduced there, or whose name or
    alias appears in its text. Edges are restricted to the selected
    nodes.

    `text` is the CAPTURE OVERRIDE, and it closes a pre-existing
    one-capture violation rather than adding a feature. With `file` and
    no `text` this re-reads the disk through `read_manuscript_files` —
    a SECOND read inside a verb that already took exactly one capture.
    `filtering._concept_notes` did precisely that, so `filter run`'s
    CONCEPT NOTES could differ between window N and window N+1 if a
    parallel session edited the file, silently forfeiting the cached
    prefix that block A exists to hold. Supplying the caller's own
    captured text replaces the disk read for the file-scoping match, and
    the file is still RESOLVED against the manuscript so an unknown name
    is still a LookupError. Defaults to today's behaviour."""
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
        haystack = (files[rel] if text is None else text).lower()
        nodes = [n for n in nodes
                 if n.get("introduced_in") == rel
                 or n["name"].lower() in haystack
                 or any(a.lower() in haystack
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


# ====================================================================
# The filter pass (docs/filter-pass-design.md)
#
# A LENS reads one essay whole and reports findings. A FILTER reads one
# essay UNIT BY UNIT and proposes an edit to each unit — one ratified
# prompt applied to every paragraph, each conditioned on what came
# before. Its output is a staged edit through the finding→edit door, not
# a guidance row.
#
# Blackboard doctrine (§15.19), the review-checklist line, answered once
# for the whole surface:
#
#  - READS are capture-consistent or pinned. Every verb takes exactly
#    ONE `summaries.capture` and hands it to the gate AND to the thing
#    the gate gates; a run additionally PINS `source_version_id` at
#    start and `filter_record` re-checks it, because the gap between
#    assembly and registration is a conversational turn the write path
#    does not have.
#  - WRITES invalidate loudly. An in-flight neighbour refuses, a drifted
#    pin refuses, a second filter's proposals on the same file warn by
#    name, a marked file is a state the bytes themselves declare, and a
#    run whose artifact class changed under it says so rather than
#    switching.
#  - ATTRIBUTION refuses rather than guessing. Verdict evidence carries
#    `episode_id = NULL`; the resolve's collect passes NO_EPISODE. A
#    filter has no goal to be filed under, so it is filed under none.
# ====================================================================

FILTER_ORIGIN = "filter"
FILTER_EVIDENCE = "filter_edit"
LENS_ORIGIN = "lens"
LENS_EVIDENCE = "lens_edit"

# A run whose proposals cover more than this share of the essay is
# almost always a PROMPT fault rather than an essay that bad. It warns
# and never blocks (§15.13): the remedy is `filter show` and an edit,
# which is the author's, and nothing here is entitled to make it.
PROMPT_FAULT_SHARE = 0.75


def _filter_capture(db: Database, manuscript: dict, file: str,
                    verb: str, checkout: bool = True
                    ) -> tuple[str, str, tuple, str]:
    """The one capture, the three in-flight/placeholder refusals, the
    file's text, and the pronunciation dictionary — shared by run,
    record, prelude, push and settle so a gate and the thing it gates
    can never read separately (§14.7's dangerous shape).

    Returns `(relpath, text, capture, dictionary_text)`.

    `checkout=False` is for exactly one caller: the DOC-mode settle.
    `_checkout_gate` refuses a file whose Doc entry is `checked_out`, and
    `write_pending_forms`'s levelling push sets that flag — so in doc
    mode the file is checked out BY DESIGN (the Doc *is* the working
    copy, which is the whole point of the mode) and the gate would
    refuse the one verb whose job is to read the Doc back. The gate is
    right for run, record, prelude, push and the local settle, and wrong
    for that one; `critique resolve` has no gate for the same reason.

    The dictionary is read HERE and nowhere else, which is the whole of
    the bend this feature puts in the blackboard doctrine (§15.22, D4).
    The capture's `texts` map is built from `reading_order`, which the
    sidecar flag removes the dictionary from, so it cannot be in the
    capture without putting the dictionary back into the reading order.
    The two alternatives — widening `summaries.capture`'s tuple arity for
    a non-prose sidecar, or pinning the dictionary to the run's version
    (which would make an accepted pronunciation invisible until the run
    ended, contradicting the Sponsor's suppression ruling) — both lost.
    What holds is the doctrine's actual requirement: ONE READ PER
    INVOCATION, with the gate and the thing it gates reading the same
    bytes."""
    from . import summaries as sums

    if checkout:
        _checkout_gate(db, manuscript, file)
    capture = sums.capture(db, manuscript)
    texts, _unlisted, inflight = capture
    rel = file if file in texts else next(
        (r for r in texts if Path(r).name == file), None)
    if rel is None:
        # Named, before the generic refusal (§15.22 §2.5 row 13). The
        # sidecar is out of the capture by construction, so the generic
        # "not in the reading order" already fires — but it reads as a
        # missing file to an author looking at the tab in their own Doc.
        structure.refuse_sidecar(Path(file).name)
        raise LookupError(
            f"'{file}' is not in the manuscript's reading order")
    if rel in inflight:
        # The refusal borrows `passes.build_context`'s wording verbatim:
        # what is on disk is a placeholder, not the essay, so a filter
        # over it would propose edits to a marker.
        raise ValueError(
            f"{rel} is being rewritten right now by an active writeup — "
            f"what is on disk is a placeholder, not the essay, so a "
            f"{verb} over it would propose edits to a marker. Finish the "
            f"writeup ('write complete') or put the old essay back "
            f"('write abandon').")
    text = texts[rel]
    if is_placeholder(text):
        raise ValueError(
            f"{rel} holds only the mid-rewrite placeholder — a marker is "
            "not prose, and a filter has nothing to pass through.")
    return rel, text, capture, _dictionary_text(manuscript)


def _dictionary_text(manuscript: dict) -> str:
    """`pronunciations.md`'s bytes, or "" when the author has none yet.

    An absent file parses as an EMPTY dictionary with no error, which is
    the common first-run case and is why the file is never seeded by a
    filter path (§15.22 §2.7): an empty table materializing because a
    filter RAN is a file in the author's vault they did not ask for, and
    it immediately becomes a Doc tab, a version-history entry and a
    diff."""
    from . import pronunciations as pron

    path = Path(manuscript["path"]) / pron.FILENAME
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _active_filter_run(db: Database, manuscript_id: str, name: str,
                       file: str) -> dict | None:
    row = db.one(
        "SELECT * FROM filter_runs WHERE manuscript_id = ? AND filter = ? "
        "AND file = ? AND status = 'active' ORDER BY created_at DESC LIMIT 1",
        (manuscript_id, name, file))
    return dict(row) if row else None


def _filter_run_row(db: Database, manuscript: dict, file: str,
                    name: str | None = None) -> dict:
    """The active run this verb is about. With no `name`, the file's ONE
    active run — ambiguity is refused rather than guessed at, because
    two filters on one essay is legitimate and picking one for the
    author is not."""
    mid = manuscript["id"]
    if name:
        run = _active_filter_run(db, mid, name, file)
        if run is None:
            raise LookupError(
                f"no active run of '{name}' on {file} — start one with "
                f"'filter run {name} {file}'.")
        return _touched_filter_run(db, run)
    rows = [dict(r) for r in db.all(
        "SELECT * FROM filter_runs WHERE manuscript_id = ? AND file = ? "
        "AND status = 'active' ORDER BY created_at", (mid, file))]
    if not rows:
        raise LookupError(f"no active filter run on {file}.")
    if len(rows) > 1:
        names = ", ".join(r["filter"] for r in rows)
        raise LookupError(
            f"{file} has {len(rows)} active filter runs ({names}) — name "
            "the one you mean.")
    return _touched_filter_run(db, rows[0])


# ------------------------------------------------------ the transport
#
# A filter run has a TRANSPORT: the road its accepted edits take to the
# author's eyes. `local` is the marked file in Obsidian; `doc` is the
# same `<<old>>{{new}}` forms written surgically into the essay's tab of
# the master Doc (design-filter-doc-settle §2.1).
#
# It lives in the run's `metadata` JSON, not in a column: it is new
# state on an existing row and the house idiom for that is the metadata
# blob, which needs no migration and cannot collide with an index.
# ABSENT is meaningful and is the birth value — *no transport chosen
# yet* — so a default would lie about a run that has staged and triaged
# edits and not yet decided where to read them.
#
# Frozen at the TRANSPORT VERB, not at run start (D-3): nothing between
# `filter run` and the first transport verb depends on it, and freezing
# earlier would refuse the legitimate late choice ("I have triaged
# thirty of these; I would like to read them in the Doc after all") for
# no mechanical reason. Once set it is frozen for the life of the run —
# switching is resolve-then-rerun. There is no toggle and no --mode flag
# on any verb, because a run whose forms are half in the Doc and half on
# disk is a run nobody can reason about.

def _run_mode(run: dict) -> str | None:
    """This run's chosen transport: 'local', 'doc', or None for a run
    that has not taken one yet."""
    return (loads(run.get("metadata"), {}) or {}).get("mode")


def _freeze_run_mode(db: Database, run: dict, mode: str) -> dict:
    """Record the transport, once. Returns the refreshed run row."""
    meta = loads(run.get("metadata"), {}) or {}
    meta["mode"] = mode
    payload = json.dumps(meta)
    db.update("filter_runs", run["id"], {"metadata": payload})
    return dict(run, metadata=payload)


def _touched_filter_run(db: Database, row: dict) -> dict:
    """The provenance stamp (§15.15), and the POST-touch row.

    A filter run is assembled in one chat and settled in another as the
    ORDINARY case — assembly and registration are two turns — so the
    touched-by list is not an edge case here, it is the shape of the
    work. Touch first and re-read, exactly as `_touched` does for
    writeups, so a caller's read-modify-write of `metadata` cannot
    clobber the clients list."""
    clients.touch(db, "filter_runs", row, verb=clients.current_verb())
    fresh = db.one("SELECT * FROM filter_runs WHERE id = ?", (row["id"],))
    return dict(fresh) if fresh is not None else row


def _version_text(db: Database, version_id: str | None,
                  file: str) -> str | None:
    if not version_id:
        return None
    row = db.one("SELECT files FROM manuscript_versions WHERE id = ?",
                 (version_id,))
    return loads(row["files"], {}).get(file) if row else None


def _latest_version_id(db: Database, manuscript_id: str) -> str | None:
    row = db.one(
        "SELECT id FROM manuscript_versions WHERE manuscript_id = ? "
        "ORDER BY version_no DESC LIMIT 1", (manuscript_id,))
    return row["id"] if row else None


def _settled_runs(db: Database, manuscript_id: str, name: str,
                  file: str) -> list[dict]:
    return [dict(r) for r in db.all(
        "SELECT * FROM filter_runs WHERE manuscript_id = ? AND filter = ? "
        "AND file = ? AND status = 'settled' ORDER BY created_at",
        (manuscript_id, name, file))]


def _run_threads(db: Database, manuscript_id: str, run: dict) -> list[dict]:
    return [dict(r) for r in db.all(
        "SELECT * FROM doc_threads WHERE manuscript_id = ? AND "
        "origin_type = ? AND file = ? AND origin_id LIKE ? "
        "ORDER BY created_at, id",
        (manuscript_id, FILTER_ORIGIN, run["file"],
         f"{run['id']}:{run['file']}:%"))]


def _run_tallies(threads: list[dict]) -> dict:
    return {
        "proposed": len(threads),
        "accepted": sum(1 for t in threads
                        if t["state"] in ("accepted", "written", "cleaned")),
        "rejected": sum(1 for t in threads
                        if t["state"] in ("rejected", "declined")),
        "open": sum(1 for t in threads if t["state"] == "proposed"),
    }


def _other_active_filters(db: Database, manuscript_id: str, rel: str,
                          name: str) -> str | None:
    """The warning a run's CREATION carries when another filter already
    has an active run on this file.

    Warn, never refuse: two filters on one essay is legitimate work, and
    refusing would be the machine deciding the author's order (§15.19
    rule 3 — invalidate loudly, never silently). Their staged edits CAN
    collide on one unit; whichever settles second then hits
    `compose_marked_text`'s drift check and refuses loudly, which is a
    safe failure rather than a silent wrong answer."""
    others = [dict(r) for r in db.all(
        "SELECT * FROM filter_runs WHERE manuscript_id = ? AND file = ? "
        "AND status = 'active' AND filter != ?", (manuscript_id, rel, name))]
    if not others:
        return None
    return (f"{', '.join(r['filter'] for r in others)} already has an "
            f"active run on {rel}. Two filters on one essay is fine, but "
            f"their edits can collide on the same unit — whichever settles "
            f"second will refuse on the drift check.")


def filter_add(manuscript: dict, name: str, text: str) -> dict:
    from . import filters as flt

    path, meta = flt.add_filter(manuscript, name, text)
    return {"name": name, "path": str(path), "class": meta["class"],
            "state": meta["state"], "prelude": meta["prelude"],
            "class_help": flt.CLASS_HELP[meta["class"]]}


def filter_run(db: Database, manuscript: dict, config: dict, name: str,
               file: str, window: int | None = None,
               from_unit: int | None = None, again: bool = False,
               native: bool = False) -> dict:
    """Assemble one window's payload. **Without `--native` this makes no
    model call at all** — it prints the payload and the conversation
    drafts the reply against it.

    The flag polarity is INVERTED against `write draft --dry-run`, and
    deliberately. That flag exists because the verb was built billed and
    the ruling came later, so it now names the DEFAULT flow, which is
    backwards. `filter run` is chat-first from its first commit, so the
    zero-call behaviour needs no flag. Two verbs with opposite defaults
    is a real cost; `--dry-run` is accepted as a silent no-op alias for
    muscle memory, and the tutorial and the skill both say the
    inversion out loud."""
    from . import filters as flt
    from . import filtering as fg

    mid = manuscript["id"]
    rel, text, _capture, dictionary = _filter_capture(
        db, manuscript, file, "filter run")
    meta, body = flt.load_filter(manuscript, name)
    warnings: list[str] = []
    if marker_present(text):
        # The weaker check (§14.3's rule): the marker survived inside a
        # file that is no longer just the placeholder. Warn, never block.
        warnings.append(
            f"{rel} still carries the mid-rewrite marker line inside "
            "otherwise-real prose — the filter will see it as a unit.")

    units = passes.paragraphs_of(text)
    run = _active_filter_run(db, mid, name, rel)
    if run is None and meta["class"] == "global":
        # BEFORE the row is created, deliberately: a global run whose
        # registry has never been written is not a run that has stalled,
        # it is a run that has not begun, and leaving a half-made row
        # behind would make `filter status` report one that does not
        # exist.
        raise ValueError(
            f"'{name}' is a GLOBAL filter: every unit is judged against a "
            f"frozen registry read from the whole essay, and there is no "
            f"run yet. Run 'filter prelude {name} {rel}' first.")
    if run is not None and again:
        raise ValueError(
            f"a run of '{name}' on {rel} is already active (cursor at "
            f"unit {run['cursor']} of {run['unit_count']}). '--again' "
            f"starts a fresh one, so settle this one ('filter resolve "
            f"{rel}') or drop it ('filter abandon {name} {rel}') first.")
    if run is None:
        # M1 — the identical-text refusal, the only HARD guarantee of
        # approximate idempotency (§1.6). One hash comparison against
        # the text a prior settled run produced.
        if not again:
            for prior in _settled_runs(db, mid, name, rel):
                produced = _version_text(db, prior["result_version_id"], rel)
                if produced is not None and produced == text:
                    t = _run_tallies(_run_threads(db, mid, prior))
                    raise ValueError(
                        f"{name} already ran on this exact text (run "
                        f"{prior['id'][:11]}, settled "
                        f"{(prior['created_at'] or '')[:10]}, "
                        f"{t['accepted']} accepted / {t['rejected']} "
                        f"rejected). Nothing has changed since. To look "
                        f"anyway: --again")
        collision = _other_active_filters(db, mid, rel, name)
        if collision:
            warnings.append(collision)
        # `class` is a reserved word, so the column is set by
        # subscript rather than as a keyword — and it is FROZEN here:
        # editing _filters/<name>.md mid-run cannot change a live run's
        # mechanics, it can only make the run say the artifact drifted.
        row = ko_fields("fr")
        row.update(manuscript_id=mid, filter=name, file=rel,
                   source_version_id=_latest_version_id(db, mid),
                   unit_count=len(units), cursor=0, state=None,
                   registry=None, result_version_id=None, status="active")
        row["class"] = meta["class"]
        db.insert("filter_runs", row)
        run = _touched_filter_run(db, row)
    else:
        run = _touched_filter_run(db, run)
        if run["class"] != meta["class"]:
            warnings.append(
                f"the artifact's class is now '{meta['class']}'; this run "
                f"is '{run['class']}' and will finish as one. The class is "
                f"frozen at run start so that editing _filters/{name}.md "
                f"mid-run cannot change a live run's mechanics.")
        if run["unit_count"] != len(units):
            warnings.append(
                f"{rel} had {run['unit_count']} units when this run "
                f"started and has {len(units)} now.")

    if run["class"] == "global" and not (run["registry"] or "").strip():
        raise ValueError(
            f"'{name}' is a GLOBAL filter: every unit is judged against a "
            f"frozen registry read from the whole essay, and this run has "
            f"none yet. Run 'filter prelude {name} {rel}' first.")

    start = from_unit if from_unit else run["cursor"] + 1
    if not 1 <= start <= len(units):
        raise ValueError(
            f"unit {start} is out of range — {rel} has {len(units)} units.")
    span = window if window and window > 0 else len(units)
    end = min(start + span - 1, len(units))
    threads = staging.door_threads(db, mid, rel,
                                   states=("proposed", "accepted",
                                           "rejected", "written", "cleaned"),
                                   origin_type=FILTER_ORIGIN)
    run_meta = loads(run["metadata"], {})
    run_meta["window"] = [start, end]
    db.update("filter_runs", run["id"], {"metadata": json.dumps(run_meta)})
    run["metadata"] = json.dumps(run_meta)
    payload = fg.assemble(db, manuscript, run, body, units, (start, end),
                          threads, text=text, dictionary=dictionary,
                          summaries=meta.get("summaries", False),
                          profiles=meta.get("profiles") or [])
    if (meta.get("prelude") and not loads(run["metadata"], {}).get("prelude")):
        # Optional where the registry is required (§15.22 §3.1): a run
        # whose pronunciation prelude never ran is still a correct audio
        # pass, so this is one printed line and never a refusal. The
        # global registry's hard refusal stays hard, because a global
        # unit judged against no registry is judged against nothing.
        warnings.append(
            f"'{name}' declares a {meta['prelude']} prelude and this run "
            f"has not had one. The units below are judged normally; the "
            f"prelude only proposes dictionary rows. To run it: "
            f"'filter prelude {name} {rel}'.")
    info = {"run": run, "filter": name, "file": rel, "class": run["class"],
            "window": (start, end), "unit_count": len(units),
            "payload": payload, "payload_hashes": payload.hashes,
            "payload_sizes": payload.sizes,
            "prompt_location": fg.prompt_location(),
            "warnings": warnings, "native": False}
    if not native:
        return info
    return {**info, "native": True,
            **_filter_native(db, manuscript, config, run, payload, units,
                             (start, end), body)}


def _filter_native(db: Database, manuscript: dict, config: dict, run: dict,
                   payload, units: list[str], window: tuple[int, int],
                   body: str) -> dict:
    """The billed path. Refuses when `[filtering]` is absent and names
    the chat flow, so a missing section can never make the pass
    unusable."""
    from . import filtering as fg
    from . import llm as llm_mod

    client = llm_mod.filtering_llm(config)      # refuses on [filtering]
    raw = client.complete_json_blocks(payload.system_blocks,
                                      payload.user_blocks)
    if raw is None:
        raise RuntimeError(
            "the filter model returned nothing (disabled, or the call "
            "failed). Nothing was staged and the cursor did not move — "
            f"'filter run {run['filter']} {run['file']}' with no flag "
            "prints the payload for the conversation instead.")
    result = fg.validate_reply(raw, units, window, run["class"])
    return {"reply": result, "usage_line": client.stats_line(),
            "model": client.model}


def filter_prelude(db: Database, manuscript: dict, config: dict, name: str,
                   file: str, replace: bool = False,
                   native: bool = False, reply: str | None = None) -> dict:
    """A prelude: a whole-essay pass that runs before any unit is judged
    and produces a RUN-SCOPED ARTIFACT (§15.22 §3.1).

    For a GLOBAL filter the artifact is the frozen registry every unit is
    judged against — immutable for the life of the run; `--replace`
    rewrites it and invalidates the run's cached prefix, which this says
    out loud.

    For a SEQUENTIAL filter that declares `prelude = "pronunciations"` in
    its front matter the artifact is a set of PROPOSALS on the ordinary
    queue: nothing enters block A, nothing is frozen into the run beyond
    a marker that it happened, and the essay is not touched."""
    from . import filters as flt
    from . import filtering as fg
    from . import llm as llm_mod

    mid = manuscript["id"]
    rel, text, _capture, dictionary = _filter_capture(db, manuscript, file,
                                                      "filter prelude")
    meta, body = flt.load_filter(manuscript, name)
    kind = ("registry" if meta["class"] == "global"
            else meta.get("prelude") or "")
    if not kind:
        raise ValueError(
            f"'{name}' is a {meta['class']} filter and declares no "
            f"prelude. A global filter always has one (its frozen "
            f"registry); a sequential filter has one only when its front "
            f"matter says so — add `prelude = "
            f"\"{fg.PRONUNCIATION_PRELUDE}\"` to _filters/{name}.md if "
            f"this filter should propose dictionary rows.")
    units = passes.paragraphs_of(text)
    run = _active_filter_run(db, mid, name, rel)
    warnings: list[str] = []
    if run is None:
        collision = _other_active_filters(db, mid, rel, name)
        if collision:
            warnings.append(collision)
        row = ko_fields("fr")
        row.update(manuscript_id=mid, filter=name, file=rel,
                   source_version_id=_latest_version_id(db, mid),
                   unit_count=len(units), cursor=0, state=None,
                   registry=None, result_version_id=None, status="active")
        row["class"] = meta["class"]
        db.insert("filter_runs", row)
        run = _touched_filter_run(db, row)
    else:
        run = _touched_filter_run(db, run)
    if kind == fg.PRONUNCIATION_PRELUDE:
        return _pronunciation_prelude(db, manuscript, config, name, rel,
                                      text, dictionary, body, units, run,
                                      warnings, replace, native, reply,
                                      meta.get("summaries", False),
                                      meta.get("profiles") or [])
    if (run["registry"] or "").strip() and not replace:
        raise ValueError(
            f"this run already has a frozen registry ({len(run['registry'])} "
            f"characters). It is immutable for the life of the run so that "
            f"every unit is judged against the same bytes. To rewrite it — "
            f"which invalidates this run's cached prefix and re-bills it on "
            f"the native path — pass --replace.")
    payload = fg.assemble_prelude(db, manuscript, run, body, units,
                                  text=text, dictionary=dictionary,
                                  summaries=meta.get("summaries", False),
                                  profiles=meta.get("profiles") or [])
    info = {"run": run, "filter": name, "file": rel, "payload": payload,
            "payload_hashes": payload.hashes, "payload_sizes": payload.sizes,
            "prompt_location": fg.prompt_location(), "unit_count": len(units),
            "registry": None, "native": False, "warnings": warnings}
    if reply is not None:
        registry = fg.parse_prelude(fg.reply_json(reply))
    elif native:
        client = llm_mod.filtering_llm(config)
        raw = client.complete_json_blocks(payload.system_blocks,
                                          payload.user_blocks)
        if raw is None:
            raise RuntimeError("the filter model returned nothing; the "
                               "registry was not written.")
        registry = fg.parse_prelude(raw)
        info["usage_line"] = client.stats_line()
    else:
        return info
    db.update("filter_runs", run["id"], {"registry": registry})
    run["registry"] = registry
    return {**info, "registry": registry, "run": run,
            "replaced": bool(replace)}


def _pronunciation_prelude(db: Database, manuscript: dict, config: dict,
                           name: str, rel: str, text: str, dictionary: str,
                           body: str, units: list[str], run: dict,
                           warnings: list[str], replace: bool,
                           native: bool, reply: str | None,
                           summaries: bool = False,
                           profiles: list[str] | None = None) -> dict:
    """The sequential prelude's artifact is a PROPOSAL SET (§15.22 §3.5).

    Nothing enters block A, nothing is frozen into the run beyond
    `metadata.prelude`, and the essay is not touched. The rows themselves
    are written by `proposals.adopt` and by nothing else — this verb only
    ASKS."""
    from . import filtering as fg
    from . import llm as llm_mod
    from . import pronunciations as pron

    mid = manuscript["id"]
    run_meta = loads(run["metadata"], {}) or {}
    if run_meta.get("prelude") and not replace:
        # The registry's refusal, word for word in shape: a second
        # prelude on the same run is refused rather than silently
        # re-asking, and `--replace` is the way through.
        prior = run_meta["prelude"]
        raise ValueError(
            f"this run already ran its pronunciation prelude "
            f"({prior.get('proposed', 0)} proposed, "
            f"{prior.get('suppressed', 0)} suppressed). To run it again — "
            f"which re-proposes only terms still absent from "
            f"{pron.FILENAME} — pass --replace.")
    payload = fg.assemble_prelude(db, manuscript, run, body, units,
                                  text=text, dictionary=dictionary,
                                  kind=fg.PRONUNCIATION_PRELUDE,
                                  summaries=summaries, profiles=profiles)
    info = {"run": run, "filter": name, "file": rel, "payload": payload,
            "payload_hashes": payload.hashes, "payload_sizes": payload.sizes,
            "prompt_location": fg.prompt_location(), "unit_count": len(units),
            "kind": fg.PRONUNCIATION_PRELUDE, "registry": None,
            "native": False, "warnings": warnings}
    if reply is not None:
        entries = fg.parse_pronunciations(fg.reply_json(reply), text,
                                          dictionary)
    elif native:
        client = llm_mod.filtering_llm(config)
        raw = client.complete_json_blocks(payload.system_blocks,
                                          payload.user_blocks)
        if raw is None:
            raise RuntimeError("the filter model returned nothing; no "
                               "pronunciation was proposed.")
        entries = fg.parse_pronunciations(raw, text, dictionary)
        info["usage_line"] = client.stats_line()
    else:
        return info

    proposed, suppressed = [], []
    for entry in entries:
        row = prop.create(
            db, mid, kind="pronunciation", target=pron.key(entry["term"]),
            payload={"name": entry["term"], "say": entry["say"],
                     "note": entry["note"], "file": rel, "filter": name},
            source="filter-prelude")
        (proposed if row else suppressed).append(entry["term"])
    run_meta["prelude"] = {"kind": fg.PRONUNCIATION_PRELUDE,
                           "at": now_iso(), "proposed": len(proposed),
                           "suppressed": len(suppressed)}
    db.update("filter_runs", run["id"], {"metadata": json.dumps(run_meta)})
    run["metadata"] = json.dumps(run_meta)
    return {**info, "native": bool(native), "run": run,
            "proposed": proposed, "suppressed": suppressed,
            "replaced": bool(replace)}


def filter_record(db: Database, manuscript: dict, config: dict,
                  file: str, reply: str, name: str | None = None) -> dict:
    """Register one window's reply. The whole reply is refused or none of
    it is: a partially admitted reply is a run whose conditioning nobody
    can reconstruct.

    The pin is re-checked HERE, not only at `filter run`: assembly and
    registration are two conversational turns, and a reply drafted
    against unit 12 cannot be staged against a unit 12 that moved."""
    from . import filtering as fg

    mid = manuscript["id"]
    rel, text, _capture, dictionary = _filter_capture(db, manuscript, file,
                                                      "filter record")
    run = _filter_run_row(db, manuscript, rel, name)
    pinned = _version_text(db, run["source_version_id"], rel)
    if pinned is not None and pinned != text:
        raise ValueError(
            f"{rel} has changed since this run pinned it, so a reply "
            f"drafted against its units cannot be staged against the text "
            f"that is there now. Nothing was staged and the cursor did not "
            f"move. Start a fresh run: 'filter run {run['filter']} {rel} "
            f"--again'.")
    units = passes.paragraphs_of(text)
    window = loads(run["metadata"], {}).get("window")
    if not window:
        raise ValueError(
            f"this run has no open window — 'filter run {run['filter']} "
            f"{rel}' assembles one.")
    result = fg.validate_reply(fg.reply_json(reply), units,
                               (window[0], window[1]), run["class"])
    staged = staging.stage_edits(db, mid, run["id"], rel, text,
                                 result["edits"],
                                 origin_type=FILTER_ORIGIN,
                                 window=(window[0], window[1]))
    changes = {"cursor": max(run["cursor"], window[1])}
    if result["state"] is not None:
        changes["state"] = result["state"]
    db.update("filter_runs", run["id"], changes)
    run.update(changes)
    covered = window[1] - window[0] + 1
    share = (len(result["edits"]) / covered) if covered else 0
    warnings = []
    # The one deterministic check the harness CAN make (§15.22 §1.9).
    # It WARNS and never blocks, naming every lost term and its unit: a
    # legitimate recast can drop one of two mentions, and a refusal would
    # discard the WHOLE reply for a judgment the harness is not entitled
    # to make. Supplying the list is the pass; being the editor is not.
    protected = fg.protected_terms(db, manuscript, rel, text,
                                   dictionary)["all"]
    for edit in result["edits"]:
        lost = fg.protected_loss(units[edit["n"] - 1], edit["new"], protected)
        if lost:
            warnings.append(
                f"unit {edit['n']}: the replacement drops "
                f"{', '.join(repr(t) for t in lost)} — "
                f"{'these are' if len(lost) > 1 else 'that is'} on "
                f"PROTECTED TERMS, the author's own vocabulary. Staged "
                f"anyway; the harness supplies the law, it is not the "
                f"editor. Reject it at triage if the term should stand.")
    if covered >= 8 and share >= PROMPT_FAULT_SHARE:
        warnings.append(
            f"{len(result['edits'])} proposals on {covered} units. A "
            f"filter that fires on nearly every unit is almost always a "
            f"PROMPT fault rather than an essay that bad — read it with "
            f"'filter show {run['filter']}' and consider narrowing it. "
            f"Nothing is blocked.")
    return {"run": run, "file": rel, "window": (window[0], window[1]),
            "staged": staged, "keeps": result["keeps"],
            "state": result["state"], "warnings": warnings,
            "remaining": run["unit_count"] - run["cursor"]}


def _open_run_threads(db: Database, manuscript_id: str, rel: str,
                     states=("proposed", "accepted", "rejected")
                     ) -> list[dict]:
    """The staged edits of this file's ACTIVE runs, in staging order.

    Scoped to the active runs and NOT to the file, deliberately. A
    settled run's rejected proposals are history — they belong to block
    A's PRIOR RUNS section, where their reasons are law for the next
    run — and listing them beside a live run's would let `filter triage
    --accept 1` re-accept something the author refused a week ago,
    silently resurrecting it into this run's settle.

    With no active run the open threads on the file are still returned,
    so a proposal orphaned by an abandoned run is visible rather than
    invisible."""
    runs = [dict(r) for r in db.all(
        "SELECT * FROM filter_runs WHERE manuscript_id = ? AND file = ? "
        "AND status = 'active' ORDER BY created_at", (manuscript_id, rel))]
    rows = staging.door_threads(db, manuscript_id, rel, states=states,
                                origin_type=FILTER_ORIGIN)
    if not runs:
        return [r for r in rows if r["state"] in ("proposed", "accepted",
                                                  "written")]
    prefixes = tuple(f"{r['id']}:{rel}:" for r in runs)
    return [r for r in rows if r["origin_id"].startswith(prefixes)]


def filter_edits(db: Database, manuscript: dict, file: str) -> dict:
    """The staged filter proposals of this file's active run(s),
    numbered for triage — plus the run's TRANSPORT and how many of its
    forms are currently out.

    The transport is reported rather than acted on, and that is the
    whole of what doc mode owes chat (§5's MCP ruling): a session that
    cannot see `mode='doc'` will offer to apply what is already sitting
    in the author's Doc, and will try to triage a `written` row that
    `passes.verdict` refuses anyway."""
    rel = _resolve_relpath(manuscript, file)
    mid = manuscript["id"]
    rows = _open_run_threads(db, mid, rel)
    items = []
    for n, t in enumerate(rows, 1):
        meta = loads(t.get("metadata"), {}) or {}
        items.append({"n": n, "id": t["id"], "state": t["state"],
                      "unit": meta.get("anchor_paragraph"),
                      "old": t["proposed_old"], "new": t["proposed_new"],
                      "why": t["note"], "ref": meta.get("ref")})
    active = [dict(r) for r in db.all(
        "SELECT * FROM filter_runs WHERE manuscript_id = ? AND file = ? "
        "AND status = 'active' ORDER BY created_at", (mid, rel))]
    # PER RUN, not one collapsed value. Two filters on one essay is
    # legitimate work, and when their transports differ the collapsed
    # answer was None — which chat reads as "no transport chosen" — and
    # it suppressed the note in exactly the case that most needs it: one
    # run's forms sitting in the author's Doc while another's proposals
    # are still on the table here.
    runs = [{"filter": r["filter"], "mode": _run_mode(r),
             "forms_out": sum(1 for t in _run_threads(db, mid, r)
                              if t["state"] == "written")}
            for r in active]
    modes = {r["mode"] for r in runs}
    mode = next(iter(modes)) if len(modes) == 1 else None
    forms_out = sum(1 for t in staging.door_threads(
        db, mid, rel, states=("written",), origin_type=FILTER_ORIGIN))
    out_in_doc = [r for r in runs if r["mode"] == "doc" and r["forms_out"]]
    note = None
    if out_in_doc:
        which = ", ".join(f"{r['filter']} ({r['forms_out']})"
                          for r in out_in_doc)
        note = (f"{sum(r['forms_out'] for r in out_in_doc)} form(s) are "
                f"out in the Doc's tab for this essay — {which}. The "
                f"author resolves those THERE, and then 'authorlm filter "
                f"resolve {rel}' (CLI) reads the tab back. Do not offer "
                f"to apply them from here.")
        if len(runs) > len(out_in_doc):
            note += (" The other run(s) on this essay are on the local "
                     "road and their proposals above are still yours to "
                     "triage as usual.")
    return {"file": rel, "count": len(items), "items": items,
            "mode": mode, "runs": runs, "forms_out": forms_out,
            "transport_note": note}


def filter_triage(db: Database, manuscript: dict, file: str,
                  operations: list[dict]) -> dict:
    """The author's verdicts, in batch. One failed op never blocks the
    rest; numbers resolve against ONE snapshot of `filter_edits` order.

    A rejection's reason is stored on the thread VERBATIM as well as
    reaching evidence, because block A's PRIOR RUNS section renders it
    into the next run's CACHED layer — which is the whole of M2, the
    mechanism that stops a filter re-proposing what the author already
    refused."""
    mid = manuscript["id"]
    rel = _resolve_relpath(manuscript, file)
    snapshot = _open_run_threads(db, mid, rel)
    results = []
    for op in operations:
        token = str(op.get("item", "")).strip()
        decision = op.get("verdict")
        entry = {"item": token, "verdict": decision}
        try:
            if token.isdigit():
                n = int(token)
                if not 1 <= n <= len(snapshot):
                    raise LookupError(f"no edit {n} (there are "
                                      f"{len(snapshot)})")
                thread = snapshot[n - 1]
            else:
                hits = [t for t in snapshot if token in t["id"]]
                if len(hits) != 1:
                    raise LookupError(f"'{token}' matches {len(hits)} edits")
                thread = hits[0]
            if decision == "reject" and not op.get("reason"):
                raise ValueError(
                    "reject requires the author's verbatim reason — it is "
                    "the highest-value evidence this run produces, and the "
                    "next run of this filter reads it before it starts")
            text = (op.get("reason") if decision == "reject"
                    else op.get("text"))
            out = passes.verdict(db, mid, thread, decision, text,
                                 evidence_type=FILTER_EVIDENCE)
            if decision == "reject":
                meta = loads(thread.get("metadata"), {}) or {}
                meta["author_reason"] = op["reason"]
                db.update("doc_threads", thread["id"],
                          {"metadata": json.dumps(meta)})
            entry.update(ok=True, id=thread["id"], **out)
        except (LookupError, ValueError, KeyError) as err:
            entry.update(ok=False, error=str(err))
        results.append(entry)
    left = [t for t in _open_run_threads(db, mid, rel)
            if t["state"] == "proposed"]
    return {"file": rel, "results": results, "still_proposed": len(left)}


def _falsified_prefix(db: Database, manuscript_id: str, run: dict
                      ) -> list[dict]:
    """§1.3's derived warning. When the author rejects unit 3, every
    later unit this run proposed was drafted against a prefix that did
    not survive. Derived, never stored: one pass over the run's threads.

    Deliberately NOT automatic — re-running the tail discards the
    author's verdicts on those units, and discarding verdicts is never
    something a verb does on its own."""
    if run["class"] != "sequential":
        return []
    threads = _run_threads(db, manuscript_id, run)
    by_unit = []
    for t in threads:
        meta = loads(t.get("metadata"), {}) or {}
        by_unit.append((meta.get("anchor_paragraph") or 0, t))
    out = []
    for n, t in sorted(by_unit):
        if t["state"] not in ("rejected", "declined"):
            continue
        later = sorted(m for m, o in by_unit
                       if m > n and o["state"] not in ("withdrawn",
                                                        "rejected"))
        if later:
            out.append({"n": n, "downstream": later})
    return out


def _uncovered_units(run: dict) -> list[tuple[int, int]]:
    """Units this run never processed. An ordinary open editorial state:
    it WARNS and never blocks (§15.13), exactly as an unwritten beat and
    an unaccounted digest point do. Completion is the author's call."""
    if run["cursor"] >= run["unit_count"]:
        return []
    return [(run["cursor"] + 1, run["unit_count"])]


def _pushed_from(thread: dict) -> str:
    """The state a written thread came from, for the recovery verbs.

    `accepted` when the thread carries no record — the local road never
    writes anything else, and neither did the Doc road before untriaged
    proposals were allowed onto it. Recovering an untriaged proposal to
    `accepted` would fabricate a verdict the author never gave, which is
    exactly what `filter unmark` exists NOT to do: it undoes the
    marking, never the triage."""
    return (loads(thread.get("metadata"), {}) or {}).get(
        "pushed_from") or "accepted"


def _owning_filter(db: Database, manuscript_id: str,
                   thread: dict) -> str | None:
    """The filter whose run staged this thread, by its `origin_id`
    prefix (`{run_id}:{file}:{ordinal}`) — so a refusal can name the
    filter the author knows rather than a row id they have never seen."""
    run_id = (thread.get("origin_id") or "").split(":", 1)[0]
    row = db.one("SELECT filter FROM filter_runs WHERE id = ? AND "
                 "manuscript_id = ?", (run_id, manuscript_id))
    return row["filter"] if row else None


def _twin_warning(accepted: list[dict]) -> str | None:
    """§9.2's one informational line, when two accepted edits replace
    byte-identical paragraphs.

    Warn, never block, and never hold anything back: the Sponsor's
    ruling is push ALL, twins included, because holding twins back on the
    local road while the rest went to the Doc would split one run across
    two transports — the exact state the mode freeze exists to forbid.

    What the author needs to know is small and specific: the forms
    settle BY POSITION, so rewording either is safe, and only DELETING
    one outright can attach the decline to the twin they kept. The
    manuscript text is correct either way; it is the record of which
    they refused that goes astray (RISK-6)."""
    from . import gdocs

    groups: dict[str, list[int]] = {}
    for t in accepted:
        unit = (loads(t.get("metadata"), {}) or {}).get("anchor_paragraph")
        groups.setdefault(t["proposed_old"], []).append(unit or 0)
    twins = {old: sorted(units) for old, units in groups.items()
             if len(units) > 1 and old}
    if not twins:
        return None
    lines = []
    for old, units in twins.items():
        where = " and ".join(str(u) for u in units)
        lines.append(
            f"{len(units)} of these changes replace the same paragraph "
            f"text, word for word: «{gdocs.clamp(old)}» (units {where}). "
            f"They settle by position — the first form in the Doc is the "
            f"first change — so reword either freely. The one thing to "
            f"avoid is DELETING one of them outright: your manuscript "
            f"text is never affected either way, but the record of which "
            f"of the two you turned down may attach to the other. If you "
            f"want one gone, empty its green half rather than deleting "
            f"the whole marked span.")
    return "\n".join(lines)


def filter_push(db: Database, manuscript: dict, config: dict, file: str,
                services=None, name: str | None = None) -> dict:
    """The DOC transport verb (design-filter-doc-settle §2.3): write this
    run's staged edits into the essay's tab of the master Doc as
    `<<old>>{{new}}` forms, through the same surgical writer `critique
    write` uses, and leave the local file holding the OLD text.

    **UNTRIAGED PROPOSALS GO TOO** (Sponsor-intent correction,
    2026-08-30). The first cut gated this on `state='accepted'`, keeping
    CLI triage as the verdict step and the Doc as a final-settle
    surface — so a freshly recorded run met *"nothing is accepted — 6
    proposal(s) are still awaiting your verdict"* and the author had to
    rule on every edit in the shell before they could look at any of
    them in the Doc. That is reviewing twice, and it is the opposite of
    the ruling this road was built to serve: *"We need to build in a way
    to review edits more easily… That path also has the learning loop
    built in."*

    The Doc settle IS the review, and it always was: an untouched form
    is an acceptance, a deleted one is a decline, a reworded one is a
    modified acceptance, and `record_resolution` records all three as
    evidence with no reference to what the thread's state was before it
    was written. So `proposed` and `accepted` both go out. Only an
    explicit `rejected` stays home — that verdict has already been
    given, and its reason is already evidence.

    Named `push` and not `write`: `filter write` reads as *write a filter
    artifact* and collides with `filter add`, and every refusal the
    author meets on this road already speaks the word "push". The cost,
    recorded: it is one word from `doc push`, which does something else.

    Nine steps, each of them a refusal point; the ordering is the whole
    of the safety argument, so it is spelled out rather than inferred."""
    from . import gdocs

    mid = manuscript["id"]
    # 1-3. The run's OWN state first, and ordered most-informative-first
    #      for the same reason `push_doc`'s two guards are: a second push
    #      would trip the checkout gate below, and that refusal would
    #      name `doc pull` for a checkout THIS RUN's own levelling push
    #      created. When the database knows the run has forms out, that
    #      is the refusal the author needs.
    rel = _resolve_relpath(manuscript, file)
    run = _filter_run_row(db, manuscript, rel, name)
    mode = _run_mode(run)
    if mode == "local":
        raise ValueError(
            f"this run already took the local road — its forms were "
            f"composed into {rel} on disk, and a run whose forms are half "
            f"in the Doc and half on disk is a run nobody can reason "
            f"about. Finish it ('filter resolve {rel}') and start a fresh "
            f"run if you want to read the next batch in the Doc.")
    threads = _run_threads(db, mid, run)
    already = [t for t in threads if t["state"] == "written"]
    if already:
        unmark = (f"filter unmark {rel} --force" if mode == "doc"
                  else f"filter unmark {rel}")
        raise ValueError(
            f"{len(already)} form(s) of this run are already out — "
            f"pushing again would mark a tab that still carries them. "
            f"Finalize ('filter resolve {rel}') or take them back out "
            f"('{unmark}'), then push again if you still want to.")
    # 3b. Q-1, ruled: refuse when SOMEONE ELSE's forms are already in
    #     this tab — a second filter's run, or the critique pass's.
    #     The composed drift check further down runs against LOCAL,
    #     which knows nothing about the tab, so a second push would mark
    #     a tab that already carries another producer's forms. It fails
    #     anyway — `push_doc`'s `forms_pending` refuses the levelling
    #     push from inside `write_pending_forms` — so this refusal exists
    #     to be EARLY and BY NAME rather than three functions deep.
    #
    #     ABOVE the capture, with the other DB-known refusals, for the
    #     reason given there: the first run's own levelling push checked
    #     this file out, so the checkout gate would otherwise answer
    #     first and send the author to `doc pull`, which does not help.
    mine = {t["id"] for t in threads}
    outside = [dict(r) for r in db.all(
        "SELECT * FROM doc_threads WHERE manuscript_id = ? AND file = ? "
        "AND state = 'written' ORDER BY created_at", (mid, rel))
        if r["id"] not in mine]
    if outside:
        origin = outside[0]["origin_type"]
        owner = (_owning_filter(db, mid, outside[0])
                 if origin == FILTER_ORIGIN else None)
        whose = f"'{owner}'" if owner else f"the {origin} pass"
        remedy = {FILTER_ORIGIN: f"filter resolve {rel}",
                  LENS_ORIGIN: f"lens resolve {rel}"}.get(
                      origin, f"critique resolve {rel}")
        raise ValueError(
            f"{len(outside)} form(s) from {whose} are already in {rel}'s "
            f"tab. Two producers' forms in one tab cannot be told apart "
            f"at resolve — the join is the old text, and neither settle "
            f"would know which forms were its own. Finish that one "
            f"('{remedy}') and then push this run.")
    # 4a. The one capture: the checkout gate, the three in-flight and
    #     placeholder refusals, the sidecar refusal, the file's text. The
    #     gate STAYS on this verb: staging read `old` from local, so
    #     pushing forms onto a tab whose text the author has since edited
    #     in the Doc is the drift case, and the gate names the remedy.
    #     (`critique write` has no such gate and opens with a `push_doc`
    #     that would overwrite a Doc-side edit — a live hazard in that
    #     pass, RISK-4. This design does not inherit it.)
    _rel, _text, _capture, _dict = _filter_capture(db, manuscript, rel,
                                                   "filter push")
    # 4b. Something to push. UNTRIAGED PROPOSALS GO TOO — see the
    #     function docstring: on this road the tab is the review surface,
    #     so gating on a prior CLI verdict would make the author review
    #     twice. Only an explicit rejection stays home.
    untriaged = [t for t in threads if t["state"] == "proposed"]
    pre_accepted = [t for t in threads if t["state"] == "accepted"]
    rejected = [t for t in threads if t["state"] == "rejected"]
    pushable = [t for t in threads
                if t["state"] in ("proposed", "accepted")]
    if not pushable:
        raise LookupError(
            f"nothing on {rel} can go to the Doc"
            + (f" — all {len(rejected)} of this run's proposals are ones "
               f"you turned down." if rejected
               else " and nothing is staged."))
    warnings: list[str] = []
    # Q-3, re-read against the Sponsor's intent. The old line said the
    # untriaged proposals were NOT going to the Doc, which was the
    # defect. What the author needs now is the opposite: they are all
    # going, and what to DO in the tab is the verdict.
    if untriaged and pre_accepted:
        warnings.append(
            f"All {len(pushable)} changes go to the Doc — the "
            f"{len(pre_accepted)} you have already accepted and the "
            f"{len(untriaged)} you have not ruled on yet. The tab is the "
            f"review: leave a change alone to take it, empty its green "
            f"half to turn it down, reword it to make it yours. Every "
            f"verdict is recorded when you run 'filter resolve {rel}'.")
    if rejected:
        warnings.append(
            f"{len(rejected)} change(s) you already turned down stay "
            f"home and are not in the Doc.")
    twin_note = _twin_warning(pushable)
    if twin_note:
        warnings.append(twin_note)
    # 5. The drift check, LOCALLY, before any Doc write: a `proposed_old`
    #    that no longer matches its paragraph raises naming the unit. The
    #    composed text is DISCARDED — nothing is written to disk, ever,
    #    on this road (F-D2).
    path = Path(manuscript["path"]) / rel
    disk = path.read_text(encoding="utf-8")
    passes.compose_marked_text(disk, pushable)
    # 6. The surgical writer: a levelling push of the pristine local
    #    file, then one marked span per thread, highest anchor first,
    #    located by occurrence index, then the read-back proof.
    #
    #    The bridge is built HERE and not at the top, deliberately: every
    #    refusal above is a state refusal that costs nothing, and a verb
    #    that pops an OAuth consent window only to then tell the author
    #    their run is on the other road has spent their attention for
    #    nothing. It also keeps `tools/testbench.py` able to assert these
    #    refusals with no key, no token and no network.
    service, docs_service = _resolve_services(services, rel)
    result = gdocs.write_pending_forms(db, manuscript, rel, pushable,
                                       service, docs_service)
    # 7. Written threads advance to `written`, remembering the state they
    #    came FROM. Failed threads are simply not touched, so each keeps
    #    its own prior state — a proposal that failed to land is still a
    #    proposal, not an acceptance the author never gave.
    #
    #    `pushed_from` exists for `filter unmark`, which returns written
    #    threads to `accepted`. That was right while only accepted
    #    threads could be written; now it would invent a verdict for
    #    every untriaged proposal it took back out.
    for t in result["written"]:
        meta = loads(t.get("metadata"), {}) or {}
        meta["pushed_from"] = t["state"]
        db.update("doc_threads", t["id"],
                  {"state": "written", "metadata": json.dumps(meta)})
    # 8. The mode is recorded only if at least one form landed. A push
    #    that landed nothing has put nothing out, and stranding the run
    #    in a mode with no forms in it would refuse the local road for no
    #    reason.
    if result["written"]:
        run = _freeze_run_mode(db, run, "doc")
    return {"file": rel, "run": run, "url": result["url"],
            "written": len(result["written"]),
            "failed": [(t, why) for t, why in result["failed"]],
            "mode": _run_mode(run), "warnings": warnings,
            "local_unchanged": path.read_text(encoding="utf-8") == disk}


def _resolve_services(services, rel: str):
    """The Drive/Docs pair, built only at the moment a verb actually
    needs it.

    A callable, not two arguments, and called LAST rather than first.
    The local settle must work with no credentials at all; and every
    state refusal on the doc road — the wrong mode, forms already out, a
    checked-out file, nothing accepted, a drifted paragraph — costs
    nothing and must be reachable without a token, so that a verb never
    pops an OAuth consent window only to then refuse, and so that
    `tools/testbench.py` can assert those refusals with no key and no
    network."""
    if services is None:
        raise ValueError(
            f"{rel} needs the Google Doc bridge for this step, and none "
            f"was supplied.")
    try:
        return services()
    except ValueError as err:
        raise ValueError(
            f"{err}\nThe Doc road for {rel} needs the [gdocs] bridge. "
            f"Authorize with 'doc auth'; or, if forms are already out in "
            f"the tab, take them back with 'filter unmark {rel} --force' "
            f"— which loses any rewording you did there.") from err


def filter_resolve(db: Database, manuscript: dict, config: dict, file: str,
                  pause: bool = False, name: str | None = None,
                  services=None) -> dict:
    """One verb, three halves: the threads' state chooses whether this is
    an apply or a finalize, and the RUN'S TRANSPORT chooses where the
    marked text is read from (D-2).

        filter resolve <file>            threads accepted → APPLY directly
        filter resolve <file> --pause    threads accepted → compose, stop
        filter resolve <file>            threads written  → read, finalize
                                        mode 'local' → the marked file
                                        mode 'doc'   → the essay's tab

    Direct apply is the DEFAULT: a filter's edits are mechanical and
    numerous, the triage verdict already IS the author's ruling, and
    `revise` already carries their wording when they want to change one.
    The pause is for the filter whose edits the author wants to read in
    place. Same door, same evidence, same rollback either way — and
    literally the same code: the direct apply composes the marked text
    and resolves it in one breath, without ever writing the markers to
    disk, so there is one composition routine and not two.

    The dispatch is on the run's MODE and not on `origin_type` (D-2):
    both filter roads carry `origin_type='filter'`, so `origin_type` is
    the wrong key, and a sibling verb would be larger than a branch in
    the verb that already dispatches on thread state.
    """
    from . import gdocs
    from . import summaries as sums

    mid = manuscript["id"]
    # The run's own state before the capture, for `filter push`'s reason:
    # in doc mode the file is checked out BY DESIGN and the gate must be
    # told not to fire (§2.4).
    rel = _resolve_relpath(manuscript, file)
    run = _filter_run_row(db, manuscript, rel, name)
    mode = _run_mode(run)
    _rel, _text, _capture, _dict = _filter_capture(
        db, manuscript, rel, "filter resolve", checkout=(mode != "doc"))
    path = Path(manuscript["path"]) / rel
    written = [t for t in _run_threads(db, mid, run)
               if t["state"] == "written"]
    warnings: list[str] = []
    if mode == "doc" and pause:
        raise ValueError(
            f"this run put its forms in the Doc — 'filter push {rel}' "
            f"already chose that road, and --pause would mark the local "
            f"file as well, leaving one run's forms in two places. "
            f"Finalize with 'filter resolve {rel}' (no flag), which reads "
            f"the tab back, or take the forms out of the Doc with "
            f"'filter unmark {rel} --force'.")

    if written:
        if pause:
            raise ValueError(
                f"{rel} is already marked — {len(written)} form(s) are "
                f"written into the file and the author's post-edits are "
                f"in them. Finalize with 'filter resolve {rel}' (no flag), "
                f"or put the original text back with 'filter unmark "
                f"{rel}'.")
        if mode == "doc":
            # The DOC road. The authoritative marked text is the TAB's —
            # `pull_doc` runs `strip_pending` on incoming text before
            # writing it, so a pull of a tab carrying forms writes the
            # pre-edit text and discards every {{new}} half INCLUDING the
            # author's rewordings. A settle that read the pulled local
            # file would find no forms, resolve nothing, and decline
            # every thread (D-1). `doc pull` is not part of this flow and
            # is harmless if it happens (§2.6).
            service, docs_service = _resolve_services(services, rel)
            fetched = gdocs.tab_marked_markdown(db, manuscript, rel,
                                                service, docs_service)
            if fetched["state"] == "missing":
                raise LookupError(
                    f"'{rel}' has no matching section in the master Doc "
                    f"export — the tab this run's forms were written to "
                    f"is gone. 'doc push {rel}' rebuilds it from the "
                    f"local file, which still holds the old text.")
            if fetched["state"] == "conflict":
                raise ValueError(
                    f"'{rel}' changed both locally and in the Doc since "
                    f"the last sync — the resolve refuses to guess which "
                    f"wins. Compare the local file against the Doc tab by "
                    f"hand, then re-run 'filter resolve {rel}'.")
            warnings.extend(fetched["marker_warnings"])
            return _filter_finalize(db, manuscript, config, run, rel, path,
                                    warnings, marked_doc=fetched["marked"])
        if mode is None:
            # A run paused before the transport existed: its forms are in
            # the bytes on disk, so the road it took was the local one.
            run = _freeze_run_mode(db, run, "local")
        return _filter_finalize(db, manuscript, config, run, rel, path,
                                warnings)

    accepted = [t for t in _run_threads(db, mid, run)
                if t["state"] == "accepted"]
    if not accepted:
        open_now = [t for t in _run_threads(db, mid, run)
                    if t["state"] == "proposed"]
        raise LookupError(
            f"nothing is accepted on {rel}"
            + (f" — {len(open_now)} proposal(s) are still awaiting your "
               f"verdict ('filter edits {rel}')." if open_now
               else " and nothing is staged."))
    # The transport is chosen HERE, by taking it: this is the local
    # road, and from now on the run says so (§2.1).
    if mode is None:
        run = _freeze_run_mode(db, run, "local")
    # The file's OWN bytes, read directly: the resolve code owns the
    # pending-change grammar, and it is the only code in the system that
    # is allowed to see markers.
    disk = path.read_text(encoding="utf-8")
    marked = staging.mark_local(path, disk, accepted) if pause else \
        passes.compose_marked_text(disk, accepted)
    for t in accepted:
        db.update("doc_threads", t["id"], {"state": "written"})
    if pause:
        # The read-back assertion: the forms must be present VERBATIM in
        # the bytes we just wrote, or the mark is undone and the resolve
        # refuses. Nothing is left half-marked.
        back = path.read_text(encoding="utf-8")
        if back != marked:
            staging.unmark(path)
            for t in accepted:
                db.update("doc_threads", t["id"], {"state": "accepted"})
            raise RuntimeError(
                f"{rel} did not read back as it was written — the mark was "
                "undone and nothing was changed.")
        return {"run": run, "file": rel, "paused": True,
                "forms": len(accepted), "warnings": warnings,
                "path": str(path)}
    # Direct apply: resolve the composed text without it ever touching
    # the disk. `_filter_finalize` reads the file, so hand it the marked
    # text through the same door by writing it first and finalizing at
    # once — the file is marked for the duration of one function call
    # and every guard in the system already covers that state.
    path.write_text(marked, encoding="utf-8")
    return _filter_finalize(db, manuscript, config, run, rel, path,
                            warnings, direct=True)


def _write_resolved_text(path: Path, root: Path, final: str) -> str:
    """Write a resolve's final text back to disk WITH its illustration
    embed lines. The Doc never carries embed lines (push strips them),
    so the text a filter or lens resolve reads back from a tab is
    embed-free — and writing it as-is silently unlinked every rendered
    illustration in the essay (the tag stayed, the picture under it
    vanished from Obsidian; 15 slots across SMSTTD, 2026-09-02). The
    ordinary pull re-inserts them; this is the same step, the prior
    pick winning while its file exists."""
    from . import gdocs
    from .illus import capture_embeds, reembed

    prior = path.read_text(encoding="utf-8") if path.exists() else ""
    normalized = gdocs.normalize_markdown(final)
    normalized = reembed(normalized, root, capture_embeds(prior))
    path.write_text(normalized if normalized.endswith("\n")
                    else normalized + "\n", encoding="utf-8")
    return normalized


def _filter_finalize(db: Database, manuscript: dict, config: dict, run: dict,
                     rel: str, path: Path, warnings: list[str],
                     direct: bool = False,
                     marked_doc: str | None = None) -> dict:
    """Read the marked text back, record the resolution, write the final
    text, collect under NO episode, rebuild the summary.

    ONE thing differs between the transports and it is where the marked
    text comes from: `marked_doc` is the tab's export on the doc road and
    None on the local road, where the file's own bytes are read. From
    `diffs` onward the two roads are literally the same code — same
    `final_text_from_marked`, same `record_resolution`, same
    `filter_edit` evidence with `episode_id = NULL`, same NO_EPISODE
    collect, same summary rebuild."""
    from . import gdocs
    from . import summaries as sums

    mid = manuscript["id"]
    # Snapshot whatever is on disk right now BEFORE it is overwritten —
    # including any local edit made outside this flow. Ambient by
    # declaration: that work predates the resolve and is the author's own.
    # (Under the canonicalization the marked bytes read as the essay, so
    # this is a no-op unless there is a real uncollected edit.)
    with contextlib.redirect_stdout(io.StringIO()):
        collect(db, manuscript, config, source="pre-filter-settle")
    if marked_doc is None:
        final, forms, diffs = staging.resolve_local(
            db, mid, rel, path, origin_type=FILTER_ORIGIN,
            evidence_type=FILTER_EVIDENCE)
    else:
        written = staging.door_threads(db, mid, rel, states=("written",),
                                       origin_type=FILTER_ORIGIN)
        # kinds=("replace",) on BOTH roads, for the same reason: an
        # unmatched insertion form collapses to its old half, which for
        # an insertion is the empty string — so a resolve that looked at
        # bare `{{…}}` would DELETE an author's `{{title}}` from the
        # finished essay. In the tab that `{{title}}` arrived from the
        # author's own file through `push_doc`, so the hazard is real
        # there too. A filter never stages an insertion.
        final, forms = passes.final_text_from_marked(
            marked_doc, written=written, kinds=("replace",))
        diffs = passes.record_resolution(db, mid, rel, forms,
                                         origin_type=FILTER_ORIGIN,
                                         evidence_type=FILTER_EVIDENCE,
                                         final_text=final)
    normalized = _write_resolved_text(path, Path(manuscript["path"]), final)

    # NO episode (§1.8). Hygiene work has no goal to be filed under, and
    # `episode=None` would file it against whatever the author happens to
    # have open — which is exactly §15.17's mis-attribution.
    with contextlib.redirect_stdout(io.StringIO()):
        collect(db, manuscript, config, source="filter-settle",
                episode=NO_EPISODE)
    result_version = _latest_version_id(db, mid)
    db.update("filter_runs", run["id"],
              {"status": "settled", "result_version_id": result_version})
    run = dict(run, status="settled", result_version_id=result_version)

    for span in _uncovered_units(run):
        warnings.append(
            f"units {span[0]}–{span[1]} of {run['unit_count']} were never "
            f"processed by this run. That is an ordinary open state, not "
            f"an error — nothing is blocked.")
    falsified = _falsified_prefix(db, mid, run)

    summary = {"rebuilt": False, "error": None, "usage": None}
    llm = sums.summarizer_llm(config)
    if llm.enabled:
        try:
            sums.rebuild_one(db, manuscript, rel, llm)
            summary.update(rebuilt=True, usage=llm.stats_line())
        except Exception as err:                        # noqa: BLE001
            summary["error"] = str(err)
    # The learnings duty, on BOTH transports — the half of the loop that
    # had never fired for a filter (§1.2). It costs a resolve a SECOND
    # model call where the filter design claimed one, on the general tier
    # rather than the cheap one, and only when the author reworded at
    # least two proposals. It fails soft.
    candidate = passes.settle_learnings(db, manuscript, diffs, config)
    # The honest tallies: `forms` counts only the marker-intact forms the
    # settle read back, which undercounts a hand-resolved tab
    # (it-4c5a8038c304) — the thread states carry the truth.
    states = [t["state"] for t in _run_threads(db, mid, run)]
    return {"run": run, "file": rel, "paused": False, "direct": direct,
            "accepted": states.count("cleaned"),
            "declined": states.count("declined"),
            "forms": len(forms), "diffs": diffs, "final": normalized,
            "warnings": warnings, "falsified_prefix": falsified,
            "summary": summary, "result_version_id": result_version,
            "pattern_candidate": candidate, "mode": _run_mode(run),
            # Q-2's default, matching `critique resolve` exactly: the
            # settle does NOT re-push. A settle that pushes is a resolve
            # that can fail halfway on the network after the evidence is
            # recorded — so the tab keeps showing the marks until the
            # author's next ordinary `doc push`, and the verb says so.
            #
            # Keyed on whether THIS settle actually read forms back out
            # of the tab, not on the run's mode. A doc-mode run whose
            # forms were taken back by `filter unmark --force` settles
            # locally against a tab that verb just rebuilt CLEAN; telling
            # the author it still shows struck-and-green text sends them
            # to look at marks that are not there.
            "tab_still_marked": marked_doc is not None}


def _file_run_mode(db: Database, manuscript_id: str, rel: str) -> str | None:
    """The transport of the most recent run on this file that took one.
    `filter unmark` and `filter rollback` are FILE-scoped verbs — they
    exist to recover a state the bytes or the rows describe — so they
    ask the file, not a named run."""
    for row in db.all(
            "SELECT * FROM filter_runs WHERE manuscript_id = ? AND file = ? "
            "ORDER BY created_at DESC", (manuscript_id, rel)):
        mode = _run_mode(dict(row))
        if mode:
            return mode
    return None


def lens_push(db: Database, manuscript: dict, config: dict, file: str,
              services=None) -> dict:
    """The lens door's DOC transport (filter-pass design §7.2, built
    2026-08-31): write the staged lens edits for this file into its tab
    as `<<old>>{{new}}` forms — the same surgical writer, the same
    review contract as `filter push`: the tab IS the review, untriaged
    proposals go too, only explicit rejections stay home.

    Deliberately run-less: lens edits are file-scoped door threads with
    no run row, no transport freeze, no unit coverage — the door's own
    state machine (proposed → written → cleaned/declined) is the whole
    of their lifecycle."""
    from . import gdocs

    mid = manuscript["id"]
    rel = _resolve_relpath(manuscript, file)
    threads = staging.door_threads(
        db, mid, rel, states=("proposed", "accepted", "rejected", "written"),
        origin_type=LENS_ORIGIN)
    already = [t for t in threads if t["state"] == "written"]
    if already:
        raise ValueError(
            f"{len(already)} lens form(s) are already out in {rel}'s tab — "
            f"pushing again would mark a tab that still carries them. "
            f"Finalize first ('lens resolve {rel}').")
    mine = {t["id"] for t in threads}
    outside = [dict(r) for r in db.all(
        "SELECT * FROM doc_threads WHERE manuscript_id = ? AND file = ? "
        "AND state = 'written' ORDER BY created_at", (mid, rel))
        if r["id"] not in mine]
    if outside:
        origin = outside[0]["origin_type"]
        owner = (_owning_filter(db, mid, outside[0])
                 if origin == FILTER_ORIGIN else None)
        whose = f"'{owner}'" if owner else f"the {origin} pass"
        remedy = {FILTER_ORIGIN: f"filter resolve {rel}"}.get(
            origin, f"critique resolve {rel}")
        raise ValueError(
            f"{len(outside)} form(s) from {whose} are already in {rel}'s "
            f"tab. Two producers' forms in one tab cannot be told apart "
            f"at resolve — finish that one ('{remedy}') and then push.")
    _filter_capture(db, manuscript, rel, "lens push")
    pushable = [t for t in threads if t["state"] in ("proposed", "accepted")]
    rejected = [t for t in threads if t["state"] == "rejected"]
    if not pushable:
        raise LookupError(
            f"no staged lens edits on {rel} can go to the Doc"
            + (f" — all {len(rejected)} were turned down." if rejected
               else " — a lens finding stages an edit only when it "
                    "carries a `replacement`."))
    warnings: list[str] = []
    warnings.append(
        f"All {len(pushable)} lens change(s) go to the Doc. The tab is "
        f"the review: leave a change alone to take it, empty its green "
        f"half (or restore the old text) to turn it down, reword it to "
        f"make it yours. Every verdict is recorded at "
        f"'lens resolve {rel}'.")
    if rejected:
        warnings.append(f"{len(rejected)} change(s) already turned down "
                        f"stay home.")
    twin_note = _twin_warning(pushable)
    if twin_note:
        warnings.append(twin_note)
    path = Path(manuscript["path"]) / rel
    disk = path.read_text(encoding="utf-8")
    passes.compose_marked_text(disk, pushable)
    service, docs_service = _resolve_services(services, rel)
    result = gdocs.write_pending_forms(db, manuscript, rel, pushable,
                                       service, docs_service)
    for t in result["written"]:
        meta = loads(t.get("metadata"), {}) or {}
        meta["pushed_from"] = t["state"]
        db.update("doc_threads", t["id"],
                  {"state": "written", "metadata": json.dumps(meta)})
    return {"file": rel, "url": result["url"],
            "written": len(result["written"]),
            "failed": [(t, why) for t, why in result["failed"]],
            "warnings": warnings,
            "local_unchanged": path.read_text(encoding="utf-8") == disk}


def lens_resolve(db: Database, manuscript: dict, config: dict, file: str,
                services=None) -> dict:
    """Read the tab back and finalize the lens forms — `filter resolve`'s
    doc road for the lens door: the author's post-edits win, a
    hand-resolved form's verdict is inferred from the prose
    (it-4c5a8038c304), evidence lands as `lens_edit` under NO episode,
    and the tab keeps its marks until the next ordinary `doc push`."""
    from . import gdocs
    from . import summaries as sums

    mid = manuscript["id"]
    rel = _resolve_relpath(manuscript, file)
    written = staging.door_threads(db, mid, rel, states=("written",),
                                   origin_type=LENS_ORIGIN)
    if not written:
        raise LookupError(
            f"no lens forms are out in {rel} — 'lens push {rel}' writes "
            f"the staged lens edits into its tab.")
    # checkout=False for the reason filter_resolve's doc branch gives:
    # the push's own levelling write checked the file out BY DESIGN.
    _filter_capture(db, manuscript, rel, "lens resolve", checkout=False)
    service, docs_service = _resolve_services(services, rel)
    fetched = gdocs.tab_marked_markdown(db, manuscript, rel,
                                        service, docs_service)
    if fetched["state"] == "missing":
        raise LookupError(
            f"'{rel}' has no matching section in the master Doc export — "
            f"the tab these forms were written to is gone. "
            f"'doc push {rel}' rebuilds it from the local file.")
    if fetched["state"] == "conflict":
        raise ValueError(
            f"'{rel}' changed both locally and in the Doc since the last "
            f"sync — the resolve refuses to guess which wins. Compare "
            f"them by hand, then re-run 'lens resolve {rel}'.")
    warnings = list(fetched["marker_warnings"])
    path = Path(manuscript["path"]) / rel
    with contextlib.redirect_stdout(io.StringIO()):
        collect(db, manuscript, config, source="pre-lens-settle")
    final, forms = passes.final_text_from_marked(
        fetched["marked"], written=written, kinds=("replace",))
    diffs = passes.record_resolution(db, mid, rel, forms,
                                     origin_type=LENS_ORIGIN,
                                     evidence_type=LENS_EVIDENCE,
                                     final_text=final)
    normalized = _write_resolved_text(path, Path(manuscript["path"]), final)
    with contextlib.redirect_stdout(io.StringIO()):
        collect(db, manuscript, config, source="lens-settle",
                episode=NO_EPISODE)
    after = {t["id"]: dict(db.one(
        "SELECT * FROM doc_threads WHERE id = ?", (t["id"],)))
        for t in written}
    states = [t["state"] for t in after.values()]
    summary = {"rebuilt": False, "error": None, "usage": None}
    llm = sums.summarizer_llm(config)
    if llm.enabled:
        try:
            sums.rebuild_one(db, manuscript, rel, llm)
            summary.update(rebuilt=True, usage=llm.stats_line())
        except Exception as err:                        # noqa: BLE001
            summary["error"] = str(err)
    candidate = passes.settle_learnings(db, manuscript, diffs, config)
    return {"file": rel, "accepted": states.count("cleaned"),
            "declined": states.count("declined"),
            "forms": len(forms), "diffs": diffs, "warnings": warnings,
            "summary": summary, "pattern_candidate": candidate,
            "tab_still_marked": True}


def filter_unmark(db: Database, manuscript: dict, file: str,
                  force: bool = False, services=None) -> dict:
    """Put the original text back and RETURN the written forms to
    `accepted`. Two lines of recovery for a state that is fully
    described by the bytes — including the crash between "compose" and
    "write threads", which leaves a marked file with no written rows.

    Returned to `accepted`, not withdrawn, and the difference matters:
    the author's verdicts survive. Unmark undoes the MARKING, not the
    triage. `filter resolve` applies them, `filter triage --undo` reopens
    them, `filter abandon` throws them away — and the author chooses
    which, because discarding a verdict is never something a recovery
    verb does on its own.

    In DOC mode there are no local bytes to strip, so the recovery is
    `critique rollback`'s own trick (§2.5): return the `written` threads
    to `accepted` FIRST — which is what lifts `push_doc`'s
    `forms_pending` refusal — then `push_doc` the unchanged local file to
    rebuild the tab clean. The verdicts survive as on the local road.

    **The rebuild is WHOLE-TAB, and the refusal must say so.** It is not
    only the green halves that go: `push_doc` reconstructs the essay's
    tab from the local file, so an edit the author made anywhere else in
    that tab since the push is destroyed too, and nothing has recorded
    it. That is why this branch alone requires `--force` (Q-4) — the one
    place doc mode is deliberately less convenient than local, because
    it is the one place the loss is unrecoverable.

    The refusal names the exit that costs nothing: `doc pull <essay>`
    brings a Doc-side edit made OUTSIDE the marked passages down to the
    local file first, leaving the tab and the forms alone (§2.6), after
    which this verb costs only the green halves. Preserving that edit
    HERE was considered and refused: it would put a tab read and a
    three-way inside a recovery verb, which is new machinery in the one
    place the author reaches for when something has already gone wrong."""
    from . import gdocs

    mid = manuscript["id"]
    rel = _resolve_relpath(manuscript, file)
    written = staging.door_threads(db, mid, rel, states=("written",),
                                   origin_type=FILTER_ORIGIN)
    path = Path(manuscript["path"]) / rel
    if _file_run_mode(db, mid, rel) == "doc":
        if not force:
            raise ValueError(
                f"{len(written)} form(s) of {rel} are out in the Google "
                f"Doc, and taking them back out REBUILDS THE WHOLE TAB "
                f"from the local file. Everything you have typed in that "
                f"tab since the push goes: the rewording inside the "
                f"green halves, and any other edit you made anywhere "
                f"else in {rel}'s tab. None of it is recorded anywhere "
                f"else. (The file on disk is untouched either way — it "
                f"has held the old essay throughout.) "
                f"To keep your wording, finish in the Doc and 'filter "
                f"resolve {rel}'. To keep an edit you made OUTSIDE the "
                f"marked passages, run 'doc pull {rel}' first: it brings "
                f"that edit down to the file and leaves the tab and the "
                f"forms alone. Then re-run with --force.")
        # Withdraw FIRST: `push_doc`'s DB guard refuses while any form is
        # `written`, and it is right to. Returning the threads to their
        # PRE-PUSH state is what lifts it.
        for t in written:
            db.update("doc_threads", t["id"], {"state": _pushed_from(t)})
        service, docs_service = _resolve_services(services, rel)
        gdocs.push_doc(db, manuscript, rel, service=service,
                       docs_service=docs_service)
        return {"file": rel, "reopened": len(written), "mode": "doc",
                "text": path.read_text(encoding="utf-8"),
                "marker_warnings": []}
    _checkout_gate(db, manuscript, rel)
    text, marker_warnings = staging.unmark(path)
    for t in written:
        db.update("doc_threads", t["id"], {"state": _pushed_from(t)})
    return {"file": rel, "reopened": len(written), "mode": "local",
            "text": text, "marker_warnings": marker_warnings}


def filter_rollback(db: Database, manuscript: dict, config: dict, file: str,
                    name: str | None = None) -> dict:
    """Restore the run's pinned source version. The verdicts STAY: they
    are evidence, and evidence is not undone by putting text back."""
    mid = manuscript["id"]
    rel = _resolve_relpath(manuscript, file)
    row = db.one(
        "SELECT * FROM filter_runs WHERE manuscript_id = ? AND file = ? "
        + ("AND filter = ? " if name else "")
        + "ORDER BY created_at DESC LIMIT 1",
        (mid, rel, name) if name else (mid, rel))
    if row is None:
        raise LookupError(f"no filter run on {rel} to roll back.")
    run = dict(row)
    # The DB guard, FIRST and in either mode (§2.5 / D-4). In doc mode
    # the local file is unmarked, so the byte check below is BLIND: the
    # rollback would proceed, restoring the pin over a file whose forms
    # are sitting in the Doc pointed at text that no longer exists there,
    # with the author's rewordings destroyed and nothing left to recover
    # them from. That is exactly the failure the byte refusal exists to
    # prevent, so it gets a refusal that can see it. Ordered
    # most-informative first, the shape `push_doc`'s two guards use, and
    # both stay separately reachable.
    out = [t for t in _run_threads(db, mid, run)
           if t["state"] == "written"]
    if out:
        mode = _run_mode(run)
        where = ("in the Google Doc" if mode == "doc"
                 else "in the file on disk")
        unmark = (f"filter unmark {rel} --force" if mode == "doc"
                  else f"filter unmark {rel}")
        raise ValueError(
            f"{len(out)} form(s) of this run are still out {where}, and "
            f"you may have already reworded them — a rollback would "
            f"destroy those edits with nothing left to recover them "
            f"from. Finalize what is there ('filter resolve {rel}'), or "
            f"take the forms back out ('{unmark}'), and then roll back "
            f"if you still want to.")
    _checkout_gate(db, manuscript, rel)
    # A marked file is mid-settle: its bytes are staged proposals the
    # author may have post-edited, and rolling back over them would
    # discard those edits AND leave written threads pointing at text
    # that is no longer there. Refuse, naming both exits — the same
    # shape `filter abandon` uses, and for the same reason: the author
    # decides whether the pause ends in an apply or an undo.
    #
    # Chosen over documenting an orphan recovery because the orphan this
    # would create is worse than the one `filter status` already finds:
    # there the bytes describe the state completely, whereas a rollback
    # mid-pause destroys the author's post-edits with nothing left to
    # recover them from.
    if staging.is_marked((Path(manuscript["path"]) / rel)
                         .read_text(encoding="utf-8")):
        raise ValueError(
            f"{rel} is mid-settle: its bytes carry staged forms you may "
            f"have already reworded, and a rollback would destroy those "
            f"edits with nothing left to recover them from. Finalize "
            f"('filter resolve {rel}') or put the original text back "
            f"('filter unmark {rel}') first — then roll back if you "
            f"still want to.")
    with contextlib.redirect_stdout(io.StringIO()):
        collect(db, manuscript, config, source="pre-filter-rollback")
    text = staging.rollback(db, manuscript, rel, run["source_version_id"])
    with contextlib.redirect_stdout(io.StringIO()):
        collect(db, manuscript, config, source="filter-rollback",
                episode=NO_EPISODE)
    return {"file": rel, "run": run, "restored_chars": len(text)}


def filter_abandon(db: Database, manuscript: dict, file: str,
                   name: str | None = None) -> dict:
    """Drop an active run and withdraw its open proposals. Written forms
    are NOT touched — the file's bytes carry them, and `filter unmark`
    is the verb that ends that state."""
    mid = manuscript["id"]
    rel = _resolve_relpath(manuscript, file)
    run = _filter_run_row(db, manuscript, rel, name)
    threads = _run_threads(db, mid, run)
    written = [t for t in threads if t["state"] == "written"]
    if written:
        raise ValueError(
            f"{rel} is marked — {len(written)} form(s) are written into "
            f"the file. Finalize ('filter resolve {rel}') or put the text "
            f"back ('filter unmark {rel}') before abandoning the run.")
    withdrawn = 0
    for t in threads:
        if t["state"] in ("proposed", "accepted", "rejected"):
            db.update("doc_threads", t["id"], {"state": "withdrawn"})
            withdrawn += 1
    db.update("filter_runs", run["id"], {"status": "abandoned"})
    return {"file": rel, "run": dict(run, status="abandoned"),
            "withdrawn": withdrawn}


def filter_status(db: Database, manuscript: dict,
                  file: str | None = None) -> dict:
    """Run history per (filter, file) with the tallies, so a filter that
    never settles down is visible without the author having to notice
    it — plus the orphaned-mark detection: bytes that carry forms with
    no matching written row.

    A doc-mode run with forms out carries its TAB URL, read from the
    stored mapping — no network, no credentials. The status verb is
    where an author goes to find a run they have half-forgotten, and on
    the Doc road the forms are somewhere this shell cannot show them.

    The orphan scan is BYTE-based and therefore covers LOCAL mode only.
    That is the one doctrine cost doc mode pays (RISK-1): forms left in
    a tab with no rows describing them cannot be detected from here
    without a network call this verb has no credentials for. The report
    says so rather than letting a clean scan read as a clean bill of
    health, and names the documented recovery."""
    from . import filters as flt
    from . import gdocs

    mid = manuscript["id"]
    where = "WHERE manuscript_id = ?" + (" AND file = ?" if file else "")
    args = (mid, file) if file else (mid,)
    rows = [dict(r) for r in db.all(
        f"SELECT * FROM filter_runs {where} ORDER BY created_at", args)]
    classes = {f["name"]: f["class"] for f in flt.list_filters(manuscript)}
    links = gdocs._mapping(db, manuscript).get("gdocs", {})
    master_id = links.get("_master_id")
    runs = []
    for run in rows:
        threads = _run_threads(db, mid, run)
        drift = (classes.get(run["filter"])
                 if classes.get(run["filter"]) not in (None, run["class"])
                 else None)
        mode = _run_mode(run)
        forms_out = sum(1 for t in threads if t["state"] == "written")
        tab_id = (links.get(run["file"]) or {}).get("tab_id")
        runs.append({
            "id": run["id"], "filter": run["filter"], "file": run["file"],
            "class": run["class"], "class_now": drift,
            "mode": mode, "forms_out": forms_out,
            "tab_url": (gdocs.tab_url(master_id, tab_id)
                        if mode == "doc" and forms_out
                        and master_id and tab_id else None),
            "status": run["status"], "cursor": run["cursor"],
            "unit_count": run["unit_count"],
            "date": (run["created_at"] or "")[:10],
            "has_registry": bool((run["registry"] or "").strip()),
            "state_chars": len(run["state"] or ""),
            **_run_tallies(threads)})
    orphans = []
    root = Path(manuscript["path"])
    for rel in sorted({r["file"] for r in rows} if rows else set()):
        path = root / rel
        if not path.exists():
            continue
        if not staging.is_marked(path.read_text(encoding="utf-8")):
            continue
        if not staging.door_threads(db, mid, rel, states=("written",),
                                    origin_type=FILTER_ORIGIN):
            orphans.append(rel)
    doc_files = sorted({r["file"] for r in runs if r["mode"] == "doc"})
    return {"runs": runs, "orphaned_marks": orphans,
            "orphan_scan_note": (
                f"The orphan scan above reads the BYTES on disk, so it "
                f"covers the LOCAL road only. "
                f"{', '.join(doc_files)} has had forms in the Doc, and a "
                f"tab left carrying forms that no row describes cannot "
                f"be seen from here — that would take a network call "
                f"this verb has no credentials for. If a tab still shows "
                f"struck-and-green text for a finished essay, 'doc push "
                f"<essay>' rebuilds it from the local file."
                if doc_files else None)}
