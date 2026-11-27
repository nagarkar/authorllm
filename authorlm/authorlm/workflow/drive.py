"""Driving a workflow instance's walks from the command line: `authorlm
workflow walk | propose | job | unit | reopen | beat-done | conclude |
wrapup | principle | discuss`.

The construction walk, the wrap-up and the filter walk are worked one unit
at a time through a form (original, the engine's reason, proposed text;
accept, reject, redo with blurbs and the author's words, edit). The
Workflows page drives them; these verbs drive the same walks from chat,
where an assistant shows each proposal verbatim and records the author's
own verdicts.

The CLI and the page write identical records: every write here goes
through the page's transport, `workflow_page.dispatch`, with the methods
and params the page sends (`propose`, `act`, `reopen`, `reopen_decline`, `beat_done`,
`conclude`, `wrapup`, `principle`, `job_dismiss`, and a failed job's own
method and params for Retry). Nothing here records a turn, a decision or a
job itself.

A model turn (propose, reopen, wrapup, a redo, an edit with a [bracket])
runs as the page's background job (`workflow/jobs.py`): one at a time per
instance, its progress and its failure on the record, Retry and Dismiss.
`--wait` runs the job in this process and blocks, printing progress on
stderr, until it ends. Without `--wait` a detached child process
(`python -m authorlm.workflow.drive`) owns the job, so it outlives the
command; the verb returns once the job is on the record, with its id.

Texts are printed whole and unchanged (the owner reads the proposal
itself, not a clip of it), between `--- … ---` marker lines.

`discuss` talks with the engine about one unit (or, with the label
`letter`, about the letter), as the page's reply box under the unit does
(`discuss`, `letter_reply`): the reply is printed verbatim, the unit's
proposal does not change, and `unit <label> --redo --with-discussion`
hands the thread to the redo. `walk --label` shows a unit's thread.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from . import core, jobs

# How long a verb without --wait waits for its detached job to appear on
# the record, and then for a quick end (a turn refused before the engine is
# asked ends in milliseconds).
APPEAR_TIMEOUT = 60.0
QUICK_END = 2.0
POLL = 0.5


def _page():
    from .. import workflow_page
    return workflow_page


def _api():
    from .. import api
    return api


# ------------------------------------------------------------ the board

def _block(title: str, text: str) -> list[str]:
    """A text printed exactly, between marker lines."""
    return [f"--- {title} ---", text if text else "(empty)", "--- end ---"]


def _job_lines(job: dict | None, iid: str) -> list[str]:
    if not job:
        return []
    prog = job.get("progress") or {}
    bits = []
    if prog.get("requests"):
        bits.append(f"{prog['requests']} read request(s)")
    if prog.get("last"):
        bits.append(f"last: {prog['last']}")
    if prog.get("total"):
        bits.append(f"{prog.get('done', 0)}/{prog['total']} done")
    lines = [f"  job {job['id']}: {job['method']} {job['state']}, "
             f"{job['elapsed']:.0f} s" + (f" ({'; '.join(bits)})" if bits else "")]
    if job["params"]:
        lines.append("    params: " + json.dumps(job["params"], ensure_ascii=False))
    if job["state"] == "failed":
        lines.append(f"    error: {job['error']}")
    return lines


def _focus(view: dict, label: str | None) -> dict | None:
    """The unit the board shows in full: the one named, else the form
    waiting for the author, else the walk's next unit."""
    step = view["instance"]["step"]
    if step == "wrapup":
        items = (view.get("wrapup") or {}).get("items") or []
    else:
        items = (view.get("walk") or {}).get("units") or []
    if label:
        hit = next((u for u in items if u["label"] == label), None)
        if hit is None:
            raise LookupError(f"no unit '{label}' at {step} (units: "
                              + (", ".join(u["label"] for u in items) or "none")
                              + ")")
        return hit
    pending = next((u for u in items if u["state"] == "pending"), None)
    if pending or step == "wrapup":
        return pending or next((u for u in items if u["state"] == "open"), None)
    nxt = (view.get("walk") or {}).get("next")
    return next((u for u in items if u["label"] == nxt), None)


