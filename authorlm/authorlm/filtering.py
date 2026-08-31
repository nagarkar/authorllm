"""The filter payload seam — stored state in, four blocks out
(docs/filter-pass-design.md §3.2).

Mirrors `writing.py` exactly, including its one requirement:

    **identical stored state ⇒ byte-identical blocks S and A.**

The blocks are ordered by volatility and that order IS the cache design:
the two stable layers carry the cache breakpoints, so anything that
reorders or re-words them between window N and window N+1 forfeits the
whole prefix. Every serialization rule below is inherited verbatim from
the write path, because every one of them was learned the hard way and
every one applies here:

- validated beliefs are sorted by STATEMENT and print no confidence
  number. `list_beliefs` orders by `confidence DESC`, and confidence
  moves on every explained verdict — printing it, or sorting on it,
  silently re-bills the cached layer between windows (design F4).
- intents render tier label and statement only: no ids, no timestamps,
  no status.
- no row ids, no timestamps, no counters anywhere.
- an absent section prints `(none)` rather than disappearing — a section
  that comes and goes changes the block's shape, and shape changes are
  cache invalidations.

Two notes worth recording because they INVERT the write path's:

- `scoped_concepts(file=file)` works here and does not there.
  `writing.py` records that the file-scoped graph slice is empty during
  a writeup because the file is a placeholder (design F3). A filter runs
  on an INTACT essay, so file scoping is exactly right and the beat
  specs' declared-concepts workaround is unnecessary.
- There is no drafting context and no before/after summaries. A filter's
  concern is intra-essay and mechanical, and requiring
  `passes.summaries_ready` would make every filter run block on the
  book's summary state — §14.1's defect wearing a new hat. The price is
  stated rather than hidden: a filter's jurisdiction is ONE ESSAY, and
  the cross-essay question is a lens.

No database writes happen here.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from .db import Database, loads
from .passes import INTENT_TIER_RANK, UNKNOWN_TIER_RANK

NONE = "(none)"

# ---------------------------------------------- protected terms (§15.22)
#
# The line is VOCABULARY versus LABEL: a kind whose `name` is a word the
# author uses IN THE PROSE is protected; a kind whose `name` is a label
# the author gave to a MOVE IN AN ARGUMENT is not.
#
# `objection`, `example`, `question` and `syllogism` are excluded because
# their names are SENTENCES, not words — "Question about Supreme God",
# "God beyond reach as excuse". Listing those would protect the ordinary
# English inside them ("question", "about", "good"), and a protection
# list that protects ordinary words protects nothing, because the model
# stops believing it.
#
# The cost, stated: an `example` node may name a genuine recurring
# exemplum whose wording is load-bearing. The remedy is the right one
# rather than a workaround — the author re-kinds it to `concept`, which
# is what the kind taxonomy is for. A second protection axis that could
# disagree with the kind would be the ambiguity two directories exist to
# refuse.
PROTECTED_KINDS = frozenset({
    "concept", "metaphor", "mathematical_construct", "historical_reference",
})

# `declared` is protected even though the concept has not been found in
# any text yet: a declared name is one the author has RATIFIED and
# intends to use, and realization is a scan that may simply not have
# caught up. A declared name that never appears costs one line; a filter
# recasting a term the author declared last night is the expensive one.
#
# `retired` is excluded, and it is the ruling most worth stating out
# loud: a retired concept's name is vocabulary the author DELIBERATELY
# ABANDONED, and protecting it would freeze exactly the wording a
# duplicate-words pass should be free to recast. The synonyms that
# matter survive anyway — `concepts.merge_concepts` turns a retired
# duplicate's names into ALIASES of the canonical, so a genuine synonym
# stays protected through the live node.
PROTECTED_STATUSES = frozenset({"declared", "realized"})

PROTECTED_HEADER = (
    "PROTECTED TERMS (the author's vocabulary — never substitute a "
    "synonym for one, never re-word one, never change its "
    "capitalization). A single-word name written here with a capital is "
    "the term of art only where the text capitalizes it: \"Field\" is the "
    "concept, \"field\" is ordinary English. A multi-word name, and any "
    "name written here in lower case, is the term of art in any casing. "
    "A name followed by \"·\" carries alternate names; all of them are "
    "the same term.")

DICTIONARY_HEADER = (
    "PRONUNCIATION DICTIONARY (settled by the author — read these aloud "
    "this way, and never flag one of these terms as hard to say: the "
    "dictionary IS the fix)")

NOT_APPLICABLE_GLOBAL = (
    "(not applicable — this is a GLOBAL filter: each unit is judged "
    "against the whole essay above and the frozen registry, never "
    "against this run's own prefix)")

# The state cap (design Q2). Refusal, not truncation: a silently
# truncated ledger is a ledger that lies about what has been flagged.
# 4,000 characters is roughly 1,000 tokens — large enough for a ledger
# of every repeated word in a long essay, small enough that it can never
# be the biggest thing in block C.
STATE_CAP = 4000

OUTPUT_CONTRACT = """OUTPUT CONTRACT
Return JSON only — no prose before it, no prose after it, no markdown fence:
  {"units": [{"n": <int>, "echo": "<the unit's first five words, copied>",
              "action": "keep" | "replace",
              "new": "<the whole rewritten unit>",   // replace only
              "why": "<one sentence>",               // replace only
              "ref": "<what this names in the state or registry>"},  // optional
             ...],
   "state": "<the updated state>"}
