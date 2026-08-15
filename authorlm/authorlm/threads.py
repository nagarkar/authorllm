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

PENDING = re.compile(
    r"(?:~~)?<<(?P<old>.*?)>>(?:~~)?\{\{(?P<new>.*?)\}\}",
    re.DOTALL)
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


_RESERVED_MARKERS = ("<<", ">>", "{{", "}}")


def assert_no_pending_markers(old: str, new: str) -> None:
    """Reject proposal text that would break the <<old>>{{new}} grammar.
    Non-greedy parsers and find('}}') close at the first delimiter, so a
    new half containing `}}` (e.g. nested braces) truncates the span and
    corrupts both pull-strip and approve."""
    for label, part in (("old", old), ("new", new)):
        for marker in _RESERVED_MARKERS:
            if marker in part:
                raise ValueError(
                    f"proposal {label} text cannot contain {marker!r} "
                    "(reserved by the pending-change grammar)")


def render_pending(old: str, new: str) -> str:
    assert_no_pending_markers(old, new)
    return f"<<{old}>>{{{{{new}}}}}"


def strip_pending(text: str) -> tuple[str, list[str]]:
    """Canonical text for local files: pending spans collapse to their
    OLD half. Returns (canonical, warnings). Unbalanced or stray
    markers are never guessed at — the span is left intact and warned
    about, to be settled in conversation."""
    stripped = PENDING.sub(lambda m: m.group("old"), text)
    warnings = []
    if _ANY_MARKER.search(stripped):
        warnings.append(
            "stray or unbalanced pending-change markers (<<, >>, {{, }}) "
            "— left untouched; fix in the Doc or ask in chat")
    return stripped, warnings


def approved_text(text: str) -> str:
    """What a tab's text becomes when every pending span is approved —
    used by the cleanup path and by equivalence checks."""
    return PENDING.sub(lambda m: m.group("new"), text)


# ------------------------------------------------------------- records

def create_thread(db: Database, manuscript_id: str, comment_id: str,
                  file: str, anchor_quote: str | None, old: str, new: str,
                  note: str, reply_id: str | None,
                  scope_kind: str | None = None,
                  scope_ref: str | None = None) -> dict:
    row = ko_fields("dt")
    row.update(
        manuscript_id=manuscript_id, comment_id=comment_id, file=file,
        anchor_quote=anchor_quote, proposed_old=old, proposed_new=new,
        note=note, state="proposed",
        our_reply_ids=json.dumps([reply_id] if reply_id else []),
        last_author_reply_id=None,
        scope_kind=scope_kind, scope_ref=scope_ref,
    )
    db.insert("doc_threads", row)
    return row


def get_thread(db: Database, manuscript_id: str, comment_id: str):
    return db.one(
        "SELECT * FROM doc_threads WHERE manuscript_id = ? AND comment_id = ?",
        (manuscript_id, comment_id),
    )


def open_threads(db: Database, manuscript_id: str,
                 file: str | None = None) -> list[dict]:
    """Threads still holding the margin: proposed, conversation, or
    applied-awaiting-review."""
    rows = db.all(
        "SELECT * FROM doc_threads WHERE manuscript_id = ? "
        "AND state IN ('proposed', 'conversation', 'applied') "
        "ORDER BY created_at",
        (manuscript_id,),
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
