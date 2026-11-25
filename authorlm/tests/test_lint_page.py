"""The Lint view of the Filters page (`lint_page`, the `lint.*` methods of
`/api/runs`): lint on chosen tabs, bulk mechanical fixes, one-by-one
rulings, direct application through the tab's pull and guarded push, and
the log with Undo.

Source: the owner's ask of 2026-11-25 ("Add the lint view in the Web UI
alongside filters and lenses ... Lint output should be reviewed in the UI
if possible (e.g. accepting spelling corrections) or just fixed (e.g.
quote style) directly in the doc by clicking an 'apply' button. I don't
want to review each quote change or other formatting standardization one
at a time; it has to be bulk. Bulk is not possible for spellings and
things like 'unbalanced quotes' ... if I accept a spell edit and that
section also has a form that overlaps, the UI will have to edit/replace
the form."), and the standing rules it keeps: one producer's forms per
tab; a push never goes while forms are out; a tab changed on both sides
is never pulled over (forms_page's guards).

Hermetic: Google is the `_SurgicalDocFake` from test_passes.py (via
test_forms_page's fixtures, which pin config, .env and the model client
away from the real ones on import); no network, no model.
Run: python3 tests/test_lint_page.py
"""
from __future__ import annotations

import contextlib
import io
import sys
import tempfile
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

from test_forms_page import (ESSAY, _by_note, _call,  # noqa: E402
                             _filter_staged, _google, _h, _no_google,
                             _staged)
from test_passes import _SurgicalDocFake  # noqa: E402

from authorlm import (api, audiobook, gdocs, lint_page,  # noqa: E402
                      runs_page, styles)
from authorlm.normalize import normalize_markdown  # noqa: E402
from authorlm.cli import main as cli_main  # noqa: E402

PASSED = 0


def check(label: str, condition: bool, context: object = "") -> None:
    global PASSED
    assert condition, f"FAIL: {label}\n{context}"
    PASSED += 1
    print(f"  ok: {label}")


A = ("# Alpha\n\n"
     "The colour of the the sky was “blue” and “wide” and 'grey'.\n\n"
     "He said “nothing at all.\n\n"
     "See https://example.com/page?id=4&utm_source=news for more.\n\n"
     "A recieve of colour — an aside — and more.\n")
B = ("# Beta\n\n"
     "It was \"quite\" so, “a” and “b” and “c”.\n\n"
     "Code `\"left\"` stays.\n")


def _plain(root: Path, name: str):
    """A manuscript of two files with no Doc: a.md and b.md."""
    ws = root / name
    ms = ws / "book"
    ms.mkdir(parents=True)
    (ms / "a.md").write_text(A)
    (ms / "b.md").write_text(B)
    with contextlib.redirect_stdout(io.StringIO()):
        cli_main(["--workspace", str(ws), "init", "--name", "book",
                  "--path", str(ms), "--extraction", "off"])
    db = api.open_db(str(ws))
    manuscript = api.get_manuscript(db)
    with contextlib.redirect_stdout(io.StringIO()):
        api.collect(db, manuscript, {})
    audiobook._SESSIONS.pop((str(ws), "book"), None)
    audiobook.session(str(ws), "book", inline=True)
    return ws, db, manuscript, ms


def _linked(root: Path, name: str):
    """solo.md pushed to its tab of a fake Doc, no forms out."""
    ws = root / name
    ms = ws / "book"
    ms.mkdir(parents=True)
    (ms / "solo.md").write_text(ESSAY)
    with contextlib.redirect_stdout(io.StringIO()):
        cli_main(["--workspace", str(ws), "init", "--name", "book",
                  "--path", str(ms), "--extraction", "off"])
    db = api.open_db(str(ws))
    manuscript = api.get_manuscript(db)
    with contextlib.redirect_stdout(io.StringIO()):
        api.collect(db, manuscript, {})
    fake = _SurgicalDocFake([("book", ""), ("solo.md", ESSAY)], render=True)
    meta = gdocs._mapping(db, manuscript)
    meta.setdefault("gdocs", {}).update({
        "_master_id": "doc-fake", "_container_tab": "tab-1",
        "solo.md": {"tab_id": "tab-2", "checked_out": True,
                    "pushed_hash": _h(normalize_markdown(ESSAY))}})
    gdocs._save_mapping(db, manuscript, meta)
    audiobook._SESSIONS.pop((str(ws), "book"), None)
    audiobook.session(str(ws), "book", inline=True)
    return ws, ms, fake


