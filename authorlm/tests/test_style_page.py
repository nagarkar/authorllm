"""The Style laws page (`style_page.dispatch`, `/api/style`) and the verbs
it shares with the CLI: edit in place, retire with a reason, un-retire,
copy, move, history, and the near-duplicate warning on add.

Source: the owner's ask of 2026-11-25, "Add a Web UI page for me to browse,
edit, add, and retire style laws at different levels (manuscript wide,
specific to part or essay, selectable as a drop down)". Until then a law
was changed by retire + re-add, which gave it a new id and lost the link
from the old wording to the new; these checks pin that an edit keeps the
id (overrides and statements cite ids), keeps the earlier wording in
history, and that every write is the CLI's own function.

Hermetic: a temp workspace and a temp defaults file; no network, no model.
Run: python3 tests/test_style_page.py
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import threading
import urllib.request
from pathlib import Path

os.environ["AUTHORLM_CONFIG"] = "/nonexistent/authorlm-test/config.toml"
os.environ["AUTHORLM_ENV"] = "/nonexistent/authorlm-test/.env"
os.environ["AUTHORLM_CLIENT"] = "none"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm import api, critic, style_page, styles  # noqa: E402
from authorlm.cli import main as cli_main  # noqa: E402
from authorlm.db import Database  # noqa: E402

PASSED = 0

DEFAULTS = '''
[[default]]
key = "dash"
aspect = "formatting"
statement = "Closed em dash, no spaces."

[[default]]
key = "serial-comma"
aspect = "formatting"
statement = "Serial comma before the final and."
'''


def check(label: str, cond: bool, detail: object = "") -> None:
    global PASSED
    print(f"  [{'ok' if cond else 'FAIL'}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)
    PASSED += 1


def _book(root: Path, name: str):
    """house ← part ← recaps, house ← papers; a.md on part, r.md on
    recaps, p.md on papers, loose.md on nothing (so the house style)."""
    ws = root / name
    (ws / ".authorlm").mkdir(parents=True)
    (ws / "defaults.toml").write_text(DEFAULTS, encoding="utf-8")
    os.environ[styles.DEFAULTS_ENV] = str(ws / "defaults.toml")
    ms = ws / "book"
    ms.mkdir()
    for f in ("a.md", "r.md", "p.md", "loose.md"):
        (ms / f).write_text(f"# {f}\n\nText.\n", encoding="utf-8")
    db = Database(ws / ".authorlm" / "authorlm.db")
    manuscript = dict(api.register_manuscript(db, "book", str(ms)))
    for guide, parent in (("House", None), ("Part", "House"),
                          ("Recaps", "Part"), ("Papers", "House")):
        api.define_style_guide(db, manuscript, guide, parent=parent)
    for f, g in (("a.md", "Part"), ("r.md", "Recaps"), ("p.md", "Papers")):
        api.attach_style(db, manuscript, f, g)
    laws = {
        "plain": api.add_style_law(db, manuscript, "lexicon",
                                   "Use everyday words.", guide_name="House",
                                   notes="Owner: 'say it plainly'. Inspect: jargon."),
        "dash": api.add_style_law(db, manuscript, "formatting",
                                  "Em dashes explain inline.", guide_name="House",
                                  overrides="default:dash"),
        "img": api.add_style_law(db, manuscript, "illustration",
                                 "Ink drawings only.", guide_name="House"),
        "q": api.add_style_law(db, manuscript, "structure",
                               "One question per essay.", guide_name="Part"),
        "recap": api.add_style_law(db, manuscript, "structure",
                                   "A commentary opens on its sermon's question.",
                                   guide_name="Recaps"),
        "paper": api.add_style_law(db, manuscript, "citation",
                                   "Cite every claim.", guide_name="Papers"),
    }
    # The recap law displaces the part's question law for r.md.
    laws["recap2"] = api.add_style_law(
        db, manuscript, "structure", "End on the bridge to the next sermon.",
        guide_name="Recaps", overrides=laws["q"]["id"])
    return ws, db, manuscript, laws


def _call(ws: Path, method: str, **params):
    return style_page.dispatch(method, {"manuscript": "book", **params},
                               workspace=str(ws))


def _cli(ws: Path, *argv: str) -> str:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        cli_main(["--workspace", str(ws), *argv, "-m", "book"])
    return out.getvalue()


def _row(db, law_id: str) -> dict:
    return dict(db.one("SELECT * FROM style_laws WHERE id = ?", (law_id,)))


# ------------------------------------------------------------- the verbs

def edit_keeps_the_id_and_the_old_wording(root: Path) -> None:
    ws, db, manuscript, laws = _book(root, "edit")
    law = laws["plain"]
    out = api.edit_style_law(db, manuscript, law["id"][:9],
                             statement="Use the words of everyday speech.",
                             actor="web")
    row = _row(db, law["id"])
    check("an edit keeps the law's id", out["id"] == law["id"]
          and row["statement"] == "Use the words of everyday speech.")
    check("an edit bumps the version", row["version"] == law["version"] + 1,
          row["version"])
    check("an edit leaves the note alone when only the statement changes",
          row["notes"] == law["notes"])
    hist = api.style_history(db, manuscript, law["id"])
    events = [h["event"] for h in hist]
    check("history holds the add and the edit, oldest first",
          events == ["add", "edit"], events)
    edit = hist[-1]
    check("the edit records the earlier wording, the actor and the time",
          edit["detail"]["before"]["statement"] == "Use everyday words."
          and edit["detail"]["after"]["statement"]
          == "Use the words of everyday speech."
          and edit["actor"] == "web" and edit["at"] and edit["by"], edit)
    # Recover: an edit back to the old wording is one more edit.
    api.edit_style_law(db, manuscript, law["id"], statement="Use everyday words.",
                       actor="cli")
    check("the old wording is recoverable by editing back",
          _row(db, law["id"])["statement"] == "Use everyday words."
          and len(api.style_history(db, manuscript, law["id"])) == 3)
    # A law that overrides another keeps displacing it after the
    # displaced law's text changes: ids, not text, carry the link.
    api.edit_style_law(db, manuscript, laws["q"]["id"],
                       statement="One question per essay, asked early.")
    got = [e["id"] for e in styles.effective_style(db, manuscript["id"], "r.md")]
    check("an edited law stays displaced by the law naming its id",
          laws["q"]["id"] not in got and laws["recap2"]["id"] in got, got)
    api.edit_style_law(db, manuscript, laws["img"]["id"], aspect="figure",
                       notes="")
    row = _row(db, laws["img"]["id"])
    check("aspect can change; an empty note clears it",
          row["aspect"] == "figure" and row["notes"] is None, row)
    for bad, kwargs in (("unknown aspect", {"aspect": "nonsense"}),
                        ("empty statement", {"statement": "  "}),
                        ("nothing to change", {})):
        try:
            api.edit_style_law(db, manuscript, law["id"], **kwargs)
            check(f"edit declines {bad}", False)
        except ValueError:
            check(f"edit declines {bad}", True)
    try:
        api.edit_style_law(db, manuscript, "default:dash", statement="x")
        check("a workspace default is not edited here", False)
    except LookupError:
        check("a workspace default is not edited here", True)


def retire_needs_no_new_id_and_unretire_restores(root: Path) -> None:
    ws, db, manuscript, laws = _book(root, "retire")
    law = laws["paper"]
    api.retire_style_law(db, manuscript, law["id"], reason="too broad",
                         actor="web")
    check("retire sets the status", _row(db, law["id"])["status"] == "retired")
    hist = api.style_history(db, manuscript, law["id"])
    check("retire records its reason and actor",
          hist[-1]["event"] == "retire"
          and hist[-1]["detail"]["reason"] == "too broad"
          and hist[-1]["actor"] == "web", hist[-1])
    try:
        api.retire_style_law(db, manuscript, law["id"], reason="again")
        check("a retired law is not retired twice", False)
    except LookupError:
        check("a retired law is not retired twice", True)
    api.unretire_style_law(db, manuscript, law["id"], actor="web")
    check("un-retire makes it active again with the same id",
          _row(db, law["id"])["status"] == "active"
          and api.style_history(db, manuscript, law["id"])[-1]["event"]
          == "unretire")
    try:
        api.unretire_style_law(db, manuscript, law["id"])
        check("an active law is not un-retired", False)
    except LookupError:
        check("an active law is not un-retired", True)
    # The page asks for a reason; the CLI keeps working without one.
    try:
        _call(ws, "retire", id=law["id"], reason=" ")
        check("the page declines a retire without a reason", False)
    except ValueError:
        check("the page declines a retire without a reason", True)
    # A replacement named at retire time is the known replacement.
    new = api.add_style_law(db, manuscript, "citation",
                            "Cite every contestable claim.", guide_name="Papers")
    api.retire_style_law(db, manuscript, law["id"], reason="reworded",
                         replaced_by=new["id"])
    listing = _call(ws, "laws", level="guide:Papers")
    old = next(x for x in listing["laws"] if x["id"] == law["id"])
    check("a retired law shows its known replacement",
          old["replacement"] and old["replacement"]["id"] == new["id"]
          and old["replacement"]["known"], old["replacement"])
    # Retire + re-add from before history existed: a likely replacement.
    older = api.add_style_law(db, manuscript, "lexicon",
                              "Never write towards; write toward.",
                              guide_name="House")
    db.update("style_laws", older["id"], {"status": "retired"})
    readd = api.add_style_law(db, manuscript, "lexicon",
                              "Never write towards; always write toward.",
                              guide_name="House")
    listing = _call(ws, "laws", level="guide:House")
    old = next(x for x in listing["laws"] if x["id"] == older["id"])
    check("a retire + re-add pair is shown as a likely replacement",
          old["replacement"] and old["replacement"]["id"] == readd["id"]
          and not old["replacement"]["known"], old["replacement"])


def move_and_copy_record_where_from(root: Path) -> None:
    ws, db, manuscript, laws = _book(root, "move")
    law = laws["q"]
    api.move_style_law(db, manuscript, law["id"], guide_name="House", actor="web")
    hist = api.style_history(db, manuscript, law["id"])
    check("move keeps the id and records from and to",
          hist[-1]["event"] == "move"
          and hist[-1]["detail"]["from"] == "Part"
          and hist[-1]["detail"]["to"] == "House", hist[-1])
    copy = api.copy_style_law(db, manuscript, laws["plain"]["id"],
                              guide_name="Papers", actor="web")
    row = _row(db, copy["id"])
    check("copy makes a new law at the target with the same words",
          copy["id"] != laws["plain"]["id"]
          and row["statement"] == laws["plain"]["statement"]
          and row["notes"] == laws["plain"]["notes"]
          and row["aspect"] == "lexicon"
          and row["guide_id"] == styles.get_guide(db, manuscript["id"], "Papers")["id"])
    src = api.style_history(db, manuscript, laws["plain"]["id"])[-1]
    dst = api.style_history(db, manuscript, copy["id"])[-1]
    check("copy is in both laws' history",
          src["event"] == "copy" and src["detail"]["copy"] == copy["id"]
          and dst["event"] == "add" and dst["detail"]["copy_of"] == laws["plain"]["id"],
          (src, dst))
    filed = api.copy_style_law(db, manuscript, laws["plain"]["id"], file="loose.md")
    check("a law can be copied to one essay (file-local)",
          _row(db, filed["id"])["file"] == "loose.md")


def similar_laws_warn_but_do_not_block(root: Path) -> None:
    ws, db, manuscript, laws = _book(root, "similar")
    got = api.similar_style_laws(db, manuscript, "Use everyday words, always.",
                                 guide_name="Recaps")
    check("a near-duplicate at an ancestor level is found",
          [g["id"] for g in got][:1] == [laws["plain"]["id"]]
          and got[0]["level"] == "House", got)
    got = api.similar_style_laws(db, manuscript, "Use everyday words, always.",
                                 guide_name="Papers")
    check("... from a sibling branch's ancestor too (House is Papers' parent)",
          [g["id"] for g in got][:1] == [laws["plain"]["id"]])
    got = api.similar_style_laws(db, manuscript, "One question per essay!",
                                 guide_name="Papers")
    check("a law on another branch is not this level's ancestor",
          laws["q"]["id"] not in [g["id"] for g in got], got)
    got = api.similar_style_laws(db, manuscript, "Closed em dash with no spaces.",
                                 file="a.md")
    check("a workspace default counts as an ancestor",
          "default:dash" in [g["id"] for g in got], got)
    out = _call(ws, "add", level="guide:Recaps", aspect="lexicon",
                statement="Use everyday words, always.")
    check("add goes ahead and returns the similar laws as a warning",
          out["law"]["id"] and out["similar"]
          and out["similar"][0]["id"] == laws["plain"]["id"], out)
    out = _call(ws, "add", level="file:a.md", aspect="tone",
                statement="Wry, never arch.")
    check("an essay's own law is file-local",
          _row(db, out["law"]["id"])["file"] == "a.md"
          and out["law"]["id"] in [e["id"] for e in styles.effective_style(
              db, manuscript["id"], "a.md")])
    hist = api.style_history(db, manuscript, out["law"]["id"])
    check("the page's add is recorded as via web", hist[0]["actor"] == "web")


# ------------------------------------------------------------- the page

def levels_lists_the_tree_and_essays(root: Path) -> None:
    ws, db, manuscript, laws = _book(root, "levels")
    out = _call(ws, "levels")
    names = [(g["name"], g["depth"]) for g in out["guides"]]
    check("guides come as a tree, parents before children",
          names == [("House", 0), ("Papers", 1), ("Part", 1), ("Recaps", 2)]
          or names == [("House", 0), ("Part", 1), ("Recaps", 2), ("Papers", 1)],
          names)
    files = {f["file"]: f["guide"] for f in out["files"]}
    check("every essay with the guide it follows (root when unattached)",
          files == {"a.md": "Part", "r.md": "Recaps", "p.md": "Papers",
                    "loose.md": "House"}, files)


def a_guide_shows_laws_defined_there(root: Path) -> None:
    ws, db, manuscript, laws = _book(root, "guide")
    out = _call(ws, "laws", level="guide:Part")
    check("a guide lists only the laws defined at it",
          [x["id"] for x in out["laws"]] == [laws["q"]["id"]], out["laws"])
    law = out["laws"][0]
    # Bound by a.md (Part) only: r.md (Recaps) displaces it.
    check("each law says how many files it binds", law["files"] == 1
          and law["bound"] == ["a.md"], law)
    house = _call(ws, "laws", level="guide:House")
    plain = next(x for x in house["laws"] if x["id"] == laws["plain"]["id"])
    check("a house law binds every file", plain["files"] == 4, plain)
    check("a law carries its note, date, version, level and history",
          plain["notes"].startswith("Owner:") and plain["created_at"]
          and plain["version"] == 1 and plain["level"] == "House"
          and plain["history"][0]["event"] == "add", plain)
    allv = _call(ws, "laws", level="all")
    check("'all' lists every law of the manuscript, each with its level",
          {x["id"] for x in allv["laws"]} == {v["id"] for v in laws.values()}
          and all(x["level"] for x in allv["laws"]))
    defaults = _call(ws, "laws", level="defaults")
    check("the defaults level lists the workspace defaults, read-only",
          [x["id"] for x in defaults["laws"]] == ["default:dash", "default:serial-comma"]
          and all(x["readonly"] for x in defaults["laws"]))


def an_essay_shows_its_effective_law_as_sent(root: Path) -> None:
    ws, db, manuscript, laws = _book(root, "effective")
    out = _call(ws, "laws", level="file:r.md")
    live = [x["id"] for x in out["laws"] if not x["displaced_by"]]
    expected = [e["id"] for e in styles.effective_style(db, manuscript["id"], "r.md")]
    check("an essay's live laws are effective_style, in its order",
          live == expected, (live, expected))
    q = next(x for x in out["laws"] if x["id"] == laws["q"]["id"])
    check("a displaced law is listed, struck, under what displaced it",
          q["displaced_by"] == [laws["recap2"]["id"]]
          and [x["id"] for x in out["laws"]].index(q["id"])
          == [x["id"] for x in out["laws"]].index(laws["recap2"]["id"]) + 1, q)
    dash = next(x for x in out["laws"] if x["id"] == "default:dash")
    check("a displaced default too", dash["displaced_by"] == [laws["dash"]["id"]])
    check("the rendered STYLE LAW block is exactly what filters and lenses get",
          out["rendered"]["style_law"] == styles.render(db, manuscript["id"], "r.md"))
    check("the critic's checklist is exactly what the critic gets",
          out["rendered"]["checklist"]
          == critic.checklist(db, manuscript["id"], "r.md", []))
    img = next(x for x in out["laws"] if x["id"] == laws["img"]["id"])
    check("a non-prose law is marked as left out of the STYLE LAW block",
          img["sent"] == ["checklist"] and
          next(x for x in out["laws"] if x["id"] == laws["plain"]["id"])["sent"]
          == ["style_law", "checklist"])
    t = out["totals"]
    eff = styles.effective_style(db, manuscript["id"], "r.md")
    check("totals count the live laws and their characters",
          t["laws"] == len(eff)
          and t["chars"] == sum(len(e["statement"]) for e in eff), t)
    check("totals by level and by aspect add up",
          sum(v["laws"] for v in t["by_level"].values()) == t["laws"]
          and sum(v["chars"] for v in t["by_aspect"].values()) == t["chars"]
          and t["by_level"]["Recaps"]["laws"] == 2, t["by_level"])
    check("totals give the size of what is sent",
          t["style_law_chars"] == len(out["rendered"]["style_law"])
          and t["checklist_chars"] == len(out["rendered"]["checklist"]))


def reads_write_nothing(root: Path) -> None:
    ws, db, manuscript, laws = _book(root, "reads")
    before = {t: db.one(f"SELECT COUNT(*) AS n, COALESCE(SUM(version),0) AS v FROM {t}")
              for t in ("style_laws", "edit_events", "style_guides",
                        "style_attachments")}
    before = {t: (r["n"], r["v"]) for t, r in before.items()}
    for method, params in (("levels", {}), ("laws", {"level": "all"}),
                           ("laws", {"level": "file:r.md"}),
                           ("history", {"id": laws["plain"]["id"]}),
                           ("similar", {"level": "guide:Part",
                                        "statement": "Use everyday words."})):
        _call(ws, method, **params)
    after = {t: db.one(f"SELECT COUNT(*) AS n, COALESCE(SUM(version),0) AS v FROM {t}")
             for t in before}
    after = {t: (r["n"], r["v"]) for t, r in after.items()}
    check("the page's reads write nothing", before == after, (before, after))
    try:
        _call(ws, "drop_everything")
        check("an unknown method is declined", False)
    except ValueError:
        check("an unknown method is declined", True)


def page_writes_are_the_cli_verbs(root: Path) -> None:
    ws, db, manuscript, laws = _book(root, "writes")
    law = laws["plain"]
    _call(ws, "edit", id=law["id"], statement="Plain words.", notes="n2",
          aspect="register")
    row = _row(db, law["id"])
    check("the page's edit changes statement, note and aspect in place",
          (row["statement"], row["notes"], row["aspect"])
          == ("Plain words.", "n2", "register"))
    _call(ws, "move", id=laws["q"]["id"], to="guide:Papers")
    check("the page's move", _row(db, laws["q"]["id"])["guide_id"]
          == styles.get_guide(db, manuscript["id"], "Papers")["id"])
    out = _call(ws, "copy", id=laws["paper"]["id"], to="guide:House")
    check("the page's copy", _row(db, out["id"])["statement"] == "Cite every claim.")
    _call(ws, "retire", id=laws["paper"]["id"], reason="folded into House")
    _call(ws, "unretire", id=laws["paper"]["id"])
    acts = {h["actor"] for h in api.style_history(db, manuscript, law["id"])[1:]}
    check("every page write is recorded as via web", acts == {"web"}, acts)


def cli_verbs_and_show(root: Path) -> None:
    ws, db, manuscript, laws = _book(root, "cli")
    lid = laws["plain"]["id"]
    out = _cli(ws, "style", "edit", lid[:10], "--statement", "Plain speech.")
    check("style edit prints the law and its version",
          lid[:8] in out and "v2" in out, out)
    shown = _cli(ws, "style", "show", "a.md")
    check("style show prints the edit's provenance and the earlier wording",
          "Plain speech." in shown and "edited" in shown and "via cli" in shown
          and "was: Use everyday words." in shown, shown)
    out = _cli(ws, "style", "history", lid[:10])
    check("style history lists every change with the earlier wording",
          "add" in out and "edit" in out and "Use everyday words." in out, out)
    _cli(ws, "style", "retire", laws["paper"]["id"][:10], "--why", "too broad")
    check("style retire --why records the reason",
          api.style_history(db, manuscript, laws["paper"]["id"])[-1]["detail"]["reason"]
          == "too broad")
    _cli(ws, "style", "unretire", laws["paper"]["id"][:10])
    check("style unretire", _row(db, laws["paper"]["id"])["status"] == "active")
    out = _cli(ws, "style", "copy", lid[:10], "--guide", "Papers")
    check("style copy", "copied" in out)
    _cli(ws, "style", "retire", laws["img"]["id"][:10])
    check("style retire without --why still works (the CLI's old form)",
          _row(db, laws["img"]["id"])["status"] == "retired")


def guide_move_reparents_a_guide_and_nothing_else(root: Path) -> None:
    """`style guide-move`: a guide takes a new parent. Its own laws and
    files stay with it; the new parent's laws now reach its files; the
    new parent's own files, and every other file, are untouched."""
    ws, db, manuscript, laws = _book(root, "guide-move")
    mid = manuscript["id"]

    def ids(f: str) -> list[str]:
        return [e["id"] for e in styles.effective_style(db, mid, f)]

    before = {f: ids(f) for f in ("a.md", "r.md", "loose.md")}
    check("before the move a paper does not get the part's laws",
          laws["q"]["id"] not in ids("p.md"))
    out = api.move_style_guide(db, manuscript, "Papers", "Part")
    check("guide-move says where the guide was and now is",
          out["from"] == "House" and out["to"] == "Part", out)
    after_p = ids("p.md")
    check("the part's laws now reach the guide's files, its own and the house's stay",
          laws["q"]["id"] in after_p and laws["paper"]["id"] in after_p
          and laws["plain"]["id"] in after_p, after_p)
    check("the new parent's files and every other file are unchanged",
          {f: ids(f) for f in before} == before)
    guide = styles.get_guide(db, mid, "Papers")
    hist = styles.law_history(db, mid, guide["id"])
    check("the move is recorded in the guide's history",
          [h["event"] for h in hist] == ["guide-move"]
          and hist[0]["detail"] == {"from": "House", "to": "Part"}, hist)
    for bad, name, parent in (("itself", "Part", "Part"),
                              ("its own descendant", "Part", "Recaps"),
                              ("the root", "House", "Part"),
                              ("an unknown parent", "Papers", "Nowhere"),
                              ("an unknown guide", "Nowhere", "Part"),
                              ("the parent it already has", "Papers", "Part")):
        try:
            api.move_style_guide(db, manuscript, name, parent)
            check(f"guide-move declines {bad}", False)
        except (LookupError, ValueError):
            check(f"guide-move declines {bad}", True)
    out = _cli(ws, "style", "guide-move", "Papers", "--parent", "House")
    check("style guide-move on the command line",
          "Papers" in out and "House" in out
          and laws["q"]["id"] not in ids("p.md"), out)


