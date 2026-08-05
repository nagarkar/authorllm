"""Storage layer.

Every canonical persistent object derives from the KnowledgeObject base
(RFC AuthorLM §18.3): id, version, created_at, created_by, schema_version,
metadata. Historical objects (versions, transitions, intents, reviews,
evidence) are immutable; only beliefs/policies/graph statuses evolve, and
they evolve by bumping `version` in place while history stays in the
`evidence` and `editorial_reviews` tables (RFC Common Core §6.5).
"""

from __future__ import annotations

import getpass
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import SCHEMA_VERSION

KNOWLEDGE_OBJECT_COLUMNS = """
    id TEXT PRIMARY KEY,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}'
"""

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS manuscripts (
    {KNOWLEDGE_OBJECT_COLUMNS},
    name TEXT NOT NULL UNIQUE,
    path TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS manuscript_versions (
    {KNOWLEDGE_OBJECT_COLUMNS},
    manuscript_id TEXT NOT NULL REFERENCES manuscripts(id),
    version_no INTEGER NOT NULL,
    checksum TEXT NOT NULL,
    files TEXT NOT NULL,          -- JSON: relpath -> full text (complete manuscript recoverable, §18.4)
    source TEXT NOT NULL,         -- e.g. 'snapshot'
    session_id TEXT
);

CREATE TABLE IF NOT EXISTS editorial_transitions (
    {KNOWLEDGE_OBJECT_COLUMNS},
    manuscript_id TEXT NOT NULL,
    version_before TEXT,          -- manuscript_versions.id (NULL for the first version)
    version_after TEXT NOT NULL,
    kind TEXT NOT NULL,           -- insert | delete | rewrite | file_added | file_removed
    location TEXT NOT NULL,       -- file[#nearest-heading]
    summary TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '{{}}'
);

CREATE TABLE IF NOT EXISTS sessions (
    {KNOWLEDGE_OBJECT_COLUMNS},
    manuscript_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    status TEXT NOT NULL DEFAULT 'active'   -- active | ended
);

CREATE TABLE IF NOT EXISTS declared_intents (
    {KNOWLEDGE_OBJECT_COLUMNS},
    manuscript_id TEXT NOT NULL,
    session_id TEXT,
    statement TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',  -- active | completed | abandoned
    outcome TEXT
);

CREATE TABLE IF NOT EXISTS style_guides (
    {KNOWLEDGE_OBJECT_COLUMNS},
    manuscript_id TEXT NOT NULL,
    name TEXT NOT NULL,
    parent TEXT                             -- style_guides.id | NULL = root
);

CREATE TABLE IF NOT EXISTS style_attachments (
    {KNOWLEDGE_OBJECT_COLUMNS},
    manuscript_id TEXT NOT NULL,
    file TEXT NOT NULL,
    guide_id TEXT NOT NULL,                 -- style_guides.id
    UNIQUE (manuscript_id, file)            -- one guide per file
);

CREATE TABLE IF NOT EXISTS style_elements (
    {KNOWLEDGE_OBJECT_COLUMNS},
    manuscript_id TEXT NOT NULL,
    guide_id TEXT,        -- owning guide | NULL when file-local
    file TEXT,            -- filename for file-local overrides | NULL
    aspect TEXT NOT NULL, -- register|lexicon|syntax|structure|formatting|citation|rhetoric|figure|tone
    statement TEXT NOT NULL,
    notes TEXT,
    status TEXT NOT NULL DEFAULT 'active',  -- active | retired
    overrides TEXT,       -- style_elements.id displaced by this element
    CHECK ((guide_id IS NULL) != (file IS NULL))
);

CREATE TABLE IF NOT EXISTS inferred_intents (
    {KNOWLEDGE_OBJECT_COLUMNS},
    manuscript_id TEXT NOT NULL,
    statement TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.3,
    status TEXT NOT NULL DEFAULT 'hypothesis',  -- hypothesis | confirmed | retired
    evidence TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS editorial_episodes (
    {KNOWLEDGE_OBJECT_COLUMNS},
    manuscript_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    intent_id TEXT,               -- declared_intents.id
    transition_ids TEXT NOT NULL DEFAULT '[]',
    outcome TEXT,
    status TEXT NOT NULL DEFAULT 'open'     -- open | closed
);

CREATE TABLE IF NOT EXISTS concept_nodes (
    {KNOWLEDGE_OBJECT_COLUMNS},
    manuscript_id TEXT NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'concept',   -- concept | definition | objection | example | metaphor | question | historical_reference | mathematical_construct | syllogism
    status TEXT NOT NULL DEFAULT 'declared',-- declared | realized
    introduced_in TEXT,
    notes TEXT,
    aliases TEXT NOT NULL DEFAULT '[]'      -- JSON list of alternate names
);

CREATE TABLE IF NOT EXISTS concept_edges (
    {KNOWLEDGE_OBJECT_COLUMNS},
    manuscript_id TEXT NOT NULL,
    from_node TEXT NOT NULL,      -- concept_nodes.id
    relation TEXT NOT NULL,       -- depends_on | motivates | contrasts_with | elaborates | permits | answers | foreshadows | illustrates | co_occurs
    to_node TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'declared',-- declared | inferred | realized
    support INTEGER NOT NULL DEFAULT 0,     -- observations supporting an inferred edge
    evidence TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS editorial_policies (
    {KNOWLEDGE_OBJECT_COLUMNS},
    manuscript_id TEXT NOT NULL,
    statement TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'candidate',  -- candidate | validated | retired
    confidence REAL NOT NULL DEFAULT 0.3,
    supporting INTEGER NOT NULL DEFAULT 0,
    contradicting INTEGER NOT NULL DEFAULT 0,
    outstanding_questions TEXT NOT NULL DEFAULT '[]',
    source TEXT NOT NULL DEFAULT 'review-explanation'
);

CREATE TABLE IF NOT EXISTS guidance_history (
    {KNOWLEDGE_OBJECT_COLUMNS},
    manuscript_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    intent_id TEXT,
    batch_id TEXT NOT NULL,
    batch_index INTEGER NOT NULL,           -- 0 for abstention record
    kind TEXT NOT NULL,                     -- bridge | prerequisite | definition | policy_reminder | focus | abstention
    suggestion TEXT NOT NULL,
    explanation TEXT NOT NULL,              -- why: evidence this traces to (§11.6)
    state TEXT NOT NULL DEFAULT 'proposed'  -- proposed | accepted | rejected | modified | deferred | superseded
);

CREATE TABLE IF NOT EXISTS editorial_reviews (
    {KNOWLEDGE_OBJECT_COLUMNS},
    manuscript_id TEXT NOT NULL,
    guidance_id TEXT NOT NULL,
    decision TEXT NOT NULL,                 -- accepted | rejected | modified | deferred
    explanation TEXT
);

CREATE TABLE IF NOT EXISTS knowledge_proposals (
    {KNOWLEDGE_OBJECT_COLUMNS},
    manuscript_id TEXT NOT NULL,
    kind TEXT NOT NULL,           -- note_update | revival | edge_reproposal | policy_revival
    target TEXT NOT NULL,         -- id of the settled object the proposal is against
    payload TEXT NOT NULL,        -- JSON: current vs proposed
    content_hash TEXT NOT NULL,   -- dedupe: a dismissed proposal never returns verbatim
    source TEXT NOT NULL DEFAULT 'extraction',
    state TEXT NOT NULL DEFAULT 'open'      -- open | adopted | dismissed
);

CREATE TABLE IF NOT EXISTS evidence (
    {KNOWLEDGE_OBJECT_COLUMNS},
    manuscript_id TEXT NOT NULL,
    episode_id TEXT,
    evidence_type TEXT NOT NULL,            -- author_review | briefing_answer | observation
    signal TEXT NOT NULL,                   -- accepted | rejected | modified | deferred | declared
    target TEXT NOT NULL,
    supports_policy TEXT,                   -- editorial_policies.id
    weight TEXT NOT NULL DEFAULT 'medium'
);

CREATE TABLE IF NOT EXISTS doc_comments (
    {KNOWLEDGE_OBJECT_COLUMNS},
    manuscript_id TEXT NOT NULL,
    comment_id TEXT NOT NULL,     -- Drive comment id (dedupe + resolve handle)
    file TEXT,                    -- relpath; NULL when the quote match is ambiguous
    location TEXT,                -- file#nearest-heading (transition convention)
    quoted TEXT,                  -- the anchor text the comment was attached to
    content TEXT NOT NULL,        -- the author's words, verbatim
    author TEXT,
    comment_created TEXT,         -- Doc-side timestamp
    replies TEXT NOT NULL DEFAULT '[]',
    state TEXT NOT NULL DEFAULT 'ingested', -- ingested | resolved
    UNIQUE (manuscript_id, comment_id)
);

CREATE TABLE IF NOT EXISTS writeups (
    {KNOWLEDGE_OBJECT_COLUMNS},
    manuscript_id TEXT NOT NULL,
    intent_id TEXT NOT NULL,      -- declared_intents.id; N writeups per intent
    file TEXT NOT NULL,           -- relpath; exactly one file per writeup
    mode TEXT NOT NULL DEFAULT 'fresh',     -- fresh (revision mode not built)
    status TEXT NOT NULL DEFAULT 'active',  -- active | completed | abandoned
    source_version_id TEXT,       -- manuscript_versions.id pinned at start;
                                  -- raw material for drafting, restore target
                                  -- for abandon
    plan TEXT NOT NULL DEFAULT '[]',        -- JSON: ordered beat specs, each
                                  -- carrying a stable monotonic n (never
                                  -- reused across replans)
    cursor INTEGER NOT NULL DEFAULT 0,      -- index into plan of the current beat
    learnings TEXT NOT NULL DEFAULT '[]'    -- JSON: distilled session lessons
);

CREATE TABLE IF NOT EXISTS improvement_tasks (
    {KNOWLEDGE_OBJECT_COLUMNS},
    manuscript_id TEXT,           -- provenance only; NULL = tool-global defect
    session_id TEXT,              -- authoring session where the defect was confirmed
    title TEXT NOT NULL,
    evidence TEXT NOT NULL,       -- what happened, with file:line specifics
    given TEXT NOT NULL,          -- repro: input/state
    observed TEXT NOT NULL,       -- repro: wrong behavior
    expected TEXT NOT NULL,       -- repro: author's verdict, verbatim where possible
    status TEXT NOT NULL DEFAULT 'open',    -- open | in_progress | proposed | resolved | dismissed
    resolution TEXT               -- fix summary + encoded test, or dismissal reason
);
"""


def now_iso() -> str:
    # Microsecond resolution: session boundaries and the events inside them
    # can land in the same second, and briefing cutoffs compare with '>'.
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def ko_fields(prefix: str) -> dict[str, Any]:
    """Fresh KnowledgeObject base fields."""
    return {
        "id": new_id(prefix),
        "version": 1,
        "created_at": now_iso(),
        "created_by": getpass.getuser(),
        "schema_version": SCHEMA_VERSION,
        "metadata": "{}",
    }


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        # WAL: background watcher writes proceed alongside interactive reads.
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.executescript(SCHEMA)
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(concept_nodes)")}
        if "aliases" not in cols:
            self.conn.execute(
                "ALTER TABLE concept_nodes ADD COLUMN aliases TEXT NOT NULL DEFAULT '[]'"
            )
        self.conn.commit()

    def insert(self, table: str, row: dict[str, Any]) -> str:
        cols = ", ".join(row)
        placeholders = ", ".join("?" for _ in row)
        self.conn.execute(
            f"INSERT INTO {table} ({cols}) VALUES ({placeholders})", list(row.values())
        )
        self.conn.commit()
        return row["id"]

    def update(self, table: str, obj_id: str, changes: dict[str, Any]) -> None:
        """Evolve a mutable knowledge object, bumping its version."""
        sets = ", ".join(f"{c} = ?" for c in changes)
        self.conn.execute(
            f"UPDATE {table} SET {sets}, version = version + 1 WHERE id = ?",
            [*changes.values(), obj_id],
        )
        self.conn.commit()

    def one(self, sql: str, args: tuple = ()) -> sqlite3.Row | None:
        return self.conn.execute(sql, args).fetchone()

    def all(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, args).fetchall()


def loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default