def _lint(ws: Path, method: str, fake=None, settle: bool = True,
          **params) -> dict:
    """One `lint.*` call. An apply, undo or retry returns once the file is
    written; with `settle` the background road to the Doc is waited out
    too (inside the same Google stand-in), and its outcome per fix is
    added as `doc` ({fix id: {state, reason}}) with each tab's `state`."""
    ctx = _google(fake) if fake is not None else _no_google()
    with ctx, contextlib.redirect_stdout(io.StringIO()):
        out = runs_page.dispatch(f"lint.{method}",
                                 {"manuscript": "book", **params},
                                 workspace=str(ws))
        if settle and method in ("apply", "undo", "retry"):
            assert lint_page.settle(), "the road to the Doc did not settle"
            status = runs_page.dispatch("lint.status", {"manuscript": "book"},
                                        workspace=str(ws))
            out["doc"] = {i: f for t in status["tabs"].values()
                          for i, f in t["fixes"].items()}
            out["states"] = {f: t["state"] for f, t in status["tabs"].items()}
            out["status"] = status
    return out


def _why(out: dict, fix_id: str) -> str:
    """Why a fix did not land: said at the click, or by the road after."""
    for t in out.get("tabs", []):
        for b in t["blocked"]:
            if b["id"] == fix_id:
                return b["reason"]
    got = out.get("doc", {}).get(fix_id, {})
    return got.get("reason", "") if got.get("state") in (
        "withdrawn", "held") else ""


def _forms(db, manuscript) -> list[dict]:
    return [dict(r) for r in db.all(
        "SELECT * FROM doc_threads WHERE manuscript_id = ? AND state = "
        "'written' ORDER BY created_at, id", (manuscript["id"],))]


# ------------------------------------------------------------ the fixes

def the_fixes_are_mechanical(_root: Path) -> None:
    print("fixes:")
    text = ('He said "yes" and \'no\', it\'s the \'90s.\n\n'
            'Code `"x"` and [a](http://e.com/?q="1") stay.\n')
    got = lint_page.apply_edits(text, lint_page.curl_edits(text, "curly"))
    check("straight marks become curly, opening and closing by place",
          got.startswith("He said “yes” and ‘no’, it’s the ’90s."), got)
    check("code and link targets are left alone",
          '`"x"`' in got and '?q="1")' in got, got)
    em = '*"The idea of death."* and _\'so\'_ end.\n'
    got_em = lint_page.apply_edits(em, lint_page.curl_edits(em, "curly"))
    check("a quote inside emphasis opens and closes by the words, not the "
          "asterisk (becker.md line 1, sandbox check 2026-11-25)",
          got_em == "*“The idea of death.”* and _‘so’_ end.\n", got_em)
    back = lint_page.apply_edits(got, lint_page.curl_edits(got, "straight"))
    check("and back to straight when the law says straight",
          back.startswith('He said "yes" and \'no\', it\'s the \'90s.'), back)
    link = "See https://e.com/p?id=4&utm_source=n&fbclid=x#top. And more."
    got = lint_page.apply_edits(link, lint_page.untrack_edits(link))
    check("tracking tags leave the link; its other query and fragment stay",
          got == "See https://e.com/p?id=4#top. And more.", got)
    only = "https://e.com/p?utm_medium=a, done"
    check("a link with nothing but tags loses its question mark",
          lint_page.apply_edits(only, lint_page.untrack_edits(only))
          == "https://e.com/p, done")
    words = "Colour, colour's COLOUR, and *colour* in italics, colourful."
    got = lint_page.apply_edits(words, lint_page.word_edits(
        words, "colour", "color", fold=True))
    check("a variant word changes in every case, never inside italics or "
          "a longer word",
          got == "Color, color's COLOR, and *colour* in italics, colourful.",
          got)
    para = "One.\n\nTwo two.\n\nThree.\n\nTwo two.\n"
    edits = lint_page.paragraph_edits(para, "Two two.", "Two.", line=7)
    check("an inline edit takes the paragraph nearest the finding's line",
          edits == [(len("One.\n\nTwo two.\n\nThree.\n\n"),
                     len(para) - 1, "Two.")], edits)
    try:
        lint_page.paragraph_edits(para, "Three.", "A.\n\nB.")
        split = False
    except ValueError:
        split = True
    check("an inline edit cannot split a paragraph in two", split)


# ------------------------------------------------------------------ run

