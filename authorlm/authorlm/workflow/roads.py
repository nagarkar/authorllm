"""The old roads, retired as entry points: a standalone filter run, a
standalone lens run, the beat loop's `write start`, and the Filters view's
run requests now send the author to the essay's workflow.

Design: docs/plans/filter-lens-consolidation.md §11.9 Q2 and §12 row 6.
The owner (2026-11-25): "I want to retire the old workflow for beats and
the old workflow for independently adding filters and lenses ... So it
could be that I skip some steps and give a reason and just run the
filters. But by default, the behavior should be like you go through that
workflow, because that'll create the right discipline."

Invariant: an old road always runs outside the workflow, so it opens only
with a skip: one sentence of reason (Q9), recorded as a `skip` decision on
the essay's open instance, which is started when there is none (refine for
an essay of the book, re-review for one that has been through the workflow
before, new for a `write start --new`), the decision saying so. The skip
does not move the instance's step. A later call of the same road for the
same filter or lens on the same instance opens without asking again (a
filter run continues window by window).

Why even at the road's own step: the old machinery (`api.filter_run`,
`lenses.assemble`, `api.write_start`) never reads the instance. It runs
without the essay's reference, voice and aware lists (Q2: "Every step of
every kind gets all three, the beat loop included"), without the
instance's engine and unit (Q6, Q19), outside its filter selection and
standard order (Q12), with lenses that Q12 folds into the letter brief,
and records no session turn. Running it is outside the workflow wherever
the instance stands, so it is recorded as such. When the workflow drives
these runs itself (the Workflows page, piece 5), the step it belongs to
is named in the message, not opened by it.

Decisions:
- The roads' internals are kept and ungated: the workflow reuses them.
  Only the entry points ask (the CLI verbs and the Filters view's queue).
- Entry points check first and record last: `enter(..., dry_run=True)`
  checks the essay, the filter or lens and the reason and writes nothing;
  `record(plan, ...)` writes the instance and the skip in one transaction,
  after the verb's own checks have passed (cli.py), or inside the caller's
  transaction (runs_page.enqueue).
- The essay is resolved as the verbs resolve it (`docs._resolve_relpath`:
  the manuscript-relative path, or a unique part of it), never cut to its
  basename. A filter or lens needs an essay of the book; only
  `write start --new` names a file that does not exist yet.
- The developmental edit's whole-book run is not a road retired here: it
  is a book-level pass (Q1a).
- No file is special-cased (owner, 2026-11-26: "there should be no
  special casing"); every essay of the reading order is treated alike.

Never imports: cli, api.
"""

from __future__ import annotations

from pathlib import Path

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


def _essay_file(manuscript: dict, essay: str, road: str,
                new: bool = False) -> tuple[str, bool]:
    """The essay's manuscript-relative path, resolved as the verb resolves
    it, and whether it is an essay of the book (in the reading order).
    Raises LookupError for a file that is not one (a filter or lens, or
    `write start` without --new), ValueError for --new on a file that
    exists."""
    from ..docs import _resolve_relpath, doc_filename

    order = core.reading_order(manuscript)
    try:
        rel = _resolve_relpath(manuscript, essay)
    except LookupError:
        if road == "beats" and new:
            rel = doc_filename(essay)
            if (Path(manuscript["path"]) / rel).exists():
                raise ValueError(f"{rel} already exists — drop --new to "
                                 "rewrite it") from None
            return rel, False
        raise
    if road == "beats" and new:
        raise ValueError(f"{rel} already exists — drop --new to rewrite it")
    from ..structure import refuse_sidecar
    refuse_sidecar(rel)            # the dictionary is refused by name
    if rel not in order:
        raise LookupError(f"'{rel}' is not an essay of this book (not in "
                          "its reading order), so it has no workflow")
    return rel, True


def _check_name(manuscript: dict, road: str, name: str) -> None:
    if road == "filter":
        from .. import filters
        filters.load_filter(manuscript, name)
    elif road == "lens":
        from .. import lenses
        lenses.load_lens(manuscript, name)


def open_instance(db: Database, manuscript: dict, file: str) -> dict | None:
    row = core._open_instance(db, manuscript, file)
    return core._instance(row) if row is not None else None


def _start_kind(db: Database, manuscript: dict, file: str,
                exists: bool) -> str:
    if not exists:
        return "new"
    done = db.one("SELECT 1 FROM workflow_instances WHERE manuscript_id = ? "
                  "AND file = ? AND step = 'done' AND finished_at IS NOT NULL "
                  "LIMIT 1", (manuscript["id"], file))
    return "re-review" if done is not None else "refine"


