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
from .concepts import (NODE_KINDS, add_concept, concept_pattern, get_concept,
                       link_concepts, node_names, scan_realizations)
from .hygiene import RECURRENCE_GATED_KINDS, passes_recurrence_bar
from .db import Database
from .llm import LLMClient
from .revisions import read_manuscript_files

VALID_RELATIONS = {
    "depends_on", "motivates", "contrasts_with", "elaborates", "generalizes",
    "specializes", "answers", "foreshadows", "illustrates", "permits",
    "creates", "distinguishes", "defines", "leads_to", "refutes",
}
MAX_TEXT_CHARS = 24000
MAX_CONCEPTS = 40
EXTRACTION_PROMPT_PATH = Path(__file__).parent / "prompts" / "extraction.md"


def extraction_system() -> str:
    return (EXTRACTION_PROMPT_PATH.read_text(encoding="utf-8")
            .replace("<<VALID_KINDS>>", str(sorted(NODE_KINDS)))
            .replace("<<MAX_CONCEPTS>>", str(MAX_CONCEPTS)))


def _prompt_section(prompt: str, heading: str,
                    next_heading: str | None = None) -> str:
    section = prompt.split(f"\n{heading}:\n", 1)[1]
    if next_heading:
        section = section.split(f"\n\n{next_heading}:\n", 1)[0]
    return section.strip() + " "


EXTRACTION_SYSTEM = extraction_system()
RELATION_GUIDE = _prompt_section(EXTRACTION_SYSTEM, "RELATION GUIDE",
                                 "ALIAS GUIDE")
ALIAS_GUIDE = _prompt_section(EXTRACTION_SYSTEM, "ALIAS GUIDE")

ALIASES_ONLY_SYSTEM = (
    "You are an editorial assistant analyzing a philosophy manuscript. The "
    "concept inventory is already established and is listed as KNOWN "
    "CONCEPTS at the top of the text — do NOT extract concepts or "
    "relationships. Your sole task is to find aliasing statements. "
    + ALIAS_GUIDE +
    'Return JSON of the shape {"aliases": [{"alias": str, "canonical": str, '
    '"sentence": str}]} using known concept names verbatim.'
)

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
                  new_kind: str | None = None,
                  reason: str | None = None) -> None:
    """Persist a triage decision as evidence (RFC: the author contributes
    evidence, never edits knowledge directly). signal: confirmed | retyped |
    rejected."""
    from .db import ko_fields

    target = f"{node['name']} ({node['kind']})"
    if signal == "retyped" and new_kind:
        target = f"{node['name']}: {node['kind']} → {new_kind}"
    elif signal == "merged" and new_kind:
        target = f"{node['name']} → alias of {new_kind}"
    if reason:
        target += f" — reason: {reason}"
    row = ko_fields("ev")
    row.update(
        manuscript_id=manuscript_id,
        episode_id=None,
        evidence_type="concept_triage",
        signal=signal,
        target=target,
        supports_belief=None,
        weight="high",  # a deliberate author decision is declared evidence
    )
    db.insert("evidence", row)


