"""The finding→edit door, and its LOCAL settle transport
(docs/filter-pass-design.md §2).

The contract, stated once:

    A producer may stage an edit if it supplies a `file` in the
    manuscript's reading order, a 1-based unit index `n` into
    `passes.paragraphs_of(<that file's text>)`, the unit's `echo`, a
    non-empty `new` free of the reserved markers, and a `why`. The
    HARNESS supplies `old` from the file itself; the producer never
    supplies it. In return the edit receives a `doc_threads` row whose
    shape it does not own, the triage verbs with their evidence, the
    compose-and-pause, the resolve with the author's post-edits winning,
    the proposal→final diff as evidence, and the rollback to a pinned
    version. A producer that cannot supply a verbatim-anchored unit gets
    NOTHING — no partial admission, no fuzzy match, no closest
    paragraph.

`origin_type` names the producer: `author_comment`, `critique`,
`filter`, and `lens` when a lens finding starts carrying a proposal.

This module owns only the parts that are genuinely new — staging from a
source-derived `old`, the local transport (mark / read back / unmark),
the direct apply, and the rollback. It CALLS `passes` and `threads`; it
moves nothing out of either, so the critique pass's behaviour is byte
for byte what it was.
"""

from __future__ import annotations

import json
from pathlib import Path

from .db import Database, ko_fields, loads
from . import passes
from . import threads as th


def is_marked(text: str) -> bool:
    """True when the raw bytes carry a pending-change form.

    A BYTE check on the file, exactly as `api.is_placeholder` is, and
    deliberately not a database query: it is the guard that survives a
    database that has lost the run row, and the state it describes is
    fully described by the bytes. `threads.pending_forms` is the same
    grammar the settle verbs parse, so this predicate and the resolver
    can never disagree about whether a file is mid-settle."""
    return bool(th.pending_forms(text))


# --------------------------------------------------------- staging

def stage_edits(db: Database, manuscript_id: str, owner_id: str, file: str,
                source_text: str, entries: list[dict],
                origin_type: str = "filter") -> list[dict]:
    """Stage validated entries as `doc_threads` rows.

    `entries` carry `n`, `new`, `why` and an optional `ref`; **`old` is
    read HERE, from `source_text`**, and never from the producer. This
    is the strongest form of the anchoring law available and it is the
    same rule the critique design states: pending-form writes cannot
    drift, because the text they claim to replace is the text that is
    actually there.

    `origin_id` is `{owner}:{file}:{ordinal}` — the shape `passes.stage`
    already builds — so a re-run over the same units REPLACES in place
    under the existing UNIQUE (manuscript_id, origin_type, origin_id)
    constraint rather than colliding with it. Entries are ordered by
    unit, so the ordinal is stable across re-runs of the same window.
    """
    units = passes.paragraphs_of(source_text)
    prefix = f"{owner_id}:{file}:"
    existing = {
        r["origin_id"]: dict(r) for r in db.all(
            "SELECT * FROM doc_threads WHERE manuscript_id = ? AND "
            "origin_type = ? AND file = ? AND origin_id LIKE ?",
            (manuscript_id, origin_type, file, prefix + "%"))}
    staged: list[dict] = []
    for ordinal, entry in enumerate(sorted(entries, key=lambda e: e["n"]), 1):
        n = entry["n"]
        old = units[n - 1]
        origin_id = f"{prefix}{ordinal}"
        meta = {"kind": "replace", "anchor_paragraph": n,
                "intent_id": None, "original_new": entry["new"],
                "ref": entry.get("ref") or None, "unit": n}
        fields = {
            "proposed_old": old, "proposed_new": entry["new"],
            "note": entry.get("why") or "", "state": "proposed",
            "our_reply_ids": "[]", "last_author_reply_id": None,
            "scope_kind": "file", "scope_ref": file,
            "metadata": json.dumps(meta),
        }
        if origin_id in existing:
            prior = existing[origin_id]
            if prior["state"] == "written":
                # A written row is a form the author is reading in the
                # file RIGHT NOW. Resetting it to 'proposed' would
                # un-pend it silently and lift the push guard on a file
                # whose bytes still carry the markers — the same rule
                # `passes.stage` states for the Doc pause.
                raise ValueError(
                    f"{file}: a staged edit at unit {n} is already "
                    f"written into the file — settle it "
                    f"('filter settle {file}') or put the text back "
                    f"('filter unmark {file}') before re-staging.")
            db.update("doc_threads", prior["id"], fields)
            row = {**prior, **fields}
        else:
            row = ko_fields("dt")
            row.update(manuscript_id=manuscript_id, origin_type=origin_type,
                       origin_id=origin_id, file=file, anchor_quote=None,
                       **fields)
            db.insert("doc_threads", row)
        staged.append(row)
    return staged


