"""The per-essay editing workflow's record: workflows, instances, the
session's turns, the author's decisions, the blurb list, and the step gates.

Design: docs/plans/filter-lens-consolidation.md §11.9 (every decision with
the owner's words) and §12 row 1. In short:

- A workflow is one essay and one kind (new | refine | re-review); each run
  of it is an instance (Q12). The instance holds the three context lists
  (reference, voice, aware; Q1, Q2), the engine and model (Q6), the unit
  (Q8, Q19), the construction mode (Q18) and the filter selection (Q12).
- A new instance starts from the last instance's choices (Q1, Q3, Q12).
- Steps run in order: start, letter, construction, wrapup, filters, lint,
  done. Construction only after the letter's findings are ruled (Q4, 11.2);
  the filters only after the wrap-up (Q23); a skip moves on only with one
  sentence of reason, recorded (Q9).
- Engine and unit may change mid-instance with a reason, recorded (Q7, Q8).
- Every turn records its engine, model, usage and the requests the model
  made (Q7 "cost against quality", Q20).
- The author's choices are decisions; a decision is a ruling only when the
  author marks it so (Q14).
- Any essay in the reading order can have a workflow; no file is special-
  cased (owner, 2026-11-26: "there should be no special casing").
- Every walk is cut from the instance's base text: the essay as it stood
  when the instance first reached construction (or a later step), taken
  once and kept on the instance. The live file may move under an open
  instance (the author edits it, a landing writes it); a unit's label
  therefore always names the same text. Each unit turn also records the
  unit's original text (`unit_before`), and a ruling lands on that text,
  never on a position (`landing`).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .. import structure
from ..db import Database, ko_fields, loads, now_iso

KINDS = ("new", "refine", "re-review")
STEPS = ("start", "letter", "construction", "wrapup", "filters", "lint", "done")
ACTIONS = ("accept", "reject", "redo", "edit", "reply", "start")
UNITS = ("paragraph", "section")
MODES = ("beats", "apply-findings")
LISTS = ("reference", "voice", "aware")
DECISION_KINDS = ("skip", "engine-switch", "unit-switch", "list-change",
                  "brief-change", "principle", "ruling")
# Q6: claude-headless and openai-api now; claude-api and codex-headless are
# named so the record can hold them, and are declined until built.
ENGINES = ("claude-headless", "openai-api", "claude-api", "codex-headless")
PLANNED_ENGINES = ("claude-api", "codex-headless")
DEFAULT_ENGINE = "claude-headless"
# Q19: new → beat by beat, text paragraph by paragraph; refine and
# re-review → the findings applied paragraph by paragraph; filters → section.
KIND_DEFAULTS = {
    "new": {"construction_mode": "beats", "unit": "paragraph"},
    "refine": {"construction_mode": "apply-findings", "unit": "paragraph"},
    "re-review": {"construction_mode": "apply-findings", "unit": "paragraph"},
}
DEFAULT_FILTER_UNIT = "section"
# Steps whose unit is the filter unit; the rest use the construction unit.
FILTER_PHASE = ("filters", "lint")
# Actions that need no model call: the model sees them in the next turn.
LOCAL_ACTIONS = ("accept", "reject")


# ------------------------------------------------------------ the manuscript

def _files(manuscript: dict) -> dict[str, str]:
    from ..revisions import read_manuscript_files
    return read_manuscript_files(Path(manuscript["path"]))


def reading_order(manuscript: dict, files: dict | None = None) -> list[str]:
    files = _files(manuscript) if files is None else files
    order, _unlisted = structure.reading_order(files)
    return order


def _essay(name: str, order: list[str]) -> str:
    """The essay's file name as the TOC has it; `x` means `x.md`."""
    name = name.strip()
    if name not in order and f"{name}.md" in order:
        return f"{name}.md"
    return name


def _book_essay(name: str, order: list[str]) -> str:
    essay = _essay(name, order)
    if essay not in order:
        raise LookupError(f"'{name}' is not an essay of this book "
                          f"(one of: {', '.join(order)})")
    return essay


# ------------------------------------------------------------------ records

def _instance(row) -> dict:
    out = dict(row)
    for key in LISTS + ("filters", "toc_order"):
        out[key] = loads(out[key], [])
    out["engine_state"] = loads(out["engine_state"], {})
    out["filters_confirmed"] = bool(out["filters_confirmed"])
    out.setdefault("base_text", None)
    return out


def _turn(row) -> dict:
    out = dict(row)
    out["blurbs"] = loads(out["blurbs"], [])
    out["usage"] = loads(out["usage"], {})
    out["requests"] = loads(out["requests"], [])
    return out


def _decision(row) -> dict:
    out = dict(row)
    out["detail"] = loads(out["detail"], {})
    return out


