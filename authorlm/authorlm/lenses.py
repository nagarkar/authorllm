"""Lenses — prompt-first sweeps (design: docs/sweep-framework.md).

A lens is an author-ratified editorial concern stored as a markdown
prompt in `_lenses/<name>.md` (observation-invisible, author-editable,
like profiles). Its reference standard lives ONLY in that prompt — that
is what distinguishes it from an auditor, whose reference is maintained
in AuthorLM's stores.

Two ways findings arrive, one store, one review loop:
- `run` — AuthorLM executes the lens natively on the configured (cheap)
  model with compact DB context.
- `register` — the registration door: an external agent (a Claude
  subagent doing world-knowledge or deep-judgment work, e.g. tether)
  produces the findings and submits them here.
Both paths pass the same hygiene gate (verbatim quote in the file) and
store findings as guidance_history rows (kind='lens', own batch), so
verdicts flow through record_review — evidence and belief learning
unchanged. Review: `authorlm lens review <n> --accept|--reject|--modify`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .concepts import concept_pattern, node_names
from .db import Database, ko_fields, loads, new_id
from .revisions import read_manuscript_files

LENS_DIR = "_lenses"
LENS_KIND = "lens"
LENS_ORIGIN = "lens"
_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")

LENS_SYSTEM = """\
You review one file of a philosophy manuscript through the author's own
editorial lens, stated below. Report only findings that the lens's
concern actually covers; if nothing qualifies, report nothing. Each
finding must quote the passage it is about VERBATIM. Reply with JSON
only:
{"findings": [{"quote": "<verbatim sentence(s) from the file>",
"note": "<the finding, one or two sentences>"}]}

