"""Revision Collector, version history and Revision Analyzer (RFC AuthorLM
§17.3–§17.4).

The collector snapshots the manuscript directory into an immutable,
checksummed version. The analyzer computes editorial transitions between
successive versions by diffing paragraph sequences per file.

Version history has one door, and it is this module. A caller asks for a
`Version` (which version, when, how it was recorded) and then for text:
`file_at`, `files_at`, `changes`. `record_version` is the only writer. No
caller reads the `manuscript_versions` table or the JSON it holds, so
where the text of a version is kept can change without a caller
noticing.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from . import form_grammar
from .db import Database, ko_fields, loads

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

@dataclass(frozen=True, slots=True)
class Version:
    """One collected version of a manuscript: which one it is, and when
    and how it was recorded. It never carries the text; `file_at`,
    `files_at` and `changes` read that."""
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


_VERSION_COLUMNS = ("id, manuscript_id, version_no, checksum, source, "
                    "session_id, created_at")


def _version(row) -> Version:
    return Version(**{name: row[name] for name in Version.__slots__})


def version_by_id(db: Database, version_id: str | None) -> Version | None:
    if not version_id:
        return None
    row = db.one(f"SELECT {_VERSION_COLUMNS} FROM manuscript_versions "
                 "WHERE id = ?", (version_id,))
    return _version(row) if row else None


def latest_versions(db: Database, manuscript_id: str,
                    count: int) -> list[Version]:
    """The `count` most recent versions, newest first."""
    # Two steps, deliberately. `source` and `session_id` sit after `files`
    # in the row, so SQLite walks a version's whole text to reach them;
    # ordering on them would walk every version of the manuscript. The
    # ids come from the leading columns alone, then each row is read by
    # primary key.
    ids = db.all(
        "SELECT id FROM manuscript_versions WHERE manuscript_id = ? "
        "ORDER BY version_no DESC LIMIT ?", (manuscript_id, count))
    found = (version_by_id(db, row["id"]) for row in ids)
    return [version for version in found if version]


def latest_version(db: Database, manuscript_id: str) -> Version | None:
    newest = latest_versions(db, manuscript_id, 1)
    return newest[0] if newest else None


def version_by_number(db: Database, manuscript_id: str,
                     version_no: int) -> Version | None:
    row = db.one(
        "SELECT id FROM manuscript_versions WHERE manuscript_id = ? "
        "AND version_no = ?", (manuscript_id, version_no))
    return version_by_id(db, row["id"]) if row else None


def version_as_of(db: Database, manuscript_id: str,
                  moment: str) -> Version | None:
    """The version that was current at `moment` (an ISO time): the most
    recent one recorded at or before it."""
    row = db.one(
        "SELECT id FROM manuscript_versions WHERE manuscript_id = ? AND "
        "created_at <= ? ORDER BY version_no DESC LIMIT 1",
        (manuscript_id, moment))
    return version_by_id(db, row["id"]) if row else None


def list_versions(db: Database, manuscript_id: str) -> list[Version]:
    """Every version, oldest first."""
    return [_version(row) for row in db.all(
        f"SELECT {_VERSION_COLUMNS} FROM manuscript_versions "
        "WHERE manuscript_id = ? ORDER BY version_no", (manuscript_id,))]


def count_versions(db: Database, manuscript_id: str) -> int:
    return db.one("SELECT COUNT(*) AS n FROM manuscript_versions "
                  "WHERE manuscript_id = ?", (manuscript_id,))["n"]


def last_recorded_at(db: Database, session_id: str) -> str | None:
    """When the session last recorded a version, or None if it never did."""
    return db.one("SELECT MAX(created_at) AS latest FROM manuscript_versions "
                  "WHERE session_id = ?", (session_id,))["latest"]


def file_at(db: Database, version_id: str | None, file: str) -> str | None:
    """One file's text at a version. None when there is no such version
    or the version does not hold the file. Reads that file only."""
    if not version_id:
        return None
    row = db.one(
        "SELECT entry.value AS text FROM manuscript_versions AS v, "
        "json_each(v.files) AS entry WHERE v.id = ? AND entry.key = ?",
        (version_id, file))
    return row["text"] if row else None


def files_at(db: Database, version_id: str | None) -> dict[str, str]:
    """Every file's text at a version, by relative path. Empty when there
    is no such version."""
    if not version_id:
        return {}
    row = db.one("SELECT files FROM manuscript_versions WHERE id = ?",
                 (version_id,))
    return loads(row["files"], {}) if row else {}


def file_counts(db: Database, manuscript_id: str) -> dict[str, int]:
    """How many files each version of the manuscript holds, by version id."""
    return {row["id"]: row["n"] for row in db.all(
        "SELECT id, (SELECT COUNT(*) FROM json_each(files)) AS n "
        "FROM manuscript_versions WHERE manuscript_id = ?", (manuscript_id,))}


def changes(db: Database, older_id: str | None,
            newer_id: str) -> dict[str, FileChange]:
    """The files that differ between two versions, by relative path, in
    path order. `older_id=None` compares against an empty manuscript, so
    every file reads as added."""
    old_files = files_at(db, older_id)
    new_files = files_at(db, newer_id)
    return {
        name: FileChange(old_files.get(name), new_files.get(name))
        for name in sorted(set(old_files) | set(new_files))
        if old_files.get(name) != new_files.get(name)
    }


def diff_pair(db: Database, manuscript_id: str,
              numbers: list[int]) -> tuple[Version | None, Version]:
    """The (older, newer) versions a diff compares: the lowest and highest
    of two or more version numbers; or one numbered version and the one
    numbered just below it; or, with no number, the latest two. The older
    is None when there is nothing below the newer. Raises LookupError for
    a number that is not a version."""
    newest = latest_versions(db, manuscript_id, 2)
    if not newest:
        raise LookupError("no versions collected yet")
    found = {n: version_by_number(db, manuscript_id, n) for n in numbers}
    for number in numbers:
        if found[number] is None:
            raise LookupError(
                f"no version v{number} (have v1..v{newest[0].version_no})")
    if len(numbers) >= 2:
        return found[min(numbers)], found[max(numbers)]
    if numbers:
        return (version_by_number(db, manuscript_id, numbers[0] - 1),
                found[numbers[0]])
    return (newest[1] if len(newest) > 1 else None), newest[0]


def record_version(db: Database, manuscript_id: str, files: dict[str, str],
                   session_id: str | None,
                   source: str = "snapshot") -> Version | None:
    """Record `files` as the manuscript's next version. Returns it, or
    None when they are what the latest version already holds."""
    digest = checksum(files)
    last = latest_version(db, manuscript_id)
    if last and last.checksum == digest:
        return None

    row = ko_fields("mv")
    row.update(
        manuscript_id=manuscript_id,
        version_no=(last.version_no + 1) if last else 1,
        checksum=digest,
        files=json.dumps(files),
        source=source,
        session_id=session_id,
    )
    db.insert("manuscript_versions", row)
    return _version(row)


def collect_revision(
    db: Database, manuscript: dict, session_id: str | None, source: str = "snapshot"
) -> Version | None:
    """Snapshot the manuscript. Returns the new version, or None if
    nothing changed since the last version.

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


