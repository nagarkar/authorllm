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
#
# The {{new}} half is closed by *balanced* `{{` / `}}` nesting, not by the
# first `}}`. Modified acceptance (margin-threads design) lets the author
# rewrite the green half in place; set notation like `{{a, b}}` is ordinary
# prose there. A non-greedy / find('}}') close truncated that prose and
# left residual markers in the manuscript on resolve/approve.
# Plant-time `assert_no_pending_markers` still refuses delimiter-bearing
# proposals so the machine never writes a nested form; the scanner is what
# honours a post-edit the author typed into the Doc.
PENDING = re.compile(
    r"(?:~~)?<<(?P<old>.*?)>>(?:~~)?\{\{(?P<new>.*?)\}\}",
    re.DOTALL)
INSERTION = re.compile(r"(?<![>}])\{\{(?P<new>.*?)\}\}", re.DOTALL)
_ANY_MARKER = re.compile(r"<<|>>|\{\{|\}\}")


def close_braced(text: str, content_start: int) -> int | None:
    """Index just past the `}}` that balances an opening `{{` whose
    content begins at `content_start`. None when the span is unclosed.

    Depth counts paired `{{` / `}}` only (single braces are prose). Shared
    by the tab scanner and `gdocs._replace_pending`'s exact-span locate."""
    depth = 1
    i = content_start
    n = len(text)
    while i < n - 1:
        pair = text[i:i + 2]
        if pair == "{{":
            depth += 1
            i += 2
        elif pair == "}}":
            depth -= 1
            i += 2
            if depth == 0:
                return i
        else:
            i += 1
    return None


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

    Plant-time only: the scanner balances nested `{{` / `}}` in a green
    half the author typed after the fact, but a machine-written proposal
    still must not carry reserved delimiters — `<<` / `>>` break the old
    half, and planting nested braces invites every older find('}}') path
    (and every human reading the tab) to mis-count the span."""
    for label, part in (("old", old), ("new", new)):
        for marker in _RESERVED_MARKERS:
            if marker in part:
                raise ValueError(
                    f"proposal {label} text cannot contain {marker!r} "
                    "(reserved by the pending-change grammar)")


def render_pending(old: str, new: str) -> str:
    """The pending form for a span. An empty `old` renders the insertion
    form ({{new}} only) — the critique pass's new-paragraph proposals."""
    assert_no_pending_markers(old, new)
    if old == "":
        return render_insertion(new)
    return f"<<{old}>>{{{{{new}}}}}"


def render_insertion(new: str) -> str:
    # The insertion form is the same reserved grammar, and the critique
    # pass reaches it directly — guarding only render_pending would leave
    # the new-paragraph path unchecked.
    assert_no_pending_markers("", new)
    return f"{{{{{new}}}}}"


def _collapse(text: str, keep: str) -> str:
    """Resolve every pending form to one half. Rebuild from `pending_forms`
    (balanced new halves) so a kept OLD half that itself contains `{{…}}`
    is not re-scanned as an insertion, and a post-edited green half that
    nests set notation is not truncated at the inner `}}`. Dropping an
    insertion that sat as its own paragraph also drops its preceding
    blank line, so the OLD text is byte-clean; keeping one leaves the
    paragraph in place."""
    result = text
    for form in sorted(pending_forms(text), key=lambda f: f["start"],
                       reverse=True):
        start, end = form["start"], form["end"]
        if keep == "new":
            repl = form["new"]
        elif form["kind"] == "insert":
            start, end, repl = _insertion_strip_span(result, start, end)
        else:
            repl = form["old"]
        result = result[:start] + repl + result[end:]
    return result


def _insertion_strip_span(text: str, start: int, end: int
                          ) -> tuple[int, int, str]:
    """When an insertion is its own paragraph, widen the delete to eat
    the blank line before it (same shape the old `_INSERTION_PARA` regex
    matched: blank line before, then `\\s*\\n` or end after). Inline
    insertions just collapse to empty in place."""
    before, after = text[:start], text[end:]
    m = re.search(r"\n\s*\n\Z", before)
    if m and (after == "" or re.match(r"\s*\n", after) is not None):
        return m.start(), end, ""
    return start, end, ""


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


_REPLACE_MARKER = re.compile(r"<<|>>")