def _unit_lines(u: dict) -> list[str]:
    form = u.get("form") or {}
    head = f"{u['label']}  {u['state']}"
    if u.get("kind"):
        head += f"  ({u['kind']}" + (f", beat {u['beat']}" if u.get("beat") else "") + ")"
    lines = [head]
    if form.get("reason"):
        lines.append(f"reason: {form['reason']}")
    if form.get("engine"):
        lines.append(f"by: {form['engine']}" + (f" ({form['model']})" if form.get("model") else ""))
    lines += _block(f"original ({u['label']})", u.get("before", ""))
    if u["state"] == "pending" or (form.get("proposed") and u["state"] not in
                                   ("accepted", "edited")):
        lines += _block(f"proposed ({u['label']})", form.get("proposed", ""))
    elif u["state"] in ("accepted", "edited"):
        lines += _block(f"kept ({u['label']}, {u['state']})", u.get("text", ""))
    if form.get("text") and not form.get("proposed"):
        lines += _block(f"the engine's note ({u['label']})", form["text"])
    for d in u.get("thread") or []:
        lines.append(f"discussion #{d['seq']}:")
        lines += _block(f"the author ({u['label']})", d["author"])
        lines += _block(f"the engine ({u['label']})", d["reply"])
    return lines


def next_hint(view: dict) -> str:
    """The command that moves the walk on from where it stands."""
    inst = view["instance"]
    iid, step = inst["id"], inst["step"]
    job = view.get("job")
    if job and job["state"] == "running":
        return f"authorlm workflow job {iid} --wait"
    if job and job["state"] == "failed":
        return f"authorlm workflow job {iid} --retry | --dismiss"
    if step == "wrapup":
        wu = view.get("wrapup") or {}
        if not wu.get("drafted"):
            return f"authorlm workflow wrapup {iid}"
        open_ = next((i for i in wu["items"] if i["state"] in ("pending", "open")), None)
        if open_:
            return (f"authorlm workflow unit {iid} {open_['label']} --accept | "
                    "--reject | --edit TEXT")
        return f"authorlm workflow advance {iid}"
    if step not in ("construction", "filters"):
        return f"authorlm workflow show {iid}"
    w = view.get("walk") or {}
    pending = next((u for u in w.get("units", []) if u["state"] == "pending"), None)
    if pending:
        return (f"authorlm workflow unit {iid} {pending['label']} --accept | "
                "--reject | --redo --blurb B --note TEXT | --edit-file PATH")
    if w.get("reopen"):
        label = w["reopen"][0]["label"]
        return (f"authorlm workflow reopen {iid} {label} [--leave]")
    if w.get("next"):
        return f"authorlm workflow propose {iid}"
    if w.get("mode") == "beats" and not w.get("concluded"):
        return (f"authorlm workflow beat-done {iid} | authorlm workflow "
                f"conclude {iid}")
    return f"authorlm workflow advance {iid}"


def walk_lines(view: dict, label: str | None = None) -> list[str]:
    inst = view["instance"]
    step = inst["step"]
    lines = [f"{inst['id']}  {inst['file']}  {inst['kind']} #{inst['number']}  "
             f"step: {step}  engine: {inst['engine']}"
             + (f" ({inst['model']})" if inst["model"] else "")]
    lines += _job_lines(view.get("job"), inst["id"])
    if step in ("construction", "filters") and view.get("walk"):
        w = view["walk"]
        if w["mode"] == "beats":
            lines.append(f"  walk: beat by beat, by {w['unit']}; beat {w['beat']}"
                         + (" open" if w.get("beat_open") else "")
                         + f"; the essay had {w.get('beats_before', 0)} section(s)"
                         + ("; concluded" if w.get("concluded") else ""))
        else:
            lines.append(f"  walk: by {w['unit']}"
                         + (f" (asked {w['asked_unit']}; the essay has no "
                            "subheadings)" if w.get("fallback") else "")
                         + f"; {len(w['units'])} unit(s)"
                         + ("; at the end" if w.get("end") else
                            f"; next: {w['next']}" if w.get("next") else ""))
        if step == "filters":
            lines.append(f"  filters: {', '.join(inst['filters']) or '(none chosen)'}")
        if w.get("earlier"):
            lines.append(f"  earlier stretches (before a unit switch): "
                         f"{len(w['earlier'])} ruled unit(s)")
        if w["units"]:
            lines.append("  units: " + ", ".join(f"{u['label']} {u['state']}"
                                                 for u in w["units"]))
        for r in w.get("reopen") or []:
            lines.append(f"  reopen proposed: {r['label']} — the revised "
                         f"letter's {r['finding']} ({r['title']}) quotes "
                         f"\"{r['quote']}\" (reopen {inst['id']} {r['label']}, "
                         "or --leave it as accepted)")
    elif step == "wrapup":
        wu = view.get("wrapup") or {}
        if not wu.get("drafted"):
            lines.append("  wrap-up: not drafted yet")
        else:
            lines.append("  wrap-up items: " + (", ".join(
                f"{i['label']} {i['state']}" for i in wu["items"]) or "(none)"))
            for p in wu.get("principles") or []:
                lines.append(f"  principle offered: {p.get('words', '')}"
                             + (f" (scope: {p['scope']})" if p.get("scope") else ""))
            if wu.get("text"):
                lines += _block("the wrap-up's reply (not JSON)", wu["text"])
    else:
        lines.append(f"  no walk at {step}: units are worked at construction, "
                     "the wrap-up and the filters")
    if step in ("construction", "filters", "wrapup"):
        u = _focus(view, label)
        if u is not None:
            lines.append("")
            lines += _unit_lines(u)
    if view.get("gate") and step != "done":
        lines.append(f"  gate: {view['gate']}")
    lines.append(f"  next: {next_hint(view)}")
    return lines


