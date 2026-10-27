"""Essay summaries — the editor's working memory (critique-pass design §4).

One summary per toc unit, built autoregressively in reading order: each
is conditioned on every prior summary, so an edit pass over essay E can
hold "everything before E" and "everything after E" in a few thousand
tokens. Since the summary hierarchy (docs/summary-hierarchy-design.md
§3.4, ruling 6) the essay summary is COMPOSED from the essay's section
summaries, never from its text: the model writes FLOW, MOVES, MOTIFS and
DOES NOT, and code derives INTRODUCES, ASSUMES, PROMISES and DEBTS from
the paragraph records (§6.6). Summaries are DERIVED machinery (like
renders): they live in the database, never in the manuscript or the Doc.

Staleness is marked, not cascaded. A collect that changes essay N leaves
N's summary with a stale `source_hash` (a lie about the text — the edit
pass refuses to run on it) and flags downstream rows `upstream_stale`
(tolerated). `critique resolve` rebuilds one essay's summary at its
confirmation gate, so processing in reading order keeps every "before"
summary fresh by construction. Every other consumer calls `ensure` for
the level it reads and the dirty rows are rebuilt there, with their cost
printed (rulings 3 and 9); `summarize rebuild` is the deliberate pass.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .db import Database, ko_fields, loads
from .llm import LLMClient, resolve_temperature

PROMPT_PATH = Path(__file__).parent / "prompts" / "summarizer.md"
DEFAULT_SUMMARIZER_MODEL = "gemini/gemini-2.5-flash"

# Target-length scaling (sponsor's ratio): a summary should run roughly
# 1 word per 7.5-10 words of essay — 150-200 words for a ~1500-word essay,
# 300-400 for a ~3000-word one. Floored so a short front-matter unit still
# gets a real target instead of "0-0 words", capped so a single unit can't
# blow the editor's working-memory budget the summaries exist to keep small.
MIN_SUMMARY_WORDS = 40
MAX_SUMMARY_WORDS = 500

# The sentinel placement for an essay that opens the book — the one
# position `after=<file>` cannot name (design §12.4 item 3).
PLACEMENT_START = "start"


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def target_length(text: str) -> tuple[int, int]:
    """(low, high) word-count target for this unit's summary, scaled to
    the unit's own word count. See the module-level ratio comment."""
    n = len(text.split())
    low = max(MIN_SUMMARY_WORDS, round(n / 10))
    high = max(low + 10, round(n / 7.5))
    high = min(high, MAX_SUMMARY_WORDS)
    low = min(low, high - 10) if high - 10 >= MIN_SUMMARY_WORDS else MIN_SUMMARY_WORDS
    return low, high


def _numbered_paragraphs(text: str) -> tuple[str, int]:
    """A file's body with each paragraph prefixed by its 1-based number.
    The essay summarizer no longer reads text (§3.4); extraction keeps
    numbering its payloads this way, so the helper stays."""
    from .revisions import _paragraphs

    paras = _paragraphs(text)
    return "\n\n".join(f"[{i}] {p}" for i, p in enumerate(paras, 1)), len(paras)


def summarizer_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def summarizer_llm(config: dict) -> LLMClient:
    """The cheap-tier client for compression work. `[critique]
    summarizer_model` overrides the general `[llm] model`; the LLM key,
    provider, and cache settings are shared. `[critique] temperature`
    (AE-3), if set, likewise overrides `[llm] temperature`/the constant —
    resolve_temperature (llm.py) is the one place that fallback chain is
    written."""
    llm = LLMClient(config)
    llm.purpose = "critique.summarizer"     # the usage ledger's key (AP)
    override = (config.get("critique", {}) or {}).get("summarizer_model")
    if override:
        llm.model = override
    llm.temperature = resolve_temperature(config, "critique")
    return llm


# ------------------------------------------------------------- reading

def units(manuscript: dict) -> list[tuple[str, str]]:
    """(file, text) pairs in toc reading order — every content file,
    front matter included (the critic has notes even there)."""
    from .revisions import read_manuscript_files
    from .structure import ordered_items

    files = read_manuscript_files(Path(manuscript["path"]))
    return ordered_items(files)


def _units_and_unlisted(manuscript: dict) -> tuple[dict[str, str], list[str]]:
    """`units()` as a dict, plus the files structure.reading_order had to
    APPEND because toc.toml never mentions them. `units()` discards that
    second element; before_after cannot afford to, because an appended
    file's position is a fallback, not a declaration."""
    from .revisions import read_manuscript_files
    from .structure import reading_order

    files = read_manuscript_files(Path(manuscript["path"]))
    order, unlisted = reading_order(files)
    return {name: files[name] for name in order if name in files}, unlisted


def all_summaries(db: Database, manuscript_id: str) -> dict[str, dict]:
    return {r["file"]: dict(r) for r in db.all(
        "SELECT * FROM essay_summaries WHERE manuscript_id = ?",
        (manuscript_id,))}


# ------------------------------------------------- in-flight context
#
# design §14: a file with an ACTIVE writeup is not described by its bytes
# on disk — `write start` truncated it and left a placeholder there. What
# it contributes to everyone else's context is its PINNED text, the text
# inside the version that writeup pinned. `units()` stays DISK truth (the
# collector, the staleness marker and the editor's paragraph list all
# want the bytes as they are); `context_units()` is CONTEXT truth, and
# only the conditioning paths read it.

# Distinguishes "not in flight" from "in flight with no pinned text".
# A sentinel, not a truthiness test: a pinned text may legitimately be
# "" (the file was empty when pinned), which must still read as in-flight.
NOT_IN_FLIGHT = object()


def inflight_sources(db: Database, manuscript: dict) -> dict[str, str | None]:
    """{file: pinned text} for every file with an active writeup.

    `None` means there IS an active writeup but no pre-rewrite text —
    which happens for exactly one reason: the writeup CREATED the file
    (`write start --new`), so the pinned version has no entry for it and
    its pre-writeup state is nonexistence. (A pinned version that has
    gone missing reads the same way, and honestly so: there is no
    pre-rewrite text to be had.)"""
    rows = db.all(
        "SELECT w.file AS file, v.files AS files FROM writeups w "
        "LEFT JOIN manuscript_versions v ON v.id = w.source_version_id "
        "WHERE w.manuscript_id = ? AND w.status = 'active'",
        (manuscript["id"],))
    return {r["file"]: loads(r["files"], {}).get(r["file"]) for r in rows}


def _context_units(db: Database, manuscript: dict
                   ) -> tuple[dict[str, str], list[str], dict[str, str | None]]:
    """`_units_and_unlisted` with the in-flight overlay applied, plus the
    overlay itself. Insertion order is reading order, as on disk.

    The hash comparison stays like-for-like at no cost: `manuscript_
    versions.files` is written through `revisions.read_manuscript_files`
    and `units()` reads through the same function, so both sides have
    already been normalized identically."""
    texts, unlisted = _units_and_unlisted(manuscript)
    flight = inflight_sources(db, manuscript)
    for file, pinned in flight.items():
        if file in texts and pinned is not None:
            texts[file] = pinned
    return texts, unlisted, flight


def context_units(db: Database, manuscript: dict) -> list[tuple[str, str]]:
    """`units()`, but every in-flight file contributes its PINNED text
    instead of the placeholder on disk. This is what every path that asks
    "what does this essay SAY, for the purpose of conditioning something
    else on it" must read."""
    texts, _unlisted, _flight = _context_units(db, manuscript)
    return list(texts.items())


def capture(db: Database, manuscript: dict):
    """ONE snapshot of the manuscript's context text, to be shared by
    every read inside a single verb invocation.

    Another session can truncate an essay at any moment — that is the
    whole point of §14 — so a verb that reads the disk twice can have the
    manuscript change under it BETWEEN the two reads. The dangerous shape
    is a gate and the thing it gates reading separately: the gate passes
    on text A and the context is then assembled from text B, which is a
    lie the gate specifically exists to prevent. So: capture once, hand
    the capture to the gate AND to the assembly, and the gate judges
    exactly the text the context will carry.

    Opaque by intention — pass it along, do not unpack it at call sites."""
    return _context_units(db, manuscript)


def _state(row, text: str, pinned=NOT_IN_FLIGHT) -> str:
    """One unit's freshness (design §1.3 — the decision list, in order).

    `unwritten` outranks everything, `deprecated` included: a filename
    that was deleted, had its summary deprecated and has now been
    recreated by `write start --new` still has a row on record, but that
    row summarizes a DIFFERENT essay that used to have this name, and
    serving it would be the exact lie `deprecated` exists to prevent.

    `deprecated` outranks the hash checks: the row belongs to an essay
    that LEFT the toc (api._deprecate_departed_summaries, commit 10497c9)
    and has come back. Its source_hash can match by coincidence — the
    file returned unedited — but the DB says it is not a live summary, so
    it must never read as `fresh`; it needs a rebuild, which is also what
    resurrects it to 'current'.

    `rewriting` sits BELOW `stale` and ABOVE `upstream_stale`: the hash
    check has already proved the stored summary matches the pinned text,
    so `rewriting` is a statement about WHICH text the reader is getting,
    not about freshness. missing/stale/deprecated still refuse while a
    file is in flight — no stored summary corresponds to the pinned text,
    so there is nothing honest to serve — but the printed remedy now
    works (rebuild reads the pinned text, never the placeholder)."""
    if pinned is None:
        return "unwritten"
    if not row:
        return "missing"
    if row["status"] == "deprecated":
        return "deprecated"
    if row["source_hash"] != _hash(text):
        return "stale"
    if row["sections_hash"] != sections_hash(
            [sec["section_hash"] for sec in sections_of(text)]):
        # "My sections changed" (§3.4, §4.3). Unreachable while the
        # source_hash matches, except for a pre-hierarchy row ('') — which
        # the first full rebuild replaces, as ruling 6 says.
        return "stale"
    if pinned is not NOT_IN_FLIGHT:
        return "rewriting"
    return "upstream_stale" if row["upstream_stale"] else "fresh"


def status(db: Database, manuscript: dict) -> list[dict]:
    """Per-unit freshness in reading order: fresh | stale (text changed)
    | upstream_stale | deprecated | missing | rewriting | unwritten."""
    have = all_summaries(db, manuscript["id"])
    texts, _unlisted, flight = _context_units(db, manuscript)
    out = []
    for file, text in texts.items():
        row = have.get(file)
        state = _state(row, text, flight.get(file, NOT_IN_FLIGHT))
        # A word count is a claim that a summary is being offered for this
        # essay. For `unwritten` there is none (§1.3 serves nothing
        # regardless of any row), exactly as for `deprecated`'s dead row —
        # so neither reports a length borrowed from a summary of something
        # else.
        countable = row is not None and state not in ("unwritten",
                                                      "deprecated")
        out.append({"file": file, "state": state,
                    "words": len(row["summary"].split()) if countable else 0,
                    "created_at": row["created_at"] if row else None})
    return out


