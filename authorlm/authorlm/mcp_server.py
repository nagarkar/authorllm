"""AuthorLM MCP server — the conversational surface (P11: thin wrapper).

Every tool is a logic-free call into `authorlm.api`, returning structured
JSON. Register project-scoped (a `.mcp.json` in the writing workspace) so
only Claude sessions opened there see these tools. Sessions bind lazily:
the first substantive tool call opens an AuthorLM session, stamped with
this connection's id; idle expiry closes it later.

Run manually: authorlm-mcp   (stdio transport)
Workspace override: AUTHORLM_WORKSPACE env var (default: ~/.authorlm).
"""

from __future__ import annotations

import os
import uuid
from typing import Any

from mcp.server.fastmcp import FastMCP

from . import api

mcp = FastMCP(
    "authorlm",
    instructions=(
        "AuthorLM is an editorial collaborator for philosophy writing: it "
        "observes manuscript revisions, maintains a Concept Graph, learns "
        "the author's editorial policies from evidence, and offers explained "
        "guidance. Use these tools whenever the author discusses their "
        "manuscript: declaring what they want to write (declare_intent), "
        "asking what to write or why (get_guidance, get_briefing), reacting "
        "to suggestions (review_suggestion — record their reasons verbatim, "
        "explanations are the highest-value evidence), curating concepts and "
        "relationships conversationally, or finishing a piece of work "
        "(complete_intent, close_session). Prefer recording the author's "
        "actual words as explanations. Never edit conceptual manuscript "
        "content without being asked."
    ),
)

CONNECTION_ID = f"mcp-{uuid.uuid4().hex[:12]}"
_WORKSPACE = os.environ.get("AUTHORLM_WORKSPACE")


def _db():
    return api.open_db(_WORKSPACE)


def _manuscript(db, name: str | None) -> dict:
    return api.get_manuscript(db, name)


def _llm():
    return api.make_llm(_WORKSPACE)


def _guard(fn) -> dict[str, Any]:
    try:
        return {"ok": True, "result": fn()}
    except (LookupError, ValueError) as err:
        return {"ok": False, "error": str(err)}


@mcp.tool()
def list_manuscripts() -> dict:
    """List every registered manuscript (name and directory). Use this to
    discover what exists or to resolve ambiguity before other calls."""
    return _guard(lambda: api.list_manuscripts(_db()))


@mcp.tool()
def resolve_file(path: str) -> dict:
    """Find which registered manuscript a file path or bare filename (e.g.
    'preface.md') belongs to. Use when the author names a file."""
    def run():
        found = api.resolve_file(_db(), path)
        if not found:
            raise LookupError(f"'{path}' is not inside any registered manuscript")
        return found
    return _guard(run)


@mcp.tool()
def get_status(manuscript: str | None = None) -> dict:
    """Current state of a manuscript: active session, and counts of
    versions, concepts, policies, proposals, evidence."""
    def run():
        db = _db()
        return api.status(db, _manuscript(db, manuscript))
    return _guard(run)


@mcp.tool()
def declare_intent(statement: str, manuscript: str | None = None) -> dict:
    """Declare the author's current writing objective (e.g. 'Introduce
    gravity'). Opens a session lazily if none is active. Returns a
    deterministic preview: concepts the intent matches (with their state,
    relationships, and precedents) or did-you-mean suggestions."""
    def run():
        db = _db()
        ms = _manuscript(db, manuscript)
        session, created = api.ensure_session(db, ms, client_id=CONNECTION_ID)
        result = api.declare_intent(db, ms, statement)
        result["session_opened"] = created
        return result
    return _guard(run)


@mcp.tool()
def list_intents(manuscript: str | None = None) -> dict:
    """All declared intents with status (active intents are the author's
    open todos; completed/abandoned are history)."""
    def run():
        db = _db()
        return api.list_intents(db, _manuscript(db, manuscript))
    return _guard(run)


