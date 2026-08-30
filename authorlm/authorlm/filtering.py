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


def _concept_notes(db: Database, manuscript: dict, file: str) -> str:
    from . import api

    try:
        graph = api.scoped_concepts(db, manuscript, file=file)
    except LookupError:
        return ""
    lines = []
    for node in sorted(graph.get("nodes", []),
                       key=lambda n: n.get("name") or ""):
        notes = (node.get("notes") or "").strip() or "(no notes)"
        lines.append(f"- {node['name']} — {notes}")
    return "\n".join(lines)


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
                 units: list[str]) -> str:
    """Block A — the second cache breakpoint.

    THE ESSAY and MOTIF REGISTRY appear for a GLOBAL filter only. The
    class is frozen at run start, so this is stable for the life of the
    run — and class resolution is observable in the bytes, which is what
    makes it testable rather than a claim about the code."""
    file = run["file"]
    sections = [
        _section("INTENTS (they govern which changes matter — never "
                 "whether a change is grounded, never what the essay "
                 "claims, and never who this work is filed under)",
                 _intents_block(db, manuscript, file)),
        _section("VALIDATED BELIEFS",
                 _validated_beliefs(db, manuscript["id"])),
        _section("CONCEPT NOTES (the author's settled definitions)",
                 _concept_notes(db, manuscript, file)),
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
             threads: list[dict]) -> Payload:
    """The whole payload, from stored state only.

    `units` comes from the caller's ONE capture of the manuscript (the
    one-capture-per-invocation convention): the text the model is sent
    must be the text the caller's own gates saw, not a second read a
    parallel session could have moved underneath it."""
    return Payload(
        law=_law_block(db, manuscript["id"], run["file"], artifact_body),
        frame=_frame_block(db, manuscript, run, units),
        prefix=_prefix_block(run, units, window[0], threads),
        units=_units_block(run, units, window),
    )


def assemble_prelude(db: Database, manuscript: dict, run: dict,
                     artifact_body: str, units: list[str]) -> Payload:
    """The prelude payload for a GLOBAL filter: the same block S, the
    whole essay, and the prelude's own contract. Blocks B and C exist
    and are labelled — a payload whose block count changes between the
    prelude and the units would be a different shape for the reader as
    well as for the cache."""
    return Payload(
        law=_law_block(db, manuscript["id"], run["file"], artifact_body),
        frame="\n".join([
            _section("INTENTS (they govern which changes matter — never "
                     "whether a change is grounded, never what the essay "
                     "claims, and never who this work is filed under)",
                     _intents_block(db, manuscript, run["file"])),
            _section("VALIDATED BELIEFS",
                     _validated_beliefs(db, manuscript["id"])),
            _section("CONCEPT NOTES (the author's settled definitions)",
                     _concept_notes(db, manuscript, run["file"])),
            _section(f"THE ESSAY — {run['file']} ({len(units)} units)",
                     _essay_block(units)),
        ]),
        prefix=_section("FILTERED PREFIX", NOT_APPLICABLE_GLOBAL),
        units=_section("THE PRELUDE",
                       "Read THE ESSAY whole and return the registry this "
                       "filter's own prompt defines. No unit is judged "
                       "yet.") + "\n" + PRELUDE_CONTRACT + "\n",
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
