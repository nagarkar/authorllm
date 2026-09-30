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
from typing import Any

from mcp.server.fastmcp import FastMCP

from . import api
from . import clients
from . import critique
from . import triage_transport
from .db import loads as _loads

TRIAGE_APP_URI = "ui://authorlm/triage-app.html"

mcp = FastMCP(
    "authorlm",
    instructions=(
        "AuthorLM is an editorial collaborator for philosophy writing: it "
        "observes manuscript revisions, maintains a Concept Graph, learns "
        "the author's editorial beliefs from evidence, and offers explained "
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
        "tone); get_concepts SCOPED — file=<essay> for the slice realized "
        "in the target essay, then name= for full ratified notes of the "
        "concepts the passage touches (unscoped returns only a summary; "
        "the graph is too large to list whole). The notes are the "
        "author's ratified definitions — use their words, keep claims "
        "consistent with the graph, never endorse a refuted position. "
        "Check the validated beliefs from list_beliefs or the briefing. "
        "Offer the draft for the author to place or reject. For "
        "naturally-plural curation ('retire these two and their edges') "
        "use curate_concepts with an operations array; suggestion "
        "verdicts stay on review_suggestion one at a time — their "
        "explanations are the evidence stream."
    ),
)

# Unchanged in shape (`mcp-<uuid12>`), but owned by `clients` now, so
# the `mcp-stdio` adapter can supply it through the normal resolution
# path instead of `ensure_session` taking it as an argument.
CONNECTION_ID = clients.CONNECTION_ID
_WORKSPACE = os.environ.get("AUTHORLM_WORKSPACE")


def _db():
    return api.open_db(_WORKSPACE)


def _manuscript(db, name: str | None) -> dict:
    return api.get_manuscript(db, name)


def _llm():
    return api.make_llm(_WORKSPACE)


def _guard(fn) -> dict[str, Any]:
    import sys
    import time

    from . import dbperf, tracelog, usage

    # The calling @mcp.tool() function's name is the verb being traced.
    verb = sys._getframe(1).f_code.co_name
    # Resolve the client ONCE for this tool call, and tell `clients` which
    # verb it is, so the writeup touched-by trail can name it without
    # sniffing the call stack.
    clients.configure(surface="mcp", workspace=_WORKSPACE, verb=verb)
    t0 = time.monotonic()
    try:
        result = {"ok": True, "result": fn()}
        error = None
    except (LookupError, ValueError, RuntimeError) as err:
        result = {"ok": False, "error": str(err)}
        error = f"{type(err).__name__}: {err}"
    except BaseException as err:
        tracelog.record(verb, surface="mcp", workspace=_WORKSPACE,
                        duration_ms=int((time.monotonic() - t0) * 1000),
                        ok=False, error=f"{type(err).__name__}: {err}")
        dbperf.flush(verb)
        usage.flush(verb)
        raise
    tracelog.record(verb, surface="mcp", workspace=_WORKSPACE,
                    duration_ms=int((time.monotonic() - t0) * 1000),
                    ok=error is None, error=error)
    # This server is long-lived, so the perf log's unit of aggregation is
    # the DISPATCH, not the process: one line per tool call, named by it.
    dbperf.flush(verb)
    usage.flush(verb)
    # An MCP-heavy chat dispatches hundreds of tool calls, which is
    # exactly why the sweep is interval-gated rather than run here
    # unconditionally: at most one chat line per session per five minutes.
    usage.sweep_opportunistic(_WORKSPACE)
    return result


@mcp.tool()
def list_manuscripts() -> dict:
    """List every registered manuscript with its publication identity and
    directory. Use this to discover what exists or resolve ambiguity."""
    return _guard(lambda: api.list_manuscripts(_db()))


@mcp.tool()
def get_manuscript_metadata(manuscript: str | None = None) -> dict:
    """Get canonical author, copyright owner, paperback ISBN, and hardcover
    ISBN metadata for the selected manuscript."""
    def run():
        db = _db()
        return api.manuscript_metadata(_manuscript(db, manuscript))
    return _guard(run)


