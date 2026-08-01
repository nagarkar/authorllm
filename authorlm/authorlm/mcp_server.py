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
        "content without being asked. When the author asks you to DRAFT "
        "prose, assemble the machinery first: get_style(file) — the "
        "rendered effective guide is law (register, lexicon, syntax, "
        "tone); get_concepts for every concept the passage touches (the "
        "notes are the author's ratified definitions — use their words, "
        "keep claims consistent with the graph, never endorse a refuted "
        "position); and the validated policies from list_policies or the "
        "briefing. Offer the draft for the author to place or reject."
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
    runs incremental extraction (concepts, links, aliasing statements) when
    an LLM is configured, and reports the prerequisite-gap delta. Call
    after the author says they saved/finished edits."""
    def run():
        db = _db()
        ms = _manuscript(db, manuscript)
        api.ensure_session(db, ms, client_id=CONNECTION_ID)
        return api.collect(db, ms, api.load_config(_WORKSPACE), analyze=True)
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
        return api.guide(db, ms, session, llm=_llm(),
                         config=api.load_config(_WORKSPACE))
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
        return api.get_briefing(db, _manuscript(db, manuscript),
                                config=api.load_config(_WORKSPACE))
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
    historical_reference | mathematical_construct | syllogism (a syllogism
    node's premises attach with depends_on, its conclusion with leads_to).
    On an existing concept, fresh notes refine the stored notes."""
    def run():
        db = _db()
        return api.add_concept(db, _manuscript(db, manuscript), name, kind, notes)
    return _guard(run)


@mcp.tool()
def alias_concept(name: str, aliases: list[str], remove: bool = False,
                  manuscript: str | None = None) -> dict:
    """Declare alternate names (synonyms) for a concept — or withdraw them
    with remove=true. Aliases resolve in every name lookup and count as
    mentions of the concept in text scans — use this for synonym rings
    instead of separate nodes joined by mutual defines edges."""
    def run():
        db = _db()
        return api.alias_concept(db, _manuscript(db, manuscript), name,
                                 aliases, remove=remove)
    return _guard(run)


@mcp.tool()
def get_style(file: str | None = None, manuscript: str | None = None) -> dict:
    """The style system. With `file`, the effective style guide governing
    that file (composed from its guide chain, nearest scope first, override
    links applied) plus a rendered form for drafting. Without `file`, an
    overview: every guide, its parent, element count, and file attachments."""
    def run():
        db = _db()
        ms = _manuscript(db, manuscript)
        if file:
            return api.style_show(db, ms, file)
        return api.style_overview(db, ms)
    return _guard(run)


@mcp.tool()
def define_style_guide(name: str, parent: str | None = None,
                       manuscript: str | None = None) -> dict:
    """Create a named style guide. Guides form a single-parent tree; the
    parentless guide is the manuscript root (house style). Files attach to
    exactly one guide via attach_style; unattached files follow the root."""
    def run():
        db = _db()
        return api.define_style_guide(db, _manuscript(db, manuscript),
                                      name, parent=parent)
    return _guard(run)


@mcp.tool()
def attach_style(file: str, guide: str, manuscript: str | None = None) -> dict:
    """Attach a manuscript file to a style guide (re-attach to switch
    styles — one operation, no element copying)."""
    def run():
        db = _db()
        return api.attach_style(db, _manuscript(db, manuscript), file, guide)
    return _guard(run)


@mcp.tool()
def add_style_element(aspect: str, statement: str, guide: str | None = None,
                      file: str | None = None, notes: str | None = None,
                      overrides: str | None = None,
                      manuscript: str | None = None) -> dict:
    """Record a ratified style rule. aspect: register | lexicon | syntax |
    structure | formatting | citation | rhetoric | figure | tone. Scope to
    exactly one of `guide` (name) or `file` (file-local override). `notes`
    holds free-text inspect/avoid hints; `overrides` names the id (prefix)
    of an inherited element this one displaces."""
    def run():
        db = _db()
        return api.add_style_element(
            db, _manuscript(db, manuscript), aspect, statement,
            guide_name=guide, file=file, notes=notes, overrides=overrides)
    return _guard(run)


