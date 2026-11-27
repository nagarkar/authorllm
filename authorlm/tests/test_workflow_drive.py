"""Driving a workflow's walks from the command line (workflow/drive.py):
`authorlm workflow walk | propose | job | unit | reopen | beat-done |
conclude | wrapup | principle`.

Source: the owner, 2026-11-26, on driving construction from chat: "I want
this to be an option for regularly working on this, so add the command
line verbs as well." The checks pin: the CLI and the Workflows page write
identical records for the same session (turns, decisions, blurbs, the
messages the engine is sent), over construction, a beat walk, the wrap-up
and its principle; `walk` shows the waiting proposal's original and
proposed text exactly; a model turn runs as the page's job, `--wait`
blocking until it ends, and without it the verb returns with the job
running; a failed turn exits 1 and stays on the record for `job --retry`
or `--dismiss`; every write verb's `--dry-run` writes nothing and sends
nothing; the help carries an example per verb and the skill names them.

Hermetic: the page tests' temp workspace, library and fake engine.
The real detached child process (`_spawn`) runs only on a turn the page
refuses before the engine is asked; elsewhere an in-process thread takes
its place so the fake engine answers. Untested: a real child completing a
model turn (it would call the real engine).
Run: python3 tests/test_workflow_drive.py
"""
from __future__ import annotations

import contextlib
import io
import json
import re
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_workflow_page as wp  # noqa: E402  (env, fake engine, world)
from test_workflow_page import FAKE, call, check, world  # noqa: E402

from authorlm import api, workflow_page  # noqa: E402
from authorlm.cli import main as cli_main  # noqa: E402
from authorlm.workflow import drive, jobs  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


def cli(ws: str, *argv: str) -> str:
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        cli_main(["--workspace", ws, "-m", "MS", "workflow", *argv])
    return out.getvalue()


def cli_exit(ws: str, *argv: str) -> str:
    try:
        cli(ws, *argv)
    except SystemExit as err:
        if err.code in (0, None):
            raise AssertionError("expected exit 1")
        return str(err.code)
    raise AssertionError("expected an exit")


def page(ws: str, method: str, **params):
    """What the page's server does: dispatch in the background, then the
    page polls until the job ends."""
    before = {j["id"] for j in jobs.jobs(api.open_db(ws), params["id"])}
    out = workflow_page.dispatch(method, {"manuscript": "MS", **params},
                                 workspace=ws, background=True)
    new = [j for j in jobs.jobs(api.open_db(ws), params["id"])
           if j["id"] not in before]
    for j in new:
        jobs.wait(j["id"], 10)
    return out


def records(ws: str, iid: str) -> dict:
    """The instance's record, without ids and times."""
    db = api.open_db(ws)
    skip = {"id", "created_at", "updated_at", "instance_id", "manuscript_id",
            "last_used", "at"}

    def rows(sql, args=()):
        return [{k: v for k, v in dict(r).items() if k not in skip}
                for r in db.all(sql, args)]
    return {"turns": rows("SELECT * FROM session_turns WHERE instance_id = ? "
                          "ORDER BY seq", (iid,)),
            "decisions": [{k: v for k, v in r.items() if k != "detail"}
                          for r in rows("SELECT * FROM workflow_decisions "
                                        "WHERE instance_id = ? ORDER BY "
                                        "created_at", (iid,))],
            "blurbs": rows("SELECT text, hidden, last_used IS NOT NULL AS used "
                           "FROM blurbs ORDER BY text"),
            "jobs": [(j["method"], j["params"], j["state"])
                     for j in jobs.jobs(db, iid)],
            "instance": {k: v for k, v in dict(db.one(
                "SELECT step, construction_mode, unit, base_text "
                "FROM workflow_instances WHERE id = ?", (iid,))).items()}}


