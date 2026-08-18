"""Mechanical document management (RFC §16.1 Level 3).

Creating, retiring, and reviving chapter files are mechanical edits — safe
to execute directly, unlike conceptual edits which remain suggestions.
Retiring moves a file into `_retired/` (invisible to observation, like all
underscore directories), so the next collection records its removal while
every historical version and transition remains replayable. Reviving moves
it back. Nothing is ever deleted.
"""

from __future__ import annotations

import re
from pathlib import Path

from .db import Database
from .revisions import iter_manuscript_paths

RETIRED_DIR = "_retired"
_UNSAFE = re.compile(r'[\\/:*?"<>|#]')


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


def add_doc(manuscript: dict, name: str, title: str | None = None) -> Path:
    root = Path(manuscript["path"])
    filename = doc_filename(name)
    target = root / filename
    if target.exists():
        raise FileExistsError(f"{filename} already exists")
    if (root / RETIRED_DIR / filename).exists():
        raise FileExistsError(
            f"{filename} exists in {RETIRED_DIR}/ — revive it instead: doc revive {filename}"
        )
    heading = title or target.stem.replace("-", " ").replace("_", " ").strip()
    target.write_text(f"# {heading}\n\n", encoding="utf-8")
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
