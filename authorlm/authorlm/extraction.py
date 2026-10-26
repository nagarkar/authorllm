"""LLM-backed concept and relationship extraction (RFC AuthorLM §7.2, §21.7).

Bootstraps the Concept Graph from an existing manuscript: the LLM proposes
concepts and relationships, which enter the graph under the standard rules —
nodes are declared (and realize against the text immediately), while
relationships are *inferred hypotheses* awaiting the author's confirmation
in the briefing. Declared knowledge always outranks extracted knowledge.

The redesigned pipeline of docs/extraction-redesign.md (ratified
2026-10-25): `assemble` builds one hashed payload per section, the model
answers in the candidate contract, eight deterministic gates screen the
reply, a per-essay adjudicator (extraction_adjudicator.py) rules on what
survives, and `run` writes §4.8's shapes. `authorlm extract` drives it
interactively; `api.run_extraction` is the non-interactive front for
init, the writeup close, an analysed collect and the MCP tool. The old
single-call path was retired 2026-10-25 (§9 phase 6).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from . import proposals
from .concepts import (primary_location, NODE_KINDS, add_concept, concept_pattern, get_concept,
                       link_concepts, mention_pattern, node_names,
                       scan_realizations)
from .hygiene import RECURRENCE_GATED_KINDS, passes_recurrence_bar
from .db import Database, ko_fields
from .llm import LLMClient
from .loop import similarity as _similarity
from .revisions import _paragraphs, read_manuscript_files

VALID_RELATIONS = {
    "depends_on", "motivates", "contrasts_with", "elaborates", "generalizes",
    "specializes", "answers", "foreshadows", "illustrates", "permits",
    "creates", "distinguishes", "defines", "leads_to", "refutes",
}
# Materiality floor (Q/note-materiality): a note_update that says the same
# thing in different words is not new information — see _note_update_blocked.
# Measured (proposal-learning-loop design doc), not guessed: 0.72 is the
# firewall's identical-restatement threshold; 0.90 sits well inside the
# "real refinement" band the adjudicator already rules on correctly, and is
# reserved for the unambiguous paraphrase case ratified for this fix.
NOTE_UPDATE_PARAPHRASE_FLOOR = 0.90
# Gate 7 of docs/extraction-redesign.md §4.4: the relations the author
# keeps more than half the time (§2.2). VALID_RELATIONS stays the schema's
# vocabulary; extraction may PROPOSE only these until `extract calibrate`
# measures a relation back in (or out) at the 60% line.
DEFAULT_PROPOSABLE = ("depends_on", "generalizes", "specializes",
                      "contrasts_with", "illustrates", "permits", "answers",
                      "foreshadows")


def _record_alias_fold_refused(db: Database, manuscript_id: str, a_node: dict,
                               c_node: dict, sentence: str) -> None:
    """Guard (Q/alias-guard): the extractor read a naming sentence as
    identifying `a_node` with `c_node` — but `a_node` already stands as its
    own live concept, so adopting that reading would MERGE it into
    `c_node`, not merely rename it. A merge is the author's call, decided
    with the full weight of both concepts' history, never something a
    single naming sentence should settle on its own — so no proposal is
    queued here.

    The read is not discarded, though: the extractor believing two live
    concepts share a name IS information, so it is logged the same way
    the per-essay adjudicator logs what it screens out — system provenance
    (`extraction_adjudication` is in SYSTEM_EVIDENCE_TYPES), auditable, but
    never queue."""
    row = ko_fields("ev")
    row.update(
        manuscript_id=manuscript_id,
        episode_id=None,
        evidence_type="extraction_adjudication",
        signal="alias_fold_refused",
        target=(f"'{a_node['name']}' named as an alias of '{c_node['name']}' "
                f"— both are already live concepts; refused (would be a "
                f"merge, not an alias)")[:200],
        supports_belief=None,
        weight="low",  # a machine screening decision, never author evidence
        metadata=json.dumps({"alias": a_node["name"],
                             "canonical": c_node["name"],
                             "sentence": sentence}),
    )
    db.insert("evidence", row)


def _record_alias_retired_refused(db: Database, manuscript_id: str, a_node: dict,
                                  c_node: dict, sentence: str) -> None:
    """Guard (Q/alias-retired-guard): the extractor read a naming sentence
    as bestowing `a_node`'s name on `c_node` — but `a_node` names a RETIRED
    concept. Retired names are banned from re-entering the graph (gate 5,
    `GateContext.retired`); letting one back in
    through the alias door would be exactly the side door that ban exists
    to close, even though the sentence names `c_node`, not the retired
    concept's own old identity, as canonical.

    Recorded with a signal distinct from the live-concept fold
    (`alias_fold_refused`, Q/alias-guard) so the two refusal reasons stay
    separable in the evidence — one is "these are the same live concept",
    the other is "this name was retired on purpose"."""
    row = ko_fields("ev")
    row.update(
        manuscript_id=manuscript_id,
        episode_id=None,
        evidence_type="extraction_adjudication",
        signal="alias_retired_refused",
        target=(f"'{a_node['name']}' named as an alias of '{c_node['name']}' "
                f"— '{a_node['name']}' is a retired concept; refused (would "
                f"re-enter a retired name through the alias door)")[:200],
        supports_belief=None,
        weight="low",  # a machine screening decision, never author evidence
        metadata=json.dumps({"alias": a_node["name"],
                             "canonical": c_node["name"],
                             "sentence": sentence}),
    )
    db.insert("evidence", row)


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


def is_in_flight(text: str) -> bool:
    """This file holds only the mid-rewrite placeholder, so it is not
    prose and must not be mined.

    PER FILE, never per payload: `eligible_files` drops such a file before
    any unit of it is assembled, so no block E can carry the marker."""
    from .api import is_placeholder

    return is_placeholder(text)


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


def _normalize(text: str | None) -> str:
    return " ".join((text or "").lower().split())


def _note_update_blocked(current: str | None, proposed: str) -> str | None:
    """Materiality floor for note_update creation (Q/note-materiality): a
    fresh note that says the same thing in different words costs the author
    a review for nothing. Returns the refusal reason, or None when the
    proposal clears the floor and may be raised.

    `_normalize(notes) != _normalize(before['notes'])` already keeps a pure
    case/whitespace change from reaching this function at all (both
    lower-case and collapse whitespace, so such a pair normalizes equal) —
    the check is repeated here anyway because the rule belongs with the
    metric that enforces it, not left implicit in a helper named for
    something else, and because it is exactly what let a term of art
    ('Quality') survive as 'quality' — a live style-law violation — through
    a note_update proposal (it-Q/note-materiality: this codebase writes
    another one from a DIFFERENT site, adjudication.py's 'improves' verdict,
    which carries no equivalent gate at all).

    Only a floor, never a ceiling: 0.75-0.90 stays open on purpose — that
    band holds real refinements, and screen/adjudication already rule on
    those correctly (do not lower NOTE_UPDATE_PARAPHRASE_FLOOR to 0.75)."""
    current = current or ""
    if _normalize(current) == _normalize(proposed):
        return "case/whitespace-only change"
    score = _similarity(current, proposed)
    if score >= NOTE_UPDATE_PARAPHRASE_FLOOR:
        return f"near-paraphrase (similarity {score:.2f})"
    return None


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


class ExtractionDisabled(RuntimeError):
    """Raised when LLM concept extraction is asked for while
    `[extraction] enabled = false`. A RuntimeError so the MCP guard
    renders it as a sentence."""


DISABLED_MESSAGE = (
    "concept extraction is switched off ([extraction] enabled = false in "
    "config.toml): the graph changes only by hand — 'authorlm concept add "
    "<name> --notes …'. Set enabled = true to run the extractor again.")


def link_endpoints(item: dict) -> tuple[str, str, str]:
    """(from, relation, to) of one extracted link. The prompt asks for
    `{"from", "relation", "to"}`, but Gemini often keys the target by the
    relation itself — `{"from": A, "leads_to": B, "relation": "leads_to"}`
    — which used to count every such link as malformed. Read that shape
    too: the relation named by `relation`, or by the item's one key that
    is a valid relation."""
    src = str(item.get("from", "")).strip()
    relation = str(item.get("relation", "")).strip()
    dst = item.get("to")
    if not dst:
        keyed = [k for k in item if k in VALID_RELATIONS]
        if relation in item:
            dst = item[relation]
        elif len(keyed) == 1:
            relation = relation or keyed[0]
            dst = item[keyed[0]]
    return src, relation, str(dst or "").strip()


def extraction_enabled(config: dict | None) -> bool:
    """The [extraction] enabled switch (default on). Author ruling
    2026-09-25: extraction is off until the concept-graph feature is
    redone; the deterministic graph upkeep (realizations, introduced_in,
    protected terms) is untouched by this switch and keeps running."""
    section = (config or {}).get("extraction") or {}
    return bool(section.get("enabled", True))


MANUSCRIPT_DISABLED_MESSAGE = (
    "concept extraction is switched off for this manuscript: the graph "
    "changes only by hand — 'authorlm concept add <name> --notes …'. "
    "'authorlm manuscript set --extraction on' runs the extractor again.")


def manuscript_extraction_enabled(db: Database, manuscript: dict) -> bool:
    """The per-manuscript switch (default on), kept in
    `manuscripts.metadata` as `{"extraction": {"enabled": false}}` —
    the same shape as the config section it narrows. A machine-kept
    manuscript (a corpus document an outside tool maintains) must never
    have concepts extracted while the books beside it in the workspace
    keep extracting; the global switch cannot say that.

    Read from the row, never from the caller's dict: the dict's
    `metadata` is whatever it was when the manuscript was loaded, and
    the Doc mapping and the extraction watermark rewrite that column
    under it (see `_set_extraction_watermark`)."""
    row = db.one("SELECT metadata FROM manuscripts WHERE id = ?",
                 (manuscript["id"],))
    meta = json.loads((row["metadata"] if row else None) or "{}")
    section = meta.get("extraction") or {}
    return bool(section.get("enabled", True))


def set_manuscript_extraction(db: Database, manuscript: dict,
                              enabled: bool) -> None:
    """Write the per-manuscript switch, merged into the row's current
    metadata (the Doc mapping and the watermark live beside it) and
    mirrored into the caller's dict."""
    row = db.one("SELECT metadata FROM manuscripts WHERE id = ?",
                 (manuscript["id"],))
    meta = json.loads((row["metadata"] if row else None) or "{}")
    # Merged, not replaced: `extraction.runs` (the run and calibration
    # record, §4.8) lives in the same object.
    meta["extraction"] = {**(meta.get("extraction") or {}),
                          "enabled": bool(enabled)}
    encoded = json.dumps(meta)
    db.update("manuscripts", manuscript["id"], {"metadata": encoded})
    manuscript["metadata"] = encoded


