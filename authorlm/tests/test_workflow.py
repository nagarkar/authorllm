"""The per-essay editing workflow's record (authorlm/workflow/): the step
gates, carry-over between instances, engine and unit switches with their
reasons, the turns recorded from an engine, blurbs, usage totals, the
standing context's stable order, the protected register, no file
special-cased, and the CLI.

    python3 tests/test_workflow.py
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm import api  # noqa: E402
from authorlm.cli import main as cli_main  # noqa: E402
from authorlm.workflow import context, contract, core  # noqa: E402

TOC = '''[[chapter]]
file = "preface.md"

[[chapter]]
file = "one.md"
register = "protected"

[[chapter]]
file = "two.md"

[[chapter]]
file = "sermons_new.md"
edits = "owner"
'''

FILES = {
    "preface.md": "# Preface\n\nWhy this book.\n",
    "one.md": "# One\n\nThe first essay.\n\nIts second paragraph.\n",
    "two.md": "# Two\n\nThe second essay.\n",
    "sermons_new.md": "# Sermons\n\nThe owner's own.\n",
}


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def raises(fn, kind=ValueError, needle: str = "") -> str:
    try:
        fn()
    except kind as err:
        if needle and needle not in str(err):
            raise AssertionError(f"expected '{needle}' in: {err}")
        return str(err)
    raise AssertionError(f"expected {kind.__name__}")


class FakeEngine:
    """Answers every turn from a script and remembers what it was sent,
    and the session_ref it was handed; each send names a fresh ref."""

    refs = 0

    def __init__(self, name="claude-headless", model="", usage=None,
                 blurbs=None, requests=None):
        self.name, self.model = name, model
        self.sent: list[tuple[dict, str]] = []
        self.usage = usage or {"input": 1000, "cached": 800, "output": 200,
                               "dollars": 0.0, "est_subscription": 0.05}
        self.blurbs = blurbs or []
        self.requests = requests or []
        self.session_ref = None
        self.seen_refs: list = []

    def send(self, instance_context: dict, message: str) -> dict:
        self.sent.append((instance_context, message))
        state = instance_context["instance"]["engine_state"] or {}
        self.seen_refs.append(state.get("session_ref"))
        FakeEngine.refs += 1
        self.session_ref = f"{self.name}-ref-{FakeEngine.refs}"
        return {"text": f"reply to {message}", "reason": "tightened it",
                "proposed": "New paragraph.",
                "suggested_blurbs": self.blurbs, "usage": self.usage,
                "requests": self.requests}


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
        (lib / "roles").mkdir(parents=True)
        (lib / "briefs").mkdir()
        (lib / "filters").mkdir()
        (lib / "charter.md").write_text("You are the author's partner.")
        (lib / "roles" / "letter.md").write_text("Senior developmental editor.")
        (lib / "briefs" / "letter.md").write_text("Read the whole essay.")
        (lib / "filters" / "concision.md").write_text("Cut what adds nothing.")
        (lib / "filters" / "copyedit.md").write_text("Fix the mechanics.")
        (lib / "order.toml").write_text(
            'order = ["cross-references", "concision", "copyedit"]\n')
        saved = os.environ.get(context.LIBRARY_ENV)
        os.environ[context.LIBRARY_ENV] = str(lib)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                cli_main(["--workspace", str(ws), "init", "--name", "MS",
                          "--path", str(root), "--extraction", "off"])
            db = api.open_db(str(ws))
            ms = dict(db.one("SELECT * FROM manuscripts WHERE name = 'MS'"))
            yield db, ms, ws
        finally:
            if saved is None:
                os.environ.pop(context.LIBRARY_ENV, None)
            else:
                os.environ[context.LIBRARY_ENV] = saved


def _to_letter(db, inst):
    return core.advance(db, inst)


def test_start_defaults_and_no_special_files() -> None:
    with world() as (db, ms, _ws):
        sermons = core.start(db, ms, "sermons_new", "refine")
        check("no file is special-cased: sermons_new.md, marked edits = "
              "\"owner\" in the toc, starts like any essay",
              sermons["file"] == "sermons_new.md" and sermons["step"] == "start")
        new =core.start(db, ms, "three", "new", brief="a new essay")
        check("new: beat by beat, paragraph by paragraph, filters by section",
              (new["construction_mode"], new["unit"], new["filter_unit"])
              == ("beats", "paragraph", "section"), new)
        check("new: a file that does not exist yet is allowed",
              new["file"] == "three.md")
        ref = core.start(db, ms, "one", "refine")
        check("refine: the findings applied paragraph by paragraph",
              (ref["construction_mode"], ref["unit"])
              == ("apply-findings", "paragraph"), ref)
        check("the default engine is claude-headless",
              ref["engine"] == "claude-headless")
        raises(lambda: core.start(db, ms, "nowhere", "refine"), LookupError)
        raises(lambda: core.start(db, ms, "one", "refine"), ValueError,
               "already has an open instance")
        raises(lambda: core.start(db, ms, "two", "re-review"), ValueError,
               "no finished workflow instance")
        raises(lambda: core.start(db, ms, "two", "new", mode="apply-findings"),
               ValueError, "beat by beat")
        raises(lambda: core.start(db, ms, "two", "refine",
                                  engine="codex-headless"),
               ValueError, "not built yet")


def test_gates() -> None:
    with world() as (db, ms, _ws):
        inst = core.start(db, ms, "one", "refine")
        inst = _to_letter(db, inst)
        check("start → letter needs nothing", inst["step"] == "letter")
        why = raises(lambda: core.advance(db, inst), ValueError)
        check("construction waits for a letter", "no findings" in why, why)
        core.add_finding(db, inst, "f1", {"finding": "the claim drifts"})
        core.add_finding(db, inst, "f2", {"finding": "the ending repeats"})
        core.rule_finding(db, inst, "f1", "accept")
        why = raises(lambda: core.advance(db, inst), ValueError, "f2")
        check("construction waits until every finding is ruled",
              "1 finding" in why, why)
        core.rule_finding(db, inst, "f2", "defer", reason="later")
        check("the verdict is on the devedit record with its event",
              db.one("SELECT verdict FROM devedit_items WHERE item_id = 'f2' "
                     "AND run = ?", (core.letter_run(inst),))["verdict"] == "defer"
              and db.one("SELECT COUNT(*) AS n FROM edit_events WHERE "
                         "source = 'workflow'")["n"] == 2)
        inst = core.advance(db, inst)
        check("construction once every finding is ruled",
              inst["step"] == "construction")
        raises(lambda: core.advance(db, inst), ValueError, "no unit")
        core.record_turn(db, inst, "accept", unit_label="paragraph 1")
        inst = core.advance(db, inst)
        check("the wrap-up after a construction turn", inst["step"] == "wrapup")
        raises(lambda: core.set_filters(db, inst, ["concision"]), ValueError,
               "after the wrap-up")
        inst = core.advance(db, inst)
        check("the filters only after the wrap-up", inst["step"] == "filters")
        raises(lambda: core.advance(db, inst), ValueError, "filter selection")
        raises(lambda: core.set_filters(db, inst, ["nonesuch"],
                                        context.standard_order()),
               ValueError, "not in the standard order")
        inst = core.set_filters(db, inst, ["copyedit", "concision"],
                                context.standard_order())
        check("ticking only includes; the standard order decides the order",
              inst["filters"] == ["concision", "copyedit"], inst["filters"])
        raises(lambda: core.advance(db, inst), ValueError, "none of the selected")
        core.record_turn(db, inst, "accept", unit_label="section 1")
        inst = core.advance(db, inst)
        check("lint after the filters", inst["step"] == "lint")
        inst = core.advance(db, inst)
        check("done after lint, with a finish time",
              inst["step"] == "done" and inst["finished_at"])
        raises(lambda: core.advance(db, inst), ValueError, "done")


def test_letter_without_findings_and_skips() -> None:
    with world() as (db, ms, _ws):
        inst = _to_letter(db, core.start(db, ms, "two", "refine"))
        raises(lambda: core.skip(db, inst, ""), ValueError, "one sentence")
        raises(lambda: core.skip(db, inst, "It is fine. Really."), ValueError,
               "not several")
        core.record_turn(db, inst, "accept", unit_label="letter")
        check("a letter with no findings passes once the author accepts it",
              core.gate(db, inst) is None)
        inst = core.skip(db, inst, "Construction was done by hand in the Doc.",
                         to="filters")
        check("a skip moves on to the named step", inst["step"] == "filters")
        d = core.decisions(db, inst)[-1]
        check("the skip is recorded with its reason and the steps passed",
              d["kind"] == "skip"
              and d["reason"] == "Construction was done by hand in the Doc."
              and d["detail"]["skipped"] == ["letter", "construction", "wrapup"],
              d)
        raises(lambda: core.skip(db, inst, "back", to="letter"), ValueError,
               "forward")


def test_carry_over() -> None:
    with world() as (db, ms, ws):
        first = core.start(db, ms, "two", "refine", engine="openai-api",
                           model="gpt-5")
        core.change_list(db, ms, first, "reference", "sermons_new")
        first = core.get_instance(db, ms, first["id"])
        core.change_list(db, ms, first, "voice", "preface", words="the tone")
        first = core.get_instance(db, ms, first["id"])
        raises(lambda: core.change_list(db, ms, first, "aware", "two"),
               ValueError, "its own lists")
        raises(lambda: core.change_list(db, ms, first, "aware", "zzz"),
               LookupError)
        core.change_list(db, ms, first, "aware", "one")
        first = core.get_instance(db, ms, first["id"])
        first = core.set_unit(db, first, "section")
        first = core.skip(db, first, "Straight to the filters for this test.",
                          to="filters")
        first = core.set_filters(db, first, ["copyedit"])
        first = core.skip(db, first, "Closing this test instance.", to="done")
        # The book grows an essay before the next instance.
        root = Path(ms["path"])
        (root / "zero.md").write_text("# Zero\n\nNew.\n")
        (root / "toc.toml").write_text('[[chapter]]\nfile = "zero.md"\n\n' + TOC)
        second = core.start(db, ms, "two", "refine")
        check("the lists carry over",
              (second["reference"], second["voice"], second["aware"])
              == (["sermons_new.md"], ["preface.md"], ["one.md"]), second)
        check("engine and model carry over",
              (second["engine"], second["model"]) == ("openai-api", "gpt-5"))
        check("the unit carries over within the workflow",
              second["unit"] == "section")
        check("the filter selection carries over, pre-ticked, not chosen",
              second["filters"] == ["copyedit"]
              and not second["filters_confirmed"])
        check("it says where the choices came from",
              second["carried_from"] == first["id"]
              and second["carry"]["same_workflow"])
        check("the start sees the essay added since the last instance",
              core.since_last(db, second)["added"] == ["zero.md"],
              core.since_last(db, second))
        check("the instance is number 2 of the same workflow",
              second["number"] == 2 and second["workflow_id"] == first["workflow_id"])
        second = core.skip(db, second, "Done for the test.", to="done")
        third = core.start(db, ms, "two", "re-review")
        check("a re-review takes the lists from the essay's last instance of "
              "another kind, and its own kind's unit",
              third["reference"] == ["sermons_new.md"]
              and third["unit"] == "paragraph"
              and not third["carry"]["same_workflow"], third)
        check("one workflow per essay and kind",
              db.one("SELECT COUNT(*) AS n FROM workflows WHERE file = 'two.md'")["n"] == 2)


def test_switches_need_reasons_and_are_logged() -> None:
    with world() as (db, ms, _ws):
        inst = core.start(db, ms, "one", "refine")
        inst = core.set_engine(db, inst, "openai-api", "gpt-5")
        check("before any turn a switch needs no reason, and is still recorded",
              core.decisions(db, inst)[-1]["kind"] == "engine-switch")
        inst = core.advance(db, inst)
        engine = FakeEngine("openai-api", "gpt-5")
        core.send_turn(db, ms, inst, "start", "write the letter", engine=engine)
        raises(lambda: core.set_engine(db, inst, "claude-headless"), ValueError,
               "needs a reason")
        raises(lambda: core.set_unit(db, inst, "section"), ValueError,
               "needs a reason")
        raises(lambda: core.set_engine(db, inst, "openai-api", "gpt-5",
                                       reason="x"), ValueError, "already runs")
        inst = core.set_engine(db, inst, "claude-headless", "opus",
                               reason="out of API budget this month")
        d = core.decisions(db, inst)[-1]
        check("the engine switch records from, to, step and the reason",
              d["detail"]["from"] == {"engine": "openai-api", "model": "gpt-5"}
              and d["detail"]["to"] == {"engine": "claude-headless",
                                        "model": "opus"}
              and d["detail"]["step"] == "letter"
              and d["reason"] == "out of API budget this month", d)
        check("the engine's own state is cleared, so it rebuilds from the record",
              inst["engine_state"] == {})
        core.send_turn(db, ms, inst, "reply", "go on",
                       engine=FakeEngine("claude-headless", "opus"))
        ts = core.turns(db, inst)
        check("each turn records the engine and model that produced it",
              [(t["engine"], t["model"]) for t in ts]
              == [("openai-api", "gpt-5"), ("claude-headless", "opus")], ts)
        inst = core.set_unit(db, inst, "section", reason="paragraphs too slow")
        check("a unit switch changes the current phase's unit",
              inst["unit"] == "section"
              and core.decisions(db, inst)[-1]["kind"] == "unit-switch")
        inst = core.set_unit(db, inst, "paragraph", phase="filters",
                             reason="this essay needs close work")
        check("--for filters changes the filter unit",
              inst["filter_unit"] == "paragraph")
        inst = core.set_unit(db, inst, mode="beats", reason="the letter says rebuild")
        check("a refine may switch to beat by beat",
              inst["construction_mode"] == "beats")
        d = core.decide(db, inst, "principle", "never open with a question")
        check("a principle is a decision, not a ruling", d["kind"] == "principle")
        raises(lambda: core.decide(db, inst, "ruling", ""), ValueError)
        check("no decision is called a ruling unless marked so",
              not [x for x in core.decisions(db, inst) if x["kind"] == "ruling"])


def test_turns_context_and_blurbs() -> None:
    with world() as (db, ms, _ws):
        inst = core.start(db, ms, "two", "refine", brief="tighten it")
        core.change_list(db, ms, inst, "voice", "one")
        inst = core.get_instance(db, ms, inst["id"])
        core.change_list(db, ms, inst, "voice", "preface")
        inst = core.get_instance(db, ms, inst["id"])
        core.change_list(db, ms, inst, "reference", "sermons_new")
        inst = core.get_instance(db, ms, inst["id"])
        check("a list keeps reading order, not the order of adding",
              inst["voice"] == ["preface.md", "one.md"], inst["voice"])
        inst = core.advance(db, inst)
        engine = FakeEngine(blurbs=["too wordy"],
                            requests=[{"name": "essay_text",
                                       "args": {"essay": "one.md"},
                                       "chars": 40}])
        t1 = core.send_turn(db, ms, inst, "start", "write the letter",
                            engine=engine, unit_label="letter")
        ctx, msg = engine.sent[0]
        names = [b["name"] for b in ctx["standing"]]
        check("the standing blocks come in the fixed order",
              tuple(names) == context.BLOCK_ORDER, names)
        blocks = {b["name"]: b for b in ctx["standing"]}
        check("the charter, the step's role and brief come from the library",
              blocks["charter"]["text"] == "You are the author's partner."
              and blocks["role"]["text"] == "Senior developmental editor."
              and blocks["brief"]["text"] == "Read the whole essay.")
        check("the author's brief is a block",
              blocks["author-brief"]["text"] == "tighten it")
        check("other essays go whole, labelled, in reading order",
              blocks["essays"]["text"].index("=== preface.md [voice] ===")
              < blocks["essays"]["text"].index("=== one.md [voice] ===")
              and "The first essay." in blocks["essays"]["text"])
        check("reference may hold the Sermons (read, never edited)",
              "=== sermons_new.md [reference] ===\n\n# Sermons\n\nThe owner's own."
              in blocks["essays"]["text"])
        check("no filter text outside the filter step",
              blocks["filters"]["text"] == "")
        check("the essay as it stands follows the standing blocks",
              ctx["essay"] == FILES["two.md"] and ctx["turns"] == [])
        again = context.standing_context(db, ms, inst)
        check("the same instance gives the same standing bytes (cacheable)",
              again == ctx["standing"])
        check("the turn records reply, reason, proposal, usage and requests",
              t1["reason"] == "tightened it" and t1["proposed"] == "New paragraph."
              and t1["usage"]["cached"] == 800
              and t1["requests"][0]["name"] == "essay_text", t1)
        check("suggested blurbs join the manuscript's list at once",
              [b["text"] for b in core.blurb_list(db, ms["id"])] == ["too wordy"])
        raises(lambda: core.send_turn(db, ms, inst, "redo", "", engine=engine),
               ValueError, "blurb")
        core.send_turn(db, ms, inst, "redo", "", author_text="",
                       blurbs=["bad grammar"], engine=engine)
        check("a ticked blurb is added and moves to the top",
              [b["text"] for b in core.blurb_list(db, ms["id"])][0] == "bad grammar")
        core.hide_blurb(db, ms["id"], "too wordy")
        check("a hidden blurb leaves the list but is kept",
              [b["text"] for b in core.blurb_list(db, ms["id"])] == ["bad grammar"]
              and len(core.blurb_list(db, ms["id"], hidden=True)) == 2)
        raises(lambda: core.send_turn(db, ms, inst, "accept", "ok",
                                      engine=engine), ValueError, "record_turn")
        check("a plain edit needs no model; a bracketed one does",
              not core.needs_model("edit", "My own words.")
              and core.needs_model("edit", "My words [improve this]."))
        core.record_turn(db, inst, "edit", author_text="My own words.")
        core.send_turn(db, ms, inst, "edit", "My words [improve this].",
                       engine=engine)
        ctx3, _ = engine.sent[-1]
        check("the engine sees the turns so far, the edit included",
              [t["action"] for t in ctx3["turns"]] == ["start", "redo", "edit"])
        # At the filter step the chosen filters' text goes in once.
        inst = core.get_instance(db, ms, inst["id"])
        inst = core.skip(db, inst, "Test goes to the filters.", to="filters")
        inst = core.set_filters(db, inst, ["concision", "nofile"])
        blocks = {b["name"]: b for b in context.standing_context(db, ms, inst)}
        check("the filter step sends each chosen filter's text once, by name",
              "## concision\n\nCut what adds nothing." in blocks["filters"]["text"]
              and blocks["filters"]["missing_filters"] == ["nofile"])
        raises(lambda: contract.normalize_reply("nope"), ValueError)
        raises(lambda: contract.normalize_reply({"usage": {"input": "x"}}),
               ValueError)
        # The engines package registers the real engines (engines/__init__.py);
        # a name nobody registered still raises.
        raises(lambda: contract.make_engine("no-such-engine", "m"), LookupError)


def test_each_essay_sent_once_with_its_roles() -> None:
    """Owner, 2026-11-26: "if an essay is in more than one list, reference
    and voice, we should be sending the essay and then saying whether this
    essay should apply to reference or reference and voice, or just
    voice." Seen live on SMSTTD (wi-7abca47358e0): recapitulation.md went
    whole as reference and again whole as voice; preface.md whole as voice
    and again as an aware summary."""
    with world() as (db, ms, _ws):
        from authorlm.db import ko_fields

        db.insert("essay_summaries", {
            **ko_fields("es"), "manuscript_id": ms["id"], "file": "preface.md",
            "summary": "PREFACE SUMMARY", "source_hash": "x",
            "upstream_hash": "y", "status": "current"})
        inst = core.start(db, ms, "two", "refine")
        for which, name in (("reference", "one"), ("voice", "one"),
                            ("voice", "preface"), ("aware", "preface"),
                            ("aware", "sermons_new")):
            core.change_list(db, ms, inst, which, name)
            inst = core.get_instance(db, ms, inst["id"])
        blocks = {b["name"]: b for b in context.standing_context(db, ms, inst)}
        text = blocks["essays"]["text"]
        check("an essay on reference and voice is sent whole once, labelled both",
              text.count("The first essay.") == 1
              and "=== one.md [reference and voice] ===" in text, text)
        check("a voice essay also on aware is labelled so, sent once",
              "=== preface.md [voice, also aware] ===" in text
              and text.count("Why this book.") == 1, text)
        check("the block opens with the legend the labels are read by",
              text.startswith(context.ESSAY_LABEL_LEGEND))
        aware = blocks["aware"]
        check("no aware summary for an essay already sent whole",
              "PREFACE SUMMARY" not in aware["text"]
              and "preface.md" not in aware["no_summary"]
              and aware["sent_whole"] == ["preface.md"], aware)
        check("an aware-only essay stays on the aware list (no summary on record)",
              aware["no_summary"] == ["sermons_new.md"], aware)
        check("the essays block names each essay's roles",
              blocks["essays"]["roles"] == {
                  "preface.md": ["voice", "aware"],
                  "one.md": ["reference", "voice"]}, blocks["essays"]["roles"])
        empty = core.start(db, ms, "one", "refine")
        blocks = {b["name"]: b for b in context.standing_context(db, ms, empty)}
        check("no lists: an empty essays block in its place",
              blocks["essays"]["text"] == ""
              and tuple(blocks) == context.BLOCK_ORDER)


def test_protected_register_is_honoured() -> None:
    with world() as (db, ms, _ws):
        mine = core.start(db, ms, "one", "refine")
        blocks = {b["name"]: b for b in context.standing_context(db, ms, mine)}
        check("an essay whose toc entry says register = \"protected\" gets "
              "the strong instruction (fallback when the library file is "
              "missing)",
              blocks["protected-register"]["text"]
              == context.PROTECTED_REGISTER_FALLBACK)
        lib = Path(os.environ[context.LIBRARY_ENV])
        (lib / "protected-register.md").write_text("Keep the register.")
        blocks = {b["name"]: b for b in context.standing_context(db, ms, mine)}
        check("the library's protected-register text when it is there",
              blocks["protected-register"]["text"] == "Keep the register.")
        other = core.start(db, ms, "two", "refine")
        names = [b["name"] for b in context.standing_context(db, ms, other)]
        blocks = {b["name"]: b for b in context.standing_context(db, ms, other)}
        check("an essay without the flag gets an empty block in the same place",
              blocks["protected-register"]["text"] == ""
              and tuple(names) == context.BLOCK_ORDER)
        root = Path(ms["path"])
        (root / "toc.toml").write_text(TOC.replace(
            'file = "one.md"\nregister = "protected"', 'file = "one.md"'))
        blocks = {b["name"]: b for b in context.standing_context(db, ms, mine)}
        check("the flag alone decides, never the file name",
              blocks["protected-register"]["text"] == "")


def test_usage_totals() -> None:
    with world() as (db, ms, _ws):
        a = core.advance(db, core.start(db, ms, "one", "refine"))
        b = core.advance(db, core.start(db, ms, "two", "refine",
                                        engine="openai-api", model="gpt-5"))
        core.send_turn(db, ms, a, "start", "go", engine=FakeEngine())
        core.send_turn(db, ms, a, "reply", "more", engine=FakeEngine())
        core.send_turn(db, ms, b, "start", "go", engine=FakeEngine(
            usage={"input": 500, "cached": 0, "output": 100, "dollars": 0.25,
                   "est_subscription": 0}))
        core.record_turn(db, a, "accept")
        u = core.usage(db, ms)
        check("the total adds every model turn and leaves out local ones",
              u["total"]["turns"] == 3 and u["total"]["input"] == 2500
              and u["total"]["cached"] == 1600 and u["total"]["output"] == 500,
              u["total"])
        check("billed dollars and the subscription estimate are kept apart",
              abs(u["total"]["dollars"] - 0.25) < 1e-9
              and abs(u["total"]["est_subscription"] - 0.10) < 1e-9)
        check("per essay",
              u["by_essay"]["one.md"]["turns"] == 2
              and u["by_essay"]["two.md"]["dollars"] == 0.25)
        check("per instance and per engine",
              u["by_instance"][a["id"]]["turns"] == 2
              and u["by_engine"]["openai-api"]["turns"] == 1)
        month = next(iter(u["by_month"]))
        check("per month", u["by_month"][month]["turns"] == 3)
        check("filtered to one essay",
              core.usage(db, ms, essay="two")["total"]["turns"] == 1)
        check("filtered to a month with nothing",
              core.usage(db, ms, month="1999-01")["total"] == {})
        raises(lambda: core.usage(db, ms, month="May"), ValueError)


def test_session_ref_is_persisted() -> None:
    with world() as (db, ms, _ws):
        inst = core.advance(db, core.start(db, ms, "one", "refine"))
        first = FakeEngine()
        core.send_turn(db, ms, inst, "start", "go", engine=first)
        check("the first send of an instance has no session to resume",
              first.seen_refs == [None], first.seen_refs)
        stored = core.get_instance(db, ms, inst["id"])["engine_state"]
        check("the engine's session_ref is written to engine_state",
              first.session_ref and stored.get("session_ref")
              == first.session_ref, stored)
        # A new engine object (a new process), handed the instance as it was
        # before the first send: the ref still comes from the record.
        second = FakeEngine()
        core.send_turn(db, ms, inst, "reply", "more", engine=second)
        check("the second send receives the first send's session_ref",
              second.seen_refs == [first.session_ref], second.seen_refs)
        inst = core.get_instance(db, ms, inst["id"])
        check("each send replaces the stored ref with the engine's latest",
              inst["engine_state"].get("session_ref") == second.session_ref
              != first.session_ref)

        class Broken(FakeEngine):
            def send(self, instance_context, message):
                raise RuntimeError("engine down")
        raises(lambda: core.send_turn(db, ms, inst, "reply", "x",
                                      engine=Broken()), RuntimeError)
        check("a failed send leaves the stored ref and the turns as they were",
              core.get_instance(db, ms, inst["id"])["engine_state"]
              == inst["engine_state"] and len(core.turns(db, inst)) == 2)
        inst = core.set_engine(db, inst, "openai-api", "gpt-5", reason="budget")
        check("an engine switch clears the stored ref",
              "session_ref" not in inst["engine_state"], inst["engine_state"])
        third = FakeEngine("openai-api", "gpt-5")
        core.send_turn(db, ms, inst, "reply", "again", engine=third)
        check("the new engine starts without the old engine's ref",
              third.seen_refs == [None], third.seen_refs)


def test_unknown_cost_is_not_zero() -> None:
    check("normalize_reply keeps an unknown dollar cost as None",
          contract.normalize_reply({"usage": {"input": 5, "dollars": None}})
          ["usage"]["dollars"] is None)
    check("a missing dollar cost is unknown too",
          contract.normalize_reply({"usage": {"input": 5}})["usage"]["dollars"]
          is None)
    check("a known zero stays zero",
          contract.normalize_reply({"usage": {"dollars": 0.0}})["usage"]["dollars"]
          == 0.0)
    with world() as (db, ms, _ws):
        a = core.advance(db, core.start(db, ms, "one", "refine"))
        unknown = {"input": 10, "cached": 0, "output": 5, "dollars": None,
                   "est_subscription": 0}
        t = core.send_turn(db, ms, a, "start", "go",
                           engine=FakeEngine(usage=unknown))
        check("the turn records the cost as unknown", t["usage"]["dollars"] is None,
              t["usage"])
        u = core.usage(db, ms)
        check("with no known cost, the billed total is unknown, not $0",
              u["total"]["dollars"] is None
              and u["total"]["dollars_unknown"] == 1, u["total"])
        core.send_turn(db, ms, a, "reply", "more", engine=FakeEngine(
            usage={**unknown, "dollars": 0.5}))
        u = core.usage(db, ms)
        check("known costs add up and unknown ones are counted apart",
              u["total"]["dollars"] == 0.5 and u["total"]["dollars_unknown"] == 1
              and u["total"]["turns"] == 2, u["total"])
        out = _cli(_ws, "usage")
        check("the usage lines say how many turns have an unknown cost",
              "1 turn unknown" in out, out)


def _tables(db) -> dict:
    names = [r["name"] for r in db.all(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT "
        "LIKE 'sqlite_%'")]
    return {n: [tuple(r) for r in db.all(f"SELECT * FROM {n}")] for n in names}


def _cli(ws, *argv) -> str:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        cli_main(["--workspace", str(ws), "workflow", *argv])
    return out.getvalue()


def _author_brief(ctx: dict) -> str:
    return next(b["text"] for b in ctx["standing"] if b["name"] == "author-brief")


def test_brief_is_editable_at_any_step() -> None:
    """Owner, 2026-11-26, at the start step of wi-7abca47358e0: "if I have
    read the recap for sermon 3 and I've read sermon 3 and I have some
    thoughts about what we should take into account, should I be able to
    edit the brief and add things there?" The brief changes at any step of
    an open instance; the next turn's standing context carries the new
    brief and its session rebuilds; earlier turns stay as they were; the
    change is recorded with both texts; a done instance keeps its brief."""
    from authorlm.engines.base import SessionRef, Turn

    with world() as (db, ms, _ws):
        inst = core.start(db, ms, "one", "refine", brief="Keep the argument.")
        new = "Keep the argument.\n\nAlso: the recap says the third move is weak."
        inst = core.set_brief(db, inst, new)
        check("the start step's brief changes", inst["brief"] == new)
        inst = core.advance(db, inst)
        engine = FakeEngine()
        core.send_turn(db, ms, inst, "start", "write the letter", engine=engine)
        ctx1 = engine.sent[-1][0]
        check("a turn sends the brief as the author-brief block",
              _author_brief(ctx1) == new)
        turns_before = [dict(t) for t in core.turns(db, inst)]
        later = new + "\n\nAnd mind the recap's last paragraph."
        inst = core.set_brief(db, inst, "  " + later + "\n")
        check("a later step's brief changes; the text is trimmed",
              inst["brief"] == later and inst["step"] == "letter", inst["brief"])
        core.send_turn(db, ms, inst, "reply", "go on", engine=engine)
        ctx2 = engine.sent[-1][0]
        check("the next turn reads the new brief", _author_brief(ctx2) == later)
        ref = SessionRef.after("claude-headless", "h", Turn.of(ctx1))
        check("the session made under the old brief is not resumed: it rebuilds",
              ref.resumable("claude-headless",
                            Turn.of({**ctx2, "standing": ctx1["standing"]}))
              and not ref.resumable("claude-headless", Turn.of(ctx2)))
        check("earlier turns stay as they were",
              [dict(t) for t in core.turns(db, inst)][:len(turns_before)]
              == turns_before)
        changes = [d for d in core.decisions(db, inst) if d["kind"] == "brief-change"]
        check("each change is recorded with the old text, the new and the step",
              [(d["detail"]["from"], d["detail"]["to"], d["detail"]["step"])
               for d in changes] == [("Keep the argument.", new, "start"),
                                     (new, later, "letter")], changes)
        raises(lambda: core.set_brief(db, inst, later + "  "), ValueError,
               "unchanged")
        inst = core.skip(db, inst, "Testing the brief.", "done")
        raises(lambda: core.set_brief(db, inst, "too late"), ValueError,
               "done")
        check("a done instance keeps its brief",
              core.get_instance(db, ms, inst["id"])["brief"] == later)


def test_brief_cli() -> None:
    with world() as (db, ms, ws):
        iid = json.loads(_cli(ws, "start", "one", "--kind", "refine",
                              "--brief", "sharpen", "--json"))["id"]
        before = _tables(db)
        out = _cli(ws, "brief", iid)
        check("brief alone shows it", "brief: sharpen" in out, out)
        out = _cli(ws, "brief", iid, "--set", "sharpen, and cut", "--dry-run")
        check("--dry-run says what would change", "would change the brief" in out, out)
        check("brief alone and --dry-run write nothing", _tables(db) == before)
        board = json.loads(_cli(ws, "brief", iid, "--json"))
        check("--json carries next", board["brief"] == "sharpen" and board["next"])
        _cli(ws, "brief", iid, "--set", "sharpen, and cut")
        check("--set replaces it",
              core.get_instance(db, ms, iid)["brief"] == "sharpen, and cut")
        path = Path(ws) / "brief.md"
        path.write_text("Para one.\n\nPara two.\n", encoding="utf-8")
        out = _cli(ws, "brief", iid, "--file", str(path))
        check("--file reads several paragraphs",
              core.get_instance(db, ms, iid)["brief"] == "Para one.\n\nPara two.", out)
        out = _cli(ws, "show", iid)
        check("show lists each brief change without the texts",
              out.count("brief-change at start") == 2 and "Para two" in out, out)
        with contextlib.redirect_stderr(io.StringIO()):
            try:
                _cli(ws, "brief", iid, "--set", "Para one.\n\nPara two.")
                raise AssertionError("expected exit")
            except SystemExit as e:
                check("an unchanged brief is an error, exit non-zero",
                      "error: the brief is unchanged" in str(e.code), e.code)


def test_cli_end_to_end() -> None:
    with world() as (db, ms, ws):
        out = _cli(ws, "start", "sermons_new", "--kind", "refine")
        check("the CLI starts a workflow on sermons_new.md like any essay",
              "sermons_new.md" in out and "step: start" in out, out)
        data =json.loads(_cli(ws, "start", "one", "--kind", "refine",
                               "--brief", "sharpen", "--json"))
        iid = data["id"]
        _cli(ws, "lists", "add", iid, "voice", "preface")
        _cli(ws, "advance", iid)
        inst = core.get_instance(db, ms, iid)
        core.send_turn(db, ms, inst, "start", "letter", engine=FakeEngine())
        before = _tables(db)
        out = _cli(ws, "show", iid)
        check("show names the step, the lists and what the next step waits for",
              "step: letter" in out and "voice: preface.md" in out
              and "waits:" in out, out)
        _cli(ws, "status")
        _cli(ws, "status", "one")
        _cli(ws, "list")
        _cli(ws, "turns", iid)
        _cli(ws, "usage")
        _cli(ws, "usage", "--essay", "one", "--json")
        _cli(ws, "filters", iid)
        _cli(ws, "blurbs")
        check("the read-only verbs write nothing", _tables(db) == before)
        with contextlib.redirect_stderr(io.StringIO()):
            try:
                _cli(ws, "set-engine", iid, "openai-api")
                raise AssertionError("expected exit")
            except SystemExit as e:
                check("a mid-instance engine switch without a reason is "
                      "declined", "reason" in str(e.code), e.code)
        _cli(ws, "set-engine", iid, "openai-api", "--model", "gpt-5",
             "--reason", "a different flavour")
        _cli(ws, "skip", iid, "--reason", "The letter was settled in chat.")
        _cli(ws, "set-unit", iid, "section", "--reason", "whole sections")
        _cli(ws, "skip", iid, "--to", "filters", "--reason", "Testing the CLI.")
        _cli(ws, "filters", iid, "--set", "copyedit,concision")
        _cli(ws, "decide", iid, "--kind", "principle", "--words", "plain words")
        _cli(ws, "blurbs", "--add", "too long")
        st = {r["file"]: r for r in json.loads(_cli(ws, "status", "--json"))}
        check("status reports each essay's step and the gate",
              st["one.md"]["step"] == "filters" and st["one.md"]["waits_for"]
              and st["sermons_new.md"]["step"] == "start", st)
        inst = core.get_instance(db, ms, "one")
        check("an essay name finds its latest instance; filters in order",
              inst["id"] == iid and inst["filters"] == ["concision", "copyedit"]
              and inst["engine"] == "openai-api" and inst["unit"] == "section",
              inst)
        kinds = [d["kind"] for d in core.decisions(db, inst)]
        check("every change is in the record",
              kinds == ["list-change", "engine-switch", "skip", "unit-switch",
                        "skip", "principle"], kinds)


def test_a_rename_carries_the_workflow() -> None:
    from authorlm import docs

    with world() as (db, ms, _ws):
        inst = core.start(db, ms, "two", "refine")
        core.change_list(db, ms, inst, "voice", "one")
        other = core.start(db, ms, "preface", "refine")
        core.change_list(db, ms, other, "aware", "one")
        with contextlib.redirect_stdout(io.StringIO()):
            docs.carry_rename(db, ms, "one.md", "first.md", move_file=True)
        inst = core.get_instance(db, ms, inst["id"])
        other = core.get_instance(db, ms, other["id"])
        check("the lists follow the renamed essay",
              inst["voice"] == ["first.md"] and other["aware"] == ["first.md"],
              (inst["voice"], other["aware"]))
        check("so does the reading order recorded at the start",
              "first.md" in inst["toc_order"] and "one.md" not in inst["toc_order"])
        mine = core.start(db, ms, "first", "refine")
        core.skip(db, mine, "Closing it.", to="done")
        with contextlib.redirect_stdout(io.StringIO()):
            docs.carry_rename(db, ms, "first.md", "uno.md", move_file=True)
        check("an essay's own workflow and instances follow its rename",
              core.get_instance(db, ms, mine["id"])["file"] == "uno.md"
              and db.one("SELECT file FROM workflows WHERE id = ?",
                         (mine["workflow_id"],))["file"] == "uno.md")


def test_migration_is_additive() -> None:
    with world() as (db, _ms, ws):
        tables = {r["name"] for r in db.all(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
        check("the five tables exist",
              {"workflows", "workflow_instances", "session_turns",
               "workflow_decisions", "blurbs"} <= tables)
        again = api.open_db(str(ws))
        check("opening again changes nothing",
              {r["name"] for r in again.all(
                  "SELECT name FROM sqlite_master WHERE type = 'table'")} == tables)


def main() -> None:
    tests = [test_start_defaults_and_no_special_files,
             test_gates,
             test_letter_without_findings_and_skips,
             test_carry_over,
             test_switches_need_reasons_and_are_logged,
             test_turns_context_and_blurbs,
             test_each_essay_sent_once_with_its_roles,
             test_protected_register_is_honoured,
             test_usage_totals,
             test_session_ref_is_persisted,
             test_unknown_cost_is_not_zero,
             test_brief_is_editable_at_any_step,
             test_brief_cli,
             test_cli_end_to_end,
             test_a_rename_carries_the_workflow,
             test_migration_is_additive]
    for fn in tests:
        print(fn.__name__)
        fn()
    print(f"OK — {len(tests)} workflow cases")


if __name__ == "__main__":
    main()
