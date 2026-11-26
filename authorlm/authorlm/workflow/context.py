"""What an engine is given for a turn: the standing blocks, the essay as it
stands, and the turns so far (design §11.3, §11.9 Q5, Q13, Q15).

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
               "style-rules", "protected-register", "reference", "voice",
               "aware")
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
        return sorted(names, key=lambda n: order.index(n) if n in order
                      else len(order))

    for which in ("reference", "voice"):
        parts = []
        for name in ordered(instance[which]):
            text = _essay_text(files, name)
            parts.append(f"=== {name} ===\n\n"
                         + (text if text is not None else "(missing on disk)"))
        blocks.append(_block(which, "\n\n".join(parts),
                             essays=ordered(instance[which])))
    summaries = {r["file"]: r["summary"] for r in db.all(
        "SELECT file, summary FROM essay_summaries WHERE manuscript_id = ? "
        "AND status = 'current'", (manuscript["id"],))}
    parts, no_summary = [], []
    for name in ordered(instance["aware"]):
        if name in summaries:
            parts.append(f"=== {name} ===\n\n{summaries[name]}")
        else:
            no_summary.append(name)
    blocks.append(_block("aware", "\n\n".join(parts),
                         essays=ordered(instance["aware"]),
                         no_summary=no_summary))
    assert tuple(b["name"] for b in blocks) == BLOCK_ORDER
    return blocks


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
                  for t in core.turns(db, instance)],
    }
