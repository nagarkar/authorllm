"""Revision Collector, version history and Revision Analyzer (RFC AuthorLM
§17.3–§17.4).

The collector snapshots the manuscript directory into an immutable,
checksummed version. The analyzer computes editorial transitions between
successive versions by diffing paragraph sequences per file.

Version history has one door, and it is this module (the "history"
section below): a caller asks for a version row (`latest_version`,
`version_by_id`, `version_by_number`, `version_as_of`, `list_versions`)
and then for text (`files_at`, `file_at`, `manifest_at`, `file_history`,
`changed_names`). `record_version` is the only writer. Each distinct file
text is kept once, by hash; no other module reads the tables.

A snapshot records each file under the name it had then, and is never
rewritten. For a manuscript with document ids on (`docs.py`), a file that
left under one name and arrived under another with the same or nearly the
same text is one document renamed (`pair_renames`), never a removal and
an addition: its transition is `file_renamed`, its
paragraphs are diffed against the old name's text, and a reader of an old
snapshot finds a document through its name history (`docs.names_of`).
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import sys
import zlib
from pathlib import Path

from . import form_grammar
from .db import Database, ko_fields

MANUSCRIPT_EXTENSIONS = {".md", ".markdown", ".txt"}


class ManuscriptRootUnreadable(RuntimeError):
    """The manuscript root directory does not exist (or is not a
    readable directory) — a vanished mount point, a moved folder, a
    permissions problem. `read_manuscript_files` returns {} for a
    missing root with no error, and downstream that reads exactly like
    the author having deleted every file. It is not: it is an I/O
    condition, never an editorial act, and must never be allowed to
    retire a concept or record a version (it-258752ea91f9).

    Deliberately a RuntimeError (not bare OSError): it is caught by the
    same `except (LookupError, ValueError, RuntimeError)` clause the MCP
    surface already uses to turn an application error into a clean
    {"ok": False, "error": ...} result instead of crashing an unattended
    call (mcp_server._guard), and by the existing `except Exception`
    around opening collects in cli.py's `session start`."""


def check_manuscript_root(root: Path) -> None:
    """Raise ManuscriptRootUnreadable unless `root` is a readable,
    existing directory. Every path that can end up recording a version
    or retiring a concept from an empty-looking read must call this
    first — see collect_revision()."""
    if not root.is_dir():
        raise ManuscriptRootUnreadable(
            f"manuscript root is not a readable directory: {root}"
        )


# Illustration embed lines (`![](_illustrations/….png)` under a tag) are
# DERIVED machinery — written by `illus render`/`pick`, stripped on Doc
# push, re-inserted after pull. Observation must not see them: the author
# never wrote them, so they must never appear in transitions or episodes.
EMBED_LINE = re.compile(r"^!\[[^\]]*\]\((?:\./)?_illustrations/[^)]+\)[ \t]*$")


def strip_embed_lines(text: str) -> str:
    lines = [ln for ln in text.split("\n") if not EMBED_LINE.match(ln)]
    return "\n".join(lines)


def peel_embed_suffix(para: str) -> tuple[str, str]:
    """Split one paragraph into `(core, embed_suffix)`.

    Observation strips embed lines (`read_manuscript_files`), so filter
    and lens proposals are staged against the core alone. On disk an
    embed sits on the line under its `[Illustration: …]` tag with no
    blank line between, so `_paragraphs` glues them into ONE unit. Local
    `compose_marked_text` must peel that suffix for the drift check and
    re-attach it after the pending form — otherwise every accepted edit
    of an illustration unit raises "text drifted", and a mixed accepted
    batch cannot settle at all."""
    lines = (para or "").split("\n")
    i = len(lines)
    while i > 0 and EMBED_LINE.match(lines[i - 1]):
        i -= 1
    if i == len(lines):
        return para, ""
    core = "\n".join(lines[:i])
    return core, "\n" + "\n".join(lines[i:])


def iter_manuscript_paths(root: Path) -> dict[str, Path]:
    """Manuscript files by relative path. Directories whose name starts with
    '.' or '_' are invisible to observation — this keeps editor internals
    (.obsidian/, .trash/) and AuthorLM's own exports (_concepts/) out of the
    observed manuscript."""
    from .structure import TOC_FILENAME

    paths: dict[str, Path] = {}
    for path in sorted(root.rglob("*")):
        if not (path.is_file()
                and (path.suffix.lower() in MANUSCRIPT_EXTENSIONS
                     or path.name == TOC_FILENAME)):
            continue
        relative = path.relative_to(root)
        if any(part.startswith((".", "_")) for part in relative.parts[:-1]):
            continue
        if relative.name.startswith("."):
            continue
        paths[str(relative)] = path
    return paths


