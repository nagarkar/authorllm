"""The Merge forms page's Conflicts (`forms_page.settle`,
`forms_page.land_conflict`, `/api/forms` list / settle / land / enqueue):
a file changed both in the file and in its Doc tab since the last push,
settled in the browser instead of by hand.

Source: the owner's ask of 2026-11-12, after settling a real conflict by
hand (`doc merge`, edit the markers, `doc pull --force`, copy the draft,
`collect`, `doc push`). Agreed then and pinned here:
  - a region only one side changed merges by itself; only a region both
    sides changed is a card (Yours, the Doc's, the base);
  - four choices: Keep mine, Keep the Doc's, Keep both (mine, then the
    Doc's, as separate paragraphs), Edit; held server-side till landing;
  - landing re-checks first: a side that moved since the cards were shown
    means a new state, not a landing;
  - picture lines (`![](_illustrations/...)`) survive the landing;
  - a tab with forms out cannot land its conflict (one producer at a
    time);
  - the CLI's `doc merge` hint names the forced pull before the push.

Hermetic: Google is the `_SurgicalDocFake` from test_passes.py; no
network, no model.
Run: python3 tests/test_forms_conflicts.py
"""
from __future__ import annotations

import contextlib
import io
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

from test_passes import _SurgicalDocFake  # noqa: E402
from test_forms_page import _call, _google, _h  # noqa: E402

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


TAG = "[Illustration: A gem ⇢ a-gem.md]"
EMBED = "![](_illustrations/a-gem-0000-01.png)"
ESSAY = ("# Topic\n\n"
         "Alpha is edited in the file only.\n\n"
         "Beta is edited on both sides.\n\n"
         "Gamma is edited in the Doc only.\n\n"
         f"{TAG}\n{EMBED}\n\n"
         "Delta closes the essay.\n")
MINE = "Beta, as the file has it."
DOCS = "Beta, as the Doc has it."


def _pushed(root: Path, name: str, stage: bool = False):
    """A manuscript whose solo.md was pushed as ESSAY (collected, so the
    base is a version snapshot), then edited on both sides: Alpha in the
    file, Gamma in the Doc, Beta in both. `stage` first puts one outside
    revision (on Delta) in the tab, as `revise stage` would."""
    ws = root / name
    ms = ws / "book"
    ms.mkdir(parents=True)
    (ms / "solo.md").write_text(ESSAY)
    (ms / "_illustrations").mkdir()
    (ms / "_illustrations" / "a-gem-0000-01.png").write_bytes(b"png")
    with contextlib.redirect_stdout(io.StringIO()):
        cli_main(["--workspace", str(ws), "init", "--name", "book",
                  "--path", str(ms), "--extraction", "off"])
    db = api.open_db(str(ws))
    manuscript = api.get_manuscript(db)
    with contextlib.redirect_stdout(io.StringIO()):
        api.collect(db, manuscript, {})
    tab = strip_embed_lines(ESSAY)
    fake = _SurgicalDocFake([("book", ""), ("solo.md", tab)], render=True)
    meta = gdocs._mapping(db, manuscript)
    links = meta.setdefault("gdocs", {})
    links["_master_id"] = "doc-fake"
    links["_container_tab"] = "tab-1"
    links["solo.md"] = {"tab_id": "tab-2", "checked_out": True,
                        "pushed_hash": _h(normalize_markdown(tab))}
    gdocs._save_mapping(db, manuscript, meta)
    if stage:
        with _google(fake), contextlib.redirect_stdout(io.StringIO()):
            out = api.stage_revisions(
                db, manuscript, "solo.md",
                [{"old": "Delta closes the essay.", "new": "Delta, staged.",
                  "note": "r-delta"}], "harvest", fake, fake)
        assert len(out["written"]) == 1, out
    audiobook._SESSIONS.pop((str(ws), "book"), None)
    audiobook.session(str(ws), "book", inline=True)
    local = (ESSAY.replace("Alpha is edited in the file only.",
                           "Alpha, edited in the file.")
             .replace("Beta is edited on both sides.", MINE))
    (ms / "solo.md").write_text(local)
    fake.edit("tab-2", "Gamma is edited in the Doc only.",
              "Gamma, edited in the Doc.")
    fake.edit("tab-2", "Beta is edited on both sides.", DOCS)
    return ws, db, manuscript, ms, fake


def _group(ws: Path, fake) -> dict:
    got = _call(ws, fake, "list")
    assert "error" not in got, got
    return next(g for g in got["files"] if g["file"] == "solo.md")


def _land(ws: Path, fake, choice: str, text: str | None = None) -> dict:
    g = _group(ws, fake)
    for card in g["conflict"]["regions"]:
        r = _call(ws, fake, "settle", file="solo.md", region=card["id"],
                  verdict=choice, text=text)
        assert "error" not in r, r
    return _call(ws, fake, "land", file="solo.md", key=g["conflict"]["key"])


# ------------------------------------------------------------------ list

