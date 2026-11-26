"""Named snapshots of the style laws (`style_snapshots`, `style snapshot`).

Source: the owner's words of 2026-11-25 (docs/plans/filter-lens-
consolidation.md 11.1): "can we name the style laws currently in place
with a version (e.g. v1-2026-full-on-style-laws)? Then, can we deprecate
a particular named version and carry forward only those laws that are
'lint style rules'", and the build plan's piece 4 (section 12). These
checks pin that a snapshot records every active law of one manuscript,
that a name is taken once, and that a restore after retiring, editing,
moving and adding laws brings back exactly the recorded set, words and
scope included, with every change in the laws' history.

Hermetic: a temp workspace and a temp defaults file; no network, no model.
Run: python3 tests/test_style_snapshots.py
"""
from __future__ import annotations

import contextlib
import io
import os
import sys
import tempfile
from pathlib import Path

os.environ["AUTHORLM_CONFIG"] = "/nonexistent/authorlm-test/config.toml"
os.environ["AUTHORLM_ENV"] = "/nonexistent/authorlm-test/.env"
os.environ["AUTHORLM_CLIENT"] = "none"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm import api, style_snapshots as ss, styles  # noqa: E402
from authorlm.cli import main as cli_main  # noqa: E402
from authorlm.db import Database  # noqa: E402


