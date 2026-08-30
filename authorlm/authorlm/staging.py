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
