"""Proposals against settled knowledge.

The hard guards make confirmed/authored knowledge machine-unwritable; this
module keeps it from fossilizing. When fresh inference conflicts with a
settled judgment — a materially different definition for an existing
concept, a retired concept recurring with new meaning, a rejected edge
argued again, a retired policy re-seeded — the conflict is recorded as an
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


def create(
    db: Database, manuscript_id: str, kind: str, target: str, payload: dict,
    source: str = "extraction",
) -> dict | None:
    """Record an open proposal. Returns None (creates nothing) if an open or
    dismissed proposal with identical content already exists."""
    content_hash = _content_hash(payload)
    existing = db.one(
        "SELECT id FROM knowledge_proposals WHERE manuscript_id = ? AND kind = ? "
        "AND target = ? AND content_hash = ? AND state IN ('open', 'dismissed')",
        (manuscript_id, kind, target, content_hash),
    )
    if existing:
        return None
    row = ko_fields("pr")
    row.update(
        manuscript_id=manuscript_id, kind=kind, target=target,
        payload=json.dumps(payload), content_hash=content_hash,
        source=source, state="open",
    )
    db.insert("knowledge_proposals", row)
    return row


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
    elif kind == "policy_revival":
        summary = f"revive retired policy: \"{payload['statement']}\""
        details = [f"new supporting explanation: {payload.get('new_explanation', '')}"]
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
    elif kind == "policy_revival":
        from .policies import reinforce_policy

        db.update("editorial_policies", row["target"], {"status": "candidate"})
        reinforce_policy(db, row["target"], "accepted")
        message = f"Policy revived as candidate: \"{payload['statement']}\""
    else:
        return f"error: unknown proposal kind '{kind}'"

    db.update("knowledge_proposals", row["id"], {"state": "adopted"})
    _record_evidence(db, manuscript_id, row, "adopted")
    return message


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
    summary, _ = describe(row)
    ev = ko_fields("ev")
    ev.update(
        manuscript_id=manuscript_id, episode_id=None,
        evidence_type="proposal_review", signal=signal,
        target=(summary + (f" — {reason}" if reason else ""))[:200],
        supports_policy=row["target"] if row["kind"] == "policy_revival" else None,
        weight="high",
    )
    db.insert("evidence", ev)