def read_manuscript_files(root: Path) -> dict[str, str]:
    """Every file returned by `iter_manuscript_paths` MUST appear in the
    result — a dropped entry reads downstream as a deletion and can feed
    the auto-retire path (api.py). A file that is not valid UTF-8 is
    re-read with `errors="replace"` (invalid bytes become U+FFFD) rather
    than raising: this is the recovery path itself (`collect()`), so it
    must not die on the failure it exists to recover from."""
    files: dict[str, str] = {}
    for rel, path in iter_manuscript_paths(root).items():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            print(f"warning: {rel} is not valid UTF-8; reading it with "
                  f"replacement characters in place of the invalid bytes",
                  file=sys.stderr)
            text = path.read_text(encoding="utf-8", errors="replace")
        # A file carrying a `<<old>>{{new}}` form is mid-settle. The
        # ratified law (form_grammar.py's docstring) is that the canonical
        # text of a pending form is its OLD half: content changes at
        # approval, not at proposal. That law has always been applied to
        # text arriving from a Doc; the filter pass's local settle
        # transport makes the same forms reachable on DISK, and the same
        # law must govern them or every observer in the system reads a
        # proposal as prose — collect would record markers into version
        # history, the extractor would mine them, the summarizer would
        # summarize them, the realization scan would match inside them,
        # `export` would ship them, and a lens would report on them.
        #
        # Beside `strip_embed_lines`, deliberately: this seam has never
        # returned the bytes on disk. It already drops illustration
        # embed lines because they are local derived machinery that the
        # observed manuscript must not contain. A pending form is the
        # same kind of thing by a different road — machinery the author
        # can see, which is not yet part of the essay.
        #
        # `strip_replacements`, NOT `strip_pending`. The Doc
        # canonicalizer also deletes a bare `{{…}}`, because in a Doc tab
        # nothing put one there but AuthorLM and it is a critique-pass
        # insertion. In a LOCAL file the opposite holds: `{{title}}` in a
        # template, `{{a, b}}` in set notation, a Handlebars sample in an
        # essay about templating are all the author's own prose, and the
        # broad strip deleted every one of them from everything the
        # system observes — silently, since nothing was left to warn
        # about. The narrow function is a true no-op on text carrying no
        # `<<old>>{{new}}` form, which is what makes it safe here
        # unconditionally (filter-pass design §2.3).
        files[rel] = form_grammar.strip_replacements(strip_embed_lines(text))[0]
    return files


def checksum(files: dict[str, str]) -> str:
    digest = hashlib.sha256()
    for name in sorted(files):
        digest.update(name.encode())
        digest.update(b"\x00")
        digest.update(files[name].encode())
        digest.update(b"\x00")
    return digest.hexdigest()


# ------------------------------------------------------------ history
#
# Version history has one door, and it is this module: no other module
# reads or writes `manuscript_versions`, `version_files` or
# `version_texts`. A version is its `manuscript_versions` row (id, number,
# checksum, source, session, time) plus the files it holds: one
# `version_files` row per file, naming the text by its hash. Each distinct
# file text is kept once in `version_texts`, zlib-compressed, however many
# versions or manuscripts hold it. A collect that changes one file adds
# one text, not a copy of the book.
#
# The text of a version is what `read_manuscript_files` returned when it
# was recorded (embed lines and pending forms stripped, invalid UTF-8
# replaced), never the bytes on disk. Version ids, numbers and checksums
# never change: other tables name versions by id.


class VersionStoreNotConverted(RuntimeError):
    """The workspace still keeps each version's whole text as JSON in
    `manuscript_versions.files`. Raised by every read and write of
    history until `authorlm version-store convert` has run, so nothing
    reads an unconverted workspace as a manuscript with no files.
    A RuntimeError for the same reason as ManuscriptRootUnreadable: the
    MCP surface and the CLI already turn one into a clean error."""


VERSION_COLUMNS = ("id, version, created_at, created_by, schema_version, "
                   "metadata, manuscript_id, version_no, checksum, source, "
                   "session_id")

# Texts are immutable and named by their hash, so a text read once can be
# served again to any caller, any manuscript, any database: the hash is
# the whole key. Bounded so a full-history walk does not hold every text.
_TEXT_CACHE: dict[str, str] = {}
_TEXT_CACHE_LIMIT = 1024


def store_converted(db: Database) -> bool:
    """Whether the workspace keeps history in the store (True) or still
    as whole-manuscript JSON per version (False: run `version-store
    convert`)."""
    return "files" not in {r[1] for r in db.conn.execute(
        "PRAGMA table_info(manuscript_versions)")}


