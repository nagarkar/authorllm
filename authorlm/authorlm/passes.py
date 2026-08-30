"""The critique pass — essay-by-essay edit machinery (design §5–§7).

Per essay: propose → triage (verdicts only) → diff-write to the Doc →
the pause (author post-edits) → explicit resolve → rebuild summary →
advance. The file is only ever pristine or fully resolved.

This module owns the state machine, the preflight gate, the context
package, the output contract with echo validation, staging as
doc_threads (origin_type='critique'), triage verdicts, and the resolve /
rollback logic. Drive I/O is delegated to gdocs (CLI-gated, never MCP).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .db import Database, ko_fields, loads
from .llm import LLMClient, resolve_temperature
from . import summaries as sums
from . import threads as th

PROMPT_PATH = Path(__file__).parent / "prompts" / "editor.md"
ESSAY_STATES = ("pending", "proposed", "triaged", "written", "resolved",
                "skipped")
ECHO_WORDS = 5


def editor_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def editor_llm(config: dict) -> LLMClient:
    """The frontier-tier client for editorial judgment. `[critique]
    editor_model` overrides the general `[llm] model`. `[critique]
    temperature` (AE-3), if set, likewise overrides `[llm]
    temperature`/the constant — resolve_temperature (llm.py) is the one
    place that fallback chain is written."""
    llm = LLMClient(config)
    llm.purpose = "critique.editor"         # the usage ledger's key (AP)
    override = (config.get("critique", {}) or {}).get("editor_model")
    if override:
        llm.model = override
    llm.temperature = resolve_temperature(config, "critique")
    return llm


# ------------------------------------------------------------ pass rows

def active_pass(db: Database, manuscript_id: str) -> dict | None:
    row = db.one("SELECT * FROM critique_passes WHERE manuscript_id = ? "
                 "AND status = 'active'", (manuscript_id,))
    return dict(row) if row else None


def ensure_pass(db: Database, manuscript_id: str, source_id: str) -> dict:
    """One active pass per manuscript; created lazily by the first run."""
    existing = active_pass(db, manuscript_id)
    if existing:
        return existing
    row = ko_fields("cp")
    row.update(manuscript_id=manuscript_id, source_id=source_id, cursor=0,
               essay_state="{}", pinned_versions="{}", status="active")
    db.insert("critique_passes", row)
    return row


def essay_state(pass_row: dict, file: str) -> str:
    return loads(pass_row["essay_state"], {}).get(file, "pending")


def set_essay_state(db: Database, pass_row: dict, file: str,
                    state: str) -> None:
    assert state in ESSAY_STATES, state
    states = loads(pass_row["essay_state"], {})
    states[file] = state
    db.update("critique_passes", pass_row["id"],
              {"essay_state": json.dumps(states)})
    pass_row["essay_state"] = json.dumps(states)


def pin_version(db: Database, pass_row: dict, file: str,
                version_id: str) -> None:
    pins = loads(pass_row["pinned_versions"], {})
    pins[file] = version_id
    db.update("critique_passes", pass_row["id"],
              {"pinned_versions": json.dumps(pins)})
    pass_row["pinned_versions"] = json.dumps(pins)


def pinned_version(pass_row: dict, file: str) -> str | None:
    return loads(pass_row["pinned_versions"], {}).get(file)


def status(db: Database, manuscript: dict) -> dict:
    """The pass at a glance: per-essay state in reading order, cursor,
    staged-proposal counts."""
    p = active_pass(db, manuscript["id"])
    order = [f for f, _ in sums.units(manuscript)]
    rows = []
    for i, f in enumerate(order):
        st = essay_state(p, f) if p else "pending"
        counts = {}
        if p:
            for r in db.all(
                    "SELECT state, COUNT(*) AS n FROM doc_threads WHERE "
                    "manuscript_id = ? AND origin_type = 'critique' AND "
                    "file = ? GROUP BY state", (manuscript["id"], f)):
                counts[r["state"]] = r["n"]
        rows.append({"index": i, "file": f, "state": st, "threads": counts,
                     "at_cursor": bool(p) and p["cursor"] == i})
    return {"pass": p, "essays": rows,
            "cursor_file": order[p["cursor"]] if p and p["cursor"] < len(order)
            else None}


# ------------------------------------------------------- scope & gate

def toc_ancestors(manuscript: dict, file: str) -> list[str]:
    """The file's parent chain from toc.toml, nearest first."""
    from .revisions import read_manuscript_files
    from .structure import _toc_chapters, TOC_FILENAME

    files = read_manuscript_files(Path(manuscript["path"]))
    parents = {c["file"]: c.get("parent")
               for c in _toc_chapters(files.get(TOC_FILENAME) or "")}
    chain, cur = [], parents.get(file)
    while cur and cur not in chain:
        chain.append(cur)
        cur = parents.get(cur)
    return chain