def door_threads(db: Database, manuscript_id: str, file: str,
                 states=("proposed", "accepted", "rejected"),
                 origin_type: str = "filter") -> list[dict]:
    """This producer's staged edits, in staging order."""
    return passes.staged_threads(db, manuscript_id, file, states=states,
                                 origin_type=origin_type)


def withdraw(db: Database, manuscript_id: str, file: str,
             origin_type: str = "filter") -> int:
    """Withdraw every open staged edit of this producer on this file.
    Written rows are NOT touched: they are the pause, and unmark is the
    verb that ends it."""
    rows = door_threads(db, manuscript_id, file, origin_type=origin_type)
    for row in rows:
        db.update("doc_threads", row["id"], {"state": "withdrawn"})
    return len(rows)


# ------------------------------------------------- the local transport

def apply_accepted(text: str, threads: list[dict]) -> str:
    """The essay with every ACCEPTED thread applied directly.

    Exactly the composition `compose_marked_text` performs — including
    its drift check, which raises rather than applying to a moved target
    — with the forms then collapsed to their NEW halves. Written this
    way on purpose: a second composition routine is a second way for the
    door to be wrong about where an edit goes."""
    return th.approved_text(passes.compose_marked_text(text, threads))


def mark_local(path: Path, text: str, threads: list[dict]) -> str:
    """Compose the marked text and write it to the LOCAL file — the
    filter's pause. Visible `<<old>>{{new}}` in the file is the
    affordance, because the author reads these files in Obsidian and a
    hidden marker fails in exactly the place that matters (§14.3's
    argument for the placeholder, applied again).

    Returns the marked text. The read-back assertion is the caller's:
    it owns the refusal wording and the unmark."""
    marked = passes.compose_marked_text(text, threads)
    path.write_text(marked, encoding="utf-8")
    return marked


def unmark(path: Path) -> tuple[str, list[str]]:
    """Put the ORIGINAL text back: every pending form collapses to its
    old half. `strip_pending` warns rather than guessing on stray or
    unbalanced markers, and those warnings are returned rather than
    swallowed — a two-line recovery for a state that is fully described
    by the bytes."""
    text, warnings = th.strip_pending(path.read_text(encoding="utf-8"))
    path.write_text(text, encoding="utf-8")
    return text, warnings


def resolve_local(db: Database, manuscript_id: str, file: str, path: Path,
                  origin_type: str = "filter",
                  evidence_type: str = "filter_edit"
                  ) -> tuple[str, list[dict], list[dict]]:
    """Read the marked local file back and finalize it.

    Byte for byte the critique resolve's second half, with
    `gdocs.critique_tab_markdown` replaced by `path.read_text`:
    `threads.pending_forms` → `passes.final_text_from_marked` →
    `passes.record_resolution`, all three unchanged. The author's
    post-edits to the `{{new}}` halves win, as always.

    Returns `(final_text, forms, diffs)`. It does NOT write the file:
    the caller snapshots first (an uncollected local edit must never be
    lost to this write) and owns the normalization."""
    written = door_threads(db, manuscript_id, file, states=("written",),
                           origin_type=origin_type)
    if not written:
        raise LookupError(
            f"nothing is written into {file} — there is no pause to "
            "finalize.")
    marked = path.read_text(encoding="utf-8")
    final, forms = passes.final_text_from_marked(marked, written=written)
    diffs = passes.record_resolution(db, manuscript_id, file, forms,
                                     origin_type=origin_type,
                                     evidence_type=evidence_type)
    return final, forms, diffs


def rollback(db: Database, manuscript: dict, file: str,
             version_id: str) -> str:
    """Restore the file from a pinned `manuscript_versions` row.

    The verdicts stay: they are evidence, and evidence is not undone by
    putting text back. Raises rather than guessing when the pinned
    version does not carry this file."""
    row = db.one("SELECT * FROM manuscript_versions WHERE id = ?",
                 (version_id,))
    if row is None:
        raise LookupError(f"no pinned version {version_id}")
    files = loads(row["files"], {})
    if file not in files:
        raise LookupError(
            f"the pinned version does not carry {file} — refusing to "
            "guess what its text was.")
    text = files[file]
    (Path(manuscript["path"]) / file).write_text(text, encoding="utf-8")
    return text
