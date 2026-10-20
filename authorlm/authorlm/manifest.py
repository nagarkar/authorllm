"""The Reader load — a DERIVED report, recomputed on every push.

It lives in the master Doc's `manifest` tab, under the chapter inventory
(`gdocs.manifest_text`), and nowhere else: there is no local file and no
tab of its own. Until 2026-10-19 it was a `manifest.md` sidecar pushed as an
ordinary tab; one derived tab is enough (ruled by the author 2026-10-19).

It answers one question, per essay and across the book: **what does this
essay assume that no earlier essay ever supplied?** For essay N that is
`ASSUMES(N)` minus everything essays 1..N-1 `INTRODUCED` — set arithmetic
over the stored summaries, normalized against the concept graph's names and
aliases so that "The Dharma", "Dharma" and an alias of either collapse to
one concept.

**It is DERIVED, and it is never a source of truth.** Nothing reads it back;
every push recomputes it from scratch, and the `manifest` tab is ignored on
pull like the rest of that tab.

**It never blocks anything.** The summaries it reads can be missing, stale,
deprecated or upstream-stale; every one of those is rendered with the `!!`
marker `write status` uses and the row is still shown. A report that will
not appear when the book is mid-flight is a report that is never there when
it is wanted.

The known limitation, stated because the numbers look more exact than they
are: `ASSUMES` and `INTRODUCES` are prose lists written by the summarizer,
not a controlled vocabulary, so a fraction of every debt column is sentence
fragments rather than concepts. Normalizing against the graph fixes the alias
half of that and cannot fix the fragment half. The real repair is upstream,
in the summarizer prompt.
"""

from __future__ import annotations

import re
from pathlib import Path

from .db import Database, loads

# The summary's own section labels (summaries.summarizer_prompt).
_ASSUMES = "ASSUMES"
_INTRODUCES = "INTRODUCES"


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


def _field(summary: str, key: str, canon: dict[str, str]) -> list[str]:
    """One labelled list out of a summary, normalized against the graph."""
    m = re.search(rf"^{key}:(.*?)(?=^[A-Z ]+:|\Z)", summary or "",
                  re.S | re.M)
    if not m:
        return []
    body = m.group(1).strip()
    if body.lower().startswith("none"):
        return []
    out = []
    for part in re.split(r"[;,]| and ", body):
        term = _normalize(part, canon)
        if term:
            out.append(term)
    return out


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
    root = Path(manuscript["path"])
    # `status` carries the reading order and each summary's freshness;
    # `all_summaries` carries the text. Two calls rather than one because
    # freshness is the thing this report must not hide, and `status` is the
    # single place the whole system decides it.
    stored = sums.all_summaries(db, manuscript["id"])
    order = sums.status(db, manuscript)

    rows, supplied = [], set()
    for entry in order:
        row = stored.get(entry["file"]) or {}
        summary = row.get("summary") or ""
        introduces = set(_field(summary, _INTRODUCES, canon))
        assumes = set(_field(summary, _ASSUMES, canon))
        debt = sorted(assumes - supplied)
        path = root / entry["file"]
        try:
            words = len(path.read_text(encoding="utf-8").split())
        except OSError:
            words = 0
        rows.append({"file": entry["file"], "words": words,
                     "introduces": len(introduces), "debt": debt,
                     "state": entry.get("state") or "missing"})
        supplied |= introduces | assumes
    return rows


def render_lines(rows: list[dict]) -> list[str]:
    """The report as plain-text lines for the `manifest` tab, which is
    written as text rather than converted from markdown — so no tables and
    no heading marks, the same shape as the inventory above it."""
    out = ["", "Reader load — for each essay, what it assumes that no "
               "earlier essay supplied."]
    if not rows:
        return out + ["No essay summaries on record yet. Run 'authorlm "
                      "summarize rebuild'."]
    worst = max((len(r["debt"]) for r in rows), default=0) or 1
    stale = [r["file"] for r in rows if r["state"] != "fresh"]
    if stale:
        out.append(
            f"{len(stale)} of {len(rows)} summaries are not fresh, and their "
            "rows are marked !!. A marked row describes the essay as it was "
            "when its summary was last built, not as it stands now — "
            "'authorlm summarize rebuild' clears this.")
    out.append("")
    for r in rows:
        bar = "█" * round(len(r["debt"]) / worst * 20)
        mark = "" if r["state"] == "fresh" else f"  !! {r['state']}"
        out.append(f"{r['file']} — introduces {r['introduces']} · debt "
                   f"{len(r['debt'])}  {bar}{mark}".rstrip())

    out += ["", "Debts — assumed by the essay, introduced by no essay "
                "before it. Some entries are sentence fragments rather than "
                "concepts: the summaries' lists are prose, not a controlled "
                "vocabulary."]
    debts = [r for r in rows if r["debt"]]
    for r in debts:
        mark = "" if r["state"] == "fresh" else f" (!! {r['state']})"
        out.append(f"{r['file']}{mark} — " + ", ".join(r["debt"]))
    if not debts:
        out.append("None. Every essay stands on ground an earlier one laid.")
    return out