def before_after(db: Database, manuscript: dict, file: str,
                 placement: str | None = None, capture=None
                 ) -> tuple[list[dict], list[dict]]:
    """Summaries of the units before and after `file` in reading order —
    the edit pass's book-context, and (with `placement`) the drafting
    pass's. Each entry: {file, summary, state}.

    `placement` overrides where `file` sits, for an essay whose toc entry
    is not committed yet (design §12.4 item 3). It is either
    PLACEMENT_START (the new essay opens the book) or the name of an
    existing unit it follows.

    An essay missing from toc.toml is REFUSED without one. It is not
    absent from the reading order — structure.reading_order appends it —
    but that appended position is a fallback, and taking it at face value
    would put the whole book behind the new essay and nothing ahead of
    it: §7's continuity contract exactly inverted, silently, for the one
    file whose placement actually matters. The position is not guessable,
    so it is asked for rather than assumed.

    `capture` is the caller's single snapshot (see `capture()`), so the
    gate and the context assembly of one verb invocation judge and serve
    the same bytes. Omitted, one is taken here."""
    have = all_summaries(db, manuscript["id"])
    texts, unlisted, flight = capture or _context_units(db, manuscript)
    order = list(texts)
    if placement is None:
        if file not in order:
            raise LookupError(f"'{file}' is not in the manuscript's reading order")
        if file in unlisted:
            # Both remedies, in the right order for BOTH callers. Every
            # pass that reads this function needs the toc entry, so that
            # goes first and unqualified; the --after flag exists only on
            # 'write start', so it is marked as such rather than handed
            # to a 'critique run' user as advice for a different verb.
            raise LookupError(
                f"'{file}' has no toc.toml entry, so the reading order "
                f"cannot say what is settled before it and what is still "
                f"to come (unlisted files are appended to the end, which "
                f"would read as 'the whole book is behind me'). Add it to "
                f"toc.toml — that is the fix for any pass. When STARTING "
                f"A WRITEUP on it, 'authorlm write start {file}' can take "
                f"the placement instead, for that writeup only: "
                f"--after <file it follows>  |  --after {PLACEMENT_START}")
        idx = order.index(file)
        before_files, after_files = order[:idx], order[idx + 1:]
    else:
        rest = [f for f in order if f != file]
        if placement == PLACEMENT_START:
            before_files, after_files = [], rest
        elif placement in rest:
            cut = rest.index(placement) + 1
            before_files, after_files = rest[:cut], rest[cut:]
        else:
            raise LookupError(
                f"placement '{placement}' is not in the manuscript's reading "
                f"order — name an existing unit to follow, or "
                f"'{PLACEMENT_START}' to open the book")

    def entry(f):
        row = have.get(f)
        pinned = flight.get(f, NOT_IN_FLIGHT)
        state = _state(row, texts[f], pinned)
        summary = row["summary"] if row else None
        if state == "unwritten":
            # Regardless of any row: whatever it summarizes, it is not
            # this essay, which does not exist yet (design §1.3).
            summary = None
        return {"file": f, "summary": summary, "state": state,
                "in_flight": pinned is not NOT_IN_FLIGHT}

    return ([entry(f) for f in before_files],
            [entry(f) for f in after_files])


# --------------------------------------------------- drafting context

BEFORE_HEADER = (
    "BEFORE — settled context, in reading order. These essays are behind "
    "the reader: their concepts are AVAILABLE (citable, buildable-upon "
    "using the author's ratified definitions) and must not be "
    "re-introduced.")
AFTER_HEADER = (
    "AFTER — upcoming essays. Their concepts are NOT available: a beat "
    "that needs one must forward-reference it (\"as a later essay will "
    "show\"), never assume it.")

# Every line the reader must not skim past starts with this, so one
# branch in cli._print_drafting_context colours all of them.
WARN_PREFIX = "!! "

# Every state whose entry the reader MUST NOT TAKE AT FACE VALUE. That
# is the true invariant `!! ` marks — wider than "the three states
# write_start refuses", which is what it used to be described as and
# never quite was (_coverage_note has always marked entries the gates
# accept). Three of these are refused by both gates; the two in-flight
# states are accepted, and marked because the entry is served from
# DIFFERENT BYTES than the file on disk.
#
# write_status cannot refuse — it is the resume entry point and must keep
# working — so it says so loudly instead. Without this the untrustworthy
# entry was the QUIETER one: its summary rendered as plain text and
# _coverage_note returned None for it, so a mid-writeup collect could
# flip a neighbour stale and the resume view would serve, unmarked, the
# exact lie the start gate had refused. `upstream_stale` is deliberately
# absent: the gate tolerates it (the text did not move, only the
# conditioning), and a marker on everything marks nothing.
STATE_WARNING = {
    "missing": "summary MISSING — this essay has no summary at all; run "
               "'summarize rebuild' before drafting against it.",
    "stale": "summary STALE — the essay's text changed after this summary "
             "was written, so what follows is a lie about it; run "
             "'summarize rebuild'.",
    "deprecated": "summary DEPRECATED — this essay left the toc and came "
                  "back; run 'summarize rebuild' to resurrect it.",
    "rewriting": "MID-REWRITE — a writeup is rewriting this essay right "
                 "now. What follows is\n   the PRE-REWRITE essay, from "
                 "that writeup's pinned source version, not\n   from the "
                 "file on disk.",
    "unwritten": "BEING WRITTEN FOR THE FIRST TIME — a writeup is drafting "
                 "this essay now.\n   There is no earlier version to "
                 "summarize, so nothing about it is in this\n   context, "
                 "and it is not in toc.toml yet.",
}

# The per-state stand-in when there is no summary to print. The default
# ("run 'summarize rebuild'") is a LIE for an unwritten essay: a rebuild
# cannot help, because there is no pre-rewrite text to summarize.
NO_SUMMARY = {
    "unwritten": "(no pre-rewrite text — this essay did not exist when "
                 "its writeup started)",
}


def inflight_warning(entries: list[dict]) -> str | None:
    """The header block naming every essay in this context that another
    writeup is writing right now — the author asked to be TOLD that
    conditioning on pre-rewrite text is slightly suboptimal (design
    §14.4). `None` when there is nothing to say.

    There is no flag to turn this off, for parity with the summaries gate
    itself: a warning the author can suppress is a warning that will be
    off on the day it mattered."""
    flying = [e for e in entries if e.get("in_flight")]
    if not flying:
        return None
    if len(flying) == 1:
        head = ("1 essay in this context is being written right now, in "
                "another writeup:")
    else:
        head = (f"{len(flying)} essays in this context are being written "
                "right now, in other writeups:")
    lines = [head]
    for e in flying:
        if e["state"] == "unwritten":
            lines += [
                f"  {e['file']} — being written for the first time. There "
                "is no earlier",
                "    version, so nothing about it is in this context at all."]
        else:
            lines += [
                f"  {e['file']} — mid-rewrite. What you get below is the "
                "PRE-REWRITE essay,",
                "    summarized from that writeup's pinned source version, "
                "not from the file",
                "    on disk (which currently holds a placeholder)."]
    lines += [
        "This is slightly suboptimal and it is worth knowing: whatever those",
        "writeups change, this draft will not know about, and this draft's "
        "essay",
        "will not be in their context either. Finish or abandon them first "
        "if that",
        "matters more than starting now."]
    return "\n".join(WARN_PREFIX + line for line in lines)


# How many section numbers a coverage note will spell out before it
# summarizes the rest; the note goes verbatim into the drafting payload.
COVERAGE_LIST_CAP = 8


def _section_list(numbers: list[int]) -> str:
    head = ", ".join(f"§{n}" for n in numbers[:COVERAGE_LIST_CAP])
    rest = len(numbers) - COVERAGE_LIST_CAP
    return head + (f" …and {rest} more" if rest > 0 else "")


def _coverage_note(entry: dict, text: str) -> str | None:
    """Item 4's loudness, now structural (§3.3, §6.4): the stored block's
    MOVES must cite every section of the essay that has a body and its
    FLOW must name every heading. Recomputed from the stored summary and
    the current text, which only correspond while source_hash matches.
    Reported, never blocking."""
    if entry["summary"] is None or entry["state"] not in ("fresh",
                                                          "upstream_stale",
                                                          "rewriting"):
        return None
    cov = section_coverage(entry["summary"], sections_of(text))
    if cov["complete"]:
        return None
    bits = []
    if cov["missing"]:
        bits.append(_section_list(cov["missing"]) + " not cited in MOVES")
    if cov["unknown"]:
        bits.append(_section_list(cov["unknown"])
                    + f" cited but the essay has {cov['section_count']} "
                      "section(s)")
    if cov["flow_missing"]:
        bits.append("FLOW names no clause for "
                    + ", ".join(f"'{h}'" for h in
                                cov["flow_missing"][:COVERAGE_LIST_CAP]))
    return WARN_PREFIX + "coverage INCOMPLETE: " + "; ".join(bits)


def drafting_context(db: Database, manuscript: dict, file: str,
                     placement: str | None = None, capture=None) -> str:
    """The L1 book-frame for drafting `file`: the compressed summaries of
    everything settled before it and everything still to come, as
    deterministic text (design §12.4 item 1 — the glue `before_after`
    was missing on the write path).

    Serialization is deliberately stable: reading order, no timestamps,
    no ids, no counts that drift — §3 requires L0/L1 to be
    byte-identical across beats or the prompt-cache economics of §4 are
    forfeited by a silent invalidator.

    `placement` is passed through to `before_after` — an essay whose toc
    entry is not committed yet still gets the right split, and is refused
    outright if it declares no placement at all.

    ONE capture serves the entry states and the coverage notes — and,
    when the caller passes its own, serves the gate that let this render
    happen too."""
    snapshot = capture or _context_units(db, manuscript)
    before, after = before_after(db, manuscript, file, placement=placement,
                                 capture=snapshot)
    # CONTEXT truth, not disk truth: the coverage note measures a stored
    # summary against the paragraphs it summarizes, and for an in-flight
    # file those are the pinned ones. Measured against the placeholder it
    # would report a fictitious 100% miss.
    texts = snapshot[0]

    def block(entries: list[dict], header: str) -> list[str]:
        lines = [header]
        if not entries:
            lines += ["", "(none)"]
            return lines
        for e in entries:
            lines += ["", f"[{e['file']}] ({e['state']})"]
            warning = STATE_WARNING.get(e["state"])
            if warning:
                lines.append(WARN_PREFIX + warning)
            note = _coverage_note(e, texts.get(e["file"], ""))
            if note:
                lines.append(note)
            lines.append(e["summary"]
                         or NO_SUMMARY.get(e["state"],
                                           "(no summary — run 'summarize "
                                           "rebuild')"))
        return lines

    if placement == PLACEMENT_START:
        where = "placed first in the manuscript"
    elif placement:
        where = f"placed after {placement}"
    else:
        where = "in its committed toc position"
    inflight = inflight_warning(before + after)
    return "\n".join(
        [f"DRAFTING CONTEXT — {file} ({where}).", ""]
        + ([inflight, ""] if inflight else [])
        + block(before, BEFORE_HEADER) + [""]
        + block(after, AFTER_HEADER))


