"""Manuscript structure: the TOC as authoritative reading order.

`toc.md` in the manuscript root is a *structural* document, not prose: its
ordered file references define the reading order that every
ordering-sensitive computation uses — first mentions, prerequisite gaps,
definition precedence (the first location in reading order is a concept's
primary location), plan placement, and hierarchical extraction chunks.

Parsing is deliberately liberal: any line mentioning a known manuscript
file name (with or without path, in plain text, list items, or markdown
links) counts, in order of appearance. Files not referenced fall to the
end alphabetically and are flagged in the briefing. Without a parseable
toc.md, reading order is alphabetical (the historical behavior).

toc.md itself is versioned and diffed like any file, but excluded from all
concept scanning — it is structure, not content.
"""

from __future__ import annotations

import re

TOC_FILENAME = "toc.md"


def is_structural(name: str) -> bool:
    return name == TOC_FILENAME


def content_files(files: dict[str, str]) -> dict[str, str]:
    """Manuscript files that count as prose (structural files removed)."""
    return {k: v for k, v in files.items() if not is_structural(k)}


# A TOC entry: "- name.md" with two spaces of indentation per nesting
# level. Depth is structural (child essays nest under their parent) and
# mirrors the master Doc's tab hierarchy via the gdocs sync.
_TOC_ENTRY = re.compile(r"^(\s*)-\s+(\S+\.md)\s*$")


def parse_toc_tree(toc_text: str) -> list[tuple[str, int]]:
    """Ordered (filename, depth) entries from toc.md's dash list.
    Lines that aren't dash entries (headings, prose) are ignored."""
    out: list[tuple[str, int]] = []
    for line in toc_text.splitlines():
        m = _TOC_ENTRY.match(line)
        if m:
            out.append((m.group(2), len(m.group(1)) // 2))
    return out


def serialize_toc_tree(entries: list[tuple[str, int]]) -> str:
    """toc.md text for ordered (filename, depth) entries."""
    lines = ["# Table of Contents", ""]
    lines += [f"{'  ' * depth}- {name}" for name, depth in entries]
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

    TOC order when toc.md parses (depth-first flatten of the tree);
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
