"""Storage layer.

Every canonical persistent object derives from the KnowledgeObject base
(RFC AuthorLM §18.3): id, version, created_at, created_by, schema_version,
metadata. Historical objects (versions, transitions, intents, reviews,
evidence) are immutable; only beliefs/beliefs/graph statuses evolve, and
they evolve by bumping `version` in place while history stays in the
`evidence` and `editorial_reviews` tables (RFC Common Core §6.5).
"""

from __future__ import annotations

import getpass
import json
import sqlite3
import time
import unicodedata
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import SCHEMA_VERSION, dbperf

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
    path TEXT NOT NULL,
    author TEXT NOT NULL DEFAULT '',
    copyright_owner TEXT NOT NULL DEFAULT '',
    paperback_isbn TEXT NOT NULL DEFAULT '',
    hardcover_isbn TEXT NOT NULL DEFAULT ''
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

CREATE TABLE IF NOT EXISTS illustration_prompt_versions (
    {KNOWLEDGE_OBJECT_COLUMNS},
    manuscript_id TEXT NOT NULL REFERENCES manuscripts(id),
    version_no INTEGER NOT NULL,
    checksum TEXT NOT NULL,
    files TEXT NOT NULL,          -- JSON: <slug>.md -> raw file text (_illustrations/prompts/ is
                                   -- '_'-prefixed and so invisible to manuscript_versions/collect_revision
                                   -- on purpose; this is its own recovery point, X7-3)
    source TEXT NOT NULL          -- e.g. 'pre-doc-pull', 'pre-reconcile'
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
                                            -- | proposed | rejected (non-author
                                            -- sources are born proposed; a
                                            -- rejection is kept as evidence)
    outcome TEXT,
    scope TEXT,           -- file path narrowing the intent: an essay, a
                          -- part-opener (chapter-wide via the toc parent
                          -- chain), or NULL = manuscript-wide
    source_id TEXT        -- sources.id: whose judgment originated this
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

CREATE TABLE IF NOT EXISTS style_laws (
    {KNOWLEDGE_OBJECT_COLUMNS},
    manuscript_id TEXT NOT NULL,
    guide_id TEXT,        -- owning guide | NULL when file-local
    file TEXT,            -- filename for file-local overrides | NULL
    aspect TEXT NOT NULL, -- register|lexicon|syntax|structure|formatting|citation|rhetoric|figure|tone
    statement TEXT NOT NULL,
    notes TEXT,
    status TEXT NOT NULL DEFAULT 'active',  -- active | retired | proposed
                                            -- | rejected (critique intake)
    overrides TEXT,       -- style_laws.id displaced by this element
    source_id TEXT,
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
    kind TEXT NOT NULL DEFAULT 'concept',   -- concept | objection | example | metaphor | question | historical_reference | mathematical_construct | syllogism
    status TEXT NOT NULL DEFAULT 'declared',-- declared | realized
    introduced_in TEXT,
    notes TEXT,
    aliases TEXT NOT NULL DEFAULT '[]',     -- JSON list of alternate names
    source_id TEXT
);

CREATE TABLE IF NOT EXISTS concept_edges (
    {KNOWLEDGE_OBJECT_COLUMNS},
    manuscript_id TEXT NOT NULL,
    from_node TEXT NOT NULL,      -- concept_nodes.id
    relation TEXT NOT NULL,       -- depends_on | motivates | contrasts_with | elaborates | generalizes | specializes | answers | foreshadows | illustrates | permits | creates | distinguishes | defines | leads_to | refutes | co_occurs
    to_node TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'declared',-- declared | inferred | realized
    support INTEGER NOT NULL DEFAULT 0,     -- observations supporting an inferred edge
    evidence TEXT NOT NULL DEFAULT '[]',
    source_id TEXT
);

CREATE TABLE IF NOT EXISTS editorial_beliefs (
    {KNOWLEDGE_OBJECT_COLUMNS},
    manuscript_id TEXT NOT NULL,
    statement TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'candidate',  -- candidate | validated | retired
    confidence REAL NOT NULL DEFAULT 0.3,
    supporting INTEGER NOT NULL DEFAULT 0,
    contradicting INTEGER NOT NULL DEFAULT 0,
    outstanding_questions TEXT NOT NULL DEFAULT '[]',
    source TEXT NOT NULL DEFAULT 'review-explanation',
    source_id TEXT
);

CREATE TABLE IF NOT EXISTS guidance_history (
    {KNOWLEDGE_OBJECT_COLUMNS},
    manuscript_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    intent_id TEXT,
    batch_id TEXT NOT NULL,
    batch_index INTEGER NOT NULL,           -- 0 for abstention record
    kind TEXT NOT NULL,                     -- bridge | prerequisite | definition | belief_reminder | focus | abstention
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
    kind TEXT NOT NULL,           -- note_update | revival | edge_reproposal | belief_revival
    target TEXT NOT NULL,         -- id of the resolved object the proposal is against
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
    supports_belief TEXT,                   -- editorial_beliefs.id
    weight TEXT NOT NULL DEFAULT 'medium',
    source_id TEXT
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

CREATE TABLE IF NOT EXISTS doc_threads (
    {KNOWLEDGE_OBJECT_COLUMNS},
    manuscript_id TEXT NOT NULL,
    origin_type TEXT NOT NULL,    -- author_comment | critique | filter  (and
                                  -- `lens` when a lens finding starts carrying
                                  -- a proposal — filter-pass design §2.4's
                                  -- deferred contract). The PRODUCER at the
                                  -- finding→edit door (§2.1).
    origin_id TEXT NOT NULL,      -- Drive comment id | owner:file:ordinal
                                  -- (pass or filter run id + ordinal); the
                                  -- thread's join key
    file TEXT NOT NULL,           -- relpath of the tab the thread lives in
    anchor_quote TEXT,            -- the span the author's comment anchors
    proposed_old TEXT,            -- exact text to be replaced (verbatim law);
                                  -- '' for a {{new}}-only insertion
    proposed_new TEXT,            -- exact replacement
    note TEXT,                    -- short body of our prefixed proposal reply /
                                  -- the editor's one-line why
    state TEXT NOT NULL DEFAULT 'proposed',
        -- proposed | conversation | applied | cleaned | declined |
        -- withdrawn | stale
        -- critique threads add: accepted | rejected | written
    our_reply_ids TEXT NOT NULL DEFAULT '[]',   -- JSON: replies we posted
    last_author_reply_id TEXT,    -- idempotency watermark for verdicts
    scope_kind TEXT,              -- file | guide | manuscript (evidence step)
    scope_ref TEXT,               -- relpath, sg-*, or ms-* (ids where they exist)
    UNIQUE (manuscript_id, origin_type, origin_id)
);

CREATE TABLE IF NOT EXISTS illus_proposals (
    {KNOWLEDGE_OBJECT_COLUMNS},
    manuscript_id TEXT NOT NULL,
    file TEXT NOT NULL,           -- relpath the placement targets
    anchor TEXT NOT NULL,         -- verbatim paragraph tail the tag follows
    description TEXT NOT NULL,    -- proposed [Illustration: …] description
    criterion TEXT,               -- which ratified placement criterion
    rationale TEXT,               -- the spot-finder's one-line why
    revises TEXT,                 -- existing tag prompt when this is a
                                  -- revision, else NULL (new placement)
    state TEXT NOT NULL DEFAULT 'proposed'
        -- proposed | accepted | rejected | stale
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

CREATE TABLE IF NOT EXISTS sources (
    {KNOWLEDGE_OBJECT_COLUMNS},
    kind TEXT NOT NULL,           -- author | critic | system
    name TEXT NOT NULL,           -- "nagarkar", "SMSTTD Editorial Analysis", "authorlm"
    detail TEXT,                  -- e.g. "literary critic, docx, 2026-08-13"
    UNIQUE (kind, name)
);

CREATE TABLE IF NOT EXISTS essay_summaries (
    {KNOWLEDGE_OBJECT_COLUMNS},
    manuscript_id TEXT NOT NULL,
    file TEXT NOT NULL,           -- toc [[chapter]] entry
    summary TEXT NOT NULL,        -- the editor's working memory of this essay
    source_hash TEXT NOT NULL,    -- hash of the essay text summarized; a
                                  -- mismatch is a lie about the text and
                                  -- blocks the edit pass
    upstream_hash TEXT NOT NULL,  -- hash of the prior summaries this one was
                                  -- conditioned on
    upstream_stale INTEGER NOT NULL DEFAULT 0,  -- tolerated by the edit pass;
                                  -- cleared by rebuild (mark, don't cascade)
    status TEXT NOT NULL DEFAULT 'current',  -- current | deprecated (the
                                  -- file left the toc — never deleted, the
                                  -- record of an essay that existed; excluded
                                  -- from stale counts and the edit pass's
                                  -- prerequisites, both of which are driven
                                  -- off the current toc reading order)
    UNIQUE (manuscript_id, file)
);

CREATE TABLE IF NOT EXISTS critique_passes (
    {KNOWLEDGE_OBJECT_COLUMNS},
    manuscript_id TEXT NOT NULL,
    source_id TEXT NOT NULL,      -- sources.id of the critique being processed
    cursor INTEGER NOT NULL DEFAULT 0,      -- index into toc reading order
    essay_state TEXT NOT NULL DEFAULT '{{}}',   -- JSON: file -> pending |
                                  -- proposed | triaged | written | resolved | skipped
    pinned_versions TEXT NOT NULL DEFAULT '{{}}',  -- JSON: file ->
                                  -- manuscript_versions.id (pre-pass pin, set
                                  -- at diff-write; rollback target)
    status TEXT NOT NULL DEFAULT 'active'   -- active | completed | abandoned
);

-- One application of one FILTER to one file (filter-pass design §1.7).
-- A filter run is not bound to a writeup, an intent, a critique pass or a
-- session, so it has no host row to hang metadata off: it gets its own
-- table rather than corrupting critique_passes' state machine. No index
-- (§15.17's discipline); dbperf is the trigger if the tables ever grow.
CREATE TABLE IF NOT EXISTS filter_runs (
    {KNOWLEDGE_OBJECT_COLUMNS},
    manuscript_id       TEXT NOT NULL,
    filter              TEXT NOT NULL,   -- the artifact's name (_filters/<name>.md)
    class               TEXT NOT NULL,   -- sequential | global, FROZEN at run start
    file                TEXT NOT NULL,   -- relpath; exactly one file per run
    source_version_id   TEXT,            -- manuscript_versions.id pinned at start:
                                         -- the text the units were read from, and
                                         -- the rollback target
    unit_count          INTEGER NOT NULL DEFAULT 0,   -- units at pin time
    cursor              INTEGER NOT NULL DEFAULT 0,   -- units recorded so far
    state               TEXT,            -- opaque carried text (sequential)
    registry            TEXT,            -- opaque frozen text (global prelude)
    result_version_id   TEXT,            -- manuscript_versions.id produced at resolve
    status              TEXT NOT NULL DEFAULT 'active'
                                         -- active | settled | abandoned
);

CREATE TABLE IF NOT EXISTS triage_runs (
    {KNOWLEDGE_OBJECT_COLUMNS},
    manuscript_id TEXT NOT NULL,
    triage_type TEXT NOT NULL,              -- concepts | edges
    profile_id TEXT NOT NULL,
    profile_version TEXT NOT NULL,
    profile_snapshot TEXT NOT NULL,
    manuscript_version_id TEXT NOT NULL,
    requested_ids TEXT NOT NULL DEFAULT '[]',
    context TEXT NOT NULL DEFAULT '{{}}',
    status TEXT NOT NULL DEFAULT 'incomplete', -- incomplete | complete
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS triage_assessments (
    {KNOWLEDGE_OBJECT_COLUMNS},
    manuscript_id TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES triage_runs(id),
    triage_type TEXT NOT NULL,
    object_id TEXT NOT NULL,
    object_version INTEGER NOT NULL,
    output TEXT NOT NULL,
    UNIQUE (run_id, object_id)
);

CREATE TABLE IF NOT EXISTS triage_drafts (
    {KNOWLEDGE_OBJECT_COLUMNS},
    manuscript_id TEXT NOT NULL,
    triage_type TEXT NOT NULL,
    object_id TEXT NOT NULL,
    object_version INTEGER NOT NULL,
    action TEXT NOT NULL,
    parameters TEXT NOT NULL DEFAULT '{{}}',
    reason TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE (manuscript_id, triage_type, object_id)
);

CREATE INDEX IF NOT EXISTS idx_triage_runs_lookup
    ON triage_runs (manuscript_id, triage_type, profile_id, profile_version, created_at);
CREATE INDEX IF NOT EXISTS idx_triage_assessments_lookup
    ON triage_assessments (manuscript_id, triage_type, object_id, created_at);
CREATE INDEX IF NOT EXISTS idx_triage_drafts_lookup
    ON triage_drafts (manuscript_id, triage_type, object_id);
"""


def now_iso() -> str:
    # Microsecond resolution: session boundaries and the events inside them
    # can land in the same second, and briefing cutoffs compare with '>'.
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def ko_fields(prefix: str) -> dict[str, Any]:
    """Fresh KnowledgeObject base fields.

    This is the single constructor of the KnowledgeObject base for every
    table in the schema, so it is also the single client-provenance stamp
    site: every row born after this change carries the join key naming
    the chat that produced it, with zero call-site edits and nothing to
    drift when a table is added (design §5.1). ~70 bytes and one dict
    lookup per row; it gates nothing, and an unresolved client
    contributes an empty `{}`, exactly as before."""
    from . import clients

    return {
        "id": new_id(prefix),
        "version": 1,
        "created_at": now_iso(),
        "created_by": getpass.getuser(),
        "schema_version": SCHEMA_VERSION,
        "metadata": json.dumps(clients.row_stamp()),
    }


# Tables whose rows originate from someone's judgment and carry provenance.
PROVENANCE_TABLES = (
    "declared_intents", "concept_nodes", "concept_edges",
    "style_laws", "editorial_beliefs", "evidence",
)

# Evidence provenance is fully determined by evidence_type. Most types record
# an author verdict or act; these are decisions made by AuthorLM itself.
SYSTEM_EVIDENCE_TYPES = {"episode_analysis", "deterministic_triage",
                         "extraction_adjudication"}


def _unicode_lower(value: Any) -> str | None:
    """Case-fold one SQL argument the same way Python does everywhere else
    in this codebase (`.lower()`), instead of SQLite's built-in `lower()`,
    which only knows ASCII. Registered over the name `LOWER` (see
    Database.__init__) so every existing `lower(...)` in a SQL string gets
    this behaviour automatically — no call site needs editing.

    Normalizes to NFC first: two names that render identically but differ
    in composed vs. decomposed accents (`ā` as one codepoint vs. 'a' +
    combining macron) must compare equal, or the same ASCII/Unicode split
    this function exists to close would just reopen one step later.
    """
    if value is None:
        return None
    return unicodedata.normalize("NFC", str(value)).lower()


def _fetchone(cursor: sqlite3.Cursor) -> Any:
    return cursor.fetchone()


def _fetchall(cursor: sqlite3.Cursor) -> Any:
    return cursor.fetchall()


def _cursor(cursor: sqlite3.Cursor) -> Any:
    return cursor


class Database:
    def __init__(self, path: Path):
        self.path = path
        # The performance log's aggregation dict lives on the instance
        # (dbperf.__doc__); `None` when [db] perf_log is false, which is
        # the whole of the off-switch — see `_run`. Built before the
        # connection, so anything the migration routes through this
        # class's own surface is timed like any other query.
        self._perf = dbperf.recorder_for(path)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        # SQLite's built-in lower() is ASCII-only and disagrees with
        # Python's str.lower() on exactly the non-Latin names this project
        # stores (e.g. Sanskrit terms). Overriding it here makes every
        # `lower(...)` already written in this module's SQL Unicode-aware
        # and NFC-normalized, matching Python-side comparisons (X7-11).
        self.conn.create_function("LOWER", 1, _unicode_lower, deterministic=True)
        # WAL: background watcher writes proceed alongside interactive reads.
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self._transaction_depth = 0
        self.conn.executescript(SCHEMA)
        self._source_cache: dict[tuple[str, str], str] = {}
        self._migrate()
        self.conn.commit()

    def _columns(self, table: str) -> set[str]:
        return {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")}

    def _migrate(self) -> None:
        # Must run first: everything below addresses tables by their new names.
        self._migrate_beliefs_and_laws()
        manuscript_columns = self._columns("manuscripts")
        if "author" not in manuscript_columns:
            self.conn.execute(
                "ALTER TABLE manuscripts ADD COLUMN author TEXT NOT NULL DEFAULT ''"
            )
        if "copyright_owner" not in manuscript_columns:
            self.conn.execute(
                "ALTER TABLE manuscripts ADD COLUMN copyright_owner TEXT NOT NULL DEFAULT ''"
            )
        if "paperback_isbn" not in manuscript_columns:
            self.conn.execute(
                "ALTER TABLE manuscripts ADD COLUMN paperback_isbn TEXT NOT NULL DEFAULT ''"
            )
        if "hardcover_isbn" not in manuscript_columns:
            self.conn.execute(
                "ALTER TABLE manuscripts ADD COLUMN hardcover_isbn TEXT NOT NULL DEFAULT ''"
            )
        if "aliases" not in self._columns("concept_nodes"):
            self.conn.execute(
                "ALTER TABLE concept_nodes ADD COLUMN aliases TEXT NOT NULL DEFAULT '[]'"
            )
        provenance_added = False
        for table in PROVENANCE_TABLES:
            if "source_id" not in self._columns(table):
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN source_id TEXT")
                provenance_added = True
        if "scope" not in self._columns("declared_intents"):
            self.conn.execute("ALTER TABLE declared_intents ADD COLUMN scope TEXT")
        if "status" not in self._columns("essay_summaries"):
            self.conn.execute(
                "ALTER TABLE essay_summaries ADD COLUMN status TEXT "
                "NOT NULL DEFAULT 'current'"
            )
        if provenance_added:
            self._backfill_provenance()
        if "comment_id" in self._columns("doc_threads"):
            self._migrate_thread_origins()

    def _tables(self) -> set[str]:
        return {r[0] for r in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}

    def _migrate_beliefs_and_laws(self) -> None:
        """2026-08-20 rename (docs/domain-vocabulary.md): the two rule tables
        were named for the opposite of what they hold. `editorial_policies`
        is 100% machine-inferred with confidence counters — beliefs;
        `style_elements` is author-ratified and binding — law. SCHEMA has
        already created the new tables empty by the time this runs, so each
        empty shell is dropped and the populated original renamed over it.
        No back-compat: nothing reads the old names after this."""
        tables = self._tables()
        for old, new in (("editorial_policies", "editorial_beliefs"),
                         ("style_elements", "style_laws")):
            if old not in tables:
                continue
            if new in tables:
                if self.conn.execute(
                        f"SELECT 1 FROM {new} LIMIT 1").fetchone() is not None:
                    raise RuntimeError(
                        f"cannot rename {old} → {new}: {new} already holds "
                        "rows; resolve by hand")
                self.conn.execute(f"DROP TABLE {new}")
            self.conn.execute(f"ALTER TABLE {old} RENAME TO {new}")
        if "supports_policy" in self._columns("evidence"):
            self.conn.execute("ALTER TABLE evidence "
                              "RENAME COLUMN supports_policy TO supports_belief")
        # Persisted enum values carrying the old word.
        for table, column, old, new in (
                ("evidence", "evidence_type", "policy_curation",
                 "belief_curation"),
                ("knowledge_proposals", "kind", "policy_revival",
                 "belief_revival"),
                ("guidance_history", "kind", "policy_reminder",
                 "belief_reminder")):
            self.conn.execute(
                f"UPDATE {table} SET {column} = ? WHERE {column} = ?",
                (new, old))

    def _migrate_thread_origins(self) -> None:
        """doc_threads: comment_id → (origin_type, origin_id). Every
        pre-existing thread was born from an author's Drive comment, so
        the migration is mechanical: origin_type='author_comment',
        origin_id=comment_id. SQLite cannot rewrite a UNIQUE constraint in
        place, so the table is rebuilt (design §6.1; no back-compat)."""
        cur = self.conn.execute("SELECT * FROM doc_threads")
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        self.conn.execute("ALTER TABLE doc_threads RENAME TO doc_threads_legacy")
        self.conn.executescript(SCHEMA)  # recreates doc_threads in the new shape
        for r in rows:
            r["origin_type"] = "author_comment"
            r["origin_id"] = r.pop("comment_id")
            names = ", ".join(r)
            marks = ", ".join("?" for _ in r)
            self.conn.execute(
                f"INSERT INTO doc_threads ({names}) VALUES ({marks})",
                list(r.values()))
        self.conn.execute("DROP TABLE doc_threads_legacy")
        self.conn.commit()

    def _backfill_provenance(self) -> None:
        """One-shot provenance for rows that predate the sources registry.
        Concepts/edges the extractor produced belong to the system; the
        system's own pattern-inference evidence likewise; everything else
        was the author's judgment."""
        author = self.source("author")
        system = self.source("system")
        self.conn.execute(
            "UPDATE concept_nodes SET source_id = ? WHERE source_id IS NULL "
            "AND json_extract(metadata, '$.origin') = 'extracted'", (system,))
        self.conn.execute(
            "UPDATE concept_edges SET source_id = ? WHERE source_id IS NULL "
            "AND (status = 'inferred' "
            "     OR json_extract(metadata, '$.origin') = 'extracted')", (system,))
        placeholders = ", ".join("?" for _ in SYSTEM_EVIDENCE_TYPES)
        self.conn.execute(
            f"UPDATE evidence SET source_id = ? WHERE source_id IS NULL "
            f"AND evidence_type IN ({placeholders})",
            (system, *SYSTEM_EVIDENCE_TYPES))
        for table in PROVENANCE_TABLES:
            self.conn.execute(
                f"UPDATE {table} SET source_id = ? WHERE source_id IS NULL",
                (author,))

    def source(self, kind: str, name: str | None = None,
               detail: str | None = None) -> str:
        """Get-or-create a row in the sources registry; returns its id.
        `source_id` records whose judgment originated a knowledge object —
        ratification lives in reviews/evidence, never here."""
        if name is None:
            name = "authorlm" if kind == "system" else getpass.getuser()
        key = (kind, name)
        if key in self._source_cache:
            return self._source_cache[key]
        row = self.one(
            "SELECT id FROM sources WHERE kind = ? AND name = ?", (kind, name))
        if row:
            self._source_cache[key] = row["id"]
            return row["id"]
        fields = ko_fields("src")
        fields.update(kind=kind, name=name, detail=detail)
        self.insert("sources", fields)
        self._source_cache[key] = fields["id"]
        return fields["id"]

    # ------------------------------------------------- timed query surface
    #
    # Every read and write the domain layer issues goes through `_run`, so
    # the always-on performance log (dbperf) sees it with no call-site
    # edits. Row materialization is INSIDE the measured window — a SELECT's
    # cost is mostly `fetchall`, and timing only `execute` would report the
    # cheap half. `finally`, so a query that raises is still measured: a
    # statement that took five seconds and then failed is exactly the one
    # worth seeing.

    def _run(self, sql: str, args, finish) -> Any:
        if self._perf is None:
            return finish(self.conn.execute(sql, args))
        started = time.perf_counter()
        try:
            return finish(self.conn.execute(sql, args))
        finally:
            self._perf.record(sql, time.perf_counter() - started)

    def _commit(self) -> None:
        """The commit is a real, separately-attributable cost (fsync), and
        it is not a statement anyone wrote — hence its own shape."""
        if self._perf is None:
            self.conn.commit()
            return
        started = time.perf_counter()
        try:
            self.conn.commit()
        finally:
            self._perf.record(dbperf.COMMIT_SHAPE,
                              time.perf_counter() - started)

    def insert(self, table: str, row: dict[str, Any]) -> str:
        if table == "evidence" and not row.get("source_id"):
            kind = ("system" if row.get("evidence_type") in SYSTEM_EVIDENCE_TYPES
                    else "author")
            row["source_id"] = self.source(kind)
        cols = ", ".join(row)
        placeholders = ", ".join("?" for _ in row)
        self._run(
            f"INSERT INTO {table} ({cols}) VALUES ({placeholders})",
            list(row.values()), _cursor,
        )
        if not self._transaction_depth:
            self._commit()
        return row["id"]

    def update(self, table: str, obj_id: str, changes: dict[str, Any]) -> None:
        """Evolve a mutable knowledge object, bumping its version."""
        sets = ", ".join(f"{c} = ?" for c in changes)
        self._run(
            f"UPDATE {table} SET {sets}, version = version + 1 WHERE id = ?",
            [*changes.values(), obj_id], _cursor,
        )
        if not self._transaction_depth:
            self._commit()

    @contextmanager
    def transaction(self):
        """Make existing insert/update-based domain verbs atomic as a group."""
        outermost = self._transaction_depth == 0
        if outermost:
            # Timed on purpose: BEGIN IMMEDIATE takes the write lock and can
            # sit on `busy_timeout` behind another process. The body is NOT
            # timed as a unit — that would double-count every query inside it.
            self._run(dbperf.BEGIN_SHAPE, (), _cursor)
        self._transaction_depth += 1
        try:
            yield
        except BaseException:
            self._transaction_depth -= 1
            if outermost:
                self.conn.rollback()
                self._source_cache.clear()
            raise
        else:
            self._transaction_depth -= 1
            if outermost:
                self._commit()

    def one(self, sql: str, args: tuple = ()) -> sqlite3.Row | None:
        return self._run(sql, args, _fetchone)

    def all(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        return self._run(sql, args, _fetchall)


def loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default
