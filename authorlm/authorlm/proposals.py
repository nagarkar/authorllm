"""Proposals against settled knowledge.

The hard guards make confirmed/authored knowledge machine-unwritable; this
module keeps it from fossilizing. When fresh inference conflicts with a
settled judgment — a materially different definition for an existing
concept, a retired concept recurring with new meaning, a rejected edge
argued again, a retired belief re-seeded — the conflict is recorded as an
open proposal for the author to adopt or dismiss, instead of being silently
discarded (Common Core §8.9: contradictions are valuable, preserve them).

Proposal creation is attention-gated by the caller: only text the author
actually changed can generate one, so LLM paraphrase churn on untouched
concepts never reaches the review queue. Dismissed proposals are
content-hashed and never recreated verbatim.
"""

from __future__ import annotations

import hashlib
import json

from .db import Database, ko_fields, loads


def _content_hash(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode()
    ).hexdigest()[:16]


def _norm(text: str | None) -> str:
    return " ".join((text or "").split()).strip().lower()


def _alias_canonical_conflict(db: Database, manuscript_id: str,
                              alias_name: str, canonical_name: str) -> str | None:
    """Guard (Q/alias-dedupe): one alias name may not have two open,
    competing canonicals — 'Prophecy' -> 'Birth-based Superstition' and
    'Prophecy' -> 'Essentialism' cannot both stand as open questions; they
    are two different answers to the same one. Returns the conflicting
    row's canonical name when the newcomer must be refused, else None.

    Chosen rule: REFUSE the newcomer rather than superseding the standing
    proposal. The standing one is a question already on the author's
    queue — silently dismissing it to make room for a second guess would
    let a later pass overwrite an earlier one the author simply has not
    reached yet, which is the opposite of 'never silently drop it'. An
    exact repeat (same alias, same canonical, any sentence) also matches
    here and collapses into the standing row rather than piling up a
    second copy that only differs by which sentence quoted it."""
    for row in db.all(
        "SELECT payload FROM knowledge_proposals WHERE manuscript_id = ? "
        "AND kind = 'alias' AND state = 'open'", (manuscript_id,),
    ):
        payload = loads(row["payload"], {}) or {}
        if _norm(payload.get("alias")) == _norm(alias_name):
            return payload.get("canonical")
    return None


def create(
    db: Database, manuscript_id: str, kind: str, target: str, payload: dict,
    source: str = "extraction",
) -> dict | None:
    """Record an open proposal. Returns None (creates nothing) if an open,
    dismissed, or demoted proposal with identical content already exists."""
    content_hash = _content_hash(payload)
    existing = db.one(
        "SELECT id FROM knowledge_proposals WHERE manuscript_id = ? AND kind = ? "
        "AND target = ? AND content_hash = ? "
        "AND state IN ('open', 'dismissed', 'demoted')",
        (manuscript_id, kind, target, content_hash),
    )
    if existing:
        return None
    if _suppressed_as_near_duplicate(db, manuscript_id, kind, target, payload):
        return None
    if kind == "alias" and _alias_canonical_conflict(
        db, manuscript_id, payload.get("alias", ""), payload.get("canonical", "")
    ) is not None:
        return None
    if kind == "pronunciation" and _pronunciation_settled(
        db, manuscript_id, target
    ):
        return None
    row = ko_fields("pr")
    row.update(
        manuscript_id=manuscript_id, kind=kind, target=target,
        payload=json.dumps(payload), content_hash=content_hash,
        source=source, state="open",
    )
    db.insert("knowledge_proposals", row)
    return row


