"""The Workflows page: the per-essay editing workflow in the app.

Design: docs/plans/filter-lens-consolidation.md §11.9 (every decision with
the owner's words) and §12 row 5. The page is `/api/workflow` on the
audiobook server (`audiobook_server.py`); `dispatch` is its transport, as
`style_page.dispatch` is the Style laws page's.

Everything the page writes goes through the workflow package
(`authorlm.workflow.core`): instances, turns, decisions, blurbs, the step
gates. Model turns go through the engine contract
(`workflow.contract.make_engine`, filled by the engines package; tests
register a fake). The accepted text of each unit lives on the session
record (the turns); the page writes the essay file and its Doc tab only
through `workflow.landing` (when a step ends, on Land, and when a reopen
takes a landed unit back), never directly.

What the page shows for an instance, by step:

- start: the three lists (reference, voice, aware), the engine's
  recommendations with a reason each, accept or decline each (Q1, Q3).
- letter: the findings with their tiers, accept / reject / defer with a
  reason; the keep list, the passes needed or not, the paragraph notes
  (Q4, Q21d, Q21e); a reply box to discuss the letter. From the letter
  step to the filters (core.LETTER_STEPS) the author edits any finding's
  fields and the Keep list of the letter in force (`finding_edit`, `keep`;
  core.edit_finding, core.set_keep): the model's words stay on the record,
  and every later turn works from his. He may revise the letter there too
  (`revise_letter`, with an optional note): the new letter, written
  against the working text, becomes the letter in force; the one it
  replaces stays on the record as superseded; the walk continues where it
  is once the new findings are ruled, and a new finding that quotes an
  accepted unit proposes reopening it (`reopen_proposals`).
- start over (`restart`): the instance is closed as abandoned with one
  sentence of reason and a new one starts with its choices and brief
  (core.restart).
- construction: the walk, one unit at a time (paragraph or section; Q8,
  Q19), or beat by beat with a plan step per beat (Q18). Each unit's form:
  before, the reason, after; accept / reject / redo / edit (Q8a, Q11);
  reopen an accepted unit. A proposal is changes of the kinds in
  `workflow.edits` (replace, insert, cut, move), several at one unit each
  their own form (`<label>:c<k>`); the author also keeps, cuts or moves a
  unit himself (`act` keep, cut, move). Beat by beat inserts each beat's
  text where its plan says and leaves the old text standing, then puts
  each paragraph the beat supersedes to the author (`b<n>:x<k>`) and,
  before the beats conclude, each old paragraph no beat claimed
  (`beats:cover:p<k>`; `_beats_walk`).
- wrapup: removed passages, placeholders, terms to check, principles with
  their scope (Q23, Q24), each item a form.
- filters: the checklist of passes in the standard order, pre-ticked from
  the last instance and the letter (Q12), then the walk by the filter unit.
- lint: `authorlm book lint`'s rules over the working text, run and
  recorded on the instance; then advance.

Every walk works the working text (the file with the record's rulings
applied), and advancing goes by `page_gate`: the walk at its end, the
wrap-up ruled, lint run; anything else is a skip with one sentence.

Skips take one sentence of reason; engine and unit switches take a reason
mid-instance (Q6-Q9). The author's brief can be edited at any step of an
open instance (`brief`), even while a model turn runs: that turn keeps the
brief it started with, the next one reads the new brief. Usage totals per
instance, essay and month (Q7).

The author can talk with the engine at any unit, as under the letter
(`discuss`, `discuss_unit`): the engine answers in words, the exchange is a
`discuss` turn at the unit's step and label (so every later turn sees it),
and the unit's form does not change until a redo, which can carry the
discussion since the form (`act` with `with_discussion`). Every model turn
from the letter step to the filters ends with the letter's findings as the
author now rules them (`core.letter_state`).

Model turns (`MODEL_TURNS`, a redo or bracketed edit in `act`) run as
background jobs when the page asks (`dispatch(background=True)`,
workflow/jobs.py): the request returns at once, the page polls the
instance and shows the job's elapsed time and read requests, and a failed
or timed-out turn stays on the instance with its error and Retry.
Recommend carries the book's essay summaries in its message
(`book_summaries`), as stored; the start step says how many are stale and
offers to bring them up to date first (`refresh_summaries`, a job too).
"""

from __future__ import annotations

import bisect
import contextvars
import json
import re
import tomllib
from datetime import datetime, timezone
from typing import Any

from . import api
from .db import Database
from .workflow import context, contract, core, edits

METHODS = ("overview", "instance", "start", "recommend", "rec", "list",
           "letter", "letter_reply", "finding", "accept_letter", "propose",
           "act", "reopen", "beat_done", "conclude", "wrapup", "principle",
           "filters", "lint", "skip", "advance", "engine", "unit", "blurbs",
           "blurb_add", "blurb_hide", "usage", "estimate", "land", "leave",
           "summaries", "job_dismiss", "brief", "finding_edit", "keep",
           "revise_letter", "reopen_decline", "restart", "discuss",
           "promote")

# The read-only methods: they write nothing (the page polls none of them).
READ_ONLY = ("overview", "instance", "blurbs", "usage", "estimate")

# Tags the author may put in an edit; any other [...] is an instruction
# at that spot (Q8a; `.claude/skills/authorlm/SKILL.md`).
SUPPORTED_TAGS = ("Illustration", "Footnote", "Suggestion")
LETTER_LABEL = "letter"
RECOMMEND_LABEL = "recommend"
WRAPUP_LABEL = "wrapup"
WRAPUP_KINDS = ("removed", "placeholders", "terms", "principles")
TIERS = ("must", "should", "could")


# ------------------------------------------------------------- the passes

def passes() -> list[dict]:
    """The standard order of the filter passes (library/workflow/order.toml):
    the order is `context.standard_order()` (`order = [...]`); each pass's
    stage and starting tick come from its `[pass.<name>]` table. [] when
    the library has none."""
    path = context.library_dir() / "order.toml"
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        data = {}
    meta = data.get("pass") if isinstance(data.get("pass"), dict) else {}
    out = []
    for name in context.standard_order():
        m = meta.get(name) if isinstance(meta.get(name), dict) else {}
        out.append({"name": name, "stage": m.get("stage", ""),
                    "default": m.get("default", "ticked")})
    return out


def _has_audiobook(manuscript: dict) -> bool:
    try:
        from . import audio
        return (audio.audio_dir(manuscript) / audio.CONFIG_FILENAME).exists()
    except Exception:  # noqa: BLE001 — no audio set up means no audiobook
        return False


# ---------------------------------------------------------------- units

_HEADING = re.compile(r"^#{1,6}\s")


def _blocks(text: str) -> list[str]:
    return [b.strip("\n") for b in re.split(r"\n\s*\n", text or "") if b.strip()]


def paragraphs(text: str) -> list[str]:
    """The essay's paragraphs: blocks between blank lines, headings left
    out (a heading is not a unit of prose)."""
    out = []
    for b in _blocks(text):
        lines = [ln for ln in b.split("\n") if not _HEADING.match(ln)]
        body = "\n".join(lines).strip()
        if body:
            out.append(body)
    return out


def sections(text: str) -> list[str]:
    """The text under each subheading (§11.4). The essay's title line is
    not a section; text before the first subheading is one. An essay
    without subheadings has no sections: it goes by paragraph."""
    lines = (text or "").split("\n")
    if lines and re.match(r"^#\s", lines[0]):
        lines = lines[1:]
    if not any(re.match(r"^#{2,6}\s", ln) for ln in lines):
        return []
    out, cur = [], []
    for ln in lines:
        if re.match(r"^#{2,6}\s", ln) and cur:
            if "\n".join(cur).strip():
                out.append("\n".join(cur).strip())
            cur = []
        cur.append(ln)
    if "\n".join(cur).strip():
        out.append("\n".join(cur).strip())
    return out


def units_of(text: str, unit: str) -> tuple[str, list[str]]:
    """(the unit actually used, the units). Section falls back to
    paragraph when the essay has no subheadings (§11.4)."""
    if unit == "section":
        secs = sections(text)
        if secs:
            return "section", secs
    return "paragraph", paragraphs(text)


def _label(unit: str, n: int) -> str:
    return f"{'s' if unit == 'section' else 'p'}{n}"


# ----------------------------------------------------------- unit state

def _by_label(turns: list[dict], step: str) -> dict[str, list[dict]]:
    """Each unit's turns at `step`, in order. The discussion of a unit
    (`discuss` turns) is not part of its form's history: talking about a
    proposal never changes it (`threads` holds the discussion)."""
    out: dict[str, list[dict]] = {}
    for t in turns:
        if t["step"] == step and t["unit_label"] and t["action"] != DISCUSS:
            out.setdefault(t["unit_label"], []).append(t)
    return out


DISCUSS = "discuss"


def threads(turns: list[dict]) -> dict[tuple[str, str], list[dict]]:
    """The discussion of each unit, by (step, label): the author's words
    and the engine's reply, oldest first."""
    out: dict[tuple[str, str], list[dict]] = {}
    for t in turns:
        if t["action"] == DISCUSS and t["unit_label"]:
            out.setdefault((t["step"], t["unit_label"]), []).append(
                {"seq": t["seq"], "author": t["author_text"],
                 "reply": t["reply"], "at": t["created_at"]})
    return out


def _since_form(db: Database, inst: dict, step: str, label: str) -> list[dict]:
    """The unit's discussion since its latest form (the turns a Redo with
    this discussion folds in)."""
    turns = core.turns(db, inst)
    hist = _by_label(turns, step).get(label, [])
    last = max((t["seq"] for t in hist if t["engine"]), default=0)
    return [d for d in threads(turns).get((step, label), []) if d["seq"] > last]


def _turn_changes(turn: dict | None) -> list[dict]:
    return list(((turn or {}).get("edits") or {}).get("changes") or [])


def _with_text(changes: list[dict], text: str) -> list[dict]:
    """The form's changes with the author's (or the engine's) text in
    place of the new text: one change keeps its kind and anchor (a cut
    with text becomes a replace of what it cut); anything else becomes a
    replace of the whole unit."""
    text = (text or "").strip("\n")
    if len(changes) == 1:
        c = dict(changes[0])
        if c["kind"] == "cut":
            c["kind"] = "replace"
        c["text"] = text
        return [c]
    return [{"kind": "replace", "original": "", "where": "", "quote": "",
             "text": text}]


WHOLE_UNIT = [{"kind": "replace", "original": "", "where": "", "quote": "",
               "text": ""}]


def _form_changes(turn: dict, base: list[dict]) -> list[dict]:
    """A model turn's changes: its own list (`changes`), else its
    `proposed` text as the unit's base change (the whole unit, for a unit
    of the walk), else none (it passes)."""
    ch = _turn_changes(turn)
    if ch:
        return ch
    if (turn.get("proposed") or "").strip():
        return _with_text(base, turn["proposed"])
    return []


def _display_text(changes: list[dict], before: str) -> str:
    """The unit's text after its changes, for the form and the record:
    the new text of a lone replace or insert, "" for a cut."""
    if len(changes) == 1 and changes[0]["kind"] in ("replace", "insert"):
        c = changes[0]
        if c["kind"] == "replace" and c["original"] and before:
            hit = edits.find(before, c["original"])
            if hit:
                return before[:hit[0]] + c["text"] + before[hit[1]:]
        return c["text"]
    if len(changes) == 1 and changes[0]["kind"] in ("cut", "move") and (
            not changes[0]["original"] or edits.norm(changes[0]["original"])
            == edits.norm(before)):
        return "" if changes[0]["kind"] == "cut" else before
    return before


def unit_state(history: list[dict], before: str = "",
               default: dict | None = None,
               base: list[dict] | None = None) -> dict:
    """Where one unit stands, from its turns in order.

    - pending: the model's last turn proposes changes; the form is open.
    - passed: the model's last turn proposes nothing (a unit with no
      change passes without stopping, Q8). A cut is its own change kind
      (workflow/edits.py), never an empty proposal.
    - accepted / rejected / edited: the author ruled. Rejecting keeps the
      original; with no original (a new essay) it drops the unit (Q11).
    - open: no turn yet.

    `default` is a form the walk makes without a model turn (a leftover
    after a beat: {changes, reason}): the unit is pending on it until the
    author rules it or the engine proposes again. Each state carries the
    form's `changes` and, once ruled in, the changes `applied`. `base` is
    the change a proposal of whole text makes (default: it replaces the
    whole unit; a beat's new text is an insert at its anchor).
    """
    base = (default or {}).get("changes") or base or WHOLE_UNIT
    model_turns = [t for t in history if t["engine"]]
    form = model_turns[-1] if model_turns else None
    out: dict[str, Any] = {}
    if form:
        out["form"] = {"seq": form["seq"], "reason": form["reason"],
                       "proposed": form["proposed"], "text": form["reply"],
                       "engine": form["engine"], "model": form["model"],
                       "changes": _form_changes(form, base),
                       "placement": (form.get("edits") or {}).get("placement")}
    elif default:
        out["form"] = {"seq": default.get("seq", 0), "reason": default.get("reason", ""),
                       "proposed": _display_text(base, before), "text": "",
                       "engine": "", "model": "", "changes": base,
                       "placement": None, "mechanical": True}
    changes = (out.get("form") or {}).get("changes") or []
    if not history:
        if default:
            return {**out, "state": "pending", "text": before, "changes": changes}
        return {"state": "open", "text": before}
    last = history[-1]
    out.update({"turns": len(history), "seq": last["seq"],
                "ruled_before": last.get("unit_before") or next(
                    (t.get("unit_before") for t in reversed(history)
                     if t.get("unit_before")), ""),
                "changes": changes})
    if last["action"] == "accept":
        applied = (_turn_changes(last) or changes
                   or _with_text(base, last["author_text"]))
        text = (last["author_text"] if not _turn_changes(last) and last["author_text"]
                else _display_text(applied, before))
        out.update(state="accepted", text=text, applied=applied)
    elif last["action"] == "reject":
        out.update(state="rejected" if before else "dropped", text=before)
    elif last["action"] == "edit" and not last["engine"]:
        applied = _turn_changes(last) or _with_text(changes or base,
                                                    last["author_text"])
        out.update(state="edited", applied=applied,
                   text=(last["author_text"] if not _turn_changes(last)
                         else _display_text(applied, before)))
    elif last["engine"] and changes:
        out.update(state="pending", text=before)
    elif last["engine"]:
        out.update(state="passed", text=before)
    else:
        out.update(state="open", text=before)
    return out


