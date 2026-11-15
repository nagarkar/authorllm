"""Chapter files as documents: a document's identity is its `dc-` id (the
`documents` table), and its relpath is a display name with history. This
module owns that identity, the rename that carries a document's records
to a new name, and the mechanical edits (add, retire, revive; RFC §16.1
Level 3).

Every other record still names a chapter by its relpath, so a rename, by
the `doc rename` verb or one made in
Finder or Obsidian and detected at the next collect, carries those
records to the new name through one function, `carry_rename`: the tables
in NAME_COLUMNS, NAME_DERIVED_COLUMNS and NAME_JSON_COLUMNS, the Doc link
(same tab), toc.toml, the audio and merge files named by the file's stem,
and the developmental edit runs. What is recorded history keeps the name
it was recorded under (NAME_HISTORY_COLUMNS: version snapshots and the
transitions that point into them); a reader of an old snapshot finds the
document through its name history (`names_of`, `snapshot_text`).

Document ids are a per-manuscript setting, `document_ids` (on | off), kept
in `manuscripts.metadata` as `{"document_ids": {"enabled": true}}`
(`document_ids_enabled`, `set_document_ids`; `authorlm manuscript set
--document-ids on|off`). Registration writes it on, so a new manuscript has
ids from its first collect; a manuscript whose metadata has no such key
existed before the setting and is off. While it is off nothing in this
module writes: no document row is seeded, a collect pairs and carries no
rename (a file renamed on disk is a removal and an addition, and the
auto-collect deletion guard holds it, exactly as before documents had
ids), and `doc rename` is declined with the command that turns the
setting on. Turning it on is the one migration moment: a backup of the
workspace database is taken first (`backup.run`), then the rows are
seeded.

Document rows are created lazily (`ensure_documents`) for every file that
is both in the latest snapshot and on disk, so a rename waiting for its
collect is never mistaken for a new document. At most one ACTIVE row per
(manuscript, relpath), kept here in code: a retired or removed file keeps
its row and its last name.

Creating, retiring, and reviving chapter files are mechanical edits — safe
to execute directly, unlike conceptual edits which remain suggestions.
Retiring moves a file into `_retired/` (invisible to observation, like all
underscore directories), so the next collection records its removal while
every historical version and transition remains replayable. Reviving moves
it back. Nothing is ever deleted.

Decisions:
- A chapter has a stable identifier independent of the name the author
  sees, so it can be renamed cheaply: no record is lost or orphaned by a
  rename. Enforced by:
  tests/test_document_identity.py::RenameVerbTests::test_rename_carries_every_record_to_the_new_name
  and
  tests/test_document_identity.py::RenameOnDiskTests::test_a_rename_on_disk_heals_at_the_next_collect.
- An existing manuscript is untouched until its owner turns document ids
  on, after a backup: with the setting off a collect behaves as it did
  before documents had ids. Enforced by:
  tests/test_document_identity.py::DocumentIdsSwitchTests::test_off_a_rename_on_disk_collects_exactly_as_it_did_before
  and
  tests/test_document_identity.py::DocumentIdsSwitchTests::test_turning_it_on_backs_up_first_then_seeds_every_file.
- `doc rename` is declined while the file is checked out to Google Docs,
  has a live filter run, a writeup in flight or pending forms: each holds
  state tied to the name while it runs. A rename made outside AuthorLM in
  one of those states cannot be declined and is carried all the same.
  Enforced by: tests/test_document_identity.py::RenameDeclinedTests.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .db import Database, ko_fields, loads, now_iso
from .revisions import MANUSCRIPT_EXTENSIONS, iter_manuscript_paths

RETIRED_DIR = "_retired"
_UNSAFE = re.compile(r'[\\/:*?"<>|#]')

# ----------------------------------------------- what a rename carries
#
# Every column that holds a chapter's relpath is in exactly one of these
# four lists (tests/test_document_identity.py::SchemaTripwireTests fails
# on a column that is in none).

# Plain relpath columns: rewritten old -> new. The two developmental edit
# tables are written by `devedit.rename_file`, their only writer.
NAME_COLUMNS = (
    ("style_attachments", "file"),
    ("style_laws", "file"),
    ("declared_intents", "scope"),
    ("writeups", "file"),
    ("essay_summaries", "file"),
    ("paragraph_summaries", "file"),
    ("section_summaries", "file"),
    ("filter_runs", "file"),
    ("filter_readings", "file"),
    ("illus_proposals", "file"),
    ("doc_comments", "file"),
    ("doc_threads", "file"),
    ("concept_nodes", "introduced_in"),
    ("devedit_items", "file"),
    ("edit_events", "file"),
)
_DEVEDIT_TABLES = ("devedit_items", "edit_events")

# Columns that embed the relpath in a longer value: `file#heading`,
# `{owner}:{file}:{ordinal}`, and a scope reference that is a relpath
# when the scope is a file.
NAME_DERIVED_COLUMNS = (
    ("doc_comments", "location"),
    ("doc_threads", "origin_id"),
    ("doc_threads", "scope_ref"),
)

# JSON columns keyed by, or holding, the relpath.
NAME_JSON_COLUMNS = (
    ("critique_passes", "essay_state"),
    ("critique_passes", "pinned_versions"),
    ("guidance_history", "metadata"),     # file, target_file, dedupe_key
    ("manuscripts", "metadata"),          # gdocs links, by gdocs.rename_link
)

# Recorded history: keeps the name it was recorded under.
NAME_HISTORY_COLUMNS = (
    ("manuscript_versions", "files"),
    ("illustration_prompt_versions", "files"),
    ("editorial_transitions", "location"),
)


def doc_filename(name: str) -> str:
    cleaned = _UNSAFE.sub("-", name.strip()).strip()
    cleaned = re.sub(r"\s+", "-", cleaned)
    if not cleaned:
        raise ValueError("empty document name")
    if not cleaned.lower().endswith((".md", ".markdown", ".txt")):
        cleaned += ".md"
    return cleaned


def _match(candidates: dict[str, Path], query: str) -> Path:
    """Exact relative-path match, else unique case-insensitive substring."""
    if query in candidates:
        return candidates[query]
    hits = [p for rel, p in candidates.items() if query.lower() in rel.lower()]
    if not hits:
        raise LookupError(f"no document matching '{query}'")
    if len(hits) > 1:
        names = ", ".join(sorted(p.name for p in hits))
        raise LookupError(f"'{query}' is ambiguous: {names}")
    return hits[0]


def _resolve_relpath(manuscript: dict, query: str) -> str:
    """Map a file argument to the manuscript-relative path used as the
    Doc-mapping key and the on-disk name. Absolute paths that sit under
    the manuscript root (and relative paths that resolve there) are
    accepted; anything outside is refused. Bare names still go through
    `_match`'s exact-then-unique-substring forgiveness."""
    root = Path(manuscript["path"])
    candidates = iter_manuscript_paths(root)
    probe = Path(query).expanduser()
    try:
        resolved = (probe if probe.is_absolute() else root / probe).resolve()
        rel = str(resolved.relative_to(root.resolve()))
    except (ValueError, OSError):
        rel = None
    if rel is not None and rel in candidates:
        return rel
    path = _match(candidates, query)
    return str(path.relative_to(root))