def the_actions_follow_the_rule(root: Path) -> None:
    print("run:")
    ws, db, manuscript, ms = _plain(root, "run-ws")
    before = {p: (ms / p).read_text() for p in ("a.md", "b.md")}
    got = _lint(ws, "run", files=["a.md", "b.md"],
                rules=["spelling", "doubled-word", "spelling-variant",
                       "quote-style", "link-tracking", "em-dash"])
    order = [g["rule"] for g in got["groups"]]
    check("the counts come per rule, rare one-by-one rules ahead of the "
          "bulk ones, em-dash last",
          order == ["spelling", "doubled-word", "unbalanced-quotes",
                    "spelling-variant", "quote-style", "link-tracking",
                    "em-dash"], order)
    acts = {g["rule"]: g["act"] for g in got["groups"]}
    check("spelling, doubled words and unbalanced quote marks are ruled one "
          "by one; variant, quote style and tracking tags are bulk",
          acts == {"spelling": "one", "doubled-word": "one",
                   "unbalanced-quotes": "one", "spelling-variant": "bulk",
                   "quote-style": "bulk", "link-tracking": "bulk",
                   "em-dash": "count"}, acts)
    groups = {g["rule"]: g for g in got["groups"]}
    check("em-dash warnings are a count with the law and no findings",
          groups["em-dash"]["count"] == 1 and groups["em-dash"]["findings"]
          == [] and "expendable" in groups["em-dash"]["note"],
          groups["em-dash"])
    spell = groups["spelling"]["findings"][0]
    check("a spelling finding carries its word, suggestions and paragraph",
          spell["word"] == "recieve" and "receive" in spell["suggestions"]
          and spell["paragraph"].startswith("A recieve"), spell)
    variant = groups["spelling-variant"]["findings"][0]
    check("a variant finding names the word, its American spelling and "
          "its uses in the file",
          variant["word"] == "colour" and variant["to"] == "color"
          and variant["uses"] == 2, variant)
    doubled = groups["doubled-word"]["findings"][0]
    check("a doubled word comes with the paragraph minus one of them",
          "of the sky" in doubled["suggested"]
          and "the the" not in doubled["suggested"], doubled)
    check("quote style is moved to the style each file uses more",
          {f["file"]: f["style"] for f in groups["quote-style"]["findings"]}
          == {"a.md": "curly", "b.md": "curly"})
    check("lint writes nothing",
          before == {p: (ms / p).read_text() for p in ("a.md", "b.md")})

    styles.add_element(db, manuscript["id"], "formatting",
                       "Straight quotation marks throughout.", file="b.md")
    law = _lint(ws, "run", files=["b.md"], rules=["quote-style"])
    check("a style law on quote marks sets the bulk fix's direction and is "
          "quoted for the page",
          law["groups"][0]["findings"][0]["style"] == "straight"
          and law["quote_laws"]["b.md"]["statement"]
          == "Straight quotation marks throughout.", law)
    pre = _lint(ws, "preview", fixes=[{"kind": "curl", "file": "b.md"}])
    check("so the preview straightens the curly marks of b.md",
          pre["count"] == 6 and {"op": "insert", "text":
                                 '"a" and "b" and "c"'}
          in pre["pairs"][0]["segments"], pre)
    for row in db.all("SELECT id FROM style_laws WHERE file = 'b.md'"):
        db.update("style_laws", row["id"], {"status": "retired"})

    pre = _lint(ws, "preview", fixes=[{"kind": "curl", "file": "a.md"},
                                      {"kind": "curl", "file": "b.md"}])
    check("a bulk preview counts every change and shows before and after",
          pre["count"] == 4 and pre["files"] == {"a.md": 2, "b.md": 2}
          and len(pre["pairs"]) == 2
          and [x["text"] for x in pre["pairs"][0]["segments"]
               if x["op"] == "insert"] == ["‘", "’"], pre)
    check("the preview writes nothing",
          before == {p: (ms / p).read_text() for p in ("a.md", "b.md")})

    out = _lint(ws, "ignore", id=doubled["id"], rule="doubled-word",
                file="a.md", quote=doubled["quote"])
    again = _lint(ws, "run", files=["a.md"], rules=["doubled-word"])
    check("a finding marked legitimate stays out of the next run, counted",
          out["added"] and again["groups"] == [] and again["ignored"]
          == {"doubled-word": 1}
          and (ms / "_lint" / "ignored-findings.tsv").is_file(), again)
    _lint(ws, "accept", words=["recieve"])
    again = _lint(ws, "run", files=["a.md"], rules=["spelling"])
    check("a word accepted for the book is no longer reported",
          again["groups"] == [] and "recieve" in (
              ms / "_lint" / "accepted-words.txt").read_text(), again)


# --------------------------------------------------------------- apply