def _construction_session(ws: str, by_cli: bool) -> tuple[str, list[str]]:
    """One construction walk, the wrap-up and its principle: by the page's
    calls or by the CLI verbs. Returns (instance, the messages sent)."""
    sent = len(FAKE.sent)
    iid = wp._to_construction(ws)
    if by_cli:
        cli(ws, "propose", iid, "--wait")
        cli(ws, "unit", iid, "p1", "--accept")
        cli(ws, "propose", iid, "--wait")
        cli(ws, "unit", iid, "p2", "--redo", "--blurb", "too wordy",
            "--note", "say it in half", "--wait")
        cli(ws, "unit", iid, "p2", "--edit", "The second, [give an example] here.",
            "--wait")
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as fh:
            fh.write("My own second paragraph.")
        cli(ws, "unit", iid, "p2", "--edit-file", fh.name)
        Path(fh.name).unlink()
        cli(ws, "propose", iid, "--wait")
        cli(ws, "reopen", iid, "p1", "--wait")
        cli(ws, "unit", iid, "p1", "--reject")
        cli(ws, "advance", iid)
        cli(ws, "wrapup", iid, "--wait")
        cli(ws, "unit", iid, "w:removed:1", "--reject")
        cli(ws, "unit", iid, "w:placeholders:1", "--accept")
        cli(ws, "principle", iid, "--words", "no rhetorical questions",
            "--scope", "this essay")
    else:
        page(ws, "propose", id=iid)
        page(ws, "act", id=iid, label="p1", action="accept")
        page(ws, "propose", id=iid)
        page(ws, "act", id=iid, label="p2", action="redo", blurbs=["too wordy"],
             free="say it in half")
        page(ws, "act", id=iid, label="p2", action="edit",
             text="The second, [give an example] here.")
        page(ws, "act", id=iid, label="p2", action="edit",
             text="My own second paragraph.")
        page(ws, "propose", id=iid)
        page(ws, "reopen", id=iid, label="p1")
        page(ws, "act", id=iid, label="p1", action="reject")
        page(ws, "advance", id=iid)
        page(ws, "wrapup", id=iid)
        page(ws, "act", id=iid, label="w:removed:1", action="reject")
        page(ws, "act", id=iid, label="w:placeholders:1", action="accept")
        page(ws, "principle", id=iid, words="no rhetorical questions",
             scope="this essay")
    return iid, FAKE.sent[sent:]


def test_cli_and_page_write_the_same_records() -> None:
    with world() as (ws1, _r1):
        i1, sent1 = _construction_session(ws1, by_cli=False)
        rec1 = records(ws1, i1)
    with world() as (ws2, _r2):
        i2, sent2 = _construction_session(ws2, by_cli=True)
        rec2 = records(ws2, i2)
    check("the session reached the wrap-up with its items ruled",
          rec1["instance"]["step"] == "wrapup"
          and [t["unit_label"] for t in rec1["turns"]][-2:]
          == ["w:removed:1", "w:placeholders:1"], rec1["turns"][-2:])
    for key in ("turns", "decisions", "blurbs", "jobs", "instance"):
        check(f"CLI and page write the same {key}", rec1[key] == rec2[key],
              (rec1[key], rec2[key]))
    check("the engine is sent the same messages", sent1 == sent2)
    check("the redo carries the blurb and the author's words",
          any("too wordy" in m and "say it in half" in m for m in sent2))


def test_beats_from_the_cli() -> None:
    def session(ws, by_cli):
        iid = call(ws, "start", essay="three", kind="new", brief="a new essay")["id"]
        call(ws, "advance", id=iid)
        call(ws, "accept_letter", id=iid)
        call(ws, "advance", id=iid)
        if by_cli:
            out = cli(ws, "propose", iid, "--wait")
            cli(ws, "unit", iid, "b1:plan", "--accept")
            cli(ws, "propose", iid, "--wait")
            cli(ws, "unit", iid, "b1:p1", "--accept")
            cli(ws, "beat-done", iid)
            cli(ws, "propose", iid, "--wait")
            cli(ws, "unit", iid, "b2:plan", "--accept")
            end = cli(ws, "conclude", iid)
            return iid, out, end
        for m, p in (("propose", {}), ("act", {"label": "b1:plan", "action": "accept"}),
                     ("propose", {}), ("act", {"label": "b1:p1", "action": "accept"}),
                     ("beat_done", {}), ("propose", {}),
                     ("act", {"label": "b2:plan", "action": "accept"}),
                     ("conclude", {})):
            page(ws, m, id=iid, **p)
        return iid, "", ""

    with world() as (ws1, _r):
        i1, _o, _e = session(ws1, False)
        rec1 = records(ws1, i1)
    with world() as (ws2, _r):
        i2, out, end = session(ws2, True)
        rec2 = records(ws2, i2)
    check("beat by beat: the CLI and the page write the same turns",
          rec1["turns"] == rec2["turns"] and [t["unit_label"] for t in rec1["turns"]][-1] == "beats:end",
          (rec1["turns"], rec2["turns"]))
    check("the plan's form shows its proposed text and a new beat's empty "
          "original",
          "--- proposed (b1:plan) ---\nSection 1 states the problem.\n--- end ---"
          in out and "--- original (b1:plan) ---\n(empty)\n--- end ---" in out, out)
    check("concluded, the walk says to advance",
          "concluded" in end and f"next: authorlm workflow advance {i2}" in end, end)