@mcp.tool()
def complete_intent(intent_id: str, outcome: str | None = None,
                    manuscript: str | None = None) -> dict:
    """Mark a writing objective achieved. Triggers episode analysis: the
    LLM infers the editorial decisions the author's edits show and seeds
    candidate policies from recurring patterns. Relay the analysis back."""
    def run():
        db = _db()
        return api.complete_intent(db, _manuscript(db, manuscript), intent_id,
                                   outcome, llm=_llm())
    return _guard(run)


@mcp.tool()
def abandon_intent(intent_id: str, reason: str | None = None,
                   manuscript: str | None = None) -> dict:
    """Drop a writing objective without achieving it. The declaration stays
    in history; it stops driving guidance."""
    def run():
        db = _db()
        return api.abandon_intent(db, _manuscript(db, manuscript), intent_id, reason)
    return _guard(run)


@mcp.tool()
def collect_revision(manuscript: str | None = None) -> dict:
    """Snapshot the manuscript now: detects transitions, realizes concepts,
    infers co-occurrence edges, and reports the prerequisite-gap delta.
    Call after the author says they saved/finished edits."""
    def run():
        db = _db()
        ms = _manuscript(db, manuscript)
        api.ensure_session(db, ms, client_id=CONNECTION_ID)
        return api.collect(db, ms, api.load_config(_WORKSPACE))
    return _guard(run)


@mcp.tool()
def get_guidance(manuscript: str | None = None) -> dict:
    """Generate explained editorial suggestions for the active intents:
    concept introductions (with drafted bridge text and precedents from the
    author's own past episodes), prerequisite gaps, unanswered objections,
    policy reminders. May abstain — abstention is a valid answer."""
    def run():
        db = _db()
        ms = _manuscript(db, manuscript)
        session, _ = api.ensure_session(db, ms, client_id=CONNECTION_ID)
        return api.guide(db, ms, session, llm=_llm())
    return _guard(run)


@mcp.tool()
def review_suggestion(index: int, decision: str,
                      explanation: str | None = None,
                      manuscript: str | None = None) -> dict:
    """Record the author's verdict on suggestion [index] from the latest
    guidance batch. decision: accepted | rejected | modified | deferred.
    ALWAYS pass the author's own reasoning as `explanation` when they give
    any — explained decisions are the highest-value evidence and can seed
    editorial policies."""
    def run():
        db = _db()
        ms = _manuscript(db, manuscript)
        session, _ = api.ensure_session(db, ms, client_id=CONNECTION_ID)
        return api.review(db, ms, session, index, decision, explanation, llm=_llm())
    return _guard(run)


@mcp.tool()
def get_briefing(manuscript: str | None = None) -> dict:
    """The session-opening learning briefing: policies strengthened or
    weakened, newly realized concepts, unconfirmed extractions, open
    proposals, inferred relationships awaiting confirmation, contradictions,
    outstanding questions, suggested focus areas, learning velocity."""
    def run():
        db = _db()
        return api.get_briefing(db, _manuscript(db, manuscript))
    return _guard(run)


@mcp.tool()
def get_concepts(manuscript: str | None = None, name: str | None = None,
                 include_all: bool = False) -> dict:
    """The Concept Graph. With `name`, full detail for one concept (notes
    untruncated, every live edge). include_all adds retired/rejected."""
    def run():
        db = _db()
        ms = _manuscript(db, manuscript)
        if name:
            return api.show_concept(db, ms, name)
        return api.list_concepts(db, ms, include_all=include_all)
    return _guard(run)


@mcp.tool()
def add_concept(name: str, kind: str = "concept", notes: str | None = None,
                manuscript: str | None = None) -> dict:
    """Add (or revive) a concept the author declares. kind: concept |
    definition | objection | example | metaphor | question |
    historical_reference | mathematical_construct."""
    def run():
        db = _db()
        return api.add_concept(db, _manuscript(db, manuscript), name, kind, notes)
    return _guard(run)