# "split": a unit whose form is several changes, each its own form (its
# children, `<label>:c<k>`). "gone": a unit not yet worked whose text an
# earlier change took out of the essay (a cut or a move).
RESOLVED = ("accepted", "rejected", "dropped", "edited", "passed", "split",
            "gone")
# The states whose changes are in the working text.
CHANGED = ("accepted", "edited")
# Beat by beat has no end of its own: the author concludes it (Q18).
CONCLUDE_LABEL = "beats:end"
# Before concluding, what the beats left standing that no beat claimed is
# put to the author, one form per old paragraph (`beats:cover:p<k>`).
COVER_LABEL = "beats:cover"
_COVER = re.compile(r"^beats:cover:p(\d+)$")
LINT_LABEL = "lint"


# --------------------------------------------------------- working text
#
# The page never writes the essay file; the text the author has accepted
# lives on the session record. The working text is that record applied to
# the file: construction's rulings, then the wrap-up's, then the filters'.
# Every later walk starts from it (Q8: "only the kept text is carried
# forward"), and it is the essay the engine is sent ("as it now stands").

def _apply_unit(text: str, u: dict, cursor: int = 0) -> str:
    """The unit's applied changes in `text` (workflow/edits.py), its pairs
    on the unit for landing; a change whose original or anchor is gone is
    not applied, and the unit says why (`unapplied`)."""
    if u["state"] not in CHANGED:
        return text
    try:
        out, pairs, _pos = edits.apply_all(text, u.get("applied") or [],
                                           cursor=cursor, unit=u.get("before", ""))
    except edits.NotApplied as err:
        u["unapplied"] = str(err)
        return text
    u["pairs"] = pairs
    return out


def _walk_over(text: str, unit: str, mode: str, hist: dict) -> dict:
    """One walk over `text` by `unit` (or beat by beat), each unit with its
    state and form from `hist`; `text` in the result is the working text
    after the walk's rulings, applied in order, each by its text (a unit's
    "before" is found in the text the units before it left)."""
    if mode == "beats":
        return _beats_walk(text, unit, hist)
    used, parts = units_of(text, unit)
    units = []
    out = text
    for i, part in enumerate(parts, start=1):
        label = _label(used, i)
        h = hist.get(label, [])
        st = unit_state(h, part)
        u = {"label": label, "kind": used, "n": i, "before": part, **st}
        hit = edits.find(out, part)
        if hit is None and not h:
            u["state"] = "gone"
            units.append(u)
            continue
        cursor = hit[0] if hit else 0
        form = st.get("form") or {}
        if len(form.get("changes") or []) >= 2 and st["state"] not in (
                "accepted", "edited", "rejected", "dropped"):
            # Several changes: each is its own form (owner, 2026-11-27:
            # "forms are for what have you deleted, what have you removed,
            # what have you changed").
            u["state"] = "split"
            kids = []
            for k, c in enumerate(form["changes"], start=1):
                cl = f"{label}:c{k}"
                ch = [t for t in hist.get(cl, []) if t["seq"] > form["seq"]]
                cst = unit_state(ch, part, default={
                    "changes": [c], "reason": form["reason"], "seq": form["seq"]})
                cu = {"label": cl, "kind": "change", "parent": label, "n": k,
                      "before": part, **cst}
                if not any(t["engine"] for t in ch):
                    cu["form"].update(engine=form["engine"], model=form["model"],
                                      mechanical=False, text=form["text"])
                out = _apply_unit(out, cu, cursor)
                kids.append(cu)
            u["children"] = [c["label"] for c in kids]
            units += [u] + kids
            continue
        out = _apply_unit(out, u, cursor)
        units.append(u)
    pending = [u["label"] for u in units if u["state"] == "pending"]
    nxt = pending[0] if pending else next(
        (u["label"] for u in units if u["state"] == "open"), None)
    return {"mode": "walk", "unit": used, "asked_unit": unit, "units": units,
            "next": nxt, "end": nxt is None, "fallback": used != unit,
            "text": out}


def _segments(turns: list[dict], decisions: list[dict], step: str,
              inst: dict) -> list[tuple[dict, list[dict]]]:
    """The step's turns cut at each unit switch (Q8: the unit "can be
    changed at any point"), with the unit and mode of each stretch. A
    label means a different unit under a different size, so each stretch
    is walked on the text the stretch before it left."""
    column = "filter_unit" if step == "filters" else "unit"
    mode_now = inst["construction_mode"] if step == "construction" else "walk"
    switches = []
    for d in decisions:
        det = d.get("detail") or {}
        frm = det.get("from") or {}
        if d["kind"] == "unit-switch" and det.get("phase") == step and (
                column in frm or (step == "construction"
                                  and "construction_mode" in frm)):
            switches.append(d)
    settings = [{"unit": inst[column], "mode": mode_now}]
    for d in reversed(switches):
        frm = d["detail"].get("from") or {}
        prev = dict(settings[0])
        if column in frm:
            prev["unit"] = frm[column]
        if step == "construction" and "construction_mode" in frm:
            prev["mode"] = frm["construction_mode"]
        settings.insert(0, prev)
    bounds = [d["created_at"] for d in switches]
    stretches: list[list[dict]] = [[] for _ in settings]
    for t in turns:
        if t["step"] == step and t["unit_label"]:
            stretches[bisect.bisect_right(bounds, t["created_at"])].append(t)
    return list(zip(settings, stretches))


def _phase_walk(db: Database, manuscript: dict, inst: dict, step: str,
                turns: list[dict] | None = None,
                decisions: list[dict] | None = None) -> dict:
    views = phase_stretches(db, manuscript, inst, step, turns, decisions)
    earlier: list[dict] = []
    for i, v in enumerate(views[:-1], start=1):
        earlier += [{"label": u["label"], "kind": u.get("kind", ""),
                     "state": u["state"], "text": u.get("text", ""),
                     "stretch": i}
                    for u in v["units"]
                    if u["state"] in RESOLVED
                    and u["state"] not in ("passed", "split", "gone")]
    view = views[-1]
    view["earlier"] = earlier
    return view


def phase_stretches(db: Database, manuscript: dict, inst: dict, step: str,
                    turns: list[dict] | None = None,
                    decisions: list[dict] | None = None) -> list[dict]:
    """Each stretch of the walk at `step` (cut at unit switches), in
    order: the walk over the text the stretch before it left, with that
    text as `base`. Construction starts from the instance's base text
    (`core.base_text`), never the live file, so a label always names the
    same text; the filters start from the text the wrap-up left."""
    turns = core.turns(db, inst) if turns is None else turns
    decisions = core.decisions(db, inst) if decisions is None else decisions
    if step == "construction":
        text = core.base_text(manuscript, inst)
    else:
        text = _after_wrapup(db, manuscript, inst, turns, decisions)["text"]
    views = []
    for st, ts in _segments(turns, decisions, step, inst):
        view = _walk_over(text, st["unit"], st["mode"], _by_label(ts, step))
        view["base"] = text
        views.append(view)
        text = view["text"]
    return views


def _walk(db: Database, manuscript: dict, inst: dict, step: str) -> dict:
    """The units of the walk at `step` (construction or filters), each with
    its state and form, the next label to work, and the working text."""
    return _phase_walk(db, manuscript, inst, step)


def _after_wrapup(db: Database, manuscript: dict, inst: dict,
                  turns: list[dict] | None = None,
                  decisions: list[dict] | None = None) -> dict:
    """Construction's working text with the wrap-up's accepted items in
    place: a placeholder or a term is replaced where its text stands. An
    accepted item whose text is not in the essay (a removed passage placed
    elsewhere) is listed as unplaced, never guessed at."""
    turns = core.turns(db, inst) if turns is None else turns
    text = _phase_walk(db, manuscript, inst, "construction", turns,
                       decisions)["text"]
    unplaced = []
    for it in _wrapup(turns)["items"]:
        if it["state"] not in CHANGED:
            continue
        if it["before"] and it["before"] in text:
            text = text.replace(it["before"], it.get("text", ""), 1)
        else:
            unplaced.append({"label": it["label"], "kind": it["kind"],
                             "text": it.get("text", "")})
    return {"text": re.sub(r"\n{3,}", "\n\n", text), "unplaced": unplaced}


def working_text(db: Database, manuscript: dict, inst: dict) -> str:
    """The essay as it now stands for this instance: the file, with every
    ruling so far applied (construction, wrap-up, filters)."""
    step = inst["step"]
    if step in ("start", "letter"):
        return core._files(manuscript).get(inst["file"], "")
    if step == "construction":
        return _walk(db, manuscript, inst, "construction")["text"]
    if step == "wrapup":
        return _after_wrapup(db, manuscript, inst)["text"]
    return _walk(db, manuscript, inst, "filters")["text"]


# A beat's labels: its plan; its new text whole (`insert`; `section` on
# records from before beats were inserted, read the same way) or paragraph
# by paragraph (`p<k>`); a leftover form per old paragraph it supersedes
# (`x<k>`, k the paragraph's number in the stretch's base text); done.
_BEAT = re.compile(r"^b(\d+):(plan|section|insert|done|p(\d+)|x(\d+))$")


def _last_block(text: str) -> str:
    bl = edits.blocks(text or "")
    return text[bl[-1][0]:bl[-1][1]] if bl else ""


def _plan_placement(units: list[dict], label: str) -> dict | None:
    u = next((x for x in units if x["label"] == label), None)
    return ((u or {}).get("form") or {}).get("placement")