def extraction_allowed(db: Database, manuscript: dict,
                       config: dict | None) -> bool:
    """Both switches at once: the global one wins when it is off, and
    the manuscript's own narrows it when it is on."""
    return (extraction_enabled(config)
            and manuscript_extraction_enabled(db, manuscript))


# ===================================================================
# The redesigned pipeline (docs/extraction-redesign.md, ratified
# 2026-10-25). Above this line: the switches, the watermark, the triage
# and alias-refusal recorders and the helpers the pipeline shares.
#
# One payload per SECTION (§4.1), five hashed blocks built
# deterministically by `assemble` (§4.2): S law, G glossary, P
# precedents, M summaries, E the section with its paragraph numbers. The
# model answers in the candidate contract of §4.4 — every candidate
# carries the verbatim span it was read from — and eight deterministic
# gates, each a named skip count, stand between the reply and the
# per-essay adjudicator (extraction_adjudicator.py, §4.5).
# ===================================================================

# §7, the knobs. `[extraction]` in config.toml overrides these; the four
# in CALIBRATED are then overridden by the latest `extract calibrate`
# run recorded on the manuscript (§4.6: thresholds move on measurement).
KNOB_DEFAULTS: dict = {
    "confidence_floor": 0.70,
    "max_concepts_per_essay": 12,
    "max_links_per_essay": 15,
    "proposable_relations": list(DEFAULT_PROPOSABLE),
    "precedent_jaccard": 0.72,      # loop.NEAR_DUPLICATE, unchanged
    "precedents_nearest": 20,
    "summaries": "prefer",          # prefer | require | ignore
    "max_section_chars": 12000,
    "thinking_budget": 0,
    "run_cost_ceiling_usd": 20.0,
    "adjudicator_prompt": "ask",    # ask | default
}
CALIBRATED = ("proposable_relations", "confidence_floor",
              "max_concepts_per_essay", "max_links_per_essay")
SUMMARY_MODES = ("prefer", "require", "ignore")


def knobs(config: dict | None, db: Database | None = None,
          manuscript: dict | None = None) -> dict:
    """The effective `[extraction]` settings for one run. Model ids are
    the owner's configuration, never constants here: `model` (section
    calls; None means `[llm] model`), `adjudicator_model` (the default
    answer to the run-time question, ruling §0.2) and
    `adjudicator_choices` (the models that question offers)."""
    section = (config or {}).get("extraction") or {}
    out = dict(KNOB_DEFAULTS)
    out.update({k: section[k] for k in KNOB_DEFAULTS if k in section})
    out["model"] = section.get("model") or None
    out["adjudicator_model"] = section.get("adjudicator_model") or None
    out["adjudicator_choices"] = list(section.get("adjudicator_choices") or [])
    if out["summaries"] not in SUMMARY_MODES:
        raise ValueError(f"[extraction] summaries = '{out['summaries']}' — "
                         f"one of {', '.join(SUMMARY_MODES)}")
    out["calibrated_by"] = None
    if db is not None and manuscript is not None:
        latest = next((r for r in reversed(runs(db, manuscript))
                       if r.get("kind") == "calibrate" and r.get("thresholds")),
                      None)
        if latest:
            out.update({k: latest["thresholds"][k] for k in CALIBRATED
                        if k in latest["thresholds"]})
            out["calibrated_by"] = latest["id"]
    return out


# --------------------------------------------------- the run record (§4.8)

def _extraction_meta(db: Database, manuscript: dict) -> dict:
    row = db.one("SELECT metadata FROM manuscripts WHERE id = ?",
                 (manuscript["id"],))
    meta = json.loads((row["metadata"] if row else None) or "{}")
    return meta.get("extraction") or {}


def runs(db: Database, manuscript: dict) -> list[dict]:
    """`manuscripts.metadata.extraction.runs`, oldest first: one entry per
    native run (`kind: extract`) and per recorded calibration (`kind:
    calibrate`, which also carries the thresholds it moved)."""
    return list(_extraction_meta(db, manuscript).get("runs") or [])


def new_run_id(kind: str) -> str:
    """`extract-…` / `calibrate-…`: the tag a run's rows carry in metadata
    and in `knowledge_proposals.source` (`extraction/<run>`)."""
    from .db import new_id

    return new_id(kind)


def record_run(db: Database, manuscript: dict, entry: dict) -> None:
    """Append one run entry, merged into the row's CURRENT metadata (the
    Doc mapping, the watermark and the switch live beside it; see
    `_set_extraction_watermark` on why the row is re-read)."""
    row = db.one("SELECT metadata FROM manuscripts WHERE id = ?",
                 (manuscript["id"],))
    meta = json.loads((row["metadata"] if row else None) or "{}")
    section = dict(meta.get("extraction") or {})
    section["runs"] = [*(section.get("runs") or []), entry]
    meta["extraction"] = section
    encoded = json.dumps(meta)
    db.update("manuscripts", manuscript["id"], {"metadata": encoded})
    manuscript["metadata"] = encoded


# ------------------------------------------------------ the unit (§4.1)

def _plain_heading(line: str | None) -> str | None:
    if not line:
        return None
    return line.lstrip("#").strip().strip("*").strip() or None


def extraction_units(text: str, max_chars: int,
                     verse_file: bool = False) -> list[dict]:
    """The file's heading sections — `summaries.sections_of`, the cut
    §4.1 ratified — as extraction units, every paragraph carrying the
    file-wide `[n]` that `summaries._numbered_paragraphs` prints. A
    section of nothing but its heading is not a unit. A section longer
    than `max_chars` splits at paragraph boundaries into parts; a single
    paragraph longer than the cap stays whole (extraction never
    truncates).

        {section, sections, part, parts, heading, section_hash,
         paragraphs: [(n, text)]}"""
    from .summaries import sections_of

    secs = sections_of(text, verse_file=verse_file)
    units: list[dict] = []
    for sec in secs:
        if all(p["kind"] == "heading" for p in sec["paragraphs"]):
            continue
        parts: list[list[tuple[int, str]]] = [[]]
        size = 0
        for p in sec["paragraphs"]:
            if parts[-1] and size + len(p["text"]) > max_chars:
                parts.append([])
                size = 0
            parts[-1].append((p["ordinal"], p["text"]))
            size += len(p["text"]) + 2
        for i, part in enumerate(parts, 1):
            units.append({"section": sec["section_ordinal"],
                          "sections": len(secs), "part": i,
                          "parts": len(parts), "heading": sec["heading"],
                          "section_hash": sec["section_hash"],
                          "paragraphs": part})
    return units


def unit_key(unit: dict) -> str:
    """'3', or '3.2' for the second part of a split section — what
    `--section` accepts beside the heading text."""
    return (f"{unit['section']}" if unit["parts"] == 1
            else f"{unit['section']}.{unit['part']}")


def select_unit(units: list[dict], section: str | int) -> list[dict]:
    """The unit(s) `--section` names: a key ('3', '3.2'), or the heading's
    text (case-blind, markup ignored; exact first, then a unique
    substring). Every part of a split section answers to its heading."""
    wanted = str(section).strip()
    by_key = [u for u in units if unit_key(u) == wanted
              or (wanted.isdigit() and str(u["section"]) == wanted)]
    if by_key:
        return by_key
    fold = (_plain_heading(wanted) or wanted).lower()
    exact = [u for u in units
             if (_plain_heading(u["heading"]) or "").lower() == fold]
    if exact:
        return exact
    loose = [u for u in units
             if fold in (_plain_heading(u["heading"]) or "").lower()]
    headings = {u["section"] for u in loose}
    if len(headings) == 1:
        return loose
    listing = "; ".join(f"{unit_key(u)} {_plain_heading(u['heading']) or '(preamble)'}"
                        for u in units)
    if not loose:
        raise LookupError(f"no section matching '{section}' — {listing}")
    raise LookupError(f"'{section}' matches {len(headings)} sections — "
                      f"name one: {listing}")


# ------------------------------------------------------- the blocks (§4.2)

def _sha(text: str) -> str:
    import hashlib

    return hashlib.sha256((text or "").encode()).hexdigest()


def _block(label: str, body: str) -> str:
    body = (body or "").strip()
    return f"{label}\n{body or '(none)'}\n"


SECTION_PROMPT_PATH = Path(__file__).parent / "prompts" / "extraction.md"


def section_system(proposable: list[str] | tuple[str, ...] | None = None
                   ) -> str:
    """`prompts/extraction.md`, rendered: the kinds, and the relations this
    run may propose (the calibrated set, or the §4.4 default)."""
    relations = sorted(proposable if proposable is not None
                       else DEFAULT_PROPOSABLE)
    return (SECTION_PROMPT_PATH.read_text(encoding="utf-8")
            .replace("<<VALID_KINDS>>", str(sorted(NODE_KINDS)))
            .replace("<<PROPOSABLE_RELATIONS>>", str(relations)))


def _law_block(db: Database, mid: str, rel: str, proposable) -> str:
    """S: the extraction prompt, then the file's effective style law
    (`styles.effective_style`) — the lexicon and protected terms are what
    tell a term of art from an English word."""
    from .styles import effective_style

    elements = effective_style(db, mid, rel)
    law = "\n".join(f"- [{e['aspect']}] {e['statement']}" for e in elements)
    return (section_system(proposable) + "\n"
            + _block(f"STYLE LAW for {rel} (ratified by the author)",
                     law or "(no style law on record for this file)"))


def glossary_nodes(db: Database, manuscript: dict, rel: str, text: str,
                   exclude: set[str] | None = None) -> tuple[list[dict], list[dict]]:
    """(nodes, settled edges) of the graph's slice for this file — the
    `api.scoped_concepts(file=)` selection, with the FULL notes (the
    compact projection previews them). `exclude` drops node ids (the
    replay leaves an essay's own candidates out of its glossary)."""
    from . import api
    from .db import loads

    try:
        sliced = api.scoped_concepts(db, manuscript, file=rel, text=text)
    except LookupError:
        return [], []
    names = {n["name"] for n in sliced.get("nodes", [])}
    exclude = exclude or set()
    nodes = [dict(r) for r in db.all(
        "SELECT * FROM concept_nodes WHERE manuscript_id = ? "
        "AND status != 'retired' ORDER BY name", (manuscript["id"],))
        if r["name"] in names and r["id"] not in exclude]
    ids = {n["id"] for n in nodes}
    by_id = {n["id"]: n["name"] for n in nodes}
    edges = [{"from": by_id[e["from_node"]], "relation": e["relation"],
              "to": by_id[e["to_node"]], "status": e["status"]}
             for e in db.all(
                 "SELECT * FROM concept_edges WHERE manuscript_id = ? "
                 "AND status NOT IN ('rejected', 'retired', 'inferred')",
                 (manuscript["id"],))
             if e["from_node"] in ids and e["to_node"] in ids]
    for node in nodes:
        node["_aliases"] = loads(node.get("aliases"), []) or []
        node["_meta"] = loads(node.get("metadata"), {}) or {}
    return nodes, edges