def triage_feedback(db: Database, manuscript_id: str) -> str:
    """Author feedback from prior triage, injected into the extraction
    prompt so the next extraction learns from it. Empty string if there is
    nothing to teach."""
    rejected = [
        n["name"] for n in db.all(
            "SELECT n.name FROM concept_nodes n WHERE n.manuscript_id = ? "
            "AND n.status = 'retired' AND NOT EXISTS ("
            "SELECT 1 FROM evidence e WHERE e.manuscript_id = n.manuscript_id "
            "AND e.evidence_type = 'deterministic_triage' "
            "AND json_extract(e.metadata, '$.triage_type') = 'concepts' "
            "AND json_extract(e.metadata, '$.object_id') = n.id) "
            "ORDER BY n.created_at DESC LIMIT 40",
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

    from . import loop

    learned = "".join(
        loop.generator_feedback(db, manuscript_id, spec)
        for spec in loop.REGISTRY.values()
        if spec.table == "knowledge_proposals")
    if not (rejected or retypes or confirmed or rejected_edges
            or relation_corrections):
        return learned
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
    # The lists above are fixed recency windows (LIMIT 40/20/30/20/15) and
    # silently forget: 538 author decisions on record, 75 reaching the
    # model. `learned` is the compacted, non-forgetting half — a handful of
    # beliefs distilled from the same verdicts, which is why it is appended
    # rather than competing for room inside those caps.
    return "\n".join(sections) + learned


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


_SECTION_HEAD = re.compile(r"^#{1,6}[ \t]")


def _sections(text: str) -> list[str]:
    """Heading-delimited sections (preamble first). The section is the
    extraction unit (ratified 2026-08-10): each carries its heading line,
    so every payload stays self-locating."""
    out: list[list[str]] = []
    cur: list[str] = []
    for line in text.split("\n"):
        if _SECTION_HEAD.match(line) and cur:
            out.append(cur)
            cur = [line]
        else:
            cur.append(line)
    if cur:
        out.append(cur)
    return [s for s in ("\n".join(c).strip() for c in out) if s]


def _changed_sections(old_text: str, new_text: str) -> list[str]:
    """Sections of new_text not present verbatim in old_text — one edited
    paragraph sends its section, not its 11,000-word file. Moved-but-
    unchanged sections are excluded (verbatim membership, order-blind)."""
    old = set(_sections(old_text))
    return [s for s in _sections(new_text) if s not in old]


def _section_payloads(units: list[tuple[str, str]], cap: int) -> list[str]:
    """'=== name ===' labelled chunks batched into payloads ≤ cap chars.
    An oversized chunk splits at section boundaries first, then hard-
    splits as a last resort — extraction never truncates."""
    pieces: list[tuple[str, str]] = []
    for name, chunk in units:
        if len(chunk) <= cap:
            pieces.append((name, chunk))
            continue
        cur = ""
        for section in _sections(chunk):
            candidate = f"{cur}\n\n{section}" if cur else section
            if cur and len(candidate) > cap:
                pieces.append((name, cur))
                cur = section
            else:
                cur = candidate
            while len(cur) > cap:
                pieces.append((name, cur[:cap]))
                cur = cur[cap:]
        if cur:
            pieces.append((name, cur))
    payloads: list[list[str]] = []
    size = 0
    for name, chunk in pieces:
        part = f"=== {name} ===\n{chunk}"
        if payloads and size + len(part) + 2 <= cap:
            payloads[-1].append(part)
            size += len(part) + 2
        else:
            payloads.append([part])
            size = len(part)
    return ["\n\n".join(p) for p in payloads]


def _normalize(text: str | None) -> str:
    return " ".join((text or "").lower().split())


def _set_extraction_watermark(db: Database, manuscript: dict,
                              latest_id: str | None) -> None:
    """Advance the incremental-extract watermark and keep the in-memory
    manuscript dict in sync (multi-pass aggregators re-read it).

    Extraction can run for minutes; `manuscript["metadata"]` was captured
    before that call started and may be stale by the time this runs (e.g. a
    Google Docs mapping update landed in the meantime). Re-reading from the
    DB immediately before merging narrows that lost-update window from
    minutes to microseconds — it does not make the write atomic."""
    if not latest_id:
        return
    current = db.one(
        "SELECT metadata FROM manuscripts WHERE id = ?", (manuscript["id"],))
    meta = json.loads((current["metadata"] if current else None) or "{}")
    meta["last_extracted_version"] = latest_id
    encoded = json.dumps(meta)
    db.update("manuscripts", manuscript["id"], {"metadata": encoded})
    manuscript["metadata"] = encoded


def extract_concepts(
    db: Database, manuscript: dict, llm: LLMClient,
    files: list[str] | None = None, full: bool = False,
    edges_only: bool = False, aliases_only: bool = False,
    _inventory: bool = False, _text_override: str | None = None,
) -> dict | None:
    """Ask the LLM for concepts/links and merge them into the graph.

    Incremental by default: only files added or changed since the last
    extraction are sent (the whole manuscript on the first run, when `full`
    is set, or when explicit `files` are given). Returns a summary dict —
    {"up_to_date": True} when there is nothing new — or None if the LLM is
    unavailable or returned nothing usable.
    """
    mid = manuscript["id"]
    if aliases_only and not files:
        # An aliases pass is a deliberate audit of the whole text — naming
        # sentences live in already-mined prose, not just fresh paragraphs.
        full = True
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

    def _passes(payloads: list[str], scope_label: str,
                commit_watermark: bool = True) -> dict:
        """One extraction pass per payload, inventory carried, results
        aggregated — the no-truncation guarantee for oversized scopes.
        Watermark advances only after EVERY pass succeeds: a mid-batch
        LLM None must not mark the version extracted (silent permanent
        skip of the remaining payloads on the next run)."""
        aggregate: dict = {
            "nodes": [], "edges": [], "realized": [], "skipped": 0,
            "suppressed": 0, "ungrounded_links": 0, "below_bar": [],
            "proposed": 0, "truncated": False, "scope": scope_label,
        }
        failed = False
        for payload in payloads:
            sub = extract_concepts(db, manuscript, llm,
                                   files=sorted(target or []),
                                   aliases_only=aliases_only,
                                   _inventory=True,
                                   _text_override=payload)
            if sub is None:
                failed = True
                continue
            if sub.get("up_to_date"):
                continue
            for key in ("nodes", "edges", "realized", "below_bar"):
                aggregate[key].extend(sub.get(key, []))
            for key in ("skipped", "suppressed", "ungrounded_links",
                        "proposed"):
                aggregate[key] += sub.get(key, 0)
        if commit_watermark and not failed and not aliases_only:
            _set_extraction_watermark(db, manuscript, latest_id)
        if failed:
            aggregate["incomplete"] = True
        return aggregate

    if _text_override is not None:
        text, truncated = _text_override, False
    elif incremental and old_files is not None and not edges_only:
        # Ratified 2026-08-10: the extraction unit is the SECTION under
        # its heading. One edited paragraph in an 11,000-word chapter
        # sends its section, not the file — the known-concept inventory
        # in the prompt supplies the linking context the rest of the
        # file would have provided.
        from .structure import is_structural, reading_order

        order, _ = reading_order(new_files)
        units = []
        for name in order:
            if name not in target or is_structural(name):
                continue
            delta = _changed_sections(old_files.get(name, ""),
                                      new_files.get(name, ""))
            if delta:
                units.append((name, "\n\n".join(delta)))
        if not units:
            return {"up_to_date": True}
        payloads = _section_payloads(units, max_chars)
        scope = (f"{len(units)} changed file(s), changed sections only"
                 + (f", {len(payloads)} pass(es)"
                    if len(payloads) > 1 else ""))
        if len(payloads) > 1:
            return _passes(payloads, scope,
                           commit_watermark=not _inventory)
        text, truncated = payloads[0], False
    else:
        text, truncated = _manuscript_text(manuscript, target,
                                           max_chars=max_chars)
    if not text.strip():
        return None

    if truncated and files and not edges_only:
        # Explicit files whose combined text exceeds one payload (e.g. a
        # deliberate re-mine of metaphysic.md): batch their sections into
        # multiple passes rather than truncating the back half away.
        disk = read_manuscript_files(Path(manuscript["path"]))
        units = [(n, disk[n]) for n in sorted(target or []) if n in disk]
        payloads = _section_payloads(units, max_chars)
        if len(payloads) > 1:
            return _passes(
                payloads,
                f"{len(units)} file(s), sections in "
                f"{len(payloads)} pass(es) (cap {max_chars} chars)",
                commit_watermark=not _inventory)

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
            "suppressed": 0, "ungrounded_links": 0, "below_bar": [],
            "proposed": 0,
            "truncated": False,
        }
        failed = False
        for name in selected:
            if not (disk.get(name) or "").strip():
                continue  # empty chapter — nothing to mine, not a failure
            sub = extract_concepts(db, manuscript, llm, files=[name],
                                   aliases_only=aliases_only, _inventory=True)
            if sub is None or sub.get("incomplete"):
                # LLM miss (or nested multi-pass miss) — do not advance the
                # watermark: the gap must remain retryable on the next extract.
                failed = True
                if sub is None:
                    continue
            if sub.get("up_to_date"):
                continue
            for key in ("nodes", "edges", "realized", "below_bar"):
                aggregate[key].extend(sub.get(key, []))
            for key in ("skipped", "suppressed", "ungrounded_links",
                        "proposed"):
                aggregate[key] += sub.get(key, 0)
            aggregate["truncated"] = aggregate["truncated"] or sub.get("truncated", False)
        aggregate["scope"] = (
            f"hierarchical: {len(selected)} file(s) in reading order, "
            f"one pass each (cap {max_chars} chars)"
            + (", aliases only" if aliases_only else "")
        )
        if not failed and not aliases_only:
            _set_extraction_watermark(db, manuscript, latest_id)
        if failed:
            aggregate["incomplete"] = True
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
    if edges_only or aliases_only:
        scope += ", edges only" if edges_only else ", aliases only"
        inventory = "; ".join(
            row["name"] for row in db.all(
                "SELECT name FROM concept_nodes WHERE manuscript_id = ? "
                "AND status != 'retired' ORDER BY name LIMIT 120",
                (mid,),
            )
        )
        system = (EDGES_ONLY_SYSTEM if edges_only else ALIASES_ONLY_SYSTEM) \
            + triage_feedback(db, mid)
        text = f"KNOWN CONCEPTS: {inventory}\n\n{text}"
    else:
        system = extraction_system() + triage_feedback(db, mid)
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
    # Extraction is schema-driven pattern work: thinking disabled
    # (it-0ae002ed7321 — thinking tokens were 80% of a 4-minute pass).
    result = llm.complete_json(system, text, thinking_budget=0)
    if not isinstance(result, dict):
        return None
    if not (edges_only or aliases_only) and not isinstance(result.get("concepts"), list):
        # A null (or otherwise non-list) 'concepts' payload is exactly as
        # malformed as a non-dict result above. Coercing it to [] here would
        # make a bad-shaped reply look like a clean "no concepts found" pass:
        # `_set_extraction_watermark` would then advance and the section
        # would never be re-mined. Returning None instead reuses the same
        # "incomplete" signal the caller already checks (`sub is None` /
        # `sub.get("incomplete")`) to hold the watermark back and keep the
        # section eligible for retry.
        return None

    # A retired concept stays retired: extraction may never resurrect what
    # the author rejected, even if the model proposes it again. But a
    # retired name living on as a live concept's alias is not banned —
    # mentions of it resolve to the live concept, never to a revival.
    banned = {
        row["name"].lower() for row in db.all(
            "SELECT name FROM concept_nodes WHERE manuscript_id = ? AND status = 'retired'",
            (mid,),
        )
    }
    banned -= {
        alias.lower()
        for row in db.all(
            "SELECT aliases FROM concept_nodes WHERE manuscript_id = ? "
            "AND status != 'retired' AND aliases != '[]'", (mid,),
        )
        for alias in json.loads(row["aliases"] or "[]")
    }
    new_nodes, new_edges, skipped, suppressed = [], [], 0, 0
    proposed = 0
    ungrounded_links = 0
    below_bar: list[str] = []
    disk_files: dict[str, str] | None = None

    for item in [] if edges_only or aliases_only else result.get("concepts", []):
        if not isinstance(item, dict) or not str(item.get("name", "")).strip():
            skipped += 1
            continue
        name = str(item["name"]).strip()[:80]
        kind = item.get("kind", "concept")
        # `kind` may come back model-shaped-but-malformed (a list, a dict) —
        # `in` against the NODE_KINDS frozenset raises TypeError on an
        # unhashable value instead of just failing the membership test.
        if not (isinstance(kind, str) and kind in NODE_KINDS):
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
        # Alias-aware: a hit on an alias is a hit on the concept it belongs
        # to, not a new concept — a name-only lookup here would send an
        # alias hit down the `before is None` branch below, discarding the
        # existing concept's metadata (including a confirmed 'True').
        before = get_concept(db, mid, name)
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
        if before is None and kind in RECURRENCE_GATED_KINDS:
            # The recurrence bar (ratified 2026-08-08, gate-as-law): the
            # model cannot count recurrence — an incremental payload only
            # shows changed text — so the system counts, against the whole
            # current manuscript. Below-bar candidates drop, self-healing:
            # the next mention arrives in changed text and re-proposes.
            # Never fed to triage_feedback — the bar rejected them, not
            # the author.
            if disk_files is None:
                disk_files = read_manuscript_files(Path(manuscript["path"]))
            admitted, _stats = passes_recurrence_bar(disk_files, [name])
            if not admitted:
                below_bar.append(name)
                continue
        # Existing knowledge is machine-unwritable (see the note_update branch
        # below): `add_concept` applies `notes` unconditionally when the
        # concept already exists, so an existing concept's notes must never
        # be passed through — only a brand-new concept may be seeded with
        # extracted notes.
        node = add_concept(db, mid, name, kind=kind,
                           notes=notes if before is None else None,
                           source_id=db.source("system"))
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

    known_nodes = {
        row["name"].lower(): row
        for row in db.all(
            "SELECT * FROM concept_nodes WHERE manuscript_id = ? AND status != 'retired'",
            (mid,),
        )
    }
    known = set(known_nodes)

    def endpoint_in_text(name: str) -> bool:
        # Hygiene gate (design: sweep framework): a link is proposed only
        # when the text asserts it, so both endpoints (or their aliases)
        # must be mentioned in the payload the model actually saw — an
        # incremental run's KNOWN CONCEPTS inventory is linkable, but not
        # out of thin air.
        node = known_nodes.get(name.lower())
        names = node_names(node) if node else [name]
        return any(concept_pattern(nm).search(text) for nm in names if nm)
    for item in [] if aliases_only else (result.get("links") or []):
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
        if not (endpoint_in_text(src) and endpoint_in_text(dst)):
            ungrounded_links += 1
            continue
        before = db.one(
            "SELECT id FROM concept_edges WHERE manuscript_id = ? AND relation = ? "
            "AND from_node = (SELECT id FROM concept_nodes WHERE manuscript_id = ? AND lower(name) = lower(?)) "
            "AND to_node = (SELECT id FROM concept_nodes WHERE manuscript_id = ? AND lower(name) = lower(?))",
            (mid, relation, mid, src, mid, dst),
        )
        edge = link_concepts(db, mid, src, relation, dst, status="inferred",
                             source_id=db.source("system"))
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

    # Aliasing statements become proposals — a merge is the author's call,
    # never the extractor's. Validation is deterministic: both names must
    # resolve to distinct live concepts and the sentence must be a verbatim
    # quote of the text sent to the model.
    flat_text = " ".join(text.split()).lower()
    for item in result.get("aliases", []) if isinstance(result.get("aliases"), list) else []:
        if not isinstance(item, dict):
            skipped += 1
            continue
        alias_name = str(item.get("alias", "")).strip()[:80]
        canonical_name = str(item.get("canonical", "")).strip()[:80]
        sentence = " ".join(str(item.get("sentence", "")).split())[:300]
        if not alias_name or not canonical_name or not sentence:
            skipped += 1
            continue
        a_node = get_concept(db, mid, alias_name)
        c_node = get_concept(db, mid, canonical_name)
        if (not a_node or not c_node or a_node["id"] == c_node["id"]
                or a_node["status"] == "retired" or c_node["status"] == "retired"):
            suppressed += 1
            continue
        if sentence.lower() not in flat_text:
            suppressed += 1  # the model must quote the text, not paraphrase it
            continue
        if not in_attention(alias_name, canonical_name):
            suppressed += 1
            continue
        location = next(
            (fname for fname, ftext in (new_files or {}).items()
             if sentence.lower() in " ".join(ftext.split()).lower()),
            None,
        )
        if proposals.create(
            db, mid, "alias", a_node["id"],
            {"alias": a_node["name"], "canonical": c_node["name"],
             "sentence": sentence, "location": location},
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

    # Advance the extraction watermark so the next run diffs from here.
    # An edges-only pass leaves it alone: the text has not been mined for
    # concepts, so a later incremental run must still see these files.
    # Inventory sub-passes leave it alone too: the aggregator commits
    # once after every pass succeeds (a mid-batch LLM miss must not
    # mark the version extracted).
    if (latest_id and not edges_only and not aliases_only
            and not _inventory):
        _set_extraction_watermark(db, manuscript, latest_id)

    return {
        "nodes": new_nodes,
        "edges": new_edges,
        "realized": realized,
        "skipped": skipped,
        "suppressed": suppressed,
        "ungrounded_links": ungrounded_links,
        "below_bar": below_bar,
        "proposed": proposed,
        "scope": scope,
        "truncated": truncated,
    }
