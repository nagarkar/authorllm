"""The Workflows page (`workflow_page.dispatch`, `/api/workflow`): an essay's
workflow driven from the app on a fake engine, step by step.

Source: docs/plans/filter-lens-consolidation.md §11.9 and §12 row 5. These
checks pin: the list of workflows per essay with the current step; the
start step's recommendations accepted or declined one by one (Q1, Q3); the
letter's findings with tiers, ruled with a reason, its passes needed or
not and its paragraph notes (Q4, Q21d, Q21e); the unit form's four actions
(accept, reject keeping the original, redo with blurbs, edit with or
without a bracket; Q8a, Q8b, Q11), reopen, the engine's suggested blurbs
on the list at once; the beat plan step (Q18); the wrap-up and its
principles (Q23, Q24); the filter checklist pre-ticked from the last
instance and the letter, in the standard order (Q12); skips with one
sentence; engine and unit switches with a reason (Q6-Q8); usage totals
(Q7); and that the page never writes the essay file.

Hermetic: a temp workspace, a temp library, a fake engine; no network.
Run: python3 tests/test_workflow_page.py
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
from http.server import ThreadingHTTPServer
from pathlib import Path

os.environ["AUTHORLM_CONFIG"] = "/nonexistent/authorlm-test/config.toml"
os.environ["AUTHORLM_ENV"] = "/nonexistent/authorlm-test/.env"
os.environ["AUTHORLM_CLIENT"] = "none"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm import audiobook_server, workflow_page  # noqa: E402
from authorlm.cli import main as cli_main  # noqa: E402
from authorlm.workflow import context, contract  # noqa: E402

PASSED = 0

TOC = '''[[chapter]]
file = "preface.md"

[[chapter]]
file = "one.md"

[[chapter]]
file = "two.md"
register = "protected"
'''

ONE = ("# One\n\nThe first paragraph.\n\nThe second paragraph.\n\n"
       "The third paragraph.\n")
FILES = {"preface.md": "# Preface\n\nWhy this book.\n", "one.md": ONE,
         "two.md": "# Two\n\n## A\n\nIn A.\n\n## B\n\nIn B.\n"}

ORDER = '''
order = ["cross-references", "concision", "audio-friendly", "copyedit"]

[pass.cross-references]
default = "ticked"

[pass.audio-friendly]
default = "audiobook"
'''

LETTER = """The essay holds; two problems.

*Findings*

1. The claim drifts
- **Where:** the second paragraph, "second"
- **What is wrong:** it claims more than it shows.
- **Why it matters:** the reader stops trusting it.
- **Direction:** narrow the claim.
- **Tier:** must

2. The ending repeats the opening
- **Tier:** could

*Keep*
- the first paragraph's plainness

*Passes*
- cross-references: not needed — no pointers to other essays
- concision: needed — the middle is wordy
- audio-friendly: not needed — short sentences already
- copyedit: needed — always

*Paragraph notes*
- "The third": too abstract; give an example.