def _glossary_block(nodes: list[dict], edges: list[dict]) -> str:
    """G: name, kind, aliases, where introduced, the author's notes; an
    extracted node the author has not confirmed says so — its notes are a
    hypothesis, not a ratified definition."""
    lines = []
    for node in nodes:
        also = (f" (also: {', '.join(sorted(node['_aliases']))})"
                if node["_aliases"] else "")
        where = (f" [introduced in {node['introduced_in']}]"
                 if node.get("introduced_in") else "")
        meta = node["_meta"]
        pending = (" [unconfirmed]" if meta.get("origin") == "extracted"
                   and not meta.get("confirmed") else "")
        notes = (node.get("notes") or "").strip() or "(no notes)"
        lines.append(f"- {node['name']} ({node['kind']}){also}{where}"
                     f"{pending} — {notes}")
    if edges:
        lines.append("")
        lines.append("Relations the author has settled between them:")
        lines.extend(sorted(f"- {e['from']} —{e['relation']}→ {e['to']}"
                            for e in edges))
    return _block("G — GLOSSARY (the graph's slice for this essay)",
                  "\n".join(lines) or "(no concepts recorded for this essay)")


_REASON = " — reason: "
_KIND_SUFFIX = re.compile(r"^(?P<name>.*) \((?P<kind>[a-z_]+)\)$")


def rejection_reasons(db: Database, mid: str) -> dict[str, str]:
    """name (lower-cased) → the author's latest stated reason for
    rejecting it, from `concept_triage` rows: `metadata.explanation` when
    present (§4.6), else the ' — reason: ' tail `record_triage` writes."""
    from .db import loads

    out: dict[str, str] = {}
    for row in db.all(
            "SELECT target, metadata FROM evidence WHERE manuscript_id = ? "
            "AND evidence_type = 'concept_triage' AND signal = 'rejected' "
            "ORDER BY created_at", (mid,)):
        head, _, tail = row["target"].partition(_REASON)
        reason = (loads(row["metadata"], {}) or {}).get("explanation") or tail
        m = _KIND_SUFFIX.match(head)
        name = (m.group("name") if m else head).strip()
        if reason.strip():
            out[name.lower()] = reason.strip()
    return out


_CAPITALISED = re.compile(r"[A-Z][\w’'-]+(?:[ \t]+[A-Z][\w’'-]+)*")


def capitalised_terms(text: str) -> set[str]:
    return set(_CAPITALISED.findall(text.replace("*", "")))


def precedent_rows(db: Database, manuscript: dict, rel: str, text: str,
                   nearest: int, exclude: set[str] | None = None
                   ) -> list[dict]:
    """Block P's rows (§4.2, §4.6 "whole, not windowed"): EVERY retired
    extracted name of this essay — introduced here, retired from here, or
    named in its text — then the `nearest` retired names elsewhere by
    `loop.similarity` to the essay's capitalised terms. Computed per
    essay, not per section, so the block is one cached prefix across the
    essay's section calls (§6.2). `exclude` drops node ids."""
    from .db import loads
    from .loop import similarity

    reasons = rejection_reasons(db, manuscript["id"])
    exclude = exclude or set()
    mine, others = [], []
    for row in db.all(
            "SELECT * FROM concept_nodes WHERE manuscript_id = ? "
            "AND status = 'retired' ORDER BY name", (manuscript["id"],)):
        meta = loads(row["metadata"], {}) or {}
        if meta.get("origin") != "extracted" or row["id"] in exclude:
            continue
        entry = {"id": row["id"], "name": row["name"], "kind": row["kind"],
                 "reason": reasons.get(row["name"].lower())}
        retired_from = (meta.get("retired_from") or {}).get("introduced_in")
        if (rel in (row["introduced_in"], retired_from)
                or concept_pattern(row["name"]).search(text)):
            mine.append(entry)
        else:
            others.append(entry)
    terms = capitalised_terms(text)
    scored = []
    for entry in others:
        score = max((similarity(entry["name"], t) for t in terms), default=0.0)
        if score > 0:
            scored.append((score, entry["name"].lower(), entry))
    scored.sort(key=lambda s: (-s[0], s[1]))
    near = [{**e, "nearest": round(s, 2)} for s, _n, e in scored[:nearest]]
    return mine + near


def _precedent_block(rows: list[dict], rel: str) -> str:
    def line(e: dict) -> str:
        why = f" — {e['reason']}" if e.get("reason") else ""
        return f"- rejected: {e['name']} ({e['kind']}){why}"

    mine = [e for e in rows if "nearest" not in e]
    near = [e for e in rows if "nearest" in e]
    body = [f"Rejected names of {rel} ({len(mine)}):"]
    body += [line(e) for e in mine] or ["(none)"]
    body += ["", f"Nearest rejected names elsewhere ({len(near)}):"]
    body += [line(e) for e in near] or ["(none)"]
    return _block("P — PRECEDENTS (the author's rejections; authoritative)",
                  "\n".join(body))


def _summary_block(db: Database, manuscript: dict, rel: str, text: str,
                   mode: str) -> tuple[str, dict | None]:
    """M (§4.3): the summary hierarchy's three levels for this essay,
    from `summaries.for_unit` (PR nagarkar/authorllm#269). A missing level
    prints as a labelled absence; a level whose hash no longer matches the
    text prints STALE. Summaries are a candidate source and the
    adjudicator's one-line context, never grounding."""
    if mode == "ignore":
        return (_block("M — SUMMARIES", "SUMMARIES: ignored "
                       "([extraction] summaries = ignore)"), None)
    from . import summaries

    unit = summaries.for_unit(db, manuscript, rel, text=text)
    lines: list[str] = []
    essay = unit.get("essay")
    if essay is None:
        lines.append("SUMMARIES: none for essay")
    else:
        stale = (" — STALE: the essay changed since this was written; a "
                 "candidate source only" if essay.get("stale") else "")
        lines.append(f"ESSAY (source_hash {essay['source_hash']}){stale}:")
        lines.append((essay.get("summary") or "").strip())
        for key in ("introduces", "assumes", "promises"):
            values = essay.get(key) or []
            lines.append(f"{key.upper()}: {'; '.join(values) or '(none)'}")
    lines.append("")
    sections = unit.get("sections")
    if sections is None:
        lines.append("SUMMARIES: none for sections")
    else:
        for sec in sections:
            lo, hi = sec["paragraphs"]
            stale = (" — STALE: no summary of the current text"
                     if sec.get("stale") else "")
            lines.append(f"SECTION {sec['n']}: "
                         f"{_plain_heading(sec['heading']) or '(preamble)'} "
                         f"[{lo}]-[{hi}]{stale}")
            if sec.get("summary"):
                lines.append(sec["summary"].strip())
            if sec.get("terms"):
                lines.append(f"terms: {'; '.join(sec['terms'])}")
    lines.append("")
    paragraphs = unit.get("paragraphs")
    if paragraphs is None:
        lines.append("SUMMARIES: none for paragraphs")
    else:
        lines.append("PARAGRAPH GISTS:")
        for p in paragraphs:
            if p.get("kind") == "heading":
                continue
            if p.get("stale"):
                lines.append(f"[{p['n']}] (STALE: no record of the current text)")
                continue
            terms = f" (terms: {'; '.join(p['terms'])})" if p.get("terms") else ""
            lines.append(f"[{p['n']}] {p.get('gist') or ''}{terms}")
    if mode == "require":
        missing = [level for level in ("essay", "sections", "paragraphs")
                   if unit.get(level) is None]
        stale = [level for level, flag in (
            ("essay", bool(essay and essay.get("stale"))),
            ("sections", bool(unit.get("section_level_stale"))),
            ("paragraphs", bool(unit.get("paragraph_level_stale")))) if flag]
        if missing or stale:
            raise ValueError(
                f"[extraction] summaries = require, and {rel} has "
                + "; ".join(filter(None, [
                    f"no {', '.join(missing)} summaries" if missing else "",
                    f"STALE {', '.join(stale)} summaries" if stale else ""]))
                + " — build them ('summarize'), or set summaries = prefer.")
    return (_block("M — SUMMARIES (context only: they may suggest a "
                   "candidate, never ground one)", "\n".join(lines)), unit)


def _section_block(rel: str, title: str, unit: dict) -> str:
    """E: the section under extraction, headed by its essay and position,
    each paragraph prefixed `[n]` exactly as `summaries._numbered_paragraphs`
    numbers the file."""
    body = "\n\n".join(f"[{n}] {p}" for n, p in unit["paragraphs"])
    lo, hi = unit["paragraphs"][0][0], unit["paragraphs"][-1][0]
    part = (f", part {unit['part']} of {unit['parts']}"
            if unit["parts"] > 1 else "")
    heading = _plain_heading(unit["heading"]) or "(preamble)"
    return _block(f"E — THE SECTION — {rel} — “{title}” — section "
                  f"{unit['section']} of {unit['sections']}{part}: {heading} "
                  f"— paragraphs [{lo}]-[{hi}] — sha256 {_sha(body)}", body)


@dataclass(frozen=True)
class ExtractionPayload:
    """The five blocks of one section call, in volatility order: S, G, P
    and M are identical across the sections of one essay (one cached
    prefix, §6.2); only E changes. Printable, hashed, and the same whether
    the configured model or a subagent answers it (principle 6)."""
    law: str            # S — prompt + the file's style law
    glossary: str       # G — the graph's slice for this file
    precedents: str     # P — the author's rejections, whole
    summaries: str      # M — the summary hierarchy, or its absence
    section: str        # E — the section, paragraphs numbered [n]
    file: str
    title: str
    unit: dict
    essay_sha: str
    summary_unit: dict | None = None
    precedent_list: tuple = ()

    @property
    def system(self) -> str:
        return self.law

    @property
    def user(self) -> str:
        return "\n".join((self.glossary, self.precedents, self.summaries,
                          self.section))

    @property
    def blocks(self) -> list[tuple[str, str]]:
        return [("S", self.law), ("G", self.glossary), ("P", self.precedents),
                ("M", self.summaries), ("E", self.section)]

    @property
    def hashes(self) -> dict[str, str]:
        return {name: _sha(text) for name, text in self.blocks}

    @property
    def paragraphs(self) -> dict[int, str]:
        return dict(self.unit["paragraphs"])

    @property
    def label(self) -> str:
        return f"{self.file} §{unit_key(self.unit)}"

    def render(self, shared: bool = True) -> str:
        """The printed form: every block with its size and hash, so a
        reply can be traced to exactly what was seen. `shared=False`
        prints E alone (the essay's other blocks were printed once)."""
        out = []
        for label, text in self.blocks:
            if not shared and label != "E":
                continue
            out.append(f"\n───── block {label} — {len(text):,} chars — "
                       f"sha256 {_sha(text)}\n")
            out.append(text.rstrip("\n"))
        return "\n".join(out) + "\n"