def apply_goes_into_the_text_not_a_form(root: Path) -> None:
    print("apply:")
    ws, db, manuscript, ms = _plain(root, "apply-ws")
    out = _lint(ws, "apply", fixes=[
        {"id": "q-a", "kind": "curl", "file": "a.md", "rule": "quote-style"},
        {"id": "q-b", "kind": "curl", "file": "b.md", "rule": "quote-style"},
        {"id": "w-a", "kind": "word", "file": "a.md", "word": "colour",
         "to": "color", "rule": "spelling-variant"},
        {"id": "s-a", "kind": "word", "file": "a.md", "word": "recieve",
         "to": "receive", "rule": "spelling"}])
    a, b = (ms / "a.md").read_text(), (ms / "b.md").read_text()
    check("two tabs' quote marks curled in one Apply, with the word fixes",
          out["applied"] == 4 and out["blocked"] == 0
          and "‘grey’" in a and "“quite”" in b and "color of" in a
          and "A receive of color" in a, out)
    check("code is left alone", '`"left"`' in b, b)
    check("no form is staged for a lint fix", _forms(db, manuscript) == [])
    logged = _lint(ws, "log")["batches"]
    check("every changed paragraph is logged with its before and after",
          len(logged) == 1 and len(logged[0]["changes"]) == 3
          and any("'grey'" in c["before_text"] and "‘grey’" in c["after_text"]
                  for c in logged[0]["changes"]), logged)
    para = "He said “nothing at all.”"
    out = _lint(ws, "apply", fixes=[
        {"id": "u", "kind": "paragraph", "file": "a.md", "line": 5,
         "old": "He said “nothing at all.", "new": para,
         "rule": "unbalanced-quotes"}])
    check("an inline edit of an unbalanced quote lands",
          out["applied"] == 1 and para in (ms / "a.md").read_text(), out)
    stale = _lint(ws, "apply", fixes=[
        {"id": "u", "kind": "paragraph", "file": "a.md", "line": 5,
         "old": "He said “nothing at all.\n", "new": "x"}])
    check("a fix whose words are gone is said and not applied",
          stale["applied"] == 0 and "run lint again" in
          stale["tabs"][0]["blocked"][0]["reason"], stale)
    first = logged[0]["batch"]
    undone = _lint(ws, "undo", ids=[first])
    a2, b2 = (ms / "a.md").read_text(), (ms / "b.md").read_text()
    check("Undo puts a batch's paragraphs back, in both tabs",
          undone["applied"] == 3 and "'grey'" in a2 and '"quite"' in b2
          and "colour" in a2 and para in a2, (undone, a2))
    rows = db.all("SELECT undone_at FROM lint_changes WHERE batch_id = ?",
                  (first,))
    check("the undone rows are marked, the undo logged as its own batch",
          all(r["undone_at"] for r in rows)
          and len(_lint(ws, "log")["batches"]) == 3)


def a_guard_applies_nothing_for_the_tab(root: Path) -> None:
    print("guards:")
    ws, db, manuscript, ms, fake = _staged(root, "guard-ws")
    before = (ms / "solo.md").read_text()
    sess = audiobook.session(str(ws), "book", inline=True)
    with mock.patch.object(type(sess), "_doc_services",
                           side_effect=RuntimeError("no token")):
        out = _lint(ws, "apply", fixes=[
            {"id": "w", "kind": "word", "file": "solo.md", "word": "Delta",
             "to": "Deltas", "rule": "spelling"}])
    check("no Google sign-in: a tab in the Doc gets nothing, and says why",
          out["applied"] == 0 and "no Google sign-in" in
          out["tabs"][0]["blocked"][0]["reason"]
          and (ms / "solo.md").read_text() == before, out)
    ws2, ms2, fake2 = _linked(root, "emptied-ws")
    full = (ms2 / "solo.md").read_text()
    for para in [p for p in full.split("\n\n") if p.strip()][1:]:
        fake2.edit("tab-2", para.strip(), "")
    out = _lint(ws2, "apply", fake2, fixes=[
        {"id": "w", "kind": "word", "file": "solo.md", "word": "Alpha",
         "to": "Alfa", "rule": "spelling"}])
    check("a tab that lost most of the file's words is never pulled over "
          "the file (sandbox check, 2026-11-25): the fix is withdrawn in "
          "the background and the file is as it was",
          out["doc"]["w"]["state"] == "withdrawn"
          and "far less than the file" in _why(out, "w")
          and (ms2 / "solo.md").read_text() == full, out)
    (ms / "solo.md").write_text(before.replace("Delta closes",
                                               "Delta, locally, closes"))
    fake.edit("tab-2", "Delta closes", "Delta, in the Doc, closes")
    out = _lint(ws, "apply", fake, fixes=[
        {"id": "w", "kind": "word", "file": "solo.md", "word": "Alpha",
         "to": "Alfa", "rule": "spelling"}])
    check("changed in the file and in the tab: nothing applied, the "
          "reason shown",
          out["doc"]["w"]["state"] == "withdrawn"
          and "both changed" in _why(out, "w")
          and "Alfa" not in (ms / "solo.md").read_text(), out)