def scope_chain(manuscript: dict, file: str) -> list[str | None]:
    """file, its toc ancestors, then None (manuscript-wide)."""
    return [file, *toc_ancestors(manuscript, file), None]


# The one ordering of the scope tiers, from most specific to least.
# `scope_tier` produces these words, so the rank that sorts them lives
# beside it rather than being spelled once in `api` (primary selection)
# and again in `writing` (block A's INTENTS section) — two copies of the
# same fact, one of which is inside the byte-stability guarantee. Both
# import it from here; `passes` imports neither of them.
# "outside" is not a derived tier: it is what an intent the author named
# explicitly gets when its scope does not cover this file.
INTENT_TIER_RANK = {"file": 0, "chapter": 1, "manuscript": 2, "outside": 3}
UNKNOWN_TIER_RANK = 9


def scope_tier(manuscript: dict, file: str,
               scope: str | None) -> str | None:
    """Where a scope sits relative to this file: `file`, `chapter` (any
    toc ancestor, however many levels up), or `manuscript` (no scope at
    all). None when the scope names a file this one is not under — an
    intent the author reached for explicitly, which is legitimate and is
    noted rather than refused (design-intent-scope §1.3)."""
    if scope is None:
        return "manuscript"
    chain = scope_chain(manuscript, file)
    if scope not in chain:
        return None
    return "file" if chain.index(scope) == 0 else "chapter"


def scope_specificity(manuscript: dict, file: str,
                      scope: str | None) -> int:
    """How specific a scope is to this file — smaller is more specific.
    The chain is already ordered nearest-first, so the nearest chapter
    ancestor beating a further one is free."""
    chain = scope_chain(manuscript, file)
    return chain.index(scope) if scope in chain else len(chain)


def intents_in_scope(db: Database, manuscript: dict, file: str,
                     status: str = "active") -> list[dict]:
    chain = scope_chain(manuscript, file)
    rows = db.all(
        "SELECT * FROM declared_intents WHERE manuscript_id = ? AND status = ?",
        (manuscript["id"], status))
    return [dict(r) for r in rows if r["scope"] in chain]


def preflight(db: Database, manuscript: dict, file: str,
              pass_row: dict | None = None) -> dict:
    """The homework check: anything unconfirmed touching this essay's
    contract. The pass refuses to run while any exist (design §5.1);
    --force runs WITHOUT them — unconfirmed items never enter the
    context package under any flag.

    Candidate beliefs: only those seeded SINCE THIS PASS BEGAN count
    (the pass's own verdicts distil into candidates the author should
    ratify before the next essay). The manuscript's long tail of older
    candidates was never gate material — a live record carries a hundred
    of them, and a literal reading would make every run refuse forever."""
    from .styles import guide_chain

    mid = manuscript["id"]
    since = pass_row["created_at"] if pass_row else None
    proposed_intents = intents_in_scope(db, manuscript, file, "proposed")
    guides = [g["id"] for g in guide_chain(db, mid, file)]
    proposed_elements = [dict(r) for r in db.all(
        "SELECT * FROM style_laws WHERE manuscript_id = ? AND "
        "status = 'proposed' AND (file = ? OR guide_id IN (%s))"
        % ",".join("?" * len(guides)) if guides else
        "SELECT * FROM style_laws WHERE manuscript_id = ? AND "
        "status = 'proposed' AND file = ?",
        (mid, file, *guides) if guides else (mid, file))]
    candidate_beliefs = [dict(r) for r in db.all(
        "SELECT * FROM editorial_beliefs WHERE manuscript_id = ? AND "
        "status = 'candidate' AND created_at >= ?",
        (mid, since or "9999"))]
    blockers = {"proposed_intents": proposed_intents,
                "proposed_elements": proposed_elements,
                "candidate_beliefs": candidate_beliefs}
    return {"clear": not any(blockers.values()), **blockers}


