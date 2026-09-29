"""Morph — combined-call paragraph-defect detection and correction
(design: docs/morph-design.md).

Detection and correction are separate (design §2): `run` only ever
produces findings for the author to accept or decline in the Doc
(via the shared registration door, `findings.store_findings`) — it
never edits the manuscript directly. Every paragraph in scope is
checked with ONE combined LLM call per file, scoring four dimensions
at once against a fixed positional window (design §3, §4) — no
concept-mention narrowing gates which paragraph gets looked at
(design §5, empirically checked against `sweeps.ontology`).

The rubric — the reference standard for what each dimension means and
when a `judgment` vs. a `replacement` is safe — is an author-editable
prompt file, `_morph/paragraph-defects.md` per manuscript (design §8),
not fixed here in code.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .concepts import concept_pattern, node_names
from .db import Database, loads
from .findings import store_findings
from .passes import paragraphs_of
from .revisions import read_manuscript_files
from .sweeps import _claims_for

MORPH_DIR = "_morph"
MORPH_KIND = "morph"
MORPH_ORIGIN = "morph"
MORPH_EVIDENCE = "morph_edit"
WINDOW_RADIUS = 2
# Author-set operating threshold (design §3/§6, ratified 2026-09-27):
# below this, a finding still gets recorded but a drafted `replacement`
# is downgraded to `judgment`-only, regardless of what the model wrote.
# Not a claim that 0.8 is calibrated truth — confidence is uncalibrated
# telemetry (design §9's own point); this just gates which findings are
# trusted enough to auto-propose text. Revisit once real manuscript
# runs (not just the synthetic pilot) give evidence either way.
CONFIDENCE_FLOOR = 0.8
DIMENSIONS = ("claim_before_evidence", "discontinuity_with_prev",
             "specificity", "concept_invalidation")
# Maps each JSON key to the rubric's own "### Flag — …" heading text
# (morph-rubric.md), so a stored finding's `rule` field matches
# `flag_rules(rubric_body)` and `rule_known` comes out true.
RULE_NAMES = {
    "claim_before_evidence": "Claim Before Evidence",
    "discontinuity_with_prev": "Discontinuity",
    "specificity": "Specificity",
    "concept_invalidation": "Concept Invalidation",
}
DEFAULT_RUBRIC = "paragraph-defects"


class NoRubric(LookupError):
    """No `_morph/<name>.md` file exists yet for this manuscript."""


def rubric_path(manuscript: dict, name: str = DEFAULT_RUBRIC) -> Path:
    return Path(manuscript["path"]) / MORPH_DIR / f"{name}.md"


def load_rubric(manuscript: dict, name: str = DEFAULT_RUBRIC) -> str:
    path = rubric_path(manuscript, name)
    if not path.exists():
        raise NoRubric(
            f"no morph rubric at {path} — copy docs/morph-rubric.md there "
            "(or 'morph add' once that verb exists) before 'morph run'")
    return path.read_text(encoding="utf-8")


SCHEMA_INSTRUCTIONS = (
    'Reply with ONLY a JSON object, no markdown fences. Top-level keys '
    'spelled EXACTLY "P1", "P2", … matching the paragraph numbers given '
    "below (never bare numbers, never any other spelling). Each holds "
    "exactly these four keys, spelled exactly this way — "
    '"claim_before_evidence", "discontinuity_with_prev", "specificity", '
    '"concept_invalidation" — each a '
    '{"present": true|false, "confidence": 0.0-1.0, "judgment": "...", '
    '"replacement": "..."} object (judgment/replacement omitted or empty '
    'when not applicable; at most one of the two present; neither when '
    '"present" is false). Do not rename, abbreviate, or add keys.'
)


_NON_PROSE = re.compile(
    r"^(?:#{1,6}\s|\[\^[^\]]+\]:|\[(?:Illustration|Footnote|Explain|"
    r"Judgment|Voice):)")


def _is_prose(unit: str) -> bool:
    """False for a heading, a footnote definition, or a directive/tag
    line — none of the four dimensions mean anything applied to
    structural markup rather than prose (found live, 2026-09-26: an
    early real-file run flagged bare section headings for
    'discontinuity' before this guard existed)."""
    if _NON_PROSE.match(unit.strip()):
        return False
    # Verse is not prose: a stanza or a thesis line is judged by the
    # verse lenses, never for claim-before-evidence or specificity — and
    # a `replacement` staged against a stanza would be a poem rewritten
    # unasked (author, 2026-09-27: "run morph on title and prologue";
    # verse-and-cast-design.md §2, §10 change 4).
    from .verse import is_verse_unit
    return not is_verse_unit(unit)


def _window(n_units: int, i: int, radius: int = WINDOW_RADIUS) -> list[int]:
    lo = max(1, i - radius)
    hi = min(n_units, i + radius)
    return list(range(lo, hi + 1))


def targets(db: Database, manuscript: dict, relpath: str,
           files: dict[str, str], *, full: bool = False) -> list[int] | None:
    """Which 1-based paragraph indices get checked this run.

    `full` forces every paragraph in the file — the on-demand override
    for a first pass or a file with no collected history (mirroring
    the escape hatch `sweeps.ontology` already needed for the same
    case, design §5). Otherwise: only paragraphs whose text differs
    from the same position in the last collected version. Returns
    `None` (never an empty list) when nothing changed and `full` is
    False, so a caller can report "nothing changed" distinctly from
    "checked everything, found nothing."."""
    units = paragraphs_of(files[relpath])
    if full:
        return [i for i, u in enumerate(units, 1) if _is_prose(u)] or None
    rows = db.all(
        "SELECT * FROM manuscript_versions WHERE manuscript_id = ? "
        "ORDER BY version_no DESC LIMIT 2", (manuscript["id"],))
    if len(rows) < 2:
        return None
    new_files = loads(rows[0]["files"], {})
    old_files = loads(rows[1]["files"], {})
    if relpath not in new_files:
        return None
    old_units = paragraphs_of(old_files.get(relpath, ""))
    changed = [i for i, u in enumerate(units, 1)
              if _is_prose(u)
              and (i > len(old_units) or old_units[i - 1] != u)]
    return changed or None


def _concept_claims(db: Database, mid: str, nodes: list[dict],
                    nodes_by_id: dict, claims_by_node: dict,
                    text: str) -> list[str]:
    claims: list[str] = []
    for node in nodes:
        if any(concept_pattern(nm).search(text) for nm in node_names(node)):
            claims.extend(claims_by_node[node["id"]])
    return sorted(set(claims))


def assemble(db: Database, manuscript: dict, relpath: str,
            files: dict[str, str], paragraph_ns: list[int]) -> str:
    """The user-message payload: one block per target paragraph, each
    with its fixed positional window (design §4) and the concept
    claims its window mentions — built the same way `sweeps.ontology`
    resolves claims (`_claims_for`), just windowed instead of
    whole-paragraph, and never used to decide which paragraphs are
    included (design §5) — only to enrich the ones already chosen."""
    mid = manuscript["id"]
    units = paragraphs_of(files[relpath])
    nodes = [dict(n) for n in db.all(
        "SELECT * FROM concept_nodes WHERE manuscript_id = ? "
        "AND status != 'retired'", (mid,))]
    nodes_by_id = {n["id"]: n for n in nodes}
    claims_by_node = {n["id"]: _claims_for(db, mid, n, nodes_by_id)
                      for n in nodes}
    blocks = []
    for i in paragraph_ns:
        window_ns = _window(len(units), i)
        window_text = "\n".join(f"P{k}: {units[k - 1]}" for k in window_ns)
        claims = _concept_claims(db, mid, nodes, nodes_by_id, claims_by_node,
                                 "\n".join(units[k - 1] for k in window_ns))
        block = f"=== Judging P{i} ===\nContext window:\n{window_text}\n"
        if claims:
            block += "Ratified concept claims for this window:\n" \
                + "\n".join(f"- {c}" for c in claims) + "\n"
        blocks.append(block)
    return "\n".join(blocks) + "\n\n" + SCHEMA_INSTRUCTIONS


def _parse_reply(raw: str) -> dict:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
    try:
        data = json.loads(text)
    except json.JSONDecodeError as err:
        raise ValueError(f"morph reply was not valid JSON: {err}") from err
    return data if isinstance(data, dict) else {}


def _findings_from_reply(data: dict, paragraph_ns: list[int],
                         units: list[str]) -> list[dict]:
    """One `store_findings`-shaped finding per (paragraph, flagged
    dimension), `quote` set to the paragraph's own verbatim text so the
    hygiene gate anchors correctly regardless of what the model quoted
    back."""
    out = []
    for i in paragraph_ns:
        entry = data.get(f"P{i}", {}) or {}
        quote = units[i - 1]
        for dim in DIMENSIONS:
            d = entry.get(dim, {}) or {}
            if not d.get("present"):
                continue
            f: dict = {"quote": quote, "rule": RULE_NAMES[dim],
                      "note": f"[{dim}, confidence "
                              f"{d.get('confidence', '?')}] "
                              + str(d.get("judgment") or d.get("replacement")
                                    or "")}
            confident = (d.get("confidence") or 0) >= CONFIDENCE_FLOOR
            if d.get("replacement") and confident:
                f["replacement"] = d["replacement"]
            elif d.get("judgment"):
                f["judgment"] = d["judgment"]
            elif d.get("replacement"):
                # A drafted replacement exists but confidence is below
                # the floor (design §3/§6): downgrade to judgment-only
                # rather than discard it — the draft is still useful
                # context for the author, it just isn't trusted enough
                # to auto-stage as text.
                f["judgment"] = (f"below the {CONFIDENCE_FLOOR:.0%} "
                                 "confidence floor for auto-staging; "
                                 f"drafted but unstaged: {d['replacement']}")
            else:
                # present with neither shape given — treat as a bare
                # judgment naming the dimension, never silently drop a
                # real finding for a malformed reply.
                f["judgment"] = f"flagged {dim.replace('_', ' ')}, no gist given"
            out.append(f)
    return out


def run(db: Database, manuscript: dict, session: dict, llm, relpath: str, *,
       full: bool = False, rubric_name: str = DEFAULT_RUBRIC) -> dict:
    """Detect, and stage any safe corrections for, the paragraphs in
    scope for `relpath`. Returns the same shape `findings.store_findings`
    does, plus `scope`/`checked` describing what was looked at."""
    files = read_manuscript_files(Path(manuscript["path"]))
    if relpath not in files:
        raise LookupError(f"'{relpath}' is not a manuscript file")
    paragraph_ns = targets(db, manuscript, relpath, files, full=full)
    if not paragraph_ns:
        return {"scope": relpath, "checked": 0, "findings": [],
                "note": "nothing changed since the previous version — "
                        "pass full=True to audit it anyway"}
    rubric_body = load_rubric(manuscript, rubric_name)
    user = assemble(db, manuscript, relpath, files, paragraph_ns)
    reply = llm.complete(rubric_body, user, thinking_budget=0)
    data = _parse_reply(reply)
    units = paragraphs_of(files[relpath])
    findings = _findings_from_reply(data, paragraph_ns, units)
    result = store_findings(
        db, manuscript, session,
        kind=MORPH_KIND, producer=rubric_name, relpath=relpath,
        file_text=files[relpath], findings=findings, source="native",
        batch_prefix="mb", origin_type=MORPH_ORIGIN, verb_stem="morph",
        producer_key="producer", rubric_body=rubric_body)
    result["scope"] = relpath
    result["checked"] = len(paragraph_ns)
    return result