def guide_rename_keeps_every_link(root: Path) -> None:
    """`style guide-rename`: a guide takes a new name. Guides are keyed by
    id (a child's parent, a file's attachment and a law's owner all name
    the id), so every link survives and every file's law is unchanged;
    the rename is in the guide's history; a name another guide holds,
    an empty name and an unknown guide are declined."""
    ws, db, manuscript, laws = _book(root, "guide-rename")
    mid = manuscript["id"]
    files = ("a.md", "r.md", "p.md", "loose.md")

    def ids(f: str) -> list[str]:
        return [e["id"] for e in styles.effective_style(db, mid, f)]

    before = {f: ids(f) for f in files}
    part = styles.get_guide(db, mid, "Part")
    out = api.rename_style_guide(db, manuscript, "Part", "Essay Laws")
    check("guide-rename says the old and the new name",
          out == {"guide": part["id"], "from": "Part", "to": "Essay Laws"}, out)
    renamed = styles.get_guide(db, mid, "Essay Laws")
    check("the guide keeps its id under the new name",
          renamed is not None and renamed["id"] == part["id"])
    check("the old name no longer finds it", styles.get_guide(db, mid, "Part") is None)
    check("every file's law is unchanged", {f: ids(f) for f in files} == before)
    recaps = styles.get_guide(db, mid, "Recaps")
    check("a child guide still sits under it", recaps["parent"] == part["id"])
    hist = styles.law_history(db, mid, part["id"])
    check("the rename is in the guide's history",
          hist[-1]["event"] == "guide-rename"
          and hist[-1]["detail"] == {"from": "Part", "to": "Essay Laws"}, hist)
    for bad, name, new in (("a name another guide holds", "Essay Laws", "papers"),
                           ("an empty name", "Essay Laws", "  "),
                           ("an unknown guide", "Nowhere", "Somewhere"),
                           ("the name it already has", "Essay Laws", "Essay Laws")):
        try:
            api.rename_style_guide(db, manuscript, name, new)
            check(f"guide-rename declines {bad}", False)
        except (LookupError, ValueError):
            check(f"guide-rename declines {bad}", True)
    out = _cli(ws, "style", "guide-rename", "Essay Laws", "Part")
    check("style guide-rename on the command line",
          "Essay Laws" in out and "Part" in out
          and styles.get_guide(db, mid, "Part")["id"] == part["id"], out)