*Blurbs*
- claims too much
"""


def check(label: str, cond: bool, detail: object = "") -> None:
    global PASSED
    print(f"  [{'ok' if cond else 'FAIL'}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)
    PASSED += 1


def raises(fn, needle: str = "") -> str:
    try:
        fn()
    except (ValueError, LookupError) as err:
        if needle and needle not in str(err):
            raise AssertionError(f"expected '{needle}' in: {err}")
        return str(err)
    raise AssertionError("expected an error")


class FakeEngine:
    """Answers from a script keyed on the message; remembers each one."""

    def __init__(self):
        self.sent: list[str] = []
        self.contexts: list[dict] = []
        self.books: list[tuple] = []
        self.name, self.model = "claude-headless", ""
        self.session_ref: str | None = None
        self.letter = LETTER

    gate: threading.Event | None = None
    fail: str | None = None

    def send(self, instance_context: dict, message: str) -> dict:
        from authorlm.engines import base
        base.report_progress(requests=1, last="essay_summaries")
        if self.gate is not None:
            self.gate.wait(10)
        if self.fail:
            raise base.EngineError(self.fail)
        self.sent.append(message)
        self.contexts.append(instance_context)
        # What a real engine does to find its book (engines.base.manuscript_of).
        self.books.append(base.manuscript_of(base.Turn.of(instance_context)))
        self.session_ref = f"fake-ref-{len(self.sent)}"
        usage = {"input": 100, "cached": 50, "output": 20, "dollars": 0.0,
                 "est_subscription": 0.01}
        base = {"usage": usage, "requests": [], "suggested_blurbs": []}
        if message.startswith("Recommend"):
            return {**base, "text": json.dumps({"recommendations": [
                {"list": "voice", "essay": "preface.md", "change": "add",
                 "reason": "the book's voice is set there"},
                {"list": "reference", "essay": "two.md", "change": "add",
                 "reason": "two defines the term"}]}),
                    "reason": "", "proposed": ""}
        if message.startswith("Write the editorial letter"):
            return {**base, "text": self.letter, "reason": "", "proposed": "",
                    "suggested_blurbs": ["too abstract"]}
        if message.startswith("The walk has reached the end"):
            return {**base, "reason": "", "proposed": "", "text": json.dumps({
                "removed": [{"text": "A cut story.", "proposal": "",
                             "reason": "drop: it repeats two.md"}],
                "placeholders": [{"text": "[TK date]", "proposal": "In 1990",
                                  "reason": "the date the author gave"}],
                "terms": [],
                "principles": [{"words": "no rhetorical questions",
                                "scope": "this essay"}]})}
        if message.startswith("Beat"):
            if "paragraph 3 " in message.split("\n")[0]:
                # Nothing more to write: the beat is complete.
                return {**base, "text": "the section is complete", "reason": "",
                        "proposed": ""}
            if "propose its plan" in message.split("\n")[0]:
                return {**base, "text": "", "reason": "it opens the case",
                        "proposed": "Section 1 states the problem."}
            return {**base, "text": "", "reason": "the first move",
                    "proposed": "A drafted paragraph."}
        if message.startswith("Redo"):
            return {**base, "text": "", "reason": "shorter now",
                    "proposed": "A shorter second.",
                    "suggested_blurbs": ["still wordy"]}
        if message.startswith("The author edited"):
            return {**base, "text": "", "reason": "worked in the note",
                    "proposed": "The second, with an example."}
        if message.startswith("Reopen"):
            return {**base, "text": "", "reason": "again", "proposed": "First, again."}
        if message.startswith("Unit p3") or message.startswith("Unit s2"):
            # A unit with nothing to change passes without stopping.
            return {**base, "text": "nothing to change", "reason": "",
                    "proposed": ""}
        if message.startswith("Unit"):
            return {**base, "text": "", "reason": "tightened it (Finding F1)",
                    "proposed": "A tightened paragraph."}
        return {**base, "text": f"on that: {message[:20]}", "reason": "",
                "proposed": ""}


FAKE = FakeEngine()
# The engines package registers the real engines when it is first imported;
# import it first so the fake registered after it is the one the page gets.
import authorlm.engines  # noqa: E402,F401
contract.register_engine("claude-headless", lambda model: FAKE)
contract.register_engine("openai-api", lambda model: FAKE)


@contextlib.contextmanager
def world():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        ws = tmp / "ws"
        root = ws / "MS"
        root.mkdir(parents=True)
        (root / "toc.toml").write_text(TOC)
        for name, text in FILES.items():
            (root / name).write_text(text)
        lib = tmp / "lib"
        lib.mkdir()
        (lib / "charter.md").write_text("You are the author's partner.")
        (lib / "order.toml").write_text(ORDER)
        saved = os.environ.get(context.LIBRARY_ENV)
        os.environ[context.LIBRARY_ENV] = str(lib)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                cli_main(["--workspace", str(ws), "init", "--name", "MS",
                          "--path", str(root), "--extraction", "off"])
            yield str(ws), root
        finally:
            if saved is None:
                os.environ.pop(context.LIBRARY_ENV, None)
            else:
                os.environ[context.LIBRARY_ENV] = saved


def call(ws: str, method: str, **params):
    return workflow_page.dispatch(method, {"manuscript": "MS", **params},
                                  workspace=ws)


def test_overview_and_start_recommendations() -> None:
    with world() as (ws, _root):
        ov = call(ws, "overview")
        check("every essay of the reading order is listed",
              [e["file"] for e in ov["essays"]] == ["preface.md", "one.md", "two.md"])
        check("planned engines are not offered",
              ov["engines"] == ["claude-headless", "openai-api"], ov["engines"])
        started = call(ws, "start", essay="one", kind="refine", brief="tighten it")
        iid = started["id"]
        ov = call(ws, "overview")
        one = next(e for e in ov["essays"] if e["file"] == "one.md")
        check("the essay shows its instance and current step",
              one["open"] == iid and one["instances"][0]["step"] == "start")
        v = call(ws, "recommend", id=iid)
        recs = v["recommendations"]["items"]
        check("recommendations come back with a reason each",
              len(recs) == 2 and all(r["reason"] for r in recs), recs)
        v = call(ws, "rec", id=iid, list="voice", essay="preface.md",
                 change="add", verdict="accept", reason=recs[0]["reason"])
        check("accepting a recommendation adds the essay to its list",
              v["instance"]["voice"] == ["preface.md"])
        v = call(ws, "rec", id=iid, list="reference", essay="two.md",
                 change="add", verdict="decline")
        answers = {r["essay"]: r["answer"] for r in v["recommendations"]["items"]}
        check("each recommendation shows the author's answer",
              answers == {"preface.md": "accepted", "two.md": "declined"}, answers)
        check("a declined one changes nothing", v["instance"]["reference"] == [])
        v = call(ws, "list", id=iid, list="aware", essay="two", change="add",
                 words="keep it in mind")
        check("the author adds an essay to a list directly",
              v["instance"]["aware"] == ["two.md"])
        check("the protected register is read from the toc flag only",
              call(ws, "instance", id=iid)["protected"] is False)


def _to_letter(ws: str) -> str:
    iid = call(ws, "start", essay="one", kind="refine")["id"]
    call(ws, "advance", id=iid)
    return iid


def test_letter() -> None:
    with world() as (ws, _root):
        iid = _to_letter(ws)
        raises(lambda: call(ws, "advance", id=iid), "waits for the letter")
        v = call(ws, "letter", id=iid)
        L = v["letter"]
        check("the letter's findings are on the record with tiers",
              [(f["item"], f["tier"]) for f in L["findings"]]
              == [("F1", "must"), ("F2", "could")], L["findings"])
        check("a finding keeps where, what, why and direction",
              L["findings"][0]["direction"] == "narrow the claim.")
        check("passes needed and not needed, with the clause of reason",
              L["passes"]["cross-references"] == {"needed": False,
                                                  "reason": "no pointers to other essays"}
              and L["passes"]["concision"]["needed"], L["passes"])
        mixed = workflow_page.parse_letter(
            "*Passes*\n- concision: needed — repetition in the middle\n",
            ["repetition", "concision"])["passes"]
        check("a pass named inside another pass's reason is not marked",
              list(mixed) == ["concision"], mixed)
        check("paragraph notes listed, not ruled one by one",
              L["notes"] == ['"The third": too abstract; give an example.'])
        check("keep list", L["keep"] == ["the first paragraph's plainness"])
        check("the letter's blurbs reach the manuscript's list",
              "claims too much" in [b["text"] for b in v["blurbs"]])
        v = call(ws, "letter", id=iid)
        check("a letter none of whose findings is ruled may be redrafted; "
              "the redraft replaces its findings",
              [f["item"] for f in v["letter"]["findings"]] == ["F1", "F2"],
              v["letter"]["findings"])
        v = call(ws, "letter_reply", id=iid, text="F2 is not a problem here")
        check("a reply on the letter is discussion on the record",
              v["letter"]["discussion"][-1]["author"] == "F2 is not a problem here")
        call(ws, "finding", id=iid, item="F1", verdict="accept", reason="yes")
        raises(lambda: call(ws, "letter", id=iid), "being ruled")
        v = call(ws, "finding", id=iid, item="F2", verdict="reject",
                 reason="the echo is deliberate")
        check("each finding carries its verdict and reason",
              [(f["verdict"], f["reason"]) for f in v["letter"]["findings"]]
              == [("accept", "yes"), ("reject", "the echo is deliberate")])
        raises(lambda: call(ws, "accept_letter", id=iid), "rule each")
        v = call(ws, "advance", id=iid)
        check("construction once every finding is ruled",
              v["instance"]["step"] == "construction")


def _to_construction(ws: str) -> str:
    iid = _to_letter(ws)
    call(ws, "letter", id=iid)
    call(ws, "finding", id=iid, item="F1", verdict="accept")
    call(ws, "finding", id=iid, item="F2", verdict="defer")
    call(ws, "advance", id=iid)
    return iid


def test_unit_form_actions() -> None:
    with world() as (ws, root):
        iid = _to_construction(ws)
        v = call(ws, "instance", id=iid)
        w = v["walk"]
        check("refine walks the findings paragraph by paragraph",
              w["unit"] == "paragraph" and [u["label"] for u in w["units"]]
              == ["p1", "p2", "p3"] and w["next"] == "p1", w)
        v = call(ws, "propose", id=iid)
        u1 = v["walk"]["units"][0]
        check("the form: before, the reason, after",
              u1["state"] == "pending" and u1["before"] == "The first paragraph."
              and u1["form"]["reason"].startswith("tightened")
              and u1["form"]["proposed"] == "A tightened paragraph.", u1)
        check("the unit's message carries the accepted findings only",
              "F1" in FAKE.sent[-1] and "F2" not in FAKE.sent[-1])
        v = call(ws, "act", id=iid, label="p1", action="accept")
        check("accept: the proposal becomes the unit's text",
              v["walk"]["units"][0]["state"] == "accepted"
              and v["walk"]["units"][0]["text"] == "A tightened paragraph.")
        check("the walk moves on", v["walk"]["next"] == "p2")
        call(ws, "propose", id=iid)
        raises(lambda: call(ws, "act", id=iid, label="p2", action="redo"),
               "blurb")
        v = call(ws, "act", id=iid, label="p2", action="redo",
                 blurbs=["too wordy"], free="say it in half")
        u2 = v["walk"]["units"][1]
        check("redo: the engine tries again with the blurbs and the words",
              u2["state"] == "pending" and u2["form"]["proposed"] == "A shorter second."
              and "too wordy" in FAKE.sent[-1] and "say it in half" in FAKE.sent[-1])
        texts = [b["text"] for b in v["blurbs"]]
        check("the engine's suggested blurb is on the list at once",
              "still wordy" in texts, texts)
        check("the ticked blurb moves to the top (most recent first)",
              texts[0] == "too wordy", texts)
        v = call(ws, "act", id=iid, label="p2", action="edit",
                 text="The second, [give an example] here.")
        u2 = v["walk"]["units"][1]
        check("an edit with a bracket is a redo at that spot",
              u2["state"] == "pending"
              and u2["form"]["proposed"] == "The second, with an example."
              and "[give an example]" in FAKE.sent[-1])
        v = call(ws, "act", id=iid, label="p2", action="edit",
                 text="My own second paragraph.")
        u2 = v["walk"]["units"][1]
        check("an edit with no bracket is the author's text, no model call",
              u2["state"] == "edited" and u2["text"] == "My own second paragraph.")
        n = len(FAKE.sent)
        v = call(ws, "propose", id=iid)
        u3 = v["walk"]["units"][2]
        check("a unit with no change passes without stopping",
              u3["state"] == "passed" and v["walk"]["next"] is None, u3)
        v = call(ws, "reopen", id=iid, label="p1")
        u1 = v["walk"]["units"][0]
        check("reopen: an accepted unit gets a new form; later units kept",
              u1["state"] == "pending" and u1["form"]["proposed"] == "First, again."
              and v["walk"]["units"][1]["state"] == "edited"
              and len(FAKE.sent) == n + 2)
        v = call(ws, "act", id=iid, label="p1", action="reject")
        check("reject keeps the original",
              v["walk"]["units"][0]["state"] == "rejected"
              and v["walk"]["units"][0]["text"] == "The first paragraph.")
        check("the page never writes the essay file",
              (root / "one.md").read_text() == ONE)
        u = v["usage"]
        check("usage totals per instance, essay and month",
              u["instance"]["turns"] == u["essay"]["turns"] == u["month"]["turns"]
              and u["instance"]["input"] == 100 * u["instance"]["turns"], u)


def test_switches_and_skip() -> None:
    with world() as (ws, _root):
        iid = _to_construction(ws)
        raises(lambda: call(ws, "engine", id=iid, engine="openai-api",
                            model="gpt-x"), "reason")
        v = call(ws, "engine", id=iid, engine="openai-api", model="gpt-x",
                 reason="a different flavour")
        check("an engine switch with a reason is recorded",
              v["instance"]["engine"] == "openai-api"
              and v["decisions"][-1]["kind"] == "engine-switch")
        v = call(ws, "unit", id=iid, unit="section", reason="whole sections now")
        check("a unit switch with a reason is recorded",
              v["instance"]["unit"] == "section"
              and v["decisions"][-1]["kind"] == "unit-switch")
        check("an essay without subheadings goes by paragraph",
              v["walk"]["unit"] == "paragraph" and v["walk"]["fallback"])
        raises(lambda: call(ws, "skip", id=iid, reason="One. Two."), "one sentence")
        v = call(ws, "skip", id=iid, reason="The essay is already rebuilt.")
        check("a skip moves on with its one sentence recorded",
              v["instance"]["step"] == "wrapup"
              and v["skipped"][-1]["reason"] == "The essay is already rebuilt.")


def test_wrapup_and_filters() -> None:
    with world() as (ws, _root):
        iid = _to_construction(ws)
        call(ws, "propose", id=iid)
        call(ws, "act", id=iid, label="p1", action="accept")
        raises(lambda: call(ws, "advance", id=iid), "not reached the end")
        check("the gate says the walk has not reached the end",
              "not reached the end" in call(ws, "instance", id=iid)["gate"])
        call(ws, "propose", id=iid)
        raises(lambda: call(ws, "advance", id=iid), "form waiting")
        call(ws, "act", id=iid, label="p2", action="edit", text="My second.")
        call(ws, "propose", id=iid)  # p3 passes
        v = call(ws, "advance", id=iid)
        check("the wrap-up follows construction", v["instance"]["step"] == "wrapup")
        raises(lambda: call(ws, "advance", id=iid), "not drafted")
        v = call(ws, "wrapup", id=iid)
        check("the wrap-up is sent the essay as construction left it",
              FAKE.contexts[-1]["essay"].startswith("# One\n\nA tightened paragraph."
                                                    "\n\nMy second.\n\nThe third"),
              FAKE.contexts[-1]["essay"])
        raises(lambda: call(ws, "advance", id=iid), "not yet ruled")
        items = v["wrapup"]["items"]
        check("removed passages and placeholders are form items",
              [i["label"] for i in items] == ["w:removed:1", "w:placeholders:1"]
              and items[1]["form"]["proposed"] == "In 1990", items)
        v = call(ws, "act", id=iid, label="w:placeholders:1", action="accept")
        check("a wrap-up item is accepted like a unit",
              v["wrapup"]["items"][1]["state"] == "accepted")
        v = call(ws, "act", id=iid, label="w:removed:1", action="reject")
        check("a removed passage with no placement is dropped",
              v["wrapup"]["items"][0]["state"] == "rejected")
        check("principles listed with their scope",
              v["wrapup"]["principles"] == [{"words": "no rhetorical questions",
                                             "scope": "this essay"}])
        v = call(ws, "principle", id=iid, words="no rhetorical questions",
                 scope="this essay")
        check("a ticked principle is a candidate style rule, recorded",
              v["principles"][-1]["words"] == "no rhetorical questions"
              and "candidate" in v["principles"][-1]["reason"])
        v = call(ws, "advance", id=iid)
        c = v["checklist"]
        ticks = {p["name"]: p["ticked"] for p in c["passes"]}
        check("the checklist is the standard order",
              [p["name"] for p in c["passes"]]
              == ["cross-references", "concision", "audio-friendly", "copyedit"])
        check("a pass the letter says is not needed starts unticked, with why",
              ticks["cross-references"] is False and "not needed" in
              c["passes"][0]["why"], c["passes"][0])
        check("audio-friendly unticked with no audiobook",
              ticks["audio-friendly"] is False)
        raises(lambda: call(ws, "propose", id=iid), "choose the filters")
        v = call(ws, "filters", id=iid, names=["copyedit", "concision"])
        check("ticking only includes; the order is the standard one",
              v["instance"]["filters"] == ["concision", "copyedit"])
        v = call(ws, "propose", id=iid)
        check("the filter pass names the chosen filters in order",
              "apply concision, copyedit" in FAKE.sent[-1]
              and v["walk"]["units"][0]["state"] == "pending")
        check("the filters walk the text construction kept, not the file",
              [u["before"] for u in v["walk"]["units"]]
              == ["A tightened paragraph.", "My second.", "The third paragraph."]
              and "A tightened paragraph." in FAKE.sent[-1], v["walk"]["units"])
        check("the placeholder accepted at the wrap-up but not in the essay "
              "is listed as unplaced",
              [u["label"] for u in v["unplaced"]] == ["w:placeholders:1"], v["unplaced"])
        call(ws, "act", id=iid, label="p1", action="accept")
        raises(lambda: call(ws, "advance", id=iid), "not reached the end")
        call(ws, "skip", id=iid, reason="The other units are fine.", to="done")
        nxt = call(ws, "start", essay="one", kind="refine")["id"]
        call(ws, "skip", id=nxt, reason="Straight to the filters this time.",
             to="filters")
        c = call(ws, "instance", id=nxt)["checklist"]
        ticks = {p["name"]: p["ticked"] for p in c["passes"]}
        check("a new instance starts from the last instance's ticks",
              ticks == {"cross-references": False, "concision": True,
                        "audio-friendly": False, "copyedit": True}, ticks)


def test_beats() -> None:
    with world() as (ws, _root):
        iid = call(ws, "start", essay="three", kind="new", brief="a new essay")["id"]
        call(ws, "advance", id=iid)
        call(ws, "accept_letter", id=iid)
        v = call(ws, "advance", id=iid)
        w = v["walk"]
        check("a new essay walks beat by beat, the plan first",
              w["mode"] == "beats" and w["next"] == "b1:plan", w)
        v = call(ws, "propose", id=iid)
        u = v["walk"]["units"][0]
        check("the beat's plan is a form with a reason",
              u["label"] == "b1:plan" and u["form"]["proposed"].startswith("Section 1"))
        v = call(ws, "act", id=iid, label="b1:plan", action="accept")
        check("then the beat's text, paragraph by paragraph",
              v["walk"]["next"] == "b1:p1")
        v = call(ws, "propose", id=iid)
        check("the plan goes with the text's request",
              "Section 1 states the problem." in FAKE.sent[-1])
        v = call(ws, "act", id=iid, label="b1:p1", action="reject")
        check("in a new essay, reject drops the paragraph",
              v["walk"]["units"][1]["state"] == "dropped")
        v = call(ws, "beat_done", id=iid)
        check("beat done: the next beat's plan", v["walk"]["next"] == "b2:plan")


def test_letter_parsing_edges() -> None:
    marks = workflow_page.parse_letter(
        "*Passes*\n- repetition: needed — the closing recap is not needed\n"
        "- concision: not needed — it is needed only elsewhere\n",
        ["repetition", "concision"])["passes"]
    check("the mark is the word after the pass's name, not one in its reason",
          marks["repetition"]["needed"] is True
          and marks["concision"]["needed"] is False, marks)
    found = workflow_page.parse_letter(
        "*Findings*\n\n1. The claim drifts\n- **Direction:** two moves:\n"
        "1. narrow the claim\n2. cut the aside\n  1. indented too\n"
        "2. The ending repeats\n- **Tier:** could\n2. A repeated number\n",
        [])["findings"]
    check("a numbered list inside a finding is part of it; findings are "
          "numbered by position",
          [(f["n"], f["title"]) for f in found]
          == [(1, "The claim drifts"), (2, "The ending repeats")], found)


def test_letter_drafting_is_atomic_and_scoped() -> None:
    with world() as (ws, _root):
        iid = call(ws, "start", essay="one", kind="refine")["id"]
        raises(lambda: call(ws, "accept_letter", id=iid), "not letter")
        call(ws, "advance", id=iid)
        raises(lambda: call(ws, "accept_letter", id=iid), "write the letter first")
        FAKE.letter = ("*Findings*\n\n1. First\n- **Tier:** must\n\n"
                       "2. Second\n- **Tier:** should\n\n"
                       "2. Third, numbered again\n- **Tier:** could\n")
        try:
            v = call(ws, "letter", id=iid)
        finally:
            FAKE.letter = LETTER
        check("a repeated number in the reply does not break the letter",
              [f["item"] for f in v["letter"]["findings"]] == ["F1", "F2", "F3"]
              and v["letter"]["drafted"], v["letter"]["findings"])
        for item in ("F1", "F2", "F3"):
            call(ws, "finding", id=iid, item=item, verdict="accept")
        call(ws, "advance", id=iid)
        raises(lambda: call(ws, "letter_reply", id=iid, text="one more thing"),
               "letter step")
        raises(lambda: call(ws, "recommend", id=iid), "start step")
        check("a reply or a recommendation at construction does not open its gate",
              call(ws, "instance", id=iid)["gate"] is not None)
        raises(lambda: call(ws, "act", id=iid, label="zzz", action="reject"),
               "no unit")
        raises(lambda: call(ws, "act", id=iid, label="p1", action="reject"),
               "no proposal")
        call(ws, "propose", id=iid)
        call(ws, "act", id=iid, label="p1", action="edit", text="Mine.")
        raises(lambda: call(ws, "act", id=iid, label="p1", action="accept"),
               "no proposal waiting")
        v = call(ws, "instance", id=iid)
        check("a stale accept never replaces the author's own text",
              v["walk"]["units"][0]["text"] == "Mine.")
        raises(lambda: call(ws, "propose", id=iid, label="p1"), "reopen")


def test_unit_switch_keeps_rulings() -> None:
    with world() as (ws, _root):
        iid = call(ws, "start", essay="preface", kind="refine")["id"]
        call(ws, "skip", id=iid, reason="No letter for the preface.", to="construction")
        call(ws, "propose", id=iid)
        raises(lambda: call(ws, "unit", id=iid, unit="section", reason="bigger"),
               "rule the open form")
        call(ws, "act", id=iid, label="p1", action="edit", text="Why this book, plainly.")
        v = call(ws, "unit", id=iid, mode="beats", reason="build it by beats")
        w = v["walk"]
        check("after a switch, earlier rulings are shown and carried forward",
              w["mode"] == "beats" and w["earlier"][0]["label"] == "p1"
              and w["base"].strip().endswith("Why this book, plainly."), w)
        call(ws, "propose", id=iid)  # b1:plan
        check("the engine is sent the kept text, not the file",
              "Why this book, plainly." in FAKE.contexts[-1]["essay"]
              and "Why this book.\n" not in FAKE.contexts[-1]["essay"])


def test_session_ref_and_workspace() -> None:
    with world() as (ws, _root):
        iid = call(ws, "start", essay="one", kind="refine")["id"]
        v = call(ws, "recommend", id=iid)
        ref = v["instance"]["engine_state"].get("session_ref")
        check("the engine's session ref is kept on the instance with its turn",
              ref == FAKE.session_ref and ref, v["instance"]["engine_state"])
        call(ws, "recommend", id=iid)
        check("the next send resumes from it",
              FAKE.contexts[-1]["instance"]["engine_state"]["session_ref"] == ref)
        check("a real engine finds the page's workspace and book",
              FAKE.books[-1] == (ws, "MS"), FAKE.books[-1])


def test_beats_end_and_new_essay_filters() -> None:
    with world() as (ws, _root):
        iid = call(ws, "start", essay="three", kind="new")["id"]
        call(ws, "advance", id=iid)
        call(ws, "accept_letter", id=iid)
        call(ws, "advance", id=iid)
        call(ws, "propose", id=iid)
        call(ws, "act", id=iid, label="b1:plan", action="accept")
        for k in (1, 2):
            call(ws, "propose", id=iid)
            call(ws, "act", id=iid, label=f"b1:p{k}", action="accept")
        v = call(ws, "propose", id=iid)  # p3: nothing more to write
        w = v["walk"]
        check("a paragraph with nothing more to write completes the beat",
              w["next"] is None and w["beat_open"], w)
        check("beat by beat waits for the author to conclude",
              "conclude the beats" in v["gate"])
        call(ws, "beat_done", id=iid)
        v = call(ws, "conclude", id=iid)
        check("concluded: the walk is at its end", v["walk"]["end"] and v["gate"] is None,
              v["gate"])
        call(ws, "advance", id=iid)
        call(ws, "wrapup", id=iid)
        call(ws, "act", id=iid, label="w:placeholders:1", action="accept")
        call(ws, "act", id=iid, label="w:removed:1", action="reject")
        call(ws, "advance", id=iid)
        call(ws, "filters", id=iid, names=["copyedit"])
        w = call(ws, "instance", id=iid)["walk"]
        check("a new essay's filter walk has the units the beats built",
              [u["before"] for u in w["units"]]
              == ["A drafted paragraph.", "A drafted paragraph."], w["units"])


def test_lint_and_checklist_defaults() -> None:
    with world() as (ws, _root):
        first = call(ws, "start", essay="preface", kind="refine")["id"]
        call(ws, "skip", id=first, reason="Nothing to do this time.", to="done")
        nxt = call(ws, "start", essay="preface", kind="refine")["id"]
        v = call(ws, "skip", id=nxt, reason="Straight to the filters.", to="filters")
        ticks = {p["name"]: p["ticked"] for p in v["checklist"]["passes"]}
        check("a last instance that never chose filters gives the defaults",
              ticks == {"cross-references": True, "concision": True,
                        "audio-friendly": False, "copyedit": True}, ticks)
        call(ws, "filters", id=nxt, names=[])
        v = call(ws, "advance", id=nxt)
        check("lint waits for a run", v["instance"]["step"] == "lint"
              and "lint" in (v["gate"] or ""), v["gate"])
        raises(lambda: call(ws, "advance", id=nxt), "run it")
        v = call(ws, "lint", id=nxt)
        check("lint runs over the essay as it stands and is recorded",
              v["lint"] is not None and "counts" in v["lint"] and v["gate"] is None,
              v["lint"])
        check("done after lint", call(ws, "advance", id=nxt)["instance"]["step"] == "done")


def test_estimate() -> None:
    from authorlm.engines import openai_api
    with world() as (ws, _root):
        check("an unbilled engine has no estimate",
              call(ws, "estimate", engine="claude-headless", essay="one")["billed"] is False)
        saved = openai_api.price_usd
        openai_api.price_usd = lambda model, fresh, cached, out: 0.001 * (fresh + out) / 1000
        try:
            e = call(ws, "estimate", engine="openai-api", model="gpt-x", essay="one")
            check("a billed engine shows its estimated cost when chosen",
                  e["billed"] and e["turns"] > 3 and e["total"] == e["per_turn"] * e["turns"]
                  and e["total"] > 0, e)
            iid = call(ws, "start", essay="one", kind="refine")["id"]
            e = call(ws, "estimate", engine="openai-api", model="gpt-x", id=iid)
            check("an instance's estimate reads its standing context",
                  e["billed"] and e["input_per_turn"] > 0, e)
        finally:
            openai_api.price_usd = saved
        e = call(ws, "estimate", engine="openai-api", model="no-such-model-zz", essay="one")
        check("an unknown price is said, never guessed",
              e["total"] is None and "no price" in e["why"], e)


def test_background_turns() -> None:
    """The page's model turns run as jobs (workflow/jobs.py): the request
    returns at once with the job running and its progress; the instance
    is held while it runs; a failed turn is kept as an attempt with its
    error, shown until retried or dismissed; an orphan is reaped."""
    from authorlm import api
    from authorlm.db import ko_fields, now_iso
    from authorlm.workflow import jobs

    def settle(ws, iid):
        """Wait for the instance's latest job (it may end before the
        request's own view is built)."""
        last = jobs.jobs(api.open_db(ws), iid)[-1]
        jobs.wait(last["id"], 10)

    def bg(ws, method, **params):
        return workflow_page.dispatch(method, {"manuscript": "MS", **params},
                                      workspace=ws, background=True)

    with world() as (ws, _root):
        iid = call(ws, "start", essay="one", kind="refine")["id"]
        sent = len(FAKE.sent)
        FAKE.gate = threading.Event()
        try:
            v = bg(ws, "recommend", id=iid)
            job = v["job"]
            check("the request returns at once with the job running",
                  job and job["state"] == "running" and job["method"] == "recommend"
                  and not v["recommendations"]["asked"], job)
            raises(lambda: bg(ws, "list", id=iid, list="aware", essay="two",
                              change="add"), "running on this instance")
            raises(lambda: bg(ws, "recommend", id=iid), "running on this instance")
            v = call(ws, "instance", id=iid)
            check("a reload sees the running job and its progress",
                  v["job"]["state"] == "running"
                  and v["job"]["progress"].get("requests") == 1, v["job"])
        finally:
            FAKE.gate.set()
            FAKE.gate = None
        jobs.wait(job["id"], 10)
        v = call(ws, "instance", id=iid)
        check("a finished job leaves the turn recorded and no job shown",
              v["job"] is None and v["recommendations"]["asked"]
              and len(FAKE.sent) == sent + 1, v["job"])
        msg = FAKE.sent[-1]
        check("Recommend carries the book's essay summaries in the message",
              "The essays of the book, with their summaries:" in msg
              and "- preface.md" in msg and "- one.md" not in msg
              and "Read every summary" not in msg, msg[-600:])
        check("the start step shows how many summaries are stale or missing",
              v["summaries"]["total"] == 2 and set(v["summaries"]["missing"])
              == {"preface.md", "two.md"}, v["summaries"])

        FAKE.fail = "claude-headless: the turn ran past 900 s and was stopped"
        try:
            v = bg(ws, "recommend", id=iid)
            settle(ws, iid)
        finally:
            FAKE.fail = None
        v = call(ws, "instance", id=iid)
        check("a failed turn is kept with its error, nothing recorded",
              v["job"]["state"] == "failed" and "ran past 900 s" in v["job"]["error"]
              and len(FAKE.sent) == sent + 1
              and v["job"]["params"] == {}, v["job"])
        check("the failed attempt is listed on the instance",
              len(v["failed_attempts"]) == 1)
        failed_id = v["job"]["id"]
        v = bg(ws, v["job"]["method"], id=iid, **v["job"]["params"])
        settle(ws, iid)
        v = call(ws, "instance", id=iid)
        check("Retry runs it again; the retried turn is recorded",
              v["job"] is None and len(FAKE.sent) == sent + 2, v["job"])
        check("the failed attempt stays in the record",
              [j["id"] for j in v["failed_attempts"]] == [failed_id])

        FAKE.fail = "boom"
        try:
            v = bg(ws, "recommend", id=iid)
            settle(ws, iid)
        finally:
            FAKE.fail = None
        v = call(ws, "job_dismiss", id=iid, job=call(ws, "instance", id=iid)["job"]["id"])
        check("Dismiss hides a failed attempt", v["job"] is None
              and len(v["failed_attempts"]) == 2)

        db = api.open_db(ws)
        db.insert("workflow_jobs", {**ko_fields("wj"), "instance_id": iid,
                                    "method": "letter", "params": "{}",
                                    "state": "running", "error": "", "progress": "{}",
                                    "owner": "999999999:dead", "started_at": now_iso(),
                                    "finished_at": None})
        v = call(ws, "instance", id=iid)
        check("a job whose server is gone is marked interrupted",
              v["job"]["state"] == "failed" and "interrupted" in v["job"]["error"],
              v["job"])

        v = bg(ws, "list", id=iid, list="aware", essay="two", change="add")
        check("a method with no model call still answers at once, in the request",
              v["instance"]["aware"] == ["two.md"])
        from types import SimpleNamespace
        from authorlm import summaries as sums
        saved = sums.rebuild_one, sums.clients
        built: list[str] = []
        sums.clients = lambda config: {k: SimpleNamespace(enabled=True)
                                       for k in ("paragraph", "section", "essay")}
        sums.rebuild_one = lambda db, m, file, llms, echo=None: built.append(file)
        try:
            bg(ws, "summaries", id=iid)
            settle(ws, iid)
        finally:
            sums.rebuild_one, sums.clients = saved
        last = jobs.jobs(api.open_db(ws), iid)[-1]
        check("Bring summaries up to date: each stale or missing essay, as a job",
              last["method"] == "summaries" and last["state"] == "done"
              and sorted(built) == ["preface.md", "two.md"]
              and last["progress"] == {"done": 2, "total": 2, "last": ""}, last)
        check("act: a redo is a model turn; accept is not",
              workflow_page.is_background("act", {"action": "redo"})
              and not workflow_page.is_background("act", {"action": "accept"}))