def check(label: str, cond: bool, detail: object = "") -> None:
    print(f"  [{'ok' if cond else 'FAIL'}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def _book(root: Path, name: str):
    ws = root / name
    (ws / ".authorlm").mkdir(parents=True)
    (ws / "defaults.toml").write_text("", encoding="utf-8")
    os.environ[styles.DEFAULTS_ENV] = str(ws / "defaults.toml")
    ms = ws / "book"
    ms.mkdir()
    for f in ("a.md", "b.md"):
        (ms / f).write_text(f"# {f}\n\nText.\n", encoding="utf-8")
    db = Database(ws / ".authorlm" / "authorlm.db")
    manuscript = dict(api.register_manuscript(db, "book", str(ms)))
    api.define_style_guide(db, manuscript, "House")
    api.define_style_guide(db, manuscript, "Part", parent="House")
    api.attach_style(db, manuscript, "a.md", "Part")
    laws = [
        api.add_style_law(db, manuscript, "lexicon", "Use everyday words.",
                          guide_name="House", notes="Inspect: jargon."),
        api.add_style_law(db, manuscript, "structure", "One question per essay.",
                          guide_name="Part"),
        api.add_style_law(db, manuscript, "figure", "No physics metaphors.",
                          guide_name="House"),
        api.add_style_law(db, manuscript, "tone", "Plain, never arch.",
                          guide_name="House"),
    ]
    # Retired before the snapshot: not part of it.
    old = api.add_style_law(db, manuscript, "tone", "Old tone law.",
                            guide_name="House")
    api.retire_style_law(db, manuscript, old["id"], reason="superseded")
    return ws, db, manuscript, [l["id"] for l in laws], old["id"]


def _state(db, mid: str) -> dict:
    """Every active law: id → (aspect, statement, notes, overrides, scope)."""
    return {r["id"]: (r["aspect"], r["statement"], r["notes"], r["overrides"],
                      r["guide_id"], r["file"])
            for r in db.all("SELECT * FROM style_laws WHERE manuscript_id = ? "
                            "AND status = 'active'", (mid,))}


def _cli(ws: Path, *argv: str) -> str:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        cli_main(["--workspace", str(ws), *argv, "-m", "book"])
    return out.getvalue()


def snapshot_records_the_active_set(root: Path) -> None:
    ws, db, ms, ids, old = _book(root, "take")
    snap = ss.take(db, ms["id"], "v1", note="the full set")
    check("a snapshot records every active law", snap["laws"] == 4, snap["laws"])
    shown = ss.show(db, ms["id"], "v1")
    check("show lists the recorded laws with their versions",
          sorted(l["id"] for l in shown["laws"]) == sorted(ids)
          and all(l["version"] >= 1 for l in shown["laws"]))
    check("a retired law is not in the snapshot",
          old not in {l["id"] for l in shown["laws"]})
    check("the note is kept", shown["note"] == "the full set")
    try:
        ss.take(db, ms["id"], "v1")
        check("a duplicate name is declined", False)
    except ValueError as err:
        check("a duplicate name is declined", "already exists" in str(err), err)
    check("list shows the snapshot with its count",
          [(s["name"], s["laws"]) for s in ss.list_snapshots(db, ms["id"])]
          == [("v1", 4)])
    other = dict(api.register_manuscript(db, "other", str(ws / "book")))
    check("a snapshot is per manuscript: another may take the same name",
          ss.take(db, other["id"], "v1")["laws"] == 0)


def restore_brings_back_exactly_the_set(root: Path) -> None:
    ws, db, ms, ids, old = _book(root, "restore")
    mid = ms["id"]
    before = _state(db, mid)
    ss.take(db, mid, "v1")
    # After the snapshot: retire two, edit one's words, move one, add one,
    # and bring back a law retired before the snapshot.
    api.retire_style_law(db, ms, ids[0], reason="lint sort")
    api.retire_style_law(db, ms, ids[2], reason="lint sort")
    api.edit_style_law(db, ms, ids[3], statement="Plain and warm.", notes="new note")
    api.move_style_law(db, ms, ids[1], guide_name="House")
    new = api.add_style_law(db, ms, "syntax", "Short sentences.", guide_name="House")
    api.unretire_style_law(db, ms, old)
    check("the set has changed", _state(db, mid) != before)

    dry = ss.restore(db, mid, "v1", dry_run=True)
    check("dry run names the laws to bring back",
          sorted(l["id"] for l in dry["unretire"]) == sorted([ids[0], ids[2]]))
    check("dry run names the words to put back",
          [l["id"] for l in dry["edit"]] == [ids[3]]
          and set(dry["edit"][0]["diff"]) == {"statement", "notes"})
    check("dry run names the move back", [l["id"] for l in dry["move"]] == [ids[1]])
    check("dry run names the laws to retire",
          sorted(l["id"] for l in dry["retire"]) == sorted([new["id"], old]))
    check("dry run changes nothing", _state(db, mid) != before
          and new["id"] in _state(db, mid))

    out = _cli(ws, "style", "snapshot", "restore", "v1")
    check("the CLI restore reports what it changed", "changed 6 law(s)" in out, out)
    check("after the restore the active set equals the snapshot's exactly",
          _state(db, mid) == before, (_state(db, mid), before))
    hist = api.style_history(db, ms, ids[0])
    check("the restore is in each law's history with the snapshot's name",
          hist[-1]["event"] == "unretire"
          and "v1" in (hist[-1]["detail"].get("reason") or ""), hist[-1])
    hist = api.style_history(db, ms, new["id"])
    check("a law added since is retired citing the snapshot",
          hist[-1]["event"] == "retire" and "v1" in hist[-1]["detail"]["reason"])
    again = ss.restore(db, mid, "v1", dry_run=True)
    check("a second restore has nothing to change",
          not any(again[k] for k in ("unretire", "edit", "move", "retire")))


def cli_verbs(root: Path) -> None:
    ws, db, ms, ids, old = _book(root, "cli")
    out = _cli(ws, "style", "snapshot", "v1-full", "--note", "before the sort")
    check("snapshot <name> takes one", "4 active law(s)" in out, out)
    out = _cli(ws, "style", "snapshot", "list")
    check("snapshot list names it with its count and note",
          "v1-full — 4 law(s)" in out and "before the sort" in out, out)
    out = _cli(ws, "style", "snapshot", "show", "v1-full")
    check("snapshot show lists every law", "4 law(s)" in out
          and "One question per essay." in out and "now: 4 active" in out, out)
    try:
        _cli(ws, "style", "snapshot", "v1-full")
        check("a duplicate name exits with a message", False)
    except SystemExit as err:
        check("a duplicate name exits with a message",
              "already exists" in str(err.code), err.code)
    api.retire_style_law(db, ms, ids[0])
    out = _cli(ws, "style", "snapshot", "restore", "v1-full", "--dry-run")
    check("restore --dry-run prints what it would change",
          "would change 1 law(s)" in out and "back in force" in out, out)
    check("restore --dry-run changes nothing",
          ids[0] not in _state(db, ms["id"]))


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for t in (snapshot_records_the_active_set,
                  restore_brings_back_exactly_the_set, cli_verbs):
            print(f"{t.__name__}:")
            t(root)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
