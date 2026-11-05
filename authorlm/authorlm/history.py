"""Where the text of a version is kept.

`revisions.py` is the door to version history and owns which versions
exist: one row each in `manuscript_versions`, under either backend. This
module owns where a version's text is. There is one store per backend,
with the same methods, and `[history] backend` in config.toml chooses:

- `table` (the default): the row's own `files` column holds the whole
  manuscript as one JSON object.
- `git`: a private git repository per manuscript, at
  `<workspace>/.authorlm/history/<manuscript id>.git`. Each version is a
  commit, found by the ref `refs/versions/<version id>`, so a version id
  written anywhere (a transition, a pin, evidence) resolves to its commit
  by name. The row's `files` column holds JSON `null`. The repository has
  no remote, and nothing is written inside the book folder.

A version holds the text collect observes (`revisions.read_manuscript_files`),
not the bytes on disk: the two differ wherever a file carries an
illustration embed line or a pending form. So a commit is built from that
text, not by adding the book folder's files.

Recording on the git backend, in order: take the manuscript's lock, write
the commit, read it back and compare, insert the row, release the lock.
The row is what makes a version exist. A crash after the commit and
before the row leaves a commit no row names: nothing can refer to it, the
branch has not moved to it (the branch follows rows), the next record
builds on the latest row's commit, and `history check` lists it. Two collects at once take the lock in turn, and the second sees the
first's version before it decides whether anything changed.

A workspace uses one backend at a time. `authorlm history replay` copies
the table's versions into git, and each store stops with the fix when it
meets a version the other one holds, instead of reading it as empty.
"""

from __future__ import annotations

import fcntl
import json
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .db import Database, loads
from .history_git import Commit, GitError, Reader, Repository, blob_id

BACKENDS = ("table", "git")
DEFAULT_BACKEND = "table"
# The `files` column of a version whose text is in git. JSON null and not
# an empty object, on purpose: code that still reads the column directly
# fails on it instead of seeing a manuscript with no files.
MOVED = "null"
VERSION_REFS = "refs/versions/"
BRANCH = "refs/heads/main"
REPLAY_COMMAND = "authorlm history replay"


class HistoryMismatch(RuntimeError):
    """A version's text is not where the configured backend keeps it."""


@dataclass(frozen=True, slots=True)
class Version:
    """One collected version of a manuscript: which one it is, and when
    and how it was recorded. It never carries the text; `file_at`,
    `files_at` and `changes` in `revisions` read that."""
    id: str
    manuscript_id: str
    version_no: int
    checksum: str
    source: str
    session_id: str | None
    created_at: str


@dataclass(frozen=True, slots=True)
class FileChange:
    """One file's text on the two sides of `changes`. `None` on a side
    means the file is not in that version."""
    old: str | None
    new: str | None


_BACKEND_BY_CONFIG: dict[str, str] = {}