def list_docs(db: Database, manuscript: dict) -> dict:
    root = Path(manuscript["path"])
    concepts_by_file: dict[str, list[str]] = {}
    for row in db.all(
        "SELECT name, introduced_in FROM concept_nodes WHERE manuscript_id = ? "
        "AND status != 'retired' AND introduced_in IS NOT NULL",
        (manuscript["id"],),
    ):
        concepts_by_file.setdefault(row["introduced_in"], []).append(row["name"])
    active = []
    for rel, path in iter_manuscript_paths(root).items():
        text = path.read_text(encoding="utf-8")
        paragraphs = len([p for p in re.split(r"\n\s*\n", text) if p.strip()])
        active.append({"file": rel, "paragraphs": paragraphs,
                       "concepts": concepts_by_file.get(rel, [])})
    retired_root = root / RETIRED_DIR
    retired = (
        sorted(p.name for p in retired_root.iterdir() if p.is_file())
        if retired_root.is_dir() else []
    )
    return {"active": active, "retired": retired}


ABOUT_THE_BOOK_WORDS = ("synopsis", "pitch", "market", "audience",
                        "perspective", "charter")


def about_the_book_hint(name: str) -> str | None:
    """A one-line hint when NAME looks like a file about the book rather
    than part of it; such content belongs in a profile, not a tab."""
    stem = Path(name).stem.lower()
    for word in ABOUT_THE_BOOK_WORDS:
        if word in stem:
            return (f"'{name}' looks like a file about the book ({word}): "
                    "consider 'authorlm profile set <key>' and a registry "
                    "entry instead of a chapter tab.")
    return None


def add_doc(manuscript: dict, name: str, title: str | None = None,
            after: str | None = None) -> Path:
    """Scaffold a new essay file and, when the manuscript has a toc.toml,
    register it there: after `after` (a listed file, or 'start' for the
    head), else at the end. Without the toc entry the next Doc push adds
    a tab that toc.toml does not list, and the pull reads that as a TOC
    order conflict. An `after` that names no listed file writes nothing."""
    from .structure import TOC_FILENAME, _toc_chapters, insert_toc_entry

    root = Path(manuscript["path"])
    filename = doc_filename(name)
    target = root / filename
    if target.exists():
        raise FileExistsError(f"{filename} already exists")
    if (root / RETIRED_DIR / filename).exists():
        raise FileExistsError(
            f"{filename} exists in {RETIRED_DIR}/ — revive it instead: doc revive {filename}"
        )
    toc_path = root / TOC_FILENAME
    new_toc = None
    if toc_path.exists():
        toc_text = toc_path.read_text(encoding="utf-8")
        if after is None:
            listed = [c["file"] for c in _toc_chapters(toc_text)
                      if c.get("file")]
            after = listed[-1] if listed else None
        outcome = insert_toc_entry(toc_text, filename, after)
        if outcome is None:
            raise LookupError(f"--after {after}: not a file in {TOC_FILENAME}")
        new_toc = outcome[0]
    heading = title or target.stem.replace("-", " ").replace("_", " ").strip()
    target.write_text(f"# {heading}\n\n", encoding="utf-8")
    if new_toc is not None:
        toc_path.write_text(new_toc, encoding="utf-8")
    return target