def _beats_walk(essay: str, unit: str, hist: dict) -> dict:
    """Beat by beat (Q18), each beat inserted, the old text left standing
    (owner, 2026-11-27: "it makes more sense to insert the beat at the
    beginning of the essay and leave the rest of the essay intact").

    For each beat: its plan, which says where its new text goes in and
    which old paragraphs it takes the place of (`placement`); then its new
    text, whole or paragraph by paragraph, inserted there (an insert,
    never a replacement of anything); then, once the text is in, one
    leftover form per paragraph it supersedes: cut (the form's proposal),
    keep, merge into the beat or move it (owner: "after insertion of a
    beat, you remove the lines from the essay that don't belong"). Beats
    come from the plans, never from the essay's headings. Before the
    author concludes, one coverage form per old paragraph still standing
    that no beat claimed: keep, cut or move (owner: "at the end ...
    whatever we've left over, have we covered everything correctly?").
    A paragraph the engine passes (nothing more to write) completes a
    beat written paragraph by paragraph; the author concludes the walk."""
    base_paras = paragraphs(essay)
    beats: dict[int, dict] = {}
    for label, h in hist.items():
        m = _BEAT.match(label)
        if not m:
            continue
        b = int(m.group(1))
        beat = beats.setdefault(b, {"plan": None, "text": [], "done": False,
                                    "left": {}})
        if m.group(2) == "plan":
            beat["plan"] = (label, h)
        elif m.group(2) == "done":
            beat["done"] = True
        elif m.group(4):
            beat["left"][int(m.group(4))] = h
        else:
            k = int(m.group(3)) if m.group(3) else 0
            beat["text"].append((k, label, h))
    concluded = bool(hist.get(CONCLUDE_LABEL))
    cover_asked = bool(hist.get(COVER_LABEL))
    text = essay
    units: list[dict] = []
    claimed: set[int] = set()
    prev: dict | None = None  # after the previous beat's new text
    complete: dict[int, bool] = {}
    for b in sorted(beats):
        beat = beats[b]
        placement = None
        if beat["plan"]:
            label, h = beat["plan"]
            st = unit_state(h, "")
            placement = (st.get("form") or {}).get("placement")
            units.append({"label": label, "kind": "plan", "beat": b,
                          "before": "", "placement": placement, **st})
        if placement and placement.get("where"):
            anchor = {"where": placement["where"], "quote": placement.get("quote", "")}
        else:
            anchor = prev or {"where": "start", "quote": ""}
        whole = any(k == 0 for k, _l, _h in beat["text"])
        last_state = ""
        for k, label, h in sorted(beat["text"]):
            ins = [{"kind": "insert", "original": "", "where": anchor["where"],
                    "quote": anchor.get("quote", ""), "text": ""}]
            st = unit_state(h, "", base=ins)
            u = {"label": label, "kind": "insert" if k == 0 else "paragraph",
                 "beat": b, "n": k, "before": "", "anchor": dict(anchor), **st}
            last_state = st["state"]
            if st["state"] in CHANGED and (st.get("text") or "").strip():
                # Always an insert at the beat's anchor, whatever the record
                # holds (an old `b<n>:section` ruling included): a beat
                # never replaces the old text (owner, 2026-11-27).
                ch = {**ins[0], "text": st["text"]}
                try:
                    text, pairs, _p = edits.apply(text, ch)
                except edits.NotApplied:
                    # The anchor is gone (an earlier ruling took it out):
                    # after the previous beat, else at the start.
                    ch.update(prev or {"where": "start", "quote": ""})
                    u["anchor"] = {"where": ch["where"], "quote": ch["quote"]}
                    text, pairs, _p = edits.apply(text, ch)
                u.update(applied=[ch], pairs=pairs)
                anchor = {"where": "after", "quote": _last_block(st["text"])}
            units.append(u)
        if beat["text"] and anchor.get("where") == "after":
            prev = anchor
        done_text = (any(k == 0 and unit_state(h, "")["state"] in RESOLVED
                         for k, _l, h in beat["text"]) if whole
                     else beat["done"] or last_state == "passed")
        complete[b] = done_text
        sup = (placement or {}).get("supersedes") or []
        for quote in sup:
            k = edits.opening_match(base_paras, quote)
            if k is None or k in claimed:
                continue
            claimed.add(k)
            if not (done_text or cover_asked or concluded):
                continue
            label, para = f"b{b}:x{k + 1}", base_paras[k]
            h = beat["left"].get(k + 1, [])
            if not h and edits.find(text, para) is None:
                continue  # already taken out by an earlier ruling
            st = unit_state(h, para, default={
                "changes": [{"kind": "cut", "original": "", "where": "",
                             "quote": "", "text": ""}],
                "reason": (f"Beat {b}'s plan says its new text takes the place "
                           "of this paragraph. Cut it, keep it, merge it into "
                           "the beat, or move it.")})
            u = {"label": label, "kind": "leftover", "beat": b, "n": k + 1,
                 "before": para, **st}
            text = _apply_unit(text, u)
            units.append(u)
    # What the beats left standing that no beat claimed (computed from the
    # base paragraphs, mechanically).
    unclaimed = [k for k, para in enumerate(base_paras)
                 if k not in claimed and edits.find(text, para) is not None]
    if cover_asked:
        for k, para in enumerate(base_paras):
            if k in claimed:
                continue
            label = f"{COVER_LABEL}:p{k + 1}"
            h = hist.get(label, [])
            if not h and k not in unclaimed:
                continue
            st = unit_state(h, para)
            u = {"label": label, "kind": "cover", "n": k + 1, "before": para, **st}
            text = _apply_unit(text, u)
            units.append(u)
    pending = [u["label"] for u in units if u["state"] == "pending"]
    cur = max(beats) if beats else 1
    nxt = pending[0] if pending else None
    if nxt is None and not concluded:
        if cover_asked:
            nxt = next((u["label"] for u in units if u["kind"] == "cover"
                        and u["state"] == "open"), None)
        elif cur not in beats or beats[cur]["done"]:
            cur = cur + 1 if cur in beats else cur
            nxt = f"b{cur}:plan"
        elif not beats[cur]["plan"] or unit_state(beats[cur]["plan"][1])["state"] not in RESOLVED:
            nxt = f"b{cur}:plan"
        elif unit == "section":
            nxt = None if complete.get(cur) else f"b{cur}:insert"
        else:
            last = max(beats[cur]["text"], default=None)
            if last and unit_state(last[2])["state"] == "passed":
                nxt = None  # the engine has nothing more for this beat
            else:
                k = (last[0] if last else 0) + 1
                nxt = f"b{cur}:p{k}"
    if text.strip() and not text.endswith("\n"):
        text += "\n"
    return {"mode": "beats", "unit": unit, "asked_unit": unit,
            "units": units, "next": nxt, "beat": cur,
            "paragraphs_before": len(base_paras),
            "unclaimed": [k + 1 for k in unclaimed],
            "cover_asked": cover_asked,
            "end": concluded and not pending,
            "concluded": concluded, "fallback": False,
            "beat_open": not concluded and cur in beats and not beats[cur]["done"],
            "text": text}


def _unit_before(db: Database, manuscript: dict, inst: dict, step: str,
                 label: str, walk: dict | None = None) -> str:
    if step == "wrapup":
        item = next((i for i in _wrapup(core.turns(db, inst))["items"]
                     if i["label"] == label), None)
        if item is None:
            raise LookupError(f"no wrap-up item '{label}'")
        return item["text"]
    walk = walk or _walk(db, manuscript, inst, step)
    hit = next((u for u in walk["units"] if u["label"] == label), None)
    if hit is not None:
        return hit["before"]
    if walk["mode"] == "beats" and label == walk["next"]:
        # A beat's plan and its new text have no original: the new text is
        # inserted, and the old text stays (`_beats_walk`).
        return ""
    raise LookupError(f"no unit '{label}' at {step}")


# --------------------------------------------------------------- engines

def _engine(inst: dict):
    """The instance's engine, from the contract's registry. Importing the
    engines package fills the registry (`engines.register_with_workflow`);
    a test registers its fake first, and a later import does not undo it
    because the package registers only once, at its first import."""
    try:
        from . import engines  # noqa: F401
    except ImportError:
        pass
    return contract.make_engine(inst["engine"], inst["model"])


# The workspace the page was opened on (`dispatch(..., workspace=)`): the
# engines look the book up by it (`engines.base.manuscript_of`), so a server
# run with `-w <other>` reaches its own book, not the default workspace's.
_WORKSPACE: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "workflow_page_workspace", default=None)


def _road(db: Database, manuscript: dict):
    """The Doc road landings go through (`workflow.landing`), for the
    workspace the page was opened on."""
    from .workflow import landing
    return landing.make_road(db, manuscript, _WORKSPACE.get())


def _context_extra(db: Database, manuscript: dict, inst: dict) -> dict:
    """What the page adds to the instance context: the essay as it now
    stands (the working text, not the untouched file), the workspace and
    the manuscript's name."""
    return {"essay": working_text(db, manuscript, inst),
            "workspace": _WORKSPACE.get(), "manuscript": manuscript["name"]}


def _send(db: Database, manuscript: dict, inst: dict, action: str,
          message: str, *, author_text: str | None = None,
          blurbs: list[str] | None = None, unit_label: str = "",
          unit_before: str = "") -> dict:
    """One model turn, recorded with the engine's session ref in the same
    transaction (`core.send_turn`)."""
    return core.send_turn(db, manuscript, inst, action, message,
                          engine=_engine(inst), author_text=author_text,
                          unit_before=unit_before,
                          blurbs=blurbs, unit_label=unit_label,
                          context_extra=_context_extra(db, manuscript, inst))


# ------------------------------------------------------------- the letter

_SECTION = re.compile(
    r"^\s*(?:#+\s*)?[*_]{0,2}(findings|keep|passes|paragraph notes|blurbs)"
    r"[*_]{0,2}\s*:?[*_]{0,2}\s*(.*)$", re.I)
_FINDING = re.compile(r"^(\s*)(#{1,4}\s*)?(?:\*\*)?(finding\s+)?(\d+)[.):]\s*(.*)$", re.I)
_FIELD = re.compile(r"^\s*[-*]?\s*\*{0,2}(where|what is wrong|why it matters|"
                    r"direction|tier|continues)\s*:\*{0,2}\s*(.*)$", re.I)
_FIELD_KEY = {"where": "where", "what is wrong": "what",
              "why it matters": "why", "direction": "direction", "tier": "tier",
              "continues": "continues"}


def _continues(text: str) -> str:
    """The earlier finding a revised letter's finding carries on ("F3",
    "Finding 3", "continues F3"), or "" (new, none, or not said)."""
    m = re.search(r"\b(?:F|finding\s*)(\d+)\b", str(text or ""), re.I)
    return f"F{m.group(1)}" if m else ""


def _bullets(lines: list[str]) -> list[str]:
    out = []
    for ln in lines:
        s = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s+", "", ln).strip()
        if s:
            out.append(s)
    return out


def _tier(text: str) -> str:
    m = re.search(r"\b(must|should|could)\b", text or "", re.I)
    return m.group(1).lower() if m else ""


def parse_letter(text: str, pass_names: list[str]) -> dict:
    """The letter's parts from the engine's reply text: findings (each with
    where, what, why, direction, tier), keep, passes (needed or not, with
    the clause of reason), paragraph notes, blurbs. The reply is Markdown
    in the shape letter.md's Output asks for, or a JSON object with the
    same keys."""
    data = _json_in(text)
    if isinstance(data, dict) and "findings" in data:
        findings = []
        for i, f in enumerate(data.get("findings") or [], start=1):
            if isinstance(f, str):
                f = {"title": f}
            f = dict(f)
            f["n"] = i
            f["tier"] = _tier(str(f.get("tier", "")))
            f["continues"] = _continues(f.get("continues", ""))
            findings.append(f)
        passes_ = {}
        for p in data.get("passes") or []:
            if isinstance(p, dict) and p.get("name"):
                passes_[p["name"]] = {"needed": bool(p.get("needed")),
                                      "reason": p.get("reason", "")}
        return {"findings": findings, "keep": list(data.get("keep") or []),
                "passes": passes_,
                "notes": list(data.get("paragraph_notes") or data.get("notes") or []),
                "blurbs": list(data.get("blurbs") or [])}
    parts: dict[str, list[str]] = {"preamble": []}
    cur = "preamble"
    for ln in (text or "").split("\n"):
        m = _SECTION.match(ln)
        if m and not _FIELD.match(ln):
            cur = m.group(1).lower()
            parts.setdefault(cur, [])
            if m.group(2).strip():
                parts[cur].append(m.group(2))
            continue
        parts.setdefault(cur, []).append(ln)
    findings: list[dict] = []
    sub = 0  # the last number of a numbered list inside the current finding
    blank = True  # the line before was blank
    for ln in parts.get("findings", []):
        fm = _FINDING.match(ln)
        field = _FIELD.match(ln)
        after_blank, blank = blank, not ln.strip()
        if blank:
            sub = 0  # a blank line ends a list inside a finding
        # A finding names itself ("Finding 3", "### 3.") or opens at the
        # margin with the next number (or any number, after a blank line,
        # so a repeated number is still a finding). A "1." inside a finding starts a list
        # of its own, which runs on while its numbers do; an indented or
        # out-of-sequence numbered line belongs to the finding before it.
        # Findings are numbered by position, whatever numbers the reply gives.
        if fm and not field:
            n = int(fm.group(4))
            named = bool(fm.group(2) or fm.group(3))
            if not named and findings and (fm.group(1) or n == 1
                                           or (sub and n == sub + 1)):
                sub = n if not fm.group(1) else sub
            elif named or n == len(findings) + 1 or (after_blank and not fm.group(1)):
                sub = 0
                findings.append({"n": len(findings) + 1,
                                 "title": fm.group(5).strip(" *"), "lines": [ln]})
                continue
        if not findings:
            continue
        findings[-1]["lines"].append(ln)
        if field:
            key = _FIELD_KEY[field.group(1).lower()]
            findings[-1][key] = field.group(2).strip()
    for f in findings:
        f["text"] = "\n".join(f.pop("lines")).strip()
        f["tier"] = _tier(f.get("tier", ""))
        f["continues"] = _continues(f.get("continues", ""))
    passes_ = {}
    for ln in parts.get("passes", []):
        for name in pass_names:
            # The pass's name opens the line and its mark follows it; a
            # name inside another pass's reason ("concision: needed —
            # repetition in the middle") is not it, and "not needed" inside
            # a needed pass's reason does not unmark it.
            m = re.match(rf"^\s*(?:[-*•]|\d+[.)])?\s*[*_`]*{re.escape(name)}[*_`]*"
                         r"\s*[:—–-]\s*[*_`]*\s*(not needed|needed)\b[*_`]*(.*)$",
                         ln, re.I)
            if m and name not in passes_:
                passes_[name] = {"needed": m.group(1).lower() == "needed",
                                 "reason": m.group(2).strip(" *:—-–()_,;.")}
    return {"findings": findings, "keep": _bullets(parts.get("keep", [])),
            "passes": passes_, "notes": _bullets(parts.get("paragraph notes", [])),
            "blurbs": _bullets(parts.get("blurbs", []))}


def _json_in(text: str) -> Any:
    """The first JSON object in a reply's text, or None."""
    text = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    start = text.find("{")
    if start < 0:
        return None
    try:
        return json.JSONDecoder().raw_decode(text[start:])[0]
    except json.JSONDecodeError:
        return None


def _letter_turn(turns: list[dict]) -> dict | None:
    """The letter itself: the latest draft, a model turn labelled letter at
    the letter step with action start (replies are the discussion)."""
    return next((t for t in reversed(turns) if t["step"] == "letter"
                 and t["unit_label"] == LETTER_LABEL and t["engine"]
                 and t["action"] == "start"), None)


def letter_keep(db: Database, inst: dict, turns: list[dict] | None = None) -> dict:
    """The letter's Keep list as it now stands: {keep, letter (the list as
    the letter wrote it), edited}. The author's list (core.set_keep) wins
    over the letter's once he has edited it."""
    lt = _letter_turn(core.turns(db, inst) if turns is None else turns)
    if lt is None:
        return {"keep": [], "letter": [], "edited": False}
    written = parse_letter(lt["reply"], [p["name"] for p in passes()])["keep"]
    mine = core.keep_edit(db, inst, lt["created_at"])
    return {"keep": written if mine is None else mine, "letter": written,
            "edited": mine is not None}


def edit_keep(db: Database, inst: dict, items: list[str],
              actor: str = "chat") -> dict:
    """Replace the letter's Keep list with the author's (letter step only)."""
    if _letter_turn(core.turns(db, inst)) is None:
        raise ValueError("the letter is not written yet; nothing to keep")
    core.set_keep(db, inst, items, letter_keep(db, inst)["keep"], actor=actor)
    return letter_keep(db, inst)