def test_http_route() -> None:
    with world() as (ws, _root):
        server = ThreadingHTTPServer(("127.0.0.1", 0),
                                     audiobook_server.make_handler(ws, "MS"))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            def post(method, **params):
                req = urllib.request.Request(
                    f"http://127.0.0.1:{server.server_port}/api/workflow",
                    data=json.dumps({"method": method, "params": params}).encode(),
                    headers={"Content-Type": "application/json"})
                try:
                    with urllib.request.urlopen(req) as resp:
                        return resp.status, json.loads(resp.read())
                except urllib.error.HTTPError as err:
                    return err.code, json.loads(err.read())
            status, body = post("overview")
            check("/api/workflow answers on the audiobook server",
                  status == 200 and body["ok"]
                  and body["result"]["manuscript"] == "MS", body)
            status, body = post("skip", id="nope", reason="x")
            check("an unknown instance is a 400 with the reason",
                  status == 400 and "no workflow instance" in body["error"], body)
        finally:
            server.shutdown()
            server.server_close()


def test_phone_navigation_controls() -> None:
    """The owner's ask of 2026-11-26, on a phone screenshot of the Workflows
    view: "The UI is too busy for this pager. Use standard UI controls like
    drop-downs, selection boxes, panels, overlays, menus, etc., to make
    workflows and tasks easily navigable." Pins the controls that answer it
    and that every deep link (#<ms>/workflows:<essay>/<instance>/<step>)
    still parses the same way."""
    src = Path(__file__).resolve().parents[1] / "web" / "audiobook" / "src"
    app = (src / "App.tsx").read_text(encoding="utf-8")
    wf = (src / "Workflows.tsx").read_text(encoding="utf-8")
    css = (src / "styles.css").read_text(encoding="utf-8")
    check("the header has no row of page pills; the view's name opens a menu",
          'className="modes top-nav"' not in app
          and 'className="view-pick" aria-haspopup="menu"' in app
          and 'className="sheet" role="menu"' in app)
    modes = ["chapters", "pron", "devedit", "forms", "runs", "style", "workflows"]
    check("the menu lists every view, Merge with its count, and Audition",
          all(f'["{m}", ' in app for m in modes)
          and "MODES.map(([m, label, about])" in app
          and 'm === "forms" && formsSummary' in app and "/try-text" in app)
    check("voice and volume sit in a popover behind the speaker button",
          'aria-label="Voice and volume" aria-haspopup="dialog"' in app
          and app.index("<VolumeSlider />") > app.index('className="popover"')
          and "<WorkflowVoicePicker manuscript={ms} />" in app)
    check("the manuscript is a drop-down in the bar; the title is not a line of its own",
          'className="ms-pick"' in app and "<h1" not in app)
    check("Escape closes the menu and the popover",
          'e.key === "Escape") { setMenuOpen(false); setSoundOpen(false)' in app)
    check("the essays are one drop-down, grouped in progress, done, not started",
          'aria-label="Essay"' in wf and 'className="wf-essays"' not in wf
          and wf.index('label="In progress"') < wf.index('label="Done"')
          < wf.index('label="Not started"'))
    check("with no essay chosen, each workflow under way is one tap to continue",
          '!route.essay && inProgress.length > 0' in wf and "Continue {short(e.file)}" in wf)
    check("the instance and the step are drop-downs; unreached steps disabled",
          'aria-label="Workflow instance"' in wf and 'aria-label="Step"' in wf
          and "disabled={!reached}" in wf and 'className="devedit-tabs wf-steps"' not in wf)
    check("starting a workflow is a panel, open when the essay has none",
          '<details className="wf-start"' in wf and "open={startOpen}" in wf
          and ">Start workflow</button>" in wf)
    check("the usage line is behind a Usage panel, not always on screen",
          '<details className="wf-usage">' in wf and "<summary>Usage</summary>" in wf
          and 'className="card-head"><span>Workflows</span>' not in wf)
    check("the removed classes left the stylesheet; the new ones are styled",
          ".wf-essays" not in css and ".top-nav" not in css
          and ".sheet-item" in css and ".popover" in css and ".wf-nav" in css)
    # The route the hash carries is unchanged, so old links open the same place.
    check("deep links still parse to essay, instance and step",
          'const [essay = "", instance = "", step = ""] = (item || "").split("/");' in wf
          and 'h.startsWith("workflows:")' in app and "item: h.slice(10)" in app)


def main() -> None:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        print(t.__name__)
        t()
    print(f"{PASSED} checks passed")


if __name__ == "__main__":
    main()
