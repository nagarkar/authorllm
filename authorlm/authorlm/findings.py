"""Generic finding storage — the door shared by any producer that emits
arbitrary quote-anchored editorial findings (lens, morph, and any future
producer alike): hygiene-gate the quote against the file's real text,
store a `guidance_history` row, and — when the finding carries a
`footnote`, `judgment`, or `replacement` — stage a Doc edit through the
same `<<old>>{{new}}` grammar every other producer uses.

Factored out of `lenses._store_findings` (2026-09-26, `docs/morph-design.md`
§7) so a new producer never has to fabricate lens-specific preconditions
(a real `_lenses/*.md` file, cross-chapter payload assembly) just to
reach this shared core. Lens's own cross-chapter behavior (`target_file`/
`target_quote` validation, poem grain) is still handled HERE, not
duplicated — it is simply inert for a caller that never supplies
`target_files` or `poem` (morph does not), so passing neither is enough
to opt out, not a second code path to maintain.
"""

from __future__ import annotations

import hashlib
import json
import re

from .db import Database, ko_fields, loads, new_id

RESERVED_MARKERS = ("<<", ">>", "{{", "}}")


def flag_rules(body: str) -> list[str]:
    """The Flag rule headings a rubric states, for tallies and for the
    `rule` field's gate. Two shapes are read: `- **Heading.** …` bullets
    and `### Flag — heading` example headings; anything else the rubric
    writes is simply not a named rule."""
    rules: list[str] = []
    for m in re.finditer(r"^\s*-\s+\*\*(.+?)\*\*", body, re.M):
        rules.append(m.group(1).strip().rstrip(".:"))
    for m in re.finditer(r"^###\s+Flag\s+[—-]+\s+(.+?)\s*$", body, re.M):
        rules.append(m.group(1).strip().rstrip("."))
    seen, out = set(), []
    for r in rules:
        k = _norm(r)
        if k and k not in seen:
            seen.add(k)
            out.append(r)
    return out


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower()).strip()


def sha(text: str) -> str:
    return hashlib.sha256((text or "").encode()).hexdigest()


def footnote_gist(raw) -> str:
    """A finding's `footnote` gist, made safe for the tag grammar: one
    line, no brackets (a tag never nests and never spans lines), no
    reserved markers. Empty when there is nothing to plant."""
    gist = " ".join(str(raw or "").split())
    gist = gist.replace("[", "(").replace("]", ")")
    for m in RESERVED_MARKERS:
        gist = gist.replace(m, "")
    return gist.strip()


def _quote_is_word_fragment(unit: str, quote: str) -> bool:
    """True when the unique `quote` span sits inside a larger word.

    A surgical replace would still rewrite that span (e.g. quote `form`
    inside `formalism` → `structurealism`). The door refuses that
    rewrite: an edit must replace a free-standing occurrence, not a
    syllable of a longer token. Boundaries are alphanumeric —
    punctuation, spaces, and string edges all count as free-standing."""
    start = unit.find(quote)
    if start < 0:
        return False
    end = start + len(quote)
    left = start > 0 and unit[start - 1].isalnum()
    right = end < len(unit) and unit[end].isalnum()
    return left or right


def edit_entry(units: list[str], raw_quote: str, replacement: str,
               note: str, taken_units: set[int]) -> tuple[dict | None, str]:
    """Anchor one finding's `replacement` to its unit, or refuse.

    The door's law (filter-pass design §7.2), applied literally: the
    quote must occur VERBATIM in exactly one unit and exactly once
    within it — an unanchored replace picks an occurrence, and picking
    is guessing. A unique span that is only a fragment of a larger word
    is also refused: rewriting inside another token corrupts the unit.
    Returns (entry, "") on success or (None, reason)."""
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
    if _quote_is_word_fragment(unit, raw_quote):
        return None, ("quote is only a fragment of a larger word in its "
                      "unit — refusing an edit that would rewrite inside "
                      "another word")
    if n in taken_units:
        return None, ("its unit already carries a staged edit from "
                      "this batch")
    if any(m in replacement for m in RESERVED_MARKERS):
        return None, "replacement carries reserved markers (<< >> {{ }})"
    start = unit.find(raw_quote)
    new = unit[:start] + replacement + unit[start + len(raw_quote):]
    if not new.strip():
        return None, "replacement would leave the unit empty"
    if new == unit:
        return None, "replacement is identical to the quoted text"
    return {"n": n, "new": new, "why": note}, ""


