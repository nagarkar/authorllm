"""LLM-backed concept and relationship extraction (RFC AuthorLM §7.2, §21.7).

Bootstraps the Concept Graph from an existing manuscript: the LLM proposes
concepts and relationships, which enter the graph under the standard rules —
nodes are declared (and realize against the text immediately), while
relationships are *inferred hypotheses* awaiting the author's confirmation
in the briefing. Declared knowledge always outranks extracted knowledge.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import proposals
from .concepts import add_concept, concept_pattern, link_concepts, scan_co_occurrences, scan_realizations
from .db import Database
from .llm import LLMClient
from .revisions import read_manuscript_files

VALID_KINDS = {
    "concept", "definition", "objection", "example", "metaphor", "question",
    "historical_reference", "mathematical_construct",
}
VALID_RELATIONS = {
    "depends_on", "motivates", "contrasts_with", "elaborates", "generalizes",
    "specializes", "answers", "foreshadows", "illustrates", "permits",
    "creates", "distinguishes", "defines",
}
MAX_TEXT_CHARS = 24000
MAX_CONCEPTS = 40

EXTRACTION_SYSTEM = (
    "You are an editorial assistant analyzing a philosophy manuscript. "
    "Extract only the LOAD-BEARING units of thought: ideas the text argues "
    "for, defines, builds upon, or answers. Apply a strict test — a term "
    "that is merely mentioned, a person or work cited in passing, an "
    "adjective, or ordinary technical vocabulary is NOT a concept. "
    "People, texts, schools, and traditions the author cites as sources or "
    "context get kind 'historical_reference'; formal or mathematical "
    "apparatus gets kind 'mathematical_construct'. Prefer the author's own "
    "terminology, singular form. Aim for 10–20 strong nodes; quality over "
    "coverage. Return JSON of the shape "
    '{"concepts": [{"name": str, "kind": str, "notes": str}], '
    '"links": [{"from": str, "relation": str, "to": str}]} '
    f"where kind is one of {sorted(VALID_KINDS)}. "
    f"Never exceed {MAX_CONCEPTS} concepts. "
)

RELATION_GUIDE = (
    "Relations (A relation B) have strict meanings — "
    "depends_on: A cannot be understood before B. "
    "permits: A makes B possible. "
    "creates: A brings B into being. "
    "defines: A fixes what B means. "
    "elaborates: A unpacks or develops B in more detail. "
    "specializes: A is a narrower case of B. "
    "generalizes: A is a broader case of B. "
    "contrasts_with: the text explicitly opposes A and B. "
    "answers: A resolves the objection or question B. "
    "motivates: A is the driving reason for which B seeks, acts, or arises — "
    "the motive belongs to B, the party moved by it. In dialogue, a question "
    "motivates its ASKER, never the character who responds to it; for the "
    "responder use answers (Responder answers Question). "
    "foreshadows: A hints at B before B is treated. "
    "illustrates: A is an example, image, or metaphor for B. "
    "distinguishes: A draws the distinction that separates B. "
    "Include a link ONLY when the text itself asserts or demonstrates the "
    "relation — co-mention is not a relationship, and direction matters. "
    "Prefer 5–15 strong links; if unsure which relation holds, omit the "
    "link rather than guessing."
)

EXTRACTION_SYSTEM = EXTRACTION_SYSTEM + RELATION_GUIDE

EDGES_ONLY_SYSTEM = (
    "You are an editorial assistant analyzing a philosophy manuscript. "
    "The concept inventory is already established and is listed as KNOWN "
    "CONCEPTS at the top of the text — do NOT extract, rename, or redefine "
    "concepts. Extract ONLY relationships among the known concepts. "
    'Return JSON of the shape {"concepts": [], '
    '"links": [{"from": str, "relation": str, "to": str}]} using known '
    "concept names verbatim. " + RELATION_GUIDE
)


def record_triage(db: Database, manuscript_id: str, node: dict, signal: str,
                  new_kind: str | None = None) -> None:
    """Persist a triage decision as evidence (RFC: the author contributes
    evidence, never edits knowledge directly). signal: confirmed | retyped |
    rejected."""
    from .db import ko_fields

    target = f"{node['name']} ({node['kind']})"
    if signal == "retyped" and new_kind:
        target = f"{node['name']}: {node['kind']} → {new_kind}"
    row = ko_fields("ev")
    row.update(
        manuscript_id=manuscript_id,
        episode_id=None,
        evidence_type="concept_triage",
        signal=signal,
        target=target,
        supports_policy=None,
        weight="high",  # a deliberate author decision is declared evidence
    )
    db.insert("evidence", row)


def triage_feedback(db: Database, manuscript_id: str) -> str:
    """Author feedback from prior triage, injected into the extraction
    prompt so the next extraction learns from it. Empty string if there is
    nothing to teach."""
    rejected = [
        n["name"] for n in db.all(
            "SELECT name FROM concept_nodes WHERE manuscript_id = ? AND status = 'retired' "
            "ORDER BY created_at DESC LIMIT 40",
            (manuscript_id,),
        )
    ]
    retypes = [
        r["target"] for r in db.all(
            "SELECT target FROM evidence WHERE manuscript_id = ? "
            "AND evidence_type = 'concept_triage' AND signal = 'retyped' "
            "ORDER BY created_at DESC LIMIT 20",
            (manuscript_id,),
        )
    ]
    confirmed = []
    for node in db.all(
        "SELECT * FROM concept_nodes WHERE manuscript_id = ? AND status != 'retired' "
        "ORDER BY created_at DESC LIMIT 60",
        (manuscript_id,),
    ):
        meta = json.loads(node["metadata"] or "{}")
        if meta.get("origin") == "extracted" and meta.get("confirmed"):
            confirmed.append(f"{node['name']} ({node['kind']})")
    confirmed = confirmed[:30]

    rejected_edges = [
        r["target"] for r in db.all(
            "SELECT target FROM evidence WHERE manuscript_id = ? "
            "AND evidence_type = 'edge_triage' AND signal = 'rejected' "
            "ORDER BY created_at DESC LIMIT 20",
            (manuscript_id,),
        )
    ]
    relation_corrections = [
        r["target"] for r in db.all(
            "SELECT target FROM evidence WHERE manuscript_id = ? "
            "AND evidence_type = 'edge_triage' AND signal = 'retyped' "
            "ORDER BY created_at DESC LIMIT 15",
            (manuscript_id,),
        )
    ]

    if not (rejected or retypes or confirmed or rejected_edges or relation_corrections):
        return ""
    sections = ["\n\nAuthor feedback from previous extractions (authoritative):"]
    if rejected:
        sections.append(
            "- REJECTED as not load-bearing — never extract these, or terms of "
            "similar peripherality: " + "; ".join(rejected)
        )
    if retypes:
        sections.append(
            "- Reclassified by the author — follow these precedents when "
            "assigning kinds: " + "; ".join(retypes)
        )
    if confirmed:
        sections.append(
            "- Confirmed as good extractions — exemplars of the right "
            "granularity: " + "; ".join(confirmed)
        )
    if rejected_edges:
        sections.append(
            "- Relationships REJECTED by the author — never propose these "
            "again, and avoid similarly unsupported links: "
            + "; ".join(rejected_edges)
        )
    if relation_corrections:
        sections.append(
            "- Relation corrections by the author (original ⇒ corrected) — "
            "follow these precedents when choosing relations: "
            + "; ".join(relation_corrections)
        )
    return "\n".join(sections)


def _manuscript_text(
    manuscript: dict, only: set[str] | None = None, max_chars: int = MAX_TEXT_CHARS
) -> tuple[str, bool]:
    """Concatenated manuscript text (optionally restricted to given relative
    paths) and whether it had to be truncated."""
    from .structure import ordered_items

    files = read_manuscript_files(Path(manuscript["path"]))
    parts = []
    for name, content in ordered_items(files):
        if only is not None and name not in only:
            continue
        parts.append(f"=== {name} ===\n{content}")
    text = "\n\n".join(parts)
    truncated = len(text) > max_chars
    if truncated:
        text = text[:max_chars] + "\n[truncated]"
    return text, truncated


def changed_files_since_extraction(db: Database, manuscript: dict):
    """(changed relative paths, latest version id, watermark files, latest
    files) since the last extraction. changed is None when there is no
    watermark to diff against (→ process everything)."""
    latest = db.one(
        "SELECT * FROM manuscript_versions WHERE manuscript_id = ? "
        "ORDER BY version_no DESC LIMIT 1",
        (manuscript["id"],),
    )
    if not latest:
        return None, None, None, None
    watermark = json.loads(manuscript["metadata"] or "{}").get("last_extracted_version")
    if not watermark:
        return None, latest["id"], None, None
    if watermark == latest["id"]:
        return set(), latest["id"], None, None
    previous = db.one(
        "SELECT files FROM manuscript_versions WHERE id = ?", (watermark,)
    )
    if not previous:
        return None, latest["id"], None, None
    old_files = json.loads(previous["files"])
    new_files = json.loads(latest["files"])
    changed = {name for name, text in new_files.items() if old_files.get(name) != text}
    return changed, latest["id"], old_files, new_files


def _changed_paragraph_text(old_files: dict, new_files: dict, target: set[str]) -> str:
    """The paragraphs that are actually new or rewritten in the changed
    files — the attention scope for proposals against settled knowledge."""
    from .structure import is_structural

    parts = []
    for name in sorted(target):
        if is_structural(name):
            continue  # TOC edits are structure, not prose — no attention
        old_paragraphs = set(re.split(r"\n\s*\n", old_files.get(name, "")))
        for paragraph in re.split(r"\n\s*\n", new_files.get(name, "")):
            if paragraph.strip() and paragraph not in old_paragraphs:
                parts.append(paragraph)
    return "\n\n".join(parts)


def _normalize(text: str | None) -> str:
    return " ".join((text or "").lower().split())


def extract_concepts(
    db: Database, manuscript: dict, llm: LLMClient,
    files: list[str] | None = None, full: bool = False,
    edges_only: bool = False, _inventory: bool = False,
) -> dict | None:
    """Ask the LLM for concepts/links and merge them into the graph.

    Incremental by default: only files added or changed since the last
    extraction are sent (the whole manuscript on the first run, when `full`
    is set, or when explicit `files` are given). Returns a summary dict —
    {"up_to_date": True} when there is nothing new — or None if the LLM is
    unavailable or returned nothing usable.
    """
    mid = manuscript["id"]
    changed, latest_id, old_files, new_files = changed_files_since_extraction(db, manuscript)
    target: set[str] | None = None
    scope = "full manuscript"
    incremental = False
    if files:
        target = set(files)
        scope = f"{len(target)} selected file(s)"
    elif not full and changed is not None:
        if not changed:
            return {"up_to_date": True}
        target = changed
        scope = f"{len(changed)} changed file(s)"
        incremental = True

    max_chars = getattr(llm, "extraction_max_chars", MAX_TEXT_CHARS)
    text, truncated = _manuscript_text(manuscript, target, max_chars=max_chars)
    if not text.strip():
        return None

    if truncated and not edges_only and not files:
        # Hierarchical extraction: the scope exceeds one payload, so run one
        # bounded pass per file in reading order. Each pass carries the
        # inventory of concepts known so far, so later chapters can link to
        # (without re-extracting) what earlier chapters established.
        from .structure import reading_order

        disk = read_manuscript_files(Path(manuscript["path"]))
        order, _ = reading_order(disk)
        selected = [n for n in order if target is None or n in target]
        aggregate: dict = {
            "nodes": [], "edges": [], "realized": [], "skipped": 0,
            "suppressed": 0, "proposed": 0, "truncated": False,
        }
        for name in selected:
            sub = extract_concepts(db, manuscript, llm, files=[name],
                                   _inventory=True)
            if not sub or sub.get("up_to_date"):
                continue
            for key in ("nodes", "edges", "realized"):
                aggregate[key].extend(sub.get(key, []))
            for key in ("skipped", "suppressed", "proposed"):
                aggregate[key] += sub.get(key, 0)
            aggregate["truncated"] = aggregate["truncated"] or sub.get("truncated", False)
        aggregate["scope"] = (
            f"hierarchical: {len(selected)} file(s) in reading order, "
            f"one pass each (cap {max_chars} chars)"
        )
        return aggregate

    # Attention scope for proposals against settled knowledge: on an
    # incremental run, only the paragraphs the author actually changed can
    # generate a proposal — LLM paraphrase churn on untouched concepts never
    # reaches the review queue. On --full / per-file runs (a deliberate
    # audit), the whole input is in scope.
    if incremental and old_files is not None:
        attention = _changed_paragraph_text(old_files, new_files, target)
    else:
        attention = text

    def in_attention(*names: str) -> bool:
        return any(concept_pattern(n).search(attention) for n in names if n)
    if edges_only:
        scope += ", edges only"
        inventory = "; ".join(
            row["name"] for row in db.all(
                "SELECT name FROM concept_nodes WHERE manuscript_id = ? "
                "AND status != 'retired' ORDER BY name LIMIT 120",
                (mid,),
            )
        )
        system = EDGES_ONLY_SYSTEM + triage_feedback(db, mid)
        text = f"KNOWN CONCEPTS: {inventory}\n\n{text}"
    else:
        system = EXTRACTION_SYSTEM + triage_feedback(db, mid)
        if _inventory:
            inventory = "; ".join(
                row["name"] for row in db.all(
                    "SELECT name FROM concept_nodes WHERE manuscript_id = ? "
                    "AND status != 'retired' ORDER BY name LIMIT 200",
                    (mid,),
                )
            )
            if inventory:
                system += (
                    " Concepts listed under KNOWN CONCEPTS are already in the "
                    "graph — do not re-extract them, but you MAY link to them."
                )
                text = f"KNOWN CONCEPTS: {inventory}\n\n{text}"
    result = llm.complete_json(system, text)
    if not isinstance(result, dict):
        return None

    # A retired concept stays retired: extraction may never resurrect what
    # the author rejected, even if the model proposes it again.
    banned = {
        row["name"].lower() for row in db.all(
            "SELECT name FROM concept_nodes WHERE manuscript_id = ? AND status = 'retired'",
            (mid,),
        )
    }
    new_nodes, new_edges, skipped, suppressed = [], [], 0, 0
    proposed = 0

    for item in [] if edges_only else result.get("concepts", []):
        if not isinstance(item, dict) or not str(item.get("name", "")).strip():
            skipped += 1
            continue
        name = str(item["name"]).strip()[:80]
        kind = item.get("kind", "concept")
        if kind not in VALID_KINDS:
            kind = "concept"
        notes = (str(item["notes"]).strip()[:300] or None) if item.get("notes") else None
        if name.lower() in banned:
            # The ban is a write-guard, not a settled-forever verdict: when a
            # retired concept recurs in text the author actually changed,
            # surface a revival proposal instead of silently suppressing.
            if in_attention(name):
                retired_node = db.one(
                    "SELECT id FROM concept_nodes WHERE manuscript_id = ? "
                    "AND lower(name) = lower(?) AND status = 'retired'",
                    (mid, name),
                )
                if retired_node and proposals.create(
                    db, mid, "revival", retired_node["id"],
                    {"name": name, "kind": kind, "notes": notes},
                ):
                    proposed += 1
                    continue
            suppressed += 1
            continue
        before = db.one(
            "SELECT * FROM concept_nodes WHERE manuscript_id = ? AND lower(name) = lower(?)",
            (mid, name),
        )
        if before is None and banned:
            # A near-miss of a retired name is called out interactively, not
            # silently admitted as a "new" concept (nor silently banned).
            import difflib as _difflib

            near = _difflib.get_close_matches(name.lower(), list(banned), n=1, cutoff=0.8)
            if near:
                retired_node = db.one(
                    "SELECT id, name FROM concept_nodes WHERE manuscript_id = ? "
                    "AND lower(name) = lower(?) AND status = 'retired'",
                    (mid, near[0]),
                )
                if retired_node and proposals.create(
                    db, mid, "variant_of_retired", retired_node["id"],
                    {"proposed_name": name, "kind": kind, "notes": notes,
                     "retired_name": retired_node["name"]},
                ):
                    proposed += 1
                continue
        node = add_concept(db, mid, name, kind=kind, notes=notes)
        if before is None:
            # Machine-extracted nodes are hypotheses awaiting the author's
            # confirmation, exactly like inferred edges (§21.7).
            db.update(
                "concept_nodes", node["id"],
                {"metadata": json.dumps({"origin": "extracted", "confirmed": False})},
            )
            new_nodes.append(node)
        elif (
            notes
            and _normalize(notes) != _normalize(before["notes"])
            and in_attention(name)
        ):
            # Existing knowledge is machine-unwritable, but a materially
            # different definition arising from changed text is surfaced for
            # the author instead of silently discarded.
            if proposals.create(
                db, mid, "note_update", before["id"],
                {
                    "name": before["name"],
                    "current_note": before["notes"],
                    "proposed_note": notes,
                    "current_kind": before["kind"],
                    "proposed_kind": kind,
                },
            ):
                proposed += 1
        if len(new_nodes) >= MAX_CONCEPTS:
            break

    known = {
        row["name"].lower()
        for row in db.all(
            "SELECT name FROM concept_nodes WHERE manuscript_id = ? AND status != 'retired'",
            (mid,),
        )
    }
    for item in result.get("links", []):
        if not isinstance(item, dict):
            skipped += 1
            continue
        src = str(item.get("from", "")).strip()
        dst = str(item.get("to", "")).strip()
        relation = str(item.get("relation", "")).strip()
        # Only link concepts that exist; unknown relations become 'elaborates'.
        if not src or not dst or src.lower() == dst.lower():
            skipped += 1
            continue
        if src.lower() not in known or dst.lower() not in known:
            skipped += 1
            continue
        if relation not in VALID_RELATIONS:
            # Honesty over volume: an unknown relation is dropped, not
            # coerced into 'elaborates' — coercion manufactures wrong edges.
            skipped += 1
            continue
        before = db.one(
            "SELECT id FROM concept_edges WHERE manuscript_id = ? AND relation = ? "
            "AND from_node = (SELECT id FROM concept_nodes WHERE manuscript_id = ? AND lower(name) = lower(?)) "
            "AND to_node = (SELECT id FROM concept_nodes WHERE manuscript_id = ? AND lower(name) = lower(?))",
            (mid, relation, mid, src, mid, dst),
        )
        edge = link_concepts(db, mid, src, relation, dst, status="inferred")
        if before is None:
            new_edges.append(edge)
        elif edge["status"] == "rejected" and in_attention(src, dst):
            # A rejected relationship argued again by changed text becomes a
            # reconsideration proposal, never a silent resurrection.
            if proposals.create(
                db, mid, "edge_reproposal", edge["id"],
                {"from_name": src, "relation": relation, "to_name": dst},
            ):
                proposed += 1

    # Realize extracted concepts against the latest collected version.
    latest = db.one(
        "SELECT * FROM manuscript_versions WHERE manuscript_id = ? "
        "ORDER BY version_no DESC LIMIT 1",
        (mid,),
    )
    realized = []
    if latest:
        realized = scan_realizations(db, mid, dict(latest))
        scan_co_occurrences(db, mid, dict(latest))

    # Advance the extraction watermark so the next run diffs from here.
    # An edges-only pass leaves it alone: the text has not been mined for
    # concepts, so a later incremental run must still see these files.
    if latest_id and not edges_only:
        meta = json.loads(manuscript["metadata"] or "{}")
        meta["last_extracted_version"] = latest_id
        db.update("manuscripts", mid, {"metadata": json.dumps(meta)})

    return {
        "nodes": new_nodes,
        "edges": new_edges,
        "realized": realized,
        "skipped": skipped,
        "suppressed": suppressed,
        "proposed": proposed,
        "scope": scope,
        "truncated": truncated,
    }
