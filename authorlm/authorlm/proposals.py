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
    row = ko_fields("pr")
    row.update(
        manuscript_id=manuscript_id, kind=kind, target=target,
        payload=json.dumps(payload), content_hash=content_hash,
        source=source, state="open",
    )
    db.insert("knowledge_proposals", row)
    return row


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


def describe(row: dict) -> tuple[str, list[str]]:
    """(one-line summary, detail lines) for list/review displays."""
    payload = loads(row["payload"], {})
    kind = row["kind"]
    if kind == "note_update":
        summary = f"reframe '{payload['name']}' — new material suggests a different definition"
        details = [
            f"current:  {payload.get('current_note') or '(no notes)'}",
            f"proposed: {payload.get('proposed_note') or '(no notes)'}",
        ]
        if payload.get("proposed_kind") and payload.get("proposed_kind") != payload.get("current_kind"):
            details.append(f"kind: {payload.get('current_kind')} → {payload['proposed_kind']}")
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
        summary = (f"the text identifies '{payload['alias']}' with "
                   f"'{payload['canonical']}' — same concept?")
        where = f" ({payload['location']})" if payload.get("location") else ""
        details = [
            f"“{payload.get('sentence', '')}”{where}",
            f"adopt = merge: '{payload['alias']}' becomes an alias of "
            f"'{payload['canonical']}' and its notes absorb this sentence · "
            f"edge = a kind, not an identity: record '{payload['canonical']}' "
            f"—generalizes→ '{payload['alias']}' instead · dismiss = keep "
            "them distinct",
        ]
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
        from .concepts import get_concept, merge_concepts

        canonical = get_concept(db, manuscript_id, payload["canonical"])
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