def eligible_files(files: dict[str, str]) -> list[str]:
    """Content files in reading order that extraction may read: not the
    toc or a sidecar, not empty, not held by an active writeup."""
    from .structure import is_structural, reading_order

    order, _ = reading_order(files)
    return [n for n in order if not is_structural(n)
            and (files.get(n) or "").strip() and not is_in_flight(files[n])]


def assemble_essay(db: Database, manuscript: dict, file: str,
                   section: str | int | None = None, *,
                   config: dict | None = None,
                   files: dict[str, str] | None = None,
                   units: list[dict] | None = None,
                   knob: dict | None = None) -> list[ExtractionPayload]:
    """Every section payload of one essay (or the `section` it names, or
    the given `units`), with S, G, P and M rendered once and shared."""
    from .lenses import _resolve, _title_of
    from .structure import refuse_sidecar
    from .summaries import verse_files_of

    refuse_sidecar(Path(file).name)
    if files is None:
        files = read_manuscript_files(Path(manuscript["path"]))
    rel = _resolve(files, file)
    text = files[rel]
    if is_in_flight(text):
        raise ValueError(f"{rel} is held by an active writeup (its text on "
                         "disk is the placeholder) — nothing to extract.")
    knob = knob or knobs(config, db, manuscript)
    if units is None:
        units = extraction_units(text, int(knob["max_section_chars"]),
                                 verse_file=rel in verse_files_of(manuscript))
        if section is not None:
            units = select_unit(units, section)
    if not units:
        return []
    mid = manuscript["id"]
    law = _law_block(db, mid, rel, knob["proposable_relations"])
    nodes, edges = glossary_nodes(db, manuscript, rel, text)
    glossary = _glossary_block(nodes, edges)
    prec = precedent_rows(db, manuscript, rel, text,
                          int(knob["precedents_nearest"]))
    precedents = _precedent_block(prec, rel)
    summaries_text, summary_unit = _summary_block(db, manuscript, rel, text,
                                                  knob["summaries"])
    title = _title_of(text) or rel
    return [ExtractionPayload(
        law=law, glossary=glossary, precedents=precedents,
        summaries=summaries_text, section=_section_block(rel, title, unit),
        file=rel, title=title, unit=unit, essay_sha=_sha(text),
        summary_unit=summary_unit, precedent_list=tuple(prec))
        for unit in units]


def assemble(db: Database, manuscript: dict, file: str,
             section: str | int | None = None, *,
             config: dict | None = None,
             files: dict[str, str] | None = None) -> ExtractionPayload:
    """The payload of ONE section call (§4.2), deterministically, from
    stored state and the manuscript on disk. `section` is a key ('3',
    '3.2') or the heading's text; it may be omitted only when the file is
    a single unit."""
    payloads = assemble_essay(db, manuscript, file, section, config=config,
                              files=files)
    if not payloads:
        raise LookupError(f"{file} has no section with prose to extract")
    if len(payloads) > 1:
        listing = "; ".join(
            f"{unit_key(p.unit)} {_plain_heading(p.unit['heading']) or '(preamble)'}"
            for p in payloads)
        raise LookupError(f"{payloads[0].file} has {len(payloads)} units — "
                          f"name one with --section: {listing}")
    return payloads[0]


def render_essay(payloads: list[ExtractionPayload]) -> str:
    """`extract <file>` without `--native`: the shared blocks once, then
    each section's E, every block hashed. No model call is made."""
    if not payloads:
        return ""
    first = payloads[0]
    head = (f"extract {first.file} — {len(payloads)} section call(s) — no "
            "model call was made; run again with --native to make the calls.")
    out = [head, first.render(shared=True)]
    out += [p.render(shared=False) for p in payloads[1:]]
    return "\n".join(out)


def changed_units(db: Database, manuscript: dict, files: dict[str, str],
                  max_chars: int) -> dict[str, list[dict]] | None:
    """The incremental scope: per file, the units whose section is not
    present (by `section_hash`) in the text at the extraction watermark.
    None when there is no watermark to diff against (everything is in
    scope); {} when nothing changed."""
    from .summaries import sections_of

    changed, _latest, old_files, _new = changed_files_since_extraction(
        db, manuscript)
    if changed is None or old_files is None:
        return None if changed is None else {}
    out: dict[str, list[dict]] = {}
    for name in eligible_files(files):
        if name not in changed:
            continue
        old = {s["section_hash"] for s in sections_of(old_files.get(name, ""))}
        units = [u for u in extraction_units(files[name], max_chars)
                 if u["section_hash"] not in old]
        if units:
            out[name] = units
    return out


# ------------------------------------------------------- the section calls

def extraction_llm(config: dict, knob: dict | None = None) -> LLMClient:
    """The section-call client: `[llm]`'s transport, `[extraction] model`
    (falling back to `[llm] model`), purpose `extraction` in the usage
    ledger."""
    knob = knob or knobs(config)
    client = LLMClient(config)
    if knob.get("model"):
        client.model = knob["model"]
    client.purpose = "extraction"
    return client


def call_sections(llm: LLMClient, payloads: list[ExtractionPayload],
                  knob: dict) -> list[dict | None]:
    """One call per section payload, the blocks sent as `system` = S and
    `user` = G P M E (so the shared prefix is identical across an essay's
    calls). A reply that is not a JSON object, or whose `concepts` or
    `links` is present but not a list (a null where the contract wants a
    list), is None: the section is unanswered, never read as 'nothing
    found', and the watermark stays."""
    replies: list[dict | None] = []
    for payload in payloads:
        reply = llm.complete_json(payload.system, payload.user,
                                  thinking_budget=knob.get("thinking_budget"))
        if isinstance(reply, dict) and all(
                isinstance(reply.get(key, []), list)
                for key in ("concepts", "links", "aliases")):
            replies.append(reply)
        else:
            replies.append(None)
    return replies


# ------------------------------------------- the candidate contract (§4.4)
#
# A reply carries, per candidate, where it was read: `paragraphs` (a
# concept) or `paragraph` (a link, an alias), the verbatim `span`, a
# `confidence` and a one-clause `why`. Parsing keeps every candidate
# that has the fields the gates need; the gates then decide, each failure
# a named skip count.

# The skip counts, in gate order after the two contract failures. Each is
# also the `signal` of the candidate's `extraction_adjudication` row.
SKIPS = ("malformed", "unknown_endpoint", "ungrounded", "common_word",
         "below_bar", "retired", "duplicate", "not_proposable", "below_floor")
GATE = {"ungrounded": "1-2", "common_word": "3", "below_bar": "4",
        "retired": "5", "duplicate": "6", "not_proposable": "7",
        "below_floor": "8", "unknown_endpoint": "contract",
        "malformed": "contract"}


def _confidence(value) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return min(1.0, max(0.0, float(value)))
    except (TypeError, ValueError):
        return None


def _ints(value) -> list[int]:
    """Paragraph numbers as the model may write them: 7, "7", "[7]",
    [7, 9]."""
    if isinstance(value, bool) or value is None:
        return []
    if isinstance(value, int):
        return [value]
    if isinstance(value, str):
        digits = value.strip().strip("[]").strip()
        return [int(digits)] if digits.isdigit() else []
    if isinstance(value, list):
        return [n for item in value for n in _ints(item)]
    return []


def parse_reply(reply: dict | None, payload: ExtractionPayload
                ) -> tuple[list[dict], int]:
    """(candidates, malformed) from one section reply. Each candidate is
    tagged with its file, section and unit, and carries `_cited`: the
    text of the paragraphs it cites that ARE in the section the call saw
    (a number outside it cites nothing, and gate 1 says so)."""
    if not isinstance(reply, dict):
        return [], 0
    paragraphs = payload.paragraphs
    base = {"file": payload.file,
            "section": _plain_heading(payload.unit["heading"]),
            "unit": unit_key(payload.unit)}
    out: list[dict] = []
    malformed = 0

    def common(item: dict, cited: list[int]) -> dict:
        return {**base, "span": str(item.get("span") or "").strip(),
                "confidence": _confidence(item.get("confidence")),
                "why": str(item.get("why") or "").strip()[:300],
                "_cited": {n: paragraphs[n] for n in cited if n in paragraphs}}

    for item in reply.get("concepts") or []:
        if not isinstance(item, dict) or not str(item.get("name") or "").strip():
            malformed += 1
            continue
        kind = item.get("kind", "concept")
        if not (isinstance(kind, str) and kind in NODE_KINDS):
            kind = "concept"
        cited = _ints(item.get("paragraphs", item.get("paragraph")))
        notes = str(item.get("notes") or "").strip()[:300] or None
        out.append({"type": "concept", "name": str(item["name"]).strip()[:80],
                    "kind": kind, "notes": notes, "paragraphs": cited,
                    **common(item, cited)})
    for item in reply.get("links") or []:
        if not isinstance(item, dict):
            malformed += 1
            continue
        src, relation, dst = link_endpoints(item)
        if not src or not dst or not relation or src.lower() == dst.lower():
            malformed += 1
            continue
        cited = _ints(item.get("paragraph", item.get("paragraphs")))[:1]
        out.append({"type": "link", "from": src[:80], "relation": relation,
                    "to": dst[:80], "paragraph": cited[0] if cited else None,
                    **common(item, cited)})
    for item in reply.get("aliases") or []:
        if not isinstance(item, dict):
            malformed += 1
            continue
        alias = str(item.get("alias") or "").strip()[:80]
        canonical = str(item.get("canonical") or "").strip()[:80]
        sentence = " ".join(str(item.get("sentence") or "").split())[:300]
        if not alias or not canonical or not sentence:
            malformed += 1
            continue
        cited = _ints(item.get("paragraph", item.get("paragraphs")))[:1]
        out.append({"type": "alias", "alias": alias, "canonical": canonical,
                    "sentence": sentence,
                    "paragraph": cited[0] if cited else None,
                    **common({**item, "span": sentence}, cited)})
    return out, malformed


def candidate_label(c: dict) -> str:
    if c["type"] == "link":
        return f"{c['from']} —{c['relation']}→ {c['to']}"
    if c["type"] == "alias":
        return f"{c['alias']} = {c['canonical']}"
    return f"{c['name']} ({c['kind']})"


