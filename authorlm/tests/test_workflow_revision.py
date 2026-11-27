"""Revising the letter, starting over, and the `earlier_instances` request.

Source: the owner's design of 2026-11-26, his words:
- revising: "Even if I accept the findings, it need not necessarily cause
  me to not be able to redraft ... if you have an existing workflow and you
  have a letter and you've done the construction either partly or fully,
  and you want to redraft the letter because you want the rest of the
  construction or the rest of the filtering to take that letter into
  account, then I think it should be allowed." (core.LETTER_STEPS)
- starting over: "abandoning the workflow instance... copying the
  selections of voice and reference and everything that's already there
  and starting from scratch, but then changing the brief as you want it
  and regenerating the letter." (core.restart)
- the verdict made unmistakable (2026-11-26: the owner pressed Accept on a
  finding already accepted and thought the page was "not taking my
  accept").

These checks pin: a revision at the letter step after verdicts, at
construction and at the filters; the new letter in force with unruled
findings that show the earlier verdict they continue; the superseded
letter kept on the record; the walk waiting for the new findings and then
going on with accepted units kept; the reopen proposal for an accepted
unit a new finding quotes; the engine session rebuilt once (not twice)
when the letter in force changes, with earlier letters marked as history;
the filter checklist re-ticked at the filters; edits open until the
filters; start over closing the instance as abandoned and carrying its
choices and brief; `earlier_instances` read from the record and served by
both engines' tool plumbing (OpenAI function tools, the MCP read server);
the CLI verbs and the page's controls.

Hermetic: a temp workspace, a temp library, fake engines; no network.
Run: python3 tests/test_workflow_revision.py
"""
from __future__ import annotations

import asyncio
import contextlib
import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_workflow_page as tp  # noqa: E402  (scrubs the env, registers the fake)
from test_workflow_page import (FakeEngine, _to_construction, _to_letter,  # noqa: E402
                                call, raises, world)

from authorlm import api, workflow, workflow_page  # noqa: E402
from authorlm.cli import main as cli_main  # noqa: E402
from authorlm.engines import base, catalogue as cat  # noqa: E402
from authorlm.workflow import context, contract, core  # noqa: E402

PASSED = 0