def summaries_ready(db: Database, manuscript: dict, file: str,
                    placement: str | None = None, capture=None) -> dict:
    """Missing summaries, a stale (source_hash) one, or a deprecated one
    anywhere in the before/after context blocks the run; upstream_stale
    is tolerated. A deprecated row belongs to an essay that left the toc
    and returned (summaries._state): the DB says it is not a live
    summary, so it is no more readable than a stale one.

    `placement` is the drafting gate's case (design §12.4 item 3): the
    context of an essay whose toc entry is not committed yet. It also
    validates the placement, so `write start --after <nonsense>` is
    refused here — before anything is truncated.

    `capture` is the caller's single snapshot of the manuscript's context
    text (summaries.capture). Passing it is what makes the gate judge
    EXACTLY the text the context assembled afterwards will carry — a
    second read here could see a parallel session's truncation and open a
    window between the two."""
    before, after = sums.before_after(db, manuscript, file,
                                      placement=placement, capture=capture)
    entries = before + after
    missing = [e["file"] for e in entries if e["state"] == "missing"]
    stale = [e["file"] for e in entries if e["state"] == "stale"]
    deprecated = [e["file"] for e in entries if e["state"] == "deprecated"]
    # In-flight entries (design §14.2) are ACCEPTED — `rewriting` serves
    # the pre-rewrite summary, `unwritten` serves nothing and says so —
    # but they are reported, because both callers must warn about them
    # and one of them changes its refusal wording when a refused file is
    # also in flight. Both gates read this one function, so they cannot
    # drift apart about a row.
    refused = set(missing) | set(stale) | set(deprecated)
    inflight = [e["file"] for e in entries if e.get("in_flight")]
    return {"ok": not missing and not stale and not deprecated,
            "missing": missing, "stale": stale, "deprecated": deprecated,
            "inflight": inflight,
            "inflight_refused": [f for f in inflight if f in refused],
            "before": before, "after": after}


def summaries_message(ready: dict, pass_name: str) -> str:
    """The precise refusal for a blocked `summaries_ready` — names every
    offending file and the one command that fixes it. Shared so the
    drafting gate (api.write_start) and the edit pass refuse in the same
    words; there is no --force for either (a lie about the text is not
    something an author can consent past)."""
    parts = []
    if ready["missing"]:
        parts.append(f"missing summaries: {', '.join(ready['missing'])}")
    if ready["stale"]:
        parts.append("stale summaries (text changed): "
                     f"{', '.join(ready['stale'])}")
    if ready["deprecated"]:
        parts.append("deprecated summaries (the essay left the toc and came "
                     f"back): {', '.join(ready['deprecated'])}")
    message = ("; ".join(parts) + " — run 'summarize rebuild' first "
               f"(the {pass_name} will not read a lie about the text).")
    # Without this, "run summarize rebuild" reads, for a file that is
    # mid-rewrite, like an instruction to summarize the placeholder. It
    # does not: the rebuild reads the pinned version. Say so, or the
    # remedy looks destructive and the author will not run it.
    flying = ready.get("inflight_refused") or []
    if flying:
        message += (f" {', '.join(flying)} is mid-rewrite: the rebuild will "
                    "summarize its PRE-REWRITE text from the writeup's "
                    "pinned version, not the placeholder on disk.")
    return message


# ---------------------------------------------------- context package

def paragraphs_of(text: str) -> list[str]:
    from .revisions import _paragraphs
    return _paragraphs(text)


def echo_of(paragraph: str) -> str:
    return " ".join(paragraph.split()[:ECHO_WORDS])


def learnings(db: Database, pass_row: dict) -> list[str]:
    """Verdict evidence recorded earlier in this pass, newest last."""
    rows = db.all(
        "SELECT target, signal FROM evidence WHERE manuscript_id = ? AND "
        "evidence_type = 'critique_edit' AND created_at >= ? "
        "ORDER BY created_at", (pass_row["manuscript_id"],
                                pass_row["created_at"]))
    return [f"{r['signal']}: {r['target']}" for r in rows][-40:]