# ------------------------------------------------------- the gates (§4.4)

def _fold(text: str | None) -> str:
    """Whitespace folded and emphasis markers dropped: the book's headings
    are bold, and a model quoting `**Rankings**` as `Rankings` has still
    quoted the text. Nothing else is forgiven — not a word, not a comma."""
    return " ".join((text or "").replace("*", "").split())


def span_in(span: str, paragraph: str) -> bool:
    """Gate 1: the span occurs verbatim in the paragraph."""
    folded = _fold(span)
    return bool(folded) and folded in _fold(paragraph)


def names_in(names: list[str], paragraph: str) -> bool:
    return any(concept_pattern(n).search(paragraph) for n in names if n)


def _sentence_initial(text: str, at: int) -> bool:
    i = at - 1
    while i >= 0 and text[i] in " \t*_#>\"'“‘([":
        i -= 1
    return i < 0 or text[i] in ".!?\n"


def is_term_of_art(name: str, files: dict[str, str]) -> bool:
    """Gate 3: a single capitalised word must occur capitalised somewhere
    a sentence does NOT start (`mention_pattern`, case kept). A name that
    is only ever capitalised by its position is an English word, not a
    term — the "Experience" failure of §2.4 #12. Multi-word and lowercase
    names pass: `mention_pattern` keeps their tolerant match."""
    if " " in name.strip() or not name[:1].isupper():
        return True
    pattern = mention_pattern(name)
    return any(not _sentence_initial(text, m.start())
               for text in files.values() for m in pattern.finditer(text))


_DEFINES_AFTER = re.compile(
    r"^[^.;:]{0,30}?\b(?:is|are|was|were|means|mean|names|denotes|refers "
    r"to|is called|we call|ye call)\b")
_DEFINES_BEFORE = re.compile(r"\b(?:call|calls|called|name|names|named)\b"
                             r"[^.;:]{0,30}$")


def grounding_strength(span: str, name: str, paragraph: str = "") -> float:
    """§4.4's grounding term: 1.0 when the span is a heading or
    definition-shaped about `name` ("X is …", "we call it X"), 0.7
    otherwise."""
    folded = _fold(span)
    if paragraph.lstrip().startswith("#") or folded.startswith("#"):
        return 1.0
    m = concept_pattern(name).search(folded) if name else None
    if m and (_DEFINES_AFTER.search(folded[m.end():])
              or _DEFINES_BEFORE.search(folded[:m.start()])):
        return 1.0
    return 0.7


@dataclass
class GateContext:
    """What the gates read, gathered once per essay. A live run builds it
    from the graph (`gate_context`); the replay builds its own, with the
    candidates under measurement left out of every list."""
    files: dict[str, str]              # the text recurrence and gate 3 read
    known: dict[str, dict]             # lower name or alias → live node
    retired: dict[str, dict]           # lower name → retired node (banned)
    edges: dict[tuple[str, str], list[dict]]   # (from id, to id) → live edges
    rejected: set[tuple[str, str, str]]        # (from id, relation, to id)
    rejected_names: list[str]          # retired extracted names (score)
    introduces: set[str]               # the essay summary's INTRODUCES, lower
    terms: dict[tuple[str, int], set[str]]     # (file, [n]) → summary terms


def gate_context(db: Database, manuscript: dict, files: dict[str, str],
                 summary_units: dict[str, dict | None] | None = None
                 ) -> GateContext:
    from .db import loads

    mid = manuscript["id"]
    live = [dict(r) for r in db.all(
        "SELECT * FROM concept_nodes WHERE manuscript_id = ? "
        "AND status != 'retired'", (mid,))]
    known: dict[str, dict] = {}
    for node in live:                       # aliases first: a name wins
        for alias in loads(node.get("aliases"), []) or []:
            known.setdefault(alias.lower(), node)
    for node in live:
        known[node["name"].lower()] = node
    retired: dict[str, dict] = {}
    rejected_names: list[str] = []
    for row in db.all("SELECT * FROM concept_nodes WHERE manuscript_id = ? "
                      "AND status = 'retired'", (mid,)):
        if row["name"].lower() not in known:
            retired[row["name"].lower()] = dict(row)
        if (loads(row["metadata"], {}) or {}).get("origin") == "extracted":
            rejected_names.append(row["name"])
    edges: dict[tuple[str, str], list[dict]] = {}
    rejected: set[tuple[str, str, str]] = set()
    for e in db.all("SELECT * FROM concept_edges WHERE manuscript_id = ? "
                    "AND status != 'retired'", (mid,)):
        if e["status"] == "rejected":
            rejected.add((e["from_node"], e["relation"], e["to_node"]))
        else:
            edges.setdefault((e["from_node"], e["to_node"]), []).append(dict(e))
    introduces: set[str] = set()
    terms: dict[tuple[str, int], set[str]] = {}
    for file, unit in (summary_units or {}).items():
        if not unit:
            continue
        essay = unit.get("essay") or {}
        introduces |= {t.lower() for t in essay.get("introduces") or []}
        for p in unit.get("paragraphs") or []:
            terms[(file, p["n"])] = {t.lower() for t in p.get("terms") or []}
    return GateContext(files=files, known=known, retired=retired, edges=edges,
                       rejected=rejected, rejected_names=rejected_names,
                       introduces=introduces, terms=terms)


def score(c: dict, ctx: GateContext, knob: dict) -> float:
    """§4.4: confidence × grounding strength × precedent factor × summary
    agreement, capped at 1. A replayed candidate has no confidence and
    scores as if it were 1.0. For a link the precedent factor halves the
    score when the author has rejected a relation on the same pair."""
    from .loop import similarity

    confidence = 1.0 if c.get("confidence") is None else c["confidence"]
    paragraph = next(iter(c["_cited"].values()), "")
    if c["type"] == "concept":
        strength = grounding_strength(c["span"], c["name"], paragraph)
        near = any(similarity(c["name"], r) >= float(knob["precedent_jaccard"])
                   for r in ctx.rejected_names)
        agree = 1.2 if c["name"].lower() in ctx.introduces else 1.0
    else:
        strength = grounding_strength(c["span"], c["from"], paragraph)
        a, b = c["_ends"][0][0], c["_ends"][1][0]
        near = any(r[0] in (a, b) and r[2] in (a, b) for r in ctx.rejected)
        agree = 1.0
    return round(min(1.0, confidence * strength * (0.5 if near else 1.0)
                     * agree), 4)


@dataclass
class Screening:
    """The gates' verdict on one essay's batch. Every candidate is in
    `candidates`; one that failed carries `signal` (its skip count),
    `gate` and `reason`; a survivor carries `score` and no signal."""
    candidates: list[dict]
    skips: dict[str, int]

    @property
    def survivors(self) -> list[dict]:
        return [c for c in self.candidates
                if c["type"] != "alias" and c.get("signal") is None]