def _require_store(db: Database) -> None:
    if getattr(db, "_version_store_ready", False):
        return
    if not store_converted(db):
        raise VersionStoreNotConverted(
            f"the workspace at {db.path} keeps version history in the old "
            "form (whole manuscript per version). Run `authorlm "
            "version-store convert` once to convert it.")
    # Only the converted state is remembered: conversion is one-way.
    db._version_store_ready = True


def _encode(text: str) -> bytes:
    # surrogatepass: a version holds whatever str it was given; this
    # round-trips every one of them exactly.
    return text.encode("utf-8", "surrogatepass")


def _decode(body: bytes) -> str:
    return zlib.decompress(body).decode("utf-8", "surrogatepass")


def text_hash(text: str) -> str:
    """The key a file text is kept under in `version_texts`."""
    return hashlib.sha256(_encode(text)).hexdigest()


def _remember(digest: str, text: str) -> None:
    if len(_TEXT_CACHE) >= _TEXT_CACHE_LIMIT:
        _TEXT_CACHE.pop(next(iter(_TEXT_CACHE)))
    _TEXT_CACHE[digest] = text


def text_of(db: Database, digest: str) -> str:
    """The file text kept under `digest`. Raises LookupError when the
    store has no such text (a damaged store, never a normal state)."""
    cached = _TEXT_CACHE.get(digest)
    if cached is not None:
        return cached
    row = db.one("SELECT body FROM version_texts WHERE hash = ?", (digest,))
    if row is None:
        raise LookupError(f"version store has no text {digest}")
    text = _decode(row["body"])
    _remember(digest, text)
    return text


def _store_text(db: Database, text: str) -> str:
    digest = text_hash(text)
    if not db.one("SELECT 1 AS x FROM version_texts WHERE hash = ?",
                  (digest,)):
        db.conn.execute(
            "INSERT INTO version_texts (hash, body) VALUES (?, ?)",
            (digest, zlib.compress(_encode(text), 6)))
    return digest


def _row(row) -> dict | None:
    return dict(row) if row else None


def version_by_id(db: Database, version_id: str | None) -> dict | None:
    """A version's row (no text), or None when there is no such version."""
    if not version_id:
        return None
    _require_store(db)
    return _row(db.one(
        f"SELECT {VERSION_COLUMNS} FROM manuscript_versions WHERE id = ?",
        (version_id,)))


def latest_versions(db: Database, manuscript_id: str,
                    count: int) -> list[dict]:
    """The `count` most recent versions, newest first."""
    _require_store(db)
    return [dict(r) for r in db.all(
        f"SELECT {VERSION_COLUMNS} FROM manuscript_versions "
        "WHERE manuscript_id = ? ORDER BY version_no DESC LIMIT ?",
        (manuscript_id, count))]


def latest_version(db: Database, manuscript_id: str) -> dict | None:
    newest = latest_versions(db, manuscript_id, 1)
    return newest[0] if newest else None


def version_by_number(db: Database, manuscript_id: str,
                      version_no: int) -> dict | None:
    _require_store(db)
    return _row(db.one(
        f"SELECT {VERSION_COLUMNS} FROM manuscript_versions "
        "WHERE manuscript_id = ? AND version_no = ?",
        (manuscript_id, version_no)))


def version_as_of(db: Database, manuscript_id: str,
                  moment: str) -> dict | None:
    """The version that was current at `moment` (an ISO time, compared as
    text like every `created_at`): the newest recorded at or before it.
    None when every version is later."""
    _require_store(db)
    return _row(db.one(
        f"SELECT {VERSION_COLUMNS} FROM manuscript_versions "
        "WHERE manuscript_id = ? AND created_at <= ? "
        "ORDER BY version_no DESC LIMIT 1", (manuscript_id, moment)))


def list_versions(db: Database, manuscript_id: str) -> list[dict]:
    """Every version of the manuscript, oldest first."""
    _require_store(db)
    return [dict(r) for r in db.all(
        f"SELECT {VERSION_COLUMNS} FROM manuscript_versions "
        "WHERE manuscript_id = ? ORDER BY version_no", (manuscript_id,))]


def count_versions(db: Database, manuscript_id: str) -> int:
    return db.one("SELECT COUNT(*) AS n FROM manuscript_versions "
                  "WHERE manuscript_id = ?", (manuscript_id,))["n"]


def last_recorded_at(db: Database, session_id: str) -> str | None:
    """When the session last recorded a version; None if it never did."""
    return db.one("SELECT MAX(created_at) AS latest FROM manuscript_versions "
                  "WHERE session_id = ?", (session_id,))["latest"]


def file_counts(db: Database, manuscript_id: str) -> dict[str, int]:
    """How many files each version of the manuscript holds, by version id."""
    _require_store(db)
    return {r["id"]: r["n"] for r in db.all(
        "SELECT v.id AS id, COUNT(f.name) AS n FROM manuscript_versions v "
        "LEFT JOIN version_files f ON f.version_id = v.id "
        "WHERE v.manuscript_id = ? GROUP BY v.id", (manuscript_id,))}