def build_context(db: Database, manuscript: dict, file: str,
                  pass_row: dict, force: bool = False) -> dict:
    """Assemble everything the editor model sees. Raises with a precise
    message when the gate or the summaries block the run."""
    from .styles import render as render_style
    from . import api

    gate = preflight(db, manuscript, file, pass_row)
    if not gate["clear"] and not force:
        raise RuntimeError(_gate_message(gate))
    # ONE capture for this whole invocation: the in-flight check, the
    # summaries gate, and the paragraph list the editor model actually
    # edits all read it. Separate reads would let a parallel session
    # change an essay between the gate passing and the context being
    # built, which is precisely the lie the gate exists to prevent.
    capture = sums.capture(db, manuscript)
    texts, _unlisted, inflight_now = capture
    # The essay this pass would edit is itself mid-rewrite: what is on
    # disk is the placeholder, not the essay, so the editor model would
    # be handed a marker as "the text" and would propose edits to it.
    # build_context mutates nothing, so the order here is free.
    if file in inflight_now:
        raise RuntimeError(
            f"{file} is being rewritten right now by an active writeup — "
            f"what is on disk is a placeholder, not the essay, so an edit "
            f"pass over it would propose edits to a marker. Finish the "
            f"writeup ('write complete') or put the old essay back "
            f"('write abandon'). To refresh its summary from the pinned "
            f"pre-rewrite text meanwhile: 'summarize rebuild {file}'.")
    ready = summaries_ready(db, manuscript, file, capture=capture)
    if not ready["ok"]:
        raise RuntimeError(summaries_message(ready, "edit pass"))
    if file not in texts:
        raise LookupError(f"'{file}' is not in the manuscript's reading order")
    # Disk text for the target: the refusal above has already proved this
    # file is not in flight, so the capture carries no overlay for it.
    paragraphs = paragraphs_of(texts[file])
    intents = intents_in_scope(db, manuscript, file, "active")
    beliefs = [dict(r) for r in db.all(
        "SELECT * FROM editorial_beliefs WHERE manuscript_id = ? AND "
        "status = 'validated' ORDER BY confidence DESC", (manuscript["id"],))]
    try:
        concepts = api.scoped_concepts(db, manuscript, file=file)
    except LookupError:
        concepts = {"nodes": [], "edges": []}
    return {
        "file": file, "paragraphs": paragraphs,
        "style": render_style(db, manuscript["id"], file),
        "intents": intents, "beliefs": beliefs, "concepts": concepts,
        "before": ready["before"], "after": ready["after"],
        "inflight": ready["inflight"],
        "learnings": learnings(db, pass_row),
        "gate": gate, "forced": force and not gate["clear"],
    }


def _gate_message(gate: dict) -> str:
    lines = ["preflight: unconfirmed items touch this essay's contract — "
             "triage them first ('critique triage --scope …'), or --force "
             "to run WITHOUT them (they will not be in the pass either way)."]
    for it in gate["proposed_intents"]:
        lines.append(f"  proposed intent [{it['id'][:8]}] "
                     f"({it['scope'] or 'manuscript'}): "
                     f"{it['statement'][:80]}")
    for el in gate["proposed_elements"]:
        lines.append(f"  proposed style element [{el['id'][:8]}]: "
                     f"{el['statement'][:80]}")
    for pol in gate["candidate_beliefs"]:
        lines.append(f"  candidate belief [{pol['id'][:8]}]: "
                     f"{pol['statement'][:80]}")
    return "\n".join(lines)


def render_user_message(ctx: dict) -> str:
    """The prompt's user turn, in the order the system prompt promises."""
    def block(title, body):
        return f"=== {title} ===\n{body.strip() or '(none)'}\n"

    intents = "\n".join(
        f"- [{i['id']}] ({i['scope'] or 'manuscript-wide'}) {i['statement']}"
        for i in ctx["intents"])
    beliefs = "\n".join(f"- {p['statement']}" for p in ctx["beliefs"])
    concepts = "\n".join(
        f"- {n['name']}" + (f" — {n['notes']}" if n.get("notes") else "")
        for n in ctx["concepts"].get("nodes", []))
    learn = "\n".join(f"- {line}" for line in ctx["learnings"])
    before = "\n\n".join(f"[{e['file']}]\n{e['summary']}"
                         for e in ctx["before"] if e["summary"])
    after = "\n\n".join(f"[{e['file']}]\n{e['summary']}"
                        for e in ctx["after"] if e["summary"])
    essay = "\n\n".join(f"[{n}] {p}" for n, p in
                        enumerate(ctx["paragraphs"], 1))
    return "\n".join([
        block("STYLE GUIDE", ctx["style"]),
        block("POLICIES", beliefs),
        block("INTENTS", intents),
        block("CONCEPTS", concepts),
        block("LEARNINGS (this pass)", learn),
        block("BOOK CONTEXT — BEFORE", before),
        block("BOOK CONTEXT — AFTER", after),
        block(f"THE ESSAY — {ctx['file']} ({len(ctx['paragraphs'])} "
              "paragraphs)", essay),
    ])


