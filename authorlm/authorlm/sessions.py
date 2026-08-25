"""Authoring sessions, declared intents, and editorial episodes
(RFC AuthorLM Chapters 9, 10, 20).

AuthorLM is intent-driven: the session begins with an objective, and the
episode groups the transitions made in its pursuit (§19.14). Declared
intents are authoritative observations — recorded, never inferred (§9.2).
"""

from __future__ import annotations

import json

from . import backup
from .db import Database, ko_fields, loads, now_iso


def active_session(db: Database, manuscript_id: str):
    return db.one(
        "SELECT * FROM sessions WHERE manuscript_id = ? AND status = 'active' "
        "ORDER BY started_at DESC LIMIT 1",
        (manuscript_id,),
    )


def start_session(db: Database, manuscript_id: str) -> dict:
    existing = active_session(db, manuscript_id)
    if existing:
        raise ValueError(f"A session is already active ({existing['id']}). End it first.")
    row = ko_fields("s")
    row.update(
        manuscript_id=manuscript_id, started_at=now_iso(), ended_at=None, status="active"
    )
    db.insert("sessions", row)
    # Session start, not every CLI invocation: sessions stay open for a
    # working day, so this fires roughly once daily rather than on every
    # command (OPS-4). Never blocks — see backup.run.
    row["backup"] = backup.run(db)
    return row


def end_session(db: Database, manuscript_id: str, ended_at: str | None = None) -> dict:
    """End the active session. `ended_at` supports retroactive closure
    (idle expiry): the record shows when work stopped, not when we noticed."""
    session = active_session(db, manuscript_id)
    if not session:
        raise ValueError("No active session.")
    # Close this session's episodes; completed intents give their outcome.
    for episode in db.all(
        "SELECT * FROM editorial_episodes WHERE session_id = ? AND status = 'open'",
        (session["id"],),
    ):
        outcome = None
        if episode["intent_id"]:
            intent = db.one(
                "SELECT * FROM declared_intents WHERE id = ?", (episode["intent_id"],)
            )
            if intent and intent["status"] == "completed":
                outcome = intent["outcome"] or intent["statement"]
        db.update(
            "editorial_episodes", episode["id"],
            {"status": "closed", "outcome": outcome},
        )
    db.update("sessions", session["id"], {"status": "ended", "ended_at": ended_at or now_iso()})
    return dict(session)


def declare_intent(db: Database, manuscript_id: str, statement: str,
                   scope: str | None = None, status: str = "active",
                   source_id: str | None = None) -> dict:
    session = active_session(db, manuscript_id)
    row = ko_fields("di")
    row.update(
        manuscript_id=manuscript_id,
        session_id=session["id"] if session else None,
        statement=statement,
        status=status,
        outcome=None,
        scope=scope,
        source_id=source_id or db.source("author"),
    )
    db.insert("declared_intents", row)

    if status == "proposed":
        # Imported (non-author) intents await triage: no episode opens and
        # no work hangs off them until the author accepts.
        return row

    if session:
        episode = ko_fields("ep")
        episode.update(
            manuscript_id=manuscript_id, session_id=session["id"], intent_id=row["id"],
            transition_ids="[]", outcome=None, status="open",
        )
        db.insert("editorial_episodes", episode)
    return row


def complete_intent(db: Database, intent, outcome: str | None) -> None:
    db.update(
        "declared_intents", intent["id"],
        {"status": "completed", "outcome": outcome},
    )
    for episode in db.all(
        "SELECT * FROM editorial_episodes WHERE intent_id = ? AND status = 'open'",
        (intent["id"],),
    ):
        db.update(
            "editorial_episodes", episode["id"],
            {"status": "closed", "outcome": outcome or intent["statement"]},
        )


def abandon_intent(db: Database, intent, reason: str | None) -> None:
    """The objective is dropped, not achieved. The declaration itself stays
    in history — declared intents are observations and are never deleted."""
    db.update(
        "declared_intents", intent["id"],
        {"status": "abandoned", "outcome": reason},
    )
    for episode in db.all(
        "SELECT * FROM editorial_episodes WHERE intent_id = ? AND status = 'open'",
        (intent["id"],),
    ):
        db.update(
            "editorial_episodes", episode["id"],
            {"status": "closed", "outcome": reason or "intent abandoned"},
        )


def current_episode(db: Database, manuscript_id: str, session: dict) -> dict:
    """The open episode for the session's most recent active intent, or an
    intent-less episode when no intent has been declared (§9.4 allows both)."""
    episode = db.one(
        "SELECT * FROM editorial_episodes WHERE session_id = ? AND status = 'open' "
        "ORDER BY created_at DESC LIMIT 1",
        (session["id"],),
    )
    if episode:
        return dict(episode)
    row = ko_fields("ep")
    row.update(
        manuscript_id=manuscript_id, session_id=session["id"], intent_id=None,
        transition_ids="[]", outcome=None, status="open",
    )
    db.insert("editorial_episodes", row)
    return row


def attach_transitions(db: Database, episode: dict, transitions: list[dict]) -> None:
    ids = loads(episode["transition_ids"], [])
    ids.extend(t["id"] for t in transitions)
    db.update("editorial_episodes", episode["id"], {"transition_ids": json.dumps(ids)})