def retire_doc(manuscript: dict, query: str) -> Path:
    root = Path(manuscript["path"])
    source = _match(iter_manuscript_paths(root), query)
    retired_root = root / RETIRED_DIR
    retired_root.mkdir(exist_ok=True)
    target = retired_root / source.name
    if target.exists():
        raise FileExistsError(f"{RETIRED_DIR}/{source.name} already exists")
    source.rename(target)
    return target


def revive_doc(manuscript: dict, query: str) -> Path:
    root = Path(manuscript["path"])
    retired_root = root / RETIRED_DIR
    candidates = {
        p.name: p for p in (retired_root.iterdir() if retired_root.is_dir() else [])
        if p.is_file()
    }
    source = _match(candidates, query)
    target = root / source.name
    if target.exists():
        raise FileExistsError(f"{source.name} already exists in the manuscript")
    source.rename(target)
    return target


# ------------------------------------------------------------ identity

SETTING = "document_ids"


def _turn_on_hint(manuscript: dict) -> str:
    return (f"authorlm manuscript set --document-ids on "
            f"-m {manuscript.get('name', '<manuscript>')}")


def document_ids_enabled(db: Database, manuscript: dict) -> bool:
    """The per-manuscript `document_ids` setting, kept in
    `manuscripts.metadata` as `{"document_ids": {"enabled": true}}`.
    ABSENT MEANS OFF: registration writes it on, so a manuscript with no
    such key is one that existed before the setting, and it stays as it
    was until its owner turns the setting on.

    Read from the row, never from the caller's dict, for the reason
    `extraction.manuscript_extraction_enabled` gives: the Doc mapping
    rewrites that column under a loaded manuscript."""
    row = db.one("SELECT metadata FROM manuscripts WHERE id = ?",
                 (manuscript["id"],))
    meta = loads(row["metadata"] if row else None, {})
    return bool((meta.get(SETTING) or {}).get("enabled", False))


def _write_setting(db: Database, manuscript: dict, enabled: bool) -> None:
    """Merged into the row's current metadata (the Doc mapping and the
    extraction switch live beside it) and mirrored into the caller's
    dict, as `extraction.set_manuscript_extraction` does."""
    row = db.one("SELECT metadata FROM manuscripts WHERE id = ?",
                 (manuscript["id"],))
    meta = loads(row["metadata"] if row else None, {})
    meta[SETTING] = {**(meta.get(SETTING) or {}), "enabled": bool(enabled)}
    encoded = json.dumps(meta)
    db.update("manuscripts", manuscript["id"], {"metadata": encoded})
    manuscript["metadata"] = encoded


def set_document_ids(db: Database, manuscript: dict, enabled: bool) -> dict:
    """Turn the `document_ids` setting on or off; returns `{enabled,
    changed, backup, seeded}`.

    Turning it on for a manuscript that has it off is the migration
    moment, and the only one: the workspace database is backed up first
    (`backup.run`, as a session start does; `backup` is the path of the
    copy, or None when the newest backup already equals the database),
    then every file gets its document row (`seeded`, their names). A
    backup that fails leaves the setting off and nothing written.
    Turning it off keeps the rows already made: they are a record, and
    turning it on again takes them back up."""
    from . import backup

    was = document_ids_enabled(db, manuscript)
    report = {"enabled": bool(enabled), "changed": was != bool(enabled),
              "backup": None, "seeded": []}
    if not enabled or was:
        _write_setting(db, manuscript, enabled)
        return report
    result = backup.run(db)
    if not result.get("ok"):
        raise ValueError(
            "document ids stay off: the backup that must come first failed "
            f"({result.get('error')}); the backups go to "
            f"{backup.backup_dir(db.path)}")
    report["backup"] = result.get("path")
    _write_setting(db, manuscript, True)
    report["seeded"] = sorted(r["relpath"]
                              for r in ensure_documents(db, manuscript))
    return report

def _history(row) -> list[dict]:
    return loads(row["names"], [])


def _document_rows(db: Database, manuscript_id: str) -> list[dict]:
    return [dict(r) for r in db.all(
        "SELECT * FROM documents WHERE manuscript_id = ? "
        "ORDER BY created_at, id", (manuscript_id,))]


def _row_named(db: Database, manuscript_id: str, relpath: str):
    """The document carrying `relpath` now: the active one, else the most
    recently created retired one. None when no document has that name."""
    rows = [r for r in _document_rows(db, manuscript_id)
            if r["relpath"] == relpath]
    active = [r for r in rows if r["status"] == "active"]
    return (active or rows[::-1] or [None])[0]


def _new_document(db: Database, manuscript_id: str, relpath: str,
                  status: str, names: list[dict] | None = None) -> dict:
    row = ko_fields("dc")
    row.update(manuscript_id=manuscript_id, relpath=relpath, status=status,
               names=json.dumps(names or [
                   {"relpath": relpath, "from": row["created_at"],
                    "to": None}]))
    db.insert("documents", row)
    return row


def _renamed_history(row: dict, new: str) -> str:
    """The row's name history with `new` in force from now."""
    now = now_iso()
    names = _history(row)
    if names and names[-1].get("to") is None:
        names[-1]["to"] = now
    names.append({"relpath": new, "from": now, "to": None})
    return json.dumps(names)