def edit_sets_what_a_law_displaces(root: Path) -> None:
    """`style edit --overrides`: a law starts (or stops) displacing an
    inherited law, same id, the change in its history; only files under
    the displacing law lose the displaced one."""
    ws, db, manuscript, laws = _book(root, "edit-overrides")
    mid = manuscript["id"]
    api.move_style_guide(db, manuscript, "Papers", "Part")
    paper = laws["paper"]
    out = api.edit_style_law(db, manuscript, paper["id"][:10],
                             overrides=laws["q"]["id"][:10])
    check("edit sets overrides to the full id, same law, version + 1",
          out["id"] == paper["id"] and out["overrides"] == laws["q"]["id"]
          and _row(db, paper["id"])["version"] == paper["version"] + 1, out)
    p = [e["id"] for e in styles.effective_style(db, mid, "p.md")]
    a = [e["id"] for e in styles.effective_style(db, mid, "a.md")]
    check("the displaced law leaves the papers only",
          laws["q"]["id"] not in p and laws["q"]["id"] in a, (p, a))
    edit = api.style_history(db, manuscript, paper["id"])[-1]
    check("history keeps what it displaced before and after",
          edit["detail"]["before"] == {"overrides": None}
          and edit["detail"]["after"] == {"overrides": laws["q"]["id"]}, edit)
    api.edit_style_law(db, manuscript, paper["id"],
                       overrides=f"{laws['q']['id']},default:serial-comma")
    check("a comma list, a default among them",
          _row(db, paper["id"])["overrides"]
          == f"{laws['q']['id']},default:serial-comma")
    api.edit_style_law(db, manuscript, paper["id"], overrides="")
    check("an empty overrides clears it", _row(db, paper["id"])["overrides"] is None
          and laws["q"]["id"] in [e["id"] for e in
                                  styles.effective_style(db, mid, "p.md")])
    for bad, value in (("the law itself", paper["id"]),
                       ("an unknown law", "se-nothere"),
                       ("an unknown default", "default:nothere")):
        try:
            api.edit_style_law(db, manuscript, paper["id"], overrides=value)
            check(f"edit --overrides declines {bad}", False)
        except (LookupError, ValueError):
            check(f"edit --overrides declines {bad}", True)
    out = _cli(ws, "style", "edit", paper["id"][:10], "--overrides",
               laws["q"]["id"][:10])
    check("style edit --overrides on the command line",
          _row(db, paper["id"])["overrides"] == laws["q"]["id"]
          and "displaces" in out, out)