def _pronunciation_settled(db: Database, manuscript_id: str,
                           term_key: str) -> bool:
    """ONE proposal per term, EVER. Any prior row in ANY state — open,
    adopted, dismissed, demoted — means the author has already been
    asked (§15.22 §3.5).

    `_content_hash` above catches only verbatim repeats, so a second
    prelude offering a DIFFERENT respelling of a dismissed term would
    sail straight past it — the exact failure
    `_suppressed_as_near_duplicate` was written for, whose own docstring
    records 18 open note_update rows on one concept. The instrument here
    is stronger and free, because the identity of the question is the
    TERM and not the spelling: deterministic, no similarity arithmetic,
    no model.

    The cost, stated rather than hidden: an author who dismisses because
    THIS RESPELLING was wrong — rather than because the term is easy —
    is never asked again. That is the right trade. The remedy is to write
    the row in pronunciations.md directly, which is a better path than
    another round of guessing, and the dismissal reason is on the
    evidence stream either way. There is no "re-open" verb and there
    should not be one; the file is the escape hatch."""
    return db.one(
        "SELECT id FROM knowledge_proposals WHERE manuscript_id = ? "
        "AND kind = 'pronunciation' AND target = ? LIMIT 1",
        (manuscript_id, term_key)) is not None


def _suppressed_as_near_duplicate(db: Database, manuscript_id: str, kind: str,
                                  target: str, payload: dict) -> bool:
    """The deterministic firewall: a proposal that says what an existing one
    already says — in different words or punctuation — never reaches the
    queue.

    `_content_hash` above catches only verbatim repeats, which is why 18
    open note_update proposals for `Nothing` accumulated, four of them the
    same sentence differing by an em-dash. This costs no model call, learns
    nothing, and settles nothing: it exists purely so the author is not
    asked the same question twice. Prior rows in ANY settled state count —
    re-asking a question they already answered is the whole failure."""
    from . import loop

    spec = loop.REGISTRY.get(f"proposals/{kind}")
    if spec is None:
        return False
    prior = [
        (r["id"], spec.dedupe_text(dict(r)))
        for r in db.all(
            "SELECT * FROM knowledge_proposals WHERE manuscript_id = ? "
            "AND kind = ? AND target = ? "
            "AND state IN ('open', 'dismissed', 'demoted', 'adopted')",
            (manuscript_id, kind, target))
    ]
    if not prior:
        return False
    candidate = spec.dedupe_text({"kind": kind, "target": target,
                                  "payload": json.dumps(payload)})
    return loop.near_duplicate(candidate, prior) is not None


def open_proposals(db: Database, manuscript_id: str) -> list[dict]:
    return [dict(r) for r in db.all(
        "SELECT * FROM knowledge_proposals WHERE manuscript_id = ? AND state = 'open' "
        "ORDER BY created_at",
        (manuscript_id,),
    )]


def group_open(rows: list[dict]) -> list[dict]:
    """Collapse open `note_update` rows that target the same concept into
    ONE synthetic 'note_update_group' entry carrying every candidate note
    (Q/note-group), so the author reviews N competing rewrites of one
    concept as a single decision instead of N unrelated-looking ones.

    Storage is untouched: every candidate stays its own row in
    `knowledge_proposals`, addressable by its own id ('proposal accept
    <id>' still works on any one of them) — this only changes what a
    LISTING shows. A target with a single open note_update, and every
    other kind, passes through unchanged. Never picks a winner: the
    grouped entry lists all candidates and lets the author choose."""
    by_target: dict[str, list[dict]] = {}
    order: list[str] = []
    for row in rows:
        if row["kind"] != "note_update":
            continue
        if row["target"] not in by_target:
            order.append(row["target"])
        by_target.setdefault(row["target"], []).append(row)
    competing = {t for t in order if len(by_target[t]) > 1}
    if not competing:
        return list(rows)
    out: list[dict] = []
    folded_in: set[str] = set()
    for row in rows:
        if row["kind"] == "note_update" and row["target"] in competing:
            if row["target"] in folded_in:
                continue
            folded_in.add(row["target"])
            members = by_target[row["target"]]
            out.append({
                "id": members[0]["id"], "kind": "note_update_group",
                "target": row["target"], "state": "open",
                "created_at": members[0]["created_at"],
                "source": members[0]["source"], "members": members,
            })
        else:
            out.append(row)
    return out