def screen(candidates: list[dict], ctx: GateContext, knob: dict, *,
           revive=None, malformed: int = 0,
           apply_gate7: bool = True) -> Screening:
    """Gates 1-8 over one essay's candidates, in order, each failure
    counted under its name (§4.4). Pure: nothing is written. `revive`,
    when given, is called with (candidate, retired node) at gate 5 and may
    raise the one revival proposal §4.10 allows; the replay passes None.
    The replay also passes `apply_gate7=False`: a relation outside the
    proposable set is then measured rather than held back (§4.6).

    Concepts are screened first, so a link may end on a concept candidate
    of the same batch that survived; a link whose endpoint is neither a
    live concept nor such a survivor fails as `unknown_endpoint`."""
    import difflib

    from .loop import similarity

    skips = {name: 0 for name in SKIPS}
    skips["malformed"] = malformed
    proposable = set(knob["proposable_relations"])
    floor = float(knob["confidence_floor"])
    jaccard = float(knob["precedent_jaccard"])

    def fail(c: dict, signal: str, reason: str) -> None:
        c.update(signal=signal, gate=GATE[signal], reason=reason)
        skips[signal] += 1

    concepts = [c for c in candidates if c["type"] == "concept"]
    links = [c for c in candidates if c["type"] == "link"]

    for c in concepts:
        files = c.get("_files") or ctx.files
        cited = c["_cited"]
        if not cited:
            fail(c, "ungrounded", "cites no paragraph of the section")
            continue
        if not any(span_in(c["span"], p) for p in cited.values()):
            fail(c, "ungrounded", "the span is not in its cited paragraph")
            continue
        node = ctx.known.get(c["name"].lower())
        names = (node_names(node) if node
                 else [c["name"], *c.get("_aliases", [])])
        in_terms = any(c["name"].lower() in ctx.terms.get((c["file"], n), ())
                       for n in cited)
        if not (any(names_in(names, p) for p in cited.values()) or in_terms):
            fail(c, "ungrounded", "the name is not in its cited paragraph")
            continue
        if node is not None:
            # Already in the graph: never a new node — the adjudicator's
            # improves / subsumed question (§4.5).
            c.update(existing=node["name"], existing_id=node["id"])
            c["score"] = score(c, ctx, knob)
            continue
        if not is_term_of_art(c["name"], files):
            fail(c, "common_word", f"'{c['name']}' is capitalised only where "
                 "a sentence starts — an English word, not a term")
            continue
        if c["kind"] in RECURRENCE_GATED_KINDS:
            admitted, stats = passes_recurrence_bar(files, [c["name"]])
            if not admitted:
                fail(c, "below_bar", f"{stats['contexts']} context(s), "
                     f"{stats['sections']} section(s), {stats['files']} "
                     "file(s) — the bar is two contexts in two sections")
                continue
        retired = ctx.retired.get(c["name"].lower())
        if retired is not None:
            reason = "the author retired this name"
            proposal = revive(c, retired) if revive else None
            if proposal:
                c["proposal_id"] = proposal["id"]
                reason += ("; it recurs in text written since — the one "
                           "revival proposal §4.10 allows is raised")
            fail(c, "retired", reason)
            continue
        near = difflib.get_close_matches(c["name"].lower(), list(ctx.retired),
                                         n=1, cutoff=0.8)
        if near:
            fail(c, "retired", "a near-miss of the retired name "
                 f"'{ctx.retired[near[0]]['name']}'")
            continue
        # §4.7: a name within the firewall's distance of a live concept is a
        # subsumption question for the adjudicator, never a new node.
        best = max(((similarity(c["name"], k), node) for k, node in
                    ctx.known.items()), key=lambda s: s[0], default=(0.0, None))
        if best[1] is not None and best[0] >= jaccard:
            c["near"] = best[1]["name"]
        c["score"] = score(c, ctx, knob)

    alive = [c for c in concepts if c.get("signal") is None]
    by_name: dict[str, list[dict]] = {}
    for c in alive:
        by_name.setdefault(c["name"].lower(), []).append(c)
    for group in by_name.values():
        group.sort(key=lambda c: (c["score"], c.get("confidence") or 0),
                   reverse=True)
        for c in group[1:]:
            fail(c, "duplicate", f"proposed again in §{group[0]['unit']} — "
                 "folded into the stronger")
    for c in concepts:
        if (c.get("signal") is None and c.get("confidence") is not None
                and c["confidence"] < floor):
            fail(c, "below_floor", f"confidence {c['confidence']:.2f} under "
                 f"the floor {floor:.2f}")

    batch = {c["name"].lower(): c for c in concepts
             if c.get("signal") is None and "existing" not in c}
    for c in links:
        ends = []
        for side in ("from", "to"):
            node = ctx.known.get(c[side].lower())
            if node is not None:
                ends.append((node["id"], node_names(node), node["name"]))
            elif c[side].lower() in batch:
                fresh = batch[c[side].lower()]
                ends.append((f"new:{fresh['name'].lower()}",
                             [fresh["name"], *fresh.get("_aliases", [])],
                             fresh["name"]))
            else:
                ends.append(None)
        missing = [c[s] for s, e in zip(("from", "to"), ends) if e is None]
        if missing:
            fail(c, "unknown_endpoint", f"'{missing[0]}' is neither a live "
                 "concept nor a surviving candidate")
            continue
        if ends[0][0] == ends[1][0]:
            fail(c, "malformed", f"'{c['from']}' and '{c['to']}' name the "
                 "same concept")
            continue
        c["_ends"] = ends
        c["from"], c["to"] = ends[0][2], ends[1][2]
        paragraph = next(iter(c["_cited"].values()), None)
        if paragraph is None:
            fail(c, "ungrounded", "cites no paragraph of the section")
            continue
        if not span_in(c["span"], paragraph):
            fail(c, "ungrounded", "the span is not in its cited paragraph")
            continue
        if not (names_in(ends[0][1], paragraph)
                and names_in(ends[1][1], paragraph)):
            fail(c, "ungrounded", "the cited paragraph does not name both "
                 "endpoints")
            continue
        if (ends[0][0], c["relation"], ends[1][0]) in ctx.rejected:
            fail(c, "retired", "the author rejected this relation")
            continue
        c["score"] = score(c, ctx, knob)

    def edge_label(e: dict) -> str:
        names = {c["_ends"][0][0]: c["_ends"][0][2],
                 c["_ends"][1][0]: c["_ends"][1][2]}
        return (f"{names.get(e['from_node'], '?')} —{e['relation']}→ "
                f"{names.get(e['to_node'], '?')}")

    alive_links = [c for c in links if c.get("signal") is None]
    for c in alive_links:
        a, b = c["_ends"][0][0], c["_ends"][1][0]
        same = [e for e in ctx.edges.get((a, b), [])
                if e["relation"] == c["relation"]]
        pair = ctx.edges.get((a, b), []) + ctx.edges.get((b, a), [])
        if same:
            c["refound"] = same[0]["id"]
            fail(c, "duplicate", f"re-found {edge_label(same[0])} — its "
                 "evidence gains this span")
        elif pair:
            fail(c, "duplicate", f"folded into {edge_label(pair[0])}, "
                 "already in the graph")
    pairs: dict[frozenset, list[dict]] = {}
    for c in links:
        if c.get("signal") is None:
            pairs.setdefault(frozenset((c["_ends"][0][0], c["_ends"][1][0])),
                             []).append(c)
    for group in pairs.values():
        # The stronger one: a relation extraction may propose before one it
        # may not, then score — so a mirror can never displace the edge the
        # author would keep (§2.4 #16 against #5).
        group.sort(key=lambda c: (c["relation"] in proposable, c["score"],
                                  c.get("confidence") or 0), reverse=True)
        for c in group[1:]:
            fail(c, "duplicate", f"folded into {candidate_label(group[0])} "
                 f"(§{group[0]['unit']})")
    for c in links:
        if c.get("signal") is not None:
            continue
        if c["relation"] not in VALID_RELATIONS:
            fail(c, "not_proposable", f"'{c['relation']}' is not in the "
                 "schema's vocabulary")
        elif apply_gate7 and c["relation"] not in proposable:
            fail(c, "not_proposable", f"'{c['relation']}' is not among the "
                 "relations extraction may propose")
    for c in links:
        if (c.get("signal") is None and c.get("confidence") is not None
                and c["confidence"] < floor):
            fail(c, "below_floor", f"confidence {c['confidence']:.2f} under "
                 f"the floor {floor:.2f}")
    return Screening(candidates=list(candidates), skips=skips)


def screen_replies(db: Database, manuscript: dict,
                   payloads: list[ExtractionPayload],
                   replies: list[dict | None], knob: dict, *,
                   files: dict[str, str] | None = None,
                   revive=None) -> Screening:
    """One essay's section replies, parsed and screened together — gate 6
    folds mirrors across the essay's sections, not just within one."""
    candidates: list[dict] = []
    malformed = 0
    for payload, reply in zip(payloads, replies):
        got, bad = parse_reply(reply, payload)
        candidates += got
        malformed += bad
    if files is None:
        files = read_manuscript_files(Path(manuscript["path"]))
    units = {payloads[0].file: payloads[0].summary_unit} if payloads else {}
    ctx = gate_context(db, manuscript, files, units)
    return screen(candidates, ctx, knob, revive=revive, malformed=malformed)


def render_screening(screening: Screening) -> str:
    """The gates' tally — the prompt's bug report (§4.6: a rise in
    `ungrounded` means the model paraphrases spans, in `common_word` that
    it names English words) — then each candidate and its fate."""
    survivors = screening.survivors
    counts = ", ".join(f"{k} {v}" for k, v in screening.skips.items() if v)
    lines = [f"gates: {len(survivors)} of "
             f"{sum(1 for c in screening.candidates if c['type'] != 'alias')}"
             f" candidate(s) passed" + (f"; {counts}" if counts else "")]
    for c in screening.candidates:
        if c["type"] == "alias":
            continue
        where = f"§{c['unit']}"
        if c.get("signal"):
            lines.append(f"  ✗ {candidate_label(c)} [{where}] — gate "
                         f"{c['gate']} {c['signal']}: {c['reason']}")
        else:
            extra = (f" (in the graph as '{c['existing']}')"
                     if c.get("existing") else
                     f" (near '{c['near']}')" if c.get("near") else "")
            lines.append(f"  ✓ {candidate_label(c)} [{where}] score "
                         f"{c['score']:.2f}{extra}")
    return "\n".join(lines)


# ----------------------------------- retired names that come back (§4.10)

def _norm_paragraph(text: str) -> str:
    return " ".join(text.split())


def retired_at(db: Database, mid: str, node: dict) -> str:
    """When the author retired `node`: the stamp `retire_concept` writes
    (`metadata.retired_from.at`), else the latest `concept_triage`
    rejection of the name, else the node's creation (the text then is
    the oldest text that could have argued for it)."""
    from .db import loads

    stamp = ((loads(node.get("metadata"), {}) or {}).get("retired_from")
             or {}).get("at")
    if stamp:
        return stamp
    row = db.one(
        "SELECT created_at FROM evidence WHERE manuscript_id = ? AND "
        "evidence_type = 'concept_triage' AND signal = 'rejected' AND "
        "(target = ? OR target LIKE ?) ORDER BY created_at DESC LIMIT 1",
        (mid, node["name"], f"{node['name']} (%"))
    return row["created_at"] if row else node["created_at"]


def revival_due(db: Database, manuscript: dict, node: dict,
                files: dict[str, str], *,
                ignore_proposals: set[str] | frozenset[str] = frozenset()
                ) -> dict | None:
    """§4.10, the door a retired name may come back through, or None.

    The ban stays; it yields only to insistence. The name must clear the
    recurrence bar in text written AFTER its retirement (paragraphs not in
    the version current at retirement). Then: never asked → ask, with the
    spans. Marked `retired_for_good` (a dismissal with a reason) → never.
    Asked and still open → not again. Dismissed without the mark → only
    once its recurrence count has doubled since that ask. A `superseded`
    ask was machine clean-up, not an answer, and does not count."""
    from .db import loads

    mid = manuscript["id"]
    meta = loads(node.get("metadata"), {}) or {}
    if meta.get("retired_for_good"):
        return None
    since = retired_at(db, mid, node)
    base = db.one(
        "SELECT files FROM manuscript_versions WHERE manuscript_id = ? AND "
        "created_at <= ? ORDER BY version_no DESC LIMIT 1", (mid, since))
    old = ({_norm_paragraph(p) for text in loads(base["files"], {}).values()
            for p in _paragraphs(text)} if base else set())
    fresh = {name: [p for p in _paragraphs(text)
                    if _norm_paragraph(p) not in old]
             for name, text in files.items()}
    names = node_names(node)
    admitted, stats = passes_recurrence_bar(
        {name: "\n\n".join(paras) for name, paras in fresh.items()}, names)
    if not admitted:
        return None
    prior = next((r for r in db.all(
        "SELECT * FROM knowledge_proposals WHERE manuscript_id = ? AND "
        "kind = 'revival' AND target = ? AND state != 'superseded' AND "
        "created_at >= ? ORDER BY created_at DESC",
        (mid, node["id"], since)) if r["id"] not in ignore_proposals), None)
    if prior is not None:
        if prior["state"] != "dismissed":
            return None
        then = ((loads(prior["payload"], {}) or {}).get("recurrence")
                or {}).get("contexts") or 1
        if stats["contexts"] < 2 * then:
            return None
    pats = [concept_pattern(n) for n in names if n]
    contexts = []
    for name, paras in fresh.items():
        for para in paras:
            if any(p.search(para) for p in pats):
                sentence = next((s for s in re.split(r"(?<=[.!?])\s+", para)
                                 if any(p.search(s) for p in pats)), para)
                contexts.append({"file": name, "span": sentence.strip()[:300]})
    return {"since": since, "recurrence": stats, "contexts": contexts[:5]}


def raise_revival(db: Database, manuscript: dict, candidate: dict,
                  node: dict, due: dict, run_id: str) -> dict | None:
    """The one revival proposal `revival_due` allows, with the spans it
    was found in and the recurrence count the next ask is measured
    against. Source `extraction/<run>` (§4.8)."""
    spans = [{"file": candidate["file"], "paragraph": n,
              "span": candidate["span"]} for n in list(candidate["_cited"])[:1]]
    return proposals.create(
        db, manuscript["id"], "revival", node["id"],
        {"name": node["name"], "kind": candidate["kind"],
         "notes": candidate.get("notes") or node.get("notes"),
         "spans": spans + due["contexts"], "recurrence": due["recurrence"]},
        source=f"extraction/{run_id}", firewall=False)