def manifest_at(db: Database, version_id: str | None) -> dict[str, str]:
    """{name: text hash} of every file the version holds, in the order it
    recorded them. Empty when there is no such version. Two files (or one
    file at two versions) hold the same text exactly when their hashes are
    equal, so this answers "what changed" without reading any text."""
    if not version_id:
        return {}
    _require_store(db)
    return {r["name"]: r["text_hash"] for r in db.all(
        "SELECT name, text_hash FROM version_files WHERE version_id = ? "
        "ORDER BY position", (version_id,))}


def files_at(db: Database, version_id: str | None) -> dict[str, str]:
    """{name: text} of every file the version holds, in the order it
    recorded them. Empty when there is no such version."""
    return {name: text_of(db, digest)
            for name, digest in manifest_at(db, version_id).items()}


def file_at(db: Database, version_id: str | None, name: str) -> str | None:
    """One file's text at a version, read alone. None when there is no
    such version or it holds no file recorded as `name` (for a renamed
    document, `_version_text` tries each name it carried)."""
    if not version_id:
        return None
    _require_store(db)
    row = db.one("SELECT text_hash FROM version_files WHERE version_id = ? "
                 "AND name = ?", (version_id, name))
    return text_of(db, row["text_hash"]) if row else None


def file_history(db: Database, manuscript_id: str,
                 name: str) -> list[tuple[int, str]]:
    """[(version_no, text hash)] for every version that holds a file
    recorded as `name`, newest first. Read a text with `text_of`; equal
    hashes are equal texts, so a caller reads each distinct one once."""
    _require_store(db)
    return [(r["version_no"], r["text_hash"]) for r in db.all(
        "SELECT v.version_no AS version_no, f.text_hash AS text_hash "
        "FROM version_files f JOIN manuscript_versions v "
        "ON v.id = f.version_id WHERE f.manuscript_id = ? AND f.name = ? "
        "ORDER BY v.version_no DESC", (manuscript_id, name))]


def changed_names(db: Database, older_id: str | None,
                  newer_id: str | None) -> set[str]:
    """Names whose text differs between two versions, or that only one of
    them holds. Compares hashes; reads no text."""
    old, new = manifest_at(db, older_id), manifest_at(db, newer_id)
    return {n for n in set(old) | set(new) if old.get(n) != new.get(n)}


def _insert_files(db: Database, version_id: str, manuscript_id: str,
                  files: dict[str, str]) -> None:
    db.conn.executemany(
        "INSERT INTO version_files (version_id, manuscript_id, position, "
        "name, text_hash) VALUES (?, ?, ?, ?, ?)",
        [(version_id, manuscript_id, position, name, _store_text(db, text))
         for position, (name, text) in enumerate(files.items())])


def record_version(db: Database, manuscript_id: str, files: dict[str, str],
                   session_id: str | None,
                   source: str = "snapshot") -> dict | None:
    """Record `files` as the manuscript's next version and return its row
    (no text), or None when they are what the latest version already
    holds. One transaction: a crash leaves the whole version or none of
    it. Only texts the store does not already hold are added."""
    _require_store(db)
    digest = checksum(files)
    with db.transaction():
        last = latest_version(db, manuscript_id)
        if last and last["checksum"] == digest:
            return None
        row = ko_fields("mv")
        row.update(
            manuscript_id=manuscript_id,
            version_no=(last["version_no"] + 1) if last else 1,
            checksum=digest,
            source=source,
            session_id=session_id,
        )
        db.insert("manuscript_versions", row)
        _insert_files(db, row["id"], manuscript_id, files)
    return row


def prune_texts(db: Database) -> int:
    """Delete every kept text no version holds any more (after a
    manuscript's versions are deleted with it). Returns how many."""
    return db.conn.execute(
        "DELETE FROM version_texts WHERE hash NOT IN "
        "(SELECT text_hash FROM version_files)").rowcount


def collect_revision(
    db: Database, manuscript: dict, session_id: str | None, source: str = "snapshot"
) -> dict | None:
    """Snapshot the manuscript. Returns the new version row as a dict
    (no text: `files_at` reads it), or None if nothing changed since the
    last version.

    Raises ManuscriptRootUnreadable — refuses to record a version — if
    the root is missing or unreadable, regardless of the caller: an
    unreadable directory must never be recorded as "every file removed"
    (it-258752ea91f9). This runs unconditionally (not just on the
    auto-collect path) because 19 of 20 api.collect() call sites,
    including the unattended _catch_up, pass auto=False and would
    otherwise bypass the mass-deletion guard entirely."""
    root = Path(manuscript["path"])
    check_manuscript_root(root)
    return record_version(db, manuscript["id"], read_manuscript_files(root),
                          session_id, source)