# ------------------------------------------------- output validation

class ContractError(ValueError):
    """The model's response violated the output contract; the whole
    response is discarded (never partially applied)."""


def validate_output(raw: dict, paragraphs: list[str]) -> dict:
    """Echo-validated parse. Returns {edits, insertions, suggestions}
    where every edit carries verbatim `old` from the SOURCE paragraph."""
    if not isinstance(raw, dict):
        raise ContractError("response is not a JSON object")
    items = raw.get("paragraphs")
    if not isinstance(items, list) or len(items) != len(paragraphs):
        raise ContractError(
            f"expected {len(paragraphs)} paragraph entries, got "
            f"{len(items) if isinstance(items, list) else 'none'}")
    edits = []
    for i, (item, src) in enumerate(zip(items, paragraphs), 1):
        if item.get("n") != i:
            raise ContractError(f"entry {i} has n={item.get('n')}")
        want = echo_of(src)
        got = " ".join(str(item.get("echo", "")).split())
        if got != want:
            raise ContractError(
                f"paragraph {i} echo mismatch: expected «{want}», got «{got}»")
        action = item.get("action")
        if action == "keep":
            continue
        if action != "replace":
            raise ContractError(f"paragraph {i}: unknown action '{action}'")
        new = item.get("new")
        if not isinstance(new, str):
            raise ContractError(f"paragraph {i}: replace without 'new'")
        new = new.strip()
        if new == src:
            continue  # a no-op replace is a keep
        edits.append({"n": i, "old": src, "new": new,
                      "why": str(item.get("why") or "").strip(),
                      "intent_id": item.get("intent_id") or None})
    insertions = []
    for ins in raw.get("insertions") or []:
        after = ins.get("after")
        if not isinstance(after, int) or not 0 <= after <= len(paragraphs):
            raise ContractError(f"insertion after={after} out of range")
        new = str(ins.get("new") or "").strip()
        if not new:
            raise ContractError("insertion without text")
        insertions.append({"after": after, "new": new,
                           "why": str(ins.get("why") or "").strip(),
                           "intent_id": ins.get("intent_id") or None})
    suggestions = []
    for s in raw.get("suggestions") or []:
        text = str(s.get("text") or "").strip()
        if text:
            suggestions.append({"kind": s.get("kind") or "other",
                                "text": text,
                                "intent_id": s.get("intent_id") or None})
    return {"edits": edits, "insertions": insertions,
            "suggestions": suggestions}


def run_editor(llm: LLMClient, ctx: dict, retries: int = 2) -> dict:
    """Call the editor model; on a contract violation, retry with the
    violation named. The raw responses are never partially used."""
    system = editor_prompt()
    user = render_user_message(ctx)
    last_err = None
    for attempt in range(retries + 1):
        prompt = user if not last_err else (
            user + f"\n\n=== YOUR PREVIOUS RESPONSE WAS REJECTED ===\n"
            f"{last_err}\nReturn the complete corrected JSON.")
        raw = llm.complete_json(system, prompt)
        if raw is None:
            raise RuntimeError("editor returned nothing (LLM disabled or "
                               "call failed)")
        try:
            return validate_output(raw, ctx["paragraphs"])
        except ContractError as err:
            last_err = str(err)
    raise ContractError(f"editor failed the contract {retries + 1} times: "
                        f"{last_err}")


# ---------------------------------------------------------- staging

