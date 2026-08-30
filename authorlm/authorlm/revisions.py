"""Revision Collector and Revision Analyzer (RFC AuthorLM §17.3–§17.4).

The collector snapshots the manuscript directory into an immutable,
checksummed version. The analyzer computes editorial transitions between
successive versions by diffing paragraph sequences per file.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import sys
from pathlib import Path

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
        # A file carrying pending-change forms is mid-settle. The
        # ratified law (threads.py's docstring) is that the canonical
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
        # `strip_pending` is pure and a no-op on text with no forms, so
        # this is unconditional; it warns rather than guessing on stray
        # or unbalanced markers, and the only code that sees markers is
        # the settle code, which reads Path.read_text directly and owns
        # the grammar (filter-pass design §2.3).
        from . import threads as _threads
        files[rel] = _threads.strip_pending(strip_embed_lines(text))[0]
    return files


def checksum(files: dict[str, str]) -> str:
    digest = hashlib.sha256()
    for name in sorted(files):
        digest.update(name.encode())
        digest.update(b"\x00")
        digest.update(files[name].encode())
        digest.update(b"\x00")
    return digest.hexdigest()


def collect_revision(
    db: Database, manuscript: dict, session_id: str | None, source: str = "snapshot"
) -> dict | None:
    """Snapshot the manuscript. Returns the new version row as a dict,
    or None if nothing changed since the last version.

    Raises ManuscriptRootUnreadable — refuses to record a version — if
    the root is missing or unreadable, regardless of the caller: an
    unreadable directory must never be recorded as "every file removed"
    (it-258752ea91f9). This runs unconditionally (not just on the
    auto-collect path) because 19 of 20 api.collect() call sites,
    including the unattended _catch_up, pass auto=False and would
    otherwise bypass the mass-deletion guard entirely."""
    root = Path(manuscript["path"])
    check_manuscript_root(root)
    files = read_manuscript_files(root)
    digest = checksum(files)

    last = db.one(
        "SELECT * FROM manuscript_versions WHERE manuscript_id = ? "
        "ORDER BY version_no DESC LIMIT 1",
        (manuscript["id"],),
    )
    if last and last["checksum"] == digest:
        return None

    row = ko_fields("mv")
    row.update(
        manuscript_id=manuscript["id"],
        version_no=(last["version_no"] + 1) if last else 1,
        checksum=digest,
        files=json.dumps(files),
        source=source,
        session_id=session_id,
    )
    db.insert("manuscript_versions", row)
    return row


def massive_deletions(db: Database, manuscript: dict,
                      shrink_ratio: float = 0.7, min_chars: int = 500) -> list[tuple[str, int]]:
    """Files on disk that lost more than (1 - shrink_ratio) of their content
    versus the last collected version — likely editor accidents. Returns
    (relative path, percent removed) pairs. Used only by the auto-collect
    path: a manual `collect` always proceeds."""
    latest = db.one(
        "SELECT files FROM manuscript_versions WHERE manuscript_id = ? "
        "ORDER BY version_no DESC LIMIT 1",
        (manuscript["id"],),
    )
    if not latest:
        return []
    old_files: dict[str, str] = loads(latest["files"], {})
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
    db: Database, manuscript_id: str, before: dict | None, after: dict
) -> list[dict]:
    """Compute editorial transitions between two manuscript versions and
    persist them as immutable observations."""
    old_files: dict[str, str] = loads(before["files"], {}) if before else {}
    new_files: dict[str, str] = loads(after["files"], {})
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

    for name in sorted(set(old_files) | set(new_files)):
        if name not in old_files:
            record(
                "file_added", name,
                f"New file: {name}",
                {"paragraphs": len(_paragraphs(new_files[name]))},
            )
            continue
        if name not in new_files:
            record("file_removed", name, f"File removed: {name}", {})
            continue
        if old_files[name] == new_files[name]:
            continue

        old_paras = _paragraphs(old_files[name])
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