def walk_board(view: dict, label: str | None = None) -> dict:
    """The `--json` board of a walk: the page's own walk or wrap-up view,
    the focused unit, the job, the gate and the next command."""
    step = view["instance"]["step"]
    return {"instance": view["instance"]["id"], "file": view["instance"]["file"],
            "step": step, "walk": view.get("walk"), "wrapup": view.get("wrapup"),
            "focus": _focus(view, label) if step in ("construction", "filters",
                                                     "wrapup") else None,
            "job": view.get("job"), "gate": view.get("gate"),
            "next": next_hint(view)}


# ------------------------------------------------------------- the calls

class Target:
    """Where a verb acts: the workspace, the manuscript's name, the
    instance id."""

    def __init__(self, workspace: str | None, manuscript: str, iid: str):
        self.workspace, self.manuscript, self.iid = workspace, manuscript, iid

    def call(self, method: str, **params: Any) -> Any:
        """The page's own call; a model turn starts as its job."""
        return _page().dispatch(method, {"manuscript": self.manuscript,
                                         "id": self.iid, **params},
                                workspace=self.workspace, background=True)

    def view(self) -> dict:
        return _page().dispatch("instance", {"manuscript": self.manuscript,
                                             "id": self.iid},
                                workspace=self.workspace)

    def db(self):
        return _api().open_db(self.workspace)


def _say(text: str) -> None:
    print(text, file=sys.stderr, flush=True)


def wait_job(t: Target, job_id: str, quiet: bool = False,
             timeout: float | None = None) -> dict:
    """Block until the job ends (or `timeout` passes), printing its
    progress on stderr as it changes; the job's last view."""
    start = time.monotonic()
    seen = None
    while True:
        jobs.wait(job_id, POLL)  # a job of this process; else a plain sleep
        job = next((j for j in jobs.jobs(t.db(), t.iid) if j["id"] == job_id), None)
        if job is None:
            raise LookupError(f"no job '{job_id}' on {t.iid}")
        if job["state"] != "running":
            return job
        prog = json.dumps(job["progress"], sort_keys=True)
        if not quiet and prog != seen:
            seen = prog
            _say(f"  {job['method']} running, {job['elapsed']:.0f} s"
                 + (f": {prog}" if job["progress"] else ""))
        if timeout is not None and time.monotonic() - start >= timeout:
            return job
        if job_id not in jobs._LIVE:
            time.sleep(POLL)


def _new_job(t: Target, before: set[str]) -> dict | None:
    return next((j for j in reversed(jobs.jobs(t.db(), t.iid))
                 if j["id"] not in before), None)