def stage(db: Database, manuscript: dict, pass_row: dict, file: str,
          result: dict) -> dict:
    """Stage validated output: edits and insertions as doc_threads rows
    (origin_type critique, ordinal-keyed so re-runs replace cleanly);
    suggestions as PROPOSED system-sourced intents with lineage."""
    mid = manuscript["id"]
    prefix = f"{pass_row['id']}:{file}:"
    existing = {
        r["origin_id"]: dict(r) for r in db.all(
            "SELECT * FROM doc_threads WHERE manuscript_id = ? AND "
            "origin_type = 'critique' AND file = ? AND origin_id LIKE ?",
            (mid, file, prefix + "%"))
    }
    staged = []
    ordinal = 0
    seq = [(e["n"], 0, e) for e in result["edits"]] + \
          [(i["after"], 1, i) for i in result["insertions"]]
    seq.sort(key=lambda t: (t[0], t[1]))
    used: set[str] = set()
    for anchor, kind, item in seq:
        ordinal += 1
        origin_id = f"{prefix}{ordinal}"
        used.add(origin_id)
        meta = {"kind": "insert" if kind else "replace",
                "anchor_paragraph": anchor, "intent_id": item["intent_id"],
                "original_new": item["new"]}
        fields = {
            "proposed_old": "" if kind else item["old"],
            "proposed_new": item["new"],
            "note": item["why"], "state": "proposed",
            "our_reply_ids": "[]", "last_author_reply_id": None,
            "scope_kind": "file", "scope_ref": file,
            "metadata": json.dumps(meta),
        }
        # Re-runs must replace in place: origin_id is UNIQUE, so a soft
        # withdraw + insert collides with the withdrawn (or written) row.
        if origin_id in existing:
            prior = existing[origin_id]
            # A row still WRITTEN is a form the author is reading in the
            # Doc right now. Resetting it to 'proposed' would un-pend it
            # silently AND lift push_doc's critique gate on an essay whose
            # Doc still carries <<old>>{{new}} spans — the pause exists
            # precisely so a push cannot land there. 'cleaned' is a
            # finished pass and re-stages freely.
            if prior["state"] == "written":
                raise ValueError(
                    f"{file}: a critique edit at this anchor is already "
                    f"{prior['state']} in the Doc — resolve or roll back "
                    "the pass before re-staging it")
            db.update("doc_threads", prior["id"], fields)
            row = {**prior, **fields}
        else:
            row = ko_fields("dt")
            row.update(
                manuscript_id=mid, origin_type="critique",
                origin_id=origin_id, file=file, anchor_quote=None, **fields)
            db.insert("doc_threads", row)
        staged.append(row)
    for oid, row in existing.items():
        if oid not in used and row["state"] in ("proposed", "accepted",
                                                  "rejected"):
            db.update("doc_threads", row["id"], {"state": "withdrawn"})
    suggestions = []
    system_src = db.source("system")
    for s in result["suggestions"]:
        from . import sessions
        intent = sessions.declare_intent(
            db, mid, f"[{s['kind']}] {s['text']}", scope=file,
            status="proposed", source_id=system_src)
        db.update("declared_intents", intent["id"], {"metadata": json.dumps(
            {"lineage": {"pass_id": pass_row["id"], "file": file,
                         "intent_id": s["intent_id"]}})})
        suggestions.append(dict(db.one(
            "SELECT * FROM declared_intents WHERE id = ?", (intent["id"],))))
    set_essay_state(db, pass_row, file, "proposed")
    return {"staged": staged, "suggestions": suggestions}


def staged_threads(db: Database, manuscript_id: str, file: str,
                   states=("proposed", "accepted", "rejected"),
                   origin_type: str = "critique") -> list[dict]:
    """Staged edits for one file, by PRODUCER (filter-pass design §2.2).

    `origin_type` defaults to today's literal, so every existing caller
    is byte-for-byte unchanged. It is the one origin-aware line in the
    settle flow: `compose_marked_text`, `final_text_from_marked` and
    `verdict` read only thread fields and never the producer."""
    rows = db.all(
        "SELECT * FROM doc_threads WHERE manuscript_id = ? AND "
        "origin_type = ? AND file = ? AND state IN (%s) "
        "ORDER BY created_at, id" % ",".join("?" * len(states)),
        (manuscript_id, origin_type, file, *states))
    return [dict(r) for r in rows]


# ---------------------------------------------------------- verdicts