def _letter_edited(db: Database, inst: dict, turns: list[dict]) -> bool:
    """Whether the author has edited a finding or the Keep list of the
    letter in force."""
    lt = _letter_turn(turns)
    since = lt["seq"] if lt else 0
    return any(t["step"] == "letter" and t["action"] == "edit" and t["seq"] > since
               and t["unit_label"].startswith(LETTER_LABEL + ":") for t in turns)


def _earlier_letters(db: Database, inst: dict) -> list[dict]:
    """Each superseded letter: its revision, the step and note that
    replaced it, and its findings with the author's verdicts."""
    out = []
    for d in core.revisions(db, inst):
        det = d["detail"] or {}
        out.append({"revision": det.get("revision"), "step": det.get("step"),
                    "note": det.get("note") or "", "at": d["created_at"],
                    "seq": det.get("superseded_seq"),
                    "findings": [core.finding_view(f) for f in core.findings_of_run(
                        db, inst, det.get("superseded_run") or "")]})
    return out


def _letter_view(db: Database, inst: dict, turns: list[dict]) -> dict:
    lt = _letter_turn(turns)
    names = [p["name"] for p in passes()]
    parsed = parse_letter(lt["reply"], names) if lt else None
    earlier = _earlier_letters(db, inst)
    before = {f["item"]: f for f in (earlier[-1]["findings"] if earlier else [])}
    findings = []
    for row in core.letter_findings(db, inst):
        f = core.finding_view(row)
        cont = core._body(row).get("continues") or ""
        prev = before.get(cont) if cont else None
        # The author's verdict on the earlier finding this one carries on,
        # so re-ruling it is one tap (the page's "Accept again").
        f["continues"] = ({"item": cont, "title": prev["title"],
                           "verdict": prev["verdict"], "reason": prev["reason"]}
                          if prev else None)
        findings.append(f)
    keep = letter_keep(db, inst, turns)
    since = lt["seq"] if lt else 0
    discussion = [{"seq": t["seq"], "action": t["action"],
                   "author": t["author_text"], "reply": t["reply"],
                   "reason": t["reason"], "at": t["created_at"],
                   "earlier": t["seq"] < since}
                  for t in turns if t["step"] == "letter"
                  and t["unit_label"] == LETTER_LABEL
                  and not (t["engine"] and t["action"] == "start")]
    revs = core.revisions(db, inst)
    last = revs[-1]["detail"] if revs else {}
    return {"drafted": lt is not None,
            "redraft": lt is not None and not any(f["verdict"] for f in findings)
            and not _letter_edited(db, inst, turns) and inst["step"] == "letter",
            # Revise: from the letter step to the filters, once a letter is
            # written (owner's words at core.LETTER_STEPS).
            "revise": lt is not None and inst["step"] in core.LETTER_STEPS,
            "open": inst["step"] in core.LETTER_STEPS,
            "revision": len(revs), "earlier": earlier,
            "revised_at": last.get("step") or "",
            "note": lt["author_text"] if lt and revs else "",
            "unruled": [f["item"] for f in findings if not f["verdict"]],
            "text": lt["reply"] if lt else "",
            "findings": findings,
            "keep": keep["keep"], "keep_letter": keep["letter"],
            "keep_edited": keep["edited"],
            "passes": parsed["passes"] if parsed else {},
            "notes": parsed["notes"] if parsed else [],
            "discussion": discussion,
            "accepted_without_findings": any(
                t["step"] == "letter" and t["action"] == "accept"
                and t["unit_label"] == LETTER_LABEL for t in turns)}


def _letter_passes(db: Database, inst: dict) -> dict:
    """The passes the letter marked, from the latest letter of the essay's
    instances (this one first)."""
    names = [p["name"] for p in passes()]
    for cand in [inst] + list(reversed(core.instances(db, {"id": inst["manuscript_id"]},
                                                       inst["file"]))):
        lt = _letter_turn(core.turns(db, cand))
        if lt:
            return parse_letter(lt["reply"], names)["passes"]
    return {}


# --------------------------------------------------------------- wrap-up

def _wrapup(turns: list[dict]) -> dict:
    """The wrap-up's items (Q23, Q24) from the latest wrap-up turn: each
    removed passage, placeholder and term to check is a form item; the
    principles are ticked, not ruled."""
    lt = next((t for t in reversed(turns) if t["step"] == "wrapup"
               and t["unit_label"] == WRAPUP_LABEL and t["engine"]), None)
    if lt is None:
        return {"drafted": False, "items": [], "principles": [], "text": ""}
    data = _json_in(lt["reply"]) or {}
    hist = _by_label(turns, "wrapup")
    items = []
    for kind in WRAPUP_KINDS[:3]:
        for i, raw in enumerate(data.get(kind) or [], start=1):
            raw = raw if isinstance(raw, dict) else {"text": str(raw)}
            label = f"w:{kind}:{i}"
            before = str(raw.get("text") or "")
            h = hist.get(label, [])
            st = unit_state(h, before)
            if "form" not in st:
                # The wrap-up's own proposal is the first form of the item.
                st["form"] = {"seq": lt["seq"], "reason": raw.get("reason", ""),
                              "proposed": raw.get("proposal", ""), "text": "",
                              "engine": lt["engine"], "model": lt["model"]}
                if not h:
                    st["state"] = "pending" if raw.get("proposal") else "open"
            items.append({"label": label, "kind": kind, "before": before, **st})
    principles = [p if isinstance(p, dict) else {"words": str(p), "scope": ""}
                  for p in data.get("principles") or []]
    return {"drafted": True, "items": items, "principles": principles,
            "text": lt["reply"] if not data else ""}


# --------------------------------------------------------------- views

def _month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _usage_for(db: Database, manuscript: dict, inst: dict) -> dict:
    u = core.usage(db, manuscript)
    month = _month()
    return {"instance": u["by_instance"].get(inst["id"], {}),
            "essay": u["by_essay"].get(inst["file"], {}),
            "month": u["by_month"].get(month, {}), "month_name": month,
            "total": u["total"]}


def overview(db: Database, manuscript: dict) -> dict:
    order = core.reading_order(manuscript)
    rows = core.instances(db, manuscript)
    gone = core.abandoned_ids(db, manuscript)
    by_file: dict[str, list[dict]] = {}
    for r in rows:
        by_file.setdefault(r["file"], []).append(
            {"id": r["id"], "kind": r["kind"], "number": r["number"],
             "step": r["step"], "engine": r["engine"], "model": r["model"],
             "created": r["created_at"], "finished": r["finished_at"],
             "abandoned": r["id"] in gone})
    essays = [{"file": f, "instances": by_file.pop(f, [])} for f in order]
    # A new essay's workflow names a file not yet in the reading order.
    essays += [{"file": f, "instances": v, "new": True} for f, v in by_file.items()]
    for e in essays:
        open_ = [i for i in e["instances"] if i["step"] != "done"]
        e["open"] = open_[-1]["id"] if open_ else None
    u = core.usage(db, manuscript)
    return {"manuscript": manuscript["name"], "essays": essays,
            "kinds": list(core.KINDS), "steps": list(core.STEPS),
            "engines": [e for e in core.ENGINES if e not in core.PLANNED_ENGINES],
            "planned_engines": list(core.PLANNED_ENGINES),
            "units": list(core.UNITS), "modes": list(core.MODES),
            "usage": {"month": u["by_month"].get(_month(), {}),
                      "month_name": _month(), "total": u["total"]}}


def _filter_checklist(db: Database, manuscript: dict, inst: dict) -> dict:
    """Every pass in the standard order with its tick (Q12): this
    instance's choice once made; else the last instance's ticks (carried
    on the instance), else the pass's default; a pass the letter marks not
    needed starts unticked, and the page says why."""
    order = passes()
    letter = _letter_passes(db, inst)
    audiobook = _has_audiobook(manuscript)
    carried = list(inst["filters"])
    # The last instance's ticks count only when it chose them; one skipped
    # past its filter step carries [] and is no choice (Q12).
    had_last = False
    if inst.get("carried_from"):
        src = db.one("SELECT filters_confirmed FROM workflow_instances "
                     "WHERE id = ?", (inst["carried_from"],))
        had_last = bool(src and src["filters_confirmed"])
    # A letter revised at the filters opens the selection again
    # (core.supersede_letter): this instance's own choice is the start, the
    # new letter's marks re-tick it, and what the author then chooses
    # applies to the units still ahead.
    revs = [d for d in core.revisions(db, inst) if d["detail"].get("step") == "filters"]
    revised_here = bool(revs)
    out = []
    for p in order:
        name = p["name"]
        why = ""
        if inst["filters_confirmed"]:
            ticked = name in carried
            why = "chosen for this instance"
        elif revised_here:
            ticked = name in carried
            why = ("your choice before the letter was revised" if ticked
                   else "not in your choice before the letter was revised")
        elif had_last:
            ticked = name in carried
            why = "ticked last instance" if ticked else "not ticked last instance"
        else:
            d = p["default"]
            ticked = d == "ticked" or (d == "audiobook" and audiobook)
            why = ("the standard default" if d != "audiobook" else
                   "ticked for a manuscript with an audiobook" if audiobook
                   else "unticked: no audiobook")
        mark = letter.get(name)
        if mark and not inst["filters_confirmed"]:
            if not mark["needed"]:
                ticked = False
                why = f"the letter: not needed — {mark['reason']}".rstrip(" —")
            elif mark["needed"] and not ticked:
                ticked = True
                why = f"the letter: needed — {mark['reason']}".rstrip(" —")
        out.append({"name": name, "stage": p["stage"], "ticked": ticked,
                    "why": why, "letter": mark})
    return {"passes": out, "confirmed": inst["filters_confirmed"],
            "audiobook": audiobook, "revised_here": revised_here}


def instance_view(db: Database, manuscript: dict, ref: str) -> dict:
    inst = core.get_instance(db, manuscript, ref)
    turns = core.turns(db, inst)
    files = core._files(manuscript)
    out: dict[str, Any] = {
        "instance": inst, "gate": page_gate(db, manuscript, inst),
        "next_step": None if inst["step"] == "done" else core._next_step(inst["step"]),
        "decisions": core.decisions(db, inst),
        "since_last": core.since_last(db, inst),
        "order": core.reading_order(manuscript, files),
        "protected": context.is_protected(files, inst["file"]),
        "usage": _usage_for(db, manuscript, inst),
        "blurbs": core.blurb_list(db, manuscript["id"]),
        "supported_tags": list(SUPPORTED_TAGS),
        "turns": [{k: t[k] for k in ("seq", "step", "unit_label", "action",
                                     "author_text", "blurbs", "reply",
                                     "reason", "proposed", "engine", "model",
                                     "usage", "requests", "created_at")}
                  for t in turns],
        "letter": _letter_view(db, inst, turns),
    }
    out["recommendations"] = _recommendations(turns, inst)
    gone = core.abandoned(db, inst)
    out["abandoned"] = ({"reason": gone["reason"], "at": gone["created_at"],
                         "step": gone["detail"].get("step"),
                         "successor": gone["detail"].get("successor")}
                        if gone else None)
    src = (core.abandoned(db, {"id": inst["carried_from"]})
           if inst.get("carried_from") else None)
    out["restarted_from"] = (
        {"id": inst["carried_from"], "reason": src["reason"]}
        if src and src["detail"].get("successor") == inst["id"] else None)
    talk = threads(turns)
    if inst["step"] in ("construction", "filters"):
        out["walk"] = _walk(db, manuscript, inst, inst["step"])
        out["walk"]["reopen"] = reopen_proposals(db, inst, out["walk"])
        for u in out["walk"]["units"]:
            u["thread"] = talk.get((inst["step"], u["label"]), [])
    if inst["step"] == "wrapup":
        out["wrapup"] = _wrapup(turns)
        for u in out["wrapup"]["items"]:
            u["thread"] = talk.get(("wrapup", u["label"]), [])
    if inst["step"] in ("filters", "lint"):
        out["checklist"] = _filter_checklist(db, manuscript, inst)
        out["unplaced"] = _after_wrapup(db, manuscript, inst, turns)["unplaced"]
    if inst["step"] in ("lint", "done"):
        out["lint"] = _lint_view(turns)
    from .workflow import landing
    out["landing"] = landing.status(db, manuscript, inst)
    out["principles"] = [d for d in out["decisions"] if d["kind"] == "principle"]
    out["promotions"] = promotions(db, manuscript, inst)
    out["skipped"] = [d for d in out["decisions"] if d["kind"] == "skip"]
    from .workflow import jobs
    out["job"] = jobs.current(db, inst["id"])
    out["failed_attempts"] = [j for j in jobs.jobs(db, inst["id"])
                              if j["state"] in ("failed", "dismissed")]
    if inst["step"] == "start":
        out["summaries"] = summary_freshness(db, manuscript, inst)
    return out


def promotions(db: Database, manuscript: dict, inst: dict) -> dict:
    """The instance's principles and rulings, each with its law once
    promoted (workflow/promote.py), and what the Promote form offers: the
    aspects and the manuscript's guides (the root first, the default)."""
    from . import styles as st
    from .workflow import promote

    root = st.root_guide(db, manuscript["id"])
    guides = [r["name"] for r in db.all(
        "SELECT name FROM style_guides WHERE manuscript_id = ? "
        "ORDER BY parent IS NOT NULL, name", (manuscript["id"],))]
    return {"decisions": promote.promotable(db, inst),
            "aspects": list(st.PROSE_ASPECTS),
            "guides": guides, "root": root["name"] if root else None}


# ------------------------------------------------------------- summaries