def _spawn(t: Target, method: str, params: dict) -> Any:
    """Start the detached child that owns the job (`child_main`). Returns
    a handle with poll() and an `errfile` holding the child's stderr."""
    fd, errfile = tempfile.mkstemp(prefix="authorlm-workflow-job-", suffix=".log")
    payload = json.dumps({"workspace": t.workspace, "manuscript": t.manuscript,
                          "id": t.iid, "method": method, "params": params})
    proc = subprocess.Popen([sys.executable, "-m", "authorlm.workflow.drive", payload],
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=fd, start_new_session=True,
                            cwd=str(Path(__file__).resolve().parents[2]))
    os.close(fd)
    proc.errfile = errfile  # type: ignore[attr-defined]
    return proc


def child_main(payload: str) -> int:
    """The detached child: the page's call, then wait for its job."""
    p = json.loads(payload)
    t = Target(p["workspace"], p["manuscript"], p["id"])
    before = {j["id"] for j in jobs.jobs(t.db(), t.iid)}
    t.call(p["method"], **p["params"])
    job = _new_job(t, before)
    if job is not None:
        wait_job(t, job["id"], quiet=True)
    return 0


def run_turn(t: Target, method: str, params: dict, wait: bool) -> tuple[dict | None, Any]:
    """Run one page method. A model turn runs as its job: with `wait`, in
    this process until it ends; otherwise in a detached child, returning
    once the job is on the record (and has had a moment to fail fast).
    Returns (the job or None, the page's reply)."""
    page = _page()
    if not page.is_background(method, params):
        return None, t.call(method, **params)
    before = {j["id"] for j in jobs.jobs(t.db(), t.iid)}
    if wait:
        out = t.call(method, **params)
        job = _new_job(t, before)
        return (wait_job(t, job["id"]) if job else None), out
    run = jobs.running(t.db(), t.iid)
    if run:
        raise ValueError(jobs.busy_message(run))
    proc = _spawn(t, method, params)
    end = time.monotonic() + APPEAR_TIMEOUT
    job = None
    while job is None:
        job = _new_job(t, before)
        if job is not None:
            break
        if proc.poll() is not None or time.monotonic() > end:
            err = ""
            try:
                err = Path(proc.errfile).read_text(encoding="utf-8").strip()
            except OSError:
                pass
            last = err.splitlines()[-1] if err else "the job never started"
            raise ValueError(f"the {method} turn did not start: {last}")
        time.sleep(0.1)
    return wait_job(t, job["id"], quiet=True, timeout=QUICK_END), None


# ------------------------------------------------------------- the verbs

VERBS = ("walk", "propose", "job", "unit", "reopen", "beat-done", "conclude",
         "wrapup", "principle", "discuss")
LETTER = "letter"


def _emit(args, data: dict, lines: list[str]) -> None:
    if getattr(args, "json", False):
        print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
    else:
        print("\n".join(lines))


def _engine_line(db, manuscript: dict, inst: dict) -> str:
    est = _page().estimate(db, manuscript, inst["engine"], inst["model"] or "",
                           inst=inst)
    who = inst["engine"] + (f" ({inst['model']})" if inst["model"] else "")
    if not est.get("billed"):
        return f"one model turn on {who} (not billed per call)"
    cost = (f"about ${est['per_turn']:.3f}" if est.get("per_turn") is not None
            else f"cost unknown: {est.get('why')}")
    return f"one model turn on {who}, billed: {cost}"