# ------------------------------------------------- converting a workspace

def convert_store(db: Database, progress=None) -> dict:
    """Move every version's text out of `manuscript_versions.files` into
    the store, check that every version reads back exactly as its JSON
    did (same names, same order, same text) and still matches its
    checksum, then drop the column. One transaction: any difference
    raises and leaves the workspace as it was. Ids, numbers and checksums
    are untouched. Returns counts."""
    if store_converted(db):
        return {"already": True}
    ids = [r["id"] for r in db.all(
        "SELECT id FROM manuscript_versions ORDER BY manuscript_id, "
        "version_no")]
    report = {"versions": len(ids), "file_copies": 0, "texts_added": 0,
              "raw_bytes": 0, "checksum_mismatches": []}
    with db.transaction():
        before = db.one("SELECT COUNT(*) AS n FROM version_texts")["n"]
        for done, vid in enumerate(ids, 1):
            row = db.one("SELECT manuscript_id, checksum, files FROM "
                         "manuscript_versions WHERE id = ?", (vid,))
            files = json.loads(row["files"])
            if checksum(files) != row["checksum"]:
                # Recorded as it is; the check below holds the converted
                # version to the same (mis)match, never a new one.
                report["checksum_mismatches"].append(vid)
            db.conn.execute("DELETE FROM version_files WHERE version_id = ?",
                            (vid,))
            _insert_files(db, vid, row["manuscript_id"], files)
            report["file_copies"] += len(files)
            report["raw_bytes"] += sum(len(_encode(t)) for t in files.values())
            if progress:
                progress("copy", done, len(ids))
        report["texts_added"] = db.one(
            "SELECT COUNT(*) AS n FROM version_texts")["n"] - before
        # Read every version back from the database (never the cache) and
        # compare it with the JSON it came from.
        for done, vid in enumerate(ids, 1):
            row = db.one("SELECT checksum, files FROM manuscript_versions "
                         "WHERE id = ?", (vid,))
            want = json.loads(row["files"])
            got = {r["name"]: _decode(r["body"]) for r in db.all(
                "SELECT f.name AS name, t.body AS body FROM version_files f "
                "JOIN version_texts t ON t.hash = f.text_hash "
                "WHERE f.version_id = ? ORDER BY f.position", (vid,))}
            if list(got.items()) != list(want.items()):
                raise RuntimeError(
                    f"version {vid} does not read back as it was stored; "
                    "nothing was converted")
            if ((checksum(got) == row["checksum"])
                    == (vid in report["checksum_mismatches"])):
                raise RuntimeError(f"version {vid} changed its checksum "
                                   "match; nothing was converted")
            if progress:
                progress("check", done, len(ids))
        db.conn.execute("ALTER TABLE manuscript_versions DROP COLUMN files")
    db._version_store_ready = True
    return report


def check_store(db: Database) -> dict:
    """Read every version of every manuscript back from the store: each
    text must hash to its key, every file a version names must have its
    text, and the files together must match the version's checksum. Also
    counts texts no version holds. Writes nothing."""
    _require_store(db)
    report = {"versions": 0, "file_copies": 0, "texts": 0, "bad_texts": [],
              "missing_texts": [], "checksum_mismatches": [],
              "unheld_texts": 0}
    good: set[str] = set()
    for row in db.all("SELECT hash, body FROM version_texts"):
        report["texts"] += 1
        if text_hash(_decode(row["body"])) == row["hash"]:
            good.add(row["hash"])
        else:
            report["bad_texts"].append(row["hash"])
    held: set[str] = set()
    for version in db.all("SELECT id, checksum FROM manuscript_versions"):
        report["versions"] += 1
        manifest = manifest_at(db, version["id"])
        report["file_copies"] += len(manifest)
        held.update(manifest.values())
        if any(d not in good for d in manifest.values()):
            report["missing_texts"].append(version["id"])
            continue
        if checksum(files_at(db, version["id"])) != version["checksum"]:
            report["checksum_mismatches"].append(version["id"])
    report["unheld_texts"] = len(good - held)
    report["ok"] = not (report["bad_texts"] or report["missing_texts"]
                        or report["checksum_mismatches"])
    return report


# A removed file and an added file are one renamed document when their
# words match at least this closely (lint.closest_by_ratio over the word
# sequences). Conservative on purpose: a missed pairing costs a
# `doc rename` to repair, a wrong one moves an essay's records onto a
# different essay.
RENAME_SIMILARITY_FLOOR = 0.9