def reviver(db: Database, manuscript: dict, files: dict[str, str],
            run_id: str):
    """The `revive` callback a live run hands `screen`."""
    def revive(candidate: dict, node: dict) -> dict | None:
        due = revival_due(db, manuscript, node, files)
        return (raise_revival(db, manuscript, candidate, node, due, run_id)
                if due else None)
    return revive


# ------------------------------------- verdicts, ceilings, writes (§4.5, §4.8)

REACHES = ("new", "retype")      # the verdicts that reach the author


def decide(passed: list[dict], verdicts: dict, knob: dict) -> None:
    """Fold the adjudicator's verdicts into the candidates that passed the
    gates, in place: each gets its final `signal` (the verdict), `gate`
    "adjudicator" and the adjudicator's `reason`. Three verdicts are
    narrowed by rule, the reason saying so: `new` for a name already in
    the graph is `subsumed` (never a second node); `new` for a near-name
    is `subsumed` with an alias proposal (§4.7); a `retype` to a relation
    extraction may not propose is `not_proposable`."""
    proposable = set(knob["proposable_relations"])
    for c in passed:
        v = verdicts.get(id(c))
        if v is None:
            c.update(signal="drop", gate="adjudicator",
                     reason="not judged by the adjudicator")
            continue
        verdict, reason = v["verdict"], v["reason"]
        if c["type"] == "concept":
            if verdict == "new" and c.get("existing"):
                verdict = "subsumed"
                reason = (f"already in the graph as '{c['existing']}' — never "
                          f"a second node ({reason})")
            elif verdict == "new" and c.get("near"):
                verdict = "subsumed"
                c["alias_of"] = c["near"]
                reason = (f"a near-name of '{c['near']}' (§4.7): an alias "
                          f"proposal, never a node — {reason}")
            if verdict in ("improves", "subsumed"):
                c["target_name"] = (v["existing"] or c.get("existing")
                                    or c.get("near"))
            if verdict == "improves":
                c["improved_notes"] = v["notes"] or c.get("notes") or ""
        elif verdict == "retype":
            relation = v["relation"]
            if relation not in proposable:
                c.update(signal="not_proposable", gate="adjudicator",
                         reason=f"retyped to '{relation or '?'}', which "
                                f"extraction may not propose — {reason}")
                continue
            if relation == c["relation"] and not v["reverse"]:
                verdict = "new"
            else:
                c["retyped_from"] = candidate_label(c)
                c["relation"] = relation
                if v["reverse"]:
                    c["from"], c["to"] = c["to"], c["from"]
                    c["_ends"] = [c["_ends"][1], c["_ends"][0]]
        c.update(signal=verdict, gate="adjudicator", reason=reason)


def apply_ceilings(candidates: list[dict], knob: dict) -> None:
    """§4.5: at most `max_concepts_per_essay` new concepts and
    `max_links_per_essay` relations reach triage, ranked by score; the
    rest are `screened` (listed by `extract screened`). A relation whose
    new endpoint did not reach triage cannot be written and says why."""
    max_c = int(knob["max_concepts_per_essay"])
    max_l = int(knob["max_links_per_essay"])
    concepts = sorted((c for c in candidates if c["type"] == "concept"
                       and c.get("signal") == "new"),
                      key=lambda c: c["score"], reverse=True)
    for c in concepts[max_c:]:
        c.update(signal="screened", gate="ceiling",
                 reason=f"over the essay's ceiling of {max_c} concept(s), "
                        f"ranked by score")
    fresh: dict[str, dict] = {}
    for c in candidates:            # the survivor of a fold, not its double
        if c["type"] == "concept" and "existing" not in c:
            held = fresh.get(c["name"].lower())
            if held is None or held.get("signal") == "duplicate":
                fresh[c["name"].lower()] = c
    links = [c for c in candidates if c["type"] == "link"
             and c.get("signal") in REACHES]
    for c in links:
        for end in c["_ends"]:
            if not end[0].startswith("new:"):
                continue
            endpoint = fresh.get(end[0][4:])
            fate = endpoint.get("signal") if endpoint else None
            if fate == "screened":
                c.update(signal="screened", gate="ceiling",
                         reason=f"its endpoint '{end[2]}' was screened by "
                                "the ceiling")
                break
            if fate != "new":
                c.update(signal="unknown_endpoint", gate="adjudicator",
                         reason=f"its endpoint '{end[2]}' did not reach "
                                f"triage ({fate})")
                break
    links = sorted((c for c in links if c.get("signal") in REACHES),
                   key=lambda c: c["score"], reverse=True)
    for c in links[max_l:]:
        c.update(signal="screened", gate="ceiling",
                 reason=f"over the essay's ceiling of {max_l} relation(s), "
                        f"ranked by score")


def append_edge_evidence(db: Database, edge_id: str, entry: dict) -> None:
    """§4.8: `concept_edges.evidence` is the list of grounding spans, one
    per run that (re-)found the edge, and `support` its length."""
    from .db import loads

    row = db.one("SELECT evidence FROM concept_edges WHERE id = ?", (edge_id,))
    evidence = [e for e in (loads(row["evidence"], []) or [])
                if isinstance(e, dict)] if row else []
    if any(e.get("run") == entry["run"] for e in evidence):
        return
    evidence.append(entry)
    db.update("concept_edges", edge_id, {"evidence": json.dumps(evidence),
                                         "support": len(evidence)})


def _span_entry(c: dict, run_id: str) -> dict:
    return {"run": run_id, "file": c["file"], "section": c["section"],
            "paragraph": next(iter(c["_cited"]), None), "span": c["span"]}


def record_candidate(db: Database, mid: str, c: dict, run_id: str,
                     model: str | None) -> None:
    """One `extraction_adjudication` row per candidate verdict (§4.8), the
    reason in metadata. System provenance, `supports_belief` NULL:
    extraction never seeds a belief (ruling §0.3)."""
    meta = {"run": run_id, "file": c["file"], "section": c["section"],
            "unit": c["unit"], "type": c["type"], "gate": c.get("gate"),
            "reason": c.get("reason"), "score": c.get("score"),
            "confidence": c.get("confidence"), "why": c.get("why"),
            "span": c["span"],
            "adjudicator": model if c.get("gate") in ("adjudicator", "ceiling")
            else None}
    if c["type"] == "concept":
        meta.update(name=c["name"], kind=c["kind"], notes=c.get("notes"),
                    paragraphs=list(c["_cited"]))
    else:
        meta.update(**{"from": c["from"], "relation": c["relation"],
                       "to": c["to"]},
                    paragraph=next(iter(c["_cited"]), None))
    for key in ("node_id", "edge_id", "proposal_id", "existing", "near",
                "target_name", "retyped_from", "refound"):
        if c.get(key):
            meta[key] = c[key]
    row = ko_fields("ev")
    row.update(manuscript_id=mid, episode_id=None,
               evidence_type="extraction_adjudication", signal=c["signal"],
               target=candidate_label(c)[:200], supports_belief=None,
               weight="low", metadata=json.dumps(meta))
    db.insert("evidence", row)


def _apply_alias(db: Database, manuscript: dict, c: dict, run_id: str) -> str:
    """§4.7: the naming-ceremony rule, unchanged, with the sentence checked
    against the paragraph the candidate cites, not the whole payload."""
    mid = manuscript["id"]
    paragraph = next(iter(c["_cited"].values()), None)
    if paragraph is None or not span_in(c["sentence"], paragraph):
        return "ungrounded"
    c_node = get_concept(db, mid, c["canonical"])
    if not c_node or c_node["status"] == "retired":
        return "unknown_endpoint"
    a_node = get_concept(db, mid, c["alias"])
    if a_node is not None and a_node["id"] == c_node["id"]:
        return "duplicate"
    if a_node is not None and a_node["status"] == "retired":
        _record_alias_retired_refused(db, mid, a_node, c_node, c["sentence"])
        return "alias_retired_refused"
    if a_node is not None:
        _record_alias_fold_refused(db, mid, a_node, c_node, c["sentence"])
        return "alias_fold_refused"
    made = proposals.create(
        db, mid, "alias", c_node["id"],
        {"alias": c["alias"], "canonical": c_node["name"],
         "sentence": c["sentence"], "location": c["file"],
         "paragraph": next(iter(c["_cited"])), "alias_is_new": True},
        source=f"extraction/{run_id}")
    return "proposed" if made else "suppressed"


