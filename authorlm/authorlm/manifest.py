"""The Reader load — a DERIVED report, recomputed on every push.

It lives in the master Doc's `manifest` tab, under the chapter inventory
(`gdocs.manifest_text`), and nowhere else: there is no local file and no
tab of its own. Until 2026-10-19 it was a `manifest.md` sidecar pushed as an
ordinary tab; one derived tab is enough (ruled by the author 2026-10-19).

It answers one question, per essay and across the book: **what does this
essay assume that no earlier essay ever supplied?** For essay N that is
`ASSUMES(N)` minus everything essays 1..N-1 `INTRODUCED` — set arithmetic
over the PARAGRAPH RECORDS (summary-hierarchy-design.md ruling 6, §6.6;
its declaration is `INPUTS` below): a term an essay introduces or defines
with no earlier such mention is INTRODUCED there, a term it only uses is
ASSUMED, normalized against the concept graph's names and aliases so that
"The Dharma", "Dharma" and an alias of either collapse to one concept.
Until 2026-10-25 both lists were prose written by the essay summarizer and
parsed back; a fraction of every debt column was sentence fragments. The
lists are now exact: every term is a mention with a verbatim span.

**It is DERIVED, and it is never a source of truth.** Nothing reads it back;
every push recomputes it from scratch, and the `manifest` tab is ignored on
pull like the rest of that tab.

**It never blocks anything, and it builds nothing.** An essay with no
paragraph records, or with stale ones (paragraphs the table has no row
for), is rendered with the `!!` marker `write status` uses and the row is
still shown. A report that will not appear when the book is mid-flight is a
report that is never there when it is wanted; the push that wants exact
rows runs `summarize rebuild` first.
"""

from __future__ import annotations

import re

from .context import Input
from .db import Database, loads

# The reader load's declaration (ruling 10).
INPUTS = (Input.PARAGRAPH_RECORDS,)


def _canonical_names(db: Database, manuscript_id: str) -> dict[str, str]:
    """Every graph name and alias, lowercased, to its concept's own name."""
    out: dict[str, str] = {}
    for row in db.all(
            "SELECT name, aliases FROM concept_nodes WHERE manuscript_id = ? "
            "AND status != 'retired'", (manuscript_id,)):
        for name in [row["name"], *loads(row["aliases"], [])]:
            if name and name.strip():
                out[name.strip().lower()] = row["name"]
    return out


def _normalize(term: str, canon: dict[str, str]) -> str | None:
    t = re.sub(r"[*`_]", "", term).strip().strip(".").strip()
    t = re.sub(r"^(the|a|an)\s+", "", t, flags=re.I)
    if len(t) < 3:
        return None
    return canon.get(t.lower(), t)


def compute(db: Database, manuscript: dict) -> list[dict]:
    """One row per essay in reading order, each with its unmet debt.

    A term counts as SUPPLIED once an earlier essay has either introduced it
    or leaned on it: the second time a reader meets a word is not the moment
    they first lacked it. So the debt column is FIRST unmet appearance, not
    standing debt, and it is the shape of an on-ramp rather than a tally of
    sins.
    """
    from . import summaries as sums

    canon = _canonical_names(db, manuscript["id"])
    texts, _unlisted, flight = sums.capture(db, manuscript)
    verse = sums.verse_files_of(manuscript)

    rows, supplied = [], set()
    for file, text in texts.items():
        pinned = flight.get(file, sums.NOT_IN_FLIGHT)
        level = sums.level_status(db, manuscript, file, text, pinned,
                                  verse_file=file in verse)["paragraph"]
        state = "unwritten" if pinned is None else level["state"]
        records = sums.paragraph_records(db, manuscript, files=[file],
                                         texts={file: text})
        lists = sums.derived_lists(db, manuscript, file, records, texts)
        introduces = {t for t in (_normalize(x, canon)
                                  for x in lists["introduces"]) if t}
        assumes = {t for t in (_normalize(x, canon)
                               for x in lists["assumes"]) if t}
        debt = sorted(assumes - supplied)
        rows.append({"file": file, "words": len(text.split()),
                     "introduces": len(introduces), "debt": debt,
                     "state": state})
        supplied |= introduces | assumes
    return rows


def render_lines(rows: list[dict]) -> list[str]:
    """The report as plain-text lines for the `manifest` tab, which is
    written as text rather than converted from markdown — so no tables and
    no heading marks, the same shape as the inventory above it."""
    out = ["", "Reader load — for each essay, what it assumes that no "
               "earlier essay supplied."]
    if not rows:
        return out + ["No paragraph records on record yet. Run 'authorlm "
                      "summarize rebuild'."]
    worst = max((len(r["debt"]) for r in rows), default=0) or 1
    stale = [r["file"] for r in rows if r["state"] != "fresh"]
    if stale:
        out.append(
            f"{len(stale)} of {len(rows)} essays have missing or stale "
            "paragraph records, and their rows are marked !!. A marked row "
            "counts only the paragraphs that have a record — "
            "'authorlm summarize rebuild' clears this.")
    out.append("")
    for r in rows:
        bar = "█" * round(len(r["debt"]) / worst * 20)
        mark = "" if r["state"] == "fresh" else f"  !! {r['state']}"
        out.append(f"{r['file']} — introduces {r['introduces']} · debt "
                   f"{len(r['debt'])}  {bar}{mark}".rstrip())

    out += ["", "Debts — terms the essay uses that no essay before it "
                "introduced or defined (from the paragraph records; each is "
                "a mention with a verbatim span)."]
    debts = [r for r in rows if r["debt"]]
    for r in debts:
        mark = "" if r["state"] == "fresh" else f" (!! {r['state']})"
        out.append(f"{r['file']}{mark} — " + ", ".join(r["debt"]))
    if not debts:
        out.append("None. Every essay stands on ground an earlier one laid.")
    return out
