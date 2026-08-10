"""Manuscript structure: the TOC as authoritative reading order.

`toc.toml` in the manuscript root is the *structural* source of truth:
its ordered `[[chapter]]` tables define the reading order that every
ordering-sensitive computation uses — first mentions, prerequisite gaps,
definition precedence (the first location in reading order is a concept's
primary location), plan placement, and hierarchical extraction chunks.

Shape (order = document order; depth via `parent`; attributes ride along):

    [[chapter]]
    file = "title.md"
    matter = "front"

    [[chapter]]
    file = "preface.md"
    parent = "chapter1.md"
    matter = "front"

`matter` is per-file: front | main (default) | back. Deterministic
filters key off it — front matter is a window (prerequisite scanning
ignores it), back matter is reference (manifest statistics split on it).

Files not referenced fall to the end alphabetically and are flagged in
the briefing. Without a parseable toc.toml, reading order is alphabetical
(the historical behavior).

toc.toml itself is versioned and diffed like any file, but excluded from
all concept scanning — it is structure, not content.
"""

from __future__ import annotations

import re
import tomllib

TOC_FILENAME = "toc.toml"

MATTER_VALUES = ("front", "main", "back")


def is_structural(name: str) -> bool:
    return name == TOC_FILENAME


def content_files(files: dict[str, str]) -> dict[str, str]:
    """Manuscript files that count as prose (structural files removed)."""
    return {k: v for k, v in files.items() if not is_structural(k)}


def _toc_chapters(toc_text: str) -> list[dict]:
    """The ordered [[chapter]] tables, or [] when unparseable (liberal
    doctrine: a broken TOC degrades to alphabetical order, never raises)."""
    try:
        data = tomllib.loads(toc_text)
    except (tomllib.TOMLDecodeError, TypeError):
        return []
    chapters = data.get("chapter", [])
    return [c for c in chapters if isinstance(c, dict) and c.get("file")]


def parse_toc_tree(toc_text: str) -> list[tuple[str, int]]:
    """Ordered (filename, depth) entries from toc.toml. Depth derives
    from the `parent` chain; entries whose parent is absent or forward-
    referenced sit at the root."""
    depth_of: dict = {}
    out: list[tuple[str, int]] = []
    for c in _toc_chapters(toc_text):
        parent = c.get("parent")
        depth = depth_of.get(parent, -1) + 1 if parent else 0
        depth_of[c["file"]] = depth
        out.append((c["file"], depth))
    return out


def toc_attrs(toc_text: str) -> dict[str, dict]:
    """Per-file attributes riding on the TOC (everything but file/parent)."""
    return {c["file"]: {k: v for k, v in c.items()
                        if k not in ("file", "parent")}
            for c in _toc_chapters(toc_text)}


def matter_map(files: dict[str, str]) -> dict[str, str]:
    """filename → front|main|back for the manuscript's files, from the
    TOC's `matter` attribute (default main; unknown values read as main)."""
    attrs = toc_attrs(files.get(TOC_FILENAME) or "")
    return {name: (attrs.get(name, {}).get("matter")
                   if attrs.get(name, {}).get("matter") in MATTER_VALUES
                   else "main")
            for name in content_files(files)}


def serialize_toc_tree(entries: list[tuple[str, int]],
                       attrs: dict[str, dict] | None = None) -> str:
    """toc.toml text for ordered (filename, depth) entries, carrying the
    given per-file attributes through (Doc-side reorders must not strip
    matter assignments)."""
    lines = ["# Table of contents — structural: order is reading order.",
             "# matter = \"front\" | \"main\" (default) | \"back\""]
    parents = tree_to_parents(entries)
    for name, parent in parents:
        lines += ["", "[[chapter]]", f'file = "{name}"']
        if parent:
            lines.append(f'parent = "{parent}"')
        for key, value in sorted(((attrs or {}).get(name) or {}).items()):
            if isinstance(value, str):
                lines.append(f'{key} = "{value}"')
    return "\n".join(lines) + "\n"


def tree_to_parents(entries: list[tuple[str, int]]) -> list[tuple]:
    """DFS (name, parent-name-or-None) pairs from (name, depth) entries —
    the comparable structure shared with the Doc's tab tree."""
    out: list[tuple] = []
    stack: list[tuple[str, int]] = []
    for name, depth in entries:
        while stack and stack[-1][1] >= depth:
            stack.pop()
        out.append((name, stack[-1][0] if stack else None))
        stack.append((name, depth))
    return out


def parents_to_tree(pairs: list[tuple]) -> list[tuple[str, int]]:
    """Inverse of tree_to_parents (pairs must be in DFS order)."""
    depth_of: dict = {None: -1}
    out: list[tuple[str, int]] = []
    for name, parent in pairs:
        depth = depth_of.get(parent, -1) + 1
        depth_of[name] = depth
        out.append((name, depth))
    return out


def reading_order(files: dict[str, str]) -> tuple[list[str], list[str]]:
    """(ordered content-file names, files-missing-from-toc).

    TOC order when toc.toml parses (depth-first flatten of the tree);
    alphabetical otherwise. Unlisted files are appended alphabetically
    and reported so the author can complete the TOC."""
    names = sorted(content_files(files))
    toc_text = files.get(TOC_FILENAME)
    if not toc_text:
        return names, []
    ordered = [n for n, _ in parse_toc_tree(toc_text) if n in names]
    if not ordered:
        # Legacy formats (numbered lists, prose lines): scan for names.
        for line in toc_text.splitlines():
            for name in names:
                if name not in ordered and name in line:
                    ordered.append(name)
    if not ordered:
        return names, []
    unlisted = [n for n in names if n not in ordered]
    return ordered + unlisted, unlisted


def ordered_items(files: dict[str, str]) -> list[tuple[str, str]]:
    """(name, text) pairs of content files in reading order."""
    order, _ = reading_order(files)
    return [(name, files[name]) for name in order if name in files]
