"""Essay summaries — the editor's working memory (critique-pass design §4).

One summary per toc unit, built autoregressively in reading order: each
is conditioned on every prior summary, so an edit pass over essay E can
hold "everything before E" and "everything after E" in a few thousand
tokens. Summaries are DERIVED machinery (like renders): they live in the
database, never in the manuscript or the Doc.

Staleness is marked, not cascaded. A collect that changes essay N leaves
N's summary with a stale `source_hash` (a lie about the text — the edit
pass refuses to run on it) and flags downstream rows `upstream_stale`
(tolerated). `critique resolve` rebuilds one essay's summary at its
confirmation gate, so processing in reading order keeps every "before"
summary fresh by construction; `summarize --rebuild` does the full pass.
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
    """THE UNIT's body with each paragraph prefixed by its 1-based
    number, so the prompt can require every paragraph be accounted for
    in MOVES by number — no paragraph silently dropped."""
    from .revisions import _paragraphs

    paras = _paragraphs(text)
    return "\n\n".join(f"[{i}] {p}" for i, p in enumerate(paras, 1)), len(paras)


# Two citation forms, and the CROSS-BRACKET one comes first on purpose.
# The prompt's example shows "[2-3]", but what the real summarizer writes
# is "[1]-[11]" — and reading that as two single citations counted every
# paragraph between them as missing (the live rebuild reported 350+
# uncited across 18 of 24 essays where the true figure was 11 of 1,138).
# Alternation is tried left to right at each position, so a cross-bracket
# range consumes both of its brackets and its endpoints are never also
# read as singles. Dashes: hyphen, en-dash, em-dash, any surrounding
# whitespace. Brackets are required either way — "3-4" in prose is prose.
_CITATION_RE = re.compile(
    r"\[(\d+)\]\s*[-–—]\s*\[(\d+)\]"
    r"|\[(\d+)(?:\s*[-–—]\s*(\d+))?\]"
)


def _cited_paragraphs(summary: str) -> set[int]:
    """Every paragraph number MOVES actually cited — single "[3]",
    in-bracket range "[2-3]", or cross-bracket range "[2]-[3]" — all
    collapsing to the same per-number set."""
    cited: set[int] = set()
    for m in _CITATION_RE.finditer(summary):
        if m.group(1) is not None:
            lo, hi = int(m.group(1)), int(m.group(2))
        else:
            lo = int(m.group(3))
            hi = int(m.group(4)) if m.group(4) else lo
        if hi < lo:
            lo, hi = hi, lo
        cited.update(range(lo, hi + 1))
    return cited


def paragraph_coverage(summary: str, n_paragraphs: int) -> dict:
    """VERIFY, don't just request, that MOVES cited every paragraph
    number 1..n_paragraphs at least once. Coverage is REPORTED, never
    enforced here: a summary short one transitional paragraph is still
    useful, so this never retries, fabricates, or blocks the rebuild —
    it records exactly which paragraphs were missed (or, if the model
    cited a number outside range, invented) so that's visible instead of
    silently assumed. A summary with `complete: True` can stand in for
    the essay as compressed context; one that isn't complete might have
    quietly dropped a move, and now that's known rather than guessed."""
    cited = _cited_paragraphs(summary)
    expected = set(range(1, n_paragraphs + 1))
    missing = sorted(expected - cited)
    out_of_range = sorted(n for n in cited if n < 1 or n > n_paragraphs)
    return {"paragraph_count": n_paragraphs,
            "cited": sorted(cited & expected),
            "missing": missing,
            "out_of_range": out_of_range,
            "complete": not missing and not out_of_range}


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


# How many paragraph numbers a coverage note will spell out before it
# summarizes the rest. Since cross-bracket ranges parse (Z/M1), ONE
# malformed blanket citation — "[1]-[100]" on a six-paragraph essay —
# yields 94 out-of-range numbers, and this note goes verbatim into the
# drafting payload. The count still tells the whole truth; only the
# enumeration is bounded.
COVERAGE_LIST_CAP = 8


def _paragraph_list(numbers: list[int]) -> str:
    head = ", ".join(f"¶{n}" for n in numbers[:COVERAGE_LIST_CAP])
    rest = len(numbers) - COVERAGE_LIST_CAP
    return head + (f" …and {rest} more" if rest > 0 else "")


def _coverage_note(entry: dict, text: str) -> str | None:
    """Item 4's loudness. Coverage is recomputed here rather than read
    from a column (`essay_summaries` stores none — summarize_unit
    computes it fresh too): the stored summary plus the essay's current
    paragraph count is all `paragraph_coverage` needs, and it is only
    trustworthy when the two actually correspond, i.e. when source_hash
    still matches. Reported, never blocking — the same tolerance editing
    has, made impossible to miss."""
    if entry["summary"] is None or entry["state"] not in ("fresh",
                                                          "upstream_stale",
                                                          "rewriting"):
        return None
    from .revisions import _paragraphs

    cov = paragraph_coverage(entry["summary"], len(_paragraphs(text)))
    if cov["complete"]:
        return None
    bits = []
    if cov["missing"]:
        bits.append(_paragraph_list(cov["missing"]) + " uncited")
    if cov["out_of_range"]:
        bits.append(_paragraph_list(cov["out_of_range"])
                    + f" cited but the essay has {cov['paragraph_count']} "
                      "paragraph(s)")
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