@mcp.tool()
def set_manuscript_metadata(author: str | None = None,
                            copyright_owner: str | None = None,
                            paperback_isbn: str | None = None,
                            hardcover_isbn: str | None = None,
                            trim_size: str | None = None,
                            bleed: bool | None = None,
                            manuscript: str | None = None) -> dict:
    """Set canonical publication identity: author, copyright owner,
    format-specific print ISBN-13 values, and the print geometry — trim
    size as WIDTHxHEIGHT inches (e.g. '6x9') and whether the interior
    bleeds. ISBNs are validated and normalized; the trim size is checked
    against the print range."""
    def run():
        db = _db()
        selected = _manuscript(db, manuscript)
        return api.update_manuscript_metadata(
            db, selected, author=author, copyright_owner=copyright_owner,
            paperback_isbn=paperback_isbn,
            hardcover_isbn=hardcover_isbn,
            trim_size=trim_size, bleed=bleed)
    return _guard(run)


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
    versions, concepts, beliefs, proposals, evidence."""
    def run():
        db = _db()
        return api.status(db, _manuscript(db, manuscript))
    return _guard(run)


@mcp.tool()
def declare_intent(statement: str, scope: str | None = None,
                   book_wide: bool = False,
                   manuscript: str | None = None) -> dict:
    """Declare the author's current writing objective (e.g. 'Introduce
    gravity'). Opens a session lazily if none is active. Returns a
    deterministic preview: concepts the intent matches (with their state,
    relationships, and precedents) or did-you-mean suggestions.

    `scope` is WHERE the goal applies: an essay's filename, or a toc part
    opener (which covers every essay beneath it). Leave it out only for a
    goal that really is book-wide — an unscoped intent attaches to EVERY
    future writeup. When the author has not said, ask one line ("just
    this essay, the whole part, or the whole book?"); never default
    silently.

    When they answer "the whole book", pass `book_wide=True` rather than
    simply omitting `scope`. Both leave the scope empty, but the flag
    records the answer as a RULING — without it the goal reappears on the
    author's scope-triage sheet as one they have never placed, and the
    sitting they just finished refills. Omit it only when they genuinely
    did not say."""
    def run():
        db = _db()
        ms = _manuscript(db, manuscript)
        session, created = api.ensure_session(db, ms)
        result = api.declare_intent(db, ms, statement, scope=scope,
                                    book_wide=book_wide)
        result["session_opened"] = created
        result["scope_means"] = (
            f"every rewrite of {result['intent']['scope']} (and of anything "
            f"beneath it) serves this goal"
            if result["intent"]["scope"] else
            "book-wide: EVERY writeup, on every essay, will carry this goal")
        return result
    return _guard(run)


@mcp.tool()
def scope_intent(intent_id: str, scope: str | None = None,
                 chapter: str | None = None, manuscript_wide: bool = False,
                 manuscript: str | None = None) -> dict:
    """(Re)place an existing intent: exactly one of `scope` (this essay),
    `chapter` (a toc opener, covering every essay beneath it), or
    `manuscript_wide`. Metadata-forward — episodes keep their
    transitions and any writeup that already ratified the intent keeps
    the tier as it stood. This is the verb the one-time scope triage runs,
    one ruling at a time."""
    def run():
        db = _db()
        return api.scope_intent(db, _manuscript(db, manuscript), intent_id,
                                scope=scope, chapter=chapter,
                                manuscript_wide=manuscript_wide)
    return _guard(run)


@mcp.tool()
def list_intents(manuscript: str | None = None,
                 evidence: bool = False) -> dict:
    """All declared intents with status (active intents are the author's
    open todos; completed/abandoned are history).

    With `evidence=True` this is the scope triage's read: for every
    active intent with no scope, the files its episodes ACTUALLY touched,
    the writeups bound to it, and a deterministic suggested tier with the
    reason in words. Zero model calls. Present them one at a time, oldest
    first, in the author's terms — never as a menu."""
    def run():
        db = _db()
        ms = _manuscript(db, manuscript)
        if evidence:
            return {"intents": api.list_intents(db, ms),
                    "unscoped_evidence": api.scope_evidence(db, ms),
                    # The sitting's closing numbers. `ruled_book_wide` is
                    # the one to report back: those are the goals every
                    # future writeup carries by the author's decision.
                    "scope_tally": api.scope_tally(db, ms)}
        return api.list_intents(db, ms)
    return _guard(run)


@mcp.tool()
def complete_intent(intent_id: str, outcome: str | None = None,
                    manuscript: str | None = None) -> dict:
    """Mark a writing objective achieved. Triggers episode analysis: the
    LLM infers the editorial decisions the author's edits show and seeds
    candidate beliefs from recurring patterns. Relay the analysis back."""
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
def import_critique(manifest_path: str, manuscript: str | None = None) -> dict:
    """Load an external critique manifest (JSON): a critic's report parsed
    into items, each classified as intent or style_element with unit,
    ordinal, text, and scope. Everything lands as PROPOSED with critic
    provenance — nothing becomes law or a work order until the author
    triages. Idempotent per (source, unit, ordinal), so a corrected
    manifest re-runs safely. Build the manifest conversationally first
    (parse the report, map units to essay files, classify items), show
    the author the per-unit accounting, then import."""
    def run():
        import json as _json
        from pathlib import Path as _Path

        db = _db()
        ms = _manuscript(db, manuscript)
        manifest = _json.loads(_Path(manifest_path).read_text())
        return critique.import_manifest(db, ms["id"], manifest)
    return _guard(run)


@mcp.tool()
def critique_status(manuscript: str | None = None) -> dict:
    """Per-critic tallies of imported critique items: proposed (awaiting
    the author's verdict), accepted, rejected — intents and style
    elements separately. Use to see whether triage homework remains."""
    def run():
        db = _db()
        return {"critics": critique.status(db, _manuscript(db, manuscript)["id"])}
    return _guard(run)


@mcp.tool()
def list_critique_items(scope: str | None = None,
                        manuscript: str | None = None) -> dict:
    """Pending (proposed) critique items in canonical order, numbered.
    scope: an essay file (e.g. 'recapitulation.md') for that essay's
    just-in-time sitting; 'manuscript' for manuscript-wide items only
    (the global sitting); omit for everything. Numbers are positional
    and go stale after verdicts land — re-list before another numbered
    batch; ids never go stale. Present items to the author for verdicts;
    record them with triage_critique."""
    def run():
        db = _db()
        ms = _manuscript(db, manuscript)
        from .db import loads as _loads

        items = []
        for n, (kind, item) in enumerate(critique.queue(db, ms["id"], scope), 1):
            meta = _loads(item["metadata"], {}).get("critique", {})
            items.append({
                "n": n, "id": item["id"], "kind": kind,
                "unit": meta.get("unit"), "ordinal": meta.get("ordinal"),
                "scope": item.get("scope") or item.get("file") or
                ("guide" if item.get("guide_id") else "manuscript"),
                "statement": item["statement"],
            })
        return {"count": len(items), "scope": scope, "items": items}
    return _guard(run)


@mcp.tool()
def list_critique_decisions(scope: str | None = None,
                            verdict: str | None = None,
                            query: str | None = None,
                            limit: int = 50,
                            manuscript: str | None = None) -> dict:
    """Critique items the author has ALREADY answered, with the verdict
    and the reason recorded at the time — "what did I decide about this,
    and why?" without opening the database. The counterpart of
    list_critique_items (which shows only what is still pending).
    scope: an essay file, or 'manuscript' for manuscript-wide items;
    verdict: 'accept' | 'reject' | 'revise'; query: matches the
    statement, the reason, or the critic's original wording. `tally`
    counts the FULL filtered set even when `items` is capped by `limit`
    — a manuscript can carry hundreds of settled rejections, so narrow
    with scope/verdict/query rather than raising the limit. A 'revise'
    entry carries revised_from: the critic's wording before the author's
    replaced it."""
    def run():
        db = _db()
        ms = _manuscript(db, manuscript)
        rows = critique.decided(db, ms["id"], scope=scope, verdict=verdict,
                                query=query, manuscript=ms)
        items = []
        for d in rows[:max(0, limit)]:
            item, meta = d["item"], _loads(d["item"]["metadata"], {})
            meta = meta.get("critique", {})
            items.append({
                "id": item["id"], "kind": d["kind"],
                "unit": meta.get("unit"), "ordinal": meta.get("ordinal"),
                "scope": item.get("scope") or item.get("file") or
                ("guide" if item.get("guide_id") else "manuscript"),
                "verdict": d["verdict"], "statement": item["statement"],
                "reason": d["reason"], "revised_from": d["revised_from"],
            })
        return {"count": len(rows), "tally": critique.tally(rows),
                "scope": scope, "verdict": verdict,
                "truncated": len(items) < len(rows), "items": items}
    return _guard(run)


@mcp.tool()
def triage_critique(operations: list[dict], scope: str | None = None,
                    manuscript: str | None = None) -> dict:
    """Record the author's verdicts on proposed critique items, in batch.
    Each operation: {"item": "<id prefix or list number>", "verdict":
    "accept" | "reject" | "revise", "reason": "..." (reject: required —
    the author's words, verbatim; they are the evidence), "text": "..."
    (revise: required — the author's replacement wording; provenance
    flips to author, the critic's original is kept as lineage)}.
    Numbers resolve against ONE snapshot of the pending queue taken at
    call start, in list_critique_items order with the same scope — pass
    the same `scope` the numbers came from. One failed operation never
    blocks the rest."""
    def run():
        db = _db()
        ms = _manuscript(db, manuscript)
        mid = ms["id"]
        snapshot = critique.queue(db, mid, scope)
        results = []
        for op in operations:
            token = str(op.get("item", "")).strip()
            verdict = op.get("verdict")
            entry = {"item": token, "verdict": verdict}
            try:
                if verdict not in ("accept", "reject", "revise"):
                    raise ValueError(f"unknown verdict '{verdict}'")
                if token.isdigit():
                    n = int(token)
                    if not 1 <= n <= len(snapshot):
                        raise LookupError(
                            f"no item {n} — the pending list has "
                            f"{len(snapshot)} item(s) for scope={scope!r}")
                    kind, item = snapshot[n - 1]
                else:
                    kind, item = _critique_by_prefix(db, mid, token)
                if verdict == "reject" and not op.get("reason"):
                    raise ValueError("reject requires the author's verbatim "
                                     "reason")
                if verdict == "revise" and not op.get("text"):
                    raise ValueError("revise requires the author's "
                                     "replacement text")
                fns = {("intent", "accept"): critique.accept_intent,
                       ("intent", "reject"): critique.reject_intent,
                       ("intent", "revise"): critique.revise_intent,
                       ("element", "accept"): critique.accept_element,
                       ("element", "reject"): critique.reject_element,
                       ("element", "revise"): critique.revise_element}
                fn = fns[(kind, verdict)]
                if verdict == "accept":
                    fn(db, mid, item)
                elif verdict == "reject":
                    fn(db, mid, item, op["reason"])
                else:
                    fn(db, mid, item, op["text"])
                entry.update(ok=True, id=item["id"],
                             statement=item["statement"][:100])
            except (LookupError, ValueError, KeyError) as err:
                entry.update(ok=False, error=str(err))
            results.append(entry)
        return {"results": results,
                "still_proposed": len(critique.queue(db, mid, scope))}
    return _guard(run)


@mcp.tool()
def add_filter(name: str, prompt: str, manuscript: str | None = None) -> dict:
    """Ratify a new FILTER artifact (docs/filter-pass-design.md). `prompt`
    is the WHOLE artifact — TOML front matter (`class` = "sequential" |
    "global", required; optional one-line `state` note) then a `---`
    line then the prompt body — exactly what `filter add <name>` reads
    from stdin in the shell. `name` is a short kebab-case slug.

    A filter is ratified law, the style-law sibling: record one only
    AFTER the author has read and approved this exact text. Do not
    paraphrase or author a filter's prompt yourself and install it
    silently — a filter they have not read is a filter they cannot rule
    on. Bad name, empty prompt, missing/unknown class, or malformed TOML
    front matter is refused through the normal {ok:false, error}
    envelope, each error naming what is wrong and the legal values.

    Creating a filter is curation, so it lives here; RUNNING one
    (`filter run`/`prelude`/`record`/`resolve`/`triage-flags`/`status`/
    `unmark`/`rollback`/`abandon`) stays CLI-only, the write loop's
    one-call-surface ruling — use list_filter_edits/triage_filter_edits
    for the conversational half of that loop instead."""
    def run():
        db = _db()
        return api.filter_add(_manuscript(db, manuscript), name, prompt)
    return _guard(run)


@mcp.tool()
def list_filters(manuscript: str | None = None) -> dict:
    """Every filter artifact defined on the manuscript: name, class
    (sequential | global), and its one-line summary — never the prompt
    body (show_filter reads one in full). A malformed artifact is listed
    with its error where the summary would be, not hidden, so a typo the
    author made stays visible instead of silently vanishing."""
    def run():
        from . import filters as flt

        db = _db()
        ms = _manuscript(db, manuscript)
        return {"filters": flt.list_filters(ms)}
    return _guard(run)


@mcp.tool()
def show_filter(name: str, manuscript: str | None = None) -> dict:
    """The full ratified text of one filter's prompt, plus its class and
    state note — for the author asking to read one back, or for you to
    check what an installed filter actually says before recommending it
    or drafting a reply to its run. Reading is not running: `filter run`
    stays a CLI-only verb."""
    def run():
        from . import filters as flt

        db = _db()
        ms = _manuscript(db, manuscript)
        return flt.show_filter(ms, name)
    return _guard(run)


@mcp.tool()
def list_filter_edits(essay: str, manuscript: str | None = None) -> dict:
    """The FILTER pass's staged edit proposals for one essay (after
    'filter record' in the shell): numbered, each with the unit
    (paragraph) it replaces, the verbatim old text, the proposed new
    text, the one-line why, the registry/ledger entry it names (`ref`),
    and its state (proposed | accepted | rejected).

    Read them back to the author AS PROSE, by paragraph, never as JSON
    or as a list of ids — and volunteer the one or two you are least
    sure about, by number. Record verdicts with triage_filter_edits, and
    take a rejection's reason in the author's OWN WORDS: it is the
    highest-value evidence the run produces, and the next run of this
    filter on this file reads it before it starts.

    Only the ACTIVE run's proposals are listed. A settled run's
    rejections are history and live in the next run's payload, not in a
    triage list. Running the filter, settling and rolling back are
    CLI-only, the same ruling the write loop gets: one call surface, so
    improving a verb improves every session.

    `mode` is the run's TRANSPORT and `forms_out` how many of its forms
    are already placed. `mode='doc'` means the author is reading and
    rewording those changes in the Google Doc right now: say so, do not
    offer to apply them, and name the CLI verb that ends the pause —
    `authorlm filter resolve <essay>`, which reads the tab back."""
    def run():
        db = _db()
        ms = _manuscript(db, manuscript)
        return api.filter_edits(db, ms, essay)
    return _guard(run)


@mcp.tool()
def triage_filter_edits(essay: str, operations: list[dict],
                        manuscript: str | None = None) -> dict:
    """Record the author's verdicts on staged FILTER proposals, in
    batch. Each operation: {"item": "<list number or id prefix>",
    "verdict": "accept" | "reject" | "revise" | "undo", "reason": "..."
    (reject: the author's VERBATIM words, required), "text": "..."
    (revise: the author's own wording — the diff is evidence)}.

    Verdicts are recorded only; nothing touches the file until 'filter
    settle' in the shell. Undo is free until then. Numbers resolve
    against one snapshot of list_filter_edits order. One failed op never
    blocks the rest."""
    def run():
        db = _db()
        ms = _manuscript(db, manuscript)
        return api.filter_triage(db, ms, essay, operations)
    return _guard(run)


@mcp.tool()
def list_critique_edits(essay: str, manuscript: str | None = None) -> dict:
    """The critique pass's STAGED EDIT proposals for one essay (after
    'critique run' in the shell): numbered, each with kind (replace |
    insert), anchor paragraph, verbatim old, proposed new, the editor's
    one-line why, the intent it serves, and its state (proposed |
    accepted | rejected). Present them to the author for verdicts and
    record them with triage_critique_edits. Running the pass, writing
    to the Doc, resolving, and rolling back are CLI-only (Drive)."""
    def run():
        from . import passes

        db = _db()
        ms = _manuscript(db, manuscript)
        items = []
        for n, t in enumerate(passes.staged_threads(db, ms["id"], essay), 1):
            meta = _loads(t.get("metadata"), {}) or {}
            items.append({
                "n": n, "id": t["id"], "state": t["state"],
                "kind": meta.get("kind"), "anchor_paragraph":
                meta.get("anchor_paragraph"), "old": t["proposed_old"],
                "new": t["proposed_new"], "why": t["note"],
                "intent_id": meta.get("intent_id"),
            })
        return {"essay": essay, "count": len(items), "items": items}
    return _guard(run)


@mcp.tool()
def triage_critique_edits(essay: str, operations: list[dict],
                          manuscript: str | None = None) -> dict:
    """Record the author's verdicts on staged edit proposals, in batch.
    Each operation: {"item": "<list number or id prefix>", "verdict":
    "accept" | "reject" | "revise" | "undo", "reason": "..." (reject: the
    author's verbatim words), "text": "..." (revise: the author's own
    wording — the diff is evidence)}. Verdicts are recorded only; nothing
    touches the file or the Doc until 'critique write' in the shell.
    Undo is free at any time before write. Numbers resolve against one
    snapshot of list_critique_edits order. One failed op never blocks the
    rest."""
    def run():
        from . import passes

        db = _db()
        ms = _manuscript(db, manuscript)
        mid = ms["id"]
        snapshot = passes.staged_threads(db, mid, essay)
        results = []
        for op in operations:
            token = str(op.get("item", "")).strip()
            verdict = op.get("verdict")
            entry = {"item": token, "verdict": verdict}
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
                if verdict == "reject" and not op.get("reason"):
                    raise ValueError("reject requires the author's verbatim "
                                     "reason")
                text = op.get("reason") if verdict == "reject" else op.get("text")
                out = passes.verdict(db, mid, thread, verdict, text)
                entry.update(ok=True, id=thread["id"], **out)
            except (LookupError, ValueError, KeyError) as err:
                entry.update(ok=False, error=str(err))
            results.append(entry)
        p = passes.active_pass(db, mid)
        if p:
            passes.mark_triaged(db, p, essay)
        left = sum(1 for t in passes.staged_threads(db, mid, essay)
                   if t["state"] == "proposed")
        return {"results": results, "still_proposed": left,
                "next": f"critique write {essay} (shell) once triage is done"}
    return _guard(run)


@mcp.tool()
def move_style_law(element_id: str, guide: str | None = None,
                       file: str | None = None,
                       manuscript: str | None = None) -> dict:
    """Re-scope a style element to another guide (by name) or to one file
    — exactly one of guide/file. Use when a rule sits at the wrong level
    (e.g. a Sermons-only voice rule imported at house scope). Keeps the
    element's id, status, provenance, and history."""
    def run():
        db = _db()
        return api.move_style_law(db, _manuscript(db, manuscript),
                                      element_id, guide_name=guide, file=file)
    return _guard(run)


def _critique_by_prefix(db, mid: str, prefix: str):
    for kind, table in (("intent", "declared_intents"),
                        ("element", "style_laws")):
        rows = db.all(
            f"SELECT * FROM {table} WHERE manuscript_id = ? "
            "AND status = 'proposed' AND id LIKE ?",
            (mid, f"%{prefix}%"))
        if len(rows) == 1:
            return kind, dict(rows[0])
        if len(rows) > 1:
            raise LookupError(f"'{prefix}' is ambiguous ({len(rows)} matches)")
    raise LookupError(f"no proposed critique item matching '{prefix}'")


@mcp.tool()
def collect_revision(manuscript: str | None = None,
                     verbose: bool = False) -> dict:
    """Snapshot the manuscript now: detects transitions, realizes concepts,
    runs incremental extraction (concepts, links, aliasing statements) when
    an LLM is configured, and reports the prerequisite-gap delta. Call
    after the author says they saved/finished edits. Compact by default
    (gap counts + resolved/new deltas); verbose=True returns the full
    gaps_before/gaps_after rows."""
    def run():
        db = _db()
        ms = _manuscript(db, manuscript)
        api.ensure_session(db, ms)
        report = api.collect(db, ms, api.load_config(_WORKSPACE), analyze=True)
        return report if verbose else api.compact_collect(report)
    return _guard(run)


@mcp.tool()
def run_sweep(kind: str, manuscript: str | None = None,
              file: str | None = None) -> dict:
    """Run a sweep (docs/sweep-framework.md). kind: 'hygiene' (ungrounded
    concepts/edges, moot suggestions — deterministic, zero tokens),
    'readiness' (pre-publication checklist — deterministic), or 'ontology'
    (narrowing auditor: changed paragraphs vs settled Concept Graph
    claims; findings become incongruence proposals for the author's
    verdict; `file` audits one file instead of the changed set). Report
    the result conversationally; never act on findings without the
    author."""
    def run():
        from pathlib import Path

        from . import hygiene as hy
        from . import sweeps
        from .revisions import read_manuscript_files

        db = _db()
        ms = _manuscript(db, manuscript)
        if kind == "hygiene":
            return hy.sweep(db, ms,
                            read_manuscript_files(Path(ms["path"])))
        if kind == "readiness":
            return sweeps.readiness(db, ms)
        if kind == "ontology":
            llm = _llm()
            if llm is None or not llm.enabled:
                raise ValueError("the ontology sweep needs the LLM "
                                 "configured ([llm] in config.toml)")
            return sweeps.ontology(db, ms, llm, file=file)
        raise ValueError("kind must be hygiene | readiness | ontology")
    return _guard(run)


@mcp.tool()
def scan_illustrations(file: str | None = None,
                       manuscript: str | None = None) -> dict:
    """Run the illustration spot-finder (cheap-model lens) over main-
    matter chapters — or one file — staging placement proposals per the
    ratified illustration-placement law (criteria, pacing; front matter
    and per-guide exclusions apply). Proposals land in a STAGING table,
    never in text: present them to the author for triage. Returns
    {files, calls, staged, dropped_unverifiable, prompt_files} —
    prompt_files names the file(s) (authorlm/prompts/...) that shaped
    this scan, so the author can go read them."""
    def run():
        from . import placement

        db = _db()
        ms = _manuscript(db, manuscript)
        llm = _llm()
        if llm is None or not llm.enabled:
            raise ValueError("the spot-finder needs the LLM configured "
                             "([llm] in config.toml)")
        report = placement.scan(db, ms, llm,
                                files=[file] if file else None)
        report["staged"] = [
            {"id": r["id"], "file": r["file"], "anchor": r["anchor"][:90],
             "description": r["description"], "criterion": r["criterion"],
             "rationale": r["rationale"], "revises": r["revises"]}
            for r in report["staged"]]
        return report
    return _guard(run)


@mcp.tool()
def triage_illustrations(proposal_id: str | None = None,
                         verdict: str | None = None,
                         revised_description: str | None = None,
                         reason: str | None = None,
                         manuscript: str | None = None) -> dict:
    """List or decide staged illustration placements. With no arguments:
    the open proposals (stale ones swept first). With proposal_id +
    verdict 'accept'|'reject': apply the author's decision — accept
    writes the tag into the local file after its anchor (then collect,
    push, and render follow without further prompting, per the skill);
    a revised_description is a modified acceptance whose diff is
    recorded as learning-loop evidence; record rejection reasons
    VERBATIM in the author's words."""
    def run():
        from . import placement

        db = _db()
        ms = _manuscript(db, manuscript)
        staled = placement.sweep_stale(db, ms)
        if proposal_id is None:
            return {"stale": [r["id"] for r in staled],
                    "open": [
                        {"id": r["id"], "file": r["file"],
                         "anchor": r["anchor"][:90],
                         "description": r["description"],
                         "criterion": r["criterion"],
                         "rationale": r["rationale"],
                         "revises": r["revises"]}
                        for r in placement.open_proposals(db, ms["id"])]}
        if verdict not in ("accept", "reject"):
            raise ValueError("verdict must be accept or reject")
        return placement.decide(db, ms, proposal_id, verdict,
                                revised_description=revised_description,
                                reason=reason, llm=_llm())
    return _guard(run)


@mcp.tool()
def get_illustration_prompt(fragment: str,
                            manuscript: str | None = None) -> dict:
    """The exact composed prompt a render of an illustration slot would
    send to the image model — the effective illustration law for the
    slot's file plus the tag's prompt text — assembled through the same
    code path the renderer uses, so it cannot diverge. `fragment`
    selects the slot by a substring of its prompt. Use when debugging
    why an image came out a certain way, or before a render to preview
    what the model will be told. Returns {file, line, prompt, caption,
    desc_hash, style_hash, law, composed}."""
    def run():
        from pathlib import Path

        from .illus import effective_prompt, find_slot

        db = _db()
        ms = _manuscript(db, manuscript)
        matches = find_slot(Path(ms["path"]), fragment)
        if not matches:
            raise LookupError(f"no illustration tag matches '{fragment}'")
        if len(matches) > 1:
            raise LookupError(
                f"'{fragment}' is ambiguous — matches: "
                + "; ".join(f"{m['file']}:{m['line']}" for m in matches))
        return effective_prompt(db, ms, matches[0])
    return _guard(run)


@mcp.tool()
def get_guidance(manuscript: str | None = None) -> dict:
    """Generate explained editorial suggestions for the active intents:
    concept introductions (with drafted bridge text and precedents from the
    author's own past episodes), prerequisite gaps, unanswered objections,
    belief reminders. May abstain — abstention is a valid answer."""
    def run():
        db = _db()
        ms = _manuscript(db, manuscript)
        session, _ = api.ensure_session(db, ms)
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
    editorial beliefs."""
    def run():
        db = _db()
        ms = _manuscript(db, manuscript)
        session, _ = api.ensure_session(db, ms)
        return api.review(db, ms, session, index, decision, explanation, llm=_llm())
    return _guard(run)


@mcp.tool()
def get_briefing(manuscript: str | None = None,
                 verbose: bool = False) -> dict:
    """The session-opening learning briefing: beliefs strengthened or
    weakened, newly realized concepts, unconfirmed extractions, open
    proposals, inferred relationships awaiting confirmation, contradictions,
    outstanding questions, suggested focus areas, learning velocity.
    Compact by default (counts + actionable projections); verbose=True
    returns full rows."""
    def run():
        db = _db()
        briefing = api.get_briefing(db, _manuscript(db, manuscript),
                                    config=api.load_config(_WORKSPACE))
        return briefing if verbose else api.compact_briefing(briefing)
    return _guard(run)


@mcp.tool()
def get_concepts(manuscript: str | None = None, name: str | None = None,
                 file: str | None = None, query: str | None = None,
                 include_all: bool = False, verbose: bool = False) -> dict:
    """The Concept Graph, scoped. With `name`: detail for one concept
    (notes untruncated — they are the ratified definition). With `file`:
    the concepts realized in that essay. With `query`: name/notes/alias
    match. Unscoped: a summary (counts, recent names) — never the whole
    graph; a mature graph is tens of thousands of tokens, so narrow the
    call instead."""
    def run():
        db = _db()
        ms = _manuscript(db, manuscript)
        if name:
            result = api.show_concept(db, ms, name)
            return result if verbose else api.compact_show_concept(result)
        if file or query:
            return api.scoped_concepts(db, ms, file=file, query=query)
        return api.concept_overview(db, ms)
    return _guard(run)


@mcp.tool()
def curate_concepts(operations: list[dict],
                    manuscript: str | None = None) -> dict:
    """Batch graph curation, applied in order with per-op status. Each
    operation: {"op": "confirm", "name", "kind"?} | {"op": "retire",
    "name"} | {"op": "revive", "name"} (the inverse of retire: restores
    status, location, and that retirement's collateral edges — use when
    a retire was a mistake) | {"op": "link", "from", "relation", "to"} |
    {"op": "reject_edge", "edge_id"} | {"op": "alias", "name", "aliases",
    "remove"?}. One failed op never blocks the rest. Suggestion verdicts
    stay on review_suggestion, one at a time — their explanations are
    evidence."""
    def run():
        db = _db()
        ms = _manuscript(db, manuscript)
        return api.curate_concepts(db, ms, operations)
    return _guard(run)


@mcp.tool()
def add_concept(name: str, kind: str = "concept", notes: str | None = None,
                manuscript: str | None = None) -> dict:
    """Add (or revive) a concept the author declares. kind: concept |
    objection | example | metaphor | question |
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
def get_profile(key: str | None = None, manuscript: str | None = None) -> dict:
    """Author-declared context about the project (market intelligence,
    positioning, …), returned verbatim. Consult FIRST for positioning,
    audience, format, or publisher-facing questions — interpret manuscript
    stats against the recorded ambition, not generic norms. Never cite a
    profile as authority for a prose decision unless the author invokes
    it. Without `key`, lists the profiles on record. Read-only: profiles
    are written via the CLI ('authorlm profile set <key>')."""
    def run():
        db = _db()
        return api.get_profile(_manuscript(db, manuscript), key)
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
def add_style_law(aspect: str, statement: str, guide: str | None = None,
                      file: str | None = None, notes: str | None = None,
                      overrides: str | None = None,
                      manuscript: str | None = None) -> dict:
    """Record a ratified style rule. aspect: register | lexicon | syntax |
    structure | formatting | citation | rhetoric | figure (figurative language, prose) | tone | illustration (image law, consumed only by the illustration renderer). Scope to
    exactly one of `guide` (name) or `file` (file-local override). `notes`
    holds free-text inspect/avoid hints; `overrides` names the id (prefix)
    of an inherited element this one displaces."""
    def run():
        db = _db()
        return api.add_style_law(
            db, _manuscript(db, manuscript), aspect, statement,
            guide_name=guide, file=file, notes=notes, overrides=overrides)
    return _guard(run)


@mcp.tool()
def retire_style_law(element_id: str, manuscript: str | None = None) -> dict:
    """Retire a style element by id (prefix) — it leaves every composition
    but stays historically accessible."""
    def run():
        db = _db()
        return api.retire_style_law(db, _manuscript(db, manuscript),
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
def list_proposals(kind: str | None = None, proposal_id: str | None = None,
                   belief: str | None = None, limit: int | None = 40,
                   verbose: bool = False,
                   manuscript: str | None = None) -> dict:
    """Open proposals against settled knowledge: reframed definitions,
    retired concepts recurring, rejected relationships argued again,
    near-duplicate names of retired concepts.

    Compact by default (id + kind + summary), newest filters first:
    `kind` narrows to one proposal kind, `proposal_id` (an id prefix)
    inspects one in full, `limit` caps the list. `folded` in the reply
    summarises, one line per belief, what the screen cut before the author
    saw it — expand a fold with `belief=<id>` to see those proposals, and
    adopting one there is what tells the system the belief is wrong.
    `verbose=True` returns full payloads (large — narrow it first)."""
    def run():
        db = _db()
        return api.list_proposals(db, _manuscript(db, manuscript), kind=kind,
                                  proposal_id=proposal_id, belief=belief,
                                  limit=limit, verbose=verbose)
    return _guard(run)


@mcp.tool()
def reconcile_proposals(dry_run: bool = False,
                        manuscript: str | None = None) -> dict:
    """Settle proposals the world has already answered and re-base the ones
    it moved under — deterministic, no LLM, no judgment. `satisfied`: what
    it asks for has already happened (the note already says exactly this;
    the concept is already retired). `orphan`: what it refers to is gone.
    `stale`: its stated 'current' text is out of date — these are RE-BASED,
    never dismissed, because a stale proposal can be a regression that only
    reads as an improvement against the old base it carries. Run before
    screening or triaging; `dry_run=True` reports without changing anything."""
    def run():
        db = _db()
        return api.reconcile_proposals(db, _manuscript(db, manuscript),
                                       apply=not dry_run)
    return _guard(run)


@mcp.tool()
def screen_proposals(manuscript: str | None = None) -> dict:
    """Run the learned screen over the open proposal queues: proposals that
    plainly violate the author's active law (validated beliefs + ratified
    rules for that aspect) are cut before the author sees them, each
    attributed to the law that cut it. Inert until beliefs have been earned.
    Cuts are recoverable — `list_proposals` reports them folded by belief,
    and adopting one is what tells the system a belief is wrong."""
    def run():
        db = _db()
        return api.screen_proposals(db, _manuscript(db, manuscript), _llm())
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
                                    proposal_id, action, reason, llm=_llm())
    return _guard(run)


@mcp.tool()
def analyze_episodes(manuscript: str | None = None) -> dict:
    """Backfill: run episode analysis over closed, unanalyzed episodes —
    inferring editorial decisions from the author's actual edits."""
    def run():
        db = _db()
        return api.analyze(db, _manuscript(db, manuscript), _llm())
    return _guard(run)


def cap_diff_files(files: dict, max_lines: int = 300) -> dict:
    """Per-file and whole-answer caps for MCP `diff_versions` payloads.

    Each file's diff is truncated at `max_lines` with a notice naming how
    to fetch more; once the whole-answer budget (`max_lines * 4`) is
    spent, remaining files collapse to a one-line stub. Mutates and
    returns `files`. `max_lines <= 0` leaves the payload untouched."""
    if not isinstance(files, dict) or max_lines <= 0:
        return files
    budget = max_lines * 4
    spent = 0
    for name, lines in files.items():
        if not isinstance(lines, list):
            continue
        if spent >= budget:
            files[name] = [f"… {len(lines)} changed line(s) — "
                           f"call with file='{name}' to see them"]
            continue
        if len(lines) > max_lines:
            dropped = len(lines) - max_lines
            lines = lines[:max_lines] + [
                f"… (+{dropped} more lines — narrow the span or "
                f"pass file='{name}' with a higher max_lines)"]
            files[name] = lines
        spent += len(lines)
    return files


def compact_belief_rows(rows: list[dict], status: str | None = None,
                        verbose: bool = False) -> dict:
    """MCP `list_beliefs` shaping: optional status filter; compact by
    default (statement/status/confidence/counts); verbose keeps full
    rows including outstanding questions and provenance."""
    if status:
        rows = [p for p in rows if p.get("status") == status]
    if verbose:
        return {"beliefs": rows}
    return {"belief_count": len(rows), "beliefs": [
        {"id": p["id"], "statement": p["statement"],
         "status": p["status"], "confidence": p["confidence"],
         "supporting": p["supporting"],
         "contradicting": p["contradicting"]}
        for p in rows]}


@mcp.tool()
def diff_versions(older: int | None = None, newer: int | None = None,
                  file: str | None = None, manuscript: str | None = None,
                  max_lines: int = 300) -> dict:
    """Unified diff between collected manuscript versions (default: the
    last two). Use when the author asks what changed. Each file's diff
    is capped at max_lines (default 300) with a truncation notice —
    narrow the span or name a file for the full picture."""
    def run():
        db = _db()
        result = api.diff_versions(db, _manuscript(db, manuscript),
                                   older, newer, file)
        files = result.get("files")
        if isinstance(files, dict):
            cap_diff_files(files, max_lines)
        return result
    return _guard(run)


@mcp.tool()
def list_beliefs(manuscript: str | None = None, status: str | None = None,
                  verbose: bool = False) -> dict:
    """Learned editorial beliefs. Compact by default (statement, status,
    confidence, support counts); status='validated' (or 'candidate',
    'retired') filters; verbose=True returns full rows including
    outstanding questions and provenance."""
    def run():
        db = _db()
        rows = api.list_beliefs(db, _manuscript(db, manuscript))
        return compact_belief_rows(rows, status=status, verbose=verbose)
    return _guard(run)


@mcp.tool()
def retire_belief(prefix: str, reason: str, manuscript: str | None = None) -> dict:
    """Retire an editorial belief the author rejects (author-initiated
    curation). Kept for history; the statement is banned from re-seeding —
    fresh supporting evidence files a revival proposal instead. Record the
    author's reason verbatim."""
    def run():
        db = _db()
        return api.retire_belief(db, _manuscript(db, manuscript), prefix, reason)
    return _guard(run)


@mcp.tool()
def demote_belief(prefix: str, reason: str, manuscript: str | None = None) -> dict:
    """Demote a validated editorial belief back to candidate (Sponsor
    override, contradicting evidence, etc.) — unlike retire_belief, the
    statement is NOT banned from re-seeding, and the belief can re-validate
    on real future evidence. Record the author's reason verbatim."""
    def run():
        db = _db()
        return api.demote_belief(db, _manuscript(db, manuscript), prefix, reason)
    return _guard(run)


@mcp.tool()
def merge_beliefs(duplicate: str, canonical: str,
                   reason: str | None = None,
                   manuscript: str | None = None) -> dict:
    """Fold a duplicate belief's evidence record (support counts, questions)
    into the canonical belief and retire the duplicate. Use when two
    learned beliefs state the same rule in different words."""
    def run():
        db = _db()
        return api.merge_beliefs(db, _manuscript(db, manuscript),
                                  duplicate, canonical, reason)
    return _guard(run)


@mcp.tool()
def convert_belief_to_law(prefix: str, aspect: str,
                            statement: str | None = None,
                            guide: str | None = None,
                            file: str | None = None,
                            notes: str | None = None,
                            reason: str | None = None,
                            manuscript: str | None = None) -> dict:
    """Convert a learned belief into a ratified style element: creates the
    element (in `guide`, or file-local via `file`; statement defaults to
    the belief's) and retires the belief with a recorded linkage. Use when
    a belief is really a timeless how-prose-reads rule, not a revision
    decision."""
    def run():
        db = _db()
        return api.convert_belief(db, _manuscript(db, manuscript), prefix,
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
def get_plan(manuscript: str | None = None) -> dict:
    """The writing plan: for every unrealized concept, where it belongs in
    the manuscript (derived from realized graph neighbors and the TOC
    reading order), which prerequisites to write first, matching active
    intents, and precedents. Use when the author asks 'what should I write
    next?' or 'where does X belong?'."""
    def run():
        db = _db()
        ms = _manuscript(db, manuscript)
        return api.get_plan(db, ms)
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