def _preview(db, manuscript: dict, inst: dict, method: str,
             params: dict) -> tuple[list[str], dict]:
    """What a write would do, from the same read-only lookups the page's
    methods use (`_check_label_step`, `_walk`, `_unit`); writes nothing
    and calls no model. Raises the error the page would raise for a unit
    that cannot take the action."""
    page = _page()
    label = params.get("label")
    lines: list[str] = []
    info: dict[str, Any] = {"method": method, "params": params,
                            "model_turn": page.is_background(method, params)}
    if method == "propose":
        step = inst["step"]
        page._check_label_step(inst, step)
        if step == "wrapup":
            raise ValueError("the wrap-up's items come from the wrap-up itself")
        if step == "filters" and not inst["filters_confirmed"]:
            raise ValueError("choose the filters first (the checklist)")
        walk = page._walk(db, manuscript, inst, step)
        label = label or walk["next"]
        if label is None:
            raise ValueError("the walk has reached the end of the essay; "
                             "advance when you are ready")
        unit, _w = page._unit(db, manuscript, inst, step, label)
        if unit["state"] != "open":
            raise ValueError(f"{label} is {unit['state']}")
        lines.append(f"would ask the engine to propose {label}")
        lines += _block(f"original ({label})", unit["before"])
    elif method in ("act", "reopen"):
        step = page._step_for(inst, label)
        page._check_label_step(inst, step)
        unit, _w = page._unit(db, manuscript, inst, step, label)
        state = unit["state"]
        action = params.get("action", "reopen")
        info["state"] = state
        if action == "accept":
            proposed = (unit.get("form") or {}).get("proposed", "")
            if state != "pending" or not proposed:
                raise ValueError(f"{label} has no proposal waiting to accept "
                                 f"(it is {state})")
            lines.append(f"would record accept: {label} becomes the proposed text")
            lines += _block(f"proposed ({label})", proposed)
        elif action == "reject":
            if state != "pending" and not (step == "wrapup" and state == "open"):
                raise ValueError(f"{label} has no proposal waiting to reject "
                                 f"(it is {state})")
            lines.append(f"would record reject: {label} keeps its original"
                         if unit.get("before") else
                         f"would record reject: {label} is dropped")
        elif action == "reopen":
            if state not in page.RESOLVED:
                raise ValueError(f"{label} is not ruled yet; nothing to reopen")
            lines.append(f"would reopen {label} ({state}): if it has landed, "
                         "the essay file and its Doc tab get its original "
                         "back first; then the engine proposes it again")
        else:
            if state in page.RESOLVED and state != "passed":
                raise ValueError(f"{label} is {state}; reopen it first")
            if action == "redo":
                talk = (page._since_form(db, inst, step, label)
                        if params.get("with_discussion") else [])
                if params.get("with_discussion") and not talk:
                    raise ValueError(f"{label} has no discussion since its "
                                     "last proposal to redo with")
                if not params.get("blurbs") and not params.get("free", "").strip() \
                        and not talk:
                    raise ValueError("a redo takes a blurb or a few words of "
                                     "what is wrong (--blurb, --note)")
                lines.append(f"would ask for a redo of {label}: "
                             + page._redo_message(label, params.get("blurbs") or [],
                                                  params.get("free", ""), talk))
            else:
                lines.append(f"would record the author's edit of {label}"
                             + (" and ask the engine to work in its [brackets]"
                                if info["model_turn"] else " (no model call)"))
                lines += _block(f"edit ({label})", params.get("text", ""))
    elif method in ("beat_done", "conclude"):
        walk = page._walk(db, manuscript, inst, "construction")
        if inst["step"] != "construction" or walk["mode"] != "beats":
            raise ValueError("beat by beat only, at construction")
        if any(u["state"] == "pending" for u in walk["units"]):
            raise ValueError("rule the open form first")
        lines.append(f"would mark beat {walk['beat']} done" if method == "beat_done"
                     else "would conclude the beats: construction's walk ends")
    elif method == "wrapup":
        if inst["step"] != "wrapup":
            raise ValueError(f"the instance is at {inst['step']}, not wrapup")
        lines.append("would ask the engine for the wrap-up")
    elif method == "principle":
        lines.append(f"would record the principle: {params.get('words', '')}"
                     + (f" (scope: {params['scope']})" if params.get("scope") else ""))
    elif method == "reopen_decline":
        walk = (page._walk(db, manuscript, inst, "construction")
                if inst["step"] == "construction" else {"units": []})
        if not any(p["label"] == label
                   for p in page.reopen_proposals(db, inst, walk)):
            raise LookupError(f"no reopen proposed for '{label}'")
        lines.append(f"would leave {label} as accepted: the revised letter "
                     "does not propose reopening it again")
    elif method == "discuss":
        step = page._step_for(inst, label)
        page._check_label_step(inst, step)
        page._unit(db, manuscript, inst, step, label)
        lines.append(f"would ask the engine about {label} (its proposal "
                     "stays as it is):")
        lines += _block("the author", params.get("text", ""))
    elif method == "letter_reply":
        if inst["step"] not in core.LETTER_STEPS:
            raise ValueError("the letter is discussed from the letter step to "
                             f"the filters; the instance is at {inst['step']}")
        lines.append("would ask the engine about the letter:")
        lines += _block("the author", params.get("text", ""))
    elif method == "job_dismiss":
        lines.append(f"would dismiss the failed job {params.get('job')}")
    else:  # a retry: the failed job's own method
        lines.append(f"would retry {method} "
                     + json.dumps(params, ensure_ascii=False))
    if info["model_turn"]:
        lines.append(_engine_line(db, manuscript, inst))
    lines.append("dry run: nothing sent, nothing written")
    return lines, info