def a_fix_inside_a_pending_form_is_restaged(root: Path) -> None:
    print("pending forms:")
    essay = ("# Topic\n\n"
             "Alpha stays as it is.\n\n"
             "Beta is to be replacd.\n\n"
             "Gamma is to be reworded.\n\n"
             "Delta closes the essay.\n")
    revisions = [
        {"old": "Alpha stays", "new": "Alpha moves", "note": "r-alpha"},
        {"old": "Beta is to be replacd.", "new": "Beta, replacd now.",
         "note": "r-beta"},
        {"old": "", "new": "Epsilon is added after Delta.", "note": "r-add",
         "anchor_paragraph": 5}]
    ws, db, manuscript, ms, fake = _staged(root, "form-ws", essay, revisions)
    out = _lint(ws, "apply", fake, fixes=[
        {"id": "s", "kind": "word", "file": "solo.md", "word": "replacd",
         "to": "replaced", "rule": "spelling"}])
    text = (ms / "solo.md").read_text()
    forms = {f["note"]: f for f in _forms(db, manuscript)}
    check("a spelling fix inside a pending form lands in the file, through "
          "the background road (accepted at the click, written there)",
          out["accepted"] == 1 and out["doc"]["s"]["state"] == "in-doc"
          and "Beta is to be replaced." in text, out)
    check("the form is restaged with the fix in its old and new words, "
          "under its own origin and note",
          forms["r-beta"]["proposed_old"] == "Beta is to be replaced."
          and forms["r-beta"]["proposed_new"] == "Beta, replaced now."
          and "harvest" in forms["r-beta"]["origin_id"], forms)
    check("the tab's other forms are restaged as they were",
          set(forms) == {"r-alpha", "r-beta", "r-add"}
          and forms["r-alpha"]["proposed_new"] == "Alpha moves"
          and forms["r-add"]["proposed_new"]
          == "Epsilon is added after Delta.", forms)
    tab = fake.tab_text("solo.md")
    check("the tab holds the restaged forms over the fixed text",
          "replacd" not in tab and "Beta, replaced now." in tab, tab)
    check("the log keeps the forms as restaged",
          "Beta, replaced now." in db.one(
              "SELECT restaged FROM lint_changes")["restaged"])

    before = (ms / "solo.md").read_text()
    real_push, calls = lint_page.gdocs.push_doc, []

    def push_once_stopped(*a, **k):
        calls.append(1)
        if len(calls) == 2:   # 1: the withdrawal's merge; 2: the fix's own
            raise LookupError("the Doc changed")
        return real_push(*a, **k)

    with mock.patch.object(lint_page.gdocs, "push_doc", push_once_stopped):
        out = _lint(ws, "apply", fake, fixes=[
            {"id": "q", "kind": "word", "file": "solo.md", "word": "Beta",
             "to": "Betta", "rule": "spelling"}])
    forms = {f["note"]: f for f in _forms(db, manuscript)}
    check("a push the guard stops puts the file back and restages the "
          "withdrawn forms as they were",
          out["doc"]["q"]["state"] == "withdrawn"
          and "the file was put back" in _why(out, "q")
          and (ms / "solo.md").read_text() == before
          and set(forms) == {"r-alpha", "r-beta", "r-add"}
          and forms["r-beta"]["proposed_old"] == "Beta is to be replaced.",
          (out, forms))

    listing = _call(ws, fake, "list")
    alpha = _by_note(listing)["r-alpha"]
    _call(ws, fake, "decide", id=alpha["id"], verdict="accept",
          seen=alpha["current"])
    out = _lint(ws, "apply", fake, fixes=[
        {"id": "a", "kind": "word", "file": "solo.md", "word": "Gamma",
         "to": "Gama", "rule": "spelling"}])
    check("a form you have ruled on is not withdrawn: the fix is blocked by "
          "it, with its origin",
          out["doc"]["a"]["state"] == "withdrawn"
          and "blocked by a pending form from harvest" in _why(out, "a")
          and "Gama" not in (ms / "solo.md").read_text(), out)

    ws, db, manuscript, ms, fake = _filter_staged(root, "filter-form-ws")
    before = (ms / "solo.md").read_text()
    # 'wall' sits only in the two paragraphs the filter left alone.
    out = _lint(ws, "apply", fake, fixes=[
        {"id": "w", "kind": "word", "file": "solo.md", "word": "wall",
         "to": "walls", "rule": "spelling"}])
    check("a filter's forms are never withdrawn by a lint fix, even for a "
          "fix outside them: blocked by a pending form, naming it",
          out["doc"]["w"]["state"] == "withdrawn"
          and "blocked by a pending form" in _why(out, "w")
          and (ms / "solo.md").read_text() == before, out)



