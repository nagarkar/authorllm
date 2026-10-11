"""FILTERED PREFIX must be THIS RUN's proposals over live disk units.

`filtering.filtered_prefix` overlays earlier units with `proposed_new`
so a sequential window is autoregressive. `api.filter_run` used to hand
it every `origin_type=filter` thread on the file — including a prior
settled run's `cleaned` rows and another active filter's proposals —
so the prefix could show stale accepted text after an author disk edit,
or another filter's open rewrites as if they were this run's. The model
then judged later units against fiction.

This file pins the assemble wiring: after a settled foreign cleaned
row and an author rewrite on disk, a new run's later window must show
the disk text, not the foreign proposal.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm import api, staging
from authorlm.db import Database, ko_fields
from authorlm.filtering import filtered_prefix

SEQ_FILTER = (
    '---\nclass = "sequential"\n'
    'state = "a ledger of every word already flagged as repeated"\n---\n\n'
    "# Duplicate words and phrases\n\n"
    "Flag a word or phrase used again too soon.\n"
)


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def _manuscript(root: Path, essay: str) -> tuple:
    db = Database(root / "authorlm.db")
    ms = root / "ms"
    ms.mkdir()
    (ms / "essay.md").write_text(essay, encoding="utf-8")
    (ms / "_filters").mkdir()
    (ms / "_filters" / "dup.md").write_text(SEQ_FILTER, encoding="utf-8")
    (ms / "_filters" / "tone.md").write_text(
        SEQ_FILTER.replace("Duplicate words", "Tone"), encoding="utf-8")
    row = ko_fields("ms")
    row.update(name="book", path=str(ms))
    db.insert("manuscripts", row)
    manuscript = dict(db.one("SELECT * FROM manuscripts WHERE id = ?",
                             (row["id"],)))
    return db, manuscript, ms


def test_settled_cleaned_does_not_hide_author_disk_edit() -> None:
    print("settled cleaned + author disk edit → prefix shows disk:")
    root = Path(tempfile.mkdtemp())
    settled_essay = (
        "Unit 1 unchanged.\n\n"
        "Unit 2 SETTLED accepted replacement.\n\n"
        "Unit 3 for the new window.\n\n"
        "Unit 4 more.\n"
    )
    db, manuscript, ms = _manuscript(root, settled_essay)
    mid = manuscript["id"]

    # Prior settled run left a cleaned thread whose proposed_new matches
    # the text it landed — then the author rewrote unit 2 on disk.
    staged = staging.stage_edits(
        db, mid, "runSETTLED", "essay.md", settled_essay,
        [{"n": 2, "new": "Unit 2 SETTLED accepted replacement.",
          "why": "prior"}],
        origin_type=api.FILTER_ORIGIN)
    db.update("doc_threads", staged[0]["id"], {"state": "cleaned"})
    author_essay = (
        "Unit 1 unchanged.\n\n"
        "Unit 2 AUTHOR rewrote this after the last settle.\n\n"
        "Unit 3 for the new window.\n\n"
        "Unit 4 more.\n"
    )
    (ms / "essay.md").write_text(author_essay, encoding="utf-8")

    # Reproduce the old poison: file-wide door_threads including cleaned.
    poisoned = staging.door_threads(
        db, mid, "essay.md",
        states=("proposed", "accepted", "rejected", "written", "cleaned"),
        origin_type=api.FILTER_ORIGIN)
    units = author_essay.strip().split("\n\n")
    bad = filtered_prefix(units, 3, poisoned)
    check("unscoped door_threads still poisons filtered_prefix alone",
          "SETTLED accepted" in bad and "AUTHOR rewrote" not in bad, bad)

    # The shipped path: filter_run → assemble with _run_threads.
    info = api.filter_run(db, manuscript, {}, "dup", "essay.md",
                          window=1, from_unit=3)
    prefix = info["payload"].prefix
    check("filter_run prefix carries the author disk rewrite",
          "AUTHOR rewrote" in prefix, prefix)
    check("filter_run prefix does not carry the settled proposal",
          "SETTLED accepted" not in prefix, prefix)


def test_other_active_filter_proposals_stay_out_of_prefix() -> None:
    print("second active filter does not read the first's proposals:")
    root = Path(tempfile.mkdtemp())
    essay = (
        "Alpha unit with moreover.\n\n"
        "Beta unit with moreover.\n\n"
        "Gamma unit for the window.\n\n"
        "Delta trailing.\n"
    )
    db, manuscript, ms = _manuscript(root, essay)
    mid = manuscript["id"]

    # First filter active with a proposal on unit 1.
    api.filter_run(db, manuscript, {}, "dup", "essay.md", window=1)
    units = essay.strip().split("\n\n")
    from authorlm import passes
    reply = __import__("json").dumps({
        "units": [{
            "n": 1, "echo": passes.echo_of(units[0]),
            "action": "replace", "new": "Alpha CLARITY rewrite.",
            "why": "drop moreover",
        }],
        "state": "ledger: moreover@1",
    })
    api.filter_record(db, manuscript, {}, "essay.md", reply, name="dup")

    # Second filter on the same file, window starting at unit 3.
    info = api.filter_run(db, manuscript, {}, "tone", "essay.md",
                          window=1, from_unit=3)
    prefix = info["payload"].prefix
    check("tone prefix keeps the live disk Alpha unit",
          "Alpha unit with moreover" in prefix, prefix)
    check("tone prefix does not absorb clarity's rewrite",
          "CLARITY rewrite" not in prefix, prefix)


def test_same_run_still_overlays_its_own_proposals() -> None:
    print("same run's earlier window still overlays the prefix:")
    root = Path(tempfile.mkdtemp())
    essay = (
        "One moreover here.\n\n"
        "Two moreover here.\n\n"
        "Three for the next window.\n"
    )
    db, manuscript, _ms = _manuscript(root, essay)
    api.filter_run(db, manuscript, {}, "dup", "essay.md", window=1)
    units = essay.strip().split("\n\n")
    from authorlm import passes
    reply = __import__("json").dumps({
        "units": [{
            "n": 1, "echo": passes.echo_of(units[0]),
            "action": "replace", "new": "One THIS RUN rewrite.",
            "why": "drop",
        }],
        "state": "ledger: moreover@1",
    })
    api.filter_record(db, manuscript, {}, "essay.md", reply, name="dup")
    info = api.filter_run(db, manuscript, {}, "dup", "essay.md", window=1)
    prefix = info["payload"].prefix
    check("continuing run shows its own earlier proposal in the prefix",
          "THIS RUN rewrite" in prefix, prefix)


def main() -> None:
    tests = [
        test_settled_cleaned_does_not_hide_author_disk_edit,
        test_other_active_filter_proposals_stay_out_of_prefix,
        test_same_run_still_overlays_its_own_proposals,
    ]
    for t in tests:
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
