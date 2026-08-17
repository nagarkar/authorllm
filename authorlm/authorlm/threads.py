"""Margin threads — in-context Doc comment conversations
(docs/margin-threads-design.md).

This module owns the deterministic core: the pending-change grammar,
the verdict grammar, and the thread records. The Docs/Drive API calls
live in gdocs.py; prose proposals are drafted in chat (the beat-loop
division of labor: the collaborator drafts, the CLI is the state
machine and evidence channel).

The pending-change grammar, reserved in Doc tabs:

    <<old text>>{{new text}}

Boundary insertions leave the author's comment anchored on the old
span. While a thread is pending, the CANONICAL text is the old text —
ratified content changes only at approval — so the pull-strip unwraps
`<<old>>` and drops `{{new}}`. Observation never sees markers.
"""

from __future__ import annotations

import json
import re
import string

from .db import Database, ko_fields, loads

PREFIX = "AuthorLM: "

# Whole-reply matches (case- and punctuation-insensitive). No LLM ever
# sits between the author's words and their manuscript: these lists are
# ratified law (2026-08-08), and anything else is conversation.
APPROVE_KEYWORDS = {"go ahead", "make the change", "apply", "yes", "ok",
                    "okay", "lgtm"}
DECLINE_KEYWORDS = {"no", "dont", "don't", "revert", "reject"}

# The reserved pending-change grammar in a Doc tab (margin-threads design,
# extended by the critique-pass design §6.3):
#   replace:   <<old>>{{new}}   — old struck through, new in green
#   insertion: {{new}}          — new text with no old half (critique
#                                 passes propose new paragraphs this way)
# Both resolve the same way: the author may edit the {{new}} half; the
# local file keeps the OLD text (nothing, for an insertion) until resolve.
PENDING = re.compile(
    r"(?:~~)?<<(?P<old>.*?)>>(?:~~)?\{\{(?P<new>.*?)\}\}",
    re.DOTALL)
INSERTION = re.compile(r"(?<![>}])\{\{(?P<new>.*?)\}\}", re.DOTALL)
_ANY_MARKER = re.compile(r"<<|>>|\{\{|\}\}")


def classify_reply(content: str) -> str:
    """'approve' | 'decline' | 'conversation' for an author reply."""
    text = content.strip().lower()
    text = text.translate(str.maketrans("", "", string.punctuation.replace("'", "")))
    text = " ".join(text.split())
    if text in {k.translate(str.maketrans("", "", ".,!")) for k in APPROVE_KEYWORDS}:
        return "approve"
    if text in DECLINE_KEYWORDS:
        return "decline"
    return "conversation"


def is_ours(content: str) -> bool:
    """Replies posted through the author's OAuth carry their identity;
    the prefix is the only authorship signal there is."""
    return content.lstrip().startswith(PREFIX)


def render_pending(old: str, new: str) -> str:
    """The pending form for a span. An empty `old` renders the insertion
    form ({{new}} only) — the critique pass's new-paragraph proposals."""
    if old == "":
        return render_insertion(new)
    return f"<<{old}>>{{{{{new}}}}}"


def render_insertion(new: str) -> str:
    return f"{{{{{new}}}}}"


_INSERTION_PARA = re.compile(r"\n\s*\n(?<![>}])\{\{(?P<new>.*?)\}\}(?=\s*\n|\Z)",
                             re.DOTALL)


def _collapse(text: str, keep: str) -> str:
    """Resolve every pending form to one half. Replaces first (their
    {{new}} halves are consumed by the PENDING match, so the INSERTION
    lookbehind never sees them), then insertions. Dropping an insertion
    also drops its paragraph separator, so the OLD text is byte-clean;
    keeping one leaves the paragraph in place."""
    text = PENDING.sub(lambda m: m.group(keep), text)
    if keep == "old":
        text = _INSERTION_PARA.sub("", text)
    return INSERTION.sub(lambda m: m.group("new") if keep == "new" else "",
                         text)


def strip_pending(text: str) -> tuple[str, list[str]]:
    """Canonical text for local files: pending spans collapse to their
    OLD half (insertions vanish). Returns (canonical, warnings).
    Unbalanced or stray markers are never guessed at — the span is left
    intact and warned about, to be settled in conversation."""
    stripped = _collapse(text, "old")
    warnings = []
    if _ANY_MARKER.search(stripped):
        warnings.append(
            "stray or unbalanced pending-change markers (<<, >>, {{, }}) "
            "— left untouched; fix in the Doc or ask in chat")
    return stripped, warnings


def approved_text(text: str) -> str:
    """What a tab's text becomes when every pending form is approved —
    used by the cleanup path and by equivalence checks."""
    return _collapse(text, "new")


