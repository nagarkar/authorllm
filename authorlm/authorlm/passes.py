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
from .llm import LLMClient
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
    editor_model` overrides the general `[llm] model`."""
    llm = LLMClient(config)
    override = (config.get("critique", {}) or {}).get("editor_model")
    if override:
        llm.model = override
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

    Candidate policies: only those seeded SINCE THIS PASS BEGAN count
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
        "SELECT * FROM style_elements WHERE manuscript_id = ? AND "
        "status = 'proposed' AND (file = ? OR guide_id IN (%s))"
        % ",".join("?" * len(guides)) if guides else
        "SELECT * FROM style_elements WHERE manuscript_id = ? AND "
        "status = 'proposed' AND file = ?",
        (mid, file, *guides) if guides else (mid, file))]
    candidate_policies = [dict(r) for r in db.all(
        "SELECT * FROM editorial_policies WHERE manuscript_id = ? AND "
        "status = 'candidate' AND created_at >= ?",
        (mid, since or "9999"))]
    blockers = {"proposed_intents": proposed_intents,
                "proposed_elements": proposed_elements,
                "candidate_policies": candidate_policies}
    return {"clear": not any(blockers.values()), **blockers}


def summaries_ready(db: Database, manuscript: dict, file: str) -> dict:
    """Missing summaries or a stale (source_hash) one anywhere in the
    before/after context blocks the run; upstream_stale is tolerated."""
    before, after = sums.before_after(db, manuscript, file)
    missing = [e["file"] for e in before + after if e["state"] == "missing"]
    stale = [e["file"] for e in before + after if e["state"] == "stale"]
    return {"ok": not missing and not stale, "missing": missing,
            "stale": stale, "before": before, "after": after}


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
    ready = summaries_ready(db, manuscript, file)
    if not ready["ok"]:
        parts = []
        if ready["missing"]:
            parts.append(f"missing summaries: {', '.join(ready['missing'])}")
        if ready["stale"]:
            parts.append(f"stale summaries (text changed): "
                         f"{', '.join(ready['stale'])}")
        raise RuntimeError("; ".join(parts) + " — run 'summarize rebuild' "
                           "first (the edit pass will not read a lie about "
                           "the text).")
    texts = dict(sums.units(manuscript))
    if file not in texts:
        raise LookupError(f"'{file}' is not in the manuscript's reading order")
    paragraphs = paragraphs_of(texts[file])
    intents = intents_in_scope(db, manuscript, file, "active")
    policies = [dict(r) for r in db.all(
        "SELECT * FROM editorial_policies WHERE manuscript_id = ? AND "
        "status = 'validated' ORDER BY confidence DESC", (manuscript["id"],))]
    try:
        concepts = api.scoped_concepts(db, manuscript, file=file)
    except LookupError:
        concepts = {"nodes": [], "edges": []}
    return {
        "file": file, "paragraphs": paragraphs,
        "style": render_style(db, manuscript["id"], file),
        "intents": intents, "policies": policies, "concepts": concepts,
        "before": ready["before"], "after": ready["after"],
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
    for pol in gate["candidate_policies"]:
        lines.append(f"  candidate policy [{pol['id'][:8]}]: "
                     f"{pol['statement'][:80]}")
    return "\n".join(lines)


def render_user_message(ctx: dict) -> str:
    """The prompt's user turn, in the order the system prompt promises."""
    def block(title, body):
        return f"=== {title} ===\n{body.strip() or '(none)'}\n"

    intents = "\n".join(
        f"- [{i['id']}] ({i['scope'] or 'manuscript-wide'}) {i['statement']}"
        for i in ctx["intents"])
    policies = "\n".join(f"- {p['statement']}" for p in ctx["policies"])
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
        block("POLICIES", policies),
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
    # A re-run withdraws the previous proposals for this essay first.
    for r in db.all("SELECT id FROM doc_threads WHERE manuscript_id = ? AND "
                    "origin_type = 'critique' AND file = ? AND state IN "
                    "('proposed', 'accepted', 'rejected')", (mid, file)):
        db.update("doc_threads", r["id"], {"state": "withdrawn"})
    staged = []
    ordinal = 0
    seq = [(e["n"], 0, e) for e in result["edits"]] + \
          [(i["after"], 1, i) for i in result["insertions"]]
    seq.sort(key=lambda t: (t[0], t[1]))
    for anchor, kind, item in seq:
        ordinal += 1
        row = ko_fields("dt")
        origin_id = f"{pass_row['id']}:{file}:{ordinal}"
        meta = {"kind": "insert" if kind else "replace",
                "anchor_paragraph": anchor, "intent_id": item["intent_id"],
                "original_new": item["new"]}
        row.update(
            manuscript_id=mid, origin_type="critique", origin_id=origin_id,
            file=file, anchor_quote=None,
            proposed_old="" if kind else item["old"], proposed_new=item["new"],
            note=item["why"], state="proposed", our_reply_ids="[]",
            last_author_reply_id=None, scope_kind="file", scope_ref=file,
            metadata=json.dumps(meta))
        db.insert("doc_threads", row)
        staged.append(row)
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
                   states=("proposed", "accepted", "rejected")) -> list[dict]:
    rows = db.all(
        "SELECT * FROM doc_threads WHERE manuscript_id = ? AND "
        "origin_type = 'critique' AND file = ? AND state IN (%s) "
        "ORDER BY created_at, id" % ",".join("?" * len(states)),
        (manuscript_id, file, *states))
    return [dict(r) for r in rows]


# ---------------------------------------------------------- verdicts

def _edit_evidence(db: Database, manuscript_id: str, thread: dict,
                   signal: str, explanation: str | None = None) -> None:
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
              evidence_type="critique_edit", signal=signal,
              target=target[:400], supports_policy=None, weight="high")
    if explanation:
        ev["metadata"] = json.dumps({"explanation": explanation})
    db.insert("evidence", ev)


def verdict(db: Database, manuscript_id: str, thread: dict, decision: str,
            text: str | None = None) -> dict:
    """accept | reject (text = author's reason) | revise (text = author's
    wording) | undo (back to proposed). Verdicts are recorded only —
    nothing touches file or Doc until diff-write. Undo is free."""
    if decision == "undo":
        meta = loads(thread.get("metadata"), {}) or {}
        original = meta.get("original_new", thread["proposed_new"])
        db.update("doc_threads", thread["id"],
                  {"state": "proposed", "proposed_new": original})
        _edit_evidence(db, manuscript_id, thread, "undone")
        return {"state": "proposed"}
    if thread["state"] not in ("proposed", "accepted", "rejected"):
        raise ValueError(f"thread is {thread['state']} — verdicts apply "
                         "only before diff-write")
    if decision == "accept":
        db.update("doc_threads", thread["id"], {"state": "accepted"})
        _edit_evidence(db, manuscript_id, thread, "accepted")
        return {"state": "accepted"}
    if decision == "reject":
        db.update("doc_threads", thread["id"], {"state": "rejected"})
        _edit_evidence(db, manuscript_id, thread, "rejected", text)
        return {"state": "rejected"}
    if decision == "revise":
        if not text:
            raise ValueError("revise needs the author's wording")
        db.update("doc_threads", thread["id"],
                  {"state": "accepted", "proposed_new": text})
        fresh = dict(db.one("SELECT * FROM doc_threads WHERE id = ?",
                            (thread["id"],)))
        _edit_evidence(db, manuscript_id, fresh, "revised")
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


def final_text_from_marked(marked: str) -> tuple[str, list[dict]]:
    """Resolve: every pending form's CURRENT {{new}} half becomes final
    (the author's post-edits win). Returns (final_text, forms)."""
    forms = th.pending_forms(marked)
    return th.approved_text(marked), forms


def record_resolution(db: Database, manuscript_id: str, file: str,
                      forms: list[dict]) -> list[dict]:
    """Match the resolved forms back to their threads by proposal text;
    record proposal→final diffs as evidence; close the threads. Returns
    the modified-acceptance diffs (the learnings duty's feedstock)."""
    threads = staged_threads(db, manuscript_id, file, states=("written",))
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
            _edit_evidence(db, manuscript_id, fresh, "revised")
            diffs.append({"file": file, "proposal": proposed,
                          "final": form["new"]})
        else:
            db.update("doc_threads", match["id"], {"state": "cleaned"})
            _edit_evidence(db, manuscript_id, match, "resolved")
    for t in unmatched:
        # The author deleted the form outright during the pause: a decline.
        db.update("doc_threads", t["id"], {"state": "declined"})
        _edit_evidence(db, manuscript_id, t, "declined")
    return diffs
