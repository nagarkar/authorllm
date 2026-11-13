"""The register of settled choices: every choice the author has made about a
book, kept with the author's words verbatim and the machine's reading of
them side by side.

A new choice is the author's; keeping to the choices already made is the
machine's rote work. So each choice the author settles (the answers of the
brief, the book's shape, a chapter's thesis, the voice, a story arc, a
term, a claim, a ruling) is one row of `register_entries`, and each piece
of work that rests on a choice is one row of `register_uses`. This module
owns both tables and is their only writer. It calls no model: the reading
is written by whoever proposes the entry and is shown to the author when
the entry is settled.

Decisions:
- An entry holds both the author's words, verbatim, and the machine's
  reading of them, and cannot be settled without both. Why: the machine's
  reading of a choice is not always the author's, and the author must be
  able to see the reading before work rests on it. Enforced by:
  tests/test_register.py::SettleTests::test_settling_needs_the_reading and
  tests/test_register.py::SettleTests::test_settling_needs_the_authors_words.
- Work rests only on a settled entry, and every piece of work that rests
  on one records a use that says whether it applies the entry as written
  or infers something the entry does not say. Why: the author must never
  be told "you told me to do this" about something they did not say.
  Enforced by:
  tests/test_register.py::UseTests::test_a_use_says_applies_or_infers_and_nothing_else
  and
  tests/test_register.py::UseTests::test_work_cannot_rest_on_an_entry_that_is_not_settled.
- No approval is eternal: a settled entry can be reopened, and its words
  or reading can change. A change is never an edit in place: it makes a
  new entry that supersedes the old one, the old one stays readable, and
  the change returns every use of the old one, the work that now needs a
  look. Enforced by:
  tests/test_register.py::ReviseAndReopenTests::test_revising_a_settled_entry_supersedes_it_and_returns_its_uses
  and
  tests/test_register.py::ReviseAndReopenTests::test_reopening_returns_the_work_that_rested_on_the_entry.
- New words the machine has not read back are not settled: revising the
  author's words without a new reading leaves the new entry proposed.
  Enforced by:
  tests/test_register.py::ReviseAndReopenTests::test_new_words_without_a_new_reading_wait_for_the_author.
- A key is unique among the live entries (proposed, settled, reopened) of
  its kind in a manuscript, kept here in code, never by a unique index.
  Enforced by:
  tests/test_register.py::KeyTests::test_a_key_is_unique_among_live_entries_of_its_kind.

Terms:
- **register**: the record of the choices the author has settled for one
  manuscript, the tables `register_entries` and `register_uses`.
- **register entry**: one choice: its kind, its key, the author's words,
  the machine's reading, an optional test, and its scope (the whole book
  or one document).
- **reading**: the machine's restatement, in one to three sentences, of
  what the author's words settle.
- **register use**: a record that a piece of work rests on an entry, in
  one of two modes: `applies` (the work does what the entry says) or
  `infers` (the work goes beyond what the entry says).
"""

from __future__ import annotations

import re

from . import docs
from .db import Database, ko_fields, now_iso

KINDS = ("purpose", "reader", "promise", "comparable", "takeaway", "shape",
         "thesis", "voice", "arc", "term", "claim", "ruling")
# The kinds that together are the book's brief.
BRIEF_KINDS = ("purpose", "reader", "promise", "comparable", "takeaway")
LIVE_STATUSES = ("proposed", "settled", "reopened")
MODES = ("applies", "infers")
ENTRY_PREFIX = "re"
USE_PREFIX = "ru"

_KEY = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def _entry(row) -> dict:
    return {"id": row["id"], "kind": row["kind"], "key": row["key"],
            "author_words": row["author_words"], "reading": row["reading"],
            "test": row["test"], "status": row["status"],
            "scope": row["scope_document_id"],
            "supersedes": row["supersedes"], "settled_at": row["settled_at"],
            "note": row["note"], "created_at": row["created_at"]}