def _pairable(name: str, text: str) -> bool:
    """A file whose text can identify it: not structure (toc.toml), not
    empty, and not the mid-rewrite placeholder, which is the same text in
    every file that holds it."""
    from .structure import TOC_FILENAME

    return (name != TOC_FILENAME and bool(text.strip())
            and not is_placeholder(text))


def pair_renames(old_files: dict[str, str],
                 new_files: dict[str, str]) -> dict[str, str]:
    """`{old name: new name}` for every file that was renamed between two
    snapshots: a name only in `old_files` paired with a name only in
    `new_files` whose text is identical or at least
    RENAME_SIMILARITY_FLOOR similar. One to one, and the same on every
    run: the closest pair is taken first, ties by name."""
    from .lint import closest_by_ratio

    removed = sorted(n for n in old_files
                     if n not in new_files and _pairable(n, old_files[n]))
    added = sorted(n for n in new_files
                   if n not in old_files and _pairable(n, new_files[n]))
    if not removed or not added:
        return {}
    scored: list[tuple[float, str, str]] = []
    for old in removed:
        old_words = old_files[old].split()
        for new in added:
            if old_files[old] == new_files[new]:
                scored.append((1.0, old, new))
                continue
            new_words = new_files[new].split()
            # The ratio cannot exceed 2·shorter/(both): skip the diff
            # when the lengths alone rule the pair out.
            if (2 * min(len(old_words), len(new_words))
                    < RENAME_SIMILARITY_FLOOR
                    * (len(old_words) + len(new_words))):
                continue
            hit, ratio = closest_by_ratio(
                old_files[old], [new_files[new]], str.split,
                floor=RENAME_SIMILARITY_FLOOR)
            if hit is not None:
                scored.append((ratio, old, new))
    pairs: dict[str, str] = {}
    taken: set[str] = set()
    for _ratio, old, new in sorted(scored, key=lambda s: (-s[0], s[1], s[2])):
        if old in pairs or new in taken:
            continue
        pairs[old] = new
        taken.add(new)
    return pairs


def massive_deletions(db: Database, manuscript: dict,
                      shrink_ratio: float = 0.7, min_chars: int = 500,
                      renames: bool = False) -> list[tuple[str, int]]:
    """Files on disk that lost more than (1 - shrink_ratio) of their content
    versus the last collected version — likely editor accidents. Returns
    (relative path, percent removed) pairs. Used only by the auto-collect
    path: a manual `collect` always proceeds. With `renames` (the
    manuscript's `document_ids` setting is on) a renamed file
    (`pair_renames`) lost nothing and is not flagged."""
    latest = latest_version(db, manuscript["id"])
    if not latest:
        return []
    old_files = files_at(db, latest["id"])
    disk = read_manuscript_files(Path(manuscript["path"]))
    renamed = pair_renames(old_files, disk) if renames else {}
    flagged = []
    for name, old_text in old_files.items():
        if len(old_text) < min_chars or name in renamed:
            continue
        new_len = len(disk.get(name, ""))
        if new_len < len(old_text) * shrink_ratio:
            removed = round(100 * (1 - new_len / len(old_text)))
            flagged.append((name, removed))
    return flagged


def _paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def _nearest_heading(paragraphs: list[str], index: int) -> str | None:
    for i in range(min(index, len(paragraphs) - 1), -1, -1):
        first_line = paragraphs[i].splitlines()[0]
        if first_line.startswith("#"):
            return first_line.lstrip("#").strip()
    return None