def describe(row: dict) -> tuple[str, list[str]]:
    """(one-line summary, detail lines) for list/review displays."""
    # group_open's synthetic 'note_update_group' row carries `members`
    # instead of a single `payload` — its branch below reads `members`
    # directly, so `.get` here just keeps this lookup from raising on it.
    payload = loads(row.get("payload"), {})
    kind = row["kind"]
    if kind == "note_update":
        summary = f"reframe '{payload['name']}' — new material suggests a different definition"
        details = [
            f"current:  {payload.get('current_note') or '(no notes)'}",
            f"proposed: {payload.get('proposed_note') or '(no notes)'}",
        ]
        if payload.get("proposed_kind") and payload.get("proposed_kind") != payload.get("current_kind"):
            details.append(f"kind: {payload.get('current_kind')} → {payload['proposed_kind']}")
    elif kind == "note_update_group":
        # Synthetic entry from group_open (Q/note-group): several open
        # note_update rows on the same concept, shown as ONE decision with
        # every candidate — never auto-picked, the author chooses.
        members = row["members"]
        first_payload = loads(members[0]["payload"], {})
        summary = (f"reframe '{first_payload.get('name', '?')}' — "
                   f"{len(members)} candidate notes on the table, choose one")
        details = [f"current:  {first_payload.get('current_note') or '(no notes)'}"]
        for n, member in enumerate(members, 1):
            p = loads(member["payload"], {})
            details.append(
                f"  {n}. [{member['id'][:8]}] {p.get('proposed_note') or '(no notes)'}")
        details.append(
            "adopt = 'proposal accept <candidate-id>' for the one you want "
            "— the others stay open for their own review")
    elif kind == "revival":
        summary = f"revive retired concept '{payload['name']}' — it recurs in new material"
        details = [f"as: {payload.get('kind', 'concept')}"]
        if payload.get("notes"):
            details.append(f"notes: {payload['notes']}")
    elif kind == "edge_reproposal":
        summary = (f"reconsider rejected relationship: {payload['from_name']} "
                   f"—{payload['relation']}→ {payload['to_name']}")
        details = ["new material involves these concepts again"]
    elif kind == "variant_of_retired":
        summary = (f"'{payload['proposed_name']}' resembles retired "
                   f"'{payload['retired_name']}' — genuinely new concept, or the "
                   f"rejected one under a new name?")
        details = [f"as: {payload.get('kind', 'concept')}"]
        if payload.get("notes"):
            details.append(f"notes: {payload['notes']}")
        details.append("adopt = add as a distinct concept · dismiss = treat as the retired one")
    elif kind == "vanished":
        summary = (f"'{payload['name']}' no longer appears anywhere in the text "
                   f"(was introduced in {payload.get('was_in', '?')})")
        details = ["adopt = retire it · dismiss = keep as a declared placeholder "
                   "for future writing"]
    elif kind == "belief_revival":
        summary = f"revive retired belief: \"{payload['statement']}\""
        details = [f"new supporting explanation: {payload.get('new_explanation', '')}"]
    elif kind == "alias":
        # Q/alias-guide-flip: a naming ceremony ('alias_is_new' in payload)
        # bestows a genuinely new name on an existing concept — adopting it
        # ADDS the name, it does not MERGE two pre-existing nodes. Legacy
        # rows (created before the flip, or raised by hand) lack the key
        # and default to the original merge phrasing.
        verb = "add" if payload.get("alias_is_new") else "merge"
        summary = (f"the text identifies '{payload['alias']}' with "
                   f"'{payload['canonical']}' — same concept?")
        where = f" ({payload['location']})" if payload.get("location") else ""
        details = [
            f"“{payload.get('sentence', '')}”{where}",
            f"adopt = {verb}: '{payload['alias']}' becomes an alias of "
            f"'{payload['canonical']}' and its notes absorb this sentence · "
            f"edge = a kind, not an identity: record '{payload['canonical']}' "
            f"—generalizes→ '{payload['alias']}' instead · dismiss = keep "
            "them distinct",
        ]
    elif kind == "pronunciation":
        summary = (f"pronounce '{payload['name']}' — {payload.get('say', '')}")
        details = []
        if payload.get("note"):
            details.append(f"note: {payload['note']}")
        if payload.get("file"):
            details.append(f"met in: {payload['file']}"
                           + (f" (filter '{payload['filter']}')"
                              if payload.get("filter") else ""))
        details.append(
            "adopt = write the row into pronunciations.md · "
            "dismiss = it does not need one")
    elif kind == "incongruence":
        summary = (f"text contradicts settled knowledge about "
                   f"'{payload.get('concept', '?')}' "
                   f"({payload.get('file', '?')})")
        details = [
            f"“{payload.get('quote', '')}”",
            f"settled: {payload.get('claim', '')}",
            f"why: {payload.get('why', '')}",
            "adopt = acknowledged, I will fix the text (or the graph) · "
            "dismiss = the text is right / no real conflict",
        ]
    else:
        summary = f"{kind} on {row['target']}"
        details = [json.dumps(payload)]
    return summary, details