def get_instance(db: Database, manuscript: dict, ref: str) -> dict:
    """An instance by its id, or the latest instance of an essay by the
    essay's name."""
    row = db.one("SELECT * FROM workflow_instances WHERE id = ? "
                 "AND manuscript_id = ?", (ref, manuscript["id"]))
    if row is None:
        name = ref if ref.endswith(".md") else f"{ref}.md"
        row = db.one("SELECT * FROM workflow_instances WHERE manuscript_id = ? "
                     "AND file IN (?, ?) ORDER BY created_at DESC LIMIT 1",
                     (manuscript["id"], ref, name))
    if row is None:
        raise LookupError(f"no workflow instance '{ref}'")
    return _instance(row)


def instances(db: Database, manuscript: dict, essay: str | None = None) -> list[dict]:
    sql = "SELECT * FROM workflow_instances WHERE manuscript_id = ?"
    args: tuple = (manuscript["id"],)
    if essay:
        name = essay if essay.endswith(".md") else f"{essay}.md"
        sql += " AND file IN (?, ?)"
        args += (essay, name)
    return [_instance(r) for r in db.all(sql + " ORDER BY created_at", args)]


def turns(db: Database, instance: dict) -> list[dict]:
    return [_turn(r) for r in db.all(
        "SELECT * FROM session_turns WHERE instance_id = ? ORDER BY seq",
        (instance["id"],))]


def decisions(db: Database, instance: dict) -> list[dict]:
    return [_decision(r) for r in db.all(
        "SELECT * FROM workflow_decisions WHERE instance_id = ? "
        "ORDER BY created_at", (instance["id"],))]


def _touch(db: Database, instance: dict, changes: dict) -> dict:
    changes = {**changes, "updated_at": now_iso()}
    db.update("workflow_instances", instance["id"], changes)
    return _instance(db.one("SELECT * FROM workflow_instances WHERE id = ?",
                            (instance["id"],)))


def _record_decision(db: Database, instance: dict, kind: str, *,
                     reason: str = "", words: str = "",
                     detail: dict | None = None) -> dict:
    if kind not in DECISION_KINDS:
        raise ValueError(f"decision kind must be one of {', '.join(DECISION_KINDS)}")
    row = {**ko_fields("wd"), "instance_id": instance["id"], "kind": kind,
           "reason": reason, "words": words,
           "detail": json.dumps(detail or {}, ensure_ascii=False)}
    db.insert("workflow_decisions", row)
    return _decision(row)


def _has_turns(db: Database, instance: dict) -> bool:
    return db.one("SELECT 1 FROM session_turns WHERE instance_id = ? LIMIT 1",
                  (instance["id"],)) is not None


# -------------------------------------------------------------------- start

def _open_instance(db: Database, manuscript: dict, file: str):
    return db.one("SELECT * FROM workflow_instances WHERE manuscript_id = ? "
                  "AND file = ? AND step != 'done' ORDER BY created_at DESC "
                  "LIMIT 1", (manuscript["id"], file))


def _carry_source(db: Database, manuscript: dict, workflow_id: str,
                  file: str) -> tuple[dict | None, bool]:
    """The instance a new one takes its choices from, and whether it is of
    the same workflow: the last instance of this workflow, else the essay's
    latest instance of another kind (its lists, engine and filters still
    fit the essay)."""
    row = db.one("SELECT * FROM workflow_instances WHERE workflow_id = ? "
                 "ORDER BY number DESC LIMIT 1", (workflow_id,))
    if row is not None:
        return _instance(row), True
    row = db.one("SELECT * FROM workflow_instances WHERE manuscript_id = ? "
                 "AND file = ? ORDER BY created_at DESC LIMIT 1",
                 (manuscript["id"], file))
    return (_instance(row), False) if row is not None else (None, False)