def list_shows_one_card(root: Path) -> None:
    print("list:")
    ws, db, manuscript, ms, fake = _pushed(root, "list-ws")
    got = _call(ws, fake, "list")
    check("a file in conflict with no forms is listed, under Conflicts",
          got["conflicts"] == 1 and got["total"] == 0
          and [g["file"] for g in got["files"]] == ["solo.md"], got)
    g = got["files"][0]
    check("it carries no forms and no 'takes no new forms' line",
          g["count"] == 0 and g["forms"] == [] and g["blocking"] == "", g)
    c = g["conflict"]
    check("one clean change on each side merges by itself: one card",
          g["state"] == "conflict" and len(c["regions"]) == 1, c)
    card = c["regions"][0]
    check("the card shows Yours, the Doc's and the base",
          card["local"] == MINE and card["doc"] == DOCS
          and card["base"] == "Beta is edited on both sides.", card)
    check("a card off the picture tag says nothing about pictures",
          card["illustration"] is False, card)
    check("nothing decided, nothing held, not ready",
          c["decided"] == 0 and c["held"] is None and c["ready"] is False, c)

    r = _call(ws, fake, "settle", file="solo.md", region=card["id"],
              verdict="mine")
    again = _group(ws, fake)["conflict"]
    check("a choice is held server-side: a reload shows it, and the file "
          "is ready",
          again["regions"][0]["ruling"]["verdict"] == "mine"
          and again["ready"] is True and r["ruling"]["verdict"] == "mine",
          again)
    for label, verdict, text in (("an unknown choice", "maybe", None),
                                 ("an empty edit", "edit", "  "),
                                 ("an edit with form markers", "edit",
                                  "Beta {{x}}"),
                                 ("an edit with merge markers", "edit",
                                  "=======\nBeta")):
        r = _call(ws, fake, "settle", file="solo.md", region=card["id"],
                  verdict=verdict, text=text)
        check(f"{label} is turned down with a reason", "error" in r, r)
    r = _call(ws, fake, "settle", file="solo.md", region=card["id"],
              verdict="clear")
    check("clear takes the choice back", r["ruling"] is None
          and _group(ws, fake)["conflict"]["decided"] == 0)
    r = _call(ws, fake, "land", file="solo.md",
              key=_group(ws, fake)["conflict"]["key"])
    check("landing with a card undecided stops",
          r["stopped"] == "undecided", r)


def illustration_card_says_stale(root: Path) -> None:
    print("picture tag:")
    ws, db, manuscript, ms, fake = _pushed(root, "tag-ws")
    local = (ms / "solo.md").read_text().replace(
        TAG, "[Illustration: A gem in the file's words ⇢ a-gem.md]")
    (ms / "solo.md").write_text(local)
    fake.edit("tab-2", TAG, "[Illustration: A gem in the Doc's words ⇢ "
                            "a-gem.md]")
    regions = _group(ws, fake)["conflict"]["regions"]
    check("a card on the picture's tag line is marked",
          any(r["illustration"] for r in regions)
          and not all(r["illustration"] for r in regions), regions)
    r = _land(ws, fake, "doc")
    text = (ms / "solo.md").read_text()
    check("the Doc's tag lands with the picked picture still under it",
          r["stopped"] is None and
          "[Illustration: A gem in the Doc's words ⇢ a-gem.md]\n" + EMBED
          in text, (r, text))


# ----------------------------------------------------------------- land

def each_choice_lands(root: Path) -> None:
    print("land:")
    cases = {
        "mine": [MINE], "doc": [DOCS], "both": [MINE + "\n\n" + DOCS],
        "edit": ["Beta, settled on the page."],
    }
    for choice, want in cases.items():
        ws, db, manuscript, ms, fake = _pushed(root, f"land-{choice}-ws")
        text_in = "Beta, settled on the page." if choice == "edit" else None
        r = _land(ws, fake, choice, text_in)
        text = (ms / "solo.md").read_text()
        check(f"{choice}: lands and pushes",
              r["stopped"] is None and r["pushed"] is True
              and r["kept"][choice] == 1, r)
        check(f"{choice}: the card's text is the choice",
              all(w in text for w in want)
              and (choice in ("mine", "both") or MINE not in text)
              and (choice in ("doc", "both") or DOCS not in text), text)
        check(f"{choice}: both clean changes are in",
              "Alpha, edited in the file." in text
              and "Gamma, edited in the Doc." in text, text)
        check(f"{choice}: the picture line is still under its tag",
              TAG + "\n" + EMBED in text, text)
        tab = fake.tab_text("solo.md")
        check(f"{choice}: the tab reads as the file, with no picture line",
              all(w.replace("\n\n", "\n") in tab for w in want)
              and "Alpha, edited in the file." in tab
              and "_illustrations" not in tab, tab)
        view_state = gdocs.doc_three_way(db, manuscript, "solo.md", fake,
                                         fake)["state"]
        check(f"{choice}: file and tab agree after", view_state ==
              "unchanged", view_state)
        g = _call(ws, fake, "list")
        check(f"{choice}: the conflict is gone from the list, its choices "
              f"cleared",
              g["conflicts"] == 0 and g["files"] == []
              and not (gdocs._read_metadata(db, manuscript).get(
                  forms_page.CONFLICT_KEY) or {}).get("solo.md"), g)