def _snippet(text: str, limit: int = 80) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def detect_transitions(
    db: Database, manuscript_id: str, before: dict | None, after: dict,
    renames: bool = False,
) -> list[dict]:
    """Compute editorial transitions between two manuscript versions and
    persist them as immutable observations. With `renames` (the
    manuscript's `document_ids` setting is on) a renamed file
    (`pair_renames`) is one `file_renamed` transition at its new name,
    followed by the paragraph edits made along with the rename, diffed
    against the text it had under the old name; without it, a removal
    and an addition."""
    old_files = files_at(db, before["id"]) if before else {}
    new_files = files_at(db, after["id"])
    transitions: list[dict] = []

    def record(kind: str, location: str, summary: str, detail: dict) -> None:
        row = ko_fields("tr")
        row.update(
            manuscript_id=manuscript_id,
            version_before=before["id"] if before else None,
            version_after=after["id"],
            kind=kind,
            location=location,
            summary=summary,
            detail=json.dumps(detail),
        )
        db.insert("editorial_transitions", row)
        transitions.append(row)

    renamed = pair_renames(old_files, new_files) if renames else {}
    renamed_from = {new: old for old, new in renamed.items()}
    for name in sorted(set(old_files) | set(new_files)):
        if name in renamed:
            continue  # recorded under its new name, below
        was = renamed_from.get(name, name)
        if name in renamed_from:
            record(
                "file_renamed", name,
                f"File renamed: {was} → {name}",
                {"from": was, "to": name},
            )
        elif name not in old_files:
            record(
                "file_added", name,
                f"New file: {name}",
                {"paragraphs": len(_paragraphs(new_files[name]))},
            )
            continue
        elif name not in new_files:
            record("file_removed", name, f"File removed: {name}", {})
            continue
        if old_files[was] == new_files[name]:
            continue

        old_paras = _paragraphs(old_files[was])
        new_paras = _paragraphs(new_files[name])
        matcher = difflib.SequenceMatcher(a=old_paras, b=new_paras, autojunk=False)
        for op, i1, i2, j1, j2 in matcher.get_opcodes():
            if op == "equal":
                continue
            heading = _nearest_heading(new_paras, j1) or _nearest_heading(old_paras, i1)
            location = f"{name}#{heading}" if heading else name
            if op == "insert":
                record(
                    "insert", location,
                    f"Inserted {j2 - j1} paragraph(s): {_snippet(new_paras[j1])}",
                    {"new_text": "\n\n".join(new_paras[j1:j2])},
                )
            elif op == "delete":
                record(
                    "delete", location,
                    f"Deleted {i2 - i1} paragraph(s): {_snippet(old_paras[i1])}",
                    {"old_text": "\n\n".join(old_paras[i1:i2])},
                )
            else:  # replace
                record(
                    "rewrite", location,
                    f"Rewrote {i2 - i1} → {j2 - j1} paragraph(s): {_snippet(new_paras[j1])}",
                    {
                        "old_text": "\n\n".join(old_paras[i1:i2]),
                        "new_text": "\n\n".join(new_paras[j1:j2]),
                    },
                )
    return transitions


# The mid-rewrite placeholder. `write start` on an
# EXISTING file leaves this where the essay was, instead of leaving the
# file empty.
#
# Visible text, not an HTML comment: the one path where a hidden marker
# fails is the one that matters. `export.publish_markdown` used to drop a
# mid-rewrite essay from the exported book in silence, and an HTML
# comment is stripped on the way to PDF/EPUB, so the hole would remain.
# Visible text cannot be shipped by accident, only loudly. The author
# also opens these files in Obsidian, where a comment is invisible.
#
# Bracketed `[AuthorLM: …]`, not the author's literal `<<…>>`: `<<` and
# `>>` are reserved by the pending-change grammar (form_grammar.PENDING,
# gdocs.diff_push), so a literal `<<…>>` in a manuscript file would make
# the next surgical push of it fail with a message about a margin thread
# that does not exist. The author's WORDS are kept exactly.
#
# Deterministic by construction — no id, no timestamp, no file name — so
# it is written once per writeup and never churns a version or
# invalidates the prompt-cache prefix on its own.
MARKER = "[AuthorLM: being presently rewritten]"

PLACEHOLDER = (
    MARKER + "\n"
    "\n"
    "This essay is mid-rewrite and is being drafted one beat at a time. The\n"
    "previous text is not lost — it is pinned in the database. Run\n"
    "`authorlm write status` to see the writeup, `authorlm write digest` to\n"
    "print the pinned original, or `authorlm write abandon` to put the old\n"
    "essay back. The first accepted beat replaces this placeholder, which is\n"
    "never part of a finished essay.\n"
)


def is_placeholder(text: str) -> bool:
    """Whole-file exact match (whitespace-tolerant at the edges). A file
    the author has HAND-EDITED is not a placeholder — their text is never
    silently discarded, which is the stronger rule."""
    return text.strip() == PLACEHOLDER.strip()


def marker_present(text: str) -> bool:
    """The weaker check: the marker line survived inside a file that is
    no longer just the placeholder. Warned about, never auto-repaired."""
    return MARKER in text


def _version_text(db: Database, version_id: str | None,
                  file: str) -> str | None:
    """The text a version holds for the document now called `file`, under
    whichever name it had when the version was recorded (`docs.names_of`,
    current name first). None when there is no such version or it holds
    none of the document's names."""
    version = version_by_id(db, version_id)
    if not version:
        return None
    from .docs import names_of

    manifest = manifest_at(db, version["id"])
    for name in names_of(db, version["manuscript_id"], file):
        if name in manifest:
            return text_of(db, manifest[name])
    return None


# ------------------------------------------------------------ as of a date

# Words too common to say which essay a heading belongs to.
_TITLE_STOPWORDS = frozenset(
    "the and for with from that this into upon what when where which who "
    "why how its our your their not but are was were has have had about "
    "recap".split())