def strip_replacements(text: str) -> tuple[str, list[str]]:
    """`strip_pending` narrowed to the REPLACE form alone.

    The canonicalizer for LOCAL files (filter-pass design §2.3), and it is
    deliberately not `strip_pending`.

    `strip_pending` is right for text arriving from a DOC: everything with
    this grammar in a Doc tab was put there by AuthorLM, so a bare
    `{{new}}` is a critique-pass insertion and collapsing it to nothing is
    the ratified behaviour. A local manuscript file is the opposite case.
    Nothing put `{{…}}` there but the author, and `{{title}}` in a
    template, `{{a, b}}` in set notation, a Jinja or Handlebars sample in
    an essay ABOUT templating are all ordinary prose. Running the Doc
    canonicalizer over local files deleted every one of them from what
    the whole system observes — silently, with no warning, because
    `_ANY_MARKER` no longer matched anything once they were gone.

    So: only `<<old>>{{new}}` collapses, and only to its old half. The
    filter pass never stages an insertion — `insert` is refused by the
    recorder because a filter passes each bit through rather than adding
    bits — so nothing this seam is for is lost by narrowing.

    Stray `<<` or `>>` surviving the collapse is an unbalanced replace
    form and is warned about rather than guessed at. A surviving `{{` or
    `}}` is NOT warned about: it is not this grammar's business, and a
    warning on every templating example would be noise that trained the
    author to ignore the warnings that matter."""
    result = text
    for form in sorted(
            (f for f in pending_forms(text) if f["kind"] == "replace"),
            key=lambda f: f["start"], reverse=True):
        result = (result[:form["start"]] + form["old"] + result[form["end"]:])
    warnings = []
    if _REPLACE_MARKER.search(result):
        warnings.append(
            "stray or unbalanced pending-change markers (<<, >>) — left "
            "untouched; settle them in the file or ask in chat")
    return result, warnings


def has_replacement(text: str) -> bool:
    """True when the text carries a `<<old>>{{new}}` form.

    The predicate `staging.is_marked` is built on, and narrow for the same
    reason `strip_replacements` is: a file containing `{{title}}` is not
    mid-settle, and treating it as such refused its Doc push forever with
    a message about a resolve that does not exist."""
    return any(f["kind"] == "replace" for f in pending_forms(text))


def approved_text(text: str) -> str:
    """What a tab's text becomes when every pending form is approved —
    used by the cleanup path and by equivalence checks."""
    return _collapse(text, "new")


def pending_forms(text: str) -> list[dict]:
    """Every pending form in a tab, in document order: {kind, old, new,
    start, end}. The resolve verb reads the author's post-edits from the
    {{new}} halves here. New halves close at balanced brace depth so a
    post-edited `{{a, b}}` inside the green run is not a false end."""
    found: list[dict] = []
    i, n = 0, len(text)
    while i < n:
        at = text.find("<<", i)
        if at < 0:
            break
        form_start = at - 2 if at >= 2 and text[at - 2:at] == "~~" else at
        gt = text.find(">>", at + 2)
        if gt < 0:
            break
        old = text[at + 2:gt]
        pos = gt + 2
        if text.startswith("~~", pos):
            pos += 2
        if not text.startswith("{{", pos):
            i = at + 2
            continue
        end = close_braced(text, pos + 2)
        if end is None:
            i = at + 2
            continue
        found.append({"kind": "replace", "old": old,
                      "new": text[pos + 2:end - 2],
                      "start": form_start, "end": end})
        i = end
    consumed = [(f["start"], f["end"]) for f in found]
    i = 0
    while i < n - 1:
        if text[i:i + 2] != "{{":
            i += 1
            continue
        if i > 0 and text[i - 1] in ">}":
            i += 2
            continue
        if any(s <= i < e for s, e in consumed):
            i += 2
            continue
        end = close_braced(text, i + 2)
        if end is None:
            i += 2
            continue
        found.append({"kind": "insert", "old": "",
                      "new": text[i + 2:end - 2],
                      "start": i, "end": end})
        i = end
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
    recorded as evidence; candidate beliefs only through the scoped,
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
        supports_belief=None, weight="high",
    )
    if explanation:
        row["metadata"] = json.dumps({"explanation": explanation})
    db.insert("evidence", row)
    if explanation:
        from .beliefs import seed_margin_candidate

        seed_margin_candidate(db, manuscript_id, explanation,
                              thread["file"], guide_chain or [], llm)