# ------------------------------------------- the Doc, in the background
#
# Source: the owner's report of 2026-11-25 ("when I apply a change in
# lint, it takes quite a lot of time although it should be a local file
# change. Also, other changes cannot be applied on other paragraphs or
# other tabs until one change is accepted."). Timing-independent: the
# Doc's push is held on an Event, so "returns before the push" is a fact
# of order, not of speed.

TWO = "# Two\n\nOmega opens here.\n\nPsi closes the second tab.\n"


def _linked_two(root: Path, name: str):
    """solo.md (ESSAY) and two.md (TWO), each pushed to its tab of one
    fake Doc, no forms out."""
    ws = root / name
    ms = ws / "book"
    ms.mkdir(parents=True)
    (ms / "solo.md").write_text(ESSAY)
    (ms / "two.md").write_text(TWO)
    with contextlib.redirect_stdout(io.StringIO()):
        cli_main(["--workspace", str(ws), "init", "--name", "book",
                  "--path", str(ms), "--extraction", "off"])
    db = api.open_db(str(ws))
    manuscript = api.get_manuscript(db)
    with contextlib.redirect_stdout(io.StringIO()):
        api.collect(db, manuscript, {})
    fake = _SurgicalDocFake([("book", ""), ("solo.md", ESSAY),
                             ("two.md", TWO)], render=True)
    meta = gdocs._mapping(db, manuscript)
    meta.setdefault("gdocs", {}).update({
        "_master_id": "doc-fake", "_container_tab": "tab-1",
        "solo.md": {"tab_id": "tab-2", "checked_out": True,
                    "pushed_hash": _h(normalize_markdown(ESSAY))},
        "two.md": {"tab_id": "tab-3", "checked_out": True,
                   "pushed_hash": _h(normalize_markdown(TWO))}})
    gdocs._save_mapping(db, manuscript, meta)
    audiobook._SESSIONS.pop((str(ws), "book"), None)
    audiobook.session(str(ws), "book", inline=True)
    return ws, db, manuscript, ms, fake


class _HeldPush:
    """gdocs.push_doc, held until `release` (each call records its file
    and waits); the real push then runs."""

    def __init__(self):
        import threading
        self.gate = threading.Event()
        self.started = threading.Event()
        self.files: list[str] = []
        self.real = lint_page.gdocs.push_doc

    def __call__(self, db, manuscript, rel, *a, **k):
        self.files.append(rel)
        self.started.set()
        assert self.gate.wait(30), "the held push was never released"
        return self.real(db, manuscript, rel, *a, **k)


def _word(fid: str, file: str, word: str, to: str) -> dict:
    return {"id": fid, "kind": "word", "file": file, "word": word, "to": to,
            "rule": "spelling"}


def apply_returns_before_the_doc(root: Path) -> None:
    print("background road:")
    ws, db, manuscript, ms, fake = _linked_two(root, "bg-ws")
    held = _HeldPush()
    with _google(fake), mock.patch.object(lint_page.gdocs, "push_doc", held):
        out = _lint(ws, "apply", fake, settle=False,
                    fixes=[_word("a", "solo.md", "Alpha", "Alfa")])
        check("Apply returns with the fix in the file before the Doc's "
              "push has finished",
              out["applied"] == 1 and out["tabs"][0]["doc"] == "queued"
              and "Alfa stays" in (ms / "solo.md").read_text()
              and "Alfa" not in fake.tab_text("solo.md"), out)
        check("and the change is logged at once, waiting for the Doc",
              [r["doc"] for b in _lint(ws, "log", fake)["batches"]
               for r in b["changes"]] == ["queued"],
              _lint(ws, "log", fake)["batches"])
        assert held.started.wait(10), "the sync never began"
        status = _lint(ws, "status", fake)
        check("the tab says it is syncing while its push runs",
              status["tabs"]["solo.md"]["state"] == "syncing", status)
        other = _lint(ws, "apply", fake, settle=False,
                      fixes=[_word("o", "two.md", "Omega", "Omegas")])
        check("a fix on another tab is applied to its file while the first "
              "tab syncs",
              other["applied"] == 1
              and "Omegas opens" in (ms / "two.md").read_text(), other)
        held.gate.set()
        assert lint_page.settle()
        status = _lint(ws, "status", fake)
    check("both tabs reach the Doc, one after the other",
          "Alfa stays" in fake.tab_text("solo.md")
          and "Omegas opens" in fake.tab_text("two.md")
          and held.files == ["solo.md", "two.md"]
          and {f: t["state"] for f, t in status["tabs"].items()}
          == {"solo.md": "in-doc", "two.md": "in-doc"}, (held.files, status))
    rows = db.all("SELECT pushed, metadata FROM lint_changes")
    check("their rows say pushed to the tab",
          len(rows) == 2 and all(r["pushed"] == 1 for r in rows), rows)
    check("and the pushed stamps of both tabs are kept",
          gdocs.unchanged_since_push(db, manuscript, "solo.md")
          and gdocs.unchanged_since_push(db, manuscript, "two.md"))