def parse_as_of(value: str) -> str:
    """The cutoff for `file_as_of`, as a UTC timestamp in the format
    `created_at` is stored in (`db.now_iso`), so the two compare as text.

    Takes `YYYY-MM-DD` (through the end of that day), `YYYY-MM-DD HH:MM`
    or `YYYY-MM-DDTHH:MM[:SS]`, with an optional `Z` or UTC offset. A date
    or time without a zone is this machine's local time: the author's
    "October 10" means their October 10. Raises ValueError naming the
    accepted forms."""
    from datetime import datetime, time, timezone

    raw = (value or "").strip()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
            moment = datetime.combine(datetime.fromisoformat(raw).date(),
                                      time(23, 59, 59, 999999))
        else:
            moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(
            f"'{value}' is not a date; use YYYY-MM-DD (the whole day) or "
            f"YYYY-MM-DDTHH:MM (local time unless it ends in Z or an offset)")
    if moment.tzinfo is None:
        moment = moment.astimezone()  # naive: this machine's local time
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def title_words(text: str | None, file: str) -> list[str]:
    """The words that say which essay `file` is: its first heading's and
    its file name's, lowercased, common words left out."""
    heading = next((line for line in (text or "").splitlines()
                    if line.startswith("#")), "")
    stem = Path(file).stem
    words: list[str] = []
    for word in re.findall(r"[a-z0-9]+", f"{heading} {stem}".lower()):
        if len(word) >= 3 and word not in _TITLE_STOPWORDS and word not in words:
            words.append(word)
    return words


def file_as_of(db: Database, manuscript: dict, file: str, as_of: str,
               current_text: str | None = None) -> dict:
    """A manuscript file's text in the latest snapshot collected at or
    before `as_of` (`parse_as_of`). Read-only.

    The snapshot's book is its own reading order (its toc.toml, minus the
    files that toc never lists), as `engines.catalogue` limits the current
    book; a file outside it is not served. `file` is looked up under every
    name the document has carried (`docs.names_of`), then as a name the
    snapshot itself holds (a file since split off or retired, such as an
    old recapitulation.md). `current_text` is the file's text now, when
    the caller has it (for its title words and to say whether it changed).
    Returns

        {version_no, created_at, cutoff, requested, file (the snapshot's
         name, or None), text (or None), book (the snapshot's files in
         reading order), title_words, matches: [{file, section, heading,
         words}]}

    `matches`, when the file is absent, are the snapshot's section
    headings that share the most title words (whole words, a plural
    allowed; at least half of them): where the essay's text most likely
    stood then.
    Raises LookupError when no snapshot is that old, ValueError for a
    date it cannot read."""
    from . import docs, structure
    from .summaries import sections_of

    cutoff = parse_as_of(as_of)
    _require_store(db)
    row = db.one(
        f"SELECT {VERSION_COLUMNS} FROM manuscript_versions "
        "WHERE manuscript_id = ? AND created_at <= ? "
        "ORDER BY created_at DESC, version_no DESC LIMIT 1",
        (manuscript["id"], cutoff))
    if not row:
        first = db.one(
            "SELECT version_no, created_at FROM manuscript_versions "
            "WHERE manuscript_id = ? ORDER BY version_no LIMIT 1",
            (manuscript["id"],))
        raise LookupError(
            f"no snapshot was collected at or before {as_of}"
            + (f"; the first is v{first['version_no']} of {first['created_at']}"
               if first else "; none has been collected yet"))
    files = files_at(db, row["id"])
    order, unlisted = structure.reading_order(files)
    book = [name for name in order if name not in set(unlisted)]
    requested = (file or "").strip()
    found = next((name for name in docs.names_of(db, manuscript["id"], requested)
                  if name in book), None)
    if found is None and f"{requested}.md" in book:
        found = f"{requested}.md"
    words = title_words(current_text if current_text is not None
                        else (files.get(found) if found else None), requested)
    matches: list[dict] = []
    if found is None and words:
        need = max(1, (len(words) + 1) // 2)
        for name in book:
            for n, sec in enumerate(sections_of(files[name]), 1):
                heading = (sec["heading"] or "").lower()
                shared = [w for w in words
                          if re.search(rf"\b{re.escape(w)}s?\b", heading)]
                if len(shared) >= need:
                    matches.append({"file": name, "section": n,
                                    "heading": sec["heading"], "words": shared})
        # Only the best: "## The Second Cup" for second-cup.md, not every
        # heading that says "cup".
        best = max((len(m["words"]) for m in matches), default=0)
        matches = [m for m in matches if len(m["words"]) == best]
    return {"version_no": row["version_no"], "created_at": row["created_at"],
            "cutoff": cutoff, "requested": requested, "file": found,
            "text": files[found] if found else None, "book": book,
            "title_words": words, "matches": matches[:10]}