def _read_text(path: str) -> str:
    return sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")


def _unit_params(args) -> dict:
    label = args.label
    if (args.blurb or args.note) and not args.redo:
        raise ValueError("--blurb and --note go with --redo")
    if args.with_discussion and not args.redo:
        raise ValueError("--with-discussion goes with --redo")
    if args.accept:
        return {"label": label, "action": "accept"}
    if args.reject:
        return {"label": label, "action": "reject"}
    if args.redo:
        out = {"label": label, "action": "redo", "blurbs": list(args.blurb or []),
               "free": args.note or ""}
        if args.with_discussion:
            out["with_discussion"] = True
        return out
    text = args.edit if args.edit is not None else _read_text(args.edit_file)
    return {"label": label, "action": "edit", "text": text}


def run_verb(db, manuscript: dict, args) -> None:
    """One of VERBS; errors are ValueError or LookupError for the CLI."""
    inst = core.get_instance(db, manuscript, args.instance)
    t = Target(getattr(args, "workspace", None), manuscript["name"], inst["id"])
    verb = args.action
    if verb == "walk":
        view = t.view()
        _emit(args, walk_board(view, args.label), walk_lines(view, args.label))
        return
    if verb == "job":
        view = t.view()
        job = view.get("job")
        if not (args.retry or args.dismiss or args.wait):
            lines = _job_lines(job, inst["id"]) or [
                f"  no job running and no failed attempt on {inst['id']}"]
            failed = view.get("failed_attempts") or []
            if failed:
                lines.append(f"  failed attempts on the record: {len(failed)}")
            _emit(args, {"instance": inst["id"], "job": job,
                         "failed_attempts": failed, "next": next_hint(view)},
                  [f"{inst['id']}  {inst['file']}  step: {inst['step']}"] + lines
                  + [f"  next: {next_hint(view)}"])
            return
        if args.wait and not (args.retry or args.dismiss):
            if job and job["state"] == "running":
                job = wait_job(t, job["id"])
            _after(args, t, job)
            return
        if not job or job["state"] != "failed":
            raise ValueError(f"no failed job on {inst['id']} to "
                             f"{'retry' if args.retry else 'dismiss'}"
                             + (f" (a {job['method']} job is running)" if job else ""))
        method, params = ((job["method"], dict(job["params"])) if args.retry
                          else ("job_dismiss", {"job": job["id"]}))
    else:
        method, params = {
            "propose": lambda: ("propose", {"label": args.label}
                                if args.label else {}),
            "unit": lambda: ("act", _unit_params(args)),
            "reopen": lambda: ("reopen_decline" if args.leave else "reopen",
                               {"label": args.label}),
            "beat-done": lambda: ("beat_done", {}),
            "conclude": lambda: ("conclude", {}),
            "wrapup": lambda: ("wrapup", {}),
            "principle": lambda: ("principle", {"words": args.words,
                                                "scope": args.scope or ""}),
            "discuss": lambda: (("letter_reply", {"text": args.text})
                                if args.label == LETTER else
                                ("discuss", {"label": args.label,
                                             "text": args.text})),
        }[verb]()
        if verb == "discuss" and not (args.text or "").strip():
            raise ValueError("--text is the author's message")
    if args.dry_run:
        lines, info = _preview(db, manuscript, inst, method, params)
        if verb == "job" and args.retry:
            lines.insert(0, f"would retry the failed {method} job {job['id']}:")
        _emit(args, {"instance": inst["id"], "dry_run": True, **info,
                     "next": next_hint(t.view())},
              [f"{inst['id']}  {inst['file']}  step: {inst['step']}"] + lines)
        return
    job, _out = run_turn(t, method, params, wait=getattr(args, "wait", False))
    if verb == "discuss":
        _after_discuss(args, t, job)
        return
    _after(args, t, job)