def _edit_evidence(db: Database, manuscript_id: str, thread: dict,
                   signal: str, explanation: str | None = None,
                   evidence_type: str = "critique_edit") -> None:
    """One edit verdict as evidence. `episode_id=None` is the door's
    standing precedent and not an oversight: an edit verdict is evidence
    with no episode, because an episode records work in service of a
    declared GOAL and a hygiene verdict serves none (filter-pass §1.8).

    `evidence_type` names the producer — `critique_edit` or
    `filter_edit` — and defaults to today's literal, so no existing
    caller changes. Explained rejections still reach belief learning
    either way: that path runs off the evidence stream and the author's
    words, never off an episode."""
    from .gdocs import clamp

    target = f"{thread['file']}: «{clamp(thread['proposed_old'] or '(insertion)')}»"
    if signal == "revised":
        meta = loads(thread.get("metadata"), {}) or {}
        target += (f" — proposal «{clamp(meta.get('original_new', ''))}» "
                   f"became «{clamp(thread['proposed_new'] or '')}»")
    if explanation:
        target += f" — {explanation}"
    ev = ko_fields("ev")
    ev.update(manuscript_id=manuscript_id, episode_id=None,
              evidence_type=evidence_type, signal=signal,
              target=target[:400], supports_belief=None, weight="high")
    if explanation:
        ev["metadata"] = json.dumps({"explanation": explanation})
    db.insert("evidence", ev)


def verdict(db: Database, manuscript_id: str, thread: dict, decision: str,
            text: str | None = None,
            evidence_type: str = "critique_edit") -> dict:
    """accept | reject (text = author's reason) | revise (text = author's
    wording) | undo (back to proposed). Verdicts are recorded only —
    nothing touches file or Doc until diff-write. Undo is free.

    `evidence_type` is passed straight down to `_edit_evidence`; it
    defaults to today's literal, so the critique pass is unchanged."""
    if decision == "undo":
        meta = loads(thread.get("metadata"), {}) or {}
        original = meta.get("original_new", thread["proposed_new"])
        db.update("doc_threads", thread["id"],
                  {"state": "proposed", "proposed_new": original})
        _edit_evidence(db, manuscript_id, thread, "undone",
                       evidence_type=evidence_type)
        return {"state": "proposed"}
    if thread["state"] not in ("proposed", "accepted", "rejected"):
        raise ValueError(f"thread is {thread['state']} — verdicts apply "
                         "only before diff-write")
    if decision == "accept":
        db.update("doc_threads", thread["id"], {"state": "accepted"})
        _edit_evidence(db, manuscript_id, thread, "accepted",
                       evidence_type=evidence_type)
        return {"state": "accepted"}
    if decision == "reject":
        db.update("doc_threads", thread["id"], {"state": "rejected"})
        _edit_evidence(db, manuscript_id, thread, "rejected", text,
                       evidence_type=evidence_type)
        return {"state": "rejected"}
    if decision == "revise":
        if not text:
            raise ValueError("revise needs the author's wording")
        db.update("doc_threads", thread["id"],
                  {"state": "accepted", "proposed_new": text})
        fresh = dict(db.one("SELECT * FROM doc_threads WHERE id = ?",
                            (thread["id"],)))
        _edit_evidence(db, manuscript_id, fresh, "revised",
                       evidence_type=evidence_type)
        return {"state": "accepted"}
    raise ValueError(f"unknown decision '{decision}'")


def mark_triaged(db: Database, pass_row: dict, file: str) -> None:
    set_essay_state(db, pass_row, file, "triaged")


# ------------------------------------------------- local apply/rollback

def compose_marked_text(text: str, threads: list[dict]) -> str:
    """The essay text with every ACCEPTED thread rendered as its pending
    form — what the Doc tab shows during the pause. Pure function; the
    push and the tests share it. Replaces are located verbatim (law);
    insertions go after their anchor paragraph (0 = before the first)."""
    paragraphs = paragraphs_of(text)
    by_para: dict[int, list[dict]] = {}
    for t in threads:
        if t["state"] != "accepted":
            continue
        meta = loads(t.get("metadata"), {}) or {}
        by_para.setdefault(meta.get("anchor_paragraph", 0), []).append(t)
    out = []
    for n, para in enumerate(paragraphs, 1):
        # a replace of paragraph n
        for t in by_para.get(n, []):
            if (loads(t.get("metadata"), {}) or {}).get("kind") == "replace":
                if t["proposed_old"] != para:
                    raise ValueError(
                        f"paragraph {n} no longer matches its proposal — the "
                        "text drifted since the pass; re-run 'critique run'")
                para = th.render_pending(t["proposed_old"], t["proposed_new"])
        out.append(para)
        for t in by_para.get(n, []):
            if (loads(t.get("metadata"), {}) or {}).get("kind") == "insert":
                out.append(th.render_insertion(t["proposed_new"]))
    head = [th.render_insertion(t["proposed_new"]) for t in by_para.get(0, [])
            if (loads(t.get("metadata"), {}) or {}).get("kind") == "insert"]
    return "\n\n".join(head + out) + "\n"