def adopt(db: Database, manuscript_id: str, row: dict) -> str:
    """Apply the proposal to the settled object. Returns a message."""
    payload = loads(row["payload"], {})
    kind = row["kind"]
    if kind == "note_update":
        changes = {"notes": payload["proposed_note"]}
        if payload.get("proposed_kind") and payload["proposed_kind"] != payload.get("current_kind"):
            changes["kind"] = payload["proposed_kind"]
        db.update("concept_nodes", row["target"], changes)
        message = f"Updated '{payload['name']}' with the proposed definition."
    elif kind == "revival":
        node = db.one("SELECT * FROM concept_nodes WHERE id = ?", (row["target"],))
        meta = loads(node["metadata"], {}) if node else {}
        meta.update(origin="extracted", confirmed=True)  # adoption is confirmation
        db.update(
            "concept_nodes", row["target"],
            {
                "status": "declared", "kind": payload.get("kind", "concept"),
                "notes": payload.get("notes"), "introduced_in": None,
                "metadata": json.dumps(meta),
            },
        )
        message = (f"Revived '{payload['name']}' — it will realize against the "
                   "text on the next collect.")
    elif kind == "edge_reproposal":
        db.update("concept_edges", row["target"], {"status": "declared"})
        message = (f"Relationship restored as declared: {payload['from_name']} "
                   f"—{payload['relation']}→ {payload['to_name']}")
    elif kind == "variant_of_retired":
        from .concepts import add_concept

        node = add_concept(
            db, manuscript_id, payload["proposed_name"],
            kind=payload.get("kind", "concept"), notes=payload.get("notes"),
        )
        meta = loads(node.get("metadata"), {})
        meta.update(origin="extracted", confirmed=True)  # adoption is confirmation
        db.update("concept_nodes", node["id"], {"metadata": json.dumps(meta)})
        message = (f"Added '{payload['proposed_name']}' as a distinct concept "
                   f"(unrelated to retired '{payload['retired_name']}').")
    elif kind == "vanished":
        from .concepts import retire_concept

        node = db.one("SELECT * FROM concept_nodes WHERE id = ?", (row["target"],))
        edges = retire_concept(db, manuscript_id, dict(node)) if node else 0
        message = (f"Retired '{payload['name']}' ({edges} edge(s) with it) — "
                   "its text is gone and the author let it go.")
    elif kind == "belief_revival":
        from .beliefs import _record_support, reinforce_belief

        # If the revived belief was retired by being merged into another
        # (metadata.curation.action == "merged"), remember the canonical
        # it was folded into BEFORE flipping this row's status — once this
        # row is no longer 'retired', beliefs._merged_into (which walks
        # only retired rows) stops seeing it from the canonical side, so
        # the canonical must be recomputed too or its stored `supporting`
        # stays stale, double-counting this belief's evidence against
        # BOTH beliefs until the canonical happens to be touched again.
        revived = db.one("SELECT metadata FROM editorial_beliefs WHERE id = ?",
                         (row["target"],))
        revived_curation = (loads(revived["metadata"], {}) if revived else {}).get("curation", {})
        merged_into_id = (revived_curation.get("into")
                          if revived_curation.get("action") == "merged" else None)

        db.update("editorial_beliefs", row["target"], {"status": "candidate"})
        # Adopting the proposal IS the author's evidence for this event;
        # write it before reinforcing so the derived count (INV-2) can see
        # it — this is also what stops revival inheriting the belief's
        # pre-retirement counter (beliefs.py's `reinforce_belief` now
        # derives fresh from evidence rather than reading the stale stored
        # value).
        _record_support(db, manuscript_id, row["target"], None,
                        payload.get("new_explanation") or payload["statement"])
        reinforce_belief(db, row["target"], "accepted")
        if merged_into_id:
            # Recompute the canonical NOW rather than leaving it stale —
            # closes the double count immediately instead of waiting on
            # the canonical's next unrelated touch.
            reinforce_belief(db, merged_into_id, "accepted")
        message = f"Belief revived as candidate: \"{payload['statement']}\""
    elif kind == "alias":
        from .concepts import add_alias, get_concept, merge_concepts

        canonical = get_concept(db, manuscript_id, payload["canonical"])
        if payload.get("alias_is_new"):
            # Q/alias-guide-flip: a naming ceremony for a name that was
            # never its own concept node — adopting it ADDS the name to
            # the canonical concept, it does not merge two existing nodes
            # (there is only one: `canonical`; `row['target']` points at
            # it too, since there was nothing else to reference at
            # creation time).
            if not canonical or canonical["status"] == "retired":
                db.update("knowledge_proposals", row["id"], {"state": "dismissed"})
                return ("error: the canonical concept has changed since "
                        "the proposal — nothing added.")
            restale = get_concept(db, manuscript_id, payload["alias"])
            if restale and restale["status"] != "retired":
                # The world moved since this was raised: the bestowed name
                # is now itself a live concept — exactly what Q/alias-guard
                # refuses at creation, so adoption must refuse it too.
                db.update("knowledge_proposals", row["id"], {"state": "dismissed"})
                return (f"error: '{payload['alias']}' has since become its "
                        "own concept — nothing added.")
            add_alias(db, manuscript_id, dict(canonical), payload["alias"])
            sentence = payload.get("sentence")
            if sentence:
                base = (canonical["notes"] or "").rstrip()
                quote = f"“{sentence}”"
                if quote.lower() not in base.lower():
                    db.update("concept_nodes", canonical["id"],
                              {"notes": (base + " " if base else "") + quote})
            message = (f"'{payload['alias']}' recorded as a new name for "
                       f"'{payload['canonical']}' — the notes absorbed the "
                       "naming sentence.")
        else:
            duplicate = db.one(
                "SELECT * FROM concept_nodes WHERE id = ?", (row["target"],))
            if (not canonical or not duplicate or canonical["status"] == "retired"
                    or duplicate["status"] == "retired"
                    or canonical["id"] == duplicate["id"]):
                db.update("knowledge_proposals", row["id"], {"state": "dismissed"})
                return ("error: these concepts have changed since the proposal — "
                        "nothing merged.")
            merged = merge_concepts(db, manuscript_id, dict(canonical), dict(duplicate))
            sentence = payload.get("sentence")
            if sentence:
                base = (canonical["notes"] or "").rstrip()
                quote = f"“{sentence}”"
                if quote.lower() not in base.lower():
                    db.update("concept_nodes", canonical["id"],
                              {"notes": (base + " " if base else "") + quote})
            message = (f"Merged '{payload['alias']}' into '{payload['canonical']}' "
                       f"— {merged['repointed']} edge(s) re-pointed, "
                       f"{merged['dropped']} retired; the notes absorbed the "
                       "aliasing sentence.")
    elif kind == "pronunciation":
        # THE ONLY WRITER of pronunciations.md in the whole system
        # (§15.22 §3.7). Not the model, not `filter prelude`, not
        # `filter record`, not `filter settle`, not `filter rollback`,
        # not `filter unmark`, and not the Doc pull except as the
        # author's own edit arriving through the ordinary tab write.
        # The immutability the Sponsor asked for is a CONSEQUENCE of
        # there being exactly one writer and it being the author's own
        # verdict — not a rule enforced anywhere.
        #
        # `adopt` takes a manuscript ID rather than the row, so it has no
        # `path`. Smallest fix, inside this branch and nowhere else: the
        # signature is used by cli, mcp and the triage app, and is not
        # worth changing for one branch.
        from pathlib import Path

        from . import pronunciations as pron

        root = Path(db.one("SELECT path FROM manuscripts WHERE id = ?",
                           (manuscript_id,))["path"])
        path = root / pron.FILENAME
        text = path.read_text(encoding="utf-8") if path.exists() else pron.SEED
        new = pron.add_row(text, {"term": payload["name"],
                                  "say": payload["say"],
                                  "note": payload.get("note") or ""})
        if new != text:
            # `add_row` is idempotent, so a double-accept writes nothing
            # twice and the file's mtime does not move for nothing.
            path.write_text(new, encoding="utf-8")
        message = (f"'{payload['name']}' is said {payload['say']} — "
                   f"written into {pron.FILENAME}.")
    elif kind == "incongruence":
        # No object mutation: the author is the execution engine. Adoption
        # records the acknowledged conflict as evidence; the fix (text or
        # graph) is the author's next edit.
        message = (f"Acknowledged: “{payload.get('quote', '')[:80]}…” "
                   f"conflicts with settled knowledge about "
                   f"'{payload.get('concept', '?')}' — fix the text or "
                   "the graph, and the next collect records it.")
    else:
        return f"error: unknown proposal kind '{kind}'"

    db.update("knowledge_proposals", row["id"], {"state": "adopted"})
    _record_evidence(db, manuscript_id, row, "adopted")
    return message