@mcp.tool()
def retire_style_element(element_id: str, manuscript: str | None = None) -> dict:
    """Retire a style element by id (prefix) — it leaves every composition
    but stays historically accessible."""
    def run():
        db = _db()
        return api.retire_style_element(db, _manuscript(db, manuscript),
                                        element_id)
    return _guard(run)


@mcp.tool()
def merge_concepts(canonical: str, duplicate: str,
                   manuscript: str | None = None) -> dict:
    """Merge two concepts the author declares to be the same idea: the
    duplicate's live edges are re-pointed at the canonical concept
    (self-edges and already-present edges retire), the duplicate node
    retires, and its name and aliases become aliases of the canonical."""
    def run():
        db = _db()
        return api.merge_concepts(db, _manuscript(db, manuscript),
                                  canonical, duplicate)
    return _guard(run)


@mcp.tool()
def link_concepts(from_name: str, relation: str, to_name: str,
                  manuscript: str | None = None) -> dict:
    """Record a relationship the author asserts (A relation B). Relations:
    depends_on, permits, creates, leads_to, defines, elaborates,
    specializes, generalizes, contrasts_with, answers, motivates,
    foreshadows, illustrates, distinguishes, refutes. Use leads_to for
    causal consequence (ineffectiveness leads_to regret); reserve creates
    for ontological production (the Field of Choice creates distinctions);
    use refutes when A argues against B — a position the text repudiates —
    and contrasts_with only when both sides are commitments of the work."""
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
    """Settle a proposal per the author's decision. action: accept | edge |
    dismiss. `edge` applies to alias proposals only: the sentence names a
    kind rather than an identity, so record 'canonical generalizes alias'
    instead of merging. Pass their reasoning as `reason` when dismissing."""
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
def retire_policy(prefix: str, reason: str, manuscript: str | None = None) -> dict:
    """Retire an editorial policy the author rejects (author-initiated
    curation). Kept for history; the statement is banned from re-seeding —
    fresh supporting evidence files a revival proposal instead. Record the
    author's reason verbatim."""
    def run():
        db = _db()
        return api.retire_policy(db, _manuscript(db, manuscript), prefix, reason)
    return _guard(run)


@mcp.tool()
def merge_policies(duplicate: str, canonical: str,
                   reason: str | None = None,
                   manuscript: str | None = None) -> dict:
    """Fold a duplicate policy's belief record (support counts, questions)
    into the canonical policy and retire the duplicate. Use when two
    learned policies state the same rule in different words."""
    def run():
        db = _db()
        return api.merge_policies(db, _manuscript(db, manuscript),
                                  duplicate, canonical, reason)
    return _guard(run)


@mcp.tool()
def convert_policy_to_style(prefix: str, aspect: str,
                            statement: str | None = None,
                            guide: str | None = None,
                            file: str | None = None,
                            notes: str | None = None,
                            reason: str | None = None,
                            manuscript: str | None = None) -> dict:
    """Convert a learned policy into a ratified style element: creates the
    element (in `guide`, or file-local via `file`; statement defaults to
    the policy's) and retires the policy with a recorded linkage. Use when
    a policy is really a timeless how-prose-reads rule, not a revision
    decision."""
    def run():
        db = _db()
        return api.convert_policy(db, _manuscript(db, manuscript), prefix,
                                  aspect, statement=statement, guide=guide,
                                  file=file, notes=notes, reason=reason)
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
                     edges_only: bool = False, aliases_only: bool = False,
                     manuscript: str | None = None) -> dict:
    """LLM-mine the manuscript for concepts and relationships (incremental
    by default: only files changed since the last extraction). New items
    arrive as hypotheses for the author to triage. aliases_only sweeps the
    full text for aliasing statements (naming/defining sentences) and files
    merge proposals; it extracts nothing else."""
    def run():
        db = _db()
        return api.run_extraction(db, _manuscript(db, manuscript), _llm(),
                                  files=files, full=full,
                                  edges_only=edges_only,
                                  aliases_only=aliases_only)
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