def start(db: Database, manuscript: dict, essay: str, kind: str, *,
          brief: str = "", engine: str | None = None,
          model: str | None = None, unit: str | None = None,
          mode: str | None = None) -> dict:
    """Start a new instance of the essay's workflow of `kind`, carrying the
    lists, engine, units and filter selection over from the last instance.
    Returns the instance, with `carry` saying what came from where."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    files = _files(manuscript)
    order = reading_order(manuscript, files)
    file = _essay(essay, order)
    if kind == "new":
        if not file.endswith(".md"):
            file = f"{file}.md"
    elif file not in order:
        raise LookupError(f"'{essay}' is not an essay of this book "
                          f"(one of: {', '.join(order)}); a {kind} works on "
                          "an essay that exists")
    open_row = _open_instance(db, manuscript, file)
    if open_row is not None:
        raise ValueError(
            f"{file} already has an open instance, {open_row['id']} "
            f"({open_row['kind']}, at {open_row['step']}). Finish it, or "
            f"close it: authorlm workflow skip {open_row['id']} --to done "
            "--reason \"...\"")
    if kind == "re-review" and db.one(
            "SELECT 1 FROM workflow_instances WHERE manuscript_id = ? "
            "AND file = ? AND step = 'done' AND finished_at IS NOT NULL "
            "LIMIT 1", (manuscript["id"], file)) is None:
        raise ValueError(f"{file} has no finished workflow instance to "
                         "re-review; start a refine instead")
    if engine is not None:
        _check_engine(engine)
    if unit is not None and unit not in UNITS:
        raise ValueError(f"unit must be one of {', '.join(UNITS)}")
    if mode is not None:
        _check_mode(kind, mode)

    with db.transaction():
        wf = db.one("SELECT * FROM workflows WHERE manuscript_id = ? "
                    "AND file = ? AND kind = ?", (manuscript["id"], file, kind))
        if wf is None:
            wf_row = {**ko_fields("wf"), "manuscript_id": manuscript["id"],
                      "file": file, "kind": kind}
            db.insert("workflows", wf_row)
            wf_id = wf_row["id"]
        else:
            wf_id = wf["id"]
        source, same = _carry_source(db, manuscript, wf_id, file)
        number = (db.one("SELECT MAX(number) AS n FROM workflow_instances "
                         "WHERE workflow_id = ?", (wf_id,))["n"] or 0) + 1
        defaults = KIND_DEFAULTS[kind]
        lists = {k: [] for k in LISTS}
        dropped: dict[str, list[str]] = {}
        carried = {}
        if source is not None:
            for k in LISTS:
                lists[k] = [e for e in source[k] if e in order and e != file]
                gone = [e for e in source[k] if e not in lists[k]]
                if gone:
                    dropped[k] = gone
            carried = {
                "engine": source["engine"], "model": source["model"],
                "filters": source["filters"],
                "filter_unit": source["filter_unit"],
            }
            if same:
                carried.update(unit=source["unit"],
                               construction_mode=source["construction_mode"])
        chosen_engine = engine or carried.get("engine") or DEFAULT_ENGINE
        if model is not None:
            chosen_model = model
        elif chosen_engine == carried.get("engine"):
            chosen_model = carried.get("model", "")
        else:
            chosen_model = ""
        inst = {
            **ko_fields("wi"), "workflow_id": wf_id,
            "manuscript_id": manuscript["id"], "file": file, "kind": kind,
            "number": number, "step": "start", "brief": brief,
            "reference": json.dumps(lists["reference"], ensure_ascii=False),
            "voice": json.dumps(lists["voice"], ensure_ascii=False),
            "aware": json.dumps(lists["aware"], ensure_ascii=False),
            "engine": chosen_engine,
            "model": chosen_model,
            "engine_state": "{}",
            "unit": unit or carried.get("unit") or defaults["unit"],
            "construction_mode": (mode or carried.get("construction_mode")
                                  or defaults["construction_mode"]),
            "filter_unit": carried.get("filter_unit") or DEFAULT_FILTER_UNIT,
            "filters": json.dumps(carried.get("filters") or [],
                                  ensure_ascii=False),
            "filters_confirmed": 0,
            "carried_from": source["id"] if source else None,
            "toc_order": json.dumps(order, ensure_ascii=False),
            "updated_at": now_iso(),
        }
        db.insert("workflow_instances", inst)
    out = get_instance(db, manuscript, inst["id"])
    out["carry"] = {"from": source["id"] if source else None,
                    "same_workflow": same, "dropped": dropped}
    return out


def since_last(db: Database, instance: dict) -> dict:
    """Essays added to or gone from the reading order between the instance
    its choices came from and this one's start (Q3): the start step's
    recommendation looks at these. Read-only."""
    if not instance.get("carried_from"):
        return {"added": [], "removed": []}
    row = db.one("SELECT toc_order FROM workflow_instances WHERE id = ?",
                 (instance["carried_from"],))
    before = loads(row["toc_order"], []) if row else []
    now = instance["toc_order"]
    return {"added": [e for e in now if e not in before
                      and e != instance["file"]],
            "removed": [e for e in before if e not in now]}


# -------------------------------------------------------------------- gates

def letter_run(instance: dict) -> str:
    """The run key of the instance's letter findings on `devedit_items`
    (Q4: the letter's findings live on the devedit item record)."""
    return f"workflow/{instance['id']}"


def letter_findings(db: Database, instance: dict) -> list[dict]:
    return [dict(r) for r in db.all(
        "SELECT item_id, file, body, verdict, reason_given, decided "
        "FROM devedit_items WHERE manuscript_id = ? AND run = ? ORDER BY ord",
        (instance["manuscript_id"], letter_run(instance)))]


def add_finding(db: Database, instance: dict, item_id: str, body: dict) -> str:
    """Record one letter finding on the instance (undecided)."""
    n = db.one("SELECT COUNT(*) AS n FROM devedit_items WHERE manuscript_id = ? "
               "AND run = ?", (instance["manuscript_id"], letter_run(instance)))["n"]
    row = {**ko_fields("di"), "manuscript_id": instance["manuscript_id"],
           "run": letter_run(instance), "item_id": item_id,
           "file": instance["file"], "ord": n + 1, "gathered": 1, "queued": 1,
           "body": json.dumps(body, ensure_ascii=False)}
    return db.insert("devedit_items", row)


def rule_finding(db: Database, instance: dict, item_id: str, verdict: str,
                 reason: str = "", actor: str = "chat") -> None:
    """The author's verdict on a letter finding: accept, reject or defer."""
    if verdict not in ("accept", "reject", "defer"):
        raise ValueError("verdict must be accept, reject or defer")
    row = db.one("SELECT id FROM devedit_items WHERE manuscript_id = ? AND "
                 "run = ? AND item_id = ?", (instance["manuscript_id"],
                                             letter_run(instance), item_id))
    if row is None:
        raise LookupError(f"no finding '{item_id}' on {instance['id']}")
    at = now_iso()
    with db.transaction():
        db.update("devedit_items", row["id"], {
            "verdict": verdict, "reason_given": reason or None, "decided": at})
        db.insert("edit_events", {
            **ko_fields("eev"), "manuscript_id": instance["manuscript_id"],
            "source": "workflow", "run": letter_run(instance),
            "item_id": item_id, "file": instance["file"], "event": "verdict",
            "verdict": verdict,
            "detail": json.dumps({"reason": reason}, ensure_ascii=False),
            "at": at, "actor": actor})


def _step_turns(db: Database, instance: dict, step: str,
                actions: tuple = ACTIONS) -> int:
    marks = ", ".join("?" for _ in actions)
    return db.one(f"SELECT COUNT(*) AS n FROM session_turns WHERE instance_id = ? "
                  f"AND step = ? AND action IN ({marks})",
                  (instance["id"], step, *actions))["n"]


def gate(db: Database, instance: dict) -> str | None:
    """Why the instance cannot advance from its step, or None when it can.
    Read-only."""
    step = instance["step"]
    if step == "done":
        return "the instance is done"
    if step == "letter":
        findings = letter_findings(db, instance)
        open_ = [f["item_id"] for f in findings if not f["verdict"]]
        if open_:
            return (f"construction waits for the letter: {len(open_)} "
                    f"finding(s) not yet accepted, rejected or deferred "
                    f"({', '.join(open_[:5])}{'…' if len(open_) > 5 else ''})")
        if not findings and not _step_turns(db, instance, "letter", ("accept",)):
            return ("construction waits for the letter: it has no findings "
                    "and no accepted letter turn")
    if step == "construction" and not _step_turns(db, instance, "construction"):
        return ("the wrap-up waits for construction: no unit has been "
                "worked; skip with a reason if construction is not needed")
    if step == "filters":
        if not instance["filters_confirmed"]:
            return ("lint waits for the filter selection: choose it with "
                    f"'authorlm workflow filters {instance['id']} --set a,b'"
                    + (f" (pre-ticked from the last instance: "
                       f"{', '.join(instance['filters'])})"
                       if instance["filters"] else ""))
        if instance["filters"] and not _step_turns(db, instance, "filters"):
            return ("lint waits for the filters: none of the selected passes "
                    "has run; skip with a reason if they are not needed")
    return None


def _next_step(step: str) -> str:
    return STEPS[STEPS.index(step) + 1]


# ---------------------------------------------------------------- base text

def file_text(manuscript: dict, file: str) -> str | None:
    """The essay file's text now, or None when there is no such file."""
    path = Path(manuscript["path"]) / file
    try:
        return path.read_text(encoding="utf-8")
    except (FileNotFoundError, IsADirectoryError):
        return None


def base_text(manuscript: dict, instance: dict) -> str:
    """The text every walk of the instance is cut from: its base when
    taken, else the file as it is now (a new essay's absent file is "")."""
    if instance.get("base_text") is not None:
        return instance["base_text"]
    return file_text(manuscript, instance["file"]) or ""


def _manuscript_of(db: Database, instance: dict) -> dict:
    return dict(db.one("SELECT * FROM manuscripts WHERE id = ?",
                       (instance["manuscript_id"],)))


def ensure_base(db: Database, manuscript: dict, instance: dict) -> dict:
    """Take the instance's base text if it has none yet: the file as it
    is now. Returns the instance as stored."""
    if instance.get("base_text") is not None:
        return instance
    db.update("workflow_instances", instance["id"],
              {"base_text": file_text(manuscript, instance["file"]) or ""})
    return _instance(db.one("SELECT * FROM workflow_instances WHERE id = ?",
                            (instance["id"],)))


def _base_change(db: Database, instance: dict, to: str) -> dict:
    """The base text to take when the step moves to `to`: at the first
    step that walks the essay (construction or later)."""
    if (instance.get("base_text") is not None
            or STEPS.index(to) < STEPS.index("construction")):
        return {}
    manuscript = _manuscript_of(db, instance)
    return {"base_text": file_text(manuscript, instance["file"]) or ""}


def advance(db: Database, instance: dict) -> dict:
    why = gate(db, instance)
    if why:
        raise ValueError(why)
    to = _next_step(instance["step"])
    changes = {"step": to, **_base_change(db, instance, to)}
    if to == "done":
        changes["finished_at"] = now_iso()
    return _touch(db, instance, changes)


_SECOND_SENTENCE = re.compile(r"[.!?]\s+\S")


def _one_sentence(reason: str | None, what: str) -> str:
    reason = (reason or "").strip()
    if not reason:
        raise ValueError(f"{what} needs a reason: one sentence")
    if _SECOND_SENTENCE.search(reason):
        raise ValueError(f"{what}: the reason is one sentence, not several")
    return reason


def skip(db: Database, instance: dict, reason: str, to: str | None = None) -> dict:
    """Move past the step's gate, with one sentence of reason recorded (Q9).
    `to` skips to a later step; every step passed is named in the record."""
    reason = _one_sentence(reason, "a skip")
    step = instance["step"]
    if step == "done":
        raise ValueError("the instance is done")
    target = to or _next_step(step)
    if target not in STEPS or STEPS.index(target) <= STEPS.index(step):
        raise ValueError(f"skip goes forward: one of "
                         f"{', '.join(STEPS[STEPS.index(step) + 1:])}")
    passed = list(STEPS[STEPS.index(step):STEPS.index(target)])
    with db.transaction():
        _record_decision(db, instance, "skip", reason=reason, words=reason,
                         detail={"from": step, "to": target,
                                 "skipped": passed,
                                 "gate": gate(db, instance)})
        changes = {"step": target, **_base_change(db, instance, target)}
        if target == "done":
            changes["finished_at"] = now_iso()
        out = _touch(db, instance, changes)
    return out


# ----------------------------------------------------------------- switches

def _check_engine(engine: str) -> None:
    if engine not in ENGINES:
        raise ValueError(f"engine must be one of {', '.join(ENGINES)}")
    if engine in PLANNED_ENGINES:
        raise ValueError(f"engine '{engine}' is planned, not built yet; "
                         f"use one of {', '.join(e for e in ENGINES if e not in PLANNED_ENGINES)}")


def _check_mode(kind: str, mode: str) -> None:
    if mode not in MODES:
        raise ValueError(f"construction mode must be one of {', '.join(MODES)}")
    if kind == "new" and mode != "beats":
        raise ValueError("a new essay is built beat by beat; there are no "
                         "findings to apply (Q18)")


def _needs_reason(db: Database, instance: dict, reason: str | None,
                  what: str) -> str:
    reason = (reason or "").strip()
    if not reason and _has_turns(db, instance):
        raise ValueError(f"{what} mid-instance needs a reason (--reason)")
    return reason


def set_engine(db: Database, instance: dict, engine: str,
               model: str | None = None, reason: str | None = None) -> dict:
    """Switch the engine or model (Q7). Mid-instance it takes a reason; the
    new engine rebuilds its context from the record on its next turn, so
    the engine's own state is cleared."""
    _check_engine(engine)
    model = instance["model"] if model is None and engine == instance["engine"] \
        else (model or "")
    if engine == instance["engine"] and model == instance["model"]:
        raise ValueError(f"the instance already runs on {engine}"
                         + (f" ({model})" if model else ""))
    reason = _needs_reason(db, instance, reason, "an engine switch")
    with db.transaction():
        _record_decision(db, instance, "engine-switch", reason=reason,
                         words=reason, detail={
                             "from": {"engine": instance["engine"],
                                      "model": instance["model"]},
                             "to": {"engine": engine, "model": model},
                             "step": instance["step"]})
        return _touch(db, instance, {"engine": engine, "model": model,
                                     "engine_state": "{}"})


def set_unit(db: Database, instance: dict, unit: str | None = None, *,
             phase: str | None = None, mode: str | None = None,
             reason: str | None = None) -> dict:
    """Change the unit (Q8: recorded, like an engine switch) or the
    construction mode (Q18). `phase` is construction or filters; by default
    the phase of the current step."""
    if unit is None and mode is None:
        raise ValueError("give a unit (paragraph|section) or --mode")
    if unit is not None and unit not in UNITS:
        raise ValueError(f"unit must be one of {', '.join(UNITS)}")
    if mode is not None:
        _check_mode(instance["kind"], mode)
    phase = phase or ("filters" if instance["step"] in FILTER_PHASE
                      else "construction")
    if phase not in ("construction", "filters"):
        raise ValueError("phase must be construction or filters")
    column = "filter_unit" if phase == "filters" else "unit"
    changes = {}
    if unit is not None and unit != instance[column]:
        changes[column] = unit
    if mode is not None and mode != instance["construction_mode"]:
        changes["construction_mode"] = mode
    if not changes:
        raise ValueError("nothing changes")
    reason = _needs_reason(db, instance, reason, "a unit switch")
    with db.transaction():
        _record_decision(db, instance, "unit-switch", reason=reason,
                         words=reason, detail={
                             "phase": phase, "step": instance["step"],
                             "from": {k: instance[k] for k in changes},
                             "to": changes})
        return _touch(db, instance, changes)


def change_list(db: Database, manuscript: dict, instance: dict, which: str,
                essay: str, *, add: bool = True, words: str = "") -> dict:
    """Add an essay to, or remove it from, one of the three lists (Q1, Q3):
    the author's explicit choice, recorded."""
    if which not in LISTS:
        raise ValueError(f"list must be one of {', '.join(LISTS)}")
    if instance["step"] == "done":
        raise ValueError("the instance is done")
    order = reading_order(manuscript)
    current = list(instance[which])
    if add:
        name = _book_essay(essay, order)
        if name == instance["file"]:
            raise ValueError("an essay is not on its own lists")
        if name in current:
            raise ValueError(f"{name} is already on the {which} list")
        current.append(name)
        current.sort(key=order.index)
    else:
        name = _essay(essay, current)
        if name not in current:
            raise ValueError(f"{essay} is not on the {which} list")
        current.remove(name)
    with db.transaction():
        _record_decision(db, instance, "list-change", reason=words,
                         words=words, detail={
                             "list": which, "essay": name,
                             "change": "add" if add else "remove"})
        return _touch(db, instance, {
            which: json.dumps(current, ensure_ascii=False)})


def set_brief(db: Database, instance: dict, brief: str) -> dict:
    """Change the author's brief of an open instance, at any step (owner,
    2026-11-26, at the start step of wi-7abca47358e0: "if I have read the
    recap for sermon 3 and I've read sermon 3 and I have some thoughts
    about what we should take into account, should I be able to edit the
    brief and add things there?").

    The brief is the `author-brief` standing block (context.py), read when
    a model turn starts: the next turn uses the new brief and rebuilds its
    session (the standing blocks changed, engines/base.py); earlier turns
    stay as they were. A turn already running keeps the brief it started
    with. Recorded as a `brief-change` decision with the old and new text,
    so the record says which brief each turn had."""
    if instance["step"] == "done":
        raise ValueError("the instance is done; its brief stays as it was")
    brief = (brief or "").strip()
    if brief == (instance["brief"] or "").strip():
        raise ValueError("the brief is unchanged")
    with db.transaction():
        _record_decision(db, instance, "brief-change", detail={
            "from": instance["brief"] or "", "to": brief,
            "step": instance["step"]})
        return _touch(db, instance, {"brief": brief})


def set_filters(db: Database, instance: dict, names: list[str],
                standard_order: list[str] | None = None) -> dict:
    """The author's filter selection, at the filter step (Q12): ticking only
    includes; the passes run in the standard order whatever the order they
    were ticked in."""
    if instance["step"] != "filters":
        raise ValueError(f"the filters are chosen at the filter step, after "
                         f"the wrap-up (Q12); the instance is at "
                         f"{instance['step']}")
    names = [n.strip() for n in names if n.strip()]
    if len(set(names)) != len(names):
        raise ValueError("a filter is named twice")
    if standard_order:
        unknown = [n for n in names if n not in standard_order]
        if unknown:
            raise ValueError(f"not in the standard order: {', '.join(unknown)} "
                             f"(one of: {', '.join(standard_order)})")
        names.sort(key=standard_order.index)
    return _touch(db, instance, {
        "filters": json.dumps(names, ensure_ascii=False),
        "filters_confirmed": 1})


def decide(db: Database, instance: dict, kind: str, words: str,
           reason: str = "") -> dict:
    """Record a principle the author stated (Q24) or a ruling the author
    explicitly marked as one (Q14). Nothing else is recorded as a ruling."""
    if kind not in ("principle", "ruling"):
        raise ValueError("decide records a principle or a ruling")
    words = (words or "").strip()
    if not words:
        raise ValueError(f"a {kind} needs the author's words")
    return _record_decision(db, instance, kind, reason=reason, words=words,
                            detail={"step": instance["step"]})


# -------------------------------------------------------------------- turns

def _bracketed(text: str) -> bool:
    return "[" in text and "]" in text


def needs_model(action: str, author_text: str = "") -> bool:
    """Accept, reject and a plain edit are recorded without a model call;
    a redo, a reply, a start, and an edit with a bracket in it ask the model
    for the next revision (Q8a, Q11)."""
    if action in LOCAL_ACTIONS:
        return False
    if action == "edit":
        return _bracketed(author_text)
    return True


def _check_turn(instance: dict, action: str, author_text: str,
                blurbs: list[str]) -> None:
    if action not in ACTIONS:
        raise ValueError(f"action must be one of {', '.join(ACTIONS)}")
    if instance["step"] == "done":
        raise ValueError("the instance is done")
    if action == "redo" and not blurbs and not author_text.strip():
        raise ValueError("a redo takes a blurb or a few words of what is "
                         "wrong (Q11)")
    if action == "edit" and not author_text.strip():
        raise ValueError("an edit carries the author's text")


def record_turn(db: Database, instance: dict, action: str, *,
                author_text: str = "", blurbs: list[str] | None = None,
                unit_label: str = "", reply: dict | None = None,
                session_ref: str | None = None, unit_before: str = "") -> dict:
    """Append one turn to the instance's session. `reply` is a normalized
    engine reply (contract.normalize_reply) when the turn asked the model;
    `session_ref` the engine's handle after it, stored in the instance's
    engine_state in the same transaction as the turn. `unit_before` is the
    unit's original text, which a ruling lands on."""
    blurbs = list(blurbs or [])
    _check_turn(instance, action, author_text, blurbs)
    reply = reply or {}
    with db.transaction():
        seq = (db.one("SELECT MAX(seq) AS n FROM session_turns WHERE "
                      "instance_id = ?", (instance["id"],))["n"] or 0) + 1
        row = {
            **ko_fields("st"), "instance_id": instance["id"], "seq": seq,
            "step": instance["step"], "unit_label": unit_label,
            "action": action, "author_text": author_text,
            "blurbs": json.dumps(blurbs, ensure_ascii=False),
            "reply": reply.get("text", ""), "reason": reply.get("reason", ""),
            "proposed": reply.get("proposed", ""),
            "engine": instance["engine"] if reply else "",
            "model": instance["model"] if reply else "",
            "usage": json.dumps(reply.get("usage") or {}),
            "requests": json.dumps(reply.get("requests") or [],
                                   ensure_ascii=False),
            "unit_before": unit_before or "",
        }
        db.insert("session_turns", row)
        if blurbs:
            use_blurbs(db, instance["manuscript_id"], blurbs)
        if reply.get("suggested_blurbs"):
            add_blurbs(db, instance["manuscript_id"], reply["suggested_blurbs"])
        changes = {}
        if session_ref:
            state = loads(db.one("SELECT engine_state FROM workflow_instances "
                                 "WHERE id = ?", (instance["id"],))
                          ["engine_state"], {})
            state["session_ref"] = session_ref
            changes["engine_state"] = json.dumps(state, ensure_ascii=False)
        _touch(db, instance, changes)
    return _turn(row)


def ask(db: Database, manuscript: dict, instance: dict, action: str,
        message: str, *, engine=None, author_text: str | None = None,
        blurbs: list[str] | None = None,
        context_extra: dict | None = None) -> tuple[dict, str | None]:
    """Ask the instance's engine for its reply and record nothing: (the
    normalized reply, the engine's session ref after it). `context_extra`
    is merged over the instance context (a caller's working text of the
    essay as `essay`, its `workspace` and `manuscript` name). A caller that
    records more than the turn (a letter's findings) records the turn and
    the rest in one transaction with `record_turn`."""
    from . import context, contract

    author_text = message if author_text is None else author_text
    blurbs = list(blurbs or [])
    _check_turn(instance, action, author_text, blurbs)
    if not needs_model(action, author_text):
        raise ValueError(f"'{action}' is recorded without a model call; "
                         "use record_turn")
    # The record, not the caller's copy, holds the session ref to resume.
    instance = _instance(db.one("SELECT * FROM workflow_instances WHERE id = ?",
                                (instance["id"],)))
    engine = engine or contract.make_engine(instance["engine"], instance["model"])
    ctx = context.instance_context(db, manuscript, instance)
    if context_extra:
        ctx.update(context_extra)
    reply = contract.normalize_reply(engine.send(ctx, message))
    return reply, getattr(engine, "session_ref", None)


def send_turn(db: Database, manuscript: dict, instance: dict, action: str,
              message: str, *, engine=None, author_text: str | None = None,
              blurbs: list[str] | None = None, unit_label: str = "",
              context_extra: dict | None = None, unit_before: str = "") -> dict:
    """Ask the instance's engine for the next reply and record the turn,
    with the engine's session ref (`ask`, then `record_turn`). `message` is
    what goes to the engine; `author_text` the author's own words (default:
    the message). `engine` is for tests and callers that hold one;
    otherwise the registered engine of the instance is made."""
    author_text = message if author_text is None else author_text
    reply, ref = ask(db, manuscript, instance, action, message, engine=engine,
                     author_text=author_text, blurbs=blurbs,
                     context_extra=context_extra)
    return record_turn(db, instance, action, author_text=author_text,
                       blurbs=blurbs, unit_label=unit_label, reply=reply,
                       session_ref=ref, unit_before=unit_before)


# ------------------------------------------------------------------- blurbs

def _clean_blurb(text: str) -> str:
    return " ".join((text or "").split())


def add_blurbs(db: Database, manuscript_id: str, texts: list[str]) -> list[str]:
    """Add blurbs not yet on the manuscript's list; a hidden one stays
    hidden. Returns the ones added."""
    added = []
    for raw in texts:
        text = _clean_blurb(raw)
        if not text or db.one("SELECT 1 FROM blurbs WHERE manuscript_id = ? "
                              "AND LOWER(text) = LOWER(?)", (manuscript_id, text)):
            continue
        db.insert("blurbs", {**ko_fields("bl"), "manuscript_id": manuscript_id,
                             "text": text, "last_used": None, "hidden": 0})
        added.append(text)
    return added


def use_blurbs(db: Database, manuscript_id: str, texts: list[str]) -> None:
    """Ticked on a redo: added if new, and moved to the top of the list."""
    add_blurbs(db, manuscript_id, texts)
    at = now_iso()
    for raw in texts:
        row = db.one("SELECT id FROM blurbs WHERE manuscript_id = ? AND "
                     "LOWER(text) = LOWER(?)", (manuscript_id, _clean_blurb(raw)))
        if row:
            db.update("blurbs", row["id"], {"last_used": at})


def hide_blurb(db: Database, manuscript_id: str, text: str, hidden: bool = True) -> None:
    row = db.one("SELECT id FROM blurbs WHERE manuscript_id = ? AND "
                 "LOWER(text) = LOWER(?)", (manuscript_id, _clean_blurb(text)))
    if row is None:
        raise LookupError(f"no blurb '{text}'")
    db.update("blurbs", row["id"], {"hidden": int(hidden)})


def blurb_list(db: Database, manuscript_id: str, hidden: bool = False) -> list[dict]:
    """Most recently used first, then newest."""
    return [dict(r) for r in db.all(
        "SELECT text, last_used, hidden, created_at FROM blurbs "
        "WHERE manuscript_id = ? " + ("" if hidden else "AND hidden = 0 ")
        + "ORDER BY COALESCE(last_used, '') DESC, created_at DESC",
        (manuscript_id,))]


# -------------------------------------------------------------------- usage

def _add(total: dict, usage: dict) -> None:
    """Add one turn's usage. A turn whose dollar cost is unknown (None) is
    counted in `dollars_unknown`, never as $0; `dollars` is the sum of the
    known costs, and None while no turn has a known one."""
    for key in ("input", "cached", "output", "est_subscription"):
        total[key] = total.get(key, 0) + (usage.get(key) or 0)
    total.setdefault("dollars", None)
    total.setdefault("dollars_unknown", 0)
    if usage.get("dollars") is None:
        total["dollars_unknown"] += 1
    else:
        total["dollars"] = (total["dollars"] or 0) + usage["dollars"]
    total["turns"] = total.get("turns", 0) + 1


def usage(db: Database, manuscript: dict, essay: str | None = None,
          month: str | None = None) -> dict:
    """Usage totals over the turns that called a model: per instance, per
    essay and per month, plus the grand total (Q7). Read-only."""
    sql = ("SELECT t.usage, t.created_at, t.engine, i.id AS instance_id, "
           "i.file FROM session_turns t JOIN workflow_instances i "
           "ON i.id = t.instance_id WHERE i.manuscript_id = ? "
           "AND t.engine != ''")
    args: tuple = (manuscript["id"],)
    if essay:
        name = essay if essay.endswith(".md") else f"{essay}.md"
        sql += " AND i.file IN (?, ?)"
        args += (essay, name)
    if month:
        if not re.fullmatch(r"\d{4}-\d{2}", month):
            raise ValueError("month is YYYY-MM")
        sql += " AND substr(t.created_at, 1, 7) = ?"
        args += (month,)
    out = {"total": {}, "by_instance": {}, "by_essay": {}, "by_month": {},
           "by_engine": {}}
    for r in db.all(sql, args):
        u = loads(r["usage"], {})
        _add(out["total"], u)
        _add(out["by_instance"].setdefault(r["instance_id"], {}), u)
        _add(out["by_essay"].setdefault(r["file"], {}), u)
        _add(out["by_month"].setdefault(r["created_at"][:7], {}), u)
        _add(out["by_engine"].setdefault(r["engine"], {}), u)
    return out


# ------------------------------------------------------------------- status

def status(db: Database, manuscript: dict, essay: str | None = None) -> list[dict]:
    """Each essay's latest instance, its step, and what the next step waits
    for. Read-only."""
    rows = instances(db, manuscript, essay)
    latest: dict[str, dict] = {}
    for inst in rows:
        latest[inst["file"]] = inst
    out = []
    for file, inst in latest.items():
        out.append({"file": file, "instance": inst["id"], "kind": inst["kind"],
                    "number": inst["number"], "step": inst["step"],
                    "engine": inst["engine"], "model": inst["model"],
                    "waits_for": gate(db, inst),
                    "next": None if inst["step"] == "done"
                    else _next_step(inst["step"])})
    return out