def same_tab_fixes_are_serialized(root: Path) -> None:
    print("one tab, in order:")
    ws, db, _manuscript, ms, fake = _linked_two(root, "serial-ws")
    held = _HeldPush()
    with _google(fake), mock.patch.object(lint_page.gdocs, "push_doc", held):
        _lint(ws, "apply", fake, settle=False,
              fixes=[_word("a", "solo.md", "Alpha", "Alfa")])
        assert held.started.wait(10), "the sync never began"
        second = _lint(ws, "apply", fake, settle=False,
                       fixes=[_word("d", "solo.md", "Delta", "Deltas")])
        check("a second fix on a tab that is syncing is accepted at once, "
              "not written under the running push",
              second["accepted"] == 1 and second["tabs"][0]["doc"]
              == "deferred" and "Deltas" not in (ms / "solo.md").read_text(),
              second)
        held.gate.set()
        assert lint_page.settle()
    text, tab = (ms / "solo.md").read_text(), fake.tab_text("solo.md")
    check("both land, in the file and in the tab, in two pushes",
          "Alfa stays" in text and "Deltas closes" in text
          and "Alfa stays" in tab and "Deltas closes" in tab
          and held.files == ["solo.md", "solo.md"], (text, tab, held.files))
    check("nothing waits and every row is pushed",
          all(r["pushed"] == 1 for r in db.all(
              "SELECT pushed FROM lint_changes")))

    # Fixes accepted on one tab within the window: one push.
    lint_page.SYNC_WINDOW = 60.0
    calls = []
    real = lint_page.gdocs.push_doc

    def counted(*a, **k):
        calls.append(a[2])
        return real(*a, **k)

    try:
        with _google(fake), mock.patch.object(lint_page.gdocs, "push_doc",
                                              counted):
            for fid, word, to in (("g", "Gamma", "Gama"),
                                  ("b", "Beta", "Betta")):
                _lint(ws, "apply", fake, settle=False,
                      fixes=[_word(fid, "solo.md", word, to)])
            check("within the window both fixes are in the file and the "
                  "tab is queued",
                  "Gama is" in (ms / "solo.md").read_text()
                  and "Betta is" in (ms / "solo.md").read_text()
                  and _lint(ws, "status", fake)["tabs"]["solo.md"]["state"]
                  == "queued")
            lint_page.road(audiobook.session(str(ws), "book")).flush()
            assert lint_page.settle()
    finally:
        lint_page.SYNC_WINDOW = 0.0
    check("and they go to the Doc in one push",
          calls == ["solo.md"] and "Gama is" in fake.tab_text("solo.md")
          and "Betta is" in fake.tab_text("solo.md"), calls)


