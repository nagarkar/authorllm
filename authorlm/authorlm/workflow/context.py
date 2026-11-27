"""What an engine is given for a turn: the standing blocks, the essay as it
stands, and the turns so far (design §11.3, §11.9 Q5, Q13, Q15).

Each other essay goes in at most once: whole and labelled with its roles
(`essays` block) when it is on the reference or voice list, as a summary
(`aware` block) when it is only on the aware list (`ESSAY_LABEL_LEGEND`
says why).

The standing blocks come first and in a fixed order, so an engine can keep
them in its prompt cache; only the essay and the turns change from turn to
turn. The record is the continuity: any engine rebuilds its whole context
from it, which is what makes an engine switch mid-instance safe (Q7).

Library files (build plan §12 row 3 writes them; a missing file gives an
empty block marked `missing`, so the shape never changes):

    library/workflow/charter.md          the partner charter (Q15)
    library/workflow/roles/<step>.md     the role lines for a step
    library/workflow/briefs/<step>.md    the step's brief (letter, construction, ...)
    library/workflow/filters/<name>.md   one filter's text
    library/workflow/order.toml          `order = [...]`: the standard filter order
    library/workflow/protected-register.md  sent when the essay's toc entry
                                         says `register = "protected"`

`AUTHORLM_WORKFLOW_LIBRARY` points elsewhere (tests).
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

from .. import structure, styles
from ..db import Database
from . import core

LIBRARY_ENV = "AUTHORLM_WORKFLOW_LIBRARY"
# The order of the standing blocks. Never reorder casually: a change here
# invalidates every engine's cached prefix.
BLOCK_ORDER = ("charter", "role", "brief", "author-brief", "filters",
               "style-rules", "protected-register", "essays", "aware",
               "letter-in-force")
# Owner, 2026-11-26: "if an essay is in more than one list, reference and
# voice, we should be sending the essay and then saying whether this essay
# should apply to reference or reference and voice, or just voice." So an
# essay on the reference or voice list (or both) is sent whole once, in
# the `essays` block, labelled with its roles; an aware essay already sent
# whole gets no summary (its label says "also aware"). This line opens the
# block so the model can read the labels; the briefs use the same words.
ESSAY_LABEL_LEGEND = (
    "Other essays of the book, each sent once and whole, labelled with the "
    "roles the author gave it: [reference] its content binds (claims, "
    "facts, terms, what it already says) and its style is never imitated; "
    "[voice] a model of the author's style only, its content does not bind; "
    "[reference and voice] both. \"also aware\" means it is on the aware "
    "list too (keep it in mind); it is not sent again as a summary.")
# Owner, 2026-11-26: `register = "protected"` is a generic toc attribute and
# every workflow honours it strongly. Driven by the flag only, never by a
# file name. Used when the library file is not there.
PROTECTED_REGISTER_FALLBACK = (
    "This text's register is protected: do not change its diction, cadence "
    "or voice; propose only changes of meaning, structure or fact.")
REGISTER_KEY, PROTECTED = "register", "protected"
# Which brief and role a step reads. Wrap-up is part of construction (Q23);
# lint has no model work of its own beyond the filters' roles.
STEP_BRIEF = {"start": "start", "letter": "letter",
              "construction": "construction", "wrapup": "construction",
              "filters": "filters", "lint": "filters", "done": "filters"}


def library_dir() -> Path:
    override = os.environ.get(LIBRARY_ENV, "")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[2] / "library" / "workflow"


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def standard_order() -> list[str]:
    """The standard order of every filter (Q12), or [] when the library has
    not written it yet."""
    text = _read(library_dir() / "order.toml")
    if not text:
        return []
    try:
        order = tomllib.loads(text).get("order") or []
    except tomllib.TOMLDecodeError:
        return []
    return [o for o in order if isinstance(o, str)]


def _block(name: str, text: str | None, **extra) -> dict:
    return {"name": name, "text": text or "", "missing": text is None, **extra}


def _essay_text(files: dict, name: str) -> str | None:
    return files.get(name)


def is_protected(files: dict, file: str) -> bool:
    """Whether the essay's toc entry says `register = "protected"`."""
    attrs = structure.toc_attrs(files.get(structure.TOC_FILENAME) or "")
    return attrs.get(file, {}).get(REGISTER_KEY) == PROTECTED