def _use(row) -> dict:
    return {"id": row["id"], "entry_id": row["entry_id"],
            "step_run_id": row["step_run_id"],
            "document_id": row["document_id"], "actor": row["actor"],
            "mode": row["mode"], "note": row["note"],
            "created_at": row["created_at"]}


def _document(db: Database, manuscript: dict, name: str | None) -> str | None:
    """A `dc-` id or a file name -> the `dc-` id of a document of this
    manuscript; None stays None (the whole book)."""
    if not name:
        return None
    if name.startswith("dc-"):
        row = db.one("SELECT id FROM documents WHERE id = ? AND "
                     "manuscript_id = ?", (name, manuscript["id"]))
        if row is None:
            raise LookupError(f"no document '{name}' in this manuscript. "
                              "List them: authorlm doc list")
        return name
    return docs.document_id(db, manuscript, name)


def find(db: Database, manuscript: dict, reference: str) -> dict:
    """The entry named by `reference`: an entry id, the start of one, or
    `kind/key` for the live entry of that kind and key."""
    mid = manuscript["id"]
    if "/" in reference:
        kind, key = reference.split("/", 1)
        rows = db.all(
            "SELECT * FROM register_entries WHERE manuscript_id = ? AND "
            "kind = ? AND key = ? AND status != 'superseded'",
            (mid, kind, key))
    else:
        rows = db.all(
            "SELECT * FROM register_entries WHERE manuscript_id = ? AND "
            "id LIKE ?", (mid, reference.replace("%", "") + "%"))
    if len(rows) != 1:
        found = "no" if not rows else "more than one"
        raise LookupError(f"{found} register entry matches '{reference}'. "
                          "List them: authorlm register list")
    return _entry(rows[0])


def _require_free_key(db: Database, manuscript: dict, kind: str,
                      key: str) -> None:
    taken = db.one(
        "SELECT id, status FROM register_entries WHERE manuscript_id = ? "
        "AND kind = ? AND key = ? AND status != 'superseded'",
        (manuscript["id"], kind, key))
    if taken:
        raise ValueError(
            f"{kind}/{key} is already an entry ({taken['id']}, "
            f"{taken['status']}). Change it: authorlm register revise "
            f"{kind}/{key}")


def add(db: Database, manuscript: dict, kind: str, key: str, *,
        author_words: str = "", reading: str = "", test: str | None = None,
        scope: str | None = None) -> dict:
    """Propose an entry. It is not settled: `settle` does that, once it has
    both the author's words and the reading. `scope` is a document (its
    `dc-` id or file name) for a choice about one chapter, None for one
    about the whole book."""
    if kind not in KINDS:
        raise ValueError(f"'{kind}' is not a kind of register entry. "
                         f"The kinds: {', '.join(KINDS)}")
    if not _KEY.fullmatch(key or ""):
        raise ValueError(
            f"the key '{key}' must be a short slug: lower-case letters and "
            "digits joined by hyphens, e.g. chapter-one")
    _require_free_key(db, manuscript, kind, key)
    row = {**ko_fields(ENTRY_PREFIX), "manuscript_id": manuscript["id"],
           "kind": kind, "key": key, "author_words": author_words,
           "reading": reading, "test": test, "status": "proposed",
           "scope_document_id": _document(db, manuscript, scope)}
    db.insert("register_entries", row)
    return find(db, manuscript, row["id"])