def check(label: str, cond: bool, detail: object = "") -> None:
    global PASSED
    print(f"  [{'ok' if cond else 'FAIL'}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)
    PASSED += 1


REVISED = """The text now meets F2 of the letter in force.

*Findings*

1. The claim still drifts
- **Where:** the first paragraph, "A tightened paragraph"
- **What is wrong:** it still claims too much.
- **Why it matters:** the reader stops trusting it.
- **Direction:** narrow it further.
- **Tier:** must
- **Continues:** F1

2. The close is abrupt
- **Where:** the third paragraph
- **Tier:** should
- **Continues:** new

*Keep*
- the plain close

*Passes*
- cross-references: needed — it now points to two
- concision: not needed — tight now
- audio-friendly: not needed — fine
- copyedit: needed — always
"""


class Engine(FakeEngine):
    """The page tests' fake, plus a revised letter, and a session that
    resumes or rebuilds by the real rule (engines/base.SessionRef)."""

    def __init__(self):
        super().__init__()
        self.modes: list[str] = []
        self.revised = REVISED

    def send(self, instance_context: dict, message: str) -> dict:
        turn = base.Turn.of(instance_context)
        ref = base.SessionRef.parse(turn.session_ref)
        self.modes.append("resume" if ref and ref.resumable(self.name, turn)
                          else "rebuild")
        if message.startswith("Revise the editorial letter"):
            self.sent.append(message)
            self.contexts.append(instance_context)
            out = {"text": self.revised, "reason": "", "proposed": "",
                   "suggested_blurbs": [], "requests": [],
                   "usage": {"input": 1, "cached": 0, "output": 1,
                             "dollars": 0.0, "est_subscription": 0.0}}
        else:
            out = super().send(instance_context, message)
        self.session_ref = str(base.SessionRef.after(self.name, "h", turn))
        return out


ENG = Engine()
contract.register_engine("claude-headless", lambda model: ENG)


def test_revise_at_the_letter_after_verdicts() -> None:
    with world() as (ws, _root):
        iid = _to_letter(ws)
        call(ws, "letter", id=iid)
        raises(lambda: call(ws, "revise_letter", id="nope"), "no workflow instance")
        call(ws, "finding", id=iid, item="F1", verdict="accept", reason="yes")
        call(ws, "finding", id=iid, item="F2", verdict="reject", reason="deliberate echo")
        v = call(ws, "revise_letter", id=iid, note="look again at the claim")
        L = v["letter"]
        check("the new letter is in force, its findings unruled",
              [(f["item"], f["verdict"]) for f in L["findings"]]
              == [("F1", None), ("F2", None)] and L["revision"] == 1
              and L["unruled"] == ["F1", "F2"], L["findings"])
        check("a continued finding shows the author's verdict on the earlier one",
              L["findings"][0]["continues"] == {"item": "F1", "title": "The claim drifts",
                                                "verdict": "accept", "reason": "yes"}
              and L["findings"][1]["continues"] is None, L["findings"])
        check("the superseded letter is kept with its verdicts and the note",
              len(L["earlier"]) == 1 and L["earlier"][0]["note"] == "look again at the claim"
              and [(f["item"], f["verdict"]) for f in L["earlier"][0]["findings"]]
              == [("F1", "accept"), ("F2", "reject")], L["earlier"])
        check("the Keep list is the new letter's",
              L["keep"] == ["the plain close"], L["keep"])
        msg = ENG.sent[-1]
        check("the revision is told the note, the verdicts and the Continues rule",
              "look again at the claim" in msg
              and "F1 (must): The claim drifts — accept: yes" in msg
              and "F2 (could): The ending repeats the opening — reject: deliberate echo" in msg
              and "**Continues:** F<n>" in msg and "which findings of the letter in force"
              in msg, msg)
        check("the revision is written under the letter's step",
              ENG.contexts[-1]["instance"]["step"] == "letter")
        d = [x for x in v["decisions"] if x["kind"] == "letter-revision"]
        check("the revision is a decision naming the step, note and runs",
              len(d) == 1 and d[0]["detail"]["step"] == "letter"
              and d[0]["detail"]["superseded_run"] == f"workflow/{iid}/superseded/1",
              d)
        raises(lambda: call(ws, "advance", id=iid), "not yet accepted")
        # "Accept again": the earlier verdict and reason, one tap.
        c = L["findings"][0]["continues"]
        v = call(ws, "finding", id=iid, item="F1", verdict=c["verdict"], reason=c["reason"])
        call(ws, "finding", id=iid, item="F2", verdict="accept")
        v = call(ws, "advance", id=iid)
        check("ruled, the revised letter opens construction",
              v["instance"]["step"] == "construction")


def _construction_with_p1(ws: str) -> str:
    iid = _to_construction(ws)
    call(ws, "propose", id=iid)
    call(ws, "act", id=iid, label="p1", action="accept")
    return iid


def test_revise_at_construction() -> None:
    with world() as (ws, _root):
        iid = _construction_with_p1(ws)
        before_modes = len(ENG.modes)
        v = call(ws, "revise_letter", id=iid, note="the claim, again")
        check("construction stays where it is; p1 stays accepted",
              v["instance"]["step"] == "construction"
              and v["walk"]["units"][0]["state"] == "accepted"
              and v["walk"]["next"] == "p2", v["walk"])
        msg = ENG.sent[-1]
        check("the revision says the accepted units stay and asks for exact quotes",
              "units accepted so far (p1) stay accepted" in msg
              and "goes on from p2" in msg and "quote that text exactly" in msg, msg)
        check("the revision reads the working text (the accepted unit in it)",
              "A tightened paragraph." in ENG.contexts[-1]["essay"])
        raises(lambda: call(ws, "propose", id=iid), "revised letter's 2 finding(s)")
        check("the gate says the walk waits for the revised letter",
              "revised letter's 2" in (v["gate"] or ""), v["gate"])
        check("no reopen proposal while the new findings are unruled",
              v["walk"]["reopen"] == [])
        call(ws, "finding", id=iid, item="F1", verdict="accept")
        v = call(ws, "finding", id=iid, item="F2", verdict="reject")
        check("a new finding quoting an accepted unit proposes reopening it",
              [(r["label"], r["finding"]) for r in v["walk"]["reopen"]] == [("p1", "F1")],
              v["walk"]["reopen"])
        v = call(ws, "propose", id=iid)
        msg = ENG.sent[-1]
        check("the walk goes on from p2 under the letter in force",
              v["walk"]["units"][1]["state"] == "pending"
              and "The claim still drifts" in msg and "The claim drifts" not in msg
              and "- the plain close" in msg and "plainness" not in msg, msg)
        modes = ENG.modes[before_modes:]
        check("the revision resumes; the next turn rebuilds once (the letter in force "
              "changed); the one after resumes",
              modes == ["resume", "rebuild"], modes)
        call(ws, "act", id=iid, label="p2", action="accept")
        call(ws, "propose", id=iid)
        check("then the session resumes", ENG.modes[-1] == "resume", ENG.modes)
        ctx = ENG.contexts[-1]
        blocks = {b["name"]: b["text"] for b in ctx["standing"]}
        letters = [t for t in ctx["turns"] if t["unit_label"] == "letter"
                   and t["action"] == "start" and t["engine"]]
        check("the standing part names the letter in force",
              f"turn #{letters[-1]['seq']}" in blocks["letter-in-force"]
              and "revision 1 of the letter, asked at the construction step"
              in blocks["letter-in-force"], blocks["letter-in-force"])
        check("never two live letters: the earlier one is marked as history",
              letters[0]["reply"].startswith("[SUPERSEDED")
              and not letters[-1]["reply"].startswith("[SUPERSEDED"), letters)
        v = call(ws, "reopen_decline", id=iid, label="p1")
        check("leaving p1 as accepted ends the proposal",
              v["walk"]["reopen"] == [] and v["walk"]["units"][0]["state"] == "accepted")
        raises(lambda: call(ws, "reopen_decline", id=iid, label="p1"), "no reopen proposed")


def test_reopen_proposal_takes_the_reopen_path() -> None:
    with world() as (ws, _root):
        iid = _construction_with_p1(ws)
        call(ws, "revise_letter", id=iid)
        call(ws, "finding", id=iid, item="F1", verdict="accept")
        call(ws, "finding", id=iid, item="F2", verdict="defer")
        v = call(ws, "reopen", id=iid, label="p1")
        check("Reopen goes the existing path: p1 has a new form, the proposal ends",
              v["walk"]["units"][0]["state"] == "pending" and v["walk"]["reopen"] == []
              and ENG.sent[-1].startswith("Reopen p1"), v["walk"]["units"][0])


def test_a_letter_with_one_draft_changes_nothing_for_the_engine() -> None:
    with world() as (ws, _root):
        iid = _to_construction(ws)
        call(ws, "propose", id=iid)
        blocks = {b["name"]: b["text"] for b in ENG.contexts[-1]["standing"]}
        check("one letter: the letter-in-force block is empty (left out of the hash)",
              blocks["letter-in-force"] == ""
              and tuple(blocks) == context.BLOCK_ORDER)


def test_revise_at_the_filters() -> None:
    with world() as (ws, _root):
        iid = _to_construction(ws)
        call(ws, "skip", id=iid, reason="Construction is not needed here.", to="filters")
        call(ws, "filters", id=iid, names=["concision", "copyedit"])
        call(ws, "propose", id=iid)
        v = call(ws, "instance", id=iid)
        first = v["walk"]["units"][0]["label"]
        call(ws, "act", id=iid, label=first, action="accept")
        v = call(ws, "revise_letter", id=iid)
        c = v["checklist"]
        ticks = {p["name"]: p["ticked"] for p in c["passes"]}
        check("at the filters the selection reopens, re-ticked by the new letter",
              c["revised_here"] and not c["confirmed"]
              and ticks == {"cross-references": True, "concision": False,
                            "audio-friendly": False, "copyedit": True}, c)
        check("a unit already filtered keeps its ruling",
              v["walk"]["units"][0]["state"] == "accepted")
        raises(lambda: call(ws, "propose", id=iid), "choose the filters")
        call(ws, "filters", id=iid, names=["cross-references", "copyedit"])
        raises(lambda: call(ws, "propose", id=iid), "revised letter")
        call(ws, "finding", id=iid, item="F1", verdict="accept")
        call(ws, "finding", id=iid, item="F2", verdict="accept")
        v = call(ws, "propose", id=iid)
        check("the walk goes on with the new choice for the units still ahead",
              v["walk"]["units"][0]["state"] == "accepted"
              and "apply cross-references, copyedit" in ENG.sent[-1], ENG.sent[-1][:80])


def test_edits_and_discussion_open_until_the_filters() -> None:
    with world() as (ws, _root):
        iid = _to_construction(ws)
        v = call(ws, "finding_edit", id=iid, item="F1", fields={"what": "late but wanted"})
        check("a finding is editable at construction",
              v["letter"]["findings"][0]["what"] == "late but wanted")
        v = call(ws, "keep", id=iid, items=["the plain close"])
        check("so is the Keep list", v["letter"]["keep"] == ["the plain close"])
        check("the edits are letter turns, not walk turns",
              all(t["step"] == "letter" for t in v["turns"] if t["action"] == "edit"))
        call(ws, "skip", id=iid, reason="On to lint.", to="lint")
        raises(lambda: call(ws, "finding_edit", id=iid, item="F1", fields={"what": "x"}),
               "from the letter step to the filters")
        raises(lambda: call(ws, "revise_letter", id=iid), "from the letter step to the filters")
        raises(lambda: call(ws, "letter_reply", id=iid, text="x"), "to the filters")


def test_revise_needs_a_letter() -> None:
    with world() as (ws, _root):
        iid = _to_letter(ws)
        raises(lambda: call(ws, "revise_letter", id=iid), "not written yet")
        check("no letter, no Revise offered",
              call(ws, "instance", id=iid)["letter"]["revise"] is False)


def test_revise_runs_as_a_background_job() -> None:
    from authorlm.workflow import jobs
    with world() as (ws, _root):
        iid = _to_letter(ws)
        call(ws, "letter", id=iid)
        v = workflow_page.dispatch("revise_letter", {"manuscript": "MS", "id": iid,
                                                     "note": "n"},
                                   workspace=ws, background=True)
        # The job may end before the view is built (test_workflow_page's
        # `settle` says why); the job record is the proof it ran as one.
        job = jobs.jobs(api.open_db(ws), iid)[-1]
        check("Revise runs as a background job",
              job["method"] == "revise_letter" and job["params"] == {"note": "n"}
              and v["instance"]["id"] == iid, job)
        jobs.wait(job["id"], 10)
        check("the job's turn lands", call(ws, "instance", id=iid)["letter"]["revision"] == 1)


def test_restart() -> None:
    with world() as (ws, _root):
        iid = call(ws, "start", essay="one", kind="refine", brief="tighten it")["id"]
        call(ws, "list", id=iid, list="voice", essay="preface", change="add")
        call(ws, "advance", id=iid)
        call(ws, "letter", id=iid)
        call(ws, "finding", id=iid, item="F1", verdict="accept", reason="yes")
        call(ws, "letter_reply", id=iid, text="F2 is deliberate")
        raises(lambda: call(ws, "restart", id=iid, reason=""), "needs a reason")
        raises(lambda: call(ws, "restart", id=iid, reason="One. Two more."), "one sentence")
        out = call(ws, "restart", id=iid, reason="The brief missed the point.",
                   brief="argue the second case")
        new = out["id"]
        v = call(ws, "instance", id=new)
        check("a new instance starts with the old one's choices and the new brief",
              out["abandoned"] == iid and v["instance"]["step"] == "start"
              and v["instance"]["number"] == 2 and v["instance"]["voice"] == ["preface.md"]
              and v["instance"]["brief"] == "argue the second case"
              and v["restarted_from"] == {"id": iid,
                                          "reason": "The brief missed the point."}, v["instance"])
        old = call(ws, "instance", id=iid)
        check("the old instance is kept, closed as abandoned, never finished",
              old["instance"]["step"] == "done" and old["instance"]["finished_at"] is None
              and old["abandoned"]["reason"] == "The brief missed the point."
              and old["abandoned"]["successor"] == new and old["abandoned"]["step"] == "letter"
              and len(old["letter"]["findings"]) == 2, old["abandoned"])
        ov = call(ws, "overview")
        one = next(e for e in ov["essays"] if e["file"] == "one.md")
        check("the essay lists both, the old one marked abandoned",
              [(i["id"], i["abandoned"]) for i in one["instances"]]
              == [(iid, True), (new, False)] and one["open"] == new, one)
        raises(lambda: call(ws, "restart", id=iid, reason="Again."), "is done")
        out = call(ws, "restart", id=new, reason="Back to the first brief.")
        check("without a new brief the brief carries over",
              call(ws, "instance", id=out["id"])["instance"]["brief"] == "argue the second case")
        call(ws, "restart", id=out["id"], reason="Once more.")
        last = call(ws, "overview")
        last = next(e for e in last["essays"] if e["file"] == "one.md")["open"]
        call(ws, "skip", id=last, reason="Close it to test the re-review rule.", to="done")
        db = api.open_db(ws)
        check("an abandoned instance never counts as finished (re-review needs one)",
              db.one("SELECT COUNT(*) AS n FROM workflow_instances WHERE step = 'done' "
                     "AND finished_at IS NOT NULL")["n"] == 1)


def test_earlier_instances() -> None:
    with world() as (ws, _root):
        iid = _to_letter(ws)
        call(ws, "letter", id=iid)
        call(ws, "finding", id=iid, item="F1", verdict="accept", reason="yes")
        call(ws, "revise_letter", id=iid, note="the claim, again")
        call(ws, "finding", id=iid, item="F1", verdict="reject", reason="no longer")
        call(ws, "letter_reply", id=iid, text="F2 is deliberate")
        db = api.open_db(ws)
        ms = api.get_manuscript(db, "MS")
        got = workflow.earlier_instances(db, ms, "one.md")
        check("an open instance is not an earlier one", isinstance(got, str)
              and "No earlier workflow instance" in got, got)
        new = call(ws, "restart", id=iid, reason="Start again on the claim.")["id"]
        got = workflow.earlier_instances(db, ms, "one.md")
        h = got[0]
        check("the abandoned instance, with why", len(got) == 1 and h["instance"] == iid
              and h["ended"] == "abandoned"
              and h["abandoned_because"] == "Start again on the claim.", h)
        sup, now = h["letters"]
        check("both letters: the superseded one with its verdicts and the note",
              not sup["in_force"] and sup["superseded"] == {"at_step": "letter",
                                                            "note": "the claim, again"}
              and sup["findings"][0]["verdict"] == "accept"
              and now["in_force"] and now["author_note"] == "the claim, again"
              and now["findings"][0] == {**now["findings"][0], "verdict": "reject",
                                         "reason": "no longer", "continues": "F1"}, h["letters"])
        check("the discussion", h["discussion"][-1]["author"] == "F2 is deliberate")
        reader = cat.BookReader(ws, "MS", refresh=False)
        text, entry = cat.run(reader, "earlier_instances", {"file": "one.md"})
        check("the catalogue serves it", entry["declined"] is None
              and "Start again on the claim." in text and iid in text, text[:300])
        text, entry = cat.run(reader, "earlier_instances", {"file": "../etc/passwd"})
        check("outside the book is still declined", entry["declined"], entry)

        # The OpenAI engine: a function call to earlier_instances, answered.
        from authorlm.engines.openai_api import OpenAIEngine
        reply = {"text": "ok", "reason": "", "proposed": None, "suggested_blurbs": []}

        def resp(rid, output, text=None):
            return SimpleNamespace(
                id=rid, status="completed", output=output, output_text=text,
                incomplete_details=None,
                usage=SimpleNamespace(input_tokens=10, output_tokens=5,
                                      input_tokens_details=SimpleNamespace(cached_tokens=0)))

        calls: list[dict] = []
        script = [resp("r1", [SimpleNamespace(
            type="function_call", name="earlier_instances", call_id="c1",
            arguments=json.dumps({"file": "one.md"}))]),
            resp("r2", [SimpleNamespace(type="message", content=[])], json.dumps(reply))]

        class Responses:
            def create(self, **kw):
                calls.append(kw)
                return script.pop(0)

        eng = OpenAIEngine("gpt-test", client=SimpleNamespace(responses=Responses()),
                           reader_factory=lambda turn: cat.BookReader(ws, turn.manuscript,
                                                                     refresh=False),
                           pricer=lambda *a: 0.0)
        got = eng.send({"standing": ["CHARTER"], "turns": [], "session_ref": None,
                        "manuscript": "MS", "essay": "one.md", "workspace": ws},
                       "Write the letter.")
        out = calls[1]["input"][0]["output"]
        check("openai: earlier_instances is a function tool and its answer goes back",
              "earlier_instances" in [t.get("name") for t in calls[0]["tools"]]
              and "Start again on the claim." in out
              and got["requests"][0]["name"] == "earlier_instances"
              and not got["requests"][0]["declined"], out[:200])

        # claude-headless: the MCP read server lists and serves it.
        import mcp.types as types
        from authorlm.engines.mcp_read import build_server
        server = build_server(cat.BookReader(ws, "MS", refresh=False))

        async def go():
            tools = await server.request_handlers[types.ListToolsRequest](
                types.ListToolsRequest(method="tools/list"))
            res = await server.request_handlers[types.CallToolRequest](
                types.CallToolRequest(method="tools/call", params=types.CallToolRequestParams(
                    name="earlier_instances", arguments={"file": "one.md"})))
            return tools.root.tools, res.root.content[0].text

        tools, text = asyncio.run(go())
        check("mcp: earlier_instances is a tool and serves the record",
              "earlier_instances" in [t.name for t in tools]
              and "Start again on the claim." in text, text[:200])
        check("the new instance's letter can read it: it is the essay's earlier one",
              call(ws, "instance", id=new)["instance"]["carried_from"] == iid)


def test_earlier_instances_of_a_new_essay() -> None:
    with world() as (ws, _root):
        iid = call(ws, "start", essay="fresh", kind="new", brief="a new one")["id"]
        call(ws, "restart", id=iid, reason="A better idea came.")
        reader = cat.BookReader(ws, "MS", refresh=False)
        text, entry = cat.run(reader, "earlier_instances", {"file": "fresh.md"})
        check("a new essay outside the toc is served once it has instances",
              entry["declined"] is None and "A better idea came." in text, text[:200])
        text, entry = cat.run(reader, "earlier_instances", {"file": "nothing.md"})
        check("a file with no instances outside the toc is declined", entry["declined"])


def _cli(ws: str, *argv) -> str:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        cli_main(["--workspace", ws, "-m", "MS", "workflow", *argv])
    return out.getvalue()


def test_cli() -> None:
    with world() as (ws, _root):
        iid = _to_letter(ws)
        call(ws, "letter", id=iid)
        call(ws, "finding", id=iid, item="F1", verdict="accept")
        sent = len(ENG.sent)
        out = json.loads(_cli(ws, "revise-letter", iid, "--note", "n", "--dry-run", "--json"))
        check("revise-letter --dry-run calls no model and writes nothing",
              out["dry_run"] and out["revision"] == 1 and out["ruled"] == 1
              and len(ENG.sent) == sent and out["next"]
              and call(ws, "instance", id=iid)["letter"]["revision"] == 0, out)
        out = json.loads(_cli(ws, "revise-letter", iid, "--note", "the claim", "--json"))
        check("revise-letter revises and the board carries next",
              out["revision"] == 1 and [f["item"] for f in out["findings"]] == ["F1", "F2"]
              and out["next"].startswith("authorlm workflow show"), out)
        out = _cli(ws, "revise-letter", iid)
        check("the board says the new letter is in force", "the new letter is in force"
              in out and "superseded" in out, out)
        out = json.loads(_cli(ws, "restart", iid, "--reason", "Start again.",
                              "--dry-run", "--json"))
        check("restart --dry-run writes nothing",
              out["dry_run"] and call(ws, "instance", id=iid)["abandoned"] is None, out)
        out = json.loads(_cli(ws, "restart", iid, "--reason", "Start again.",
                              "--brief", "b2", "--json"))
        check("restart starts the new instance with the new brief",
              out["brief"] == "b2" and out["number"] == 2 and out["next"], out)
        try:
            _cli(ws, "restart", out["id"], "--reason", "One. Two.")
            raise AssertionError("expected exit")
        except SystemExit as err:
            check("a two-sentence reason exits with the error", "one sentence" in str(err.code))
        for verb in ("revise-letter", "restart"):
            text = io.StringIO()
            with contextlib.redirect_stdout(text), contextlib.suppress(SystemExit):
                cli_main(["workflow", verb, "--help"])
            check(f"{verb} has an example", f"example: authorlm workflow {verb}"
                  in text.getvalue())
        from authorlm import completion
        from authorlm.cli import build_parser
        script = completion.bash_script(build_parser()) if hasattr(completion, "bash_script") \
            else ""
        if script:
            check("completion offers both verbs", "revise-letter" in script
                  and "restart" in script)
        skill = (Path(__file__).resolve().parents[2] / ".claude" / "skills" / "authorlm"
                 / "SKILL.md").read_text(encoding="utf-8")
        check("the skill names both verbs", "workflow revise-letter" in skill
              and "workflow restart" in skill)


def test_page_controls() -> None:
    src = Path(__file__).resolve().parents[1] / "web" / "audiobook" / "src"
    wf = (src / "Workflows.tsx").read_text(encoding="utf-8")
    css = (src / "styles.css").read_text(encoding="utf-8")
    panel = wf[wf.index("function RevisePanel"):wf.index("function LetterStep")]
    check("Revise the letter: a button, an optional note, sent as revise_letter",
          "Revise the letter…" in panel and "<textarea" in panel
          and 'run("revise_letter", { note: note.trim() })' in panel
          and ">Cancel</button>" in panel)
    check("the unsent note survives a reload, per instance",
          "`authorlm.wf.reviseNote.${manuscript}.${v.instance.id}`" in panel
          and "authorlm.wf.reviseNote.<ms>.<instance>" in wf)
    check("Revise is offered at construction, the wrap-up and the filters",
          '["construction", "wrapup", "filters"].includes(shownStep)' in wf
          and "<LetterInForce" in wf and "Rule them at the Letter step" in wf)
    check("its progress shows like other background turns",
          'revise_letter: "Revising the letter"' in wf
          and "revise_letter" in workflow_page.MODEL_TURNS)
    check("a continued finding shows its earlier verdict, with a one-tap again",
          "Continues {f.continues.item} of the earlier letter" in wf
          and "} again</button>" in wf)
    check("the reopen proposal: Reopen (the existing path) or leave it",
          'run("reopen", { label: r.label })' in wf
          and 'run("reopen_decline", { label: r.label })' in wf)
    check("at the filters, one line says the passes re-tick the units ahead",
          "apply only to the units still ahead" in wf and "c.revised_here &&" in wf)
    check("Start over from here: reason and brief, the draft kept, then the new instance",
          "Start over from here…" in wf
          and 'transport.request<{ id: string }>("restart", { id: inst.id, reason: draft.reason, brief: draft.brief })' in wf
          and "`authorlm.wf.restartDraft.${manuscript}.${inst.id}`" in wf)
    check("an abandoned instance is marked in the drop-down and on its page",
          'i.abandoned ? "abandoned"' in wf and "Abandoned at the" in wf)
    check("the methods are on the server's list",
          all(m in workflow_page.METHODS for m in ("revise_letter", "reopen_decline",
                                                   "restart")))
    check("phone: 16px editors, 44px targets",
          ".wf-revise textarea,.wf-restart textarea{" in css and "font-size:16px" in
          css[css.index(".wf-revise textarea"):] and ".wf-again{min-height:44px" in css)


def test_verdict_is_unmistakable() -> None:
    """Owner, 2026-11-26: he pressed Accept on a finding already accepted
    and thought the page was "not taking my accept"."""
    src = Path(__file__).resolve().parents[1] / "web" / "audiobook" / "src"
    wf = (src / "Workflows.tsx").read_text(encoding="utf-8")
    css = (src / "styles.css").read_text(encoding="utf-8")
    card = wf[wf.index("function FindingCard"):wf.index("function RevisePanel")]
    check("the chosen verdict button reads Accepted ✓, filled",
          "`${VERDICT_LABEL[verdict]} ✓`" in card and 'Accepted' in wf
          and '" on wf-chosen"' in card and ".pill.wf-chosen{" in css
          and "aria-pressed={chosen}" in card)
    check("the card carries a visible verdict line",
          'className={`wf-verdict ${f.verdict || "open"}`}' in card
          and '"Not yet decided"' in card and ".wf-verdict.accept{" in css)
    check("pressing the chosen verdict again sends nothing and says so",
          "if (chosen) { setAlready(true); return; }" in card
          and "Already {VERDICT_WORDS[f.verdict]}." in card)
    letter = wf[wf.index("function LetterStep"):wf.index("function LetterInForce")]
    check("when every finding is ruled, one line points to On to Construction",
          "All {L.findings.length} findings decided — next: <b>On to Construction</b>"
          in letter and "!L.unruled.length" in letter)


def main() -> None:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        print(t.__name__)
        t()
    print(f"{PASSED} checks passed")


if __name__ == "__main__":
    main()