def summary_freshness(db: Database, manuscript: dict, inst: dict) -> dict:
    """The book's essay summaries a Recommend reads: {total, stale: [file],
    missing: [file]} over the reading order, the instance's own essay left
    out. A turn serves them as stored (engines/catalogue.py says why); the
    page offers to bring them up to date first (`refresh_summaries`)."""
    from . import summaries as sums

    rows = [r for r in sums.status(db, manuscript) if r["file"] != inst["file"]]
    return {"total": len(rows),
            "stale": [r["file"] for r in rows if r["state"] == "stale"],
            "missing": [r["file"] for r in rows if r["state"] == "missing"]}


def refresh_summaries(db: Database, manuscript: dict, inst: dict) -> dict:
    """Bring every stale or missing essay summary of the book up to date
    (`summaries.rebuild_one` per essay, its lower levels first; billed like
    `authorlm summarize rebuild` on the [critique] / [summaries] models),
    reporting each essay as it starts. Returns {rebuilt, failed}."""
    from . import paths
    from . import summaries as sums
    from .engines import base
    from .engines.openai_api import _project_config

    fresh = summary_freshness(db, manuscript, inst)
    todo = fresh["stale"] + fresh["missing"]
    llms = sums.clients(_project_config(paths))
    if not llms["essay"].enabled:
        raise ValueError("the summary model is off ([llm] enabled = false)")
    rebuilt, failed = [], []
    for n, file in enumerate(todo, 1):
        base.report_progress(done=n - 1, total=len(todo), last=file)
        try:
            sums.rebuild_one(db, manuscript, file, llms,
                             echo=lambda *_a, **_k: None)
            rebuilt.append(file)
        except Exception as err:  # noqa: BLE001 — one essay never stops the rest
            failed.append(f"{file}: {type(err).__name__}: {err}")
    base.report_progress(done=len(todo), total=len(todo), last="")
    if failed:
        raise RuntimeError(f"{len(failed)} of {len(todo)} summaries not rebuilt: "
                           + "; ".join(failed[:5]))
    return {"rebuilt": rebuilt, "failed": failed}


def _recommendations(turns: list[dict], inst: dict) -> dict:
    """The start step's recommendations (Q3), each with the author's
    answer: accepted, declined or open."""
    lt = next((t for t in reversed(turns) if t["unit_label"] == RECOMMEND_LABEL
               and t["engine"]), None)
    if lt is None:
        return {"asked": False, "items": [], "text": ""}
    data = _json_in(lt["reply"]) or {}
    answered = {t["unit_label"]: t["action"] for t in turns
                if t["unit_label"].startswith("rec:") and t["seq"] > lt["seq"]}
    items = []
    for r in data.get("recommendations") or []:
        if not isinstance(r, dict) or r.get("list") not in core.LISTS:
            continue
        change = "remove" if r.get("change") == "remove" else "add"
        label = f"rec:{r['list']}:{change}:{r.get('essay', '')}"
        action = answered.get(label)
        items.append({"list": r["list"], "essay": r.get("essay", ""),
                      "change": change, "reason": r.get("reason", ""),
                      "label": label,
                      "answer": {"accept": "accepted", "reject": "declined"}.get(action, "")})
    return {"asked": True, "items": items,
            "text": "" if data else lt["reply"], "seq": lt["seq"]}


# ------------------------------------------------------------- messages

def _accepted_findings(db: Database, inst: dict) -> str:
    """The accepted findings as the author left them: each one's title, and
    for one the author edited, every field in his words (the session holds
    the letter as first written, so the edit is spelled out here too)."""
    rows = [core.finding_view(f) for f in core.letter_findings(db, inst)
            if f["verdict"] == "accept"]
    lines = []
    for f in rows:
        lines.append(f"- {f['item']}"
                     + (f" ({f['tier']})" if f["tier"] else "")
                     + f": {f['title'] or f['what'] or ''}"
                     + (" [as the author edited it]" if f["edited"] else ""))
        if f["edited"]:
            lines += [f"  {core.FINDING_WORDS[k]}: {f[k]}"
                      for k in ("where", "what", "why", "direction") if f[k]]
    return "\n".join(lines)


def _kept(db: Database, inst: dict) -> str:
    """The letter's Keep list (the author's version once he edited it) and
    its paragraph notes, for every construction message: said in the
    message itself, so a turn has them even when the letter is no longer
    in the engine's session (a rebuilt or switched engine, a long walk).
    "" when the letter has neither."""
    turns = core.turns(db, inst)
    lt = _letter_turn(turns)
    if lt is None:
        return ""
    keep = letter_keep(db, inst, turns)
    notes = parse_letter(lt["reply"], [p["name"] for p in passes()])["notes"]
    parts = []
    if keep["keep"] or keep["edited"]:
        head = ("Keep (the author's list, in place of the letter's):"
                if keep["edited"] else "Keep (from the letter):")
        parts.append(head + "\n" + ("\n".join(f"- {k}" for k in keep["keep"])
                                    or "(nothing named)"))
    if notes:
        parts.append("Paragraph notes (from the letter; work each at the "
                     "paragraph it names):\n" + "\n".join(f"- {n}" for n in notes))
    return "\n\n".join(parts)


def _unit_message(db: Database, manuscript: dict, inst: dict, step: str,
                  label: str, before: str) -> str:
    if step == "filters":
        names = ", ".join(inst["filters"]) or "(none chosen)"
        return (f"Unit {label} ({inst['filter_unit']}): apply {names}, in that "
                "order. Mark each change in your reason with the filter that "
                "made it. Return the whole unit in `proposed`, or an empty "
                f"`proposed` when nothing needs changing.\n\n{before}")
    m = _BEAT.match(label)
    kept = _kept(db, inst)
    cover = _COVER.match(label)
    if cover:
        return (f"Unit {label}: the beats are written, and paragraph "
                f"{cover.group(1)} of the old text still stands, claimed by no "
                "beat's plan. Propose what becomes of it: keep it (empty "
                "`changes` and `proposed`), or cut it or move it (`changes`), "
                "with your reason." + CHANGES_HOW
                + f"\n\nThe paragraph:\n\n{before}")
    if m and m.group(4):
        return (f"Unit {label}: beat {m.group(1)}'s plan says its new text takes "
                "the place of the old paragraph below. Propose what becomes of "
                "it: cut it, merge what it still has into the beat (a replace "
                "of the beat's paragraph and a cut of this one), or move it."
                + CHANGES_HOW + f"\n\nThe paragraph:\n\n{before}")
    if m and inst["construction_mode"] == "beats":
        b = int(m.group(1))
        if m.group(2) == "plan":
            return (f"Beat {b}: propose its plan — what the beat is for, the "
                    "point it makes and how it follows from the one before — "
                    "in `proposed`, with your reason. Its new text is inserted, "
                    "and the essay around it stays as it is: say in `placement` "
                    "where it goes in (`where` before or after the paragraph "
                    "whose opening words are `quote`, or the start or end of the "
                    "essay; an existing essay's first beat usually goes in at "
                    "the start) and name in `placement.supersedes` the opening "
                    "words of each old paragraph this beat takes the place of. "
                    "Once the beat's text is in, each of those comes to the "
                    "author to cut, keep, merge into the beat or move."
                    + (f"\n\n{kept}" if kept else ""))
        hist = _by_label(core.turns(db, inst), "construction")
        plan = unit_state(hist.get(f"b{b}:plan", []))
        plan_text = plan.get("text") or (plan.get("form") or {}).get("proposed", "")
        what = ("the beat's new text, whole" if m.group(2) in ("section", "insert")
                else f"paragraph {m.group(3)} of the beat's new text")
        return (f"Beat {b}: write {what}, following the accepted plan:\n\n"
                f"{plan_text}\n\nIt is inserted where the plan says; the old "
                "text stays until the author rules each paragraph the beat "
                "supersedes. Return it in `proposed`; an empty `proposed` "
                "means the beat is complete."
                + (f"\n\n{kept}" if kept else ""))
    findings = _accepted_findings(db, inst)
    return (f"Unit {label} ({inst['unit']}): propose the changes this unit "
            "needs, working the accepted findings and any paragraph note that "
            "names it. Reason first; an empty `proposed` and no `changes` when "
            "nothing needs changing here." + CHANGES_HOW
            + (f"\n\nAccepted findings:\n{findings}" if findings else "")
            + (f"\n\n{kept}" if kept else "")
            + f"\n\nThe unit as it stands:\n\n{before}")


# How a turn gives its changes (workflow/edits.py), said in every message
# that may change text in place.
CHANGES_HOW = (" Give the whole unit rewritten in `proposed`, or, when the "
               "change is not a rewrite of the whole unit, list it in "
               "`changes`, each one change the author rules on its own: "
               "replace (`original` quoted exactly, `text` the new words), "
               "insert (`text`, placed `where` before or after the paragraph "
               "whose opening words are `quote`, or at the start or end), cut "
               "(`original`), or move (`original`, and its new place as for "
               "an insert). An empty `original` means this whole unit.")


def _redo_message(label: str, blurbs: list[str], free: str,
                  discussion: list[dict] | None = None) -> str:
    parts = [f"Redo {label}."]
    if blurbs:
        parts.append("What is wrong: " + "; ".join(blurbs) + ".")
    if free.strip():
        parts.append(free.strip())
    out = " ".join(parts)
    if discussion:
        out += ("\n\nRedo it in the light of the author's discussion of this "
                "unit with you since its last proposal; where he and you "
                "disagreed, his word stands:\n\n" + _thread_text(discussion))
    return out


def _thread_text(discussion: list[dict]) -> str:
    return "\n\n".join(f"The author: {d['author']}\nYou: {d['reply']}"
                        for d in discussion)


def _discuss_message(label: str, unit: dict, text: str) -> str:
    form = unit.get("form") or {}
    pending = unit.get("state") == "pending"
    what = ("its plan" if label.endswith(":plan") else "this unit")
    if pending:
        now = (f"The proposal waiting for him (your reason: "
               f"{form.get('reason') or 'none given'}):\n\n{form.get('proposed', '')}")
    else:
        now = (f"The unit is {unit.get('state')}; as it stands:\n\n"
               f"{unit.get('text') or unit.get('before') or '(empty)'}")
    return (f"The author is discussing {label} with you ({what}). This is a "
            "conversation, not a new proposal: answer him plainly in `text`, "
            "as an editor would. If you would change the proposal, say how, "
            "in words; leave `proposed` empty. The proposal changes only when "
            "he presses Redo, which carries this discussion to you.\n\n"
            f"{now}\n\nHis message:\n\n{text}")


def _edit_message(label: str, text: str) -> str:
    return (f"The author edited {label} to the text below. Work in each "
            "[...] note at its place: the supported tags ("
            + ", ".join(f"[{t}: ...]" for t in SUPPORTED_TAGS)
            + ") keep their meaning, and any other bracket is an instruction "
            "to you at that spot. Propose the revision, and say in your "
            f"reason what you did with each note.\n\n{text}")


# ------------------------------------------------------------- the gates

def page_gate(db: Database, manuscript: dict, inst: dict) -> str | None:
    """Why the instance cannot advance from its step, or None: the
    workflow's own gate (`core.gate`), then what the page knows about the
    walk (Q18 "Construction is done when the walk reaches the end of the
    essay and the author concludes it"; Q23 "construction is done only after
    the wrap-up"). Anything else goes by Skip, with one sentence (Q9)."""
    why = core.gate(db, inst)
    if why:
        return why
    step = inst["step"]
    skip = "; skip with a reason if not needed"
    if step in ("construction", "filters") and (step == "construction"
                                               or inst["filters"]):
        w = _walk(db, manuscript, inst, step)
        nxt = "the wrap-up" if step == "construction" else "lint"
        pending = [u["label"] for u in w["units"] if u["state"] == "pending"]
        if pending:
            return f"{nxt} waits: {pending[0]} has a form waiting for you" + skip
        if not w["end"]:
            if w["mode"] == "beats":
                left = [u["label"] for u in w["units"]
                        if u["kind"] == "cover" and u["state"] == "open"]
                if left:
                    return (f"{nxt} waits: rule what the beats left standing "
                            f"({', '.join(left[:5])}), then conclude" + skip)
                return (f"{nxt} waits: conclude the beats when the essay is "
                        "built" + skip)
            return (f"{nxt} waits: the walk has not reached the end of the "
                    f"essay (next: {w['next']})" + skip)
    if step == "wrapup":
        wu = _wrapup(core.turns(db, inst))
        if not wu["drafted"]:
            return "the filters wait for the wrap-up: it is not drafted" + skip
        open_ = [i["label"] for i in wu["items"] if i["state"] in ("pending", "open")]
        if open_:
            return (f"the filters wait for the wrap-up: {len(open_)} item(s) "
                    f"not yet ruled ({', '.join(open_[:5])})" + skip)
    if step == "lint" and not any(t["unit_label"] == LINT_LABEL and t["step"] == "lint"
                                  for t in core.turns(db, inst)):
        return "done waits for lint: run it" + skip
    return None


# ------------------------------------------------------------- actions

def _step_for(inst: dict, label: str) -> str:
    if label.startswith("w:"):
        return "wrapup"
    return inst["step"]


def _check_label_step(inst: dict, step: str) -> None:
    if step not in ("construction", "filters", "wrapup"):
        raise ValueError(f"no units are worked at {inst['step']}")
    if step != inst["step"]:
        raise ValueError(f"the instance is at {inst['step']}, not {step}")