def test_walk_shows_the_proposal_exactly() -> None:
    with world() as (ws, root):
        long = ("The first paragraph, which is long enough that any clip "
                "of it at a hundred and sixty characters would cut it off "
                "before its end, and its end matters: here it is.")
        (root / "one.md").write_text(wp.ONE.replace("The first paragraph.", long))
        iid = wp._to_construction(ws)
        before = records(ws, iid)
        out = cli(ws, "walk", iid)
        check("walk is read-only", records(ws, iid) == before)
        check("before any turn: the next unit with its original, and propose next",
              "next: p1" in out and f"--- original (p1) ---\n{long}\n--- end ---" in out
              and f"next: authorlm workflow propose {iid}" in out, out)
        cli(ws, "propose", iid, "--wait")
        out = cli(ws, "walk", iid)
        check("the waiting form: reason, original whole, proposed whole",
              "p1  pending" in out and "reason: tightened it (Finding F1)" in out
              and f"--- original (p1) ---\n{long}\n--- end ---" in out
              and "--- proposed (p1) ---\nA tightened paragraph.\n--- end ---" in out,
              out)
        check("the next command names the verdicts",
              f"authorlm workflow unit {iid} p1 --accept | --reject" in out, out)
        board = json.loads(cli(ws, "walk", iid, "--json"))
        check("--json: the focused unit, the page's walk, next",
              board["focus"]["label"] == "p1"
              and board["focus"]["form"]["proposed"] == "A tightened paragraph."
              and board["walk"]["units"][0]["before"] == long
              and board["next"].startswith("authorlm workflow unit"), board)
        cli(ws, "unit", iid, "p1", "--accept")
        out = cli(ws, "walk", iid, "--label", "p1")
        check("--label shows a ruled unit with its kept text",
              "p1  accepted" in out
              and "--- kept (p1, accepted) ---\nA tightened paragraph.\n--- end ---"
              in out, out)
        check("an unknown label exits 1 naming the units",
              "no unit 'p9'" in cli_exit(ws, "walk", iid, "--label", "p9"))


def test_dry_run_writes_nothing() -> None:
    with world() as (ws, _root):
        iid = wp._to_construction(ws)
        sent = len(FAKE.sent)
        before = records(ws, iid)
        out = cli(ws, "propose", iid, "--dry-run")
        check("propose --dry-run names the unit and its original",
              "would ask the engine to propose p1" in out
              and "--- original (p1) ---\nThe first paragraph.\n--- end ---" in out
              and "one model turn on claude-headless" in out
              and "nothing sent, nothing written" in out, out)
        check("an accept with no form waiting is the page's error, in a dry run",
              "no proposal waiting to accept" in
              cli_exit(ws, "unit", iid, "p1", "--accept", "--dry-run"))
        cli(ws, "propose", iid, "--wait")
        sent = len(FAKE.sent)
        before = records(ws, iid)
        for argv in (("unit", iid, "p1", "--accept", "--dry-run"),
                     ("unit", iid, "p1", "--reject", "--dry-run"),
                     ("unit", iid, "p1", "--redo", "--note", "shorter", "--dry-run"),
                     ("unit", iid, "p1", "--edit", "Mine [and an example].",
                      "--dry-run", "--json"),
                     ("propose", iid, "--label", "p2", "--dry-run")):
            out = cli(ws, *argv)
        board = json.loads(cli(ws, "unit", iid, "p1", "--edit", "Mine [x].",
                               "--dry-run", "--json"))
        check("--json dry run: the page method and whether it is a model turn",
              board["dry_run"] and board["method"] == "act"
              and board["model_turn"] and board["next"], board)
        check("no dry run sent or wrote anything",
              records(ws, iid) == before and len(FAKE.sent) == sent)
        check("a redo needs a blurb or the author's words",
              "a redo takes a blurb" in cli_exit(ws, "unit", iid, "p1", "--redo",
                                                 "--dry-run"))
        check("--note goes with --redo only",
              "go with --redo" in cli_exit(ws, "unit", iid, "p1", "--accept",
                                           "--note", "x"))