# ------------------------------------------------------------- building

def _concept_slice(db: Database, manuscript: dict, file: str) -> str:
    """The graph's file= slice as prompt text: name — notes."""
    from . import api

    try:
        sliced = api.scoped_concepts(db, manuscript, file=file)
    except LookupError:
        return "(none recorded)"
    lines = []
    for n in sliced.get("nodes", []):
        notes = (n.get("notes") or "").strip()
        lines.append(f"- {n['name']}" + (f" — {notes}" if notes else ""))
    return "\n".join(lines) or "(none recorded)"


def _prior_block(prior: list[tuple[str, str]]) -> str:
    if not prior:
        return "(this is the first unit)"
    return "\n\n".join(f"[{f}]\n{s}" for f, s in prior)


def _sections_block(sections: list[tuple[dict, dict]]) -> tuple[str, list[int]]:
    """The SECTIONS block of the essay call: "[n] heading" and the stored
    section summary, in order; a heading-only section is named and
    excluded from COVER THESE."""
    blocks, cover = [], []
    for sec, row in sections:
        n = sec["section_ordinal"]
        head = sec["heading"] or "(preamble)"
        if any(p["kind"] != "heading" for p in sec["paragraphs"]):
            cover.append(n)
            blocks.append(f"[{n}] {head}\n{row['summary']}")
        else:
            blocks.append(f"[{n}] {head}\n(heading only)")
    return "\n\n".join(blocks), cover


def summarize_unit(db: Database, manuscript: dict, file: str, text: str,
                   prior: list[tuple[str, str]], llm: LLMClient,
                   sections: list[tuple[dict, dict]],
                   texts: dict[str, str] | None = None) -> dict:
    """Write (or rewrite) one unit's summary from its SECTION SUMMARIES
    (`sections`: (section as `sections_of` yields it, its current row),
    in order), conditioned on `prior` [(file, summary), …] (§3.4). The
    model writes FLOW, MOVES, MOTIFS, DOES NOT; INTRODUCES, ASSUMES,
    PROMISES, DEBTS are derived from the paragraph records and rendered
    under those labels (ruling 6, §6.6). `texts` is the caller's capture
    of the whole manuscript (the derived lists read every file's
    records); omitted, one is taken. Returns the stored row with
    `coverage` (the structural check) and `labels_missing`."""
    low, high = target_length(text)
    block, cover = _sections_block(sections)
    user = (
        "PRIOR SUMMARIES (reading order):\n" + _prior_block(prior)
        + "\n\nCONCEPTS (author's ratified definitions):\n"
        + _concept_slice(db, manuscript, file)
        + f"\n\nTARGET LENGTH: {low}-{high} words "
          f"(scaled to this unit's {len(text.split())} words)."
        + f"\n\nCOVER THESE: {', '.join(str(n) for n in cover) or 'none'}"
        + f"\n\nTHE UNIT: {file}\n\nSECTIONS ({len(sections)}, in order):"
          f"\n\n{block}"
    )
    reply = llm.complete(summarizer_prompt(), user)
    if not reply or not reply.strip():
        raise RuntimeError(f"summarizer returned nothing for {file} "
                           "(LLM disabled or call failed)")
    derived = derived_lists(
        db, manuscript, file,
        paragraph_records(db, manuscript, files=[file], texts={file: text}),
        texts=texts if texts is not None else None)
    summary, labels_missing = render_essay_block(reply.strip(), derived)
    coverage = section_coverage(summary, [sec for sec, _row in sections])
    upstream_hash = _hash("\n".join(s for _, s in prior))
    existing = db.one(
        "SELECT id FROM essay_summaries WHERE manuscript_id = ? AND file = ?",
        (manuscript["id"], file))
    # `status` is written explicitly, not left to the column default: a
    # rebuild is exactly how a summary whose file LEFT the toc and came
    # back is resurrected. Without this the row would stay 'deprecated'
    # forever and never read as a live summary again.
    fields = {"summary": summary, "source_hash": _hash(text),
              "upstream_hash": upstream_hash, "upstream_stale": 0,
              "status": "current", "model": llm.model or "",
              "sections_hash": sections_hash(
                  [sec["section_hash"] for sec, _row in sections])}
    if existing:
        db.update("essay_summaries", existing["id"], fields)
        row_id = existing["id"]
    else:
        row = ko_fields("es")
        row.update(manuscript_id=manuscript["id"], file=file, **fields)
        db.insert("essay_summaries", row)
        row_id = row["id"]
    result = dict(db.one("SELECT * FROM essay_summaries WHERE id = ?", (row_id,)))
    # Not persisted: computed fresh on every write so a caller can act on
    # it immediately.
    result["coverage"] = coverage
    result["labels_missing"] = labels_missing
    return result


def rebuild(db: Database, manuscript: dict, llm, level: str = "essay",
            files: list[str] | None = None, force: bool = False,
            echo=print, progress=None) -> dict:
    """The deliberate pass (§4.5, §8 `summarize rebuild`): `ensure` for
    `level` that ALSO rebuilds the essay rows whose conditioning moved
    (`upstream_stale`), which the lazy on-use path tolerates; `force`
    rebuilds every row of `level` from scratch (the author asked for
    truth from scratch). Lower levels are always brought up to date
    lazily first. Returns `ensure`'s report for `level`.

    ONE snapshot, taken before the first model call (inside `ensure`): a
    full rebuild runs for minutes and another session can truncate an
    essay at any point during it; every unit is summarized from the text
    captured first and the stored hashes are the hashes of THAT text. A
    file a writeup CREATED has no pre-rewrite text and is skipped."""
    return ensure(db, manuscript, llm, level=level, files=files, echo=echo,
                  force=force, upstream=True, progress=progress)


def rebuild_one(db: Database, manuscript: dict, file: str, llm,
                echo=print) -> dict:
    """Rebuild one unit's summary against the CURRENT prior summaries
    (the confirmation-gate rebuild), its lower levels brought up to date
    first, then mark everything downstream upstream_stale — mark, don't
    cascade. Returns the stored row with its `coverage`.

    Reads CONTEXT truth, as `ensure` does, over `ensure`'s ONE capture: a
    file a writeup CREATED raises rather than summarizing nothing."""
    try:
        report = ensure(db, manuscript, llm, level="essay", files=[file],
                        echo=echo, force=True)
    except LookupError:
        raise LookupError(f"'{file}' is not in the manuscript's reading order")
    if file not in report["built"]:
        if file in report["unwritten"]:
            raise LookupError(
                f"'{file}' is being written for the first time by an active "
                f"writeup — there is no pre-rewrite text to summarize, so a "
                f"rebuild would summarize an empty file and store it as "
                f"fresh. See 'write status'; its summary is built after "
                f"'write complete'.")
        reasons = [why for f, why in report["skipped"] if f == file]
        raise RuntimeError(f"the summary of {file} was not rebuilt: "
                           + (reasons[0] if reasons else "see the report"))
    row = dict(db.one(
        "SELECT * FROM essay_summaries WHERE manuscript_id = ? AND file = ?",
        (manuscript["id"], file)))
    misses = [cov for f, cov in report["misses"] if f == file]
    row["coverage"] = misses[0] if misses else {"complete": True}
    return row


def mark_changed(db: Database, manuscript: dict,
                 changed_files: list[str]) -> list[str]:
    """After a collect: flag downstream summaries of any changed unit as
    upstream_stale. The changed unit's own row needs no flag — its
    source_hash mismatch already says the text moved. Returns files
    flagged."""
    if not changed_files:
        return []
    order = [f for f, _ in units(manuscript)]
    have = all_summaries(db, manuscript["id"])
    first = min((order.index(f) for f in changed_files if f in order),
                default=None)
    if first is None:
        return []
    flagged = []
    for f in order[first + 1:]:
        if f in have and not have[f]["upstream_stale"]:
            db.update("essay_summaries", have[f]["id"], {"upstream_stale": 1})
            flagged.append(f)
    return flagged


# ===================================================================
# The summary hierarchy (docs/summary-hierarchy-design.md): paragraph
# records and section summaries beneath the essay row. Up to the
# paragraph pass at the end, this part of the module makes NO model call.
# It owns the units (§0 ruling 1), the kinds (§3.2), the hashes that are
# each level's identity (§4), freshness derived at read time (§4.4) and
# the read API consumers use (§7). The builders that fill the tables are
# §11 steps 2–4; step 2, the paragraph pass, closes the module.
# ===================================================================

PARAGRAPH_KINDS = ("heading", "verse", "dialogue", "prose")
MOVES = ("states", "defines", "argues", "illustrates", "objects", "answers",
         "transitions", "restates", "promises", "recalls")
MENTION_ROLES = ("introduces", "defines", "uses", "names")
REFERENCE_DIRECTIONS = ("back", "forward")

# A paragraph record's shape (§3.2). `EMPTY_RECORD` is what a heading row
# stores: it exists so a section's paragraph list is complete and
# `ordinal` is a position in the file (ruling 8), never to be summarized.
RECORD_LIST_FIELDS = ("mentions", "definitions", "relations", "references",
                      "depends_on")
EMPTY_RECORD: dict = {"gist": "", "claim": None, "move": None,
                      "mentions": [], "definitions": [], "relations": [],
                      "references": [], "depends_on": []}

# The house's speaker-prefixed line: an optional bold or italic wrapper
# around a short name, a colon, then speech. ("Socrates: …", "**A:** …").
# verse-and-cast-design.md §2 reserves the dialogue form for a future play;
# this is the shape every line of such a paragraph takes.
_SPEAKER_LINE = re.compile(
    r"^(?:\*{1,2}|_{1,2})?[A-Z][\w .'’-]{0,40}:(?:\*{1,2}|_{1,2})?(?:\s|$)")
