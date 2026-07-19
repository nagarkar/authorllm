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


def reading_order(files: dict[str, str]) -> tuple[list[str], list[str]]:
    """(ordered content-file names, files-missing-from-toc).

    TOC order when toc.md parses; alphabetical otherwise. Unlisted files
    are appended alphabetically and reported so the author can complete
    the TOC."""
    names = sorted(content_files(files))
    toc_text = files.get(TOC_FILENAME)
    if not toc_text:
        return names, []
    ordered: list[str] = []
    for line in toc_text.splitlines():
        for name in names:
            if name in ordered:
                continue
            stem = re.escape(name.rsplit(".", 1)[0])
            if name in line or re.search(rf"\b{stem}\b", line):
                ordered.append(name)
    if not ordered:
        return names, []
    unlisted = [n for n in names if n not in ordered]
    return ordered + unlisted, unlisted


def ordered_items(files: dict[str, str]) -> list[tuple[str, str]]:
    """(name, text) pairs of content files in reading order."""
    order, _ = reading_order(files)
    return [(name, files[name]) for name in order if name in files]