def test_jobs_from_the_cli() -> None:
    """A model turn is the page's job: without --wait the verb returns
    while it runs; `job` shows it; a failure exits 1 and is retried or
    dismissed from the CLI as from the page."""
    threads: list[threading.Thread] = []

    class Handle:
        errfile = "/nonexistent"

        def poll(self):
            return None

    def spawn(t, method, params):
        payload = json.dumps({"workspace": t.workspace, "manuscript": t.manuscript,
                              "id": t.iid, "method": method, "params": params})
        th = threading.Thread(target=drive.child_main, args=(payload,), daemon=True)
        th.start()
        threads.append(th)
        return Handle()

    saved = drive._spawn, drive.QUICK_END
    drive._spawn, drive.QUICK_END = spawn, 0.3
    try:
        with world() as (ws, _root):
            iid = wp._to_construction(ws)
            FAKE.gate = threading.Event()
            try:
                out = cli(ws, "propose", iid)
                check("without --wait the verb returns with the job running",
                      "started propose as job wj" in out
                      and f"next: authorlm workflow job {iid} --wait" in out, out)
                out = cli(ws, "job", iid)
                check("job shows the running turn", "propose running" in out, out)
                check("another turn waits for it (the page's rule)",
                      "running on this instance" in cli_exit(ws, "propose", iid))
            finally:
                FAKE.gate.set()
                FAKE.gate = None
            out = cli(ws, "job", iid, "--wait")
            for th in threads:
                th.join(10)
            check("job --wait ends with the form waiting",
                  "p1  pending" in out and "A tightened paragraph." in out, out)

            cli(ws, "unit", iid, "p1", "--accept")
            FAKE.fail = "claude-headless: the turn ran past 900 s and was stopped"
            try:
                err = cli_exit(ws, "propose", iid, "--wait")
            finally:
                FAKE.fail = None
            check("a failed turn exits 1 with its error and the retry command",
                  "ran past 900 s" in err and f"job {iid} --retry" in err, err)
            out = cli(ws, "job", iid)
            check("job shows the failed attempt", "propose failed" in out
                  and f"next: authorlm workflow job {iid} --retry" in out, out)
            before = records(ws, iid)
            out = cli(ws, "job", iid, "--retry", "--dry-run")
            check("job --retry --dry-run writes nothing",
                  "would retry the failed propose job" in out and "propose p2" in out and records(ws, iid) == before, out)
            out = cli(ws, "job", iid, "--retry", "--wait")
            check("job --retry runs the failed turn again",
                  "p2  pending" in out, out)
            view = call(ws, "instance", id=iid)
            check("the failed attempt stays on the record, no job shown",
                  view["job"] is None and len(view["failed_attempts"]) == 1)
            FAKE.fail = "boom"
            try:
                cli_exit(ws, "unit", iid, "p2", "--redo", "--blurb", "wordy", "--wait")
            finally:
                FAKE.fail = None
            failed = call(ws, "instance", id=iid)["job"]
            check("the failed redo keeps its params for Retry",
                  failed["params"] == {"label": "p2", "action": "redo",
                                       "blurbs": ["wordy"], "free": ""}, failed)
            cli(ws, "job", iid, "--dismiss")
            check("job --dismiss hides it (the page's job_dismiss)",
                  call(ws, "instance", id=iid)["job"] is None)
            check("nothing to retry exits 1",
                  "no failed job" in cli_exit(ws, "job", iid, "--retry"))
    finally:
        drive._spawn, drive.QUICK_END = saved