# "Every line is short" (§3.2) — a stanza in a prose file. Measured against
# SMSTTD's average 47-word paragraph: a line of verse rarely passes 12
# words; a prose paragraph of 2+ lines never keeps every line under it
# (hard-wrapped prose is not the house style; the files are one line per
# paragraph).
VERSE_LINE_MAX_WORDS = 12
_HEADING_LINE = re.compile(r"^#{1,6}[ \t]")
# A paragraph that is nothing but one bracketed tag line ("[Omit: all]",
# an audiobook directive): no prose to index, so it gets an empty record
# with no model call, as a heading does (ruling 8). The flash tier returns
# no record for such a line however often it is asked (live, 2026-10-25).
_TAG_ONLY = re.compile(r"^\[[^\[\]\n]+\]$")


def tag_only(text: str) -> bool:
    return bool(_TAG_ONLY.match(text.strip()))


def _normalize_paragraph(text: str) -> str:
    """The text a paragraph hash is taken over: trailing whitespace per
    line and surrounding blank lines dropped, nothing else. Wording,
    punctuation, line breaks inside the block (verse) all count."""
    return "\n".join(line.rstrip() for line in text.strip().splitlines())


def para_hash(text: str) -> str:
    return _hash(_normalize_paragraph(text))


def section_hash(para_hashes: list[str]) -> str:
    """Identity of a section: its ordered paragraph hashes (§4.2), so a
    reorder inside the section moves it and a paragraph that moves
    across sections moves both."""
    return _hash("\n".join(para_hashes))


def sections_hash(section_hashes: list[str]) -> str:
    """Identity of an essay's section structure (§4.3)."""
    return _hash("\n".join(section_hashes))


def paragraph_kind(text: str, *, verse_file: bool = False) -> str:
    """Deterministic, no model (§3.2): heading | verse | dialogue | prose.
    A heading is a block whose first line starts with `#`; dialogue is a
    block whose every line is speaker-prefixed; verse is any block in a
    toc `form = "verse"` file, or a multi-line block whose every line is
    short. Everything else is prose."""
    lines = [ln for ln in text.strip().splitlines() if ln.strip()]
    if not lines:
        return "prose"
    if _HEADING_LINE.match(lines[0]):
        return "heading"
    # One speaker-prefixed line is dialogue only when the label is
    # marked (bold or italic): a lone "Note: …" is prose with a colon.
    if all(_SPEAKER_LINE.match(ln.strip()) for ln in lines) and (
            len(lines) > 1 or lines[0].lstrip()[0] in "*_"):
        return "dialogue"
    if verse_file:
        return "verse"
    if len(lines) > 1 and all(len(ln.split()) <= VERSE_LINE_MAX_WORDS
                              for ln in lines):
        return "verse"
    return "prose"


def sections_of(text: str, *, verse_file: bool = False) -> list[dict]:
    """The file split into its heading-delimited sections (ruling 1; the
    same cut as `extraction._sections`, a file without headings is one
    section), each carrying its paragraphs in order:

        {section_ordinal, heading (None for a preamble), section_hash,
         para_hashes, paragraphs: [{ordinal, section_ordinal, kind,
         text, para_hash}]}

    `ordinal` is 1-based over the whole file, so it is a position
    (ruling 8). A paragraph repeated verbatim (a refrain) appears once
    per occurrence here, under one hash."""
    from .revisions import _paragraphs

    out: list[dict] = []
    cur: dict | None = None
    ordinal = 0
    for block in _paragraphs(text):
        ordinal += 1
        kind = paragraph_kind(block, verse_file=verse_file)
        if cur is None or kind == "heading":
            cur = {"section_ordinal": len(out) + 1,
                   "heading": block.splitlines()[0].strip()
                   if kind == "heading" else None,
                   "paragraphs": []}
            out.append(cur)
        cur["paragraphs"].append({
            "ordinal": ordinal, "section_ordinal": cur["section_ordinal"],
            "kind": kind, "text": block, "para_hash": para_hash(block)})
    for sec in out:
        sec["para_hashes"] = [p["para_hash"] for p in sec["paragraphs"]]
        sec["section_hash"] = section_hash(sec["para_hashes"])
    return out


def paragraphs_of(text: str, *, verse_file: bool = False) -> list[dict]:
    """Every paragraph of the file in order, as `sections_of` lays them
    out."""
    return [p for sec in sections_of(text, verse_file=verse_file)
            for p in sec["paragraphs"]]


def verse_files_of(manuscript: dict) -> set[str]:
    """The files whose toc entry says `form = "verse"` (verse.py owns
    the flag); every paragraph in them is verse (§3.2)."""
    from .revisions import read_manuscript_files
    from .verse import form_map

    files = read_manuscript_files(Path(manuscript["path"]))
    return {f for f, form in form_map(files).items() if form == "verse"}


# ------------------------------------------------------- storage (rows)
#
# Writers for the two new tables. No model call happens here: the builder
# (step 2) produces the record, these store it. Both are idempotent on
# the row's identity hash and resurrect an `orphaned` row whose text has
# come back, exactly as `summarize_unit` resurrects a deprecated essay.

def _check_record(record: dict, text: str) -> tuple[dict, dict]:
    """Guarantee 1 of §7: every span is a verbatim substring of the
    paragraph (byte equality), every relation is in
    `extraction.VALID_RELATIONS`, every role, move and direction is a
    known value. Offending items are DROPPED, not repaired, and counted
    in the returned report. `definitions[].sentence` is a span too."""
    from .extraction import VALID_RELATIONS

    dropped = {"span": 0, "relation": 0, "role": 0, "direction": 0}
    clean: dict = {"gist": str(record.get("gist") or "").strip(),
                   "claim": record.get("claim") or None,
                   "move": record.get("move")
                   if record.get("move") in MOVES else None}
    def _ok_span(item: dict, key: str) -> bool:
        span = item.get(key)
        if isinstance(span, str) and span and span in text:
            return True
        dropped["span"] += 1
        return False

    clean["mentions"] = []
    for m in record.get("mentions") or []:
        if not isinstance(m, dict) or not m.get("term"):
            continue
        if m.get("role") not in MENTION_ROLES:
            dropped["role"] += 1
            continue
        if _ok_span(m, "span"):
            clean["mentions"].append({"term": m["term"], "span": m["span"],
                                      "role": m["role"]})
    clean["definitions"] = [
        {"term": d["term"], "sentence": d["sentence"]}
        for d in record.get("definitions") or []
        if isinstance(d, dict) and d.get("term") and _ok_span(d, "sentence")]
    clean["relations"] = []
    for r in record.get("relations") or []:
        if not isinstance(r, dict) or not (r.get("from") and r.get("to")):
            continue
        if r.get("relation") not in VALID_RELATIONS:
            dropped["relation"] += 1
            continue
        if _ok_span(r, "span"):
            clean["relations"].append({"from": r["from"],
                                       "relation": r["relation"],
                                       "to": r["to"], "span": r["span"]})
    clean["references"] = []
    for ref in record.get("references") or []:
        if not isinstance(ref, dict) or not ref.get("term"):
            continue
        if ref.get("direction") not in REFERENCE_DIRECTIONS:
            dropped["direction"] += 1
            continue
        if _ok_span(ref, "span"):
            clean["references"].append({
                "term": ref["term"], "direction": ref["direction"],
                "target": ref.get("target") or None, "span": ref["span"]})
    clean["depends_on"] = [h for h in record.get("depends_on") or []
                           if isinstance(h, str)]
    return clean, dropped


def store_paragraph_record(db: Database, manuscript: dict, file: str,
                           para: dict, record: dict | None, model: str
                           ) -> tuple[dict, dict]:
    """Store one paragraph's record (`para` as `sections_of` yields it).
    A heading stores `EMPTY_RECORD` with `model = ''` and no check
    (ruling 8). Returns (row, dropped-counts)."""
    if para["kind"] == "heading" or record is None:
        clean, dropped = dict(EMPTY_RECORD), {}
        model = "" if para["kind"] == "heading" else model
    else:
        clean, dropped = _check_record(record, para["text"])
    fields = {"ordinal": para["ordinal"],
              "section_ordinal": para["section_ordinal"],
              "kind": para["kind"], "record": json.dumps(clean),
              "model": model, "status": "current"}
    existing = db.one(
        "SELECT id FROM paragraph_summaries WHERE manuscript_id = ? "
        "AND file = ? AND para_hash = ?",
        (manuscript["id"], file, para["para_hash"]))
    if existing:
        db.update("paragraph_summaries", existing["id"], fields)
        row_id = existing["id"]
    else:
        row = ko_fields("ps")
        row.update(manuscript_id=manuscript["id"], file=file,
                   para_hash=para["para_hash"], **fields)
        db.insert("paragraph_summaries", row)
        row_id = row["id"]
    return (dict(db.one("SELECT * FROM paragraph_summaries WHERE id = ?",
                        (row_id,))), dropped)


def store_section_summary(db: Database, manuscript: dict, file: str,
                          section: dict, summary: str, model: str) -> dict:
    """Store one section's summary (`section` as `sections_of` yields
    it)."""
    fields = {"section_ordinal": section["section_ordinal"],
              "heading": section["heading"],
              "para_hashes": json.dumps(section["para_hashes"]),
              "summary": summary.strip(), "model": model,
              "status": "current"}
    existing = db.one(
        "SELECT id FROM section_summaries WHERE manuscript_id = ? "
        "AND file = ? AND section_hash = ?",
        (manuscript["id"], file, section["section_hash"]))
    if existing:
        db.update("section_summaries", existing["id"], fields)
        row_id = existing["id"]
    else:
        row = ko_fields("ss")
        row.update(manuscript_id=manuscript["id"], file=file,
                   section_hash=section["section_hash"], **fields)
        db.insert("section_summaries", row)
        row_id = row["id"]
    return dict(db.one("SELECT * FROM section_summaries WHERE id = ?",
                       (row_id,)))


def orphan_rows(db: Database, manuscript: dict, file: str, text: str
                ) -> dict[str, list[dict]]:
    """The `current` rows of both lower levels whose hash is no longer in
    the file's text — what a rebuild marks `orphaned` (§4.1). Derived
    here, never on collect."""
    live_paras = {p["para_hash"] for p in paragraphs_of(text)}
    live_secs = {s["section_hash"] for s in sections_of(text)}
    paras = [dict(r) for r in db.all(
        "SELECT * FROM paragraph_summaries WHERE manuscript_id = ? "
        "AND file = ? AND status = 'current'", (manuscript["id"], file))
        if r["para_hash"] not in live_paras]
    secs = [dict(r) for r in db.all(
        "SELECT * FROM section_summaries WHERE manuscript_id = ? "
        "AND file = ? AND status = 'current'", (manuscript["id"], file))
        if r["section_hash"] not in live_secs]
    return {"paragraph": paras, "section": secs}