def _unit(db: Database, manuscript: dict, inst: dict, step: str,
          label: str) -> tuple[dict, dict | None]:
    """(the unit or wrap-up item named `label`, the walk), or LookupError.
    The walk's next label counts though it has no turn yet."""
    if step == "wrapup":
        item = next((i for i in _wrapup(core.turns(db, inst))["items"]
                     if i["label"] == label), None)
        if item is None:
            raise LookupError(f"no wrap-up item '{label}'")
        return item, None
    walk = _walk(db, manuscript, inst, step)
    hit = next((u for u in walk["units"] if u["label"] == label), None)
    if hit is None and label and label == walk["next"]:
        hit = {"label": label, "state": "open",
               "before": _unit_before(db, manuscript, inst, step, label, walk)}
    if hit is None:
        raise LookupError(f"no unit '{label}' at {step}")
    return hit, walk


def propose(db: Database, manuscript: dict, inst: dict, label: str | None) -> dict:
    """Ask the engine for the form of a unit: the walk's next unit when no
    label is given; a named unit only when it is not yet worked."""
    step = inst["step"]
    _check_label_step(inst, step)
    inst = core.ensure_base(db, manuscript, inst)
    if step == "wrapup":
        raise ValueError("the wrap-up's items come from the wrap-up itself")
    if step == "filters" and not inst["filters_confirmed"]:
        raise ValueError("choose the filters first (the checklist)")
    revised = core.revised_letter_gate(db, inst)
    if revised:
        raise ValueError(revised)
    walk = _walk(db, manuscript, inst, step)
    if label is None:
        label = walk["next"]
        if label is None:
            raise ValueError("the walk has reached the end of the essay; "
                             "advance when you are ready")
    unit, walk = _unit(db, manuscript, inst, step, label)
    if unit["state"] != "open":
        raise ValueError(f"{label} is {unit['state']}; "
                         + ("rule its form" if unit["state"] == "pending"
                            else "reopen it to work it again"))
    return _send(db, manuscript, inst, "start",
                 _unit_message(db, manuscript, inst, step, label, unit["before"]),
                 author_text="", unit_label=label, unit_before=unit["before"])


ACTIONS = ("accept", "reject", "redo", "edit", "cut", "keep", "move")


def act(db: Database, manuscript: dict, inst: dict, label: str, action: str,
        text: str = "", blurbs: list[str] | None = None, free: str = "",
        with_discussion: bool = False, where: str = "", quote: str = "") -> dict:
    """The actions on a unit or one change of it (Q8a, Q11). Accept and
    reject, an edit with no bracket, and the author's own changes (cut the
    unit; keep it, as a reject; move it `where` before or after the
    paragraph `quote` opens, or to the start or end) are recorded without a
    model call; a redo, and an edit with a bracket in it, ask the engine
    for the next revision. Accept and reject rule an open form only; a
    ruled unit is reopened first, so a stale click never overwrites the
    author's own text."""
    step = _step_for(inst, label)
    _check_label_step(inst, step)
    if action not in ACTIONS:
        raise ValueError(f"action is one of {', '.join(ACTIONS)}")
    inst = core.ensure_base(db, manuscript, inst)
    unit, walk = _unit(db, manuscript, inst, step, label)
    state = unit["state"]
    before = unit.get("before", "")
    blurbs = [b for b in (blurbs or []) if b.strip()]
    if state == "split":
        raise ValueError(f"{label} is several changes; rule each of them: "
                         + ", ".join(unit.get("children") or []))
    form = unit.get("form") or {}
    if action == "keep":
        if state not in ("pending", "open") or not before or step == "wrapup":
            raise ValueError(f"{label} has nothing to keep (it is {state})")
        action = "reject"
    if action == "accept":
        if step == "wrapup":
            proposed = form.get("proposed", "")
            if state != "pending" or not proposed:
                raise ValueError(f"{label} has no proposal waiting to accept "
                                 f"(it is {state})")
            return core.record_turn(db, inst, "accept", author_text=proposed,
                                    unit_label=label, unit_before=before)
        changes = form.get("changes") or []
        if state != "pending" or not changes:
            raise ValueError(f"{label} has no proposal waiting to accept "
                             f"(it is {state})")
        return core.record_turn(db, inst, "accept",
                                author_text=form.get("proposed") or "",
                                unit_label=label, unit_before=before,
                                edits={"changes": changes})
    if action == "reject":
        if state not in ("pending",) and not (
                state == "open" and (step == "wrapup" or _COVER.match(label))):
            raise ValueError(f"{label} has no proposal waiting to reject "
                             f"(it is {state})")
        return core.record_turn(db, inst, "reject", unit_label=label,
                                unit_before=before)
    if state in RESOLVED and state != "passed":
        raise ValueError(f"{label} is {state}; reopen it first")
    if action in ("cut", "move"):
        if step == "wrapup" or not before:
            raise ValueError(f"{label} has no original text to {action}")
        if action == "cut":
            return core.record_turn(db, inst, "edit", unit_label=label,
                                    author_text="(cut: the author takes this out)",
                                    unit_before=before,
                                    edits={"changes": [{
                                        "kind": "cut", "original": "", "where": "",
                                        "quote": "", "text": ""}]})
        change = edits.clean({"kind": "move", "original": "", "where": where,
                              "quote": quote, "text": text})
        if change["where"] not in edits.WHERES:
            raise ValueError("a move says where: before or after a paragraph "
                             "(its opening words), or the start or end")
        if change["where"] in ("before", "after") and (
                not change["quote"] or edits.find((walk or {}).get("text", ""),
                                                  change["quote"]) is None):
            raise ValueError("the paragraph to move it next to is not in the "
                             "essay: quote its opening words exactly")
        return core.record_turn(db, inst, "edit", unit_label=label,
                                author_text=f"(moved: {edits.describe(change)})",
                                unit_before=before, edits={"changes": [change]})
    if state in RESOLVED and state != "passed":
        raise ValueError(f"{label} is {state}; reopen it first")
    if action == "redo":
        # "Redo with this discussion": the unit's discussion since its last
        # proposal goes to the engine with the redo, as the author's
        # direction (his words in the thread).
        talk = _since_form(db, inst, step, label) if with_discussion else []
        if with_discussion and not talk:
            raise ValueError(f"{label} has no discussion since its last "
                             "proposal to redo with")
        if not blurbs and not free.strip() and not talk:
            raise ValueError("a redo takes a blurb or a few words of what is "
                             "wrong")
        return _send(db, manuscript, inst, "redo",
                     _redo_message(label, blurbs, free, talk),
                     author_text=free.strip() or (
                         "(redo with this discussion)" if talk else ""),
                     blurbs=blurbs, unit_label=label, unit_before=before)
    if action == "edit":
        if not text.strip():
            raise ValueError("an edit carries the author's text")
        if core.needs_model("edit", text):
            return _send(db, manuscript, inst, "edit", _edit_message(label, text),
                         author_text=text, unit_label=label, unit_before=before)
        return core.record_turn(db, inst, "edit", author_text=text,
                                unit_label=label, unit_before=before)
    raise ValueError("action is accept, reject, redo or edit")


def discuss_unit(db: Database, manuscript: dict, inst: dict, label: str,
                 text: str) -> dict:
    """The author talks with the engine about one unit (a beat's plan, a
    unit's text, a wrap-up item, a filter unit): a model turn whose reply
    is words only, recorded as a `discuss` turn at the unit's step and
    label. The unit's form does not change (`_by_label` leaves discussion
    out): a proposal the engine would make in its reply is kept in the
    reply's text, never as a form. Every later turn of the instance sees
    the thread (it is in the session), and "Redo with this discussion"
    hands it to the redo."""
    text = (text or "").strip()
    if not text:
        raise ValueError("say something to discuss the unit")
    step = _step_for(inst, label)
    _check_label_step(inst, step)
    unit, _w = _unit(db, manuscript, inst, step, label)
    reply, ref = core.ask(db, manuscript, inst, DISCUSS,
                          _discuss_message(label, unit, text),
                          author_text=text, engine=_engine(inst),
                          context_extra=_context_extra(db, manuscript, inst))
    reply = dict(reply)
    proposed = (reply.get("proposed") or "").strip()
    if proposed:
        reply["text"] = ((reply.get("text") or "").rstrip()
                         + "\n\nWhat I would propose on Redo:\n\n" + proposed).strip()
    reply["proposed"] = ""
    reply["reason"] = ""
    return core.record_turn(db, inst, DISCUSS, author_text=text,
                            unit_label=label, reply=reply, session_ref=ref)


def reopen(db: Database, manuscript: dict, inst: dict, label: str) -> dict:
    """Go back to a unit already ruled (Q11, confirmed 2026-11-25): the
    engine proposes it again; units after it are kept, and the engine is
    told so. A unit already landed in the essay is first taken back out of
    it (`landing.revert`): the file gets the unit's original text again."""
    from .workflow import landing

    step = _step_for(inst, label)
    _check_label_step(inst, step)
    inst = core.ensure_base(db, manuscript, inst)
    unit, _w = _unit(db, manuscript, inst, step, label)
    if unit["state"] not in RESOLVED or unit["state"] == "gone":
        raise ValueError(f"{label} is not ruled yet; nothing to reopen"
                         if unit["state"] != "gone" else
                         f"{label} was taken out by an earlier change; reopen that")
    landing.revert(db, manuscript, inst, step, label, road=_road(db, manuscript))
    kept = unit.get("text", "")
    return _send(db, manuscript, inst, "reply",
                 f"Reopen {label}: the author goes back to this unit. The units "
                 "after it are kept as they are. Propose it again in the light "
                 f"of everything since.\n\nAs it was kept:\n\n{kept}",
                 author_text=f"(reopened {label})", unit_label=label,
                 unit_before=unit.get("before", ""))


def draft_letter(db: Database, manuscript: dict, inst: dict) -> dict:
    """Write the letter: the turn and its findings are recorded together,
    or not at all. A letter none of whose findings is ruled yet may be
    redrafted; the redraft replaces its findings."""
    if inst["step"] != "letter":
        raise ValueError(f"the letter is written at the letter step; the "
                         f"instance is at {inst['step']}")
    turns = core.turns(db, inst)
    old = core.letter_findings(db, inst)
    if _letter_turn(turns) and any(f["verdict"] for f in old):
        raise ValueError("the letter is drafted and its findings are being "
                         "ruled; discuss it with a reply")
    if _letter_turn(turns) and _letter_edited(db, inst, turns):
        raise ValueError("you have edited this letter; a redraft would "
                         "replace your edits. Discuss it with a reply")
    names = [p["name"] for p in passes()]
    message = ("Write the editorial letter on this essay now, in `text`, in "
               "the shape your brief's Output asks for (Findings, Keep, "
               "Passes, Paragraph notes, Blurbs). The passes, in their fixed "
               f"order: {', '.join(names) or '(none)'}.")
    reply, ref = core.ask(db, manuscript, inst, "start", message,
                          engine=_engine(inst), author_text="",
                          context_extra=_context_extra(db, manuscript, inst))
    parsed = parse_letter(reply.get("text", ""), names)
    with db.transaction():
        if old:
            db.conn.execute("DELETE FROM devedit_items WHERE manuscript_id = ? AND "
                       "run = ?", (inst["manuscript_id"], core.letter_run(inst)))
        turn = core.record_turn(db, inst, "start", author_text="",
                                unit_label=LETTER_LABEL, reply=reply,
                                session_ref=ref)
        for i, f in enumerate(parsed["findings"], start=1):
            core.add_finding(db, inst, f"F{i}",
                             {k: f.get(k, "") for k in ("n", "title", "where",
                                                        "what", "why", "direction",
                                                        "tier", "text")})
        if parsed["blurbs"]:
            core.add_blurbs(db, manuscript["id"], parsed["blurbs"])
    return turn


def _findings_as_ruled(db: Database, inst: dict) -> str:
    """The letter in force's findings, each with the author's verdict and
    reason, and his words for any field he edited: what a revision is
    told of the letter it replaces."""
    lines = []
    for row in core.letter_findings(db, inst):
        f = core.finding_view(row)
        verdict = f["verdict"] or "not ruled yet"
        lines.append(f"- {f['item']}" + (f" ({f['tier']})" if f["tier"] else "")
                     + f": {f['title'] or f['what']} — {verdict}"
                     + (f": {f['reason']}" if f["reason"] else ""))
        if f["edited"]:
            lines += [f"  {core.FINDING_WORDS[k]} (the author's words): {f[k]}"
                      for k in f["edited"] if k != "tier"]
    return "\n".join(lines) or "(the letter had no findings)"


def revise_message(db: Database, manuscript: dict, inst: dict, note: str) -> str:
    """The message of a revision turn (`revise_letter`)."""
    turns = core.turns(db, inst)
    lt = _letter_turn(turns)
    names = [p["name"] for p in passes()]
    keep = letter_keep(db, inst, turns)
    k = len(core.revisions(db, inst)) + 1
    step = inst["step"]
    where = ""
    if step == "construction":
        w = _walk(db, manuscript, inst, "construction")
        done = [u["label"] for u in w["units"] if u["state"] in CHANGED]
        where = (" Construction is under way: the units accepted so far "
                 f"({', '.join(done) or 'none yet'}) stay accepted and are in "
                 "the essay as you have it; the walk goes on from "
                 f"{w['next'] or 'its end'} under your new letter. Where a "
                 "finding bears on a unit already accepted, quote that text "
                 "exactly in its Where, so the author can reopen the unit.")
    elif step in ("wrapup", "filters"):
        where = (" Construction is done and the instance is at the "
                 f"{'wrap-up' if step == 'wrapup' else 'filters'}: the essay "
                 "you have is the text as the author accepted it. Your "
                 "findings and passes steer what is still ahead.")
    return (f"Revise the editorial letter on this essay (revision {k}, asked "
            f"at the {step} step). Write the whole new letter in `text`, in "
            "the shape your brief's Output asks for (Findings, Keep, Passes, "
            "Paragraph notes, Blurbs). It replaces the letter in force (turn "
            f"#{lt['seq']}), which stays on the record as history.{where}\n\n"
            "Read the essay as it now stands. Open by saying which findings of "
            "the letter in force the text now meets. Under every finding add "
            "a line \"- **Continues:** F<n>\" naming the finding of the letter "
            "in force it carries on, or \"- **Continues:** new\". Weigh the "
            "author's verdicts and the discussion: do not raise again what he "
            "rejected unless the text has changed or you have a reason the "
            "discussion did not meet, and then give it.\n\n"
            f"The letter in force, as the author ruled it:\n{_findings_as_ruled(db, inst)}"
            "\n\nIts Keep list as it stands"
            + (" (the author's)" if keep["edited"] else "") + ":\n"
            + ("\n".join(f"- {x}" for x in keep["keep"]) or "(nothing named)")
            + "\n\nThe author's note on what to look at differently: "
            + (note.strip() or "(none: read it again with fresh eyes)")
            + f"\n\nThe passes, in their fixed order: {', '.join(names) or '(none)'}.")


