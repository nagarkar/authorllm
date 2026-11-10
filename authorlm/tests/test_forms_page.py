"""The Merge forms page (`forms_page.dispatch`, `/api/forms`): every open
outside revision listed as the Doc holds it, ruled on the page, and merged
one file at a time.

Source: the owner's ask of 2026-11-10 (one place to rule on and merge every
open external form), and that day's failures, each pinned here:
  - a file changed both locally and in its tab: no forced pull (it drops
    local edits), no push over it; the page shows the `doc diff` sides;
  - staging pulled a stale tab over the local file and reverted another
    session's accepted edits: an older text in the tab is never pulled;
  - `![](_illustrations/...)` lines must never leave the local file.

Hermetic: Google is the `_SurgicalDocFake` from test_passes.py (which pins
config, .env and the model client away from the real ones on import); no
network, no model.
Run: python3 tests/test_forms_page.py
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

from test_passes import _SurgicalDocFake  # noqa: E402

from authorlm import api, audiobook, forms_page, gdocs  # noqa: E402
from authorlm.cli import main as cli_main  # noqa: E402
from authorlm.normalize import normalize_markdown  # noqa: E402
from authorlm.revisions import strip_embed_lines  # noqa: E402

PASSED = 0


def check(label: str, condition: bool, context: object = "") -> None:
    global PASSED
    assert condition, f"FAIL: {label}\n{context}"
    PASSED += 1
    print(f"  ok: {label}")


def _h(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


@contextlib.contextmanager
def _google(fake):
    saved = (gdocs.get_service, gdocs.get_docs_service)
    gdocs.get_service = lambda *a, **k: fake
    gdocs.get_docs_service = lambda *a, **k: fake
    try:
        yield
    finally:
        gdocs.get_service, gdocs.get_docs_service = saved


@contextlib.contextmanager
def _no_google():
    """Any Google call fails the test."""
    def boom(*_a, **_k):
        raise AssertionError("a Google service was asked for")
    saved = (gdocs.get_service, gdocs.get_docs_service)
    gdocs.get_service = gdocs.get_docs_service = boom
    try:
        yield
    finally:
        gdocs.get_service, gdocs.get_docs_service = saved


ESSAY = ("# Topic\n\n"
         "Alpha stays as it is.\n\n"
         "Beta is to be replaced.\n\n"
         "Gamma is to be reworded.\n\n"
         "Delta closes the essay.\n")

REVISIONS = [
    {"old": "Alpha stays", "new": "Alpha moves", "note": "r-alpha"},
    {"old": "Beta is to be replaced.", "new": "Beta, replaced.",
     "note": "r-beta"},
    {"old": "Gamma is to be reworded.", "new": "Gamma, as proposed.",
     "note": "r-gamma"},
    {"old": "", "new": "Epsilon is added after Delta.", "note": "r-add",
     "anchor_paragraph": 5},
]


def _staged(root: Path, name: str, essay: str = ESSAY):
    """A manuscript whose solo.md was pushed as ESSAY (collected, so the
    base is a version snapshot) and has REVISIONS staged in its tab by
    the outside caller 'harvest'."""
    ws = root / name
    ms = ws / "book"
    ms.mkdir(parents=True)
    (ms / "solo.md").write_text(essay)
    (ms / "_illustrations").mkdir()
    (ms / "_illustrations" / "a-gem-0000-01.png").write_bytes(b"png")
    with contextlib.redirect_stdout(io.StringIO()):
        cli_main(["--workspace", str(ws), "init", "--name", "book",
                  "--path", str(ms), "--extraction", "off"])
    db = api.open_db(str(ws))
    manuscript = api.get_manuscript(db)
    with contextlib.redirect_stdout(io.StringIO()):
        api.collect(db, manuscript, {})
    tab = strip_embed_lines(essay)
    fake = _SurgicalDocFake([("book", ""), ("solo.md", tab)], render=True)
    meta = gdocs._mapping(db, manuscript)
    links = meta.setdefault("gdocs", {})
    links["_master_id"] = "doc-fake"
    links["_container_tab"] = "tab-1"
    links["solo.md"] = {"tab_id": "tab-2", "checked_out": True,
                        "pushed_hash": _h(normalize_markdown(tab))}
    gdocs._save_mapping(db, manuscript, meta)
    with _google(fake), contextlib.redirect_stdout(io.StringIO()):
        out = api.stage_revisions(db, manuscript, "solo.md", REVISIONS,
                                  "harvest", fake, fake)
    assert len(out["written"]) == 4, out
    # The page's session, without its worker threads.
    audiobook._SESSIONS.pop((str(ws), "book"), None)
    audiobook.session(str(ws), "book", inline=True)
    return ws, db, manuscript, ms, fake


def _call(ws: Path, fake, method: str, **params) -> dict:
    with _google(fake), contextlib.redirect_stdout(io.StringIO()):
        return forms_page.dispatch(method, {"manuscript": "book", **params},
                                   workspace=str(ws))


def _by_note(listing: dict) -> dict[str, dict]:
    return {f["note"]: f for g in listing["files"] for f in g["forms"]}


def _rule_all(ws: Path, fake, rulings: dict[str, tuple[str, str | None]]):
    forms = _by_note(_call(ws, fake, "list"))
    for note, (verdict, text) in rulings.items():
        r = _call(ws, fake, "decide", id=forms[note]["id"], verdict=verdict,
                  seen=forms[note]["current"], text=text)
        assert "error" not in r, r


def _open(db, manuscript) -> int:
    return db.one("SELECT COUNT(*) AS n FROM doc_threads WHERE "
                  "manuscript_id = ? AND state = 'written'",
                  (manuscript["id"],))["n"]


# ----------------------------------------------------------- api helpers

def rulings_on_marked_text(_root: Path) -> None:
    print("api.pair_external_forms / rule_forms_in_marked:")
    marked = ("# T\n\n"
              "~~<<Same old.>>~~{{First new.}} and ~~<<Same old.>>~~"
              "{{Second new.}}\n\n"
              "Anchor paragraph.\n\n"
              "{{A wholly new paragraph.}}\n")
    threads = [
        {"id": "a", "proposed_old": "Same old.", "proposed_new": "First new."},
        {"id": "b", "proposed_old": "Same old.", "proposed_new": "Second new."},
        {"id": "c", "proposed_old": "", "proposed_new": "A wholly new paragraph."},
        {"id": "gone", "proposed_old": "Not in the tab.", "proposed_new": "x"},
        {"id": "em", "proposed_old": "He mistaketh affliction",
         "proposed_new": "He mistaketh it"},
    ]
    marked += "\n~~<<*He mistaketh affliction*>>~~{{He mistaketh it}}*, and so.*\n"
    pairs = api.pair_external_forms(marked, threads)
    check("two forms on the same old text pair in staging order",
          pairs["a"]["new"] == "First new." and pairs["b"]["new"]
          == "Second new.", pairs)
    check("an old half the export wrapped in emphasis still pairs "
          "(SMSTTD nietzsche.md, 2026-11-10)",
          pairs.get("em", {}).get("old") == "*He mistaketh affliction*", pairs)
    check("an addition pairs with its own-line form; a missing one is absent",
          pairs["c"]["kind"] == "insert" and "gone" not in pairs, pairs)
    ruled, order = api.rule_forms_in_marked(
        marked, threads, {"a": "", "b": "Edited second.",
                          "c": "Nothing like the staged words at all."})
    check("each ruling becomes the form's green half, nothing else moves",
          "~~<<Same old.>>~~{{}} and ~~<<Same old.>>~~{{Edited second.}}"
          in ruled and "{{Nothing like the staged words at all.}}" in ruled
          and "Anchor paragraph." in ruled, ruled)
    check("a ruled addition keeps its pairing by place", order == {"c": 0},
          order)
    try:
        api.rule_forms_in_marked(marked, threads, {"gone": "y"})
        raised = False
    except ValueError:
        raised = True
    check("a ruling on a form gone from the tab lands nothing (raises)",
          raised)


# ------------------------------------------------------------------ list

def list_shows_the_doc_as_it_stands(root: Path) -> None:
    print("list:")
    ws, db, manuscript, ms, fake = _staged(root, "list-ws")
    fake.edit("tab-2", "{{Gamma, as proposed.}}", "{{Gamma, my words.}}")
    got = _call(ws, fake, "list")
    check("one file, its four forms counted", len(got["files"]) == 1
          and got["files"][0]["file"] == "solo.md"
          and got["files"][0]["count"] == 4 and got["total"] == 4, got)
    group = got["files"][0]
    check("the file links to its own tab of the master Doc",
          group["url"] == "https://docs.google.com/document/d/doc-fake/edit"
                          "?tab=tab-2", group["url"])
    forms = _by_note(got)
    check("each form carries its origin and note",
          all(f["origin"] == "harvest" for f in forms.values())
          and set(forms) == {"r-alpha", "r-beta", "r-gamma", "r-add"})
    gamma = forms["r-gamma"]
    check("the green half reads as the author left it in the Doc",
          gamma["current"] == "Gamma, my words."
          and gamma["proposed"] == "Gamma, as proposed."
          and gamma["edited_in_doc"] is True, gamma)
    alpha = forms["r-alpha"]
    check("a form inside a sentence shows the rest of its paragraph",
          alpha["prefix"] == "" and alpha["suffix"] == " as it is."
          and alpha["current"] == "Alpha moves", alpha)
    check("an addition shows the paragraph it follows",
          forms["r-add"]["kind"] == "insert"
          and forms["r-add"]["anchor"] == "Delta closes the essay.",
          forms["r-add"])
    check("the file says it takes no new forms while these are open",
          "takes no new forms" in group["blocking"]
          and "4 open form" in group["blocking"], group["blocking"])
    check("every form waits for a ruling", group["undecided"] == 4)

    ws2 = root / "empty-ws"
    (ws2 / "book").mkdir(parents=True)
    (ws2 / "book" / "solo.md").write_text(ESSAY)
    with contextlib.redirect_stdout(io.StringIO()):
        cli_main(["--workspace", str(ws2), "init", "--name", "book",
                  "--path", str(ws2 / "book"), "--extraction", "off"])
    audiobook.session(str(ws2), "book", inline=True)
    with _no_google():
        got = forms_page.dispatch("list", {"manuscript": "book"},
                                  workspace=str(ws2))
    check("no open forms: an empty list and no Google call",
          got["files"] == [] and got["total"] == 0, got)


# ---------------------------------------------------------------- decide

def decide_holds_rulings(root: Path) -> None:
    print("decide:")
    ws, db, manuscript, ms, fake = _staged(root, "decide-ws")
    tab_before = fake.tab_text("solo.md")
    forms = _by_note(_call(ws, fake, "list"))
    beta = forms["r-beta"]
    r = _call(ws, fake, "decide", id=beta["id"], verdict="edit",
              seen=beta["current"], text="Beta, in my words.")
    check("an edit is held with the text and what was seen",
          r["ruling"]["verdict"] == "edit"
          and r["ruling"]["text"] == "Beta, in my words."
          and r["ruling"]["seen"] == "Beta, replaced.", r)
    check("a ruling touches neither the tab nor the file",
          fake.tab_text("solo.md") == tab_before
          and (ms / "solo.md").read_text() == ESSAY)
    again = _by_note(_call(ws, fake, "list"))["r-beta"]
    check("the list shows the held ruling",
          again["ruling"]["verdict"] == "edit", again)
    for label, text in (("an edit with form markers",
                         "Beta {{x}}"),
                        ("an empty edit", "  "),
                        ("an edit over two lines", "one\ntwo")):
        r = _call(ws, fake, "decide", id=beta["id"], verdict="edit",
                  seen=beta["current"], text=text)
        check(f"{label} is turned down with a reason", "error" in r, r)
    r = _call(ws, fake, "decide", id=beta["id"], verdict="maybe",
              seen=beta["current"])
    check("an unknown verdict is turned down", "error" in r, r)
    r = _call(ws, fake, "decide", id=beta["id"], verdict="clear", seen=None)
    check("clear takes the ruling back", r["ruling"] is None
          and _by_note(_call(ws, fake, "list"))["r-beta"]["ruling"] is None)

    r = _call(ws, fake, "merge", file="solo.md")
    check("merge with forms unruled stops, naming them",
          r["stopped"] == "undecided" and "4 form(s)" in r["message"]
          and "Beta is to be replaced." in r["message"], r)
    check("a stopped merge leaves the tab, the file and the threads",
          fake.tab_text("solo.md") == tab_before
          and (ms / "solo.md").read_text() == ESSAY
          and _open(db, manuscript) == 4)


# ----------------------------------------------------------------- merge

def merge_lands_the_rulings(root: Path) -> None:
    print("merge:")
    ws, db, manuscript, ms, fake = _staged(root, "merge-ws")
    fake.edit("tab-2", "{{Gamma, as proposed.}}", "{{Gamma, my words.}}")
    _rule_all(ws, fake, {"r-alpha": ("decline", None),
                         "r-beta": ("accept", None),
                         "r-gamma": ("accept", None),
                         "r-add": ("edit", "Epsilon, in the page's words.")})
    r = _call(ws, fake, "merge", file="solo.md")
    check("merge lands: 1 accepted, 2 modified, 1 declined, pushed",
          r["stopped"] is None and r["accepted"] == 1 and r["modified"] == 2
          and r["declined"] == 1 and r["pushed"] is True, r)
    text = (ms / "solo.md").read_text()
    check("the file has each ruling: the Doc's words, the page's words, "
          "the declined text kept",
          "Alpha stays as it is." in text and "Alpha moves" not in text
          and "Beta, replaced." in text and "Gamma, my words." in text
          and "Epsilon, in the page's words." in text
          and "Epsilon is added after Delta." not in text, text)
    check("no form markers in the file", "<<" not in text and "{{" not in text,
          text)
    tab = fake.tab_text("solo.md")
    check("the tab is cleared and reads as the file",
          "<<" not in tab and "{{" not in tab
          and "Epsilon, in the page's words." in tab
          and "Alpha stays as it is." in tab, tab)
    check("no form is left open", _open(db, manuscript) == 0)
    again = _call(ws, fake, "list")
    check("the list is empty after the merge", again["files"] == [], again)


def merge_declines_everything(root: Path) -> None:
    print("decline:")
    ws, db, manuscript, ms, fake = _staged(root, "decline-ws")
    _rule_all(ws, fake, {n: ("decline", None)
                         for n in ("r-alpha", "r-beta", "r-gamma", "r-add")})
    r = _call(ws, fake, "merge", file="solo.md")
    check("every form declined", r["stopped"] is None and r["declined"] == 4
          and r["accepted"] == 0 and r["modified"] == 0, r)
    check("the file keeps its text", normalize_markdown(
        (ms / "solo.md").read_text()) == normalize_markdown(ESSAY))
    tab = fake.tab_text("solo.md")
    check("the tab drops every form and keeps the old text",
          "<<" not in tab and "{{" not in tab and "Beta is to be replaced."
          in tab and "Epsilon" not in tab, tab)


def merge_stops_on_a_doc_edit_after_ruling(root: Path) -> None:
    print("changed after ruling:")
    ws, db, manuscript, ms, fake = _staged(root, "changed-ws")
    _rule_all(ws, fake, {n: ("accept", None)
                         for n in ("r-alpha", "r-beta", "r-gamma", "r-add")})
    fake.edit("tab-2", "{{Beta, replaced.}}", "{{Beta, rewritten later.}}")
    r = _call(ws, fake, "merge", file="solo.md")
    check("a green half edited in the Doc after the ruling stops the merge",
          r["stopped"] == "changed" and "Beta, rewritten later." in r["message"],
          r)
    check("nothing landed", (ms / "solo.md").read_text() == ESSAY
          and _open(db, manuscript) == 4)


# -------------------------------------------------------------- safety

def merge_stops_on_conflict(root: Path) -> None:
    print("conflict:")
    ws, db, manuscript, ms, fake = _staged(root, "conflict-ws")
    _rule_all(ws, fake, {n: ("accept", None)
                         for n in ("r-alpha", "r-beta", "r-gamma", "r-add")})
    local = ESSAY.replace("Delta closes the essay.",
                          "Delta closes the essay, edited locally.")
    (ms / "solo.md").write_text(local)
    fake.edit("tab-2", "# Topic", "# Topic, retitled in the Doc")
    tab_before = fake.tab_text("solo.md")
    listing = _call(ws, fake, "list")
    group = listing["files"][0]
    check("the list marks the file in conflict and shows both sides",
          group["state"] == "conflict"
          and "retitled in the Doc" in group["diff"]["doc"]
          and "edited locally" in group["diff"]["local"], group.get("diff"))
    r = _call(ws, fake, "merge", file="solo.md")
    check("merge stops on a conflict and shows both sides of doc diff",
          r["stopped"] == "conflict"
          and "retitled in the Doc" in r["diff"]["doc"]
          and "edited locally" in r["diff"]["local"], r)
    check("no pull: the local edit stays",
          (ms / "solo.md").read_text() == local)
    check("no push: the tab is as it was, forms and all",
          fake.tab_text("solo.md") == tab_before)
    check("the forms stay open", _open(db, manuscript) == 4)


def merge_never_pulls_a_stale_tab(root: Path) -> None:
    print("stale tab:")
    ws, db, manuscript, ms, fake = _staged(root, "stale-ws")
    _rule_all(ws, fake, {n: ("accept", None)
                         for n in ("r-alpha", "r-beta", "r-gamma", "r-add")})
    # Another session landed an edit locally and recorded the file as the
    # tab's base without the tab taking it: the tab now holds an OLDER
    # text of the file, and reads as if only the Doc had moved.
    newer = ESSAY.replace("Delta closes the essay.",
                          "Delta closes the essay, as accepted elsewhere.")
    (ms / "solo.md").write_text(newer)
    with contextlib.redirect_stdout(io.StringIO()):
        api.collect(db, manuscript, {})
    meta = gdocs._mapping(db, manuscript)
    meta["gdocs"]["solo.md"]["pushed_hash"] = _h(normalize_markdown(newer))
    gdocs._save_mapping(db, manuscript, meta)
    tab_before = fake.tab_text("solo.md")
    r = _call(ws, fake, "merge", file="solo.md")
    check("a tab holding an older version of the file is not pulled",
          r["stopped"] == "stale" and "older text" in r["message"], r)
    check("the file keeps the newer edit",
          (ms / "solo.md").read_text() == newer)
    check("the tab and the threads are as they were",
          fake.tab_text("solo.md") == tab_before
          and _open(db, manuscript) == 4)


def merge_keeps_illustration_lines(root: Path) -> None:
    print("illustrations:")
    tagged = ("[Illustration: A gem ⇢ a-gem.md]\n"
              "![](_illustrations/a-gem-0000-01.png)")
    ws, db, manuscript, ms, fake = _staged(
        root, "illus-ws", ESSAY + "\n" + tagged + "\n")
    _rule_all(ws, fake, {n: ("accept", None)
                         for n in ("r-alpha", "r-beta", "r-gamma", "r-add")})
    r = _call(ws, fake, "merge", file="solo.md")
    after = (ms / "solo.md").read_text()
    check("the merge lands", r["stopped"] is None and r["accepted"] == 4, r)
    check("the illustration line is still under its tag", tagged in after,
          after)
    check("the embed line never reaches the tab",
          "_illustrations" not in fake.tab_text("solo.md"))

    # The guard behind the landing: a line the landing dropped is put back
    # (under its tag, else after the line it followed); one with no place
    # is named.
    before = "# T\n\nAlpha.\n![](_illustrations/x-01.png)\n\nBeta.\n"
    text, back, missing = forms_page.keep_embeds(
        before, "# T\n\nAlpha, edited.\n\nBeta.\n", ms)
    check("a dropped line whose neighbour is gone is named, not guessed",
          back == [] and missing == ["![](_illustrations/x-01.png)"],
          (text, back, missing))
    text, back, missing = forms_page.keep_embeds(
        before, "# T\n\nAlpha.\n\nBeta, edited.\n", ms)
    check("a dropped line goes back after the line it followed",
          "Alpha.\n![](_illustrations/x-01.png)\n" in text
          and back == ["![](_illustrations/x-01.png)"] and missing == [],
          text)


def unknown_method(root: Path) -> None:
    print("transport:")
    ws, db, manuscript, ms, fake = _staged(root, "transport-ws")
    r = _call(ws, fake, "frobnicate")
    check("an unknown method is an error, not an exception",
          r == {"error": "unknown method 'frobnicate'"}, r)
    r = _call(ws, fake, "decide", id="dt-nope", verdict="accept", seen="x")
    check("an unknown form is an error", "error" in r, r)


def main() -> None:
    root = Path(tempfile.mkdtemp(prefix="forms-page-"))
    for fn in (rulings_on_marked_text, list_shows_the_doc_as_it_stands, decide_holds_rulings,
               merge_lands_the_rulings, merge_declines_everything,
               merge_stops_on_a_doc_edit_after_ruling,
               merge_stops_on_conflict, merge_never_pulls_a_stale_tab,
               merge_keeps_illustration_lines, unknown_method):
        fn(root)
    print(f"OK — {PASSED} Merge forms checks")


if __name__ == "__main__":
    main()
