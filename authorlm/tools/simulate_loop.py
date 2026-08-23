"""Tune the loop's thresholds against the real corpus instead of guessing.

Two questions the design deliberately left to measurement:

A. What Jaccard threshold should the firewall use? Too low and it eats
   materially different proposals; too high and paraphrase churn survives.

B. Does belief count N really grow like O(log M) once semantic matching
   works — i.e. is a volume-adaptive promotion threshold needed at all?

Runs against a COPY of the live database; the original is never written.

    python3 tools/simulate_loop.py            # part A only (no LLM calls)
    python3 tools/simulate_loop.py --distil   # A + B (spends LLM calls)
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import tomllib
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from authorlm import loop  # noqa: E402
from authorlm.db import Database, loads  # noqa: E402

LIVE = Path(os.path.expanduser("~/.authorlm/authorlm.db"))
THRESHOLDS = [0.50, 0.60, 0.65, 0.70, 0.72, 0.75, 0.80, 0.85, 0.90]


def copy_db() -> Database:
    tmp = Path(tempfile.mkdtemp(prefix="authorlm-sim-")) / "sim.db"
    shutil.copy(LIVE, tmp)
    return Database(tmp)


def part_a(db: Database, manuscript_id: str) -> float:
    """Replay every proposal in creation order at each threshold and count
    what the firewall would have suppressed."""
    rows = [dict(r) for r in db.all(
        "SELECT * FROM knowledge_proposals WHERE manuscript_id = ? "
        "ORDER BY created_at", (manuscript_id,))]
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in rows:
        groups[(r["kind"], r["target"])].append(r)

    print(f"\nPART A — firewall sweep over {len(rows)} proposals "
          f"in {len(groups)} (kind, target) groups\n")
    print(f"  {'thresh':>7}  {'suppressed':>10}  {'%':>6}   survivors")
    results = {}
    for t in THRESHOLDS:
        suppressed, kept_total = 0, 0
        examples = []
        for (kind, _target), items in groups.items():
            spec = loop.REGISTRY.get(f"proposals/{kind}")
            if spec is None:
                kept_total += len(items)
                continue
            kept: list[tuple[str, str]] = []
            for r in items:
                text = spec.dedupe_text(r)
                hit = loop.near_duplicate(text, kept, threshold=t)
                if hit:
                    suppressed += 1
                    if len(examples) < 3 and t == 0.72:
                        prior = next(x for i, x in kept if i == hit[0])
                        examples.append((round(hit[1], 3), prior, text))
                else:
                    kept.append((r["id"], text))
            kept_total += len(kept)
        results[t] = suppressed
        print(f"  {t:>7.2f}  {suppressed:>10}  {100*suppressed/len(rows):>5.1f}%   "
              f"{kept_total}")
    return results


def part_a_precision(db: Database, manuscript_id: str, threshold: float,
                     limit: int = 6) -> None:
    """Print the pairs the firewall would collapse, so the threshold is
    judged on what it actually eats rather than on a count."""
    rows = [dict(r) for r in db.all(
        "SELECT * FROM knowledge_proposals WHERE manuscript_id = ? "
        "AND kind = 'note_update' ORDER BY created_at", (manuscript_id,))]
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        groups[r["target"]].append(r)
    spec = loop.spec_for("proposals/note_update")
    by_id = {r["id"]: r for r in rows}
    print(f"\n  suppressed pairs at {threshold} (judge these by eye):")
    shown = 0
    for items in groups.values():
        kept: list[tuple[str, str]] = []
        for r in items:
            text = spec.dedupe_text(r)
            hit = loop.near_duplicate(text, kept, threshold=threshold)
            if hit and shown < limit:
                prior_row = by_id[hit[0]]
                print(f"\n    [{hit[1]:.2f}] kept: "
                      f"{loads(prior_row['payload'], {}).get('proposed_note','')[:96]}")
                print(f"           cut : "
                      f"{loads(r['payload'], {}).get('proposed_note','')[:96]}")
                shown += 1
            elif not hit:
                kept.append((r["id"], text))


def part_b(db: Database, manuscript: dict) -> None:
    """Replay real dismissal explanations through the real distiller and
    watch how belief count N grows with explanations processed M."""
    from authorlm import beliefs as bel
    from authorlm.llm import LLMClient

    cfg = tomllib.load(open(os.path.expanduser("~/.authorlm/config.toml"), "rb"))
    llm = LLMClient(cfg)
    if not llm.enabled:
        print("\nPART B skipped — no LLM configured")
        return

    mid = manuscript["id"]
    explanations = []
    for r in db.all(
            "SELECT * FROM evidence WHERE manuscript_id = ? "
            "AND evidence_type = 'proposal_review' AND signal = 'dismissed' "
            "ORDER BY created_at", (mid,)):
        meta = loads(r["metadata"], {}) or {}
        text = meta.get("explanation")
        if not text:
            # Pre-fix rows kept the reason only in the display line, clipped
            # at 200 chars — the truncation this build removed. Recover what
            # is there so the simulation has a corpus at all.
            _, _, tail = (r["target"] or "").partition(" — ")
            text = tail
        if text:
            explanations.append(text)

    print(f"\nPART B — {len(explanations)} real dismissal explanations "
          f"through the live distiller\n")
    print(f"  {'M':>4}  {'N':>4}   newest belief")
    db.conn.execute("DELETE FROM editorial_beliefs WHERE source LIKE 'triage-%'")
    db.conn.commit()
    curve = []
    for m, text in enumerate(explanations, 1):
        out = bel.seed_candidate_belief(db, mid, text,
                                        source="triage-note_update", llm=llm)
        n = db.one("SELECT COUNT(*) AS n FROM editorial_beliefs "
                   "WHERE manuscript_id = ? AND source = 'triage-note_update'",
                   (mid,))["n"]
        curve.append((m, n))
        if m % 5 == 0 or m == len(explanations):
            statement = (out or {}).get("statement", "(declined)")
            print(f"  {m:>4}  {n:>4}   {str(statement)[:80]}")

    print("\n  final beliefs:")
    for r in db.all("SELECT * FROM editorial_beliefs WHERE manuscript_id = ? "
                    "AND source = 'triage-note_update' "
                    "ORDER BY supporting DESC", (mid,)):
        print(f"    [{r['status']:<9} {r['supporting']}+/{r['contradicting']}-] "
              f"{r['statement'][:88]}")
    m_final, n_final = curve[-1]
    import math
    print(f"\n  M={m_final}  N={n_final}   "
          f"log10(M)={math.log10(m_final):.2f}  "
          f"N/log10(M)={n_final/math.log10(m_final):.2f}")


def main() -> None:
    db = copy_db()
    ms = dict(db.one("SELECT * FROM manuscripts WHERE name = ?", ("SMSTTD",)))
    print(f"simulating against a copy of {LIVE} — manuscript {ms['name']}")
    part_a(db, ms["id"])
    part_a_precision(db, ms["id"], 0.72)
    if "--distil" in sys.argv:
        part_b(db, ms)


if __name__ == "__main__":
    main()