def summarize_unit(db: Database, manuscript: dict, file: str, text: str,
                   prior: list[tuple[str, str]], llm: LLMClient) -> dict:
    """Write (or rewrite) one unit's summary conditioned on `prior`
    [(file, summary), …]. Returns the stored row."""
    low, high = target_length(text)
    numbered, n_paras = _numbered_paragraphs(text)
    user = (
        "PRIOR SUMMARIES (reading order):\n" + _prior_block(prior)
        + "\n\nCONCEPTS (author's ratified definitions):\n"
        + _concept_slice(db, manuscript, file)
        + f"\n\nTARGET LENGTH: {low}-{high} words "
          f"(scaled to this unit's {len(text.split())} words)."
        + f"\n\nPARAGRAPH COUNT: {n_paras} — every paragraph number below "
          "must be cited at least once in MOVES."
        + f"\n\nTHE UNIT: {file}\n\n{numbered}"
    )
    summary = llm.complete(summarizer_prompt(), user)
    if not summary:
        raise RuntimeError(f"summarizer returned nothing for {file} "
                           "(LLM disabled or call failed)")
    summary = summary.strip()
    coverage = paragraph_coverage(summary, n_paras)
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
              "status": "current"}
    if existing:
        db.update("essay_summaries", existing["id"], fields)
        row_id = existing["id"]
    else:
        row = ko_fields("es")
        row.update(manuscript_id=manuscript["id"], file=file, **fields)
        db.insert("essay_summaries", row)
        row_id = row["id"]
    result = dict(db.one("SELECT * FROM essay_summaries WHERE id = ?", (row_id,)))
    # Not persisted (essay_summaries stores no such column): computed fresh
    # on every write so a caller can act on it immediately without a
    # schema change.
    result["paragraph_coverage"] = coverage
    return result


def rebuild(db: Database, manuscript: dict, llm: LLMClient,
            only_missing_or_stale: bool = False,
            progress=None) -> dict:
    """The full sequential pass in reading order. With
    `only_missing_or_stale`, fresh units are reused as conditioning
    context (their text unchanged) rather than re-summarized — but a
    stale unit forces every unit AFTER it to rebuild too, since their
    conditioning changed (this is the one place cascade is correct:
    the author asked for truth from scratch).

    Reads CONTEXT truth: an in-flight file is summarized from the PINNED
    text pulled out of `manuscript_versions`, never from the placeholder
    on disk — which is what makes the refusal's printed remedy actually
    work. A file a writeup CREATED has no pre-rewrite text at all, so it
    is skipped and reported rather than quietly summarized as empty.

    **ONE snapshot, taken before the first model call.** A full rebuild
    runs for minutes, and another session can truncate an essay at any
    point during it. Re-reading each unit as its turn came would let a
    truncation land between the pass starting and that unit being read,
    and the unit would then be summarized as EMPTY and stored as fresh —
    the exact incident this design exists to prevent, wearing a
    stopwatch. Every unit is summarized from the text captured here, and
    the stored `source_hash` is the hash of THAT text.

    A file that becomes in-flight mid-run is safe by the same token: the
    snapshot holds its pre-truncation text, which is exactly the text the
    writeup went on to pin — so the hash stored here is the hash the
    in-flight logic looks for, and the entry settles at `rewriting`
    rather than at `stale`."""
    have = all_summaries(db, manuscript["id"])
    texts, _unlisted, flight = _context_units(db, manuscript)
    prior: list[tuple[str, str]] = []
    built, reused, skipped_inflight = [], [], []
    incomplete_coverage: dict[str, dict] = {}
    cascade = False
    for file, text in texts.items():
        pinned = flight.get(file, NOT_IN_FLIGHT)
        if pinned is None:
            # `unwritten`: nothing to summarize, and nothing to condition
            # anything else on either — so it does not join `prior`.
            skipped_inflight.append(file)
            continue
        row = have.get(file)
        fresh = _state(row, text, pinned) == "fresh"
        if only_missing_or_stale and fresh and not cascade:
            prior.append((file, row["summary"]))
            reused.append(file)
            continue
        cascade = True
        if progress:
            progress(file)
        new = summarize_unit(db, manuscript, file, text, prior, llm)
        prior.append((file, new["summary"]))
        built.append(file)
        cov = new.get("paragraph_coverage")
        if cov and not cov["complete"]:
            incomplete_coverage[file] = cov
    return {"built": built, "reused": reused,
            "skipped_inflight": skipped_inflight,
            "incomplete_coverage": incomplete_coverage}


def rebuild_one(db: Database, manuscript: dict, file: str,
                llm: LLMClient) -> dict:
    """Rebuild one unit's summary against the CURRENT prior summaries
    (the confirmation-gate rebuild), then mark everything downstream
    upstream_stale — mark, don't cascade.

    Reads CONTEXT truth, as `rebuild` does. Silently summarizing an empty
    file is the failure the in-flight states exist to prevent, so it must
    not be reachable through the singular verb either: a file a writeup
    CREATED raises rather than summarizing nothing.

    ONE capture, as in `rebuild`. This used to read the disk TWICE (once
    for the order, once for the texts), which is two chances for a
    parallel writer to interleave and for the order and the texts to
    disagree about the same manuscript."""
    texts, _unlisted, flight = _context_units(db, manuscript)
    order = list(texts)
    if file not in order:
        raise LookupError(f"'{file}' is not in the manuscript's reading order")
    if flight.get(file, NOT_IN_FLIGHT) is None:
        raise LookupError(
            f"'{file}' is being written for the first time by an active "
            f"writeup — there is no pre-rewrite text to summarize, so a "
            f"rebuild would summarize an empty file and store it as fresh. "
            f"See 'write status'; its summary is built after "
            f"'write complete'.")
    have = all_summaries(db, manuscript["id"])
    idx = order.index(file)
    prior = [(f, have[f]["summary"]) for f in order[:idx] if f in have]
    new = summarize_unit(db, manuscript, file, texts[file], prior, llm)
    for f in order[idx + 1:]:
        if f in have:
            db.update("essay_summaries", have[f]["id"], {"upstream_stale": 1})
    return new


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