def final_text_from_marked(marked: str,
                           written: list[dict] | None = None
                           ) -> tuple[str, list[dict]]:
    """Resolve: critique-written forms keep their CURRENT {{new}} half
    (author post-edits win); any other pending form on the tab (e.g. an
    open margin-thread proposal) collapses to OLD so resolve never
    silently approves foreign grammar. Returns (final_text, critique_forms)."""
    forms = th.pending_forms(marked)
    if not written:
        # No written threads: keep old for every form (nothing to approve).
        return th.strip_pending(marked)[0], []
    unmatched = list(written)
    insert_queue = [t for t in written if t["proposed_old"] == ""]
    critique_forms = []
    keep_new: set[tuple[int, int]] = set()
    for form in forms:
        match = None
        if form["kind"] == "replace":
            match = next((t for t in unmatched
                          if t["proposed_old"] == form["old"]), None)
        elif insert_queue:
            match = insert_queue.pop(0)
        if match is None or match not in unmatched:
            continue
        unmatched.remove(match)
        critique_forms.append(form)
        keep_new.add((form["start"], form["end"]))
    # Rebuild from the end so indices stay valid: matched → new, else → old.
    text = marked
    for form in sorted(forms, key=lambda f: f["start"], reverse=True):
        span = (form["start"], form["end"])
        replacement = (form["new"] if span in keep_new else form["old"])
        text = text[: form["start"]] + replacement + text[form["end"]:]
    return text, critique_forms


def record_resolution(db: Database, manuscript_id: str, file: str,
                      forms: list[dict],
                      origin_type: str = "critique",
                      evidence_type: str = "critique_edit") -> list[dict]:
    """Match the resolved forms back to their threads by proposal text;
    record proposal→final diffs as evidence; close the threads. Returns
    the modified-acceptance diffs (the learnings duty's feedstock).

    Origin-agnostic apart from the `staged_threads` call it makes: both
    parameters default to today's values (filter-pass design §2.2)."""
    threads = staged_threads(db, manuscript_id, file, states=("written",),
                             origin_type=origin_type)
    diffs = []
    unmatched = list(threads)
    # Replaces match by their verbatim OLD half (law). Insertions have no
    # old half, so they match in document order among the insertion
    # threads — the marked text was composed in that same order.
    insert_queue = [t for t in threads if t["proposed_old"] == ""]
    for form in forms:
        match = None
        if form["kind"] == "replace":
            match = next((t for t in unmatched
                          if t["proposed_old"] == form["old"]), None)
        elif insert_queue:
            match = insert_queue.pop(0)
        if match is None or match not in unmatched:
            continue
        unmatched.remove(match)
        proposed = match["proposed_new"]
        if form["new"] != proposed:
            db.update("doc_threads", match["id"],
                      {"proposed_new": form["new"], "state": "cleaned"})
            fresh = dict(db.one("SELECT * FROM doc_threads WHERE id = ?",
                                (match["id"],)))
            meta = loads(fresh.get("metadata"), {}) or {}
            meta.setdefault("original_new", proposed)
            db.update("doc_threads", match["id"],
                      {"metadata": json.dumps(meta)})
            fresh["metadata"] = json.dumps(meta)
            _edit_evidence(db, manuscript_id, fresh, "revised",
                           evidence_type=evidence_type)
            diffs.append({"file": file, "proposal": proposed,
                          "final": form["new"]})
        else:
            db.update("doc_threads", match["id"], {"state": "cleaned"})
            _edit_evidence(db, manuscript_id, match, "resolved",
                           evidence_type=evidence_type)
    for t in unmatched:
        # The author deleted the form outright during the pause: a decline.
        db.update("doc_threads", t["id"], {"state": "declined"})
        _edit_evidence(db, manuscript_id, t, "declined",
                       evidence_type=evidence_type)
    return diffs