def demote_to_edge(db: Database, manuscript_id: str, row: dict) -> str:
    """Alias proposals only: the author judges the aliasing sentence names a
    kind, not an identity — record 'canonical generalizes alias' and keep
    both concepts."""
    if row["kind"] != "alias":
        return "error: only alias proposals can be demoted to an edge."
    from .concepts import link_concepts

    payload = loads(row["payload"], {})
    link_concepts(db, manuscript_id, payload["canonical"], "generalizes",
                  payload["alias"])
    db.update("knowledge_proposals", row["id"], {"state": "demoted"})
    _record_evidence(db, manuscript_id, row, "demoted-to-edge")
    return (f"Recorded {payload['canonical']} —generalizes→ "
            f"{payload['alias']} — kept as distinct concepts.")


def dismiss(db: Database, manuscript_id: str, row: dict, reason: str | None = None) -> str:
    db.update("knowledge_proposals", row["id"], {"state": "dismissed"})
    _record_evidence(db, manuscript_id, row, "dismissed", reason)
    summary, _ = describe(row)
    if row["kind"] == "vanished":
        # Dismissal has declared semantics here: keep the concept as a
        # placeholder awaiting future text (it will re-realize on mention).
        payload = loads(row["payload"], {})
        db.update("concept_nodes", row["target"],
                  {"status": "declared", "introduced_in": None})
        return (f"Kept '{payload['name']}' as a declared placeholder — it will "
                "realize again when the text returns.")
    return f"Dismissed: {summary} (will not be re-proposed verbatim)."


def _record_evidence(db: Database, manuscript_id: str, row: dict, signal: str,
                     reason: str | None = None) -> None:
    """The author's verbatim reason is the highest-value evidence there is,
    so it is stored WHOLE in metadata.explanation — `target` stays a short
    display line and is still clipped. Clipping the reason (it used to be
    appended to `target` and cut at 200 chars, losing the end of every
    explanation longer than a sentence) starved the distiller of the only
    input that makes it worth running."""
    summary, _ = describe(row)
    ev = ko_fields("ev")
    ev.update(
        manuscript_id=manuscript_id, episode_id=None,
        evidence_type="proposal_review", signal=signal,
        target=(summary + (f" — {reason}" if reason else ""))[:200],
        supports_belief=row["target"] if row["kind"] == "belief_revival" else None,
        weight="high",
    )
    if reason:
        ev["metadata"] = json.dumps({
            "explanation": reason, "kind": row["kind"],
            "proposal_id": row["id"], "summary": summary,
        })
    db.insert("evidence", ev)