One entry for every unit in THE UNITS, in order, none omitted. A mismatched
echo discards the WHOLE reply. Never emit << >> {{ or }} anywhere in the reply."""

PRELUDE_CONTRACT = """OUTPUT CONTRACT
Return JSON only, in this shape and nothing else:
  {"registry": "<the registry, as THE FILTER defines it>"}
The registry is frozen for the life of this run: every unit will see these
exact bytes. Never emit << >> {{ or }} anywhere in the reply."""

# The one legal value of the `prelude` front-matter key.
PRONUNCIATION_PRELUDE = "pronunciations"

HARD_TERMS_HEADER = (
    "HARD TERMS FOUND IN THIS ESSAY (computed, not judged — every one of "
    "these needs a pronunciation unless the dictionary already has it)")

PRONUNCIATION_CONTRACT = """OUTPUT CONTRACT
Return JSON only, in this shape and nothing else:
  {"pronunciations": [{"term": "<the term, copied from the essay>",
                       "say": "<plain respelling, e.g. uh-NUT-taa>",
                       "note": "<the language or the one thing worth saying>"}]}
No unit is judged here and the essay is not touched. A term that is not in
THE ESSAY verbatim, or that PRONUNCIATION DICTIONARY already carries,
discards the WHOLE reply. Never emit << >> {{ or }} anywhere in the reply."""

# Same doctrine as STATE_CAP: a prelude proposing 200 terms is a prompt
# fault, and a silently truncated list lies about what was found.
PRONUNCIATION_CAP = 60


@dataclass(frozen=True)
class Payload:
    """The four blocks, in volatility order. S and A are the cached
    prefix; B and C are re-read every window."""

    law: str        # block S — harness prompt + the filter + style law
    frame: str      # block A — intents, beliefs, graph, prior runs, essay
    prefix: str     # block B — the run's own filtered prefix
    units: str      # block C — this window, the state, the contract

    @property
    def system_blocks(self) -> list[str]:
        return [self.law]

    @property
    def user_blocks(self) -> list[str]:
        return [self.frame, self.prefix, self.units]

    @property
    def blocks(self) -> list[tuple[str, str]]:
        return [("S", self.law), ("A", self.frame),
                ("B", self.prefix), ("C", self.units)]

    @property
    def hashes(self) -> dict[str, str]:
        return {name: hashlib.sha256(text.encode()).hexdigest()
                for name, text in self.blocks}

    @property
    def sizes(self) -> dict[str, int]:
        return {name: len(text) for name, text in self.blocks}


class ReplyError(ValueError):
    """The reply violated the output contract. The WHOLE reply is
    discarded, never part of it (`passes.ContractError`'s standing rule):
    a partially admitted reply is a run whose conditioning nobody can
    reconstruct."""


# ------------------------------------------------------------- the prompt

def filter_run_prompt() -> str:
    from . import prompt_registry

    return prompt_registry.by_name("filter-run").text()


def prompt_location() -> str:
    from . import prompt_registry

    return prompt_registry.by_name("filter-run").location


# ------------------------------------------------------------- the blocks

def _section(label: str, body: str) -> str:
    body = (body or "").strip()
    return f"{label}\n{body or NONE}\n"


def _law_block(db: Database, manuscript_id: str, file: str,
               artifact_body: str) -> str:
    """Block S — the least volatile layer, and the system message.

    The harness prompt owns the grammar and the discipline; THE FILTER
    owns editorial law only. That division is what keeps the artifacts
    short enough for the author to actually ratify."""
    from . import styles as st

    return "\n".join([
        filter_run_prompt().rstrip("\n") + "\n",
        _section("THE FILTER (ratified by the author)", artifact_body),
        _section("STYLE LAW", st.render(db, manuscript_id, file)),
    ])


def _validated_beliefs(db: Database, manuscript_id: str) -> str:
    rows = db.all(
        "SELECT statement FROM editorial_beliefs "
        "WHERE manuscript_id = ? AND status = 'validated'",
        (manuscript_id,))
    return "\n".join(f"- {s}" for s in
                     sorted(row["statement"] for row in rows))


def _intents_block(db: Database, manuscript: dict, file: str) -> str:
    """The active intents whose scope covers this essay — tier label and
    statement only, sorted by tier rank then statement.

    Labelled the way `beat-draft.md` labels it: a goal governs SELECTION
    AND EMPHASIS, never grounding — and here, never attribution either.
    A metaphor filter genuinely benefits from knowing what the author is
    trying to do with the book. Crediting the sweep to that goal is a
    different thing entirely, and this pass does not do it."""
    from . import passes

    rows = passes.intents_in_scope(db, manuscript, file, "active")
    entries = []
    for row in rows:
        tier = passes.scope_tier(manuscript, file, row["scope"]) or "outside"
        entries.append((INTENT_TIER_RANK.get(tier, UNKNOWN_TIER_RANK),
                        row["statement"], tier))
    entries.sort(key=lambda e: (e[0], e[1]))
    return "\n".join(f"- [{tier}] {statement}"
                     for _rank, statement, tier in entries)


def _scoped(db: Database, manuscript: dict, file: str,
            text: str | None) -> dict:
    """The file-scoped graph slice, read from THE CAPTURE.

    `text` is the caller's captured essay text (`assemble`'s parameter),
    which `scoped_concepts` uses in place of a second disk read. Passing
    it is what makes CONCEPT NOTES and PROTECTED TERMS obey the
    one-capture rule: without it a parallel session's edit between window
    N and window N+1 changes block A and forfeits the cached prefix."""
    from . import api

    return api.scoped_concepts(db, manuscript, file=file, text=text)


def _concept_notes(db: Database, manuscript: dict, file: str,
                   text: str | None = None) -> str:
    try:
        graph = _scoped(db, manuscript, file, text)
    except LookupError:
        return ""
    lines = []
    for node in sorted(graph.get("nodes", []),
                       key=lambda n: n.get("name") or ""):
        notes = (node.get("notes") or "").strip() or "(no notes)"
        lines.append(f"- {node['name']} — {notes}")
    return "\n".join(lines)


def _is_protected(node: dict) -> bool:
    return (node.get("kind") in PROTECTED_KINDS
            and node.get("status") in PROTECTED_STATUSES)


def _names_of(node: dict) -> list[str]:
    return [node["name"], *(node.get("aliases") or [])]


def protected_terms(db: Database, manuscript: dict, file: str,
                    text: str | None = None,
                    dictionary: str = "") -> dict:
    """The deterministic derivation (§15.22 §1.1). No model, no prose
    parsing, no second vocabulary source.

    Three lists:

    1. IN THIS ESSAY — the file-scoped slice, gated on kind and status.
    2. THE BOOK'S LEXICON — the same gate over the whole manuscript,
       names and aliases only. It exists because a term of art reaches an
       essay through a quotation, an allusion or a cross-reference
       without ever having realized there, and the file-scoped slice
       cannot see that.
    3. The dictionary's own terms — a term the author has ruled on aloud
       is by construction a term of art, and joins the protected set even
       when no concept node carries it.

    Returns `{"in_essay": [(name, [alias, …]), …], "lexicon": [name, …],
    "all": [name, …]}`, every list `sorted()` on the RAW name — codepoint
    order, no locale, no case-folding, which is what makes the rendered
    block byte-identical across processes and platforms.

    Deliberately NOT filtered by capitalization. `concepts.mention_pattern`
    already carries the ratified rule for where a name is the term of art;
    the block states that rule in prose, once, in its header, and leaves
    the list COMPLETE underneath it. Pre-filtering would be a second
    implementation of `mention_pattern`'s split, free to drift from it —
    and it would drop every borrowed lower-case term (`anattā`,
    `ressentiment`, `upaya`) from the very protection they most need."""
    from . import pronunciations as pron

    try:
        scoped = _scoped(db, manuscript, file, text)
    except LookupError:
        scoped = {"nodes": []}
    in_essay = sorted(
        ((n["name"], sorted(n.get("aliases") or []))
         for n in scoped.get("nodes", []) if _is_protected(n)),
        key=lambda pair: pair[0])

    rows = db.all(
        "SELECT name, kind, status, aliases FROM concept_nodes "
        "WHERE manuscript_id = ?", (manuscript["id"],))
    lexicon: set[str] = set()
    for row in rows:
        node = {"name": row["name"], "kind": row["kind"],
                "status": row["status"],
                "aliases": loads(row["aliases"], [])}
        if _is_protected(node):
            lexicon.update(_names_of(node))

    dict_terms = pron.terms(dictionary)
    everything = set(lexicon) | set(dict_terms)
    for name, aliases in in_essay:
        everything.add(name)
        everything.update(aliases)
    return {"in_essay": in_essay,
            "lexicon": sorted(lexicon | set(dict_terms)),
            "all": sorted(everything)}


def _protected_block(terms: dict) -> str:
    """The rendered section. No counts, no ids, no timestamps, no
    statuses, no kinds — the whole serialization discipline this module
    inherits from the write path. Identical stored state ⇒ byte-identical
    section."""
    lines = ["IN THIS ESSAY"]
    if terms["in_essay"]:
        for name, aliases in terms["in_essay"]:
            suffix = (" · " + " · ".join(aliases)) if aliases else ""
            lines.append(f"- {name}{suffix}")
    else:
        lines.append(NONE)
    lines.append("THE BOOK'S LEXICON (every term of art in the manuscript "
                 "— one may reach this essay through a quotation or an "
                 "allusion)")
    if terms["lexicon"]:
        lines.extend(f"- {name}" for name in terms["lexicon"])
    else:
        lines.append(NONE)
    return "\n".join(lines)


def _dictionary_block(dictionary: str) -> str:
    from . import pronunciations as pron

    return "\n".join(pron.block_lines(dictionary))


def is_hard_to_say(term: str) -> bool:
    """F1 — the hard signal, harness-computed and never model-judged.

    A term containing, after NFC normalization, a character with
    `ord(c) > 127` WHOSE UNICODE CATEGORY STARTS WITH `L`. Letters only:
    `’` and `—` are punctuation and carry no phonetic information, so
    `Noether’s Theorem` is correctly NOT hard to say while `Bṛhadāraṇyaka`,
    `Nāgārjuna`, `anattā`, `Śūnyatā` and `οὐκ ὄν θεός` all are.

    This IS the Sponsor's "all standard non-english terms", and it needs
    no model at all."""
    import unicodedata

    return any(ord(c) > 127 and unicodedata.category(c).startswith("L")
               for c in unicodedata.normalize("NFC", term or ""))


def _occurs(term: str, text: str) -> bool:
    from .concepts import mention_pattern

    return bool(mention_pattern(term).search(text or ""))


def hard_terms(text: str, protected: list[str],
               dictionary: str = "") -> list[str]:
    """F1 over this essay: the protected names and aliases that occur in
    it, are absent from the dictionary, and carry a non-ASCII letter.
    Sorted, deduplicated, deterministic."""
    from . import pronunciations as pron

    settled = {pron.key(t) for t in pron.terms(dictionary)}
    return sorted({t for t in protected
                   if is_hard_to_say(t)
                   and pron.key(t) not in settled
                   and _occurs(t, text)})


def candidate_terms(text: str, protected: list[str],
                    dictionary: str = "") -> list[str]:
    """F2 — candidacy, harness-computed; difficulty, prompt-judged.

    Every protected term or alias occurring in this essay and absent from
    the dictionary, EXCEPT a single-word ASCII name whose lower-case form
    is also live in this essay's prose. That exception is the
    `Field`/`field`, `Test`/`test`, `Measure`/`measure` case: an ordinary
    English word doing duty as a term of art. It is a HOMOPHONE problem,
    which the audio filter already owns as a unit finding, and it is not
    a pronunciation problem — nobody needs to be told how to say
    "field"."""
    import re as _re

    from . import pronunciations as pron

    settled = {pron.key(t) for t in pron.terms(dictionary)}
    out = set()
    for term in protected:
        name = (term or "").strip()
        if not name or pron.key(name) in settled or not _occurs(name, text):
            continue
        if (" " not in name and name.isascii() and name[:1].isupper()
                and _re.search(rf"\b{_re.escape(name.lower())}\b",
                               text or "")):
            continue
        out.add(name)
    return sorted(out)


def parse_pronunciations(raw, text: str, dictionary: str = "") -> list[dict]:
    """The pronunciation prelude's reply. Every check REFUSES the WHOLE
    reply, never part of it — `validate_reply`'s standing rule, for the
    same reason: a partially admitted reply is a run whose conditioning
    nobody can reconstruct."""
    from . import pronunciations as pron

    if not isinstance(raw, dict):
        raise ReplyError("the prelude reply is not a JSON object")
    items = raw.get("pronunciations")
    if not isinstance(items, list):
        raise ReplyError(
            "the prelude must return a `pronunciations` list — one entry "
            "per term of this essay a narrator would stumble over, and an "
            "empty list when there are none.")
    if len(items) > PRONUNCIATION_CAP:
        raise ReplyError(
            f"{len(items)} pronunciations in one reply, over the "
            f"{PRONUNCIATION_CAP} cap. A prelude proposing that many "
            "terms is a PROMPT fault rather than an essay that hard. "
            "Refused rather than truncated: a silently truncated list "
            "lies about what was found.")
    settled = {pron.key(t) for t in pron.terms(dictionary)}
    seen: dict[str, str] = {}
    out = []
    for item in items:
        if not isinstance(item, dict):
            raise ReplyError(
                "every entry in `pronunciations` must be an object")
        term = str(item.get("term") or "").strip()
        say = str(item.get("say") or "").strip()
        note = str(item.get("note") or "").strip()
        if not term:
            raise ReplyError("a pronunciation with no term")
        if term not in (text or ""):
            # The anchoring law, applied to the prelude: a pronunciation
            # for a term that is not in the essay is a hallucination, and
            # the door's standing rule is that a producer which cannot
            # anchor gets nothing.
            raise ReplyError(
                f"'{term}' does not appear in the essay verbatim. A "
                "pronunciation for a term that is not there is a guess "
                "about a word this essay does not use — copy the term "
                "from THE ESSAY. The whole reply is refused.")
        if not say:
            raise ReplyError(
                f"'{term}' has no pronunciation. An entry with a term and "
                "nothing to say about it asks the author a question with "
                "no answer in it.")
        for field, value in (("term", term), ("say", say), ("note", note)):
            for marker in _MARKERS:
                if marker in value:
                    raise ReplyError(
                        f"'{term}': the {field} contains {marker!r}, which "
                        "is reserved grammar in this system (the "
                        "pending-change form). The whole reply is refused.")
            if "\n" in value or "\r" in value:
                raise ReplyError(
                    f"'{term}': the {field} carries a newline. A "
                    "dictionary row is ONE LINE — a multi-line cell does "
                    "not survive a Docs table round trip in a shape the "
                    "parser can trust.")
        key = pron.key(term)
        if key in seen:
            raise ReplyError(
                f"'{term}' and '{seen[key]}' are the same term (case and "
                "Unicode composition do not distinguish two rows). One "
                "entry per term.")
        if key in settled:
            raise ReplyError(
                f"'{term}' is already in PRONUNCIATION DICTIONARY. Those "
                "rows are SETTLED — additions only, and a reply that "
                "proposes to restate one is a reply that did not read "
                "block A.")
        seen[key] = term
        out.append({"term": term, "say": say, "note": note})
    return out


def protected_loss(old: str, new: str, protected: list[str]) -> list[str]:
    """Protected terms present in `old` and absent from `new`, in the
    casing the protection rule gives them. Deterministic, no model.

    Matching is `concepts.mention_pattern` — the SAME pattern the
    realization scan uses — so "Field" in `old` and "field" in `new`
    counts as a loss and "field"→"field" does not. One rule for "appears
    as a term of art" across the whole system, never a second one here.

    The harness supplies the list and does not enforce it; this is the
    single exception, and it WARNS rather than refusing (§15.22, D6). A
    legitimate recast can drop one of two mentions of a term, and a
    refusal discards the WHOLE reply for a judgment the harness is not
    entitled to make. Flipping it to a refusal is one line if the Sponsor
    wants it."""
    from .concepts import mention_pattern

    lost = []
    for name in protected:
        if not (name or "").strip():
            continue
        pattern = mention_pattern(name)
        if pattern.search(old or "") and not pattern.search(new or ""):
            lost.append(name)
    return lost


def prior_runs(db: Database, manuscript_id: str, filter_name: str,
               file: str, exclude_run_id: str | None = None) -> str:
    """M2 — the mechanism that does the real work of approximate
    idempotency (§1.6).

    Every SETTLED run of this filter on this file, with its tallies and
    the author's rejection reasons VERBATIM, rendered into the CACHED
    layer. The author's reasons are the highest-value evidence this
    system collects, and putting them in front of the model before it
    starts is the cheapest possible way to stop a filter re-proposing
    what the author already refused.

    The current run is excluded: a run cannot be evidence about itself."""
    rows = [dict(r) for r in db.all(
        "SELECT * FROM filter_runs WHERE manuscript_id = ? AND filter = ? "
        "AND file = ? AND status = 'settled' ORDER BY created_at, id",
        (manuscript_id, filter_name, file))]
    out = []
    for run in rows:
        if exclude_run_id and run["id"] == exclude_run_id:
            continue
        threads = [dict(t) for t in db.all(
            "SELECT * FROM doc_threads WHERE manuscript_id = ? AND "
            "origin_type = 'filter' AND file = ? AND origin_id LIKE ? "
            "ORDER BY created_at, id",
            (manuscript_id, file, f"{run['id']}:{file}:%"))]
        if not threads:
            continue
        accepted = sum(1 for t in threads
                       if t["state"] in ("accepted", "written", "cleaned"))
        rejected = sum(1 for t in threads
                       if t["state"] in ("rejected", "declined"))
        out.append(f"{(run['created_at'] or '')[:10]} — {len(threads)} "
                   f"proposed, {accepted} accepted, {rejected} rejected.")
        for t in threads:
            if t["state"] not in ("rejected", "declined"):
                continue
            meta = loads(t.get("metadata"), {}) or {}
            reason = (meta.get("author_reason") or "").strip()
            n = meta.get("anchor_paragraph")
            if reason:
                out.append(f"  rejected at n={n}: \"{reason}\"")
            else:
                out.append(f"  rejected at n={n}: (no reason recorded)")
    return "\n".join(out)


def _essay_block(units: list[str]) -> str:
    return "\n\n".join(f"[{n}] {u}" for n, u in enumerate(units, 1))


def _frame_block(db: Database, manuscript: dict, run: dict,
                 units: list[str], text: str | None = None,
                 dictionary: str = "") -> str:
    """Block A — the second cache breakpoint.

    THE ESSAY and MOTIF REGISTRY appear for a GLOBAL filter only. The
    class is frozen at run start, so this is stable for the life of the
    run — and class resolution is observable in the bytes, which is what
    makes it testable rather than a claim about the code.

    PROTECTED TERMS and PRONUNCIATION DICTIONARY sit between CONCEPT
    NOTES and PRIOR RUNS: graph-derived sections together, run-history
    and essay sections adjacent to each other. Both render for EVERY
    filter of EVERY class, always — a section that comes and goes is a
    shape change, and shape changes are cache invalidations. A `global`
    filter needs the protection as much as a sequential one, because a
    motif family's own name is a protected term and a `replace` that
    swaps it for a synonym is precisely the harm."""
    file = run["file"]
    sections = [
        _section("INTENTS (they govern which changes matter — never "
                 "whether a change is grounded, never what the essay "
                 "claims, and never who this work is filed under)",
                 _intents_block(db, manuscript, file)),
        _section("VALIDATED BELIEFS",
                 _validated_beliefs(db, manuscript["id"])),
        _section("CONCEPT NOTES (the author's settled definitions)",
                 _concept_notes(db, manuscript, file, text)),
        _section(PROTECTED_HEADER,
                 _protected_block(protected_terms(db, manuscript, file,
                                                  text, dictionary))),
        _section(DICTIONARY_HEADER, _dictionary_block(dictionary)),
        _section("PRIOR RUNS OF THIS FILTER ON THIS FILE",
                 prior_runs(db, manuscript["id"], run["filter"], file,
                            exclude_run_id=run["id"])),
    ]
    if run["class"] == "global":
        sections.append(_section(f"THE ESSAY — {file} ({len(units)} units)",
                                 _essay_block(units)))
        sections.append(_section("MOTIF REGISTRY (frozen for this run)",
                                 run["registry"] or ""))
    return "\n".join(sections)


def filtered_prefix(units: list[str], window_start: int,
                    threads: list[dict]) -> str:
    """The units before the window, AS THIS RUN HAS THEM.

    The run's own proposals, not the original text — this is what makes
    the pass autoregressive rather than N independent calls with a
    shared prompt. A word ledger is worthless otherwise: shown the
    ORIGINAL unit 3, unit 5 cannot know the run already removed the
    second "moreover" there, and it will remove it again — or decline to
    remove its own on the grounds that the word has appeared once."""
    by_unit = {}
    for t in threads:
        meta = loads(t.get("metadata"), {}) or {}
        n = meta.get("anchor_paragraph")
        if n and t["state"] not in ("withdrawn", "rejected", "declined"):
            by_unit[n] = t["proposed_new"]
    out = []
    for n in range(1, window_start):
        out.append(f"[{n}] {by_unit.get(n, units[n - 1])}")
    return "\n\n".join(out)


def _prefix_block(run: dict, units: list[str], window_start: int,
                  threads: list[dict]) -> str:
    """Block B — volatile, and it grows."""
    if run["class"] == "global":
        return _section("FILTERED PREFIX", NOT_APPLICABLE_GLOBAL)
    return _section("FILTERED PREFIX (units before this window, in the "
                    "form THIS RUN gave them)",
                    filtered_prefix(units, window_start, threads))


def _unit_lines(units: list[str], window: tuple[int, int]) -> str:
    from .passes import echo_of

    out = []
    for n in range(window[0], window[1] + 1):
        text = units[n - 1]
        heading = " (heading)" if is_heading(text) else ""
        out.append(f"[{n}]{heading} echo: {echo_of(text)}\n{text}")
    return "\n\n".join(out)


def is_heading(unit: str) -> bool:
    """A unit whose first line is a markdown heading. Deterministic and
    free, and it lets a prompt say "leave headings alone" without the
    harness enforcing a rule that belongs to editorial law."""
    first = (unit or "").lstrip().split("\n", 1)[0]
    stripped = first.lstrip("#")
    hashes = len(first) - len(stripped)
    return 1 <= hashes <= 6 and stripped[:1] in (" ", "\t")


def _units_block(run: dict, units: list[str],
                 window: tuple[int, int]) -> str:
    """Block C — everything that changes every window."""
    sections = []
    if run["class"] == "sequential":
        sections.append(_section("FILTER STATE (carry it forward and "
                                 "return its updated value)",
                                 run["state"] or ""))
    sections.append(_section(
        f"THE UNITS — {run['file']}, units {window[0]}–{window[1]} of "
        f"{run['unit_count']}", _unit_lines(units, window)))
    sections.append(OUTPUT_CONTRACT + "\n")
    return "\n".join(sections)


def assemble(db: Database, manuscript: dict, run: dict, artifact_body: str,
             units: list[str], window: tuple[int, int],
             threads: list[dict], text: str | None = None,
             dictionary: str = "") -> Payload:
    """The whole payload, from stored state only.

    `units` comes from the caller's ONE capture of the manuscript (the
    one-capture-per-invocation convention): the text the model is sent
    must be the text the caller's own gates saw, not a second read a
    parallel session could have moved underneath it. `text` is that same
    capture's essay text, handed down so the graph slice reads it too.

    `dictionary` is `pronunciations.md`'s text, read ONCE per invocation
    by `api._filter_capture` and handed to every consumer of that
    invocation. It is read LIVE from the file rather than pinned to the
    run's `source_version_id`, and that is the Sponsor's suppression
    ruling made mechanical: an accepted pronunciation must take effect at
    the NEXT WINDOW, not the next run. It sits in the stable layer, so a
    dictionary edit mid-run invalidates block A and re-bills the cached
    prefix — an HONEST invalidation, because the suppression set
    genuinely changed, and `filter run` prints the per-block hashes so it
    is visible rather than mysterious."""
    return Payload(
        law=_law_block(db, manuscript["id"], run["file"], artifact_body),
        frame=_frame_block(db, manuscript, run, units, text, dictionary),
        prefix=_prefix_block(run, units, window[0], threads),
        units=_units_block(run, units, window),
    )


def assemble_prelude(db: Database, manuscript: dict, run: dict,
                     artifact_body: str, units: list[str],
                     text: str | None = None, dictionary: str = "",
                     kind: str = "registry") -> Payload:
    """The prelude payload. Blocks B and C exist and are labelled — a
    payload whose block count changes between the prelude and the units
    would be a different shape for the reader as well as for the cache.

    `kind` is the prelude's ARTIFACT (§15.22 §3.1). A prelude is a
    whole-essay pass that runs before any unit is judged and produces a
    run-scoped artifact: for `global` that artifact is the frozen
    registry, rendered into block A; for a sequential filter that
    declares `prelude = "pronunciations"` it is a set of PROPOSALS, and
    nothing enters block A at all."""
    file = run["file"]
    terms = protected_terms(db, manuscript, file, text, dictionary)
    frame = [
        _section("INTENTS (they govern which changes matter — never "
                 "whether a change is grounded, never what the essay "
                 "claims, and never who this work is filed under)",
                 _intents_block(db, manuscript, file)),
        _section("VALIDATED BELIEFS",
                 _validated_beliefs(db, manuscript["id"])),
        _section("CONCEPT NOTES (the author's settled definitions)",
                 _concept_notes(db, manuscript, file, text)),
        _section(PROTECTED_HEADER, _protected_block(terms)),
        _section(DICTIONARY_HEADER, _dictionary_block(dictionary)),
        _section(f"THE ESSAY — {file} ({len(units)} units)",
                 _essay_block(units)),
    ]
    if kind == PRONUNCIATION_PRELUDE:
        essay = text if text is not None else "\n\n".join(units)
        body = _section(HARD_TERMS_HEADER,
                        "\n".join(f"- {t}" for t in hard_terms(
                            essay, terms["all"], dictionary)))
        units_block = (body + "\n" + PRONUNCIATION_CONTRACT + "\n")
    else:
        units_block = (_section("THE PRELUDE",
                                "Read THE ESSAY whole and return the "
                                "registry this filter's own prompt "
                                "defines. No unit is judged yet.")
                       + "\n" + PRELUDE_CONTRACT + "\n")
    return Payload(
        law=_law_block(db, manuscript["id"], file, artifact_body),
        frame="\n".join(frame),
        prefix=_section("FILTERED PREFIX", NOT_APPLICABLE_GLOBAL),
        units=units_block,
    )


# -------------------------------------------------------------- the reply

_MARKERS = ("<<", ">>", "{{", "}}")


def validate_reply(raw, units: list[str],
                   window: tuple[int, int], klass: str) -> dict:
    """§2.5's table, in one place. Every check REFUSES; none repairs, and
    the whole reply is discarded rather than part of it.

    Returns `{"edits": [...], "keeps": [n, …], "state": str | None}`
    where every edit's `old` will be taken from the SOURCE unit by the
    recorder — never from here, and never from the model."""
    from .passes import echo_of

    if not isinstance(raw, dict):
        raise ReplyError("the reply is not a JSON object")
    items = raw.get("units")
    if not isinstance(items, list):
        raise ReplyError("the reply carries no `units` list")
    expected = list(range(window[0], window[1] + 1))
    got_ns = []
    for item in items:
        if not isinstance(item, dict):
            raise ReplyError("every entry in `units` must be an object")
        got_ns.append(item.get("n"))
    if got_ns != expected:
        missing = [n for n in expected if n not in got_ns]
        extra = [n for n in got_ns if n not in expected]
        parts = []
        if missing:
            parts.append("missing n=" + ", ".join(str(n) for n in missing))
        if extra:
            parts.append("not in this window: n="
                         + ", ".join(str(n) for n in extra))
        if not parts:
            parts.append("the entries are out of order")
        raise ReplyError(
            f"one entry per unit in the window, in order — expected "
            f"n={expected[0]}..{expected[-1]}; " + "; ".join(parts)
            + ". Nothing was staged and the cursor did not move.")
    edits, keeps = [], []
    for item in items:
        n = item["n"]
        if not 1 <= n <= len(units):
            raise ReplyError(f"n={n} is out of range (the essay has "
                             f"{len(units)} units)")
        src = units[n - 1]
        want = echo_of(src)
        got = " ".join(str(item.get("echo", "")).split())
        if got != want:
            raise ReplyError(
                f"unit {n} echo mismatch: expected «{want}», got «{got}». "
                "The echo is how the reply is aligned to the essay — copy "
                "it from the unit, never retype it. The whole reply is "
                "discarded.")
        action = item.get("action")
        if action == "keep":
            keeps.append(n)
            continue
        if action == "insert":
            raise ReplyError(
                f"unit {n}: a filter never ADDS a unit. It passes each "
                "bit of the essay through one concern; a pass that wants "
                "to add a paragraph is a critique pass or a beat. Legal "
                "actions are 'keep' and 'replace'.")
        if action != "replace":
            raise ReplyError(f"unit {n}: unknown action {action!r} — "
                             "legal actions are 'keep' and 'replace'.")
        new = item.get("new")
        if not isinstance(new, str) or not new.strip():
            raise ReplyError(
                f"unit {n}: a replace with no text. \"Delete this "
                "paragraph\" is not a filter's judgment to make.")
        new = new.strip()
        for marker in _MARKERS:
            if marker in new:
                raise ReplyError(
                    f"unit {n}: the replacement contains {marker!r}, "
                    "which is reserved grammar in this system (the "
                    "pending-change form). The whole reply is refused.")
        if new == src:
            # A no-op replace is a keep — `validate_output`'s existing
            # rule, and the only check here that downgrades rather than
            # refusing, because nothing about it is a contract violation.
            keeps.append(n)
            continue
        edits.append({"n": n, "new": new,
                      "why": str(item.get("why") or "").strip(),
                      "ref": (str(item.get("ref")).strip()
                              if item.get("ref") else None)})
    state = raw.get("state")
    if state is not None and not isinstance(state, str):
        raise ReplyError("`state` is opaque TEXT this filter's own prompt "
                         "defines — not a structure.")
    if klass == "sequential" and not (state or "").strip():
        raise ReplyError(
            "a sequential filter must return its updated `state`: the "
            "next window is conditioned on it, and an empty one makes "
            "the run stop being autoregressive without saying so.")
    if state and len(state) > STATE_CAP:
        raise ReplyError(
            f"the state is {len(state):,} characters, over the "
            f"{STATE_CAP:,} cap. It has become a transcript rather than "
            "a ledger. Without the cap a long run's state grows without "
            "bound and eventually dominates the payload. Refused rather "
            "than truncated: a silently truncated ledger lies about what "
            "has been flagged.")
    return {"edits": edits, "keeps": keeps,
            "state": state if state else None}


def parse_prelude(raw) -> str:
    """The prelude's reply: `{"registry": "…"}`. Opaque text, stored and
    re-rendered, never parsed."""
    if not isinstance(raw, dict):
        raise ReplyError("the prelude reply is not a JSON object")
    registry = raw.get("registry")
    if not isinstance(registry, str) or not registry.strip():
        raise ReplyError(
            "the prelude must return a non-empty `registry` — it is the "
            "frozen coordination object every unit of a global run is "
            "judged against, and without it there is nothing keeping two "
            "independently judged units coherent.")
    registry = registry.strip()
    for marker in _MARKERS:
        if marker in registry:
            raise ReplyError(
                f"the registry contains {marker!r}, which is reserved "
                "grammar in this system.")
    return registry


def reply_json(text: str):
    """Parse a reply that arrived as text (the chat path writes the JSON
    into stdin). A fenced block is tolerated — the prompt forbids it,
    but a refusal the author has to fix by hand-editing JSON is friction
    for nothing, and the grammar is unambiguous either way."""
    body = (text or "").strip()
    if body.startswith("```"):
        body = body.split("\n", 1)[1] if "\n" in body else ""
        if body.rstrip().endswith("```"):
            body = body.rstrip()[:-3]
    try:
        return json.loads(body)
    except json.JSONDecodeError as err:
        head = body[:400]
        raise ReplyError(
            f"the reply is not valid JSON ({err}). The first 400 "
            f"characters of what came in:\n\n{head}") from None