def server_routes_api_style(root: Path) -> None:
    from http.server import ThreadingHTTPServer
    from authorlm import audiobook_server
    ws, db, manuscript, laws = _book(root, "server")
    server = ThreadingHTTPServer(("127.0.0.1", 0),
                                 audiobook_server.make_handler(str(ws), "book"))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/api/style",
            data=json.dumps({"method": "laws", "params": {"level": "file:a.md"}}).encode(),
            headers={"Content-Type": "application/json"})
        body = json.loads(urllib.request.urlopen(req).read())
        check("POST /api/style answers with the essay's laws",
              body["ok"] and body["result"]["totals"]["laws"] >= 4, body)
    finally:
        server.shutdown()
        server.server_close()


def main() -> None:
    root = Path(tempfile.mkdtemp(prefix="style-page-"))
    for fn in (edit_keeps_the_id_and_the_old_wording,
               retire_needs_no_new_id_and_unretire_restores,
               move_and_copy_record_where_from,
               similar_laws_warn_but_do_not_block,
               levels_lists_the_tree_and_essays,
               a_guide_shows_laws_defined_there,
               an_essay_shows_its_effective_law_as_sent,
               reads_write_nothing, page_writes_are_the_cli_verbs,
               cli_verbs_and_show, guide_move_reparents_a_guide_and_nothing_else,
               guide_rename_keeps_every_link,
               edit_sets_what_a_law_displaces, server_routes_api_style):
        print(f"{fn.__name__}:")
        fn(root)
    print(f"OK — {PASSED} style page checks")


if __name__ == "__main__":
    main()