def mark_orphans(db: Database, manuscript: dict, file: str, text: str
                 ) -> dict[str, int]:
    """Flip the rows `orphan_rows` finds to `orphaned`. Returns counts."""
    found = orphan_rows(db, manuscript, file, text)
    for row in found["paragraph"]:
        db.update("paragraph_summaries", row["id"], {"status": "orphaned"})
    for row in found["section"]:
        db.update("section_summaries", row["id"], {"status": "orphaned"})
    return {k: len(v) for k, v in found.items()}


# ----------------------------------------------------- freshness (read)

def _current_paragraph_rows(db: Database, manuscript_id: str, file: str
                            ) -> dict[str, dict]:
    return {r["para_hash"]: dict(r) for r in db.all(
        "SELECT * FROM paragraph_summaries WHERE manuscript_id = ? "
        "AND file = ? AND status = 'current'", (manuscript_id, file))}


def _current_section_rows(db: Database, manuscript_id: str, file: str
                          ) -> dict[str, dict]:
    return {r["section_hash"]: dict(r) for r in db.all(
        "SELECT * FROM section_summaries WHERE manuscript_id = ? "
        "AND file = ? AND status = 'current'", (manuscript_id, file))}


def level_status(db: Database, manuscript: dict, file: str, text: str,
                 pinned=NOT_IN_FLIGHT, *, verse_file: bool = False) -> dict:
    """Freshness of every level for one file, derived from the hashes at
    read time (§4.4) — nothing here is stored:

        paragraph: {state: fresh|stale|missing, needed, current, missing: [hashes]}
        section:   {state, needed, current, missing: [section_ordinals]}
        essay:     {state: as `_state` (fresh|stale|upstream_stale|…),
                    sections_moved: bool}

    `needed` counts distinct hashes (a refrain is one row); `missing`
    is `stale` when some rows exist and `missing` when none do.
    `sections_moved` is the essay row's `sections_hash` disagreeing with
    the text's — the "my sections changed" signal of §3.4 (False on a
    pre-hierarchy row, whose stored hash is '')."""
    secs = sections_of(text, verse_file=verse_file)
    para_hashes = {p["para_hash"] for s in secs for p in s["paragraphs"]}
    have_p = _current_paragraph_rows(db, manuscript["id"], file)
    missing_p = sorted(h for h in para_hashes if h not in have_p)
    have_s = _current_section_rows(db, manuscript["id"], file)
    missing_s = [s["section_ordinal"] for s in secs
                 if s["section_hash"] not in have_s]

    def _state_of(needed: int, missing: int, present: int) -> str:
        if missing == 0 and needed:
            return "fresh"
        return "stale" if present else "missing"

    essay_row = db.one(
        "SELECT * FROM essay_summaries WHERE manuscript_id = ? AND file = ?",
        (manuscript["id"], file))
    text_sections_hash = sections_hash([s["section_hash"] for s in secs])
    moved = bool(essay_row and essay_row["sections_hash"]
                 and essay_row["sections_hash"] != text_sections_hash)
    return {
        "paragraph": {"state": _state_of(len(para_hashes), len(missing_p),
                                         len(have_p)),
                      "needed": len(para_hashes),
                      "current": len(para_hashes) - len(missing_p),
                      "missing": missing_p},
        "section": {"state": _state_of(len(secs), len(missing_s),
                                       len(have_s)),
                    "needed": len(secs),
                    "current": len(secs) - len(missing_s),
                    "missing": missing_s},
        "essay": {"state": _state(essay_row, text, pinned),
                  "sections_moved": moved},
    }


def hierarchy_status(db: Database, manuscript: dict) -> list[dict]:
    """`level_status` for every unit in reading order, over one capture."""
    texts, _unlisted, flight = _context_units(db, manuscript)
    verse = verse_files_of(manuscript)
    return [dict(file=file, **level_status(
        db, manuscript, file, text, flight.get(file, NOT_IN_FLIGHT),
        verse_file=file in verse)) for file, text in texts.items()]


# --------------------------------------------------------- read API (§7)

def _record_of(row: dict) -> dict:
    rec = dict(EMPTY_RECORD)
    rec.update(loads(row["record"], {}))
    return rec


def _terms(records: list[dict]) -> list[str]:
    """Distinct mention terms in first-seen order, the author's spelling."""
    seen: list[str] = []
    for rec in records:
        for m in rec.get("mentions", []):
            if m["term"] not in seen:
                seen.append(m["term"])
    return seen


def _first_mentions(db: Database, manuscript: dict,
                    texts: dict[str, str] | None = None) -> dict[str, str]:
    """term (lower-cased) → the first file in reading order with an
    `introduces | defines` mention of it, over current rows. The input to
    §6.6's INTRODUCES / ASSUMES split. `texts` is the caller's capture."""
    first: dict[str, str] = {}
    for rec in paragraph_records(db, manuscript, texts=texts):
        for m in rec["mentions"]:
            if m["role"] in ("introduces", "defines"):
                first.setdefault(m["term"].lower(), rec["file"])
    return first


def derived_lists(db: Database, manuscript: dict, file: str,
                  records: list[dict] | None = None,
                  texts: dict[str, str] | None = None) -> dict[str, list[str]]:
    """INTRODUCES, ASSUMES, PROMISES, DEBTS for one essay, from paragraph
    records (ruling 6, §6.6), as lists. INTRODUCES: terms this essay
    introduces or defines with no earlier such mention in reading order.
    ASSUMES: terms it only uses whose first introduction lies elsewhere
    or nowhere. PROMISES: forward references' terms; DEBTS: backward
    references' terms. Normalization against the graph is the consumer's
    step (`manifest._canonical_names`), as §7 guarantee 3 says."""
    if records is None:
        records = paragraph_records(db, manuscript, files=[file], texts=texts)
    first = _first_mentions(db, manuscript, texts)
    introduces: list[str] = []
    uses: list[str] = []
    promises: list[str] = []
    debts: list[str] = []
    for rec in records:
        for m in rec["mentions"]:
            if m["role"] in ("introduces", "defines"):
                if first.get(m["term"].lower()) == file \
                        and m["term"] not in introduces:
                    introduces.append(m["term"])
            elif m["role"] == "uses" and m["term"] not in uses:
                uses.append(m["term"])
        for ref in rec["references"]:
            bucket = promises if ref["direction"] == "forward" else debts
            if ref["term"] not in bucket:
                bucket.append(ref["term"])
    intro_lower = {t.lower() for t in introduces}
    assumes = [t for t in uses if t.lower() not in intro_lower]
    return {"introduces": introduces, "assumes": assumes,
            "promises": promises, "debts": debts}


def for_unit(db: Database, manuscript: dict, file: str, text: str | None = None,
             texts: dict[str, str] | None = None) -> dict:
    """The three levels for one essay, projected for a consumer (§7's
    table; extraction-redesign.md §4.3). Read-only over `current` rows.

    A level with no rows at all is `None`. A level with rows is returned
    with its hash per row and `stale: True` when some unit of the text
    has no row — a paragraph or section without one carries `gist` /
    `summary` `None` so the consumer can print STALE against it. The
    essay carries `_state`'s verdict as `state`.

    `texts` is the caller's snapshot of the whole book (the derived lists
    read every unit's records); omitted, one is taken here. `text` is this
    unit's own text, which defaults to `texts[file]`."""
    if text is None or texts is None:
        if texts is None:
            texts, _unlisted, _flight = _context_units(db, manuscript)
        if text is None:
            if file not in texts:
                raise LookupError(f"'{file}' is not a unit of the manuscript")
            text = texts[file]
    verse_file = file in verse_files_of(manuscript)
    secs = sections_of(text, verse_file=verse_file)
    have_p = _current_paragraph_rows(db, manuscript["id"], file)
    have_s = _current_section_rows(db, manuscript["id"], file)

    paragraphs = None
    if have_p:
        paragraphs = []
        for sec in secs:
            for p in sec["paragraphs"]:
                row = have_p.get(p["para_hash"])
                rec = _record_of(row) if row else None
                paragraphs.append({
                    "n": p["ordinal"], "section": sec["section_ordinal"],
                    "kind": p["kind"], "source_hash": p["para_hash"],
                    "gist": rec["gist"] if rec else None,
                    "terms": _terms([rec]) if rec else [],
                    "stale": row is None})
    sections = None
    if have_s:
        sections = []
        for sec in secs:
            row = have_s.get(sec["section_hash"])
            recs = [_record_of(have_p[h]) for h in sec["para_hashes"]
                    if h in have_p]
            ords = [p["ordinal"] for p in sec["paragraphs"]]
            sections.append({
                "n": sec["section_ordinal"], "heading": sec["heading"],
                "paragraphs": [ords[0], ords[-1]],
                "source_hash": sec["section_hash"],
                "summary": row["summary"] if row else None,
                "terms": _terms(recs), "stale": row is None})
    essay_row = db.one(
        "SELECT * FROM essay_summaries WHERE manuscript_id = ? AND file = ?",
        (manuscript["id"], file))
    essay = None
    if essay_row:
        state = _state(essay_row, text)
        essay = {"summary": essay_row["summary"],
                 "source_hash": essay_row["source_hash"],
                 "state": state,
                 "stale": state not in ("fresh", "upstream_stale")}
        essay.update(derived_lists(
            db, manuscript, file,
            paragraph_records(db, manuscript, files=[file], texts={file: text}),
            texts=texts))
    return {"file": file, "essay": essay, "sections": sections,
            "paragraphs": paragraphs,
            "paragraph_level_stale": bool(paragraphs) and any(
                p["stale"] for p in paragraphs),
            "section_level_stale": bool(sections) and any(
                s["stale"] for s in sections)}