def _after_discuss(args, t: Target, job: dict | None) -> None:
    """The engine's reply, verbatim, once the turn has ended; else the job
    to wait for. A failed turn exits 1 naming Retry."""
    view = t.view()
    iid, label = t.iid, args.label
    if label == LETTER:
        thread = [{"seq": d["seq"], "author": d["author"], "reply": d["reply"]}
                  for d in view["letter"]["discussion"]]
    else:
        unit = _focus(view, label)
        thread = (unit or {}).get("thread") or []
    last = thread[-1] if thread else None
    data = {"instance": iid, "label": label, "job": job,
            "reply": last if job and job["state"] == "done" else None,
            "next": next_hint(view)}
    if job and job["state"] == "running":
        lines = [f"{iid}  {label}: started {job['method']} as job {job['id']}; "
                 f"it runs on after this command (authorlm workflow job {iid} "
                 f"--wait, then authorlm workflow walk {iid} --label {label})"]
    elif job and job["state"] == "done" and last:
        lines = [f"{iid}  {label}: the engine's reply (#{last['seq']}); the "
                 "proposal is unchanged"]
        lines += _block("the engine", last["reply"])
        if label != LETTER:
            lines.append(f"  redo with it: authorlm workflow unit {iid} {label} "
                         "--redo --with-discussion")
    else:
        lines = _job_lines(job, iid) or [f"{iid}  {label}: no turn ran"]
    _emit(args, data, lines)
    if job is not None and job["state"] == "failed":
        sys.exit(f"error: the {job['method']} turn failed: {job['error']}. "
                 f"authorlm workflow job {iid} --retry")


def _after(args, t: Target, job: dict | None) -> None:
    """The walk as it now stands; a failed turn exits 1 naming Retry."""
    view = t.view()
    label = getattr(args, "label", None) if args.action in ("unit", "reopen") else None
    board = walk_board(view, label)
    lines = walk_lines(view, label)
    if job is not None and job["state"] == "running":
        lines.insert(1, f"  started {job['method']} as job {job['id']}; it runs "
                        f"on after this command (authorlm workflow job "
                        f"{t.iid} --wait)")
    board["started_job"] = job
    _emit(args, board, lines)
    if job is not None and job["state"] == "failed":
        sys.exit(f"error: the {job['method']} turn failed: {job['error']}. "
                 f"authorlm workflow job {t.iid} --retry")


