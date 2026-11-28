"""Change kinds in place (`authorlm.workflow.edits`): replace, insert, cut
and move as the forms of construction, and beats inserted, not replacing.

The owner's design (2026-11-27): "I don't want to replace the whole current
essay ... it makes more sense to insert the beat at the beginning of the
essay and leave the rest of the essay intact. And then at the end ...
whatever we've left over, have we covered everything correctly? ... after
insertion of a beat, you remove the lines from the essay that don't
belong"; his approval: "you can build, cut, insert and move, section by
section insertion."

These checks pin, on an essay without headings: beat 1 inserted at the
start leaves every old paragraph; each paragraph the beat supersedes is a
leftover form (cut by default, or keep); coverage lists exactly the
paragraphs no beat claimed; a cut removes only its paragraph; a move
removes in one place and adds in the other; several changes at one unit
are several forms; landing inserts in the right place and is idempotent;
reopening an insert takes it back out of the file; no ruling shrinks the
essay to a beat's own text (the guard); and a walk recorded before change
kinds (the in-flight apply-findings walk, after a beats stretch with an
old `b1:section` form) still renders, applies and lands.

Hermetic: the fixture world of test_workflow_page and the fake Doc road of
test_workflow_landing; no network.
Run: python3 tests/test_workflow_changes.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_workflow_landing as tl  # noqa: E402  (the fake Doc road)
from test_workflow_page import FAKE, ONE, call, raises, world  # noqa: E402

from authorlm import api  # noqa: E402
from authorlm.workflow import core, edits  # noqa: E402

PASSED = 0
P1, P2, P3 = "The first paragraph.", "The second paragraph.", "The third paragraph."


def check(label: str, cond: bool, detail: object = "") -> None:
    global PASSED
    print(f"  [{'ok' if cond else 'FAIL'}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)
    PASSED += 1


def change(kind: str, original: str = "", where: str = "", quote: str = "",
           text: str = "") -> dict:
    return {"kind": kind, "original": original, "where": where, "quote": quote,
            "text": text}


def walk(ws: str, iid: str) -> dict:
    return call(ws, "instance", id=iid)["walk"]


def units(w: dict) -> dict:
    return {u["label"]: u for u in w["units"]}


def to_beats(ws: str, unit: str = "section") -> str:
    iid = call(ws, "start", essay="one", kind="refine")["id"]
    call(ws, "skip", id=iid, reason="No letter this time.", to="construction")
    call(ws, "unit", id=iid, mode="beats", unit=unit, reason="rebuild it")
    return iid


def plan(supersedes: list[str], where: str = "start", quote: str = "") -> dict:
    return {"proposed": "Open on the demand.", "reason": "it opens the case",
            "placement": {"where": where, "quote": quote, "supersedes": supersedes}}


def test_edits_module() -> None:
    text = "# T\n\nA one.\n\nB two.\n\nC three.\n"
    out, pairs, _ = edits.apply(text, change("insert", where="start", text="New."))
    check("an insert at the start goes in after the title, nothing removed",
          out == "# T\n\nNew.\n\nA one.\n\nB two.\n\nC three.\n"
          and pairs == [{"original": "# T", "new": "# T\n\nNew."}], (out, pairs))
    out, pairs, _ = edits.apply(text, change("cut", original="B two."))
    check("a cut of a paragraph takes it out with its blank line",
          out == "# T\n\nA one.\n\nC three.\n", out)
    out, pairs, _ = edits.apply("# T\n\nOne. Two. Three.\n\nNext.\n",
                                change("cut", original="Two."))
    check("a cut inside a paragraph changes that paragraph only",
          out == "# T\n\nOne. Three.\n\nNext.\n"
          and pairs == [{"original": "One. Two. Three.", "new": "One. Three."}], out)
    out, pairs, _ = edits.apply(text, change("move", original="A one.",
                                             where="after", quote="C thr"))
    check("a move takes it out in one place and puts it in at the other",
          out == "# T\n\nB two.\n\nC three.\n\nA one.\n" and len(pairs) == 2, out)
    raises(lambda: edits.apply(text, change("cut", original="Z gone.")), "not in")
    raises(lambda: edits.clean({"kind": "replace", "original": "x", "text": ""}),
           "cut is its own kind")
    check("opening words name a paragraph",
          edits.opening_match(["A one.", "B two."], "B tw") == 1)


def test_beat_inserted_leftovers_and_coverage() -> None:
    tl.reset_road()
    with world() as (ws, root):
        iid = to_beats(ws)
        check("beats come from the plan, not from headings: the walk asks the "
              "first plan", walk(ws, iid)["next"] == "b1:plan")
        FAKE.queue.append(plan(["The second"]))
        call(ws, "propose", id=iid)
        u = units(walk(ws, iid))["b1:plan"]
        check("the plan's form carries where its text goes and what it supersedes",
              u["placement"]["where"] == "start"
              and u["placement"]["supersedes"] == ["The second"], u)
        call(ws, "act", id=iid, label="b1:plan", action="accept")
        w = walk(ws, iid)
        check("then the beat's new text, as an insert", w["next"] == "b1:insert", w["next"])
        FAKE.queue.append({"proposed": "A new opening.", "reason": "the demand first"})
        msg_before = len(FAKE.sent)
        call(ws, "propose", id=iid)
        check("the engine is told its text is inserted and the old text stays",
              "inserted" in FAKE.sent[msg_before] and "old text stays" in FAKE.sent[msg_before])
        w = walk(ws, iid)
        ins = units(w)["b1:insert"]
        check("the insert's form shows its anchor and has no original",
              ins["state"] == "pending" and ins["anchor"]["where"] == "start"
              and ins["before"] == "" and ins["form"]["changes"][0]["kind"] == "insert", ins)
        call(ws, "act", id=iid, label="b1:insert", action="accept")
        w = walk(ws, iid)
        check("beat 1 inserted at the start leaves every old paragraph",
              w["text"] == f"# One\n\nA new opening.\n\n{P1}\n\n{P2}\n\n{P3}\n", w["text"])
        x = units(w).get("b1:x2")
        check("each paragraph the beat supersedes is a leftover form, a cut by default",
              x and x["state"] == "pending" and x["before"] == P2
              and x["form"]["changes"][0]["kind"] == "cut" and w["next"] == "b1:x2", w["units"])
        call(ws, "act", id=iid, label="b1:x2", action="accept")
        w = walk(ws, iid)
        check("accepting the leftover cuts that paragraph only",
              w["text"] == f"# One\n\nA new opening.\n\n{P1}\n\n{P3}\n", w["text"])
        check("what no beat claimed is computed from the base paragraphs",
              w["unclaimed"] == [1, 3], w["unclaimed"])
        v = call(ws, "conclude", id=iid)
        w = v["walk"]
        cover = [u["label"] for u in w["units"] if u["kind"] == "cover"]
        check("before concluding, coverage lists exactly the unclaimed paragraphs",
              cover == ["beats:cover:p1", "beats:cover:p3"] and not w["concluded"], cover)
        check("the gate names them", "left standing" in v["gate"], v["gate"])
        raises(lambda: call(ws, "conclude", id=iid), "left standing")
        call(ws, "act", id=iid, label="beats:cover:p1", action="keep")
        call(ws, "act", id=iid, label="beats:cover:p3", action="cut")
        v = call(ws, "conclude", id=iid)
        check("ruled, the beats conclude", v["walk"]["end"] and v["gate"] is None, v["gate"])
        v = call(ws, "advance", id=iid)
        text = (root / "one.md").read_text()
        check("landing inserts the beat in the right place and makes the cuts",
              text == f"# One\n\nA new opening.\n\n{P1}\n", text)
        check("each change lands as its own unit",
              sorted(u["label"] for u in v["landed"]["units"])
              == ["b1:insert", "b1:x2", "beats:cover:p3"], v["landed"]["units"])
        out = tl.cli(ws, "workflow", "-m", "MS", "land", iid)
        check("landing again lands nothing twice",
              "nothing to land" in out and (root / "one.md").read_text() == text, out)


def test_reopen_insert_takes_it_out() -> None:
    tl.reset_road()
    with world() as (ws, root):
        iid = to_beats(ws)
        FAKE.queue.append(plan([], where="after", quote="The first"))
        call(ws, "propose", id=iid)
        call(ws, "act", id=iid, label="b1:plan", action="accept")
        FAKE.queue.append({"proposed": "Inserted after the first."})
        call(ws, "propose", id=iid)
        call(ws, "act", id=iid, label="b1:insert", action="accept")
        call(ws, "land", id=iid)
        check("an insert anchored after a paragraph lands after it",
              (root / "one.md").read_text()
              == f"# One\n\n{P1}\n\nInserted after the first.\n\n{P2}\n\n{P3}\n")
        call(ws, "reopen", id=iid, label="b1:insert")
        check("reopening the insert takes it back out of the file",
              (root / "one.md").read_text() == ONE, (root / "one.md").read_text())


def _apply_findings(ws: str) -> str:
    iid = call(ws, "start", essay="one", kind="refine")["id"]
    call(ws, "skip", id=iid, reason="No letter this time.", to="construction")
    return iid


def test_cut_move_and_several_changes() -> None:
    tl.reset_road()
    with world() as (ws, root):
        iid = _apply_findings(ws)
        FAKE.queue.append({"changes": [change("move", where="after", quote="The third")],
                           "reason": "the first belongs at the end"})
        call(ws, "propose", id=iid)
        u = units(walk(ws, iid))["p1"]
        check("one change is the unit's own form, of its kind",
              u["state"] == "pending" and u["form"]["changes"][0]["kind"] == "move", u)
        call(ws, "act", id=iid, label="p1", action="accept")
        w = walk(ws, iid)
        check("a move removes in one place and adds in the other",
              w["text"] == f"# One\n\n{P2}\n\n{P3}\n\n{P1}\n", w["text"])
        FAKE.queue.append({"changes": [change("replace", original="second", text="2nd"),
                                       change("insert", where="end", text="A close.")]})
        call(ws, "propose", id=iid)
        w = walk(ws, iid)
        us = units(w)
        check("several changes at one unit are several forms",
              us["p2"]["state"] == "split" and us["p2:c1"]["state"] == "pending"
              and us["p2:c2"]["form"]["changes"][0]["kind"] == "insert"
              and w["next"] == "p2:c1", w["units"])
        raises(lambda: call(ws, "act", id=iid, label="p2", action="accept"), "rule each")
        out = tl.cli(ws, "workflow", "-m", "MS", "walk", iid)
        check("the CLI shows the waiting change plainly: its kind and texts",
              "p2:c1  pending  (change)" in out
              and "REPLACE" in out and "--- with (p2:c1) ---\n2nd\n--- end ---" in out, out)
        out = tl.cli(ws, "workflow", "-m", "MS", "walk", iid, "--label", "p2:c2")
        check("an insert shows where it goes and its new text",
              "INSERT" in out and "inserted at the end of the essay" in out
              and "--- new text (p2:c2) ---\nA close.\n--- end ---" in out, out)
        out = tl.cli(ws, "workflow", "-m", "MS", "walk", iid, "--label", "p1")
        check("a ruled move shows both places",
              "MOVE" in out and "moved to: after the paragraph opening “The third”" in out, out)
        call(ws, "act", id=iid, label="p2:c1", action="accept")
        call(ws, "act", id=iid, label="p2:c2", action="reject")
        call(ws, "act", id=iid, label="p3", action="cut")
        w = walk(ws, iid)
        check("a cut removes only its paragraph; a rejected change leaves the text",
              w["text"] == f"# One\n\nThe 2nd paragraph.\n\n{P1}\n", w["text"])
        check("the walk reaches its end", w["end"], w["next"])
        call(ws, "advance", id=iid)
        text = (root / "one.md").read_text()
        check("each change lands on its own text",
              text == f"# One\n\nThe 2nd paragraph.\n\n{P1}\n", text)


def test_author_move_and_edit_of_a_leftover() -> None:
    with world() as (ws, _root):
        iid = _apply_findings(ws)
        raises(lambda: call(ws, "act", id=iid, label="p1", action="move",
                            where="after", quote="Not there"), "not in the essay")
        call(ws, "act", id=iid, label="p1", action="move", where="end")
        w = walk(ws, iid)
        check("the author moves a paragraph himself, no model call",
              w["text"] == f"# One\n\n{P2}\n\n{P3}\n\n{P1}\n"
              and units(w)["p1"]["state"] == "edited", w["text"])


def test_guard_no_ruling_shrinks_the_essay_to_a_beat() -> None:
    """The defect the owner hit: accepting a beat's section made the beat's
    text the whole essay. A beat's text is an insert under every label,
    the old `b<n>:section` included."""
    tl.reset_road()
    with world() as (ws, root):
        iid = to_beats(ws)
        db = api.open_db(ws)
        ms = api.get_manuscript(db, "MS")
        inst = core.get_instance(db, ms, iid)
        reply = {"text": "", "reason": "the plan", "proposed": "Open on it.",
                 "suggested_blurbs": [], "usage": {}, "requests": []}
        core.record_turn(db, inst, "start", unit_label="b1:plan", reply=reply)
        core.record_turn(db, inst, "edit", unit_label="b1:plan", author_text="My plan.")
        body = f"{P1}\n\n{P2}\n\n{P3}"
        core.record_turn(db, inst, "start", unit_label="b1:section", unit_before=body,
                         reply={**reply, "proposed": "Only the beat."})
        call(ws, "act", id=iid, label="b1:section", action="accept")
        w = walk(ws, iid)
        check("an old-format section accepted is inserted; every paragraph stays",
              all(p in w["text"] for p in (P1, P2, P3, "Only the beat.")), w["text"])
        call(ws, "beat_done", id=iid)
        call(ws, "conclude", id=iid)  # coverage forms for p1..p3
        for k in (1, 2, 3):
            call(ws, "act", id=iid, label=f"beats:cover:p{k}", action="keep")
        call(ws, "conclude", id=iid)
        call(ws, "advance", id=iid)
        text = (root / "one.md").read_text()
        check("and the landed file keeps every paragraph",
              all(p in text for p in (P1, P2, P3)) and "Only the beat." in text, text)


def test_in_flight_walk_recorded_before_change_kinds() -> None:
    """The shape of the owner's walk on 2026-11-27: a beats stretch (an
    edited plan, a `b1:section` form never ruled, its unit_before the
    whole body), then a switch to apply the findings by paragraph, p1-p3
    passed and p4 with a whole-paragraph proposal waiting; no turn has
    changes."""
    tl.reset_road()
    with world() as (ws, root):
        iid = to_beats(ws)
        db = api.open_db(ws)
        ms = api.get_manuscript(db, "MS")
        inst = core.get_instance(db, ms, iid)
        reply = {"text": "", "reason": "r", "proposed": "Plan.",
                 "suggested_blurbs": [], "usage": {}, "requests": []}
        core.record_turn(db, inst, "start", unit_label="b1:plan", reply=reply)
        core.record_turn(db, inst, "edit", unit_label="b1:plan", author_text="Plan, mine.")
        core.record_turn(db, inst, "start", unit_label="b1:section",
                         unit_before=f"{P1}\n\n{P2}\n\n{P3}",
                         reply={**reply, "proposed": "A whole new essay."})
        # Switched with the b1:section form still waiting, as the record
        # shows (straight through the workflow, as the coordinator did).
        core.set_unit(db, core.get_instance(db, ms, iid), "paragraph",
                      phase="construction", mode="apply-findings",
                      reason="in place, paragraph by paragraph")
        inst = core.get_instance(db, ms, iid)
        for k, part in ((1, P1), (2, P2)):
            core.record_turn(db, inst, "start", unit_label=f"p{k}", unit_before=part,
                             reply={**reply, "proposed": "", "reason": ""})
        core.record_turn(db, inst, "start", unit_label="p3", unit_before=P3,
                         reply={**reply, "proposed": "A third, tighter.",
                                "reason": "tightened"})
        rows = db.all("SELECT edits FROM session_turns WHERE instance_id = ?", (iid,))
        check("the record holds no changes, as the old format did",
              all(r["edits"] == "{}" for r in rows), [r["edits"] for r in rows])
        v = call(ws, "instance", id=iid)
        w = v["walk"]
        check("it renders: the earlier beats stretch changes nothing, p1-p2 "
              "passed, p3 waiting",
              [u["state"] for u in w["units"]] == ["passed", "passed", "pending"]
              and w["text"] == ONE and w["next"] == "p3", w["units"])
        check("the waiting form reads as one whole-paragraph replace",
              w["units"][2]["form"]["changes"][0]["kind"] == "replace"
              and w["units"][2]["form"]["proposed"] == "A third, tighter.")
        call(ws, "act", id=iid, label="p3", action="accept")
        w = walk(ws, iid)
        check("it applies", w["text"] == ONE.replace(P3, "A third, tighter."), w["text"])
        call(ws, "advance", id=iid)
        check("and it lands", (root / "one.md").read_text()
              == ONE.replace(P3, "A third, tighter."))


def test_cut_reopen_with_repeated_neighbour() -> None:
    """A whole-paragraph cut keeps enough neighbour context that `new` is
    unique, so reopen splices the cut back beside the same neighbour — not
    an earlier copy of the same words."""
    text = "So.\n\nSo.\n\nGone.\n"
    out, pairs, _ = edits.apply(text, change("cut", original="Gone."))
    check("the cut pair widens past a repeated neighbour",
          pairs == [{"original": "So.\n\nSo.\n\nGone.", "new": "So.\n\nSo."}]
          and out == "So.\n\nSo.\n", (out, pairs))
    row = pairs[0]
    hit = edits.find(out, row["new"])
    i, j, _how = hit
    check("reopening the pair restores the essay",
          out[:i] + row["original"] + out[j:] == text)

    tl.reset_road()
    with world() as (ws, root):
        essay = "# One\n\nSo.\n\nSo.\n\nGone.\n"
        (root / "one.md").write_text(essay)
        iid = _apply_findings(ws)
        call(ws, "act", id=iid, label="p3", action="cut")
        call(ws, "land", id=iid)
        check("the cut lands without touching the earlier So.",
              (root / "one.md").read_text() == "# One\n\nSo.\n\nSo.\n")
        call(ws, "reopen", id=iid, label="p3")
        check("reopen puts Gone. back after the second So., not the first",
              (root / "one.md").read_text() == essay,
              (root / "one.md").read_text())


def test_sole_paragraph_cut_reopen_restores() -> None:
    """Cutting the only paragraph lands new=\"\"; reopen restores while the
    file is still empty (session and file stay in sync)."""
    tl.reset_road()
    with world() as (ws, root):
        (root / "one.md").write_text("Only paragraph.\n")
        iid = _apply_findings(ws)
        call(ws, "act", id=iid, label="p1", action="cut")
        call(ws, "land", id=iid)
        check("a sole-paragraph cut empties the file",
              not (root / "one.md").read_text().strip())
        call(ws, "reopen", id=iid, label="p1")
        check("reopen restores the sole paragraph",
              (root / "one.md").read_text() == "Only paragraph.\n",
              (root / "one.md").read_text())


def test_page_lays_out_each_kind() -> None:
    src = (Path(__file__).resolve().parents[1] / "web" / "audiobook" / "src"
           / "Workflows.tsx").read_text(encoding="utf-8")
    view = src[src.index("function ChangeView"):src.index("const KIND_WORDS")]
    check("the page lays out a replace as before and after (the diff)",
          'c.kind === "replace") return <Diff' in view)
    check("an insert with its place and the new text in green",
          'c.kind === "insert"' in view and "Inserted {whereWords" in view
          and '<ins className="add">{c.text}</ins>' in view)
    check("a cut struck", 'c.kind === "cut"' in view and '<del className="del">{orig}</del>' in view)
    check("a move in both places", "Moved from here" in view and "…and put in {whereWords" in view)
    check("the author's own cut and move, and keep on a leftover or coverage form",
          'action: "cut"' in src and 'action: "move"' in src and 'action: "keep"' in src)


def main() -> None:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        print(t.__name__)
        t()
    print(f"{PASSED} checks passed")


if __name__ == "__main__":
    main()