def paragraph_records(db: Database, manuscript: dict,
                      files: list[str] | None = None,
                      texts: dict[str, str] | None = None) -> list[dict]:
    """Every paragraph's full record in reading order, over `current`
    rows: the §7 object (file, para_hash, ordinal, section_ordinal, kind,
    gist, claim, move, mentions, definitions, relations, references,
    depends_on). Order and ordinals come from the TEXT, so a refrain
    yields its one record once per occurrence and a moved paragraph
    reports where it is now. A paragraph with no current row is absent
    (the read API serves rows; `level_status` reports the gap)."""
    if texts is None:
        all_texts, _unlisted, _flight = _context_units(db, manuscript)
        texts = all_texts
    verse = verse_files_of(manuscript)
    out: list[dict] = []
    for file, text in texts.items():
        if files is not None and file not in files:
            continue
        have = _current_paragraph_rows(db, manuscript["id"], file)
        if not have:
            continue
        for p in paragraphs_of(text, verse_file=file in verse):
            row = have.get(p["para_hash"])
            if not row:
                continue
            rec = {"file": file, "para_hash": p["para_hash"],
                   "ordinal": p["ordinal"],
                   "section_ordinal": p["section_ordinal"],
                   "kind": p["kind"]}
            rec.update(_record_of(row))
            out.append(rec)
    return out


def mentions(db: Database, manuscript: dict, term: str | None = None,
             texts: dict[str, str] | None = None) -> list[dict]:
    """Every mention across the book in reading order, flattened for the
    recurrence query (ruling 7): {term, role, span, file, para_hash,
    ordinal, section_ordinal}. `term` filters case-insensitively on the
    author's spelling. `texts` is the caller's snapshot; omitted, one is
    taken here."""
    want = term.lower() if term else None
    out = []
    for rec in paragraph_records(db, manuscript, texts=texts):
        for m in rec["mentions"]:
            if want is not None and m["term"].lower() != want:
                continue
            out.append({"term": m["term"], "role": m["role"],
                        "span": m["span"], "file": rec["file"],
                        "para_hash": rec["para_hash"],
                        "ordinal": rec["ordinal"],
                        "section_ordinal": rec["section_ordinal"]})
    return out


# ------------------------------------------- the paragraph pass (§11.2)
#
# One call per heading section, the section's paragraphs numbered by
# ordinal, a JSON array of records back (§3.2, §3.5). Lazy (ruling 3):
# `ensure` is what a consumer calls before reading a level, and it
# rebuilds only the rows the text has and the table lacks.

PARAGRAPH_PROMPT_PATH = Path(__file__).parent / "prompts" / "paragraph-records.md"
# The flash tier (ruling 5). config.toml names it under `[summaries]`;
# without that key the pass falls through to `[critique]
# summarizer_model`, and only then to this. The call sends
# `paragraph_schema()`, so flash replies in that shape.
DEFAULT_PARAGRAPH_MODEL = "gemini/gemini-3.1-flash-lite"
# Extraction's cap (§3.5): a longer section splits at paragraph
# boundaries into several calls.
MAX_SECTION_CHARS = 24000
# Built levels, lowest first: `ensure` for a level rebuilds every level
# below it first (§4.5).
LEVELS = ("paragraph", "section", "essay")

SECTION_PROMPT_PATH = Path(__file__).parent / "prompts" / "section-summary.md"
# Today's n/10 – n/7.5 formula re-floored for the smaller unit (§3.3).
MIN_SECTION_WORDS = 30
MAX_SECTION_WORDS = 250
# The four labels of a section summary, in order (§3.3).
SECTION_LABELS = ("POINT", "MOVES", "DEPENDS ON", "LEAVES OPEN")
# A section that is only its heading (a chapter title straight into its
# first "##") has no body to summarize: stored without a call, like a
# heading row (ruling 8).
HEADING_ONLY_SUMMARY = ("POINT: A heading with no body text.\nMOVES: none\n"
                        "DEPENDS ON: none\nLEAVES OPEN: none")
# The essay block's labels in stored order (§3.4): the model writes
# ESSAY_MODEL_LABELS from the section summaries, code derives
# DERIVED_LABELS from the paragraph records (ruling 6, §6.6) and renders
# them under the same names, so `manifest._field` keeps working.
ESSAY_MODEL_LABELS = ("FLOW", "MOVES", "MOTIFS", "DOES NOT")
DERIVED_LABELS = ("INTRODUCES", "ASSUMES", "PROMISES", "DEBTS")
ESSAY_LABELS = ("FLOW", "MOVES", "INTRODUCES", "ASSUMES", "PROMISES", "DEBTS",
                "MOTIFS", "DOES NOT")


def paragraph_prompt() -> str:
    from .extraction import VALID_RELATIONS

    return (PARAGRAPH_PROMPT_PATH.read_text(encoding="utf-8")
            .replace("<<MOVES>>", ", ".join(MOVES))
            .replace("<<RELATIONS>>", ", ".join(sorted(VALID_RELATIONS))))


def _list(item: dict) -> dict:
    return {"type": "array", "items": item}


def _obj(**props) -> dict:
    return {"type": "object", "properties": props,
            "required": list(props), "additionalProperties": False}


_STR = {"type": "string"}
_STR_OR_NULL = {"type": ["string", "null"]}


def paragraph_schema() -> tuple[str, dict]:
    """The reply's shape as a JSON schema (§3.2), sent with the call so a
    model that honours `json_schema` (the flash tier does) is held to it.
    The root is an object holding `records`, since strict schemas want an
    object root; `_reply_records` unwraps it. Enum checks stay in
    `store_paragraph_record` too: a model without schema support still
    gets only the prose."""
    from .extraction import VALID_RELATIONS

    record = _obj(
        n={"type": "integer"}, gist=_STR, claim=_STR_OR_NULL,
        move={"type": "string", "enum": list(MOVES)},
        mentions=_list(_obj(term=_STR, span=_STR, role={
            "type": "string", "enum": list(MENTION_ROLES)})),
        definitions=_list(_obj(term=_STR, sentence=_STR)),
        relations=_list(_obj(**{"from": _STR, "relation": {
            "type": "string", "enum": sorted(VALID_RELATIONS)},
            "to": _STR, "span": _STR})),
        references=_list(_obj(term=_STR, direction={
            "type": "string", "enum": list(REFERENCE_DIRECTIONS)},
            target=_STR_OR_NULL, span=_STR)),
        depends_on=_list({"type": "integer"}))
    return "paragraph_records", _obj(records=_list(record))


def paragraph_llm(config: dict) -> LLMClient:
    """The client for paragraph records: `[summaries] paragraph_model`,
    else `[critique] summarizer_model`, else the flash tier. Ledger
    purpose `summaries.paragraph`, so this level's spend is its own line."""
    llm = LLMClient(config)
    llm.purpose = "summaries.paragraph"
    llm.model = ((config.get("summaries", {}) or {}).get("paragraph_model")
                 or (config.get("critique", {}) or {}).get("summarizer_model")
                 or DEFAULT_PARAGRAPH_MODEL)
    llm.temperature = resolve_temperature(config, "summaries")
    return llm


def _chunks(paragraphs: list[dict], max_chars: int = MAX_SECTION_CHARS
            ) -> list[list[dict]]:
    """A section's non-heading paragraphs cut at paragraph boundaries so
    no chunk's text passes `max_chars` (a single longer paragraph is a
    chunk of its own: a paragraph is never split)."""
    out: list[list[dict]] = []
    size = 0
    for p in paragraphs:
        if out and size + len(p["text"]) <= max_chars:
            out[-1].append(p)
            size += len(p["text"])
        else:
            out.append([p])
            size = len(p["text"])
    return out


def _records_user(file: str, heading: str | None, chunk: list[dict],
                  wanted: list[int]) -> str:
    numbered = "\n\n".join(f"[{p['ordinal']}] {p['text']}" for p in chunk)
    return (f"FILE: {file}\nSECTION: {heading or '(preamble)'}\n\n"
            f"RECORD THESE: {', '.join(str(n) for n in wanted)}\n\n"
            f"THE SECTION:\n\n{numbered}")


def _reply_records(reply) -> dict[int, dict]:
    """The model's array keyed by `n`; a dict wrapping the array under
    one key is accepted, anything else yields nothing."""
    if isinstance(reply, dict):
        lists = [v for v in reply.values() if isinstance(v, list)]
        reply = lists[0] if len(lists) == 1 else None
    out: dict[int, dict] = {}
    for item in reply or []:
        if isinstance(item, dict) and isinstance(item.get("n"), int):
            out.setdefault(item["n"], item)
    return out


def _depends_on(raw, ordinal: int, earlier: dict[int, str]) -> tuple[list[str], int]:
    """Ordinals the model named → hashes of EARLIER paragraphs of the
    same section (§7 guarantee 5); anything else is dropped and counted."""
    hashes: list[str] = []
    dropped = 0
    for n in raw or []:
        h = earlier.get(n) if isinstance(n, int) and n < ordinal else None
        if h is None:
            dropped += 1
        elif h not in hashes:
            hashes.append(h)
    return hashes, dropped


def _build_file(db: Database, manuscript: dict, file: str, text: str,
                llm: LLMClient, report: dict, *, verse_file: bool,
                force: bool = False, **_ctx) -> None:
    """Fill the paragraph rows one file's text lacks: headings with
    `EMPTY_RECORD` and no call (ruling 8), an orphaned row whose text came
    back by flipping it current (no call), the rest one call per section
    chunk that holds a dirty paragraph. `force` treats every row as
    lacking (the stores are idempotent on the hash, so rows are rewritten
    in place)."""
    mark_orphans(db, manuscript, file, text)
    have = {} if force else _current_paragraph_rows(db, manuscript["id"], file)
    orphaned = {} if force else {r["para_hash"]: r["id"] for r in db.all(
        "SELECT id, para_hash FROM paragraph_summaries WHERE manuscript_id = ? "
        "AND file = ? AND status = 'orphaned'", (manuscript["id"], file))}
    done: set[str] = set(have)
    for sec in sections_of(text, verse_file=verse_file):
        body = []
        for p in sec["paragraphs"]:
            if p["para_hash"] in done:
                if p["kind"] != "heading":
                    body.append(p)
                continue
            if p["para_hash"] in orphaned:
                db.update("paragraph_summaries", orphaned[p["para_hash"]],
                          {"status": "current", "ordinal": p["ordinal"],
                           "section_ordinal": p["section_ordinal"]})
                report["resurrected"] += 1
            elif p["kind"] == "heading" or tag_only(p["text"]):
                store_paragraph_record(db, manuscript, file, p, None, "")
                report["headings" if p["kind"] == "heading" else "tags"] += 1
            else:
                body.append(p)
                continue
            done.add(p["para_hash"])
        earlier = {}
        for chunk in _chunks(body):
            wanted, seen = [], set()
            for p in chunk:
                if p["para_hash"] not in done and p["para_hash"] not in seen:
                    wanted.append(p)
                    seen.add(p["para_hash"])
            if not wanted:
                earlier.update({p["ordinal"]: p["para_hash"] for p in chunk})
                continue
            if not llm.enabled:
                raise RuntimeError(
                    f"paragraph records for {file} need the LLM, which is "
                    "disabled ([llm] enabled)")
            reply = llm.complete_json(
                paragraph_prompt(),
                _records_user(file, sec["heading"], chunk,
                              [p["ordinal"] for p in wanted]),
                thinking_budget=0, schema=paragraph_schema())
            report["calls"] += 1
            got = _reply_records(reply)
            for p in chunk:
                if p in wanted:
                    raw = got.get(p["ordinal"])
                    if raw is None:
                        report["missing"].append((file, p["ordinal"]))
                    else:
                        record = dict(raw)
                        record["depends_on"], bad = _depends_on(
                            raw.get("depends_on"), p["ordinal"], earlier)
                        _row, dropped = store_paragraph_record(
                            db, manuscript, file, p, record, llm.model or "")
                        dropped["depends_on"] = bad
                        for k, v in dropped.items():
                            report["dropped"][k] = report["dropped"].get(k, 0) + v
                        report["records"] += 1
                        done.add(p["para_hash"])
                earlier[p["ordinal"]] = p["para_hash"]