def ensure_documents(db: Database, manuscript: dict) -> list[dict]:
    """Give every manuscript file its document row; returns the rows
    created. Lazy and idempotent: a second call creates nothing.

    A file gets a row when it is in the latest snapshot AND on disk. A
    file in only one of the two is waiting for a collect (a new file, a
    removal, or one half of a rename made outside AuthorLM) and is left
    for that collect to settle, so a rename is never seeded as a second
    document. A document whose file is in neither is retired; a file
    under `_retired/` no document accounts for is seeded retired; a file
    that comes back (revived, or added again) takes its retired row and
    id back. toc.toml is structure, not a document. Writes nothing while
    the manuscript's `document_ids` setting is off."""
    from .structure import TOC_FILENAME

    root = Path(manuscript["path"])
    if not root.is_dir() or not document_ids_enabled(db, manuscript):
        return []
    mid = manuscript["id"]
    latest = db.one(
        "SELECT files FROM manuscript_versions WHERE manuscript_id = ? "
        "ORDER BY version_no DESC LIMIT 1", (mid,))
    snapshot = set(loads(latest["files"], {})) if latest else set()
    on_disk = set(iter_manuscript_paths(root))
    retired_root = root / RETIRED_DIR
    in_retired = {p.name for p in (retired_root.iterdir()
                                   if retired_root.is_dir() else [])
                  if p.is_file() and p.suffix.lower() in MANUSCRIPT_EXTENSIONS}
    rows = _document_rows(db, mid)
    active = {r["relpath"]: r for r in rows if r["status"] == "active"}
    retired = [r for r in rows if r["status"] == "retired"]
    # Decide first, write second: a call with nothing to do (nearly every
    # call) takes no write lock.
    updates: list[tuple[str, dict]] = []
    inserts: list[tuple[str, str]] = []
    for name in sorted((snapshot & on_disk) - {TOC_FILENAME}):
        if name in active:
            continue
        back = ([r for r in retired if r["relpath"] == name]
                or [r for r in retired
                    if Path(r["relpath"]).name == Path(name).name
                    and Path(name).name not in in_retired])
        if back:
            row = back[-1]
            retired.remove(row)
            changes = {"status": "active"}
            if row["relpath"] != name:
                changes.update(relpath=name,
                               names=_renamed_history(row, name))
            updates.append((row["id"], changes))
        else:
            inserts.append((name, "active"))
    for row in active.values():
        if row["relpath"] not in snapshot and row["relpath"] not in on_disk:
            updates.append((row["id"], {"status": "retired"}))
    known = {Path(r["relpath"]).name for r in rows}
    known |= {Path(name).name for name, _status in inserts}
    inserts += [(name, "retired") for name in sorted(in_retired - known)]
    if not updates and not inserts:
        return []
    with db.transaction():
        for row_id, changes in updates:
            db.update("documents", row_id, changes)
        return [_new_document(db, mid, name, status)
                for name, status in inserts]


def document_id(db: Database, manuscript: dict, relpath: str) -> str:
    """relpath -> the `dc-` id of the document carrying that name now."""
    row = _row_named(db, manuscript["id"], relpath)
    if row is None:
        ensure_documents(db, manuscript)
        row = _row_named(db, manuscript["id"], relpath)
    if row is None and not document_ids_enabled(db, manuscript):
        raise LookupError(
            f"no document named '{relpath}': document ids are off for this "
            f"manuscript. Turn them on: {_turn_on_hint(manuscript)}")
    if row is None:
        raise LookupError(
            f"no document named '{relpath}'. A new or renamed file gets "
            "its document at the next collect: run 'authorlm collect'.")
    return row["id"]


def document_relpath(db: Database, document_id: str) -> str:
    """A `dc-` id -> the relpath the document carries now."""
    row = db.one("SELECT relpath FROM documents WHERE id = ?", (document_id,))
    if row is None:
        raise LookupError(f"no document '{document_id}'")
    return row["relpath"]


def list_documents(db: Database, manuscript: dict) -> list[dict]:
    """Every document of the manuscript, by name: `{id, relpath, status,
    names}`, `names` being the history oldest first."""
    ensure_documents(db, manuscript)
    return [{"id": r["id"], "relpath": r["relpath"], "status": r["status"],
             "names": _history(r)}
            for r in sorted(_document_rows(db, manuscript["id"]),
                            key=lambda r: (r["relpath"], r["created_at"]))]


def names_of(db: Database, manuscript_id: str, relpath: str) -> list[str]:
    """Every name the document now called `relpath` has carried: that
    name first, then the earlier ones, newest first. `[relpath]` for a
    file with no document row yet."""
    row = _row_named(db, manuscript_id, relpath)
    names = [relpath]
    for entry in reversed(_history(row) if row else []):
        if entry.get("relpath") and entry["relpath"] not in names:
            names.append(entry["relpath"])
    return names


def snapshot_text(db: Database, manuscript_id: str, files: dict[str, str],
                  relpath: str) -> str | None:
    """The text of the document now called `relpath` in an old snapshot
    (`files`: name -> text), under whichever of its names the snapshot
    holds. None when the snapshot holds none of them."""
    for name in names_of(db, manuscript_id, relpath):
        if name in files:
            return files[name]
    return None