def settle(db: Database, manuscript: dict, reference: str, *,
           author_words: str | None = None, reading: str | None = None,
           test: str | None = None) -> dict:
    """Settle a proposed or reopened entry. A proposed entry may take its
    words, reading and test here. A reopened entry is settled again as it
    stands; new words or a new reading for it go through `revise`, because
    work already rests on the old ones."""
    entry = find(db, manuscript, reference)
    name = f"{entry['kind']}/{entry['key']}"
    if entry["status"] not in ("proposed", "reopened"):
        raise ValueError(f"{name} ({entry['id']}) is {entry['status']}, so "
                         "there is nothing to settle. See it: authorlm "
                         f"register show {entry['id']}")
    changes: dict = {}
    for column, value in (("author_words", author_words),
                          ("reading", reading), ("test", test)):
        if value is not None and value != entry[column]:
            changes[column] = value
    if entry["status"] == "reopened" and (
            "author_words" in changes or "reading" in changes):
        raise ValueError(
            f"{name} was settled before and work may rest on it, so its "
            "words and reading change only by revise: authorlm register "
            f"revise {name} --author-words … --reading …")
    merged = {**entry, **changes}
    if not merged["author_words"].strip():
        raise ValueError(
            f"{name} has no author's words, so it cannot be settled. Give "
            f"them verbatim: authorlm register settle {name} "
            "--author-words '…'")
    if not merged["reading"].strip():
        raise ValueError(
            f"{name} has no reading, so it cannot be settled. Say what the "
            f"words settle: authorlm register settle {name} --reading '…'")
    db.update("register_entries", entry["id"],
              {**changes, "status": "settled", "settled_at": now_iso()})
    return find(db, manuscript, entry["id"])


def uses(db: Database, manuscript: dict, reference: str) -> list[dict]:
    """Every use of one entry, oldest first: the work that rests on it."""
    entry = find(db, manuscript, reference)
    return [_use(r) for r in db.all(
        "SELECT * FROM register_uses WHERE entry_id = ? "
        "ORDER BY created_at, id", (entry["id"],))]


def reopen(db: Database, manuscript: dict, reference: str,
           reason: str | None = None) -> dict:
    """Mark a settled entry reopened; returns `{entry, uses}`, the uses
    being the work that rested on it and now needs a look."""
    entry = find(db, manuscript, reference)
    if entry["status"] != "settled":
        raise ValueError(
            f"{entry['kind']}/{entry['key']} ({entry['id']}) is "
            f"{entry['status']}; only a settled entry can be reopened")
    db.update("register_entries", entry["id"],
              {"status": "reopened", "note": reason})
    return {"entry": find(db, manuscript, entry["id"]),
            "uses": uses(db, manuscript, entry["id"])}


def revise(db: Database, manuscript: dict, reference: str, *,
           author_words: str | None = None, reading: str | None = None,
           test: str | None = None, reason: str | None = None) -> dict:
    """Change an entry's words, reading or test; returns `{entry,
    superseded, uses}`.

    A proposed entry is changed in place (nothing rests on it yet;
    `superseded` is None). A settled or reopened entry is not: a new entry
    takes its kind, key and scope and supersedes it, the old one stays as
    it was, and `uses` lists the work that rested on the old one. The new
    entry is settled, unless the author's words changed and no new reading
    came with them: then it is proposed until `settle` shows the reading."""
    entry = find(db, manuscript, reference)
    name = f"{entry['kind']}/{entry['key']}"
    if entry["status"] == "superseded":
        raise ValueError(f"{entry['id']} was superseded; revise the entry "
                         f"that replaced it: authorlm register show {name}")
    changes = {column: value
               for column, value in (("author_words", author_words),
                                     ("reading", reading), ("test", test))
               if value is not None and value != entry[column]}
    if not changes:
        raise ValueError(f"nothing to revise: {name} already says that")
    if entry["status"] == "proposed":
        db.update("register_entries", entry["id"], changes)
        return {"entry": find(db, manuscript, entry["id"]),
                "superseded": None, "uses": []}
    merged = {**entry, **changes}
    read_back = "author_words" not in changes or "reading" in changes
    settled = (read_back and merged["author_words"].strip()
               and merged["reading"].strip())
    row = {**ko_fields(ENTRY_PREFIX), "manuscript_id": manuscript["id"],
           "kind": entry["kind"], "key": entry["key"],
           "author_words": merged["author_words"],
           "reading": merged["reading"], "test": merged["test"],
           "status": "settled" if settled else "proposed",
           "scope_document_id": entry["scope"], "supersedes": entry["id"],
           "settled_at": now_iso() if settled else None, "note": reason}
    with db.transaction():
        db.update("register_entries", entry["id"], {"status": "superseded"})
        db.insert("register_entries", row)
    return {"entry": find(db, manuscript, row["id"]),
            "superseded": find(db, manuscript, entry["id"]),
            "uses": uses(db, manuscript, entry["id"])}


