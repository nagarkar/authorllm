"""Style guides — ratified prose law, composed per file.

A guide is a named scope in a single-parent tree (root = manuscript house
style); files attach to exactly one guide (DB-enforced) and unattached
files fall back to the root. An element is one rule: an aspect, a
statement, optional free-text notes, and an optional `overrides` link that
displaces an inherited element. Composition is deterministic set
arithmetic — no model in the loop; the author ratifies every row.

Below every manuscript's root guide sits one more layer, the DEFAULTS:
author-ratified law for the conventions where English allows more than
one valid form (serial comma, possessive of names in s, dash style…),
kept in the repo at `library/style-defaults.toml` and read in place, so a
change there reaches every book. A default is an element with id
`default:<key>`; a manuscript law departs from it by naming that id in
`overrides` (a comma-separated list when one law displaces several).

A law changes in place: an edit (`edit_element`) keeps its id, because
other rows cite ids (`overrides`, statements that name `se-…`, lint's
copy of the em-dash law), and bumps `version`. Its history, the wording
before every edit, each retire with its reason, un-retire, move and copy,
is one `edit_events` row per change with `source = 'style'`, the time,
the user (`created_by`) and the road (`actor`: cli, web, chat), so no
earlier wording is lost and any of it can be put back by editing again.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import tomllib
from pathlib import Path

from .db import Database, ko_fields, now_iso

DEFAULTS_ENV = "AUTHORLM_STYLE_DEFAULTS"
DEFAULT_PREFIX = "default:"


def defaults_path() -> Path:
    """`authorlm/library/style-defaults.toml` in the checkout, or the file
    named by AUTHORLM_STYLE_DEFAULTS (tests point it at a temp file, or at
    a missing one to see a manuscript alone)."""
    override = os.environ.get(DEFAULTS_ENV, "")
    if override:
        return Path(override).expanduser()
    return Path(__file__).resolve().parent.parent / "library" / "style-defaults.toml"


def default_laws(manuscript_id: str = "") -> list[dict]:
    """The default layer as element dicts (id `default:<key>`), in file
    order. A missing file is no defaults; a malformed row fails loudly."""
    path = defaults_path()
    if not path.is_file():
        return []
    rows = tomllib.loads(path.read_text(encoding="utf-8")).get("default", [])
    out: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        key, aspect, statement = row.get("key"), row.get("aspect"), row.get("statement")
        if not (key and aspect and statement):
            raise ValueError(f"{path}: a default needs key, aspect and statement: {row}")
        if aspect not in ASPECTS:
            raise ValueError(f"{path}: default '{key}' has unknown aspect '{aspect}'")
        if key in seen:
            raise ValueError(f"{path}: default '{key}' appears twice")
        seen.add(key)
        out.append({
            "id": DEFAULT_PREFIX + key, "default": key,
            "manuscript_id": manuscript_id, "guide_id": None, "file": None,
            "aspect": aspect, "statement": statement, "notes": row.get("notes"),
            "status": "active", "overrides": None, "source_id": None,
        })
    return out


def overridden_ids(value: str | None) -> list[str]:
    """The ids an element's `overrides` names, in order: one id, or a
    comma list."""
    return list(dict.fromkeys(
        part.strip() for part in (value or "").split(",") if part.strip()))

ASPECTS = {
    "register", "lexicon", "syntax", "structure", "formatting",
    "citation", "rhetoric", "figure", "tone", "illustration",
    "illustration-placement", "pronunciation", "clip",
    "concept-note", "concept-identity", "concept-lifecycle",
    "concept-relation",
}
# 'pronunciation' is law about HOW A TERM IS SAID ALOUD ("never respell
# a Sanskrit term for an English ear"). Like the four 'concept-*'
# aspects it names the QUESTION being decided, and it is consumed by the
# proposal screen for `pronunciation` proposals (loop.REGISTRY). It
# exists so that spec's aspect is a real one: until the author ratifies
# law under it, `active_law` finds nothing and the screen is inert by
# construction.
#
# 'clip' is generated-video law (look, motion, sound), consumed only by
# media_gen.clip_law and composed into Veo prompts — never into prose or
# image prompts (docs/video-clips-design.md).
#
# 'figure' is figurative language — metaphor and analogy law for PROSE.
# 'illustration' is image law, consumed only by the illustration
# renderer (illus.illustration_law). 'illustration-placement' is
# where-images-belong law, consumed only by the placement spot-finder —
# never composed into render prompts. The three must never mix: prose
# motif guidance injected into an image prompt draws literal chains
# and flames (it-3e79bce24f73).
#
# The three 'concept-*' aspects govern the CONCEPT GRAPH, not prose, and are
# consumed only by the proposal screen (loop.active_law). Each names the
# QUESTION being decided, not the queue asking it, so two proposal kinds
# that ask the same question share one aspect and law written for it
# transfers correctly:
#   concept-note      how a concept's note must read        (note_update)
#   concept-identity  whether two concepts are the same     (alias,
#                                                            variant_of_retired)
#   concept-lifecycle whether a concept should live         (vanished, revival)
#   concept-relation  whether a relationship should stand   (edge_reproposal)
# edge_reproposal was briefly on concept-identity, which silently armed the
# alias laws ("metaphor is not identity") against reproposed EDGES — a
# relationship is not an identity claim, and the law did not transfer.
# They are law in the same sense as the rest — ratified by the author, never
# machine-written — but they must never reach a drafting prompt, exactly as
# illustration law must never reach a prose one.


def create_guide(db: Database, manuscript_id: str, name: str,
                 parent_name: str | None = None) -> dict:
    existing = get_guide(db, manuscript_id, name)
    if existing:
        return dict(existing)
    parent_id = None
    if parent_name:
        parent = get_guide(db, manuscript_id, parent_name)
        if not parent:
            raise ValueError(f"no style guide named '{parent_name}'")
        parent_id = parent["id"]
    row = ko_fields("sg")
    row.update(manuscript_id=manuscript_id, name=name, parent=parent_id)
    db.insert("style_guides", row)
    return row


def get_guide(db: Database, manuscript_id: str, name: str):
    return db.one(
        "SELECT * FROM style_guides WHERE manuscript_id = ? AND lower(name) = lower(?)",
        (manuscript_id, name),
    )


def root_guide(db: Database, manuscript_id: str):
    return db.one(
        "SELECT * FROM style_guides WHERE manuscript_id = ? AND parent IS NULL",
        (manuscript_id,),
    )


def attach_file(db: Database, manuscript_id: str, file: str, guide: dict) -> dict:
    existing = db.one(
        "SELECT * FROM style_attachments WHERE manuscript_id = ? AND file = ?",
        (manuscript_id, file),
    )
    if existing:
        db.update("style_attachments", existing["id"], {"guide_id": guide["id"]})
        return {**dict(existing), "guide_id": guide["id"]}
    row = ko_fields("sa")
    row.update(manuscript_id=manuscript_id, file=file, guide_id=guide["id"])
    db.insert("style_attachments", row)
    return row


def add_element(db: Database, manuscript_id: str, aspect: str, statement: str,
                guide: dict | None = None, file: str | None = None,
                notes: str | None = None, overrides: str | None = None,
                status: str = "active", source_id: str | None = None) -> dict:
    if aspect not in ASPECTS:
        raise ValueError(f"unknown aspect '{aspect}' (use one of {sorted(ASPECTS)})")
    if (guide is None) == (file is None):
        raise ValueError("an element is scoped to exactly one of: a guide, a file")
    row = ko_fields("se")
    row.update(
        manuscript_id=manuscript_id,
        guide_id=guide["id"] if guide else None,
        file=file, aspect=aspect, statement=statement, notes=notes,
        status=status, overrides=overrides,
        source_id=source_id or db.source("author"),
    )
    db.insert("style_laws", row)
    return row


# edit_events.source for a style law's history (see the module docstring).
HISTORY_SOURCE = "style"
# The fields an edit may change; scope moves go through move, status
# through retire / un-retire.
EDITABLE = ("statement", "notes", "aspect")


def record_event(db: Database, element: dict, event: str, actor: str,
                 detail: dict | None = None) -> None:
    """One history row for a law: add | edit | retire | unretire | move |
    copy, with its detail (before/after, reason, from/to, copy ids)."""
    row = ko_fields("eev")
    row.update(manuscript_id=element["manuscript_id"], source=HISTORY_SOURCE,
               run="", item_id=element["id"], file=element.get("file") or "",
               event=event, verdict=None,
               detail=json.dumps(detail or {}, ensure_ascii=False),
               at=now_iso(), actor=actor or "")
    db.insert("edit_events", row)


def law_history(db: Database, manuscript_id: str, law_id: str) -> list[dict]:
    """A law's history, oldest first: event, at, by (the user), actor (the
    road: cli, web, chat) and detail."""
    rows = db.all(
        "SELECT * FROM edit_events WHERE manuscript_id = ? AND source = ? "
        "AND item_id = ? ORDER BY at, rowid",
        (manuscript_id, HISTORY_SOURCE, law_id))
    return [{"event": r["event"], "at": r["at"], "by": r["created_by"],
             "actor": r["actor"], "detail": json.loads(r["detail"] or "{}")}
            for r in rows]


def histories(db: Database, manuscript_id: str) -> dict[str, list[dict]]:
    """Every law's history at once (law id → law_history)."""
    out: dict[str, list[dict]] = {}
    for r in db.all(
            "SELECT * FROM edit_events WHERE manuscript_id = ? AND source = ? "
            "ORDER BY at, rowid", (manuscript_id, HISTORY_SOURCE)):
        out.setdefault(r["item_id"], []).append(
            {"event": r["event"], "at": r["at"], "by": r["created_by"],
             "actor": r["actor"], "detail": json.loads(r["detail"] or "{}")})
    return out