def earlier_name(db: Database, manuscript_id: str, relpath: str,
                 old_files: dict[str, str],
                 new_files: dict[str, str]) -> str | None:
    """The name under which an older snapshot holds the document that a
    newer snapshot calls `relpath`: a former name present in `old_files`
    and gone from `new_files`. None when the older snapshot already has
    `relpath`, or holds none of its names."""
    if relpath in old_files:
        return None
    return next((name for name in names_of(db, manuscript_id, relpath)[1:]
                 if name in old_files and name not in new_files), None)


# -------------------------------------------------------------- rename

class DiskChanges:
    """The file moves and rewrites one rename makes, done as they are
    asked for and undone together, newest first, when the rename fails.
    With `apply=False` (a preview) nothing is touched and `paths` still
    lists what would be."""

    def __init__(self, root: Path, apply: bool = True):
        self.root = root
        self.apply = apply
        self.paths: list[str] = []
        self._done: list[tuple] = []

    def _note(self, path: Path) -> None:
        rel = str(path.relative_to(self.root))
        if rel not in self.paths:
            self.paths.append(rel)

    def move(self, source: Path, target: Path) -> None:
        self._note(target)
        if not self.apply:
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        source.rename(target)
        self._done.append(("move", source, target))

    def write(self, path: Path, text: str) -> None:
        """Replace a file's text (temp file + rename, so a reader never
        sees half of it)."""
        self._note(path)
        if not self.apply:
            return
        previous = path.read_bytes() if path.exists() else None
        temporary = path.with_name(path.name + ".rename-tmp")
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)
        self._done.append(("write", path, previous))

    def undo(self) -> None:
        for kind, first, second in reversed(self._done):
            if kind == "move":
                second.rename(first)
            elif second is None:
                first.unlink(missing_ok=True)
            else:
                first.write_bytes(second)
        self._done.clear()


class _Preview(Exception):
    """Raised inside the transaction of a preview so every row it wrote
    is rolled back."""


def _rename_document_row(db: Database, mid: str, old: str, new: str) -> str:
    """Point `old`'s document at `new`, keeping its id and adding the name
    to its history; returns the id. A second call for the same rename
    finds it done. A document still marked active under `new` belongs to
    a file that is gone (the name is being taken now) and is retired."""
    rows = _document_rows(db, mid)
    source = next((r for r in rows
                   if r["relpath"] == old and r["status"] == "active"), None)
    holders = [r for r in rows
               if r["relpath"] == new and r["status"] == "active"]
    if source is None:
        done = next((r for r in holders
                     if [n.get("relpath") for n in _history(r)][-2:-1] == [old]),
                    None)
        if done is not None:
            return done["id"]
    for row in holders:
        db.update("documents", row["id"], {"status": "retired"})
    if source is None:
        now = now_iso()
        return _new_document(db, mid, new, "active", names=[
            {"relpath": old, "from": now, "to": now},
            {"relpath": new, "from": now, "to": None}])["id"]
    db.update("documents", source["id"],
              {"relpath": new, "names": _renamed_history(source, new)})
    return source["id"]


def _carry_rows(db: Database, mid: str, old: str, new: str,
                report: dict) -> None:
    """Rewrite the relpath in every table that stores it. A row that
    cannot take the new name because one is already there (a UNIQUE key
    held by an earlier file of that name) stays under the old name and is
    counted in `report["left"]`."""
    def moved(table: str, column: str, cursor) -> None:
        if cursor.rowcount:
            report["records"][f"{table}.{column}"] = (
                report["records"].get(f"{table}.{column}", 0)
                + cursor.rowcount)

    for table, column in NAME_COLUMNS:
        if table in _DEVEDIT_TABLES:
            continue
        moved(table, column, db.conn.execute(
            f"UPDATE OR IGNORE {table} SET {column} = ?, "
            f"version = version + 1 WHERE manuscript_id = ? AND {column} = ?",
            (new, mid, old)))
        stayed = db.one(
            f"SELECT COUNT(*) AS n FROM {table} WHERE manuscript_id = ? "
            f"AND {column} = ?", (mid, old))["n"]
        if stayed:
            report["left"].append(
                f"{stayed} {table} row(s): '{new}' already has them")

    # doc_comments.location: `file` or `file#heading`.
    for row in db.all(
            "SELECT id, location FROM doc_comments WHERE manuscript_id = ? "
            "AND location IS NOT NULL", (mid,)):
        name, mark, heading = row["location"].partition("#")
        if name == old:
            moved("doc_comments", "location", db.conn.execute(
                "UPDATE doc_comments SET location = ? WHERE id = ?",
                (new + mark + heading, row["id"])))

    # doc_threads.origin_id `{owner}:{file}:{tail}` (every producer but an
    # author's comment, whose origin is the Drive comment id), and the
    # scope reference of a file-scoped thread.
    for row in db.all(
            "SELECT id, origin_id FROM doc_threads WHERE manuscript_id = ? "
            "AND origin_type != 'author_comment'", (mid,)):
        owner, mark, rest = row["origin_id"].partition(":")
        if mark and rest.startswith(old + ":"):
            cursor = db.conn.execute(
                "UPDATE OR IGNORE doc_threads SET origin_id = ? WHERE id = ?",
                (f"{owner}:{new}:{rest[len(old) + 1:]}", row["id"]))
            moved("doc_threads", "origin_id", cursor)
            if not cursor.rowcount:
                report["left"].append(
                    f"doc_threads {row['origin_id']}: '{new}' already "
                    "has that thread")
    moved("doc_threads", "scope_ref", db.conn.execute(
        "UPDATE doc_threads SET scope_ref = ? WHERE manuscript_id = ? "
        "AND scope_kind = 'file' AND scope_ref = ?", (new, mid, old)))

    # critique_passes: two JSON objects keyed by file.
    for row in db.all(
            "SELECT id, essay_state, pinned_versions FROM critique_passes "
            "WHERE manuscript_id = ?", (mid,)):
        for column in ("essay_state", "pinned_versions"):
            value = loads(row[column], {})
            if old in value and new not in value:
                renamed = {(new if k == old else k): v
                           for k, v in value.items()}
                moved("critique_passes", column, db.conn.execute(
                    f"UPDATE critique_passes SET {column} = ? WHERE id = ?",
                    (json.dumps(renamed), row["id"])))

    # guidance_history.metadata: file, target_file, and a dedupe key that
    # embeds the relpath (`kind:producer:file:index`, `…:file#…`).
    in_key = re.compile(rf"(?<=:){re.escape(old)}(?=[:#]|$)")
    for row in db.all(
            "SELECT id, metadata FROM guidance_history WHERE "
            "manuscript_id = ? AND instr(metadata, ?) > 0", (mid, old)):
        meta = loads(row["metadata"], {})
        renamed = dict(meta)
        for key in ("file", "target_file"):
            if renamed.get(key) == old:
                renamed[key] = new
        if isinstance(renamed.get("dedupe_key"), str):
            renamed["dedupe_key"] = in_key.sub(
                lambda _m: new, renamed["dedupe_key"])
        if renamed != meta:
            moved("guidance_history", "metadata", db.conn.execute(
                "UPDATE guidance_history SET metadata = ? WHERE id = ?",
                (json.dumps(renamed), row["id"])))