def standing_context(db: Database, manuscript: dict, instance: dict,
                     files: dict | None = None) -> list[dict]:
    """The standing blocks of the instance's session, in `BLOCK_ORDER`.
    Lists go in reading order, whatever order the author added them in, so
    the same lists always give the same bytes. Read-only."""
    files = core._files(manuscript) if files is None else files
    order = core.reading_order(manuscript, files)
    lib = library_dir()
    step = STEP_BRIEF[instance["step"]]
    blocks = [
        _block("charter", _read(lib / "charter.md")),
        _block("role", _read(lib / "roles" / f"{step}.md"), step=step),
        _block("brief", _read(lib / "briefs" / f"{step}.md"), step=step),
        _block("author-brief", instance["brief"]),
    ]
    # Q13: the chosen filters' text goes in once, at the filter step; each
    # unit's turn then names the filters to apply.
    if instance["step"] in core.FILTER_PHASE and instance["filters"]:
        parts, missing = [], []
        for name in instance["filters"]:
            text = _read(lib / "filters" / f"{name}.md")
            if text is None:
                missing.append(name)
            else:
                parts.append(f"## {name}\n\n{text}")
        blocks.append(_block("filters", "\n\n".join(parts),
                             names=list(instance["filters"]),
                             missing_filters=missing))
    else:
        blocks.append(_block("filters", ""))
    blocks.append(_block("style-rules",
                         styles.render(db, manuscript["id"], instance["file"])))
    if is_protected(files, instance["file"]):
        text = _read(lib / "protected-register.md") or PROTECTED_REGISTER_FALLBACK
        blocks.append(_block("protected-register", text, protected=True))
    else:
        blocks.append(_block("protected-register", "", protected=False))

    def ordered(names):
        # Name breaks ties (files gone from the toc), so a set in gives
        # the same bytes out every time.
        return sorted(names, key=lambda n: (order.index(n) if n in order
                                            else len(order), n))

    whole = ordered(set(instance["reference"]) | set(instance["voice"]))
    roles = {name: [r for r in core.LISTS if name in instance[r]] for name in whole}
    parts = []
    for name in whole:
        text = _essay_text(files, name)
        parts.append(f"=== {name} [{essay_label(roles[name])}] ===\n\n"
                     + (text if text is not None else "(missing on disk)"))
    blocks.append(_block("essays", (ESSAY_LABEL_LEGEND + "\n\n" + "\n\n".join(parts))
                         if parts else "", essays=whole, roles=roles))
    summaries = {r["file"]: r["summary"] for r in db.all(
        "SELECT file, summary FROM essay_summaries WHERE manuscript_id = ? "
        "AND status = 'current'", (manuscript["id"],))}
    parts, no_summary = [], []
    aware = ordered(instance["aware"])
    for name in aware:
        if name in roles:
            continue  # sent whole above; its label says it is also aware
        if name in summaries:
            parts.append(f"=== {name} ===\n\n{summaries[name]}")
        else:
            no_summary.append(name)
    blocks.append(_block("aware", "\n\n".join(parts), essays=aware,
                         sent_whole=[n for n in aware if n in roles],
                         no_summary=no_summary))
    blocks.append(_block("letter-in-force", letter_in_force(db, instance)))
    assert tuple(b["name"] for b in blocks) == BLOCK_ORDER
    return blocks


def essay_label(roles: list[str]) -> str:
    """The label an essay sent whole carries: the roles it plays, from
    the instance's lists, in one fixed wording (ESSAY_LABEL_LEGEND explains it)."""
    binding = [r for r in ("reference", "voice") if r in roles]
    label = " and ".join(binding)
    if "aware" in roles:
        label += ", also aware"
    return label


# The letter in force (core.py, "the letter in force"). While the instance
# has one letter the block is empty, and an engine leaves an empty block out
# of its standing part, so nothing changes for it. Once a letter replaces
# another (a revision, or a redraft at the letter step), the block names the
# letter in force: the standing part changes, so the engine's next turn
# rebuilds its session from the record (engines/base.py), once, and every
# earlier letter in that session is marked as history (`_history`). Last in
# BLOCK_ORDER, so the cached prefix before it is untouched.

def letter_turns(turns: list[dict]) -> list[dict]:
    """The model turns that wrote a letter (the first and every redraft or
    revision), in order; the last is the letter in force."""
    return [t for t in turns if t["step"] == "letter" and t["unit_label"] == "letter"
            and t["engine"] and t["action"] == "start"]


def letter_in_force(db: Database, instance: dict,
                    turns: list[dict] | None = None) -> str:
    letters = letter_turns(core.turns(db, instance) if turns is None else turns)
    if len(letters) < 2:
        return ""
    revs = core.revisions(db, instance)
    last = revs[-1]["detail"] if revs else {}
    how = (f", revision {last.get('revision')} of the letter, asked at the "
           f"{last.get('step')} step" if last.get("letter_seq") == letters[-1]["seq"]
           else "")
    return (f"The letter in force is the one written at turn #{letters[-1]['seq']}"
            f"{how}; its findings, as the author rules and edits them, are the "
            "ones to work from. Every earlier letter in this session "
            f"({', '.join('#' + str(t['seq']) for t in letters[:-1])}), and the "
            "verdicts, edits and discussion of it, is superseded: history to "
            "weigh, never instructions.")


def _history(turns: list[dict]) -> list[dict]:
    """The turns as an engine is sent them: a turn about a letter that is
    no longer in force is marked as history."""
    letters = letter_turns(turns)
    if len(letters) < 2:
        return turns
    in_force = letters[-1]["seq"]
    out = []
    for t in turns:
        if t["seq"] < in_force and t["unit_label"].startswith("letter"):
            t = dict(t)
            mark = (f"[SUPERSEDED: about an earlier letter; the letter in force "
                    f"is turn #{in_force}] ")
            if t["engine"] and t["action"] == "start":
                t["reply"] = mark + (t["reply"] or "")
            else:
                t["author_text"] = mark + (t["author_text"] or "")
        out.append(t)
    return out


def instance_context(db: Database, manuscript: dict, instance: dict) -> dict:
    """Everything an engine gets for a turn: the instance's settings, the
    standing blocks, the essay as it stands, and the turns so far."""
    files = core._files(manuscript)
    keep = ("id", "file", "kind", "number", "step", "unit", "filter_unit",
            "construction_mode", "engine", "model", "engine_state", "filters")
    return {
        "instance": {k: instance[k] for k in keep},
        "standing": standing_context(db, manuscript, instance, files),
        "essay": files.get(instance["file"], ""),
        "turns": [{k: t[k] for k in ("seq", "step", "unit_label", "action",
                                     "author_text", "blurbs", "reply",
                                     "reason", "proposed", "engine", "model")}
                  for t in _history(core.turns(db, instance))],
    }