# ------------------------------------------------------ section pass (§3.3)

def section_prompt() -> str:
    return SECTION_PROMPT_PATH.read_text(encoding="utf-8")


def section_llm(config: dict) -> LLMClient:
    """The client for section summaries: Luna, `[critique]
    summarizer_model` (ruling 5), unless `[summaries] section_model`
    names another. Ledger purpose `summaries.section`."""
    llm = LLMClient(config)
    llm.purpose = "summaries.section"
    llm.model = ((config.get("summaries", {}) or {}).get("section_model")
                 or (config.get("critique", {}) or {}).get("summarizer_model")
                 or DEFAULT_SUMMARIZER_MODEL)
    llm.temperature = resolve_temperature(config, "summaries")
    return llm


def essay_llm(config: dict) -> LLMClient:
    """The client for essay summaries: `summarizer_llm` (Luna, `[critique]
    summarizer_model`, ruling 5), ledger purpose `critique.summarizer` —
    the key the ledger has carried since 2026-08-30 (§10)."""
    return summarizer_llm(config)


def clients(config: dict) -> dict[str, LLMClient]:
    """One client per built level, for `ensure(llm=…)`: each level's
    spend is its own ledger line and its own cost line."""
    return {"paragraph": paragraph_llm(config), "section": section_llm(config),
            "essay": essay_llm(config)}


def stats_lines(llm) -> str | None:
    """The cost lines of one client or of a `clients()` dict, one per
    level that was consulted, joined with '; '. None if none was."""
    if not isinstance(llm, dict):
        return llm.stats_line()
    lines = [f"{lv}: {c.stats_line()}" for lv, c in llm.items()
             if c.stats_line()]
    return "; ".join(lines) or None


def section_target_length(text: str) -> tuple[int, int]:
    """(low, high) words for a section summary: n/10 to n/7.5 of the
    section's words, floored at 30, capped at 250 (§3.3)."""
    n = len(text.split())
    low = max(MIN_SECTION_WORDS, round(n / 10))
    high = min(MAX_SECTION_WORDS, max(low + 10, round(n / 7.5)))
    low = min(low, high - 10)
    return low, high


def _label_block(summary: str, label: str,
                 labels: tuple[str, ...] = SECTION_LABELS) -> str:
    """The text under one label of a summary block, up to the next of
    `labels`; '' when the label is absent."""
    others = "|".join(re.escape(x) for x in labels if x != label)
    m = re.search(rf"^{re.escape(label)}:[ \t]*(.*?)(?=^(?:{others}):|\Z)",
                  summary, re.S | re.M)
    return m.group(1).strip() if m else ""


def point_of(summary: str) -> str:
    """The POINT line of a stored section summary (what later sections
    of the same essay are conditioned on, §3.3)."""
    return " ".join(_label_block(summary, "POINT").split())


_ORDINAL_RE = re.compile(r"\[(\d+)\]")


def moves_coverage(summary: str, ordinals: list[int]) -> dict:
    """Set equality between the paragraphs MOVES cites and the section's
    body paragraphs (§3.3, ruling 8): each non-empty line of the MOVES
    block is one move and the `[n]` numbers on it are its paragraphs.
    No range is read: "[4]-[6]" cites 4 and 6, and 5 is missing.

    {complete, missing: [n] cited by no move, duplicated: [n] cited by
    more than one move, unknown: [n] cited but not in the section}."""
    moves = [ln for ln in _label_block(summary, "MOVES").splitlines()
             if ln.strip()]
    counts: dict[int, int] = {}
    for move in moves:
        for n in {int(x) for x in _ORDINAL_RE.findall(move)}:
            counts[n] = counts.get(n, 0) + 1
    wanted = set(ordinals)
    missing = sorted(wanted - set(counts))
    duplicated = sorted(n for n, c in counts.items() if c > 1 and n in wanted)
    unknown = sorted(set(counts) - wanted)
    return {"complete": not (missing or duplicated or unknown),
            "missing": missing, "duplicated": duplicated, "unknown": unknown}


def _section_user(db: Database, manuscript: dict, file: str, section: dict,
                  records: dict[str, dict], prior_points: list[tuple[str, str]]
                  ) -> str:
    body = [p for p in section["paragraphs"] if p["kind"] != "heading"]
    text = "\n\n".join(p["text"] for p in section["paragraphs"])
    low, high = section_target_length(text)
    earlier = ("\n".join(f"{h or '(preamble)'}: {pt}" for h, pt in prior_points)
               or "(this is the first section)")
    skeleton = "\n".join(
        f"[{p['ordinal']}] {records[p['para_hash']].get('gist') or '(no gist)'}"
        for p in body)
    numbered = "\n\n".join(f"[{p['ordinal']}] {p['text']}"
                            for p in section["paragraphs"])
    return (f"FILE: {file}\nSECTION: {section['heading'] or '(preamble)'}\n\n"
            f"EARLIER SECTIONS (their POINT lines, in order):\n{earlier}"
            "\n\nCONCEPTS (author's ratified definitions):\n"
            + _concept_slice(db, manuscript, file)
            + f"\n\nTARGET LENGTH: {low}-{high} words "
              f"(scaled to this section's {len(text.split())} words)."
            + f"\n\nSKELETON:\n{skeleton}"
            + f"\n\nCOVER THESE: {', '.join(str(p['ordinal']) for p in body)}"
            + f"\n\nTHE SECTION:\n\n{numbered}")


def _build_sections(db: Database, manuscript: dict, file: str, text: str,
                    llm: LLMClient, report: dict, *, verse_file: bool,
                    force: bool = False, **_ctx) -> None:
    """Fill the section rows one file's text lacks (§3.5 order: in file
    order, each call conditioned on the POINT lines of the sections
    before it). A heading-only section is stored without a call. A
    section whose paragraph records are not all current is skipped and
    reported (the paragraph level is retried by the next ensure);
    coverage misses are reported, never repaired."""
    mark_orphans(db, manuscript, file, text)
    have = {} if force else _current_section_rows(db, manuscript["id"], file)
    orphaned = {} if force else {r["section_hash"]: dict(r) for r in db.all(
        "SELECT * FROM section_summaries WHERE manuscript_id = ? "
        "AND file = ? AND status = 'orphaned'", (manuscript["id"], file))}
    records = {h: _record_of(r) for h, r in
               _current_paragraph_rows(db, manuscript["id"], file).items()}
    points: list[tuple[str | None, str]] = []
    for sec in sections_of(text, verse_file=verse_file):
        row = have.get(sec["section_hash"])
        if row is None and sec["section_hash"] in orphaned:
            # Its text came back (an edit undone): no call (§4.1).
            row = orphaned[sec["section_hash"]]
            db.update("section_summaries", row["id"],
                      {"status": "current",
                       "section_ordinal": sec["section_ordinal"]})
            report["resurrected"] += 1
        elif row is not None and row["section_ordinal"] != sec["section_ordinal"]:
            db.update("section_summaries", row["id"],
                      {"section_ordinal": sec["section_ordinal"]})
        if row is not None:
            points.append((sec["heading"], point_of(row["summary"])))
            continue
        body = [p for p in sec["paragraphs"] if p["kind"] != "heading"]
        if not body:
            row = store_section_summary(db, manuscript, file, sec,
                                        HEADING_ONLY_SUMMARY, "")
            report["headings_only"] += 1
            points.append((sec["heading"], point_of(row["summary"])))
            continue
        if any(p["para_hash"] not in records for p in body):
            report["skipped"].append((file, sec["section_ordinal"]))
            continue
        if not llm.enabled:
            raise RuntimeError(
                f"section summaries for {file} need the LLM, which is "
                "disabled ([llm] enabled)")
        summary = llm.complete(
            section_prompt(),
            _section_user(db, manuscript, file, sec, records, points))
        report["calls"] += 1
        if not summary or not summary.strip():
            report["skipped"].append((file, sec["section_ordinal"]))
            continue
        row = store_section_summary(db, manuscript, file, sec,
                                    summary, llm.model or "")
        report["sections"] += 1
        cov = moves_coverage(row["summary"], [p["ordinal"] for p in body])
        if not cov["complete"]:
            report["misses"].append((file, sec["section_ordinal"], cov))
        points.append((sec["heading"], point_of(row["summary"])))


def _section_line(report: dict) -> str:
    line = (f"summaries: section level rebuilt — {report['sections']} "
            f"section(s) in {report['calls']} call(s) over "
            f"{len(report['files'])} file(s)")
    if report["headings_only"]:
        line += f", {report['headings_only']} heading-only"
    if report["resurrected"]:
        line += f", {report['resurrected']} restored"
    if report["misses"]:
        line += (f"; {len(report['misses'])} section(s) with a coverage "
                 "miss: " + ", ".join(
                     f"{f} §{n} (" + ", ".join(
                         f"{k} {v}" for k, v in cov.items()
                         if k != "complete" and v) + ")"
                     for f, n, cov in report["misses"]))
    if report["skipped"]:
        line += f"; {len(report['skipped'])} section(s) skipped"
    return line


def _paragraph_line(report: dict) -> str:
    dropped = sum(report["dropped"].values())
    line = (f"summaries: paragraph level rebuilt — {report['records']} "
            f"record(s) in {report['calls']} call(s) over "
            f"{len(report['files'])} file(s)")
    if report["headings"]:
        line += f", {report['headings']} heading(s)"
    if report["tags"]:
        line += f", {report['tags']} tag-only line(s)"
    if report["resurrected"]:
        line += f", {report['resurrected']} restored"
    if dropped:
        line += f"; {dropped} item(s) dropped by the span/vocabulary check"
    if report["missing"]:
        line += f"; {len(report['missing'])} paragraph(s) got no record"
    return line