def add_leaves(leaf, instance) -> None:
    """The verbs' parsers, on `authorlm workflow` (cli.add_parser)."""
    def wait(q):
        q.add_argument("--wait", action="store_true",
                       help="run the model turn here and block, with its "
                            "progress on stderr, until it ends (default: it "
                            "runs on as a background job and the verb prints "
                            "its id)")

    def dry(q):
        q.add_argument("--dry-run", action="store_true",
                       help="say what would be sent or recorded and what a "
                            "billed engine would cost; send and write nothing")

    billed = (" A model turn on openai-api is billed (--dry-run shows the "
              "estimate).")
    q = leaf("walk", "where the walk stands at construction, the wrap-up or "
                     "the filters (read-only): each unit's state, then one "
                     "unit in full (the form waiting for the author, else "
                     "the next) with its reason and its original and "
                     "proposed text exactly, the job, the gate and the next "
                     "command", "authorlm workflow walk wi-123")
    instance(q)
    q.add_argument("--label", help="show this unit in full (e.g. p3, "
                                   "b2:plan, w:terms:1)")
    q = leaf("propose", "ask the engine for the next unit's form (or the "
                        "named one, while it is not yet worked), as the "
                        "page's background job." + billed,
             "authorlm workflow propose wi-123 --wait")
    instance(q)
    q.add_argument("--label", help="a unit not yet worked (default: the "
                                   "walk's next)")
    wait(q)
    dry(q)
    q = leaf("job", "the instance's running job, or its failed attempt, with "
                    "progress and error (read-only without a flag); --retry "
                    "sends the failed turn again, --dismiss hides it, --wait "
                    "blocks until a running job ends." + billed,
             "authorlm workflow job wi-123 --retry --wait")
    instance(q)
    g = q.add_mutually_exclusive_group()
    g.add_argument("--retry", action="store_true",
                   help="send the failed turn again (its method and params)")
    g.add_argument("--dismiss", action="store_true",
                   help="hide the failed attempt; it stays on the record")
    wait(q)
    dry(q)
    q = leaf("unit", "rule a unit's form with the author's verdict: --accept "
                     "the proposal, --reject it (keeps the original), --redo "
                     "with --blurb and --note (the author's words; a model "
                     "turn), or --edit/--edit-file the author's text (a "
                     "[bracket] in it is an instruction to the engine, a "
                     "model turn); wrap-up items (w:...) too." + billed,
             "authorlm workflow unit wi-123 p2 --redo --blurb 'too wordy' "
             "--note 'say it in half'")
    instance(q)
    q.add_argument("label", help="the unit (p3, s2, b1:plan, b1:p2, w:terms:1)")
    g = q.add_mutually_exclusive_group(required=True)
    g.add_argument("--accept", action="store_true", help="accept the proposal")
    g.add_argument("--reject", action="store_true",
                   help="reject it: the original stays")
    g.add_argument("--redo", action="store_true",
                   help="ask again, with --blurb and/or --note")
    g.add_argument("--edit", metavar="TEXT", help="the author's text, whole")
    g.add_argument("--edit-file", metavar="PATH",
                   help="the author's text from a file ('-' for stdin)")
    q.add_argument("--blurb", action="append", metavar="TEXT",
                   help="with --redo: what is wrong, from the blurb list "
                        "(repeat for several)")
    q.add_argument("--note", metavar="TEXT",
                   help="with --redo: the author's own words to the engine")
    q.add_argument("--with-discussion", action="store_true",
                   help="with --redo: hand the engine the unit's discussion "
                        "since its last proposal (`discuss`)")
    wait(q)
    dry(q)
    q = leaf("reopen", "go back to a ruled unit: a landed one is first taken "
                       "back out of the essay and its Doc tab, then the "
                       "engine proposes it again; later units are kept. "
                       "--leave answers a reopen the revised letter "
                       "proposes: the unit stays as accepted." + billed,
             "authorlm workflow reopen wi-123 p1 --wait")
    instance(q)
    q.add_argument("label", help="the ruled unit")
    q.add_argument("--leave", action="store_true",
                   help="leave it as accepted (a reopen the revised letter "
                        "proposed; recorded, not proposed again)")
    wait(q)
    dry(q)
    q = leaf("beat-done", "beat by beat: the current beat is done; the next "
                          "propose plans the next beat",
             "authorlm workflow beat-done wi-123")
    instance(q)
    dry(q)
    q = leaf("conclude", "beat by beat: the essay is built; construction's "
                         "walk ends (then advance)",
             "authorlm workflow conclude wi-123")
    instance(q)
    dry(q)
    q = leaf("wrapup", "ask the engine for the wrap-up (removed passages, "
                       "placeholders, terms, principles), whose items are "
                       "then ruled with `unit`." + billed,
             "authorlm workflow wrapup wi-123 --wait")
    instance(q)
    wait(q)
    dry(q)
    q = leaf("principle", "tick a principle the wrap-up offered: record the "
                          "author's words as a candidate style rule with its "
                          "scope (as the page's tick does)",
             "authorlm workflow principle wi-123 --words 'no rhetorical "
             "questions' --scope 'this essay'")
    instance(q)
    q.add_argument("--words", required=True, help="the author's words")
    q.add_argument("--scope", help="this paragraph | this essay | beyond")
    dry(q)
    q = leaf("discuss", "talk with the engine about one unit (a beat's plan, "
                        "a paragraph or section, a wrap-up item, a filter "
                        "unit) or, with the label `letter`, about the letter: "
                        "the author's message goes to the engine and its "
                        "reply is printed verbatim. The proposal does not "
                        "change; `unit <label> --redo --with-discussion` "
                        "hands the thread to the redo. Recorded on the "
                        "instance, so every later turn sees it." + billed,
             "authorlm workflow discuss wi-123 b1:plan --text 'why does the "
             "objection come second?' --wait")
    instance(q)
    q.add_argument("label", help="the unit (b1:plan, p3, s2, w:terms:1), or "
                                 "`letter`")
    q.add_argument("--text", required=True, help="the author's message, "
                                                 "in his words")
    wait(q)
    dry(q)


if __name__ == "__main__":
    sys.exit(child_main(sys.argv[1]))