def a_stopped_sync_is_shown_and_kept(root: Path) -> None:
    print("a sync that cannot finish:")
    ws, db, _manuscript, ms, fake = _linked_two(root, "stop-ws")

    def unreachable(*_a, **_k):
        raise RuntimeError("Google answered 503")

    with mock.patch.object(lint_page.gdocs, "doc_three_way", unreachable):
        out = _lint(ws, "apply", fake,
                    fixes=[_word("a", "solo.md", "Alpha", "Alfa")])
    check("the Doc out of reach: the change stays in the file and the tab "
          "says not in the Doc, with the reason",
          out["states"]["solo.md"] == "held"
          and "503" in out["status"]["tabs"]["solo.md"]["reason"]
          and "Alfa stays" in (ms / "solo.md").read_text()
          and "Alfa" not in fake.tab_text("solo.md"), out)
    check("the log row says it is held, not pushed",
          [(r["doc"], r["pushed"]) for b in _lint(ws, "log", fake)["batches"]
           for r in b["changes"]] == [("held", 0)])
    again = _lint(ws, "retry", fake, file="solo.md")
    check("Push again sends it",
          again["states"]["solo.md"] == "in-doc"
          and "Alfa stays" in fake.tab_text("solo.md")
          and db.one("SELECT pushed FROM lint_changes")["pushed"] == 1,
          again)

    # The owner typed in the tab since the last push: the fix is not
    # pushed over it; the tab's words come down first, then the fix.
    fake.edit("tab-3", "Psi closes", "Psi, typed in the Doc, closes")
    out = _lint(ws, "apply", fake,
                fixes=[_word("o", "two.md", "Omega", "Omegas")])
    text, tab = (ms / "two.md").read_text(), fake.tab_text("two.md")
    check("newer words in the Doc are pulled before the fix goes up: both "
          "are in the file and in the tab",
          out["doc"]["o"]["state"] == "in-doc"
          and "Omegas opens" in text and "typed in the Doc" in text
          and "Omegas opens" in tab and "typed in the Doc" in tab,
          (out, text, tab))
    logged = [r for b in _lint(ws, "log", fake)["batches"] for r in b["changes"]
              if r["file"] == "two.md"]
    check("its log row is the paragraph as it stands after the pull",
          len(logged) == 1 and logged[0]["after_text"] == "Omegas opens "
          "here." and logged[0]["doc"] == "in-doc", logged)

    # Undo goes the same road.
    batch = next(b["batch"] for b in _lint(ws, "log", fake)["batches"]
                 if b["changes"][0]["file"] == "two.md")
    undone = _lint(ws, "undo", fake, ids=[batch])
    check("Undo writes the file at once and the tab follows",
          undone["applied"] == 1
          and "Omega opens" in (ms / "two.md").read_text()
          and "Omega opens" in fake.tab_text("two.md")
          and undone["states"]["two.md"] == "in-doc", undone)

    # Undo of a fix whose sync is still queued: both reach the Doc as one.
    lint_page.SYNC_WINDOW = 60.0
    try:
        with _google(fake):
            _lint(ws, "apply", fake, settle=False,
                  fixes=[_word("g", "solo.md", "Gamma", "Gama")])
            b = next(b["batch"] for b in _lint(ws, "log", fake)["batches"]
                     if "Gama" in b["changes"][0]["after_text"])
            _lint(ws, "undo", fake, settle=False, ids=[b])
            lint_page.road(audiobook.session(str(ws), "book")).flush()
            assert lint_page.settle()
    finally:
        lint_page.SYNC_WINDOW = 0.0
    check("an Undo before the sync: the file and the tab end as they began",
          "Gamma is" in (ms / "solo.md").read_text()
          and "Gama" not in fake.tab_text("solo.md"))

    # A file marked the owner's alone (the owner's ruling of 2026-11-25 on
    # the Sermons: "I don't want to change the sermons at all at this
    # stage. All further edits there will be by me manually."): the
    # click and the road both make the one check.
    before = (ms / "two.md").read_text()
    why = "two.md is the owner's alone"
    with mock.patch.object(lint_page.structure, "owner_edits_reason",
                           lambda _toc, f: why if f.endswith("two.md")
                           else None, create=True), \
            mock.patch.object(lint_page.structure, "TOC_FILENAME",
                              "two.md", create=True):
        out = _lint(ws, "apply", fake,
                    fixes=[_word("p", "two.md", "Psi", "Psy")])
    check("a file only the owner edits gets no fix: nothing written",
          out["applied"] == 0 and _why(out, "p") == why
          and (ms / "two.md").read_text() == before, out)
    lint_page.SYNC_WINDOW = 60.0
    try:
        with _google(fake):
            _lint(ws, "apply", fake, settle=False,
                  fixes=[_word("p", "two.md", "Psi", "Psy")])
            with mock.patch.object(lint_page.structure,
                                   "owner_edits_reason",
                                   lambda _toc, f: why, create=True), \
                    mock.patch.object(lint_page.structure, "TOC_FILENAME",
                                      "two.md", create=True):
                lint_page.road(audiobook.session(str(ws), "book")).flush()
                assert lint_page.settle()
                status = _lint(ws, "status", fake)
    finally:
        lint_page.SYNC_WINDOW = 0.0
    check("marked after the click: the road puts the file back and sends "
          "nothing",
          (ms / "two.md").read_text() == before
          and "Psy" not in fake.tab_text("two.md")
          and status["tabs"]["two.md"]["state"] == "withdrawn"
          and status["tabs"]["two.md"]["reason"] == why, status)


def main() -> None:
    root = Path(tempfile.mkdtemp(prefix="lint-page-"))
    lint_page.SYNC_WINDOW = 0.0   # no window to wait out; batching proved
    for fn in (the_fixes_are_mechanical, the_actions_follow_the_rule,
               apply_goes_into_the_text_not_a_form,
               a_guard_applies_nothing_for_the_tab,
               a_fix_inside_a_pending_form_is_restaged,
               apply_returns_before_the_doc, same_tab_fixes_are_serialized,
               a_stopped_sync_is_shown_and_kept):
        fn(root)
    print(f"OK — {PASSED} Lint view checks")


if __name__ == "__main__":
    main()
