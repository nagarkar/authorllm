"""Shared readings — one whole-essay reading for a family of global filters
(issue #412).

A GLOBAL filter begins with a prelude: one model call that reads the essay
whole and returns a REGISTRY, frozen into that run and shown beside every
unit. The units are then judged paragraph by paragraph against it. The
prelude is the only whole-essay analysis; this module changes WHERE its
result is kept, never how a unit is judged.

**The reading.** Filters whose front matter says `reading = "<group>"`
(the three strunk-and-white passes say `reading = "strunk-and-white"`)
share one reading per essay, stored in `filter_readings` and keyed by
(manuscript, group, file). The first prelude in the group reads the essay
and records the reading; every later prelude in the group on that essay
reuses it, with no model call, so the passes cannot read the essay two
ways. The reading is made ONCE: edits that land between passes do not
refresh it (it describes the essay's form, which line edits do not
change). Only `filter prelude <name> <essay> --replace` reads the essay
again, and records the new reading for the passes that follow.

**Robustness.** Every run copies the reading into its own
`filter_runs.registry` at prelude time and never reads this table again:
the shared row is a source to copy from, not a live reference. So a
`--replace`, or a parallel run, can never change what a live run is
judging against. Recording is one transaction (supersede the old current
row, insert the new one), so there is at most one current reading per
(group, file); rows are superseded, never edited.
"""

from __future__ import annotations

from .db import Database, ko_fields, now_iso


def current(db: Database, manuscript_id: str, group: str,
            file: str) -> dict | None:
    row = db.one(
        "SELECT * FROM filter_readings WHERE manuscript_id = ? AND "
        "reading = ? AND file = ? AND status = 'current' "
        "ORDER BY created_at DESC LIMIT 1", (manuscript_id, group, file))
    return dict(row) if row else None


def record(db: Database, manuscript_id: str, group: str, file: str,
           registry: str, run_id: str) -> dict:
    """Make `registry` the current reading of `file` for `group`, in one
    transaction: every earlier current row is superseded, never edited."""
    row = ko_fields("rd")
    row.update(manuscript_id=manuscript_id, reading=group, file=file,
               registry=registry, run_id=run_id, status="current")
    with db.transaction():
        for old in db.all(
                "SELECT id FROM filter_readings WHERE manuscript_id = ? AND "
                "reading = ? AND file = ? AND status = 'current'",
                (manuscript_id, group, file)):
            db.update("filter_readings", old["id"],
                      {"status": "superseded", "superseded_at": now_iso()})
        db.insert("filter_readings", row)
    return row