def backend() -> str:
    """`[history] backend` from config.toml: `table` or `git`.

    An absent file, section or key means `table`, and so does a config
    that does not parse (the rest of AuthorLM ignores one with a
    warning). A value that is neither is an error, by name: a mistyped
    backend must not quietly become the other one. Remembered per config
    path."""
    from . import paths

    path = paths.config_path()
    known = _BACKEND_BY_CONFIG.get(str(path))
    if known:
        return known
    chosen = DEFAULT_BACKEND
    if path.exists():
        import tomllib

        try:
            parsed = tomllib.loads(path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError:
            parsed = {}
        chosen = (parsed.get("history") or {}).get("backend", DEFAULT_BACKEND)
    if chosen not in BACKENDS:
        raise ValueError(
            f"[history] backend in {path} is {chosen!r}. "
            f"Set it to one of: {', '.join(BACKENDS)}")
    _BACKEND_BY_CONFIG[str(path)] = chosen
    return chosen


def history_root(db: Database) -> Path:
    """The folder holding the workspace's git histories, beside its database."""
    return Path(db.path).resolve().parent / "history"


def store_for(db: Database) -> "TableStore | GitStore":
    """The store the configured backend names, for this workspace."""
    if backend() == "git":
        return GitStore(db)
    return TableStore(db)


def version_ref(version_id: str) -> str:
    return VERSION_REFS + version_id


def encode(text: str) -> bytes:
    """A file's text as the bytes git holds. Any Python string survives
    the round trip, an unpaired surrogate included."""
    return text.encode("utf-8", "surrogatepass")


def decode(data: bytes) -> str:
    return data.decode("utf-8", "surrogatepass")


def _differing(old: dict[str, str], new: dict[str, str]) -> dict[str, FileChange]:
    return {
        name: FileChange(old.get(name), new.get(name))
        for name in sorted(set(old) | set(new))
        if old.get(name) != new.get(name)
    }


class TableStore:
    """A version's text is the JSON in its row's `files` column."""

    def __init__(self, db: Database):
        self.db = db

    def _moved(self, version_id: str) -> HistoryMismatch:
        return HistoryMismatch(
            f"the text of version {version_id} is in the git history, but "
            f"[history] backend is \"table\". Set [history] backend = \"git\" "
            f"in config.toml")

    @contextmanager
    def recording(self, manuscript_id: str):
        yield

    def put(self, version: Version, files: dict[str, str],
            parent: Version | None) -> str:
        """The `files` column value for a new version's row."""
        if parent and self.db.one(
                "SELECT 1 FROM manuscript_versions WHERE id = ? AND files = ?",
                (parent.id, MOVED)):
            raise self._moved(parent.id)
        return json.dumps(files)

    def settled(self, version: Version) -> None:
        pass

    def file(self, version_id: str, name: str) -> str | None:
        row = self.db.one(
            "SELECT entry.value AS text FROM manuscript_versions AS v, "
            "json_each(v.files) AS entry WHERE v.id = ? AND entry.key = ?",
            (version_id, name))
        if row:
            return row["text"]
        if self.db.one(
                "SELECT 1 FROM manuscript_versions WHERE id = ? AND files = ?",
                (version_id, MOVED)):
            raise self._moved(version_id)
        return None

    def files(self, version_id: str) -> dict[str, str]:
        row = self.db.one("SELECT files FROM manuscript_versions WHERE id = ?",
                          (version_id,))
        if not row:
            return {}
        if row["files"] == MOVED:
            raise self._moved(version_id)
        return loads(row["files"], {})

    def has_text(self, version_id: str) -> bool:
        """Whether the row still holds this version's text."""
        return not self.db.one(
            "SELECT 1 FROM manuscript_versions WHERE id = ? AND files = ?",
            (version_id, MOVED))

    def changes(self, older_id: str | None,
                newer_id: str) -> dict[str, FileChange]:
        old = self.files(older_id) if older_id else {}
        return _differing(old, self.files(newer_id))

    def file_counts(self, manuscript_id: str) -> dict[str, int]:
        counts = {row["id"]: row["n"] for row in self.db.all(
            "SELECT id, CASE WHEN files = ? THEN -1 ELSE "
            "(SELECT COUNT(*) FROM json_each(files)) END AS n "
            "FROM manuscript_versions WHERE manuscript_id = ?",
            (MOVED, manuscript_id))}
        for version_id, count in counts.items():
            if count < 0:
                raise self._moved(version_id)
        return counts


class GitStore:
    """A version's text is a commit in the manuscript's private git
    repository, found by the ref named after the version id."""

    def __init__(self, db: Database):
        self.db = db
        self.root = history_root(db)

    def repository(self, manuscript_id: str) -> Repository:
        return Repository(self.root / f"{manuscript_id}.git")

    def _missing(self, version_id: str) -> HistoryMismatch:
        return HistoryMismatch(
            f"version {version_id} is not in the git history at {self.root}. "
            f"Run: {REPLAY_COMMAND}")

    def _manuscript_of(self, version_id: str) -> str | None:
        row = self.db.one(
            "SELECT manuscript_id FROM manuscript_versions WHERE id = ?",
            (version_id,))
        return row["manuscript_id"] if row else None

    @contextmanager
    def recording(self, manuscript_id: str):
        """Hold the manuscript's lock: one record at a time, across
        processes. A process that dies gives the lock up."""
        self.root.mkdir(parents=True, exist_ok=True)
        with open(self.root / f"{manuscript_id}.lock", "w") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    @contextmanager
    def _reading(self, manuscript_id: str, version_id: str):
        repository = self.repository(manuscript_id)
        if not repository.exists():
            raise self._missing(version_id)
        with repository.reader() as reader:
            yield reader

    def _commit(self, reader: Reader, version_id: str) -> str:
        commit = reader.commit(version_ref(version_id))
        if commit is None:
            raise self._missing(version_id)
        return commit

    def put(self, version: Version, files: dict[str, str],
            parent: Version | None) -> str:
        """Commit `files` as `version`, on top of `parent`'s commit, and
        check the commit holds exactly them. Returns the `files` column
        value for the version's row."""
        repository = self.repository(version.manuscript_id)
        if not repository.exists():
            if parent:
                raise self._missing(parent.id)
            repository.create(observed_names())
        data = {name: encode(text) for name, text in files.items()}
        wanted = {name: blob_id(payload) for name, payload in data.items()}
        ref = version_ref(version.id)
        with repository.reader() as reader:
            below = self._commit(reader, parent.id) if parent else None
            before = reader.blob_ids(below) if below else {}
            repository.write([Commit(
                ref=ref, parent=below, message=commit_message(version),
                when=commit_time(version.created_at),
                changed={name: data[name] for name in data
                         if before.get(name) != wanted[name]},
                removed=tuple(name for name in before if name not in data),
            )])
            if reader.blob_ids(ref) != wanted:
                repository.delete_ref(ref)
                raise GitError(
                    f"the commit written for version {version.id} does not "
                    f"hold the files handed in; it was removed and nothing "
                    f"was recorded")
        return MOVED

    def settled(self, version: Version) -> None:
        """Housekeeping once a version's row is in: move the branch and
        the index to its commit (for `git log` and `git status` by hand)
        and pack when enough loose objects have gathered. A failure here
        loses nothing, so it is reported and the record stands."""
        repository = self.repository(version.manuscript_id)
        try:
            repository.follow(BRANCH, version_ref(version.id))
            repository.pack_if_loose()
        except GitError as err:
            print(f"warning: {err}", file=sys.stderr)

    def file(self, version_id: str, name: str) -> str | None:
        manuscript_id = self._manuscript_of(version_id)
        if manuscript_id is None:
            return None
        with self._reading(manuscript_id, version_id) as reader:
            data = reader.file(self._commit(reader, version_id), name)
        return None if data is None else decode(data)

    def files(self, version_id: str) -> dict[str, str]:
        manuscript_id = self._manuscript_of(version_id)
        if manuscript_id is None:
            return {}
        with self._reading(manuscript_id, version_id) as reader:
            found = reader.files(self._commit(reader, version_id))
        return {name: decode(data) for name, data in found.items()}

    def changes(self, older_id: str | None,
                newer_id: str) -> dict[str, FileChange]:
        manuscript_id = self._manuscript_of(newer_id)
        if manuscript_id is None:
            old = self.files(older_id) if older_id else {}
            return _differing(old, {})
        with self._reading(manuscript_id, newer_id) as reader:
            older = self._commit(reader, older_id) if older_id else None
            found = reader.changes(older, self._commit(reader, newer_id))
        return {
            name: FileChange(None if old is None else decode(old),
                             None if new is None else decode(new))
            for name, (old, new) in found.items()
        }

    def file_counts(self, manuscript_id: str) -> dict[str, int]:
        ids = [row["id"] for row in self.db.all(
            "SELECT id FROM manuscript_versions WHERE manuscript_id = ?",
            (manuscript_id,))]
        if not ids:
            return {}
        with self._reading(manuscript_id, ids[0]) as reader:
            return {version_id: len(reader.blob_ids(
                self._commit(reader, version_id))) for version_id in ids}


def observed_names() -> list[str]:
    """The file-name patterns a version holds, for the repository's
    ignore rules."""
    from .revisions import MANUSCRIPT_EXTENSIONS
    from .structure import TOC_FILENAME

    return [*(f"*{suffix}" for suffix in sorted(MANUSCRIPT_EXTENSIONS)),
            TOC_FILENAME]


def commit_message(version: Version) -> str:
    return (f"v{version.version_no} {version.source}\n\n"
            f"Version: {version.id}\n"
            f"Recorded: {version.created_at}\n"
            f"Session: {version.session_id or '-'}\n"
            f"Checksum: {version.checksum}\n")


def commit_time(created_at: str) -> int:
    """A version's recorded time as whole seconds since 1970, UTC."""
    return int(datetime.fromisoformat(
        created_at.replace("Z", "+00:00")).timestamp())