def massive_deletions(db: Database, manuscript: dict,
                      shrink_ratio: float = 0.7, min_chars: int = 500) -> list[tuple[str, int]]:
    """Files on disk that lost more than (1 - shrink_ratio) of their content
    versus the last collected version — likely editor accidents. Returns
    (relative path, percent removed) pairs. Used only by the auto-collect
    path: a manual `collect` always proceeds."""
    latest = latest_version(db, manuscript["id"])
    if not latest:
        return []
    old_files = files_at(db, latest.id)
    disk = read_manuscript_files(Path(manuscript["path"]))
    flagged = []
    for name, old_text in old_files.items():
        if len(old_text) < min_chars:
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
    db: Database, manuscript_id: str, before: Version | None, after: Version
) -> list[dict]:
    """Compute editorial transitions between two manuscript versions and
    persist them as immutable observations."""
    transitions: list[dict] = []

    def record(kind: str, location: str, summary: str, detail: dict) -> None:
        row = ko_fields("tr")
        row.update(
            manuscript_id=manuscript_id,
            version_before=before.id if before else None,
            version_after=after.id,
            kind=kind,
            location=location,
            summary=summary,
            detail=json.dumps(detail),
        )
        db.insert("editorial_transitions", row)
        transitions.append(row)

    changed = changes(db, before.id if before else None, after.id)
    for name, change in changed.items():
        if change.old is None:
            record(
                "file_added", name,
                f"New file: {name}",
                {"paragraphs": len(_paragraphs(change.new))},
            )
            continue
        if change.new is None:
            record("file_removed", name, f"File removed: {name}", {})
            continue

        old_paras = _paragraphs(change.old)
        new_paras = _paragraphs(change.new)
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