def recheck_recomputes(root: Path) -> None:
    print("re-check:")
    ws, db, manuscript, ms, fake = _pushed(root, "moved-ws")
    g = _group(ws, fake)
    card = g["conflict"]["regions"][0]
    _call(ws, fake, "settle", file="solo.md", region=card["id"],
          verdict="mine")
    local_before = (ms / "solo.md").read_text()
    fake.edit("tab-2", "Delta closes the essay.",
              "Delta closes the essay, and the Doc moved again.")
    tab_before = fake.tab_text("solo.md")
    r = _call(ws, fake, "land", file="solo.md", key=g["conflict"]["key"])
    check("a Doc that moved since the cards: no landing, the new state",
          r["stopped"] == "moved" and r["conflict"]["key"]
          != g["conflict"]["key"], r)
    check("a card that reads the same keeps its choice",
          r["conflict"]["regions"][0]["ruling"]["verdict"] == "mine"
          and r["conflict"]["ready"] is True, r["conflict"])
    check("nothing pulled, written or pushed",
          (ms / "solo.md").read_text() == local_before
          and fake.tab_text("solo.md") == tab_before)

    (ms / "solo.md").write_text(local_before.replace(
        MINE, "Beta, rewritten in the file since."))
    r2 = _call(ws, fake, "land", file="solo.md", key=r["conflict"]["key"])
    check("a file that moved since: the new state, and the changed card "
          "is open again",
          r2["stopped"] == "moved"
          and r2["conflict"]["regions"][0]["ruling"] is None
          and r2["conflict"]["regions"][0]["local"]
          == "Beta, rewritten in the file since.", r2)
    r3 = _land(ws, fake, "mine")
    check("with the new key it lands",
          r3["stopped"] is None and "Beta, rewritten in the file since."
          in (ms / "solo.md").read_text()
          and "and the Doc moved again" in (ms / "solo.md").read_text(), r3)


def forms_out_hold_the_landing(root: Path) -> None:
    print("forms out:")
    ws, db, manuscript, ms, fake = _pushed(root, "forms-ws", stage=True)
    g = _group(ws, fake)
    check("the tab shows its forms and its conflict",
          g["count"] == 1 and g["state"] == "conflict"
          and len(g["conflict"]["regions"]) == 1, g)
    check("the conflict says it is held by the forms",
          g["conflict"]["held"] and "1 open form" in g["conflict"]["held"]
          and g["conflict"]["ready"] is False, g["conflict"])
    local_before = (ms / "solo.md").read_text()
    tab_before = fake.tab_text("solo.md")
    r = _land(ws, fake, "mine")
    check("landing stops while forms are out",
          r["stopped"] == "forms" and "open form" in r["message"], r)
    check("nothing pulled or pushed",
          (ms / "solo.md").read_text() == local_before
          and fake.tab_text("solo.md") == tab_before)


def queue_lands_a_conflict(root: Path) -> None:
    print("queue:")
    import time
    ws, db, manuscript, ms, fake = _pushed(root, "queue-ws")
    g = _group(ws, fake)
    _call(ws, fake, "settle", file="solo.md",
          region=g["conflict"]["regions"][0]["id"], verdict="both")
    with _google(fake):
        q = forms_page.dispatch("enqueue", {
            "manuscript": "book", "file": "solo.md", "conflict": True,
            "key": g["conflict"]["key"]}, workspace=str(ws))
        for _ in range(200):
            q = forms_page.dispatch("queue", {"manuscript": "book"},
                                    workspace=str(ws))
            if q["results"].get("solo.md"):
                break
            time.sleep(0.05)
    r = q["results"].get("solo.md") or {}
    check("enqueue with conflict lands the conflict on the worker",
          r.get("stopped") is None and r.get("kept", {}).get("both") == 1
          and r.get("pushed") is True, q)


def cli_hint_names_the_forced_pull(_root: Path) -> None:
    print("cli hint:")
    src = (HERE.parent / "authorlm" / "cli.py").read_text()
    at = src.index("next: settle each <<<<<<< local")
    hint = src[at: at + 700]
    check("the doc merge hint puts the forced pull before the push",
          "--force" in hint and hint.index("--force")
          < hint.index("doc push"), hint)


def main() -> None:
    root = Path(tempfile.mkdtemp(prefix="forms-conflicts-"))
    for fn in (list_shows_one_card, illustration_card_says_stale,
               each_choice_lands, recheck_recomputes,
               forms_out_hold_the_landing, queue_lands_a_conflict,
               cli_hint_names_the_forced_pull):
        fn(root)
    print(f"OK — {PASSED} Conflicts checks")


if __name__ == "__main__":
    main()