def test_detached_child_process() -> None:
    """The real detached child (`python -m authorlm.workflow.drive`) owns
    the job, on a turn the page refuses before any engine is asked (the
    walk is at its end), so no model is called: the job is on the record
    as the child's, failed with the page's error, and the verb exits 1."""
    with world() as (ws, _root):
        iid = wp._to_construction(ws)
        for label in ("p1", "p2"):
            call(ws, "propose", id=iid)
            call(ws, "act", id=iid, label=label, action="accept")
        call(ws, "propose", id=iid)  # p3 passes: the walk is at its end
        sent = len(FAKE.sent)
        saved = drive.QUICK_END
        drive.QUICK_END = 20.0
        try:
            err = cli_exit(ws, "propose", iid)
        finally:
            drive.QUICK_END = saved
        job = jobs.jobs(api.open_db(ws), iid)[-1]
        check("the child's job failed with the page's error, nothing sent",
              job["method"] == "propose" and job["state"] == "failed"
              and "reached the end" in job["error"] and len(FAKE.sent) == sent, job)
        check("the verb exits 1 naming the retry", "reached the end" in err
              and f"job {iid} --retry" in err, err)


def test_reopen_proposed_by_a_revised_letter() -> None:
    """A revised letter proposes reopening an accepted unit it quotes
    (workflow_page.reopen_proposals): `walk` shows it, and `reopen
    --leave` is the page's "Leave as accepted" (`reopen_decline`)."""
    from authorlm.workflow import contract
    import test_workflow_revision as rev  # registers its engine
    try:
        with world() as (ws, _root):
            iid = rev._construction_with_p1(ws)
            call(ws, "revise_letter", id=iid, note="the claim, again")
            call(ws, "finding", id=iid, item="F1", verdict="accept")
            call(ws, "finding", id=iid, item="F2", verdict="reject")
            out = cli(ws, "walk", iid)
            check("walk shows the proposed reopen and names both answers",
                  "reopen proposed: p1" in out
                  and f"next: authorlm workflow reopen {iid} p1 [--leave]" in out, out)
            before = records(ws, iid)
            out = cli(ws, "reopen", iid, "p1", "--leave", "--dry-run")
            check("reopen --leave --dry-run writes nothing",
                  "would leave p1 as accepted" in out and records(ws, iid) == before,
                  out)
            out = cli(ws, "reopen", iid, "p1", "--leave")
            check("reopen --leave records the page's decision; the proposal ends",
                  "reopen proposed" not in out and any(
                      d["kind"] == "reopen-declined"
                      for d in records(ws, iid)["decisions"]), out)
            check("a second --leave exits 1",
                  "no reopen proposed" in cli_exit(ws, "reopen", iid, "p1", "--leave"))
    finally:
        contract.register_engine("claude-headless", lambda model: FAKE)


def test_help_completion_and_skill() -> None:
    from authorlm.completion import generate_bash_completion
    words = next(line for line in generate_bash_completion().splitlines()
                 if "set-engine" in line and "compgen -W" in line)
    script = words.split('"')[1].split()
    skill = (ROOT / ".claude" / "skills" / "authorlm" / "SKILL.md").read_text(
        encoding="utf-8")
    for verb in drive.VERBS:
        text = io.StringIO()
        with contextlib.redirect_stdout(text), contextlib.suppress(SystemExit):
            cli_main(["workflow", verb, "--help"])
        check(f"workflow {verb} has an example",
              f"example: authorlm workflow {verb}" in text.getvalue())
        check(f"completion offers workflow {verb}", verb in script)
        check(f"the skill names workflow {verb}",
              re.search(rf"workflow\s+{re.escape(verb)}\s", skill) is not None)
    check("the skill has the chat recipe",
          "Driving construction from chat" in skill)


def main() -> None:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        print(t.__name__)
        t()
    print(f"{wp.PASSED} checks passed")


if __name__ == "__main__":
    main()