def edit_element(db: Database, element: dict, actor: str = "cli",
                 **changes: str | None) -> dict:
    """Change a law's statement, notes or aspect in place: same id,
    version + 1, the earlier wording kept in history. A change left as
    None is no change; notes "" clears the note."""
    fields: dict = {}
    for key, value in changes.items():
        if key not in EDITABLE:
            raise ValueError(f"a style law's {key} is not edited here")
        if value is None:
            continue
        value = value.strip()
        if key == "statement" and not value:
            raise ValueError("a style law needs a statement")
        if key == "aspect" and value not in ASPECTS:
            raise ValueError(f"unknown aspect '{value}' (use one of {sorted(ASPECTS)})")
        value = value or None
        if value != element[key]:
            fields[key] = value
    if not fields:
        raise ValueError("nothing to change: the law already reads so")
    with db.transaction():
        db.update("style_laws", element["id"], fields)
        record_event(db, element, "edit", actor, {
            "before": {k: element[k] for k in fields},
            "after": fields, "version": element["version"] + 1})
    return {**element, **fields, "version": element["version"] + 1}


def retire_element(db: Database, element: dict, reason: str | None = None,
                   actor: str = "cli", replaced_by: str | None = None) -> None:
    with db.transaction():
        db.update("style_laws", element["id"], {"status": "retired"})
        detail: dict = {"reason": reason} if reason else {}
        if replaced_by:
            detail["replaced_by"] = replaced_by
        record_event(db, element, "retire", actor, detail)