@mcp.tool()
def link_concepts(from_name: str, relation: str, to_name: str,
                  manuscript: str | None = None) -> dict:
    """Record a relationship the author asserts (A relation B). Relations:
    depends_on, permits, creates, defines, elaborates, specializes,
    generalizes, contrasts_with, answers, motivates, foreshadows,
    illustrates, distinguishes."""
    def run():
        db = _db()
        return api.link_concepts(db, _manuscript(db, manuscript),
                                 from_name, relation, to_name)
    return _guard(run)


@mcp.tool()
def confirm_concept(name: str, kind: str | None = None,
                    manuscript: str | None = None) -> dict:
    """Confirm an extracted concept as genuinely load-bearing (optionally
    retyping its kind). Use when the author validates a concept in
    conversation — this is triage, recorded as evidence."""
    def run():
        db = _db()
        return api.confirm_concept(db, _manuscript(db, manuscript), name, kind)
    return _guard(run)


@mcp.tool()
def retire_concept(name: str, manuscript: str | None = None) -> dict:
    """Retire a concept (and its edges) the author rejects. Kept for
    history; extraction is banned from re-adding it."""
    def run():
        db = _db()
        return api.retire_concept(db, _manuscript(db, manuscript), name)
    return _guard(run)


@mcp.tool()
def confirm_edge(edge_id: str, relation: str | None = None,
                 manuscript: str | None = None) -> dict:
    """Confirm an inferred relationship (optionally correcting the
    relation). Use when the author agrees a relationship holds."""
    def run():
        db = _db()
        return api.confirm_edge(db, _manuscript(db, manuscript), edge_id, relation)
    return _guard(run)


@mcp.tool()
def reject_edge(edge_id: str, manuscript: str | None = None) -> dict:
    """Reject an inferred relationship the author disagrees with."""
    def run():
        db = _db()
        return api.reject_edge(db, _manuscript(db, manuscript), edge_id)
    return _guard(run)


@mcp.tool()
def list_proposals(manuscript: str | None = None) -> dict:
    """Open proposals against settled knowledge: reframed definitions,
    retired concepts recurring, rejected relationships argued again,
    near-duplicate names of retired concepts. Surface these to the author."""
    def run():
        db = _db()
        return api.list_proposals(db, _manuscript(db, manuscript))
    return _guard(run)


@mcp.tool()
def resolve_proposal(proposal_id: str, action: str, reason: str | None = None,
                     manuscript: str | None = None) -> dict:
    """Settle a proposal per the author's decision. action: accept |
    dismiss. Pass their reasoning as `reason` when dismissing."""
    def run():
        db = _db()
        return api.resolve_proposal(db, _manuscript(db, manuscript),
                                    proposal_id, action, reason)
    return _guard(run)


@mcp.tool()
def analyze_episodes(manuscript: str | None = None) -> dict:
    """Backfill: run episode analysis over closed, unanalyzed episodes —
    inferring editorial decisions from the author's actual edits."""
    def run():
        db = _db()
        return api.analyze(db, _manuscript(db, manuscript), _llm())
    return _guard(run)


@mcp.tool()
def diff_versions(older: int | None = None, newer: int | None = None,
                  file: str | None = None, manuscript: str | None = None) -> dict:
    """Unified diff between collected manuscript versions (default: the
    last two). Use when the author asks what changed."""
    def run():
        db = _db()
        return api.diff_versions(db, _manuscript(db, manuscript), older, newer, file)
    return _guard(run)


@mcp.tool()
def list_policies(manuscript: str | None = None) -> dict:
    """Learned editorial policies with confidence, status, support counts,
    and outstanding questions."""
    def run():
        db = _db()
        return api.list_policies(db, _manuscript(db, manuscript))
    return _guard(run)


@mcp.tool()
def get_doc_links(manuscript: str | None = None) -> dict:
    """Google Docs link state for a manuscript's files: which files have a
    linked Doc (with URL) and which are currently checked out — i.e. the
    Doc, not the local file, is the working copy. Use for questions like
    'is anything checked out to Google Docs?'. Read-only; the actual
    push/pull commands are CLI-only (see the authorlm skill)."""
    def run():
        db = _db()
        return api.get_doc_links(db, _manuscript(db, manuscript))
    return _guard(run)