THE LENS (ratified by the author):
"""


def _lens_path(manuscript: dict, name: str) -> Path:
    return Path(manuscript["path"]) / LENS_DIR / f"{name}.md"


def add_lens(manuscript: dict, name: str, prompt: str) -> Path:
    if not _NAME.match(name):
        raise ValueError("lens names are short kebab-case slugs, e.g. "
                         "'kantian-objections'")
    if not prompt.strip():
        raise ValueError("a lens is its prompt — pass the prompt on stdin")
    path = _lens_path(manuscript, name)
    path.parent.mkdir(exist_ok=True)
    path.write_text(prompt.strip() + "\n", encoding="utf-8")
    return path


def list_lenses(manuscript: dict) -> list[dict]:
    directory = Path(manuscript["path"]) / LENS_DIR
    if not directory.is_dir():
        return []
    out = []
    for path in sorted(directory.glob("*.md")):
        first = path.read_text(encoding="utf-8").strip().split("\n", 1)[0]
        out.append({"name": path.stem, "summary": first[:100]})
    return out


RESERVED_MARKERS = ("<<", ">>", "{{", "}}")


def _edit_entry(units: list[str], raw_quote: str, replacement: str,
                note: str, taken_units: set[int]) -> tuple[dict | None, str]:
    """Anchor one finding's `replacement` to its unit, or refuse.

    The door's law (filter-pass design §7.2), applied literally: the
    quote must occur VERBATIM in exactly one unit and exactly once
    within it — an unanchored replace picks an occurrence, and picking
    is guessing. Returns (entry, "") on success or (None, reason)."""
    hits = [i for i, u in enumerate(units, 1) if raw_quote in u]
    if not hits:
        return None, ("quote matches the file only after whitespace "
                      "normalization — not verbatim at the byte level, "
                      "so no surgical edit can anchor to it")
    if len(hits) > 1:
        return None, (f"quote occurs in {len(hits)} units — an edit "
                      f"cannot anchor to an ambiguous quote")
    n = hits[0]
    unit = units[n - 1]
    if unit.count(raw_quote) > 1:
        return None, ("quote occurs more than once within its unit — "
                      "an edit cannot anchor to an ambiguous quote")
    if n in taken_units:
        return None, ("its unit already carries a staged edit from "
                      "this batch")
    if any(m in replacement for m in RESERVED_MARKERS):
        return None, "replacement carries reserved markers (<< >> {{ }})"
    new = unit.replace(raw_quote, replacement)
    if not new.strip():
        return None, "replacement would leave the unit empty"
    if new == unit:
        return None, "replacement is identical to the quoted text"
    return {"n": n, "new": new, "why": note}, ""


def _store_findings(db: Database, manuscript: dict, session: dict,
                    lens_name: str, relpath: str, file_text: str,
                    findings: list, source: str) -> dict:
    """The registration door's core: hygiene-gate each finding (verbatim
    quote in the file) and store the survivors as a fresh lens batch.

    A finding may carry an optional `replacement` (filter-pass design
    §7.2, built 2026-08-31): the quoted text's proposed substitute
    within its unit. When it anchors cleanly, the harness builds `new`
    from the unit itself and stages a `doc_threads` row with
    `origin_type='lens'` through the same door the filters use — from
    there `lens push` / `lens resolve` give it the filter road's
    user-side workflow. An ambiguous or unanchorable replacement is
    REFUSED (reason reported per finding); the finding itself is still
    stored. Ruling on the finding and settling the edit stay
    independent verdicts."""
    from . import passes
    from . import staging

    flat = " ".join(file_text.split()).lower()
    units = passes.paragraphs_of(file_text)
    batch_id = new_id("lb")
    stored, dropped = [], 0
    entries: list[tuple[dict, dict]] = []   # (entry, finding-row) pairs
    refused: list[dict] = []
    taken_units: set[int] = set()
    index = 0
    for f in findings:
        if not isinstance(f, dict):
            dropped += 1
            continue
        raw_quote = str(f.get("quote", ""))
        quote = " ".join(raw_quote.split())
        note = str(f.get("note", "")).strip()
        if not quote or not note or quote.lower() not in flat:
            dropped += 1
            continue
        index += 1
        row = ko_fields("gd")
        row.update(
            manuscript_id=manuscript["id"], session_id=session["id"],
            intent_id=None, batch_id=batch_id, batch_index=index,
            kind=LENS_KIND,
            suggestion=f"[{lens_name}] {note}",
            explanation=f"On “{quote}” ({relpath}): {note}",
            state="proposed",
        )
        meta = {
            "lens": lens_name, "file": relpath, "quote": quote,
            "source": source,
            "dedupe_key": f"lens:{lens_name}:{relpath}:{index}",
        }
        if "replacement" in f:
            entry, reason = _edit_entry(units, raw_quote,
                                        str(f.get("replacement") or ""),
                                        note, taken_units)
            if entry is None:
                meta["edit_refused"] = reason
                refused.append({"quote": quote[:80], "reason": reason})
            else:
                taken_units.add(entry["n"])
                entries.append((entry, row))
        row["metadata"] = json.dumps(meta)
        db.insert("guidance_history", row)
        stored.append(row)
    edits = []
    if entries:
        staged = staging.stage_edits(
            db, manuscript["id"], lens_name, relpath, file_text,
            [e for e, _ in entries], origin_type=LENS_ORIGIN,
            verb_stem="lens")
        by_n = {loads(t["metadata"], {}).get("anchor_paragraph"): t
                for t in staged}
        for entry, frow in entries:
            thread = by_n.get(entry["n"])
            if thread is None:
                continue
            fmeta = json.loads(frow["metadata"])
            fmeta["edit_thread"] = thread["id"]
            db.update("guidance_history", frow["id"],
                      {"metadata": json.dumps(fmeta)})
            frow["metadata"] = json.dumps(fmeta)
            edits.append(thread)
    return {"lens": lens_name, "file": relpath, "batch_id": batch_id,
            "findings": [dict(r) for r in stored],
            "dropped_ungrounded": dropped,
            "edits_staged": edits, "edits_refused": refused}


def run_lens(db: Database, manuscript: dict, session: dict, name: str,
             relpath: str, llm) -> dict:
    """Native execution on the configured model: lens prompt + file text +
    compact concept notes for the concepts the file mentions."""
    path = _lens_path(manuscript, name)
    if not path.exists():
        known = ", ".join(l["name"] for l in list_lenses(manuscript)) or "none"
        raise LookupError(f"no lens '{name}' (defined: {known})")
    from .structure import refuse_sidecar

    # §2.5 row 15 — the one site that accepts toc.toml today. A lens
    # reads an essay whole and reports findings about the prose; the
    # dictionary is a table of the author's rulings, and there is
    # nothing in it for a lens to find.
    refuse_sidecar(Path(relpath).name)
    files = read_manuscript_files(Path(manuscript["path"]))
    if relpath not in files:
        raise LookupError(f"'{relpath}' is not a manuscript file")
    text = files[relpath]

    notes = []
    for node in db.all(
        "SELECT * FROM concept_nodes WHERE manuscript_id = ? "
        "AND status != 'retired' AND notes IS NOT NULL", (manuscript["id"],),
    ):
        if any(concept_pattern(nm).search(text) for nm in node_names(node)):
            notes.append(f"- {node['name']}: {node['notes']}")
    context = ("\n\nRATIFIED CONCEPT NOTES (the author's settled "
               "definitions):\n" + "\n".join(notes[:40]) if notes else "")

    system = LENS_SYSTEM + path.read_text(encoding="utf-8") + context
    result = llm.complete_json(system, text)
    findings = (result or {}).get("findings", []) \
        if isinstance(result, dict) else []
    return _store_findings(db, manuscript, session, name, relpath, text,
                           findings, source="native")


def register_findings(db: Database, manuscript: dict, session: dict,
                      name: str, relpath: str, findings: list) -> dict:
    """The registration door for externally produced findings (Claude
    subagent lenses: tether, deep philosophical passes). Same hygiene,
    same store, same review loop — one evidence stream, no forks."""
    from .structure import refuse_sidecar

    refuse_sidecar(Path(relpath).name)      # the other door, same refusal
    files = read_manuscript_files(Path(manuscript["path"]))
    if relpath not in files:
        raise LookupError(f"'{relpath}' is not a manuscript file")
    return _store_findings(db, manuscript, session, name, relpath,
                           files[relpath], findings, source="external")