def _carry_audio(root: Path, old: str, new: str, changes: DiskChanges,
                 report: dict) -> None:
    """The audiobook folder (audio.py's layout): the author's
    `[[chapter]] file` and retail-sample entries in audiobook.toml, the
    chapter's entry in audiobook.json, and the files named by the stem:
    `chapters/<stem>.json`, `state/<stem>.json` (audiostation's takes,
    which stay done: a take is keyed by its section id, not the file) and
    the stitched `audio/<stem>.<quality>.mp3` the state points at."""
    from .generation import sanitise_filename

    audio = root / "_audio"
    if not audio.is_dir():
        return
    old_stem, new_stem = Path(old).stem, Path(new).stem

    config = audio / "audiobook.toml"
    if config.exists():
        text = config.read_text(encoding="utf-8")
        renamed = re.sub(
            rf'^(\s*file\s*=\s*)(["\']){re.escape(old)}\2(\s*)$',
            lambda m: f"{m.group(1)}{m.group(2)}{new}{m.group(2)}{m.group(3)}",
            text, flags=re.M)
        for was, now in ((old, new), (old_stem, new_stem)):
            renamed = re.sub(
                rf'^(\s*chapter\s*=\s*)(["\']){re.escape(was)}\2(\s*)$',
                lambda m, now=now: (f"{m.group(1)}{m.group(2)}{now}"
                                    f"{m.group(2)}{m.group(3)}"),
                renamed, flags=re.M)
        if renamed != text:
            changes.write(config, renamed)

    def rewrite_json(path: Path, patch, read_from: Path | None = None) -> None:
        """Patch a JSON file in place. `read_from` is where its text is
        now when a preview has left it unmoved."""
        try:
            value = json.loads((read_from or path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        before = json.dumps(value, sort_keys=True)
        patch(value)
        if json.dumps(value, sort_keys=True) != before:
            changes.write(path, json.dumps(value, indent=2,
                                           ensure_ascii=False) + "\n")

    def move(folder: str, was: str, now: str) -> tuple[Path, Path] | None:
        """Move `_audio/<folder>/<was>` to `<now>`; `(its new path, where
        its text can be read)`, or None when there is none to carry."""
        source, target = audio / folder / was, audio / folder / now
        if not source.exists():
            return None
        if source == target:
            return source, source
        if target.exists():
            report["left"].append(
                f"_audio/{folder}/{was}: _audio/{folder}/{now} already "
                "exists")
            return None
        changes.move(source, target)
        return target, (target if changes.apply else source)

    chapter = move("chapters", f"{old_stem}.json", f"{new_stem}.json")
    if chapter is not None:
        def patch_chapter(value: dict) -> None:
            if value.get("file") == old:
                value["file"] = new
            if value.get("stem") == old_stem:
                value["stem"] = new_stem
        rewrite_json(chapter[0], patch_chapter, read_from=chapter[1])

    state = move("state", f"{old_stem}.json", f"{new_stem}.json")
    if state is not None and old_stem != new_stem:
        was_name = sanitise_filename(old_stem)
        now_name = sanitise_filename(new_stem)

        def patch_state(value: dict) -> None:
            stitched = value.get("stitched")
            for quality, rel in list((stitched or {}).items()):
                if rel == f"audio/{was_name}.{quality}.mp3" and move(
                        "audio", f"{was_name}.{quality}.mp3",
                        f"{now_name}.{quality}.mp3") is not None:
                    stitched[quality] = f"audio/{now_name}.{quality}.mp3"
        rewrite_json(state[0], patch_state, read_from=state[1])

    def patch_book(value: dict) -> None:
        was_file = f"chapters/{old_stem}.json"
        now_file = f"chapters/{new_stem}.json"
        for entry in value.get("chapters") or []:
            if isinstance(entry, dict) and entry.get("stem") == old_stem:
                entry["stem"] = new_stem
                if entry.get("file") == was_file:
                    entry["file"] = now_file
        for key in ("aboutAuthor", "openingCredits", "closingCredits"):
            if value.get(key) == was_file:
                value[key] = now_file
    if old_stem != new_stem:
        rewrite_json(audio / "audiobook.json", patch_book)


def carry_rename(db: Database, manuscript: dict, old: str, new: str, *,
                 move_file: bool = False, apply: bool = True) -> dict:
    """Carry everything that names the chapter `old` to `new`: the one
    function behind `doc rename` and a rename detected at collect.

    In one transaction: the document row (same id, the name added to its
    history), every table and JSON column that stores the relpath, the
    Doc link (same tab; Google is not called), toc.toml, the audiobook
    and merge files named by the stem, and the developmental edit runs.
    `move_file` also renames the file itself (the verb); a rename made
    outside AuthorLM has already moved it. A failure undoes the file
    changes and rolls the rows back. Safe to call twice: the second call
    finds nothing left under the old name. `apply=False` previews: the
    same report, nothing written.

    Returns `{from, to, document, records, files, left, tab_title_stale}`:
    `records` counts rows per `table.column`, `files` lists the files
    moved or rewritten, `left` what stayed under the old name because the
    new name was already taken there."""
    from . import devedit, gdocs
    from .structure import TOC_FILENAME, rename_toc_file

    root = Path(manuscript["path"])
    mid = manuscript["id"]
    report: dict = {"from": old, "to": new, "document": None, "records": {},
                    "files": [], "left": [], "tab_title_stale": False}
    changes = DiskChanges(root, apply)
    old_stem, new_stem = Path(old).stem, Path(new).stem
    try:
        with db.transaction():
            report["document"] = _rename_document_row(db, mid, old, new)
            _carry_rows(db, mid, old, new, report)
            if gdocs.rename_link(db, manuscript, old, new):
                report["records"]["manuscripts.metadata"] = 1
                entry = gdocs.doc_status(db, manuscript).get(new) or {}
                report["tab_title_stale"] = bool(entry.get("tab_title"))
            elif old in gdocs.doc_status(db, manuscript):
                report["left"].append(
                    f"the Doc link of {old}: '{new}' already has a Doc link")
            run_report = devedit.rename_file(db, manuscript, old, new, changes)
            report["left"] += run_report["left"]

            if move_file:
                changes.move(root / old, root / new)
            toc = root / TOC_FILENAME
            if toc.exists():
                text = toc.read_text(encoding="utf-8")
                renamed = rename_toc_file(text, old, new)
                if renamed != text:
                    changes.write(toc, renamed)
            _carry_audio(root, old, new, changes, report)
            merged = root / "_drafts" / f"{old_stem}.merged.md"
            if old_stem != new_stem and merged.exists():
                target = merged.with_name(f"{new_stem}.merged.md")
                if target.exists():
                    report["left"].append(
                        f"_drafts/{merged.name}: _drafts/{target.name} "
                        "already exists")
                else:
                    changes.move(merged, target)
            report["files"] = list(changes.paths)
            if not apply:
                raise _Preview
    except _Preview:
        pass
    except BaseException:
        changes.undo()
        raise
    return report


def _new_relpath(root: Path, name: str) -> str:
    """The relpath a rename's NEW argument names: a path inside the
    manuscript, in a folder observation can see, with a manuscript
    extension (`.md` when none is given)."""
    from .structure import TOC_FILENAME, is_sidecar

    parts = [part for part in name.strip().replace("\\", "/").split("/")
             if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        raise ValueError(f"'{name}' is not a file name inside the manuscript")
    if any(_UNSAFE.search(part) for part in parts):
        raise ValueError(f"'{name}' has a character a file name cannot "
                         'carry (one of \\ : * ? " < > | #)')
    if any(part.startswith((".", "_")) for part in parts):
        raise ValueError(
            f"'{name}': a name or folder starting with '.' or '_' is "
            "invisible to AuthorLM. To set a chapter aside use "
            "'authorlm doc retire'.")
    if parts[-1] == TOC_FILENAME or is_sidecar("/".join(parts)):
        raise ValueError(f"'{'/'.join(parts)}' is a name AuthorLM reserves: "
                         "pick another name")
    if Path(parts[-1]).suffix.lower() not in MANUSCRIPT_EXTENSIONS:
        parts[-1] += ".md"
    relpath = "/".join(parts)
    return relpath


def _name_already_used(db: Database, manuscript: dict, old: str,
                       new: str) -> list[str]:
    """What an earlier file called `new` left behind, which a rename onto
    that name would mix with this chapter's records.

    Stem-named audiobook and draft files (`_audio/chapters/<stem>.json`,
    `_audio/state/<stem>.json`, `_drafts/<stem>.merged.md`) belong to this
    chapter when the stem does not change (a folder move such as
    `foo.md` → `papers/foo.md`): they are not leftovers, and `carry_rename`
    leaves them in place while rewriting the file field inside. Only a
    different stem that already has those files is a collision."""
    mid = manuscript["id"]
    found = []
    for table, column in NAME_COLUMNS:
        n = db.one(f"SELECT COUNT(*) AS n FROM {table} WHERE "
                   f"manuscript_id = ? AND {column} = ?", (mid, new))["n"]
        if n:
            found.append(f"{n} {table} row(s)")
    from . import gdocs

    if new in gdocs.doc_status(db, manuscript):
        found.append("a Doc link")
    root = Path(manuscript["path"])
    old_stem, new_stem = Path(old).stem, Path(new).stem
    if old_stem == new_stem:
        return found
    for rel in (f"_audio/chapters/{new_stem}.json",
                f"_audio/state/{new_stem}.json",
                f"_drafts/{new_stem}.merged.md"):
        if (root / rel).exists():
            found.append(rel)
    return found


def check_rename(db: Database, manuscript: dict, query: str, new_name: str,
                 force: bool = False) -> tuple[str, str]:
    """Resolve and vet a rename; returns `(old relpath, new relpath)` and
    writes nothing. Declined, each with its fix, when: the manuscript's
    `document_ids` setting is off; the file is the
    table of contents or a sidecar; the new name is taken (in the
    manuscript or in `_retired/`); the file has a live filter run, a
    writeup in flight, pending `<<old>>{{new}}` forms, or is checked out
    to Google Docs (each holds state tied to the name while it runs, and
    carrying a run mid-flight is not proven safe); or an earlier file of
    the new name left records behind, which `force` carries past."""
    from . import gdocs, staging
    from .structure import TOC_FILENAME, is_sidecar

    root = Path(manuscript["path"])
    mid = manuscript["id"]
    if not document_ids_enabled(db, manuscript):
        raise ValueError(
            "document ids are off for this manuscript (its 'document_ids' "
            "setting), so 'doc rename' has nothing to carry its records "
            "with. Turn them on first (a backup is taken, then every file "
            f"gets its id): {_turn_on_hint(manuscript)}")
    old = _resolve_relpath(manuscript, query)
    if old == TOC_FILENAME:
        raise ValueError(f"{TOC_FILENAME} is the table of contents, not a "
                         "chapter: it cannot be renamed")
    if is_sidecar(old):
        raise ValueError(f"{old} is found by its name and cannot be renamed")
    new = _new_relpath(root, new_name)
    if new == old:
        raise ValueError(f"{old} already has that name")
    source, target = root / old, root / new
    if target.exists() and not target.samefile(source):
        raise FileExistsError(f"{new} already exists in the manuscript")
    if (root / RETIRED_DIR / Path(new).name).exists():
        raise FileExistsError(
            f"{Path(new).name} exists in {RETIRED_DIR}/: pick another name, "
            f"or revive that file first: authorlm doc revive {Path(new).name}")
    run = db.one(
        "SELECT filter FROM filter_runs WHERE manuscript_id = ? AND file = ? "
        "AND status = 'active'", (mid, old))
    if run:
        raise ValueError(
            f"{old} has a live filter run ({run['filter']}). Finish it "
            f"('authorlm filter resolve {old}') or drop it "
            f"('authorlm filter abandon {run['filter']} {old}'), then rename.")
    if db.one("SELECT 1 FROM writeups WHERE manuscript_id = ? AND file = ? "
              "AND status = 'active'", (mid, old)):
        raise ValueError(
            f"{old} has a writeup in flight. Finish it ('authorlm write "
            "complete') or drop it ('authorlm write abandon'), then rename.")
    link = gdocs.doc_status(db, manuscript).get(old)
    if isinstance(link, dict) and link.get("checked_out"):
        raise ValueError(
            f"{old} is checked out to Google Docs. Bring it back first: "
            f"authorlm doc pull {old}")
    if staging.is_marked(source.read_text(encoding="utf-8")):
        raise ValueError(
            f"{old} carries pending <<old>>{{{{new}}}} forms. Settle them "
            "first, then rename.")
    used = _name_already_used(db, manuscript, old, new)
    if used and not force:
        raise ValueError(
            f"an earlier file named {new} left records behind ("
            + ", ".join(used) + "), and this chapter's would be mixed with "
            "them. Pick another name, or pass --force to rename anyway: "
            "records that collide stay under the old name and are listed.")
    return old, new


def rename_doc(db: Database, manuscript: dict, query: str, new_name: str, *,
               force: bool = False, apply: bool = True) -> dict:
    """`doc rename`: rename a chapter file and carry everything that
    names it (`carry_rename`). `check_rename` vets it first; nothing is
    written when it declines. The caller collects before (so the text as
    it stood is in a snapshot) and after (so the rename is observed)."""
    old, new = check_rename(db, manuscript, query, new_name, force=force)
    return carry_rename(db, manuscript, old, new, move_file=True, apply=apply)