def unretire_element(db: Database, element: dict, actor: str = "cli",
                     reason: str | None = None) -> None:
    with db.transaction():
        db.update("style_laws", element["id"], {"status": "active"})
        record_event(db, element, "unretire", actor,
                     {"reason": reason} if reason else {})


def similarity(a: str, b: str) -> float:
    """How alike two statements read, 0..1: the larger of the character
    match ratio and the overlap of their words. A cue for a person, never
    a decision: it only ever warns."""
    a, b = a.lower().strip(), b.lower().strip()
    ratio = difflib.SequenceMatcher(None, a, b).ratio()
    wa, wb = set(re.findall(r"[a-z']+", a)), set(re.findall(r"[a-z']+", b))
    jaccard = len(wa & wb) / len(wa | wb) if wa | wb else 0.0
    return max(ratio, jaccard)


def guide_chain(db: Database, manuscript_id: str, file: str) -> list[dict]:
    """Nearest-first guides governing `file`: its attached guide (or the
    root when unattached) followed by that guide's ancestors."""
    attachment = db.one(
        "SELECT * FROM style_attachments WHERE manuscript_id = ? AND file = ?",
        (manuscript_id, file),
    )
    guide = (db.one("SELECT * FROM style_guides WHERE id = ?",
                    (attachment["guide_id"],))
             if attachment else root_guide(db, manuscript_id))
    chain: list[dict] = []
    seen: set[str] = set()
    while guide and guide["id"] not in seen:
        chain.append(dict(guide))
        seen.add(guide["id"])
        guide = (db.one("SELECT * FROM style_guides WHERE id = ?", (guide["parent"],))
                 if guide["parent"] else None)
    return chain


