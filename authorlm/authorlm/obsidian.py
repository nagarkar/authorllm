"""Export the Concept Graph as Obsidian stub notes.

One note per concept in `_concepts/` inside the manuscript folder (a
directory observation ignores), with outgoing relationships as wikilinks —
so Obsidian's built-in graph view renders AuthorLM's Concept Graph natively
when the manuscript folder is opened as a vault.

The export is read-only and regenerable: every stub carries an
`authorlm: exported` frontmatter marker, and each export removes previously
exported stubs before writing, so retired concepts disappear and manual
notes in the same folder are never touched.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from .db import Database

MARKER = "authorlm: exported"
_UNSAFE = re.compile(r'[\\/:*?"<>|#^\[\]]')


def note_name(concept_name: str) -> str:
    """Obsidian-safe note name; also used as the wikilink target."""
    return _UNSAFE.sub("-", concept_name).strip() or "concept"


def export_obsidian(db: Database, manuscript: dict, out_dir: str | None = None) -> dict:
    out = Path(out_dir) if out_dir else Path(manuscript["path"]) / "_concepts"
    out.mkdir(parents=True, exist_ok=True)

    removed = 0
    for existing in out.glob("*.md"):
        try:
            head = existing.read_text(encoding="utf-8", errors="ignore")[:300]
        except OSError:
            continue
        if MARKER in head:
            existing.unlink()
            removed += 1

    nodes = db.all(
        "SELECT * FROM concept_nodes WHERE manuscript_id = ? AND status != 'retired'",
        (manuscript["id"],),
    )
    by_id = {n["id"]: n for n in nodes}
    outgoing = defaultdict(list)
    incoming = defaultdict(list)
    link_count = 0
    for edge in db.all(
        "SELECT * FROM concept_edges WHERE manuscript_id = ? "
        "AND status NOT IN ('rejected', 'retired')",
        (manuscript["id"],),
    ):
        if edge["from_node"] in by_id and edge["to_node"] in by_id:
            outgoing[edge["from_node"]].append(edge)
            incoming[edge["to_node"]].append(edge)
            link_count += 1

    for node in nodes:
        lines = [
            "---",
            MARKER,
            f"kind: {node['kind']}",
            f"status: {node['status']}",
        ]
        if node["introduced_in"]:
            lines.append(f"introduced_in: {node['introduced_in']}")
        lines += ["---", "", f"# {node['name']}", ""]
        if node["notes"]:
            lines += [node["notes"], ""]
        if outgoing[node["id"]]:
            lines.append("## Relationships")
            for edge in outgoing[node["id"]]:
                target = note_name(by_id[edge["to_node"]]["name"])
                inferred = " *(inferred)*" if edge["status"] == "inferred" else ""
                lines.append(f"- {edge['relation']} [[{target}]]{inferred}")
            lines.append("")
        if incoming[node["id"]]:
            # Plain text (no wikilinks) so the graph shows each edge once,
            # in its true direction.
            lines.append("## Referenced by")
            for edge in incoming[node["id"]]:
                source = by_id[edge["from_node"]]["name"]
                lines.append(f"- {source} — {edge['relation']} → this")
            lines.append("")
        (out / f"{note_name(node['name'])}.md").write_text(
            "\n".join(lines), encoding="utf-8"
        )

    return {"dir": str(out), "notes": len(nodes), "links": link_count, "removed": removed}
