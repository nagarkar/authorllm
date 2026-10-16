"""`sessions.episode_for_intent` must find an intent's open episode in ANY
session, not only the current one.

`declare_intent` opens an episode while a session is live; work the next
day must still hang off that episode. Scoping to the current session (the
`current_episode` mistake) silently attaches transitions to whichever
intent was declared last (design-intent-scope §1.7). No suite on main
calls this helper directly.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.db import Database, ko_fields
from authorlm import sessions


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def _db() -> Database:
    root = Path(tempfile.mkdtemp(prefix="authorlm-episode-"))
    return Database(root / "authorlm.db")


def _session(db: Database, mid: str, started: str) -> dict:
    row = ko_fields("s")
    row.update(manuscript_id=mid, started_at=started, ended_at=None,
               status="active")
    db.insert("sessions", row)
    return row


def _intent(db: Database, mid: str, session_id: str) -> dict:
    row = ko_fields("di")
    row.update(manuscript_id=mid, session_id=session_id,
               statement="finish the bridge", status="active",
               outcome=None, scope=None, source_id=None)
    db.insert("declared_intents", row)
    return row


def _episode(db: Database, mid: str, session_id: str, intent_id: str) -> dict:
    row = ko_fields("ep")
    row.update(manuscript_id=mid, session_id=session_id, intent_id=intent_id,
               transition_ids="[]", outcome=None, status="open")
    db.insert("editorial_episodes", row)
    return row


def test_finds_yesterday_episode_from_today_session() -> None:
    db = _db()
    mid = "m-1"
    yesterday = _session(db, mid, "2026-01-01T10:00:00Z")
    intent = _intent(db, mid, yesterday["id"])
    prior = _episode(db, mid, yesterday["id"], intent["id"])
    today = _session(db, mid, "2026-01-02T10:00:00Z")

    got = sessions.episode_for_intent(db, mid, today, intent["id"])
    check("returns the open episode opened yesterday",
          got["id"] == prior["id"], got)
    check("does not invent a new episode in today's session",
          got["session_id"] == yesterday["id"], got)


def test_mints_when_no_open_episode() -> None:
    db = _db()
    mid = "m-1"
    today = _session(db, mid, "2026-01-02T10:00:00Z")
    intent = _intent(db, mid, today["id"])

    got = sessions.episode_for_intent(db, mid, today, intent["id"])
    check("a missing open episode is created in the current session",
          got["session_id"] == today["id"]
          and got["intent_id"] == intent["id"]
          and got["status"] == "open",
          got)
    check("transition_ids starts empty",
          json.loads(got["transition_ids"]) == [])


def test_ignores_closed_episode() -> None:
    db = _db()
    mid = "m-1"
    old = _session(db, mid, "2026-01-01T10:00:00Z")
    intent = _intent(db, mid, old["id"])
    closed = _episode(db, mid, old["id"], intent["id"])
    db.update("editorial_episodes", closed["id"],
              {"status": "closed", "outcome": "done"})
    today = _session(db, mid, "2026-01-02T10:00:00Z")

    got = sessions.episode_for_intent(db, mid, today, intent["id"])
    check("a closed episode is not reused",
          got["id"] != closed["id"], got)
    check("a fresh open episode is minted instead",
          got["status"] == "open" and got["session_id"] == today["id"], got)


def test_attach_transitions_appends() -> None:
    db = _db()
    mid = "m-1"
    sess = _session(db, mid, "2026-01-02T10:00:00Z")
    intent = _intent(db, mid, sess["id"])
    ep = _episode(db, mid, sess["id"], intent["id"])
    sessions.attach_transitions(db, ep, [{"id": "t1"}, {"id": "t2"}])
    row = db.one("SELECT transition_ids FROM editorial_episodes WHERE id = ?",
                 (ep["id"],))
    check("attach_transitions appends ids in order",
          json.loads(row["transition_ids"]) == ["t1", "t2"],
          row["transition_ids"])
    sessions.attach_transitions(db, {**ep, "transition_ids": row["transition_ids"]},
                                [{"id": "t3"}])
    row = db.one("SELECT transition_ids FROM editorial_episodes WHERE id = ?",
                 (ep["id"],))
    check("a second attach extends rather than replacing",
          json.loads(row["transition_ids"]) == ["t1", "t2", "t3"],
          row["transition_ids"])


def main() -> None:
    for t in (test_finds_yesterday_episode_from_today_session,
              test_mints_when_no_open_episode,
              test_ignores_closed_episode,
              test_attach_transitions_appends):
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
