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
              "status": "current", "model": llm.model or "",
              "sections_hash": sections_hash(
                  [sec["section_hash"] for sec in sections_of(text)])}
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


# ===================================================================
# The summary hierarchy (docs/summary-hierarchy-design.md): paragraph
# records and section summaries beneath the essay row. This part of the
# module makes NO model call. It owns the units (§0 ruling 1), the kinds
# (§3.2), the hashes that are each level's identity (§4), freshness
# derived at read time (§4.4) and the read API consumers use (§7). The
# builders that fill the tables are §11 steps 2–4.
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


def _first_mentions(db: Database, manuscript: dict) -> dict[str, str]:
    """term (lower-cased) → the first file in reading order with an
    `introduces | defines` mention of it, over current rows. The input to
    §6.6's INTRODUCES / ASSUMES split."""
    first: dict[str, str] = {}
    for rec in paragraph_records(db, manuscript):
        for m in rec["mentions"]:
            if m["role"] in ("introduces", "defines"):
                first.setdefault(m["term"].lower(), rec["file"])
    return first


def derived_lists(db: Database, manuscript: dict, file: str,
                  records: list[dict] | None = None) -> dict[str, list[str]]:
    """INTRODUCES, ASSUMES, PROMISES, DEBTS for one essay, from paragraph
    records (ruling 6, §6.6), as lists. INTRODUCES: terms this essay
    introduces or defines with no earlier such mention in reading order.
    ASSUMES: terms it only uses whose first introduction lies elsewhere
    or nowhere. PROMISES: forward references' terms; DEBTS: backward
    references' terms. Normalization against the graph is the consumer's
    step (`manifest._canonical_names`), as §7 guarantee 3 says."""
    if records is None:
        records = paragraph_records(db, manuscript, files=[file])
    first = _first_mentions(db, manuscript)
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


def for_unit(db: Database, manuscript: dict, file: str, text: str | None = None
             ) -> dict:
    """The three levels for one essay, projected for a consumer (§7's
    table; extraction-redesign.md §4.3). Read-only over `current` rows.

    A level with no rows at all is `None`. A level with rows is returned
    with its hash per row and `stale: True` when some unit of the text
    has no row — a paragraph or section without one carries `gist` /
    `summary` `None` so the consumer can print STALE against it. The
    essay carries `_state`'s verdict as `state`."""
    if text is None:
        texts, _unlisted, _flight = _context_units(db, manuscript)
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
            paragraph_records(db, manuscript, files=[file], texts={file: text})))
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


def mentions(db: Database, manuscript: dict, term: str | None = None
             ) -> list[dict]:
    """Every mention across the book in reading order, flattened for the
    recurrence query (ruling 7): {term, role, span, file, para_hash,
    ordinal, section_ordinal}. `term` filters case-insensitively on the
    author's spelling."""
    want = term.lower() if term else None
    out = []
    for rec in paragraph_records(db, manuscript):
        for m in rec["mentions"]:
            if want is not None and m["term"].lower() != want:
                continue
            out.append({"term": m["term"], "role": m["role"],
                        "span": m["span"], "file": rec["file"],
                        "para_hash": rec["para_hash"],
                        "ordinal": rec["ordinal"],
                        "section_ordinal": rec["section_ordinal"]})
    return out