def _earlier_skip(db: Database, inst: dict, road: str, name: str) -> dict | None:
    for d in core.decisions(db, inst):
        detail = d["detail"]
        if d["kind"] == "skip" and detail.get("road") == road \
                and detail.get("name", "") == name:
            return d
    return None


def how_to(manuscript: dict, file: str, road: str, inst: dict | None,
           start_kind: str, name: str = "", skip_hint: str = "") -> str:
    """The message a closed road prints: where the essay's workflow
    stands and the command that starts or continues it."""
    m = f" -m {manuscript['name']}"
    step, what = ROADS[road]
    what = f"{what} ({name})" if name else what
    if inst is None:
        head = (f"{what} on {file} goes through the essay's workflow now "
                f"(letter, construction, wrap-up, filters, lint). {file} has "
                f"no open workflow. Start it: authorlm workflow start "
                f"{file.removesuffix('.md')} --kind {start_kind}{m}")
    else:
        where = (f"{file} is in its workflow ({inst['kind']} #{inst['number']}"
                 f", {inst['id']}) at the {inst['step']} step")
        if inst["step"] == step:
            where += ("; the old road does not carry the instance's lists, "
                      "engine or filter selection, so even here it runs "
                      "outside the workflow")
        head = (f"{what} belongs to the {step} step of the essay's workflow; "
                f"{where}. Continue it: authorlm workflow show {inst['id']}{m}")
    return head + ". " + (skip_hint or (
        f"To run it anyway, outside the workflow, add {SKIP_FLAG} "
        "\"<one sentence: why>\"; the reason is recorded on the essay's "
        "workflow."))


def enter(db: Database, manuscript: dict, essay: str, road: str, *,
          name: str = "", reason: str | None = None, via: str = "",
          dry_run: bool = False, skip_hint: str = "",
          new: bool = False) -> dict:
    """May `road` run on `essay` now? Returns {how, file, instance,
    decision, start_kind, road, name, reason, via}: how is
    `skipped-before` (an earlier skip for this road and name on the open
    instance) or `skip` (this call's reason; recorded unless `dry_run`).
    Raises SentToWorkflow, with how to start or continue the workflow,
    when there is no reason; LookupError for a file that is not an essay
    of the book or an unknown filter or lens. `dry_run` checks everything
    and writes nothing; pass its result to `record` once the verb's own
    checks have passed."""
    if road not in ROADS:
        raise ValueError(f"road must be one of {', '.join(ROADS)}")
    file, exists = _essay_file(manuscript, essay, road, new)
    _check_name(manuscript, road, name)
    inst = open_instance(db, manuscript, file)
    plan = {"file": file, "instance": inst, "decision": None,
            "start_kind": None if inst else _start_kind(db, manuscript, file,
                                                        exists),
            "road": road, "name": name, "via": via or road, "reason": None}
    if inst is not None:
        earlier = _earlier_skip(db, inst, road, name)
        if earlier is not None:
            return {**plan, "how": "skipped-before", "decision": earlier}
    if reason is None or not reason.strip():
        raise SentToWorkflow(how_to(manuscript, file, road, inst,
                                    plan["start_kind"], name, skip_hint))
    plan.update(how="skip", reason=core._one_sentence(
        reason, "running outside the workflow"))
    return plan if dry_run else record(db, manuscript, plan)


def record(db: Database, manuscript: dict, plan: dict) -> dict:
    """Write what a dry-run `enter` planned: for `skip`, the instance
    (started when there was none) and the skip decision, in one
    transaction. `skipped-before` writes nothing."""
    if plan["how"] != "skip" or plan["decision"] is not None:
        return plan
    road, name, file, reason = (plan["road"], plan["name"], plan["file"],
                                plan["reason"])
    with db.transaction():
        inst = open_instance(db, manuscript, file)
        started = inst is None
        if started:
            inst = core.start(db, manuscript, file, plan["start_kind"])
        else:
            earlier = _earlier_skip(db, inst, road, name)
            if earlier is not None:
                return {**plan, "how": "skipped-before", "instance": inst,
                        "decision": earlier}
        decision = core._record_decision(
            db, inst, "skip", reason=reason, words=reason, detail={
                "road": road, "name": name, "file": file,
                "step": inst["step"], "outside_workflow": True,
                "via": plan["via"], "instance_started": started,
                **({"why_started": f"started so the skip on "
                                   f"{ROADS[road][1]} has an instance to "
                                   "be recorded on"} if started else {})})
    return {**plan, "instance": inst, "decision": decision}