def unit_of(units: list[str], raw_quote: str) -> tuple[int | None, str]:
    """The one unit a quote sits in, 1-based, or (None, reason). The same
    law as `edit_entry` without the replacement checks: a judgment tag is
    planted AFTER the unit, so it needs the unit and nothing else."""
    hits = [i for i, u in enumerate(units, 1) if raw_quote in u]
    if not hits:
        return None, ("quote matches the file only after whitespace "
                      "normalization — not verbatim at the byte level, "
                      "so the tag has no unit to follow")
    if len(hits) > 1:
        return None, (f"quote occurs in {len(hits)} units — the tag "
                      "cannot follow an ambiguous quote")
    return hits[0], ""


def judgment_tag(producer: str, judgment: str, finding_id: str) -> str:
    return f"[Judgment: {producer} — {judgment} | id: {finding_id[:11]}]"


def store_findings(db: Database, manuscript: dict, session: dict, *,
                   kind: str, producer: str, relpath: str, file_text: str,
                   findings: list, source: str,
                   batch_prefix: str = "fb",
                   origin_type: str = "filter", verb_stem: str = "filter",
                   producer_key: str = "producer",
                   rubric_body: str = "",
                   target_files: dict[str, str] | None = None,
                   target_shas: dict[str, str] | None = None,
                   poem: dict | None = None) -> dict:
    """Hygiene-gate each finding (verbatim quote in the file) and store
    the survivors as a fresh batch under `kind`.

    `producer` names the specific lens/morph-rubric/etc. that found
    these — it goes in `metadata[producer_key]`, the dedupe key, and
    the finding's own suggestion text. `producer_key` defaults to the
    generic `"producer"`; lens's wrapper passes `"lens"` instead, so
    every row this writes for lens — and the return dict's own
    identifying key — stays byte-identical to what `lens status`/
    `lens findings`/`lens review` (and every already-stored historical
    row) already expect. A new producer with no existing consumers
    reading a specific key name should just take the default. `kind`
    is the `guidance_history` kind every row gets (e.g. `"lens"`,
    `"morph"`) — callers sharing this door but wanting distinct
    reporting/review scopes should pass distinct `kind` values, not
    distinct `producer` values.

    Cross-chapter targeting (`target_file`/`target_quote` on a finding)
    and poem grain are handled here but inert unless a caller actually
    supplies `target_files`/`poem` — a same-file, whole-file producer
    (morph) simply never triggers either path.

    A finding may carry an optional `replacement` (filter-pass design
    §7.2): the quoted text's proposed substitute within its unit. When
    it anchors cleanly a `doc_threads` row is staged with the given
    `origin_type`. Ruling on the finding and settling the edit stay
    independent."""
    from . import passes
    from . import staging

    scope = poem["text"] if poem else file_text
    flat = " ".join(scope.split()).lower()
    units = passes.paragraphs_of(file_text)
    batch_id = new_id(batch_prefix)
    stored, dropped = [], 0
    refused_targets: list[dict] = []
    entries: list[tuple[dict, dict]] = []
    refused: list[dict] = []
    judgments: list[tuple[int, str, dict]] = []
    taken_units: set[int] = set()
    known_rules = {_norm(r): r for r in flag_rules(rubric_body)} \
        if rubric_body else {}
    target_files = target_files or {}
    target_shas = target_shas or {}
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
        tfile = str(f.get("target_file") or "").strip() or None
        if tfile and tfile not in target_files:
            refused_targets.append({"quote": quote[:80], "target": tfile})
            continue
        index += 1
        row = ko_fields("gd")
        row.update(
            manuscript_id=manuscript["id"], session_id=session["id"],
            intent_id=None, batch_id=batch_id, batch_index=index,
            kind=kind,
            suggestion=f"[{producer}] {note}",
            explanation=f"On “{quote}” ({relpath}): {note}",
            state="proposed",
        )
        meta = {
            producer_key: producer, "file": relpath, "quote": quote,
            "source": source,
            "dedupe_key": f"{kind}:{producer}:{relpath}:{index}",
            "essay_sha": sha(file_text),
            "targets": target_shas,
        }
        if poem:
            meta["poem"] = poem["title"]
            meta["poem_n"] = poem["n"]
            meta["poem_sha"] = poem["sha"]
            meta["dedupe_key"] = (f"{kind}:{producer}:{relpath}#"
                                  f"{poem['n']}:{index}")
        rule = str(f.get("rule") or "").strip()
        if rule:
            meta["rule"] = known_rules.get(_norm(rule), rule)
            meta["rule_known"] = _norm(rule) in known_rules
        if tfile:
            meta["target_file"] = tfile
            tq = " ".join(str(f.get("target_quote") or "").split())
            if tq:
                meta["target_quote"] = tq
                tflat = " ".join(target_files[tfile].split())
                if tq not in tflat:
                    meta["target_unverified"] = ("target quote is not "
                                                 "verbatim in the target")
        gist = footnote_gist(f.get("footnote"))
        if gist:
            meta["footnote"] = gist
        judgment = footnote_gist(f.get("judgment"))
        if judgment:
            meta["judgment"] = judgment
        replacement = None
        if "replacement" in f or gist:
            replacement = str(f.get("replacement") or raw_quote)
            if gist and "[footnote:" not in replacement.lower():
                # Planted, never drafted (footnote design §0, §12): the
                # tag is the author's own request grammar, inert until
                # the footnote road resolves it.
                replacement = replacement.rstrip() + f"[Footnote: {gist}]"
        elif judgment:
            # A judgment is planted as an INSERTION after the unit the
            # quote sits in: nothing struck, the tag alone in green as its
            # own paragraph (author ruling 2026-09-27, on a stanza struck
            # whole to plant one tag: "I don't see the change"). The
            # finding's id rides in the tag so `repair` finds its way
            # back. Deleting the tag in the tab rejects the finding;
            # leaving it accepts it.
            n, reason = unit_of(units, raw_quote)
            if n is None:
                meta["edit_refused"] = reason
                refused.append({"quote": quote[:80], "reason": reason})
            else:
                judgments.append((n, judgment_tag(producer, judgment,
                                                  row["id"]), row))
        if replacement is not None:
            entry, reason = edit_entry(units, raw_quote, replacement,
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
        # The owner is the BATCH, not the producer: stage_edits keys
        # threads by owner:file:ordinal and replaces in place on a
        # re-run, which for a shared producer name would overwrite
        # forms already out in the tab.
        staged = staging.stage_edits(
            db, manuscript["id"], f"{producer}@{batch_id[3:11]}", relpath,
            file_text, [e for e, _ in entries], origin_type=origin_type,
            verb_stem=verb_stem)
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
    for k, (n, tag, frow) in enumerate(judgments, 1):
        trow = ko_fields("dt")
        trow.update(
            manuscript_id=manuscript["id"], origin_type=origin_type,
            origin_id=f"{producer}@{batch_id[3:11]}:{relpath}:j{k}",
            file=relpath, anchor_quote=units[n - 1][:200] or None,
            proposed_old="", proposed_new=tag, note=frow["suggestion"],
            state="proposed", our_reply_ids="[]", last_author_reply_id=None,
            scope_kind="file", scope_ref=relpath,
            metadata=json.dumps({"kind": "insert", "anchor_paragraph": n,
                                 "intent_id": None, "original_new": tag,
                                 "unit": n, "judgment": True}))
        db.insert("doc_threads", trow)
        fmeta = json.loads(frow["metadata"])
        fmeta["edit_thread"] = trow["id"]
        db.update("guidance_history", frow["id"],
                  {"metadata": json.dumps(fmeta)})
        frow["metadata"] = json.dumps(fmeta)
        edits.append(trow)
    return {producer_key: producer, "file": relpath, "batch_id": batch_id,
            "poem": (poem["title"] if poem else None),
            "findings": [dict(r) for r in stored],
            "dropped_ungrounded": dropped,
            "refused_targets": refused_targets,
            "edits_staged": edits, "edits_refused": refused}