def pending_forms(text: str) -> list[dict]:
    """Every pending form in a tab, in document order: {kind, old, new,
    start, end}. The resolve verb reads the author's post-edits from the
    {{new}} halves here."""
    found = []
    for m in PENDING.finditer(text):
        found.append({"kind": "replace", "old": m.group("old"),
                      "new": m.group("new"), "start": m.start(),
                      "end": m.end()})
    consumed = [(f["start"], f["end"]) for f in found]
    for m in INSERTION.finditer(text):
        if any(s <= m.start() < e for s, e in consumed):
            continue
        found.append({"kind": "insert", "old": "", "new": m.group("new"),
                      "start": m.start(), "end": m.end()})
    return sorted(found, key=lambda f: f["start"])


# ------------------------------------------------------------- records

def create_thread(db: Database, manuscript_id: str, comment_id: str,
                  file: str, anchor_quote: str | None, old: str, new: str,
                  note: str, reply_id: str | None,
                  scope_kind: str | None = None,
                  scope_ref: str | None = None) -> dict:
    """A margin thread born from the author's Drive comment (origin_type
    author_comment; the comment id is the origin_id). Critique-pass
    proposals use critique.stage_edit instead (origin_type critique)."""
    row = ko_fields("dt")
    row.update(
        manuscript_id=manuscript_id, origin_type="author_comment",
        origin_id=comment_id, file=file,
        anchor_quote=anchor_quote, proposed_old=old, proposed_new=new,
        note=note, state="proposed",
        our_reply_ids=json.dumps([reply_id] if reply_id else []),
        last_author_reply_id=None,
        scope_kind=scope_kind, scope_ref=scope_ref,
    )
    db.insert("doc_threads", row)
    return row


def get_thread(db: Database, manuscript_id: str, comment_id: str):
    """The author-comment thread joined to a Drive comment id."""
    return db.one(
        "SELECT * FROM doc_threads WHERE manuscript_id = ? "
        "AND origin_type = 'author_comment' AND origin_id = ?",
        (manuscript_id, comment_id),
    )


def open_threads(db: Database, manuscript_id: str,
                 file: str | None = None,
                 origin_type: str = "author_comment") -> list[dict]:
    """Threads still holding the margin: proposed, conversation, or
    applied-awaiting-review. Scoped by origin: the pull path must only
    ever see author-comment threads — critique threads are the explicit
    resolve verb's business (design §6.3)."""
    rows = db.all(
        "SELECT * FROM doc_threads WHERE manuscript_id = ? "
        "AND origin_type = ? "
        "AND state IN ('proposed', 'conversation', 'applied') "
        "ORDER BY created_at",
        (manuscript_id, origin_type),
    )
    return [dict(r) for r in rows if file is None or r["file"] == file]


def set_state(db: Database, thread: dict, state: str,
              reply_id: str | None = None,
              author_reply_id: str | None = None) -> None:
    changes: dict = {"state": state}
    if reply_id:
        ids = loads(thread["our_reply_ids"], [])
        ids.append(reply_id)
        changes["our_reply_ids"] = json.dumps(ids)
    if author_reply_id:
        changes["last_author_reply_id"] = author_reply_id
    db.update("doc_threads", thread["id"], changes)


def ledger(db: Database, manuscript_id: str) -> dict:
    """The pull-output summary: counts by state plus the open rows."""
    counts: dict[str, int] = {}
    for row in db.all(
        "SELECT state, COUNT(*) AS n FROM doc_threads "
        "WHERE manuscript_id = ? GROUP BY state", (manuscript_id,),
    ):
        counts[row["state"]] = row["n"]
    return {"counts": counts,
            "open": open_threads(db, manuscript_id)}


def record_margin_verdict(db: Database, manuscript_id: str, thread: dict,
                          signal: str, explanation: str | None = None,
                          llm=None, guide_chain: list | None = None) -> None:
    """Terminal margin verdicts are the third evidence channel: always
    recorded as evidence; candidate policies only through the scoped,
    decline-capable distiller (never by force of habit)."""
    from .gdocs import clamp

    target = f"{thread['file']}: «{clamp(thread['proposed_old'] or '')}»"
    if signal == "modified":
        original = (loads(thread.get("metadata"), {}) or {}).get(
            "original_new", "")
        target += (f" — proposal «{clamp(original)}» became "
                   f"«{clamp(thread['proposed_new'] or '')}»")
    row = ko_fields("ev")
    row.update(
        manuscript_id=manuscript_id, episode_id=None,
        evidence_type="margin_thread", signal=signal, target=target,
        supports_policy=None, weight="high",
    )
    if explanation:
        row["metadata"] = json.dumps({"explanation": explanation})
    db.insert("evidence", row)
    if explanation:
        from .policies import seed_margin_candidate

        seed_margin_candidate(db, manuscript_id, explanation,
                              thread["file"], guide_chain or [], llm)
