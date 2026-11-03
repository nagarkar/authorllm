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


# The third value of `collect(episode=…)`.
#
# `None` already means AMBIENT — attach to the session's most recently
# created open episode — so there was no way to say "attach to nothing"
# without inventing a value that is neither a row nor None. A sentinel
# object is that value: it can never collide with an episode dict, it
# cannot be produced by a database read, and `is` comparison makes the
# branch unmistakable at the call site.
NO_EPISODE = object()


def active_session(db: Database, manuscript_id: str):
    return db.one(
        "SELECT * FROM sessions WHERE manuscript_id = ? AND status = 'active' "
        "ORDER BY started_at DESC LIMIT 1",
        (manuscript_id,),
    )


def start_session(db: Database, manuscript_id: str) -> dict:
    existing = active_session(db, manuscript_id)
    if existing:
        # A session already being open is the NORMAL state for the whole
        # span of a working day — the author does not re-run 'session
        # start' once one is open, so the INSERT branch below (the only
        # place that used to call backup.run) never fired during exactly
        # the stretch when the database is being changed
        # (it-0bffe1ff657b). Back up on this "resume" attempt too, before
        # refusing — never blocks (see backup.run), and skip-if-unchanged
        # means a second attempt minutes later costs nothing extra.
        backup.run(db)
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


def episode_for_intent(db: Database, manuscript_id: str, session: dict,
                       intent_id: str) -> dict:
    """The open episode for THIS intent, in ANY session, else a fresh one
    in the current session.

    Any session, deliberately. `declare_intent` opens an episode only
    while a session is live, and `complete_intent` / `abandon_intent`
    close every open episode for the intent across sessions — so an
    intent declared yesterday has exactly one open episode and it is not
    in today's session. `current_episode`, which is scoped to the
    session, would never find it and would attach the work to whichever
    intent happened to be declared last (design-intent-scope §1.7)."""
    episode = db.one(
        "SELECT * FROM editorial_episodes WHERE manuscript_id = ? "
        "AND intent_id = ? AND status = 'open' "
        "ORDER BY created_at DESC LIMIT 1",
        (manuscript_id, intent_id),
    )
    if episode:
        return dict(episode)
    row = ko_fields("ep")
    row.update(
        manuscript_id=manuscript_id, session_id=session["id"],
        intent_id=intent_id, transition_ids="[]", outcome=None, status="open",
    )
    db.insert("editorial_episodes", row)
    return row


def attach_transitions(db: Database, episode: dict, transitions: list[dict]) -> None:
    ids = loads(episode["transition_ids"], [])
    ids.extend(t["id"] for t in transitions)
    db.update("editorial_episodes", episode["id"], {"transition_ids": json.dumps(ids)})


def critic_tallies(db: Database, manuscript_id: str) -> list[dict]:
    """Per-critic counts of proposed, accepted and rejected intents and style
    laws: what the briefing badges and `critique status` show. A read-only
    tally over the rows each critic sourced."""
    rows = db.all(
        """SELECT s.id AS source_id, s.name,
                  SUM(CASE WHEN i.status = 'proposed' THEN 1 ELSE 0 END) AS proposed,
                  SUM(CASE WHEN i.status IN ('active', 'completed') THEN 1 ELSE 0 END) AS accepted,
                  SUM(CASE WHEN i.status = 'rejected' THEN 1 ELSE 0 END) AS rejected
           FROM sources s JOIN declared_intents i ON i.source_id = s.id
           WHERE s.kind = 'critic' AND i.manuscript_id = ?
           GROUP BY s.id""", (manuscript_id,))
    result = []
    for r in rows:
        el = db.one(
            """SELECT SUM(CASE WHEN status = 'proposed' THEN 1 ELSE 0 END) AS proposed,
                      SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) AS accepted,
                      SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END) AS rejected
               FROM style_laws WHERE manuscript_id = ? AND source_id = ?""",
            (manuscript_id, r["source_id"]))
        result.append({
            "source_id": r["source_id"], "name": r["name"],
            "intents": {"proposed": r["proposed"] or 0,
                        "accepted": r["accepted"] or 0,
                        "rejected": r["rejected"] or 0},
            "elements": {"proposed": el["proposed"] or 0,
                         "accepted": el["accepted"] or 0,
                         "rejected": el["rejected"] or 0},
        })
    return result