def use(db: Database, manuscript: dict, reference: str, *, mode: str,
        actor: str, note: str = "", step_run_id: str | None = None,
        document: str | None = None) -> dict:
    """Record that a piece of work rests on a settled entry. `mode` is
    `applies` (the work does what the entry says) or `infers` (the work
    goes beyond what the entry says); `document` is the chapter the work
    is in (its `dc-` id or file name)."""
    if mode not in MODES:
        raise ValueError(f"a use is one of: {', '.join(MODES)}; "
                         f"'{mode}' is neither")
    if not (actor or "").strip():
        raise ValueError("a use names who did the work: --actor "
                         "agent:<role> or human:<who>")
    entry = find(db, manuscript, reference)
    name = f"{entry['kind']}/{entry['key']}"
    if entry["status"] != "settled":
        raise ValueError(
            f"{name} ({entry['id']}) is {entry['status']}, not settled, so "
            f"no work can rest on it. See it: authorlm register show {name}")
    if step_run_id and not db.one(
            "SELECT id FROM step_runs WHERE id = ? AND manuscript_id = ?",
            (step_run_id, manuscript["id"])):
        raise LookupError(f"no step run '{step_run_id}' in this manuscript. "
                          "See the runs: authorlm book status")
    row = {**ko_fields(USE_PREFIX), "manuscript_id": manuscript["id"],
           "entry_id": entry["id"], "step_run_id": step_run_id,
           "document_id": _document(db, manuscript, document),
           "actor": actor.strip(), "mode": mode, "note": note}
    db.insert("register_uses", row)
    return _use(db.one("SELECT * FROM register_uses WHERE id = ?",
                       (row["id"],)))


def list_entries(db: Database, manuscript: dict, *, kind: str | None = None,
                 status: str | None = None,
                 include_superseded: bool = False) -> list[dict]:
    """The manuscript's entries by kind and key; superseded ones only when
    asked for, by `include_superseded` or `status='superseded'`."""
    if kind is not None and kind not in KINDS:
        raise ValueError(f"'{kind}' is not a kind of register entry. "
                         f"The kinds: {', '.join(KINDS)}")
    rows = [_entry(r) for r in db.all(
        "SELECT * FROM register_entries WHERE manuscript_id = ? "
        "ORDER BY created_at, id", (manuscript["id"],))]
    keep = [e for e in rows
            if (kind is None or e["kind"] == kind)
            and (e["status"] == status if status
                 else include_superseded or e["status"] != "superseded")]
    return sorted(keep, key=lambda e: (KINDS.index(e["kind"]), e["key"],
                                       e["created_at"]))


def show(db: Database, manuscript: dict, reference: str) -> dict:
    """One entry with everything that rests on it: `{entry, uses,
    superseded_by}`."""
    entry = find(db, manuscript, reference)
    later = db.one("SELECT id FROM register_entries WHERE supersedes = ?",
                   (entry["id"],))
    return {"entry": entry, "uses": uses(db, manuscript, entry["id"]),
            "superseded_by": later["id"] if later else None}


def in_scope(db: Database, manuscript: dict,
             document_ids: list[str] | None) -> list[dict]:
    """The live entries a role working on `document_ids` is handed: every
    entry about the whole book, and those about these documents. None
    means the whole book, so every live entry."""
    live = list_entries(db, manuscript)
    if document_ids is None:
        return live
    return [e for e in live
            if e["scope"] is None or e["scope"] in document_ids]