def effective_layers(db: Database, manuscript_id: str, file: str) -> list[dict]:
    """Every active law that reaches `file`, nearest scope first (file-
    local rows, each guide up the chain, then the defaults), each with
    `displaced_by`: the ids of the nearer laws whose `overrides` names it
    (empty for a law in force)."""
    layers: list[list[dict]] = [[dict(e) for e in db.all(
        "SELECT * FROM style_laws WHERE manuscript_id = ? AND file = ? "
        "AND status = 'active' ORDER BY created_at",
        (manuscript_id, file),
    )]]
    for guide in guide_chain(db, manuscript_id, file):
        layers.append([dict(e) for e in db.all(
            "SELECT * FROM style_laws WHERE manuscript_id = ? AND guide_id = ? "
            "AND status = 'active' ORDER BY created_at",
            (manuscript_id, guide["id"]),
        )])
    layers.append(default_laws(manuscript_id))
    by: dict[str, list[str]] = {}
    for layer in layers:
        for e in layer:
            for i in overridden_ids(e["overrides"]):
                by.setdefault(i, []).append(e["id"])
    return [{**e, "displaced_by": by.get(e["id"], [])}
            for layer in layers for e in layer]


def effective_style(db: Database, manuscript_id: str, file: str) -> list[dict]:
    """Active elements governing `file`, nearest scope first: file-local
    rows, then each guide up the chain, then the defaults — minus every
    element displaced by a nearer element's `overrides` link."""
    out = []
    for e in effective_layers(db, manuscript_id, file):
        if not e.pop("displaced_by"):
            out.append(e)
    return out


# The aspects that govern prose. Every other aspect (image, placement,
# clip, pronunciation, concept-*) has its own consumer and must never reach
# a drafting or editing prompt.
PROSE_ASPECTS = ("register", "lexicon", "syntax", "structure", "formatting",
                 "citation", "rhetoric", "figure", "tone")


def render(db: Database, manuscript_id: str, file: str) -> str:
    """The effective guide as prose — injected into drafting prompts so
    generated text arrives in the file's ratified register. Prose aspects
    only (PROSE_ASPECTS)."""
    elements = [e for e in effective_style(db, manuscript_id, file)
                if e["aspect"] in PROSE_ASPECTS]
    if not elements:
        return ""
    lines = [f"STYLE GUIDE for {file} (ratified by the author — follow strictly):"]
    for element in elements:
        lines.append(f"- [{element['aspect']}] {element['statement']}")
    return "\n".join(lines)