def revise_letter(db: Database, manuscript: dict, inst: dict,
                  note: str = "") -> dict:
    """Revise the letter (owner, 2026-11-26: "Even if I accept the findings,
    it need not necessarily cause me to not be able to redraft"), at any of
    `core.LETTER_STEPS` once a letter is written, with the author's
    optional note on what to look at differently.

    The new letter is written under the letter's brief and role
    (`as_step="letter"`) against the working text as it stands (accepted
    units applied), with the letter in force, its verdicts, edits and
    discussion as history. It becomes the letter in force: its findings
    start without verdicts (each says which earlier finding it continues),
    the replaced findings move to a superseded run, and the revision is a
    `letter-revision` decision (core.supersede_letter); the turn and all of
    that land in one transaction, or nothing does. Accepted units stay
    accepted; the walk continues from where it is once the new findings
    are ruled (core.revised_letter_gate), and a finding that touches an
    accepted unit proposes reopening it (`reopen_proposals`)."""
    if inst["step"] not in core.LETTER_STEPS:
        raise ValueError("the letter is revised from the letter step to the "
                         f"filters; the instance is at {inst['step']}")
    turns = core.turns(db, inst)
    old = _letter_turn(turns)
    if old is None:
        raise ValueError("the letter is not written yet; write it first")
    note = (note or "").strip()
    names = [p["name"] for p in passes()]
    message = revise_message(db, manuscript, inst, note)
    reply, ref = core.ask(db, manuscript, inst, "start", message,
                          engine=_engine(inst), author_text=note,
                          context_extra=_context_extra(db, manuscript, inst),
                          as_step="letter")
    parsed = parse_letter(reply.get("text", ""), names)
    with db.transaction():
        turn = core.record_turn(db, core.letter_turn_instance(inst), "start",
                                author_text=note, unit_label=LETTER_LABEL,
                                reply=reply, session_ref=ref)
        core.supersede_letter(db, inst, note=note, superseded_seq=old["seq"],
                              letter_seq=turn["seq"],
                              findings=len(parsed["findings"]))
        for i, f in enumerate(parsed["findings"], start=1):
            core.add_finding(db, inst, f"F{i}", {
                **{k: f.get(k, "") for k in ("n", "title", "where", "what", "why",
                                             "direction", "tier", "text",
                                             "continues")},
                "letter_seq": turn["seq"]})
        if parsed["blurbs"]:
            core.add_blurbs(db, manuscript["id"], parsed["blurbs"])
    return turn


_QUOTE = re.compile(r"[\"“”«]([^\"“”«»]{8,})[\"“”»]|[‘']([^‘’']{8,})[’']")


def _quotes(text: str) -> list[str]:
    return [" ".join((a or b).split()) for a, b in _QUOTE.findall(text or "")]


def reopen_proposals(db: Database, inst: dict, walk: dict) -> list[dict]:
    """After a revision, each accepted finding of the letter in force
    whose Where quotes the text of a unit accepted (or edited) before the
    revision: a proposal to reopen that unit (`reopen`, the existing
    path), rather than redoing the walk. A unit worked again since the
    revision, or one the author chose to leave (`decline_reopen`), is not
    proposed. Construction only: the later steps do not walk the
    findings."""
    revs = core.revisions(db, inst)
    if not revs or inst["step"] != "construction":
        return []
    since = revs[-1]["detail"].get("letter_seq") or 0
    declined = {(d["detail"] or {}).get("label") for d in core.decisions(db, inst)
                if d["kind"] == "reopen-declined"
                and (d["detail"] or {}).get("letter_seq") == since}

    def norm(t):
        return " ".join((t or "").split())

    out, seen = [], set()
    for row in core.letter_findings(db, inst):
        if row["verdict"] != "accept":
            continue
        f = core.finding_view(row)
        for q in _quotes(f["where"]):
            for u in walk["units"]:
                if (u["state"] not in CHANGED or u["label"] in seen
                        or u["label"] in declined or (u.get("seq") or 0) > since):
                    continue
                if q in norm(u.get("text")) or q in norm(u.get("before")):
                    seen.add(u["label"])
                    out.append({"label": u["label"], "finding": f["item"],
                                "title": f["title"], "quote": q,
                                "letter_seq": since})
    return out


def decline_reopen(db: Database, manuscript: dict, inst: dict, label: str) -> dict:
    """The author leaves a unit as accepted though the revised letter
    touches it; recorded, so it is not proposed again for this letter."""
    walk = (_walk(db, manuscript, inst, "construction")
            if inst["step"] == "construction" else {"units": []})
    hit = next((p for p in reopen_proposals(db, inst, walk) if p["label"] == label),
               None)
    if hit is None:
        raise LookupError(f"no reopen proposed for '{label}'")
    return core._record_decision(db, inst, "reopen-declined", detail=hit)


def accept_letter(db: Database, inst: dict) -> dict:
    """A letter with no findings is accepted as it stands: once it is
    written, or for a new essay, whose letter is its beat plan. Anything
    else passes the letter only by Skip, with one sentence (Q9)."""
    if inst["step"] != "letter":
        raise ValueError(f"the instance is at {inst['step']}, not letter")
    if core.letter_findings(db, inst):
        raise ValueError("the letter has findings: rule each of them")
    if not _letter_turn(core.turns(db, inst)) and inst["kind"] != "new":
        raise ValueError("write the letter first, or skip it with a reason")
    return core.record_turn(db, inst, "accept", unit_label=LETTER_LABEL)


# ------------------------------------------------------------------ lint

def _lint_text(db: Database, manuscript: dict, file: str, text: str) -> dict:
    """`authorlm book lint <file>` over the working text instead of the
    file on disk (`changes.lint_chapter`'s rules): no model call."""
    from . import changes, critic, docs

    try:
        relpath = docs._resolve_relpath(manuscript, file)
    except Exception:  # noqa: BLE001 — a new essay is not on disk yet
        relpath = file
    rules = changes.lint_rules(None, None)
    found = []
    if any(r in changes.LINT_CODES for r in rules):
        found += critic.lint_draft(db, manuscript, relpath, text,
                                   include_self=False).findings
    found += changes._proof_findings(db, manuscript, relpath, text, rules,
                                     changes.proof_lexicon(db, manuscript))
    findings = changes._rows(text, found, rules)
    counts: dict[str, int] = {}
    for f in findings:
        counts[f["severity"]] = counts.get(f["severity"], 0) + 1
    return {"findings": findings, "counts": counts}


def run_lint(db: Database, manuscript: dict, inst: dict) -> dict:
    """Lint the essay as it now stands and record the run on the instance
    (the lint step's gate wants one)."""
    if inst["step"] != "lint":
        raise ValueError(f"the instance is at {inst['step']}, not lint")
    board = _lint_text(db, manuscript, inst["file"],
                       working_text(db, manuscript, inst))
    keep = [{k: f.get(k) for k in ("severity", "code", "message", "quote", "line")}
            for f in board["findings"][:200]]
    return core.record_turn(db, inst, "accept", unit_label=LINT_LABEL,
                            author_text=json.dumps(
                                {"counts": board["counts"], "findings": keep,
                                 "total": len(board["findings"])},
                                ensure_ascii=False))


def _lint_view(turns: list[dict]) -> dict | None:
    lt = next((t for t in reversed(turns) if t["unit_label"] == LINT_LABEL
               and t["step"] == "lint"), None)
    if lt is None:
        return None
    return {"at": lt["created_at"], **(_json_in(lt["author_text"]) or {})}


# ------------------------------------------------------------- estimate

BILLED_ENGINES = ("openai-api",)
# Assumptions of the estimate, shown with it: about four characters a token,
# this many output tokens a turn, and one redo for every four units.
CHARS_PER_TOKEN = 4
OUTPUT_TOKENS = 800


