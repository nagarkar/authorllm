"""Landing (`authorlm.workflow.landing`): an instance's ruled units written
into the essay file and pushed to its Doc tab through the Doc road.

These checks pin: the rulings land when a step that rules text ends, and on
demand (`workflow land`, Land on the page), never twice; each unit lands on
its original text, never on a position, so an essay the owner edited
between turns gets the ruled units where their text is and keeps his
edits; a unit whose original is gone is not landed, needs the owner, and
shows on the page and in `workflow status` until it is left; reject in a
refine keeps the original and in a new essay drops the paragraph; a reopen
takes a landed unit back out of the file; the Doc road's guard stops a
landing before anything is written, and a stopped push puts the file back;
`--dry-run` writes nothing; `edits = "owner"` declines, `register =
"protected"` does not.

Hermetic: the fixture world of test_workflow_page (a temp workspace, a temp
library, a fake engine) and a fake Doc road; no network, no real Doc.
Run: python3 tests/test_workflow_landing.py
"""
from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_workflow_page as wp  # noqa: E402  (registers the fake engine)
from test_workflow_page import ONE, call, raises, world  # noqa: E402

from authorlm.cli import main as cli_main  # noqa: E402
from authorlm.workflow import landing  # noqa: E402

PASSED = 0


def check(label: str, cond: bool, detail: object = "") -> None:
    global PASSED
    print(f"  [{'ok' if cond else 'FAIL'}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)
    PASSED += 1


class FakeRoad:
    """The Doc road's two moves: the guard before writing, the push after."""

    def __init__(self):
        self.pushes: list[tuple[str, str]] = []
        self.checks = 0
        self.block: str | None = None
        self.fail: str | None = None

    def check(self, db, manuscript, rel):
        self.checks += 1
        return self.block

    def push(self, db, manuscript, rel):
        if self.fail:
            raise LookupError(self.fail)
        text = (Path(manuscript["path"]) / rel).read_text(encoding="utf-8")
        self.pushes.append((rel, text))
        return {"pushed": True, "note": "", "url": f"https://doc/{rel}"}


ROAD = FakeRoad()
landing.ROAD_FACTORY = lambda db, manuscript, workspace: ROAD


def reset_road() -> None:
    ROAD.pushes.clear()
    ROAD.checks = 0
    ROAD.block = ROAD.fail = None


def cli(ws: str, *argv: str) -> str:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        cli_main(["--workspace", ws, *argv])
    return buf.getvalue()


def _to_construction(ws: str) -> str:
    iid = call(ws, "start", essay="one", kind="refine")["id"]
    call(ws, "skip", id=iid, reason="No letter this time.", to="construction")
    return iid


def test_lands_at_step_end_and_once() -> None:
    reset_road()
    with world() as (ws, root):
        iid = _to_construction(ws)
        call(ws, "propose", id=iid)
        call(ws, "act", id=iid, label="p1", action="accept")
        call(ws, "propose", id=iid)
        call(ws, "act", id=iid, label="p2", action="edit", text="My second.")
        v = call(ws, "propose", id=iid)  # p3 passes
        check("nothing lands while the step is worked",
              (root / "one.md").read_text() == ONE and not ROAD.pushes)
        check("the page says what waits to land",
              v["landing"]["waiting"] == 2 and not v["landing"]["needs_owner"],
              v["landing"])
        v = call(ws, "advance", id=iid)
        text = (root / "one.md").read_text()
        check("leaving construction lands its rulings in the file",
              text == "# One\n\nA tightened paragraph.\n\nMy second.\n\n"
                      "The third paragraph.\n", text)
        check("and pushes the Doc tab once, after writing",
              ROAD.pushes == [("one.md", text)], ROAD.pushes)
        check("the page shows what landed",
              v["landed"]["written"] and v["landing"]["landed"] == 2
              and v["landing"]["waiting"] == 0, v["landed"])
        out = cli(ws, "workflow", "-m", "MS", "land", iid)
        check("landing again lands nothing twice",
              "nothing to land" in out and len(ROAD.pushes) == 1
              and (root / "one.md").read_text() == text, out)
        call(ws, "wrapup", id=iid)
        call(ws, "act", id=iid, label="w:placeholders:1", action="accept")
        call(ws, "act", id=iid, label="w:removed:1", action="reject")
        v = call(ws, "advance", id=iid)
        need = [u["label"] for u in v["landing"]["needs_owner"]]
        check("a wrap-up item whose text is not in the essay needs the owner",
              need == ["w:placeholders:1"] and len(ROAD.pushes) == 1, need)


def test_owner_edits_between_turns() -> None:
    reset_road()
    with world() as (ws, root):
        iid = _to_construction(ws)
        call(ws, "propose", id=iid)
        call(ws, "act", id=iid, label="p1", action="accept")
        # The owner edits the file by hand: a new opening paragraph (every
        # position moves), his own first paragraph, and p2 rewrapped.
        (root / "one.md").write_text(
            "# One\n\nA new opening the owner wrote.\n\n"
            "The first paragraph, edited by hand.\n\nThe second\nparagraph.\n\n"
            "The third paragraph.\n")
        v = call(ws, "propose", id=iid)
        u2 = v["walk"]["units"][1]
        check("the walk is cut from the base text: p2 is still the second "
              "paragraph as construction found it",
              u2["label"] == "p2" and u2["before"] == "The second paragraph.", u2)
        check("the page says the file moved",
              v["landing"]["file_changed"] is True)
        call(ws, "act", id=iid, label="p2", action="accept")
        call(ws, "propose", id=iid)
        v = call(ws, "advance", id=iid)
        text = (root / "one.md").read_text()
        check("p2 lands on its own text, not its old position, and the "
              "owner's edits stay",
              text == "# One\n\nA new opening the owner wrote.\n\n"
                      "The first paragraph, edited by hand.\n\n"
                      "A tightened paragraph.\n\nThe third paragraph.\n", text)
        need = v["landing"]["needs_owner"]
        check("p1, whose original the owner rewrote, is not landed and "
              "needs him, with both texts",
              [u["label"] for u in need] == ["p1"]
              and need[0]["original"] == "The first paragraph."
              and need[0]["new"] == "A tightened paragraph.", need)
        out = cli(ws, "workflow", "-m", "MS", "status", "one")
        check("workflow status shows it",
              "1 unit(s) need you, not landed: construction p1" in out, out)
        out = cli(ws, "workflow", "-m", "MS", "land", iid)
        check("a retry still does not land it", "NEEDS YOU" in out
              and (root / "one.md").read_text() == text, out)
        cli(ws, "workflow", "-m", "MS", "land", iid, "--leave", "p1")
        out = cli(ws, "workflow", "-m", "MS", "status", "one")
        check("left as the file has it, it is no longer reported",
              "need you" not in out and (root / "one.md").read_text() == text, out)


def test_reject_refine_vs_new() -> None:
    reset_road()
    with world() as (ws, root):
        iid = _to_construction(ws)
        call(ws, "propose", id=iid)
        call(ws, "act", id=iid, label="p1", action="reject")
        call(ws, "skip", id=iid, reason="The rest is fine.", to="wrapup")
        check("reject in a refine keeps the original: nothing written",
              (root / "one.md").read_text() == ONE and not ROAD.pushes)
    reset_road()
    with world() as (ws, root):
        iid = call(ws, "start", essay="three", kind="new")["id"]
        call(ws, "advance", id=iid)
        call(ws, "accept_letter", id=iid)
        call(ws, "advance", id=iid)
        call(ws, "propose", id=iid)
        call(ws, "act", id=iid, label="b1:plan", action="accept")
        call(ws, "propose", id=iid)
        call(ws, "act", id=iid, label="b1:p1", action="accept")
        call(ws, "propose", id=iid)
        call(ws, "act", id=iid, label="b1:p2", action="reject")
        call(ws, "beat_done", id=iid)
        call(ws, "conclude", id=iid)
        v = call(ws, "advance", id=iid)
        text = (root / "three.md").read_text()
        check("a new essay lands whole; reject dropped its paragraph",
              text.strip() == "A drafted paragraph." and v["landed"]["written"]
              and ROAD.pushes[-1][0] == "three.md", text)


def test_reopen_restores_text() -> None:
    reset_road()
    with world() as (ws, root):
        iid = _to_construction(ws)
        call(ws, "propose", id=iid)
        call(ws, "act", id=iid, label="p1", action="accept")
        v = call(ws, "land", id=iid)
        check("Land on the page lands the step's rulings so far",
              "A tightened paragraph." in (root / "one.md").read_text()
              and v["landed"]["written"], v["landed"])
        call(ws, "reopen", id=iid, label="p1")
        check("a reopen takes the landed unit back out of the file and the tab",
              (root / "one.md").read_text() == ONE
              and ROAD.pushes[-1][1] == ONE and len(ROAD.pushes) == 2)
        call(ws, "act", id=iid, label="p1", action="accept")  # "First, again."
        call(ws, "land", id=iid)
        check("the new ruling lands on the original",
              (root / "one.md").read_text().startswith("# One\n\nFirst, again.\n"))


def test_doc_road_guard_and_stopped_push() -> None:
    reset_road()
    with world() as (ws, root):
        iid = _to_construction(ws)
        call(ws, "propose", id=iid)
        call(ws, "act", id=iid, label="p1", action="accept")
        ROAD.block = "the Doc tab of one.md has edits that are not in the file"
        v = call(ws, "land", id=iid)
        check("the Doc having edits not in the file stops the landing before "
              "anything is written",
              (root / "one.md").read_text() == ONE and not ROAD.pushes
              and "nothing was landed" in v["landed"]["note"], v["landed"])
        check("the page shows the stop",
              "edits that are not in the file" in v["landing"]["last"]["note"])
        ROAD.block, ROAD.fail = None, "did not push; doc has changed. pull first"
        v = call(ws, "land", id=iid)
        check("a stopped push puts the file back and lands nothing",
              (root / "one.md").read_text() == ONE
              and "put back" in v["landed"]["note"]
              and v["landing"]["waiting"] == 1, v["landed"])
        ROAD.fail = None
        v = call(ws, "land", id=iid)
        check("once the road is clear the landing goes through",
              v["landed"]["written"] and v["landing"]["waiting"] == 0)


def test_dry_run_and_normalized_match() -> None:
    reset_road()
    with world() as (ws, root):
        iid = _to_construction(ws)
        call(ws, "propose", id=iid)
        call(ws, "act", id=iid, label="p1", action="accept")
        (root / "one.md").write_text(ONE.replace("The first paragraph.",
                                                 "The  first\nparagraph."))
        out = cli(ws, "workflow", "-m", "MS", "land", iid, "--dry-run")
        check("--dry-run says what would land and writes nothing",
              "construction p1: would land" in out
              and "to:   A tightened paragraph." in out
              and "The  first\nparagraph." in (root / "one.md").read_text()
              and ROAD.checks == 0, out)
        cli(ws, "workflow", "-m", "MS", "land", iid)
        check("a unit whose original was only rewrapped lands after "
              "normalizing whitespace",
              (root / "one.md").read_text().startswith(
                  "# One\n\nA tightened paragraph.\n\nThe second"))
    check("normalized matching straightens quotes and dashes",
          landing.find("He said “no” — twice.", 'said "no" - twice')
          is not None)
    check("between equal matches, the first after the cursor",
          landing.find("x. y. x.", "x.", 3)[0] == 6)


def test_owner_only_attribute_and_protected_register() -> None:
    reset_road()
    with world() as (ws, root):
        toc = (root / "toc.toml").read_text()
        (root / "toc.toml").write_text(toc.replace(
            'file = "one.md"', 'file = "one.md"\nedits = "owner"'))
        iid = _to_construction(ws)
        call(ws, "propose", id=iid)
        call(ws, "act", id=iid, label="p1", action="accept")
        v = call(ws, "land", id=iid)
        check('edits = "owner" declines the landing, with its reason',
              (root / "one.md").read_text() == ONE
              and 'edits = "owner"' in v["landed"]["note"], v["landed"])
    reset_road()
    with world() as (ws, root):
        iid = call(ws, "start", essay="two", kind="refine")["id"]
        call(ws, "skip", id=iid, reason="No letter.", to="construction")
        call(ws, "propose", id=iid)
        call(ws, "act", id=iid, label="p1", action="edit", text="In A, plainly.")
        call(ws, "land", id=iid)
        check('register = "protected" does not block a landing',
              "In A, plainly." in (root / "two.md").read_text())


def test_google_road_without_a_tab() -> None:
    with world() as (ws, _root):
        from authorlm import api
        db = api.open_db(ws)
        ms = api.get_manuscript(db, "MS")
        road = landing.GoogleDocRoad(ws)
        check("a file with no Doc tab needs no Google sign-in to land",
              road.check(db, ms, "one.md") is None)
        out = road.push(db, ms, "one.md")
        check("and is not pushed, with how to make its tab",
              out["pushed"] is False and "doc push one.md" in out["note"], out)


def test_filters_walk_the_file_once_the_wrapup_is_in_it() -> None:
    """#554: a wrap-up item placed by hand and left is in the text the
    filters walk; the base is the file, taken once."""
    reset_road()
    with world() as (ws, root):
        iid = _to_construction(ws)
        for _ in range(3):
            v = call(ws, "propose", id=iid)
            u = v["walk"]["units"]
            pend = [x for x in u if x["state"] == "pending"]
            if pend:
                call(ws, "act", id=iid, label=pend[0]["label"], action="accept")
        call(ws, "advance", id=iid)
        call(ws, "wrapup", id=iid)
        call(ws, "act", id=iid, label="w:placeholders:1", action="accept")
        call(ws, "act", id=iid, label="w:removed:1", action="reject")
        v = call(ws, "advance", id=iid)
        check("at the filters, with an item that needs the owner",
              v["instance"]["step"] == "filters"
              and [u["label"] for u in v["landing"]["needs_owner"]]
              == ["w:placeholders:1"], v["landing"])
        hand = (root / "one.md").read_text().rstrip("\n") + (
            "\n\nA line the owner placed by hand.\n")
        (root / "one.md").write_text(hand)
        v = call(ws, "instance", id=iid)
        check("no base is taken while a ruling is not in the file",
              "A line the owner placed by hand." not in v["walk"].get("base", ""),
              v["walk"].get("base", ""))
        cli(ws, "workflow", "-m", "MS", "land", iid, "--leave", "w:placeholders:1")
        v = call(ws, "instance", id=iid)
        check("once it is left, the filters walk the file as it stands",
              v["walk"].get("base") == hand, v["walk"].get("base"))
        (root / "one.md").write_text(hand + "\nLater owner text.\n")
        cli(ws, "workflow", "-m", "MS", "land", iid)
        v = call(ws, "instance", id=iid)
        check("the base is taken once, so the labels keep naming the same text",
              v["walk"].get("base") == hand, v["walk"].get("base"))


def main() -> None:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        print(t.__name__)
        t()
    print(f"{PASSED} checks passed")


if __name__ == "__main__":
    main()
