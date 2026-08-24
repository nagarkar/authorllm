"""Distil beliefs from the author's decisions already on record.

The loop learns from explained verdicts, but every verdict made before the
loop existed went into `evidence` and was never distilled. This is a
one-time (repeatable) backfill over that history.

It invents nothing: every explanation fed to the distiller is the author's
own recorded reason. Verdicts are NOT made here — decisions the author has
not taken stay untaken.

    python3 tools/bootstrap_beliefs.py            # dry run, on a copy
    python3 tools/bootstrap_beliefs.py --apply    # write to the live DB
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import tomllib
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from authorlm import beliefs as bel  # noqa: E402
from authorlm.db import Database, loads  # noqa: E402
from authorlm.llm import LLMClient  # noqa: E402

LIVE = Path(os.path.expanduser("~/.authorlm/authorlm.db"))

# Which recorded verdicts carry an author's reason, and which belief source
# they belong to. Sources matter: they scope the distiller's match menu and
# set the promotion bar (beliefs.VALIDATE_MIN_SUPPORT_BY_SOURCE).
CHANNELS = [
    ("critique_triage", ("rejected",), "critique-triage"),
    ("edge_triage", ("retyped", "rejected"), "triage-edge"),
    ("author_review", ("rejected", "modified"), "review-explanation"),
    ("illus_triage", ("rejected", "modified"), "margin-thread"),
    ("margin_thread", ("accepted", "modified"), "margin-thread"),
    # improvement_task is deliberately absent: its "close" notes are
    # engineering records about AuthorLM itself ("compact_briefing keeps all
    # rows — 144KB compact MCP briefing"), not editorial judgments about the
    # manuscript. Distilling them produced "Verify and close improvements."
    # with 12 supports — a contentless belief that would then screen the
    # author's proposals. A channel must carry reasons ABOUT THE WRITING.
]

# proposal_review rows predate the metadata fix, so their reason lives after
# the em-dash in `target`. The summary prefix identifies the proposal kind,
# which decides the belief source — a note_update lesson must not land in
# the same pool as an alias lesson.
PROPOSAL_KINDS = [
    ("reframe '", "triage-note_update"),
    ("the text identifies", "triage-alias"),
    ("revive retired concept", "triage-revival"),
    ("reconsider rejected relationship", "triage-edge_reproposal"),
    ("no longer appears anywhere", "triage-vanished"),
    ("resembles retired", "triage-variant_of_retired"),
]


def explanation_of(row: dict) -> str | None:
    meta = loads(row["metadata"], {}) or {}
    if meta.get("explanation"):
        return meta["explanation"]
    _, sep, tail = (row["target"] or "").partition(" — ")
    return tail.strip() if sep and len(tail.strip()) > 15 else None


def harvest(db: Database, mid: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = defaultdict(list)
    for etype, signals, source in CHANNELS:
        marks = ", ".join("?" for _ in signals)
        for row in db.all(
                f"SELECT * FROM evidence WHERE manuscript_id = ? "
                f"AND evidence_type = ? AND signal IN ({marks}) "
                "ORDER BY created_at", (mid, etype, *signals)):
            text = explanation_of(dict(row))
            if text:
                out[source].append(text)
    for row in db.all(
            "SELECT * FROM evidence WHERE manuscript_id = ? "
            "AND evidence_type = 'proposal_review' AND signal = 'dismissed' "
            "ORDER BY created_at", (mid,)):
        row = dict(row)
        text = explanation_of(row)
        if not text:
            continue
        target = (row["target"] or "").lower()
        source = next((s for frag, s in PROPOSAL_KINDS if frag in target),
                      "triage-note_update")
        out[source].append(text)
    return out


def main() -> None:
    apply = "--apply" in sys.argv
    path = LIVE
    if not apply:
        path = Path(tempfile.mkdtemp(prefix="authorlm-boot-")) / "sim.db"
        shutil.copy(LIVE, path)
    db = Database(path)
    ms = dict(db.one("SELECT * FROM manuscripts WHERE name = ?", ("SMSTTD",)))
    cfg = tomllib.load(open(os.path.expanduser("~/.authorlm/config.toml"), "rb"))
    llm = LLMClient(cfg)
    if not llm.enabled:
        sys.exit("no LLM configured")

    if "--reset" in sys.argv:
        db.conn.execute("DELETE FROM editorial_beliefs WHERE manuscript_id = ? "
                        "AND (source LIKE 'triage-%' OR source IN "
                        "('critique-triage', 'margin-thread'))", (ms["id"],))
        db.conn.commit()
        print("(reset: cleared previously bootstrapped beliefs)\n")

    corpus = harvest(db, ms["id"])
    total = sum(len(v) for v in corpus.values())
    print(f"{'APPLYING to live DB' if apply else 'DRY RUN (copy)'} — "
          f"{total} recorded author explanations\n")
    for source, items in sorted(corpus.items()):
        print(f"  {source:<26} {len(items):>4}")

    print("\ndistilling (each explanation matches an existing belief or "
          "starts a new one)…\n")
    made = 0
    for source, items in sorted(corpus.items()):
        before = {r["id"] for r in bel.live_beliefs(db, ms["id"], source)}
        for text in items:
            try:
                bel.seed_candidate_belief(db, ms["id"], text, source=source,
                                          llm=llm)
            except Exception as err:  # noqa: BLE001
                print(f"    ! {source}: {type(err).__name__}: {err}")
        rows = bel.live_beliefs(db, ms["id"], source)
        fresh = [r for r in rows if r["id"] not in before]
        made += len(fresh)
        print(f"  {source}  ({len(items)} explanations → {len(fresh)} new "
              f"belief(s))")
        for r in sorted(rows, key=lambda x: -x["supporting"]):
            if r["id"] in before:
                continue
            print(f"    [{r['status']:<9} {r['supporting']}+/"
                  f"{r['contradicting']}-] {r['statement'][:88]}")
    print(f"\n{made} belief(s) created from {total} of the author's own "
          "recorded decisions.")
    if not apply:
        print("(dry run — nothing written to the live database)")


if __name__ == "__main__":
    main()
