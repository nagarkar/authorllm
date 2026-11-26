"""The old roads, retired as entry points: a standalone filter run, a
standalone lens run, the beat loop's `write start`, and the Filters view's
run requests now send the author to the essay's workflow.

Design: docs/plans/filter-lens-consolidation.md §11.9 Q2 and §12 row 6.
The owner (2026-11-25): "I want to retire the old workflow for beats and
the old workflow for independently adding filters and lenses ... So it
could be that I skip some steps and give a reason and just run the
filters. But by default, the behavior should be like you go through that
workflow, because that'll create the right discipline."

Invariant: a road opens without a word only where the essay's open
workflow instance has reached the step the road belongs to (filters and
lenses at `filters`; the beat loop at `construction` built beat by beat).
Anywhere else it opens only with a skip: one sentence of reason (Q9),
recorded as a `skip` decision on the essay's open instance, which is
started (refine for an essay that exists, new for one that does not) when
there is none, the decision saying so. The skip does not move the
instance's step; it records that this road ran outside the order. A later
call of the same road for the same filter or lens on the same instance
opens without asking again (a filter run continues window by window).

Decisions:
- The roads' internals are kept and ungated (`api.filter_run`,
  `lenses.assemble`, `api.write_start`): the workflow reuses them. Only
  the entry points ask (the CLI verbs and the Filters view's queue).
- The developmental edit's whole-book run is not a road retired here: it
  is a book-level pass (Q1a).
- No file is special-cased (owner, 2026-11-26: "there should be no
  special casing"); every essay of the reading order is treated alike.

Never imports: cli, api.
"""

from __future__ import annotations

from ..db import Database
from . import core

# road → (the step it belongs to, what the author calls it)
ROADS = {
    "filter": ("filters", "a filter run"),
    "lens": ("filters", "a lens run"),
    "beats": ("construction", "the beat loop (write start)"),
}
SKIP_FLAG = "--skip-reason"


class SentToWorkflow(ValueError):
    """The road is closed for this essay now; the message says how to
    start or continue its workflow, and how to skip with a reason."""


def _essay_file(manuscript: dict, essay: str) -> tuple[str, bool]:
    """The essay's file name as the reading order has it, and whether it
    is in the reading order (an essay that exists)."""
    order = core.reading_order(manuscript)
    name = core._essay(essay.split("/")[-1], order)
    if name not in order and not name.endswith(".md"):
        name = f"{name}.md"
    return name, name in order


def open_instance(db: Database, manuscript: dict, file: str) -> dict | None:
    row = core._open_instance(db, manuscript, file)
    return core._instance(row) if row is not None else None


def _belongs(road: str, inst: dict) -> bool:
    step = ROADS[road][0]
    if inst["step"] != step:
        return False
    if road == "beats":
        return inst["construction_mode"] == "beats"
    return True


def _earlier_skip(db: Database, inst: dict, road: str, name: str) -> dict | None:
    for d in core.decisions(db, inst):
        detail = d["detail"]
        if d["kind"] == "skip" and detail.get("road") == road \
                and detail.get("name", "") == name:
            return d
    return None


def how_to(manuscript: dict, file: str, road: str, inst: dict | None,
           exists: bool, name: str = "", skip_hint: str = "") -> str:
    """The message a closed road prints: where the essay's workflow
    stands and the command that starts or continues it."""
    m = f" -m {manuscript['name']}"
    step, what = ROADS[road]
    what = f"{what} ({name})" if name else what
    if inst is None:
        kind = "refine" if exists else "new"
        head = (f"{what} on {file} goes through the essay's workflow now "
                f"(letter, construction, wrap-up, filters, lint). {file} has "
                f"no open workflow. Start it: authorlm workflow start "
                f"{file.removesuffix('.md')} --kind {kind}{m}")
    else:
        where = (f"{file} is in its workflow ({inst['kind']} #{inst['number']}"
                 f", {inst['id']}) at the {inst['step']} step")
        if road == "beats" and inst["step"] == "construction":
            where += (f", built by {inst['construction_mode']}, not beat by "
                      "beat")
        head = (f"{what} belongs to the {step} step of the essay's workflow; "
                f"{where}. Continue it: authorlm workflow show {inst['id']}{m}")
    return head + ". " + (skip_hint or (
        f"To run it anyway, outside the workflow, add {SKIP_FLAG} "
        "\"<one sentence: why>\"; the reason is recorded on the essay's "
        "workflow."))


def enter(db: Database, manuscript: dict, essay: str, road: str, *,
          name: str = "", reason: str | None = None, via: str = "",
          dry_run: bool = False, skip_hint: str = "") -> dict:
    """May `road` run on `essay` now? Returns {how, file, instance,
    decision}: how is `workflow` (the open instance is at the road's own
    step), `skipped-before` (an earlier skip for this road and name on the
    open instance), or `skip` (this call's reason, recorded). Raises
    SentToWorkflow, with how to start or continue the workflow, when there
    is no reason. `dry_run` checks a reason and writes nothing."""
    if road not in ROADS:
        raise ValueError(f"road must be one of {', '.join(ROADS)}")
    file, exists = _essay_file(manuscript, essay)
    inst = open_instance(db, manuscript, file)
    if inst is not None:
        if _belongs(road, inst):
            return {"how": "workflow", "file": file, "instance": inst,
                    "decision": None}
        earlier = _earlier_skip(db, inst, road, name)
        if earlier is not None:
            return {"how": "skipped-before", "file": file, "instance": inst,
                    "decision": earlier}
    if reason is None or not reason.strip():
        raise SentToWorkflow(how_to(manuscript, file, road, inst, exists,
                                    name, skip_hint))
    reason = core._one_sentence(reason, "running outside the workflow")
    if dry_run:
        return {"how": "skip", "file": file, "instance": inst,
                "decision": None}
    started = inst is None
    if started:
        inst = core.start(db, manuscript, file, "refine" if exists else "new")
    decision = core._record_decision(
        db, inst, "skip", reason=reason, words=reason, detail={
            "road": road, "name": name, "file": file, "step": inst["step"],
            "outside_workflow": True, "via": via or road,
            "instance_started": started,
            **({"why_started": f"started so the skip on {ROADS[road][1]} "
                               "has an instance to be recorded on"}
               if started else {})})
    return {"how": "skip", "file": file, "instance": inst,
            "decision": decision}