@mcp.tool()
def get_plan(draft: bool = False, manuscript: str | None = None) -> dict:
    """The writing plan: for every unrealized concept, where it belongs in
    the manuscript (derived from realized graph neighbors and the TOC
    reading order), which prerequisites to write first, matching active
    intents, and precedents. With draft=true, opening stubs are written to
    _drafts/ for the author to pull in. Use when the author asks 'what
    should I write next?' or 'where does X belong?'."""
    def run():
        db = _db()
        ms = _manuscript(db, manuscript)
        return api.get_plan(db, ms, llm=_llm(), draft=draft)
    return _guard(run)


@mcp.tool()
def close_session(manuscript: str | None = None) -> dict:
    """End the active writing session: closes episodes, runs episode
    analysis, and returns the learning-velocity summary. Use when the
    author says they're done for now."""
    def run():
        db = _db()
        ms = _manuscript(db, manuscript)
        session = api.close_session(db, ms)
        analysis = api.analyze(db, ms, _llm())
        briefing = api.get_briefing(db, ms, since=session["started_at"])
        return {
            "session": session,
            "analysis": analysis,
            "learning_velocity": briefing["learning_velocity"],
        }
    return _guard(run)


@mcp.tool()
def extract_concepts(files: list[str] | None = None, full: bool = False,
                     edges_only: bool = False,
                     manuscript: str | None = None) -> dict:
    """LLM-mine the manuscript for concepts and relationships (incremental
    by default: only files changed since the last extraction). New items
    arrive as hypotheses for the author to triage."""
    def run():
        db = _db()
        return api.run_extraction(db, _manuscript(db, manuscript), _llm(),
                                  files=files, full=full, edges_only=edges_only)
    return _guard(run)


@mcp.tool()
def file_improvement(title: str, evidence: str, given: str, observed: str,
                     expected: str, manuscript: str | None = None) -> dict:
    """Record a confirmed AuthorLM defect as an improvement task. File this
    the moment the author confirms a tool behavior is wrong (e.g. a
    prerequisite-gap false positive) — never before their confirmation.
    `evidence` carries the trail with file:line specifics; the repro is
    structured prose: `given` (input/state), `observed` (wrong behavior),
    `expected` (the author's verdict, verbatim where possible). Defects
    only — feature ideas go through the normal design workflow."""
    def run():
        db = _db()
        ms = _manuscript(db, manuscript) if manuscript else None
        return api.file_improvement(db, title=title, evidence=evidence,
                                    given=given, observed=observed,
                                    expected=expected, manuscript=ms)
    return _guard(run)


@mcp.tool()
def list_improvements(status: str | None = None) -> dict:
    """List AuthorLM self-improvement tasks (tool defects, not manuscript
    knowledge). status filters: open | in_progress | proposed | resolved |
    dismissed | active (any unfinished). Includes each task's fix bundle
    availability via improvement_bundle."""
    def run():
        return api.list_improvements(_db(), status)
    return _guard(run)


@mcp.tool()
def improvement_bundle(task_id: str) -> dict:
    """The self-contained fix prompt for one improvement task (evidence +
    repro + repo conventions) — hand it to a coding agent. Marks an open
    task in_progress."""
    def run():
        return api.improvement_bundle(_db(), task_id)
    return _guard(run)


@mcp.tool()
def resolve_improvement(task_id: str, action: str,
                        note: str | None = None) -> dict:
    """Settle an improvement task. action: 'propose' (fixing agent records
    fix summary + name of the encoded test as `note`), 'close' (only after
    the author confirms the resolution in conversation), 'dismiss' (author
    declined; `note` = their reason, mandatory, recorded as evidence)."""
    def run():
        return api.resolve_improvement(_db(), task_id, action, note)
    return _guard(run)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
