"""Style guides — ratified prose law, composed per file.

A guide is a named scope in a single-parent tree (root = manuscript house
style); files attach to exactly one guide (DB-enforced) and unattached
files fall back to the root. An element is one rule: an aspect, a
statement, optional free-text notes, and an optional `overrides` link that
displaces an inherited element. Composition is deterministic set
arithmetic — no model in the loop; the author ratifies every row.
"""

from __future__ import annotations

from .db import Database, ko_fields

ASPECTS = {
    "register", "lexicon", "syntax", "structure", "formatting",
    "citation", "rhetoric", "figure", "tone", "illustration",
    "illustration-placement",
}
# 'figure' is figurative language — metaphor and analogy law for PROSE.
# 'illustration' is image law, consumed only by the illustration
# renderer (illus.illustration_law). 'illustration-placement' is
# where-images-belong law, consumed only by the placement spot-finder —
# never composed into render prompts. The three must never mix: prose
# motif guidance injected into an image prompt draws literal chains
# and flames (it-3e79bce24f73).


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
                notes: str | None = None, overrides: str | None = None) -> dict:
    if aspect not in ASPECTS:
        raise ValueError(f"unknown aspect '{aspect}' (use one of {sorted(ASPECTS)})")
    if (guide is None) == (file is None):
        raise ValueError("an element is scoped to exactly one of: a guide, a file")
    row = ko_fields("se")
    row.update(
        manuscript_id=manuscript_id,
        guide_id=guide["id"] if guide else None,
        file=file, aspect=aspect, statement=statement, notes=notes,
        status="active", overrides=overrides,
    )
    db.insert("style_elements", row)
    return row


def retire_element(db: Database, element: dict) -> None:
    db.update("style_elements", element["id"], {"status": "retired"})


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


def effective_style(db: Database, manuscript_id: str, file: str) -> list[dict]:
    """Active elements governing `file`, nearest scope first: file-local
    rows, then each guide up the chain — minus every element displaced by
    a nearer element's `overrides` link."""
    layers: list[list[dict]] = [[dict(e) for e in db.all(
        "SELECT * FROM style_elements WHERE manuscript_id = ? AND file = ? "
        "AND status = 'active' ORDER BY created_at",
        (manuscript_id, file),
    )]]
    for guide in guide_chain(db, manuscript_id, file):
        layers.append([dict(e) for e in db.all(
            "SELECT * FROM style_elements WHERE manuscript_id = ? AND guide_id = ? "
            "AND status = 'active' ORDER BY created_at",
            (manuscript_id, guide["id"]),
        )])
    displaced = {e["overrides"] for layer in layers for e in layer if e["overrides"]}
    return [e for layer in layers for e in layer if e["id"] not in displaced]


def render(db: Database, manuscript_id: str, file: str) -> str:
    """The effective guide as prose — injected into drafting prompts so
    generated text arrives in the file's ratified register."""
    elements = effective_style(db, manuscript_id, file)
    if not elements:
        return ""
    lines = [f"STYLE GUIDE for {file} (ratified by the author — follow strictly):"]
    for element in elements:
        lines.append(f"- [{element['aspect']}] {element['statement']}")
    return "\n".join(lines)