# -------------------------------------------------------- essay pass (§3.4)

def _norm_heading(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def section_coverage(summary: str, sections: list[dict]) -> dict:
    """The structural check that replaced `paragraph_coverage` (§3.3,
    §3.4): MOVES must cite every section with a body (by `[n]`, no range
    is read) and FLOW must name every heading. {section_count, missing:
    ordinals no move cites, unknown: ordinals cited that the essay lacks,
    flow_missing: headings FLOW does not name, complete}. Reported,
    never enforced."""
    body = {s["section_ordinal"] for s in sections
            if any(p["kind"] != "heading" for p in s["paragraphs"])}
    cited = {int(x) for x in _ORDINAL_RE.findall(
        _label_block(summary, "MOVES", ESSAY_LABELS))}
    flow = _norm_heading(_label_block(summary, "FLOW", ESSAY_LABELS))
    flow_missing = [s["heading"] for s in sections if s["heading"]
                    and _norm_heading(s["heading"].lstrip("#")) not in flow]
    missing = sorted(body - cited)
    unknown = sorted(cited - {s["section_ordinal"] for s in sections})
    return {"section_count": len(sections), "missing": missing,
            "unknown": unknown, "flow_missing": flow_missing,
            "complete": not (missing or unknown or flow_missing)}


def flow_of(summary: str) -> str:
    """The FLOW paragraph of a stored essay summary (the one-screen map,
    §8 `summarize outline`)."""
    return " ".join(_label_block(summary, "FLOW", ESSAY_LABELS).split())


def render_essay_block(reply: str, derived: dict[str, list[str]]
                       ) -> tuple[str, list[str]]:
    """The stored essay block (ruling 6): the model's four labels in
    `ESSAY_LABELS` order with the derived lists rendered under theirs as
    comma-separated terms ("none" when empty). Returns (block, the model
    labels that were absent — rendered as "none" and reported)."""
    found = {lb: _label_block(reply, lb, ESSAY_LABELS)
             for lb in ESSAY_MODEL_LABELS}
    missing = [lb for lb, body in found.items() if not body]
    parts = dict(found)
    for lb in DERIVED_LABELS:
        parts[lb] = ", ".join(derived.get(lb.lower(), [])) or "none"
    return ("\n".join(f"{lb}: {parts[lb] or 'none'}" for lb in ESSAY_LABELS),
            missing)


def _build_essay(db: Database, manuscript: dict, file: str, text: str,
                 llm: LLMClient, report: dict, *, verse_file: bool,
                 force: bool = False, upstream: bool = False,
                 texts: dict[str, str] | None = None, flight: dict | None = None,
                 **_ctx) -> None:
    """Rebuild one essay's row from its section summaries when its state
    says so: `missing`, `stale` (source or sections hash) or `deprecated`
    always; `upstream_stale` only with `upstream` (the deliberate pass);
    everything with `force`. An essay whose sections are not all current
    is skipped and reported (the section level is retried by the next
    ensure). Afterwards every later row is marked `upstream_stale` —
    mark, don't cascade — which a later file of the same pass clears by
    its own rebuild."""
    flight = flight or {}
    row = db.one(
        "SELECT * FROM essay_summaries WHERE manuscript_id = ? AND file = ?",
        (manuscript["id"], file))
    state = _state(row, text, flight.get(file, NOT_IN_FLIGHT))
    settled = ("fresh", "rewriting") + (() if upstream else ("upstream_stale",))
    if not force and state in settled:
        return
    secs = sections_of(text, verse_file=verse_file)
    have_s = _current_section_rows(db, manuscript["id"], file)
    missing = [s["section_ordinal"] for s in secs
               if s["section_hash"] not in have_s]
    if missing:
        report["skipped"].append(
            (file, f"section(s) {', '.join(map(str, missing))} have no "
                   "current summary"))
        return
    if not llm.enabled:
        raise RuntimeError(f"the essay summary of {file} needs the LLM, "
                           "which is disabled ([llm] enabled)")
    order = list(texts or {})
    idx = order.index(file)
    have = all_summaries(db, manuscript["id"])
    prior = [(f, have[f]["summary"]) for f in order[:idx] if f in have]
    new = summarize_unit(db, manuscript, file, text, prior, llm,
                         [(sec, have_s[sec["section_hash"]]) for sec in secs],
                         texts=texts)
    report["calls"] += 1
    report["built"].append(file)
    if not new["coverage"]["complete"]:
        report["misses"].append((file, new["coverage"]))
    if new["labels_missing"]:
        report["labels_missing"].append((file, new["labels_missing"]))
    for f in order[idx + 1:]:
        if f in have and not have[f]["upstream_stale"]:
            db.update("essay_summaries", have[f]["id"], {"upstream_stale": 1})
            report["marked"] += 1


def _essay_line(report: dict) -> str:
    line = (f"summaries: essay level rebuilt — {len(report['built'])} "
            f"essay(s) in {report['calls']} call(s)")
    if report["marked"]:
        line += f", {report['marked']} downstream marked upstream_stale"
    if report["misses"]:
        line += (f"; {len(report['misses'])} essay(s) with a coverage miss: "
                 + ", ".join(
                     f"{f} (" + ", ".join(
                         f"{k} {v}" for k, v in cov.items()
                         if k not in ("complete", "section_count") and v)
                     + ")" for f, cov in report["misses"]))
    if report["labels_missing"]:
        line += (f"; {len(report['labels_missing'])} essay(s) missing a "
                 "label: " + ", ".join(
                     f"{f} ({', '.join(labels)})"
                     for f, labels in report["labels_missing"]))
    if report["skipped"]:
        line += f"; {len(report['skipped'])} essay(s) skipped"
    return line


_LEVEL_BUILDERS = {
    "paragraph": (_build_file, _paragraph_line,
                  lambda: {"calls": 0, "records": 0, "headings": 0, "tags": 0,
                           "resurrected": 0, "missing": [], "dropped": {}}),
    "section": (_build_sections, _section_line,
                lambda: {"calls": 0, "sections": 0, "headings_only": 0,
                         "resurrected": 0, "misses": [], "skipped": []}),
    "essay": (_build_essay, _essay_line,
              lambda: {"calls": 0, "built": [], "marked": 0, "misses": [],
                       "labels_missing": [], "skipped": []}),
}


def _client_for(llm, level: str) -> LLMClient:
    if isinstance(llm, dict):
        if level not in llm:
            raise LookupError(f"no LLM client given for the {level} level")
        return llm[level]
    return llm


def _ensure_level(db: Database, manuscript: dict, llm: LLMClient, level: str,
                  texts: dict[str, str], flight: dict, files, echo, *,
                  force: bool = False, upstream: bool = False,
                  progress=None) -> dict:
    build, line_of, fresh = _LEVEL_BUILDERS[level]
    verse = verse_files_of(manuscript)
    report = {"level": level, "files": [], "unwritten": [], **fresh(),
              "line": None}
    for file, text in texts.items():
        if files is not None and file not in files:
            continue
        if flight.get(file, NOT_IN_FLIGHT) is None:
            # A writeup created it: no text to record yet (§14). Reported,
            # never summarized as empty.
            report["unwritten"].append(file)
            continue
        if progress:
            progress(file)
        before = json.dumps({k: v for k, v in report.items()
                             if k not in ("files", "unwritten")}, default=str)
        build(db, manuscript, file, text, llm, report, verse_file=file in verse,
              force=force, upstream=upstream, texts=texts, flight=flight)
        if json.dumps({k: v for k, v in report.items()
                       if k not in ("files", "unwritten")},
                      default=str) != before:
            report["files"].append(file)
    if report["files"]:
        stats = llm.stats_line()
        report["line"] = line_of(report) + (f" — {stats}" if stats else "")
        echo(report["line"])
    return report


def ensure(db: Database, manuscript: dict, llm, level: str = "paragraph",
           files: list[str] | None = None, echo=print, *,
           force: bool = False, upstream: bool = False, progress=None) -> dict:
    """Bring `level` up to date for `files` (every unit when None) and
    return its build report (§4.5, ruling 3): every level below it is
    brought up to date first, in `LEVELS` order, and only rows the text
    has and the table lacks are built, over ONE capture. Each level that
    built anything gives `echo` one line with its client's cost line
    (ruling 9); nothing is gated and nothing is asked.

    `llm` is one client for every level, or a dict {level: client} (see
    `clients`) so each level runs on its own model and prints its own
    cost line.

    Paragraph report: {level, files, calls, records, headings,
    resurrected, missing: [(file, ordinal)] the model returned no record
    for (still dirty, retried by the next ensure), dropped: {span,
    relation, role, direction, depends_on} items discarded by the §7
    checks, line}.

    Section report: {level, files, calls, sections, headings_only,
    resurrected (orphaned rows whose text came back, no call), misses: [(file, section_ordinal, coverage)] whose MOVES do not place
    every body paragraph exactly once (stored all the same), skipped:
    [(file, section_ordinal)] not built because a paragraph record is
    missing or the model answered nothing, line, paragraph: the lower
    level's report}.

    Essay report: {level, files, calls, built: [file] in reading order,
    marked (downstream rows flagged upstream_stale), misses: [(file,
    coverage)] whose MOVES or FLOW miss a section (stored all the same),
    labels_missing: [(file, [label])] the model left out (rendered
    "none"), skipped: [(file, why)] not built because a section summary
    is missing, line, section: the lower level's report}.

    `force` rebuilds every row of `level` itself (lower levels stay
    lazy); `upstream` also rebuilds essays whose conditioning moved
    (`upstream_stale`), which the on-use path tolerates. `progress(file)`
    is called before each file of `level` is examined."""
    if level not in LEVELS:
        raise ValueError(f"unknown summary level {level!r} "
                         f"(levels: {', '.join(LEVELS)})")
    texts, _unlisted, flight = _context_units(db, manuscript)
    unknown = sorted(set(files or ()) - set(texts))
    if unknown:
        raise LookupError(f"not a unit of the manuscript: {', '.join(unknown)}")
    lower = None
    for lv in LEVELS[:LEVELS.index(level) + 1]:
        top = lv == level
        report = _ensure_level(db, manuscript, _client_for(llm, lv), lv,
                               texts, flight, files, echo,
                               force=force and top, upstream=upstream and top,
                               progress=progress if top else None)
        if lower is not None:
            report[lower["level"]] = lower
        lower = report
    return report