def write_essay(db: Database, manuscript: dict, candidates: list[dict], *,
                run_id: str, model: str | None, files: dict[str, str],
                judged: bool) -> dict:
    """The writes of one essay, in §4.8's shapes. New concepts are
    unconfirmed extracted nodes with `metadata.extraction` = {run, file,
    section, paragraphs, span, score, adjudicator, reason}; new relations
    are inferred edges whose `evidence` lists their spans and whose
    `support` counts them, with `metadata.extraction` = {score,
    adjudicator, reason}; `improves` is a `note_update` and a near-name an
    `alias` proposal, source `extraction/<run>`. Every concept and
    relation candidate leaves its evidence row. With `judged` False (the
    adjudicator could not be consulted) the candidates that passed the
    gates are left unwritten and unrecorded, to be asked again."""
    from .db import loads
    from .structure import ordered_items

    mid = manuscript["id"]
    system = db.source("system")
    ordered = ordered_items(files)
    out = {"nodes": [], "edges": [], "proposals": 0, "refound": 0,
           "aliases": {}}
    for c in candidates:
        if c["type"] != "concept" or c.get("signal") != "new":
            continue
        node = add_concept(db, mid, c["name"], kind=c["kind"],
                           notes=c.get("notes"), source_id=system)
        meta = {"origin": "extracted", "confirmed": False,
                "extraction": {"run": run_id, "file": c["file"],
                               "section": c["section"],
                               "paragraphs": list(c["_cited"]),
                               "span": c["span"], "score": c["score"],
                               "adjudicator": model, "reason": c["reason"]}}
        db.update("concept_nodes", node["id"],
                  {"metadata": json.dumps(meta),
                   "introduced_in": primary_location(ordered, node)})
        c["node_id"] = node["id"]
        out["nodes"].append(c["name"])
    for c in candidates:
        if c["type"] != "concept" or c.get("signal") not in ("improves",
                                                              "subsumed"):
            continue
        target = get_concept(db, mid, c.get("target_name") or "")
        if target is None or target["status"] == "retired":
            continue
        if c["signal"] == "subsumed" and c.get("alias_of"):
            made = proposals.create(
                db, mid, "alias", target["id"],
                {"alias": c["name"], "canonical": target["name"],
                 "sentence": c["span"], "location": c["file"],
                 "paragraph": next(iter(c["_cited"]), None),
                 "alias_is_new": True}, source=f"extraction/{run_id}")
            if made:
                c["proposal_id"] = made["id"]
                out["proposals"] += 1
            continue
        if c["signal"] != "improves":
            continue
        blocked = _note_update_blocked(target["notes"], c["improved_notes"])
        made = None if blocked else proposals.create(
            db, mid, "note_update", target["id"],
            {"name": target["name"], "current_note": target["notes"],
             "proposed_note": c["improved_notes"],
             "current_kind": target["kind"], "proposed_kind": target["kind"],
             "span": c["span"], "file": c["file"],
             "paragraphs": list(c["_cited"])}, source=f"extraction/{run_id}")
        if made:
            c["proposal_id"] = made["id"]
            out["proposals"] += 1
        else:
            c["signal"] = "subsumed"
            c["reason"] += ("; the materiality floor held it back "
                            f"({blocked})" if blocked else
                            "; an equivalent question is already on record")
    for c in candidates:
        if c["type"] != "link":
            continue
        if c.get("refound"):
            append_edge_evidence(db, c["refound"], _span_entry(c, run_id))
            c["edge_id"] = c["refound"]
            out["refound"] += 1
            continue
        if c.get("signal") not in REACHES:
            continue
        src = get_concept(db, mid, c["from"])
        dst = get_concept(db, mid, c["to"])
        if src is None or dst is None:
            c.update(signal="unknown_endpoint", gate="adjudicator",
                     reason="an endpoint is no longer in the graph")
            continue
        existing = db.one(
            "SELECT * FROM concept_edges WHERE manuscript_id = ? AND "
            "from_node = ? AND to_node = ? AND relation = ?",
            (mid, src["id"], dst["id"], c["relation"]))
        if existing is not None and existing["status"] == "rejected":
            c.update(signal="retired", reason="the author rejected this "
                     "relation" + (" (after the retype)" if
                                   c.get("retyped_from") else ""))
            continue
        if existing is not None and existing["status"] != "retired":
            append_edge_evidence(db, existing["id"], _span_entry(c, run_id))
            c.update(signal="duplicate", edge_id=existing["id"],
                     refound=existing["id"],
                     reason=f"re-found {candidate_label(c)} — its evidence "
                            "gains this span")
            out["refound"] += 1
            continue
        edge = link_concepts(db, mid, src["name"], c["relation"], dst["name"],
                             status="inferred", source_id=system)
        if edge["status"] == "retired":
            db.update("concept_edges", edge["id"], {"status": "inferred"})
        emeta = loads(edge.get("metadata"), {}) or {}
        emeta["extraction"] = {"score": c["score"], "adjudicator": model,
                               "reason": c["reason"]}
        db.update("concept_edges", edge["id"], {"metadata": json.dumps(emeta)})
        append_edge_evidence(db, edge["id"], _span_entry(c, run_id))
        c["edge_id"] = edge["id"]
        out["edges"].append(candidate_label(c))
    for c in candidates:
        if c["type"] == "alias":
            fate = _apply_alias(db, manuscript, c, run_id)
            out["aliases"][fate] = out["aliases"].get(fate, 0) + 1
            if fate == "proposed":
                out["proposals"] += 1
            continue
        if c.get("signal") is None and not judged:
            continue
        record_candidate(db, mid, c, run_id, model)
    out["revivals"] = sum(1 for c in candidates if c.get("signal") == "retired"
                          and c.get("proposal_id"))
    out["proposals"] += out["revivals"]
    return out


def run_essay(db: Database, manuscript: dict, payloads: list[ExtractionPayload],
              *, extract_llm, judge_llm, knob: dict, run_id: str,
              files: dict[str, str]) -> dict:
    """One essay, end to end: its section calls, the gates over the whole
    batch, one adjudicator call, the ceilings, the writes."""
    from . import extraction_adjudicator as adj

    replies = call_sections(extract_llm, payloads, knob)
    screening = screen_replies(db, manuscript, payloads, replies, knob,
                               files=files,
                               revive=reviver(db, manuscript, files, run_id))
    passed = list(screening.survivors)
    verdicts = (adj.adjudicate(judge_llm, payloads[0], passed,
                               knob["proposable_relations"])
                if passed else {})
    judged = verdicts is not None
    if judged:
        decide(passed, verdicts, knob)
        apply_ceilings(screening.candidates, knob)
    with db.transaction():
        written = write_essay(db, manuscript, screening.candidates,
                              run_id=run_id, model=judge_llm.model,
                              files=files, judged=judged)
    signals: dict[str, int] = {}
    for c in screening.candidates:
        if c["type"] != "alias" and c.get("signal"):
            signals[c["signal"]] = signals.get(c["signal"], 0) + 1
    return {"file": payloads[0].file, "sections": len(payloads),
            "answered": sum(1 for r in replies if r is not None),
            "candidates": sum(1 for c in screening.candidates
                              if c["type"] != "alias"),
            "passed": len(passed), "judged": judged, "skips": screening.skips,
            "signals": signals, "written": written,
            "screening": screening}


def spent(*clients) -> float | None:
    """Estimated dollars the clients' live calls have cost so far
    (`usage.estimate`, llmlm's prices); None when a model is unpriced."""
    from . import usage

    total = 0.0
    for client in clients:
        if not client.input_tokens and not client.output_tokens:
            continue
        value = usage.estimate(client.model, client.input_tokens,
                               client.output_tokens, client.cache_read_tokens,
                               client.cache_write_tokens)
        if value is None:
            return None
        total += value
    return total


def run(db: Database, manuscript: dict, essays: list[tuple[str, list]], *,
        extract_llm, judge_llm, knob: dict, run_id: str,
        advance_watermark: bool) -> dict:
    """A native run over `essays` [(file, payloads)], essay by essay in
    the order given. Stops before an essay once the estimated spend has
    passed `[extraction] run_cost_ceiling_usd`. The watermark advances
    only on an incremental or full run that completed (`advance_watermark`
    and nothing unanswered, unjudged or stopped). The run is recorded in
    `manuscripts.metadata.extraction.runs`."""
    from .db import now_iso

    started = now_iso()
    files = read_manuscript_files(Path(manuscript["path"]))
    ceiling = float(knob["run_cost_ceiling_usd"])
    reports: list[dict] = []
    stopped = None
    for i, (_name, payloads) in enumerate(essays):
        so_far = spent(extract_llm, judge_llm)
        if so_far is not None and so_far >= ceiling:
            stopped = (f"stopped before {essays[i][0]}: estimated spend "
                       f"${so_far:.2f} reached the ceiling ${ceiling:.2f}")
            break
        reports.append(run_essay(db, manuscript, payloads,
                                 extract_llm=extract_llm, judge_llm=judge_llm,
                                 knob=knob, run_id=run_id, files=files))
    latest = db.one("SELECT * FROM manuscript_versions WHERE manuscript_id = ? "
                    "ORDER BY version_no DESC LIMIT 1", (manuscript["id"],))
    realized = scan_realizations(db, manuscript["id"], dict(latest)) \
        if latest else []
    incomplete = bool(stopped) or any(
        r["answered"] < r["sections"] or not r["judged"] for r in reports)
    if advance_watermark and not incomplete and latest:
        _set_extraction_watermark(db, manuscript, latest["id"])
    total = spent(extract_llm, judge_llm)
    entry = {"id": run_id, "started": started, "kind": "extract",
             "model": extract_llm.model, "adjudicator": judge_llm.model,
             "units": sum(r["sections"] for r in reports),
             "cost": round(total, 4) if total is not None else None,
             "files": [r["file"] for r in reports],
             "reached": {
                 "concepts": sum(len(r["written"]["nodes"]) for r in reports),
                 "relations": sum(len(r["written"]["edges"]) for r in reports),
                 "proposals": sum(r["written"]["proposals"] for r in reports)},
             "incomplete": incomplete}
    record_run(db, manuscript, entry)
    return {"run": entry, "essays": reports, "stopped": stopped,
            "realized": len(realized)}


def render_run(report: dict) -> str:
    entry = report["run"]
    lines = []
    for r in report["essays"]:
        verdicts = ", ".join(f"{k} {v}" for k, v in sorted(r["signals"].items())
                             if k in ("new", "retype", "improves", "subsumed",
                                      "drop", "screened"))
        w = r["written"]
        lines.append(
            f"{r['file']}: {r['answered']}/{r['sections']} section call(s) "
            f"answered; {r['passed']} of {r['candidates']} candidate(s) "
            f"passed the gates"
            + ("" if r["judged"] else " — the adjudicator could not be "
               "consulted: nothing written for them")
            + (f"; adjudicator: {verdicts}" if verdicts else ""))
        lines.append(f"  reached triage: {len(w['nodes'])} concept(s), "
                     f"{len(w['edges'])} relation(s), {w['proposals']} "
                     f"proposal(s); {w['refound']} existing edge(s) re-found")
        skips = ", ".join(f"{k} {v}" for k, v in r["skips"].items() if v)
        if skips:
            lines.append(f"  gates: {skips}")
        aliases = ", ".join(f"{k} {v}" for k, v in sorted(w["aliases"].items()))
        if aliases:
            lines.append(f"  aliases: {aliases}")
    if report["stopped"]:
        lines.append(report["stopped"])
    money = (f"≈ ${entry['cost']:.2f}" if entry["cost"] is not None
             else "cost unknown (a model is unpriced)")
    lines.append(f"run {entry['id']}: {entry['units']} section call(s) on "
                 f"{entry['model']}, adjudicated by {entry['adjudicator']}, "
                 f"{money}" + (" — INCOMPLETE, the watermark stays"
                               if entry["incomplete"] else ""))
    return "\n".join(lines)


def screened(db: Database, manuscript: dict, file: str) -> list[dict]:
    """`extract screened <file>`: the candidates the per-essay ceiling held
    back, newest run first, best score first (§4.5)."""
    from .db import loads

    rows = []
    for r in db.all(
            "SELECT * FROM evidence WHERE manuscript_id = ? AND evidence_type "
            "= 'extraction_adjudication' AND signal = 'screened' "
            "ORDER BY created_at DESC", (manuscript["id"],)):
        meta = loads(r["metadata"], {}) or {}
        if meta.get("file") == file:
            rows.append({"run": meta.get("run"), "label": r["target"],
                         "score": meta.get("score"), "span": meta.get("span"),
                         "unit": meta.get("unit"), "reason": meta.get("reason"),
                         "created_at": r["created_at"]})
    runs_order = list(dict.fromkeys(r["run"] for r in rows))
    rows.sort(key=lambda r: (runs_order.index(r["run"]), -(r["score"] or 0)))
    return rows