def estimate(db: Database, manuscript: dict, engine: str, model: str = "",
             essay: str = "", inst: dict | None = None) -> dict:
    """The estimated cost of a billed engine for an instance (Q6: "A billed
    engine shows its estimated cost when chosen"): the standing context and
    the essay as input on every turn, uncached, times the turns of a whole
    walk (recommend, letter, each unit at construction and at the filters,
    the wrap-up, a redo for every four units). A floor: the turns so far
    and the reads the engine makes add to it."""
    if engine not in BILLED_ENGINES:
        return {"engine": engine, "billed": False}
    from .engines import default_model
    from .engines.openai_api import price_usd

    model = model or (inst["model"] if inst and inst["engine"] == engine else "") \
        or default_model(engine) or ""
    if inst is not None:
        text = working_text(db, manuscript, inst)
        standing = sum(len(b.get("text") or "") for b in
                       context.standing_context(db, manuscript, inst))
        file = inst["file"]
    else:
        files = core._files(manuscript)
        order = core.reading_order(manuscript, files)
        try:
            file = core._book_essay(essay, order)
        except (ValueError, LookupError):
            file = essay
        text = files.get(file, "")
        lib = context.library_dir()
        sizes = [len(t or "") for t in (context._read(lib / "charter.md"),)]
        for sub in ("briefs", "roles"):
            texts = [x.read_text(encoding="utf-8") for x in (lib / sub).glob("*.md")] \
                if (lib / sub).is_dir() else []
            sizes.append(max((len(t) for t in texts), default=0))
        standing = sum(sizes)
    n = len(paragraphs(text)) or 4
    turns = 2 + n + 1 + n + max(1, n // 4)
    tokens_in = (standing + len(text)) // CHARS_PER_TOKEN
    per_turn = price_usd(model, tokens_in, 0, OUTPUT_TOKENS) if model else None
    return {"engine": engine, "billed": True, "model": model, "file": file,
            "turns": turns, "input_per_turn": tokens_in,
            "output_per_turn": OUTPUT_TOKENS,
            "per_turn": per_turn,
            "total": per_turn * turns if per_turn is not None else None,
            "why": "" if per_turn is not None else
            (f"no price known for '{model}'" if model else "no model named")}


def start_instance(db: Database, manuscript: dict, p: dict) -> dict:
    inst = core.start(db, manuscript, str(p.get("essay") or ""),
                      str(p.get("kind") or ""),
                      brief=str(p.get("brief") or ""),
                      engine=p.get("engine") or None,
                      model=p.get("model") or None,
                      unit=p.get("unit") or None, mode=p.get("mode") or None)
    return {"id": inst["id"], "carry": inst.get("carry")}


def book_summaries(db: Database, manuscript: dict, inst: dict,
                   order: list[str]) -> str:
    """The essay summary of every other essay of the book, as stored, in
    reading order: what a Recommend needs, in the message itself, so the
    turn makes no read request for them (a turn that asked for every
    summary one by one, each rebuilt first, ran past its limit; see
    engines/catalogue.py). A summary the essay moved past is marked."""
    from . import summaries as sums

    have = sums.all_summaries(db, manuscript["id"])
    states = {r["file"]: r["state"] for r in sums.status(db, manuscript)}
    lines = []
    for file in order:
        if file == inst["file"]:
            continue
        row = have.get(file)
        state = states.get(file, "missing")
        text = " ".join((row or {}).get("summary", "").split()) \
            if state not in ("deprecated", "unwritten") else ""
        mark = "" if state in ("fresh", "upstream_stale", "rewriting") else \
            f" [{state}: the essay changed since; check its text before a finding rests on it]" \
            if text else f" [{state}]"
        lines.append(f"- {file}{mark}: {text or '(no summary)'}")
    return "The essays of the book, with their summaries:\n" + "\n".join(lines)


def recommend(db: Database, manuscript: dict, inst: dict) -> dict:
    """Ask the engine for list recommendations (Q3). On a later instance
    it looks only at what changed since the last one. The book's essay
    summaries go in the message (`book_summaries`)."""
    if inst["step"] != "start":
        raise ValueError(f"recommendations are asked at the start step; the "
                         f"instance is at {inst['step']}")
    since = core.since_last(db, inst)
    order = core.reading_order(manuscript)
    lists = "\n".join(f"- {k}: {', '.join(inst[k]) or '(empty)'}" for k in core.LISTS)
    scope = (("This is a later instance: look only at what changed since the "
              f"last one (added: {', '.join(since['added']) or 'none'}; removed: "
              f"{', '.join(since['removed']) or 'none'}), and recommend "
              "additions or removals.")
             if inst.get("carried_from") else
             "Recommend from the essay summaries below. Ask for an essay's "
             "section summaries or text only where a summary leaves the "
             "choice open, and for a few essays at most.")
    return _send(db, manuscript, inst, "start",
                 "Recommend essays for this essay's three lists: reference "
                 "(content binds; never imitated for style), voice (style), "
                 "aware (summaries; keep in mind). " + scope
                 + f"\n\nThe lists now:\n{lists}\n\n"
                 + book_summaries(db, manuscript, inst, order)
                 + '\n\nPut in `text` only a JSON object: {"recommendations": '
                 '[{"list": "reference|voice|aware", "essay": "<file>", '
                 '"change": "add|remove", "reason": "<one sentence>"}]}.',
                 author_text="", unit_label=RECOMMEND_LABEL)


def answer_rec(db: Database, manuscript: dict, inst: dict, p: dict) -> dict:
    if inst["step"] != "start":
        raise ValueError(f"the instance is at {inst['step']}, not start")
    which, essay = str(p.get("list") or ""), str(p.get("essay") or "")
    change = "remove" if p.get("change") == "remove" else "add"
    verdict = p.get("verdict")
    label = f"rec:{which}:{change}:{essay}"
    if verdict == "accept":
        inst = core.change_list(db, manuscript, inst, which, essay,
                                add=change == "add",
                                words=str(p.get("reason") or "recommended"))
        return core.record_turn(db, inst, "accept", unit_label=label)
    if verdict == "decline":
        return core.record_turn(db, inst, "reject", unit_label=label)
    raise ValueError("verdict is accept or decline")


def wrapup(db: Database, manuscript: dict, inst: dict) -> dict:
    if inst["step"] != "wrapup":
        raise ValueError(f"the instance is at {inst['step']}, not wrapup")
    revised = core.revised_letter_gate(db, inst)
    if revised:
        raise ValueError(revised)
    return _send(db, manuscript, inst, "start",
                 "The walk has reached the end. Give the wrap-up (your brief's "
                 "Wrap-up): put in `text` only a JSON object: "
                 '{"removed": [{"text": "<the passage>", "proposal": "<placed '
                 'text or empty to drop>", "reason": "<where, and why>"}], '
                 '"placeholders": [{"text", "proposal", "reason"}], '
                 '"terms": [{"text", "proposal", "reason"}], '
                 '"principles": [{"words": "<the author\'s principle>", '
                 '"scope": "this paragraph | this essay | beyond"}]}.',
                 author_text="", unit_label=WRAPUP_LABEL)


# ------------------------------------------------------------- dispatch

def _land_after(db: Database, manuscript: dict, before: dict,
                after: dict) -> dict | None:
    """Land once a step that rules text has ended (`landing.after_move`);
    the step has moved either way, and the result says what landed."""
    from .workflow import landing
    return landing.after_move(db, manuscript, before, after,
                              road=_road(db, manuscript), actor="web",
                              workspace=_WORKSPACE.get())


def _blurb_list(p: dict) -> list[str]:
    raw = p.get("blurbs") or []
    if isinstance(raw, str):
        raw = [raw]
    return [str(b) for b in raw]


# The page methods that ask the engine for a turn (a redo, and an edit with
# a bracket, in `act`). The page runs them as background jobs
# (workflow/jobs.py); `summaries` (bring the book's summaries up to date
# ahead of a turn) runs the same way.
MODEL_TURNS = ("recommend", "letter", "letter_reply", "propose", "reopen", "wrapup",
               "revise_letter", "discuss")
BACKGROUND = MODEL_TURNS + ("summaries",)


def is_background(method: str, p: dict) -> bool:
    if method in BACKGROUND:
        return True
    if method == "act":
        action = p.get("action")
        return action == "redo" or (action == "edit" and core.needs_model(
            "edit", str(p.get("text") or "")))
    return False


def dispatch(method: str, params: dict[str, Any],
             workspace: str | None = None, background: bool = False) -> Any:
    """The page's API (`/api/workflow`). Every method names the
    manuscript; instance methods name the instance as `id`.

    `background` (the page's server passes it): a model turn starts as a
    job and the instance view comes back at once, with the job on it
    (`job`); the page polls `instance` until the job ends. Without it (the
    CLI, tests) the turn runs here and its result comes back."""
    from .workflow import jobs

    if method not in METHODS:
        raise ValueError(f"unknown method '{method}'")
    _WORKSPACE.set(workspace)
    db = api.open_db(workspace)
    manuscript = api.get_manuscript(db, params.get("manuscript"))
    p = params
    if method == "overview":
        return overview(db, manuscript)
    if method == "start":
        return start_instance(db, manuscript, p)
    if method == "blurbs":
        return core.blurb_list(db, manuscript["id"], hidden=bool(p.get("all")))
    if method == "blurb_add":
        return {"added": core.add_blurbs(db, manuscript["id"], _blurb_list(p))}
    if method == "blurb_hide":
        core.hide_blurb(db, manuscript["id"], str(p.get("text") or ""),
                        bool(p.get("hidden", True)))
        return core.blurb_list(db, manuscript["id"], hidden=True)
    if method == "usage":
        return core.usage(db, manuscript, p.get("essay") or None,
                          p.get("month") or None)
    if method == "estimate":
        inst = core.get_instance(db, manuscript, str(p["id"])) if p.get("id") else None
        return estimate(db, manuscript, str(p.get("engine") or ""),
                        str(p.get("model") or ""), str(p.get("essay") or ""),
                        inst)
    inst = core.get_instance(db, manuscript, str(p.get("id") or ""))
    if method == "instance":
        return instance_view(db, manuscript, inst["id"])
    if method == "job_dismiss":
        jobs.dismiss(db, inst["id"], str(p.get("job") or ""))
        return instance_view(db, manuscript, inst["id"])
    if method == "brief":
        # The one write allowed while a model turn runs: the brief is read
        # when a turn starts (core.ask), so the running turn keeps the brief
        # it started with and the next turn reads the new one; saving it
        # touches nothing the running turn records (core.set_brief).
        core.set_brief(db, inst, str(p.get("brief") or ""))
        return instance_view(db, manuscript, inst["id"])
    if method == "promote":
        # Allowed while a model turn runs: it writes a style law and the
        # decision's law_id, nothing a running turn reads or records.
        # One decision per call; the page offers no bulk promotion.
        from .workflow import promote
        promote.promote(db, manuscript, inst, str(p.get("decision") or ""),
                        statement=p.get("statement"),
                        aspect=str(p.get("aspect") or "") or None,
                        guide=str(p.get("guide") or "") or None, actor="web")
        return instance_view(db, manuscript, inst["id"])
    run = jobs.running(db, inst["id"])
    if run:
        raise ValueError(jobs.busy_message(run))
    if background and is_background(method, p):
        name, inst_id = manuscript["name"], inst["id"]
        kept = {k: v for k, v in p.items() if k not in ("manuscript", "id")}

        def work(jdb: Database) -> None:
            _WORKSPACE.set(workspace)
            m = api.get_manuscript(jdb, name)
            _apply(jdb, m, core.get_instance(jdb, m, inst_id), method, p)

        jobs.start(workspace, inst_id, method, kept, work, api.open_db)
        return instance_view(db, manuscript, inst_id)
    out = _apply(db, manuscript, inst, method, p)
    return out if out is not None else instance_view(db, manuscript, inst["id"])


def _apply(db: Database, manuscript: dict, inst: dict, method: str,
           p: dict[str, Any]) -> Any:
    """One instance method; returns a result to send instead of the
    instance view, or None."""
    if method == "summaries":
        return refresh_summaries(db, manuscript, inst)
    if method == "restart":
        brief = p.get("brief")
        new = core.restart(db, manuscript, inst, str(p.get("reason") or ""),
                           brief=None if brief is None else str(brief))
        return {"id": new["id"], "abandoned": inst["id"], "carry": new["carry"]}
    if method == "recommend":
        recommend(db, manuscript, inst)
    elif method == "rec":
        answer_rec(db, manuscript, inst, p)
    elif method == "list":
        core.change_list(db, manuscript, inst, str(p.get("list") or ""),
                         str(p.get("essay") or ""),
                         add=p.get("change") != "remove",
                         words=str(p.get("words") or ""))
    elif method == "letter":
        draft_letter(db, manuscript, inst)
    elif method == "letter_reply":
        if inst["step"] not in core.LETTER_STEPS:
            raise ValueError("the letter is discussed from the letter step to "
                             f"the filters; the instance is at {inst['step']}")
        text = str(p.get("text") or "").strip()
        if not text:
            raise ValueError("say something to discuss the letter")
        # Under the letter's brief, and recorded at the letter step, at
        # whatever step the instance is (core.letter_turn_instance).
        reply, ref = core.ask(db, manuscript, inst, "reply", text,
                              engine=_engine(inst),
                              context_extra=_context_extra(db, manuscript, inst),
                              as_step="letter")
        core.record_turn(db, core.letter_turn_instance(inst), "reply",
                         author_text=text, unit_label=LETTER_LABEL,
                         reply=reply, session_ref=ref)
    elif method == "revise_letter":
        revise_letter(db, manuscript, inst, str(p.get("note") or ""))
    elif method == "reopen_decline":
        decline_reopen(db, manuscript, inst, str(p.get("label") or ""))
    elif method == "finding":
        core.rule_finding(db, inst, str(p.get("item") or ""),
                          str(p.get("verdict") or ""),
                          str(p.get("reason") or ""), actor="web")
    elif method == "finding_edit":
        fields = p.get("fields") or {}
        if not isinstance(fields, dict):
            raise ValueError("fields is an object of field: text")
        core.edit_finding(db, inst, str(p.get("item") or ""), fields, actor="web")
    elif method == "keep":
        items = p.get("items")
        if not isinstance(items, list):
            raise ValueError("items is the whole Keep list, one string each")
        edit_keep(db, inst, [str(k) for k in items], actor="web")
    elif method == "accept_letter":
        accept_letter(db, inst)
    elif method == "propose":
        propose(db, manuscript, inst, p.get("label") or None)
    elif method == "act":
        act(db, manuscript, inst, str(p.get("label") or ""),
            str(p.get("action") or ""), text=str(p.get("text") or ""),
            blurbs=_blurb_list(p), free=str(p.get("free") or ""),
            with_discussion=bool(p.get("with_discussion")),
            where=str(p.get("where") or ""), quote=str(p.get("quote") or ""))
    elif method == "discuss":
        discuss_unit(db, manuscript, inst, str(p.get("label") or ""),
                     str(p.get("text") or ""))
    elif method == "reopen":
        reopen(db, manuscript, inst, str(p.get("label") or ""))
    elif method == "beat_done":
        walk = _walk(db, manuscript, inst, "construction")
        if inst["step"] != "construction" or walk["mode"] != "beats":
            raise ValueError("beat by beat only, at construction")
        if any(u["state"] == "pending" for u in walk["units"]):
            raise ValueError("rule the open form first")
        core.record_turn(db, inst, "accept", unit_label=f"b{walk['beat']}:done")
    elif method == "conclude":
        walk = _walk(db, manuscript, inst, "construction")
        if inst["step"] != "construction" or walk["mode"] != "beats":
            raise ValueError("the beats are concluded at construction, beat by beat")
        if any(u["state"] == "pending" for u in walk["units"]):
            raise ValueError("rule the open form first")
        if not walk["cover_asked"] and walk["unclaimed"]:
            # First what the beats left standing, one form each.
            core.record_turn(db, inst, "accept", unit_label=COVER_LABEL)
        else:
            left = [u["label"] for u in walk["units"]
                    if u["kind"] == "cover" and u["state"] == "open"]
            if left:
                raise ValueError("rule what the beats left standing first: "
                                 + ", ".join(left[:5]))
            core.record_turn(db, inst, "accept", unit_label=CONCLUDE_LABEL)
    elif method == "lint":
        run_lint(db, manuscript, inst)
    elif method == "wrapup":
        wrapup(db, manuscript, inst)
    elif method == "principle":
        scope = str(p.get("scope") or "").strip()
        core.decide(db, inst, "principle", str(p.get("words") or ""),
                    reason=("candidate style rule, ticked at the wrap-up"
                            + (f"; scope: {scope}" if scope else "")))
    elif method == "filters":
        names = p.get("names") or []
        if isinstance(names, str):
            names = names.split(",")
        core.set_filters(db, inst, [str(n) for n in names],
                         [x["name"] for x in passes()])
    elif method == "skip":
        moved = core.skip(db, inst, str(p.get("reason") or ""), p.get("to") or None)
        landed = _land_after(db, manuscript, inst, moved)
        out = instance_view(db, manuscript, inst["id"])
        out["landed"] = landed
        return out
    elif method == "advance":
        why = page_gate(db, manuscript, inst)
        if why:
            raise ValueError(why)
        moved = core.advance(db, inst)
        landed = _land_after(db, manuscript, inst, moved)
        out = instance_view(db, manuscript, inst["id"])
        out["landed"] = landed
        return out
    elif method in ("land", "leave"):
        from .workflow import landing
        if method == "leave":
            landing.leave(db, manuscript, inst, str(p.get("label") or ""),
                          actor="web")
            landed = None
        else:
            landed = landing.land(db, manuscript, inst,
                                  dry_run=bool(p.get("dry_run")),
                                  road=_road(db, manuscript), trigger="page",
                                  actor="web", workspace=_WORKSPACE.get())
        out = instance_view(db, manuscript, inst["id"])
        out["landed"] = landed
        return out
    elif method == "engine":
        core.set_engine(db, inst, str(p.get("engine") or ""),
                        p.get("model") if p.get("model") is not None else None,
                        p.get("reason") or None)
    elif method == "unit":
        if inst["step"] in ("construction", "filters") and any(
                u["state"] == "pending"
                for u in _walk(db, manuscript, inst, inst["step"])["units"]):
            raise ValueError("rule the open form before switching the unit")
        core.set_unit(db, inst, p.get("unit") or None, phase=p.get("phase") or None,
                      mode=p.get("mode") or None, reason=p.get("reason") or None)
    return None
