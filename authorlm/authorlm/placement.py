"""Illustration placement pipeline — spot-finding, staging, triage
(docs/illustration-placement-design.md, grilled and ratified 2026-08-10).

The spot-finder is a cheap-model lens: it reads a chapter plus the
effective `illustration-placement` law (criteria, pacing, scope
boundaries) and proposes placements — or revisions of existing tag
descriptions — into a STAGING TABLE, never into text. Nothing touches
the manuscript or the Doc until the author accepts at triage. On
acceptance the tag lands in the local file after its anchor paragraph;
the normal collect → push → render machinery takes over.

Triage verdicts are the illustration learning loop's evidence channel:
accepted / modified (original → final diff, the richest signal) /
rejected (reason verbatim), feeding the same scoped, decline-by-default
distiller as margin threads.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import prompt_registry
from .db import Database, ko_fields, loads
from .llm import LLMClient

# File-backed (prompt_registry.py "illus-placement" / "illus-arbiter") so
# the author can read and edit them without a code change, same as
# extraction.md / adjudication.md — see prompt_registry.py's module
# docstring.
_PLACEMENT_PROMPT_PATH = Path(__file__).parent / "prompts" / "illus-placement.md"
_ARBITER_PROMPT_PATH = Path(__file__).parent / "prompts" / "illus-arbiter.md"


def placement_system() -> str:
    return _PLACEMENT_PROMPT_PATH.read_text(encoding="utf-8")


def arbiter_system() -> str:
    return _ARBITER_PROMPT_PATH.read_text(encoding="utf-8")


PLACEMENT_SYSTEM = placement_system()
ARBITER_SYSTEM = arbiter_system()

# Deterministic pacing ceiling: the ratified flex limit is 2 per 1,500
# words, i.e. one per PACE_WORDS. Arithmetic, not model judgment.
PACE_WORDS = 750

_TAG = re.compile(r"^\[Illustration:\s*(?P<prompt>[^\]|]+?)\s*(\|.*)?\]\s*$")


def placement_law(db: Database, manuscript_id: str, file: str) -> str:
    """The effective illustration-placement law for a file — the exact
    text the spot-finder sees. Only the 'illustration-placement' aspect:
    render law ('illustration') and prose-figure law never reach it."""
    from .styles import effective_style

    elements = [e for e in effective_style(db, manuscript_id, file)
                if e["aspect"] == "illustration-placement"]
    if not elements:
        return ""
    lines = ["PLACEMENT LAW (ratified by the author — follow strictly):"]
    lines += [f"- {e['statement']}" for e in elements]
    return "\n".join(lines)


def _existing_tags(text: str) -> list[str]:
    return [m.group("prompt").strip() for line in text.split("\n")
            if (m := _TAG.match(line.strip()))]


def open_proposals(db: Database, manuscript_id: str,
                   file: str | None = None) -> list[dict]:
    rows = [dict(r) for r in db.all(
        "SELECT * FROM illus_proposals WHERE manuscript_id = ? "
        "AND state = 'proposed' ORDER BY created_at, id",
        (manuscript_id,))]
    return [r for r in rows if file is None or r["file"] == file]


def sweep_stale(db: Database, manuscript: dict) -> list[dict]:
    """Proposals whose anchor no longer exists verbatim go stale rather
    than guessing (the margin exactness rule, applied here)."""
    from .revisions import read_manuscript_files

    files = read_manuscript_files(Path(manuscript["path"]))
    staled = []
    for row in open_proposals(db, manuscript["id"]):
        text = files.get(row["file"], "")
        if row["anchor"] not in text or (
                row["revises"] and row["revises"] not in _existing_tags(text)):
            db.update("illus_proposals", row["id"], {"state": "stale"})
            staled.append(row)
    return staled


def scan(db: Database, manuscript: dict, llm: LLMClient,
         files: list[str] | None = None) -> dict:
    """Run the spot-finder over main-matter chapters (one call per
    chapter), staging validated proposals. Deterministic guards do the
    enforcement the law states absolutely: front matter never gets
    slots; files marked `illustrations = "none"` in toc.toml are never
    scanned; each chapter's NEW placements are budget-capped by word
    count (PACE_WORDS). Anchors are validated verbatim — an
    unverifiable proposal is dropped and counted, never guessed at.
    Ends with the arbitration pass (cross-chapter dedup)."""
    from .illus import illustration_law
    from .revisions import read_manuscript_files
    from .structure import (TOC_FILENAME, is_structural, matter_map,
                            reading_order, toc_attrs)

    if not llm or not getattr(llm, "enabled", False):
        raise RuntimeError("the spot-finder needs an LLM (config [llm])")
    mid = manuscript["id"]
    disk = read_manuscript_files(Path(manuscript["path"]))
    matter = matter_map(disk)
    attrs = toc_attrs(disk.get(TOC_FILENAME) or "")
    order, _ = reading_order(disk)
    targets = [n for n in order
               if not is_structural(n)
               and matter.get(n, "main") == "main"
               and attrs.get(n, {}).get("illustrations") != "none"
               and (files is None or n in files)]
    sweep_stale(db, manuscript)
    # Dedupe against EVERY prior proposal, any state: what the author
    # rejected must not resurrect on the next scan.
    already = {(r["file"], r["anchor"], r["description"])
               for r in db.all(
                   "SELECT file, anchor, description FROM illus_proposals "
                   "WHERE manuscript_id = ?", (mid,))}
    staged, dropped, calls, skipped_budget = [], 0, 0, []
    for name in targets:
        text = disk[name]
        tags = _existing_tags(text)
        budget = max(1, len(text.split()) // PACE_WORDS) - len(tags)
        open_here = len([r for r in open_proposals(db, mid, file=name)
                         if not r["revises"]])
        budget -= open_here
        if budget <= 0:
            skipped_budget.append(name)
            continue
        law = placement_law(db, mid, name)
        img_law = illustration_law(db, mid, name)
        from .illus import craft_text

        craft = craft_text(getattr(llm, "config", {}) or {})
        user = (f"{law}\n\n"
                + (f"IMAGE LAW (descriptions must be renderable under "
                   f"it):\n{img_law}\n\n" if img_law else "")
                + f"DESCRIPTION CRAFT:\n{craft}\n\n"
                + f"CHAPTER: {name} ({len(text.split())} words)\n"
                f"BUDGET: at most {budget} new placement(s)\n"
                f"EXISTING ILLUSTRATIONS ({len(tags)}): "
                + ("; ".join(tags) if tags else "none")
                + f"\n\nTEXT:\n{text}")
        reply = llm.complete_json(PLACEMENT_SYSTEM, user)
        calls += 1
        staged_here = 0
        for item in (reply or {}).get("proposals", []) or []:
            anchor = str(item.get("anchor") or "").strip()
            desc = str(item.get("description") or "").strip()
            revises = (str(item["revises"]).strip()
                       if item.get("revises") else None)
            if not anchor or not desc or anchor not in text or (
                    revises and revises not in tags):
                dropped += 1
                continue
            if not revises and staged_here >= budget:
                dropped += 1  # past the deterministic budget — discarded
                continue
            key = (name, anchor, desc)
            if key in already:
                continue
            already.add(key)
            row = ko_fields("ip")
            row.update(manuscript_id=mid, file=name, anchor=anchor,
                       description=desc,
                       criterion=str(item.get("criterion") or "").strip(),
                       rationale=str(item.get("rationale") or "").strip(),
                       revises=revises, state="proposed")
            db.insert("illus_proposals", row)
            staged.append(row)
            if not revises:
                staged_here += 1
    report = {"files": targets, "calls": calls, "staged": staged,
              "dropped_unverifiable": dropped,
              "skipped_at_budget": skipped_budget}
    if staged:
        report["arbitration"] = arbitrate(db, manuscript, llm)
    report["prompt_files"] = _prompt_locations(
        calls > 0, bool((report.get("arbitration") or {}).get(
            "arbiter_called")))
    return report


def _prompt_locations(placement_called: bool, arbiter_called: bool) -> list[str]:
    """`Prompt.location` (prompt_registry.py) of every prompt THIS scan
    actually invoked — mirrors extraction.py's `_prompt_locations`:
    derived from the registry, never a hardcoded filename, and reflects
    what ran (not what merely could have run). `placement_called` is True
    whenever at least one spot-finder call was made (`calls > 0`);
    `arbiter_called` is True only when the arbitration pass actually
    reached the model (see `arbitrate`'s `arbiter_called` flag) — with
    fewer than two staged proposals, or no LLM, it never calls out."""
    names = []
    if placement_called:
        names.append("illus-placement")
    if arbiter_called:
        names.append("illus-arbiter")
    return [prompt_registry.by_name(name).location for name in names]


def arbitrate(db: Database, manuscript: dict, llm: LLMClient) -> dict:
    """The cross-chapter pass the per-chapter scans cannot do: one
    cheap-model call over ALL staged proposals, cutting duplicate homes
    for the same metaphor/diagram (the law's 'concretized once').
    Deterministic pre-pass: proposals on files toc.toml excludes are
    cut by guard, no model involved. Arbiter cuts carry their reason in
    metadata — they are pipeline hygiene, never author evidence."""
    from .revisions import read_manuscript_files
    from .structure import TOC_FILENAME, toc_attrs

    mid = manuscript["id"]
    disk = read_manuscript_files(Path(manuscript["path"]))
    attrs = toc_attrs(disk.get(TOC_FILENAME) or "")
    guard_cut = []
    for row in open_proposals(db, mid):
        if attrs.get(row["file"], {}).get("illustrations") == "none":
            db.update("illus_proposals", row["id"],
                      {"state": "rejected", "metadata": json.dumps(
                          {"by": "guard", "reason":
                           "file excluded (toc.toml illustrations = "
                           "\"none\")"})})
            guard_cut.append(row["id"])
    rows = open_proposals(db, mid)
    if len(rows) < 2 or not llm or not getattr(llm, "enabled", False):
        return {"guard_cut": guard_cut, "arbiter_cut": [],
                "open": len(rows), "arbiter_called": False}
    law = placement_law(db, mid, rows[0]["file"])
    listing = "\n".join(
        f"- id {r['id']} | {r['file']} | criterion {r['criterion']} | "
        f"[{r['description']}] | after «{r['anchor'][:60]}»"
        for r in rows)
    reply = llm.complete_json(
        ARBITER_SYSTEM, f"{law}\n\nSTAGED PROPOSALS:\n{listing}")
    valid = {r["id"] for r in rows}
    cut = []
    for item in (reply or {}).get("cut", []) or []:
        pid = str(item.get("id") or "").strip()
        if pid not in valid:
            continue
        db.update("illus_proposals", pid,
                  {"state": "rejected", "metadata": json.dumps(
                      {"by": "arbiter",
                       "reason": str(item.get("reason") or "")[:200]})})
        cut.append({"id": pid, "reason": item.get("reason")})
    return {"guard_cut": guard_cut, "arbiter_cut": cut,
            "open": len(open_proposals(db, mid)), "arbiter_called": True}


def _record_evidence(db: Database, manuscript: dict, row: dict,
                     signal: str, explanation: str | None,
                     llm=None) -> None:
    """Triage verdicts are the illustration learning loop's evidence.
    Explanations (revision diffs, rejection reasons) feed the scoped,
    decline-by-default distiller — never a rule by force of habit."""
    target = f"{row['file']}: illustration after «{row['anchor'][:80]}»"
    if signal == "modified":
        original = (loads(row.get("metadata"), {}) or {}).get(
            "original_description", "")
        target += (f" — proposal «{original}» became "
                   f"«{row['description']}»")
    ev = ko_fields("ev")
    ev.update(manuscript_id=manuscript["id"], episode_id=None,
              evidence_type="illus_triage", signal=signal, target=target,
              supports_belief=None, weight="high")
    if explanation:
        ev["metadata"] = json.dumps({"explanation": explanation,
                                     "file": row["file"]})
    db.insert("evidence", ev)
    if explanation and llm is not None:
        # Never seed per verdict — that bypassed the ">= 2" guardrail and
        # produced one near-duplicate belief per rejection. The batch rule
        # now lives in loop.distil_pending, which holds on every surface.
        from . import loop

        loop.distil_pending(db, manuscript, loop.spec_for("illustrations"), llm)


def distill_batch(db: Database, manuscript: dict,
                  items: list[tuple[str, str]], llm) -> dict | None:
    """One distiller call for a whole triage batch — evidence is
    recorded per verdict instantly (decide with llm=None), and the
    pattern-hunting happens here, once, where the decline-by-default
    guardrail can actually see whether a pattern spans two instances.
    items: (file, explanation) pairs from this batch."""
    if not items or not llm or not getattr(llm, "enabled", False):
        return None
    from collections import Counter

    from .beliefs import seed_margin_candidate
    from .styles import guide_chain

    combined = ("Illustration-triage verdicts from one sitting "
                "(a candidate needs a pattern across at least two):\n"
                + "\n".join(f"- [{f}] {e}" for f, e in items))
    file = Counter(f for f, _ in items).most_common(1)[0][0]
    return seed_margin_candidate(db, manuscript["id"], combined, file,
                                 guide_chain(db, manuscript["id"], file),
                                 llm)


def decide(db: Database, manuscript: dict, proposal_id: str, verdict: str,
           revised_description: str | None = None,
           reason: str | None = None, llm=None) -> dict:
    """Author's triage verdict on one staged proposal. accept writes the
    tag into the local file after its anchor paragraph (or rewrites the
    existing tag for a revision proposal); reject records the reason
    verbatim. A revised description is a modified acceptance — the
    original stays in metadata, the diff is evidence."""
    row = db.one("SELECT * FROM illus_proposals WHERE id = ? "
                 "AND manuscript_id = ?", (proposal_id, manuscript["id"]))
    if row is None:
        raise LookupError(f"no illustration proposal '{proposal_id}'")
    row = dict(row)
    if row["state"] != "proposed":
        raise ValueError(f"proposal is {row['state']}, not proposed")
    if verdict not in ("accept", "reject"):
        raise ValueError("verdict must be accept or reject")

    if verdict == "reject":
        db.update("illus_proposals", row["id"], {"state": "rejected"})
        _record_evidence(db, manuscript, row, "rejected", reason, llm=llm)
        return {"state": "rejected", "file": row["file"]}

    path = Path(manuscript["path"]) / row["file"]
    text = path.read_text(encoding="utf-8")
    if row["anchor"] not in text:
        db.update("illus_proposals", row["id"], {"state": "stale"})
        raise LookupError("the anchor changed since this proposal — stale")
    final = (revised_description or row["description"]).strip()
    modified = bool(revised_description) and final != row["description"]
    if modified:
        meta = loads(row.get("metadata"), {}) or {}
        meta["original_description"] = row["description"]
        db.update("illus_proposals", row["id"],
                  {"metadata": json.dumps(meta), "description": final})
        row["metadata"] = json.dumps(meta)
        row["description"] = final

    if row["revises"]:
        pattern = re.compile(
            r"(?m)^(\[Illustration:\s*)" + re.escape(row["revises"])
            + r"(\s*(\|[^\]]*)?\])")
        # Replacement must be a function: a string template treats
        # backslashes in the new description as group refs / escapes
        # (crash on \U… paths; silent corruption on \1).
        new_text, n = pattern.subn(
            lambda m, desc=final: m.group(1) + desc + m.group(2),
            text, count=1)
        if n != 1:
            db.update("illus_proposals", row["id"], {"state": "stale"})
            raise LookupError("the existing tag changed — stale")
    else:
        idx = text.index(row["anchor"]) + len(row["anchor"])
        para_end = text.find("\n\n", idx)
        insert_at = para_end if para_end != -1 else len(text)
        tag = f"\n\n[Illustration: {final}]"
        new_text = text[:insert_at] + tag + text[insert_at:]
    path.write_text(new_text, encoding="utf-8")
    db.update("illus_proposals", row["id"], {"state": "accepted"})
    explanation = None
    if modified:
        explanation = (f"Illustration description revised at triage: "
                       f"«{loads(row['metadata'], {})['original_description']}» "
                       f"became «{final}» (criterion: {row['criterion']})")
    _record_evidence(db, manuscript, row,
                     "modified" if modified else "accepted",
                     explanation or reason, llm=llm)
    return {"state": "accepted", "file": row["file"], "modified": modified,
            "description": final}
