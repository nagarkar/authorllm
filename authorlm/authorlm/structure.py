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

# Sidecars: structural, but MARKDOWN and Doc-mirrored (§15.22). The
# author edits `pronunciations.md` in the Doc exactly like an essay, and
# it is never part of the book.
#
# The flag is the FILENAME, in this tuple, and not a marker inside the
# file. A marker can be deleted by a careless Doc edit or eaten by a
# round trip, and the failure mode is that the dictionary silently
# reclassifies as an essay and SHIPS INSIDE THE BOOK — the same argument
# that gave `_filters/` and `_lenses/` two directories instead of one
# with a kind marker, applied to a file. `toc.toml` uses a filename for
# the same reason. A configurable name is not supported and not wanted:
# one manuscript, one dictionary.
SIDECAR_FILES = ("pronunciations.md", "manifest.md")


def is_sidecar(name: str) -> bool:
    """Structural, but markdown and Doc-mirrored.

    A separate predicate from `is_structural` rather than an inline
    literal, because exactly one site must NOT exclude it — the Doc tab
    list (`gdocs._reading_order_files`) — and that site has to be
    greppable."""
    return name in SIDECAR_FILES


def is_structural(name: str) -> bool:
    return name == TOC_FILENAME or is_sidecar(name)


def refuse_sidecar(name: str) -> None:
    """The named refusal, shared by every verb that could be pointed at
    the dictionary and must not run on it: `filter run|prelude|record|
    resolve`, `lens run`, and `intent --scope`.

    Every one of these ALREADY refuses through the generic
    "not in the manuscript's reading order" path — the sidecar is out of
    the reading order, so the exclusion is structural rather than
    conditional. But that wording is misleading for a file that plainly
    exists and that the author can see as a tab in their own Doc, so the
    refusal says what the file IS and where the work actually happens.
    One helper rather than three, so the three cannot drift."""
    if is_sidecar(name):
        raise ValueError(
            f"{name} is the pronunciation dictionary, not an essay — no "
            f"filter, no lens and no critique pass runs on it, and it "
            f"never ships inside the book. Edit it directly (or in its "
            f"Doc tab), or rule on proposed rows with 'proposal review'.")


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


# The placement sentinel for an essay that opens the book, spelled the
# same way summaries.PLACEMENT_START spells it. Not imported from there:
# summaries reads this module, and the cycle is not worth one string.
PLACEMENT_START = "start"

_TOC_CHAPTER_LINE = "[[chapter]]"


def _file_line_re(name: str) -> re.Pattern:
    return re.compile(rf'^\s*file\s*=\s*["\']{re.escape(name)}["\']\s*$')


def insert_toc_entry(toc_text: str, file: str,
                     after: str | None) -> tuple[str, str] | None:
    """Register `file` in toc.toml immediately after `after` (or, for the
    PLACEMENT_START sentinel, at the head). Returns
    `(new_toc_text, stanza)`, or None when the anchor cannot be found —
    the caller warns and prints the stanza rather than guessing.

    PURE TEXT INSERTION, deliberately: a `serialize_toc_tree` round trip
    would silently drop hand-written comments and every attribute the
    serializer does not know about (the live toc carries `matter` and
    `illustrations`). Nothing that was already in the file is rewritten.

    The stanza carries the anchor's `parent` — structural depth, which is
    deterministic — and nothing else. `matter` and the rest are semantic;
    the author sets them.
    """
    # Split and re-join on the file's OWN dominant line ending. `splitlines`
    # + `"\n".join` silently relaid a CRLF toc in LF, rewriting every line
    # the insertion never touched — the exact thing K3's "never rewrites
    # anything it did not add" forbids. split/join on one separator is
    # lossless, trailing newline (or its absence) included.
    crlf = toc_text.count("\r\n")
    newline = "\r\n" if crlf > toc_text.count("\n") - crlf else "\n"
    lines = toc_text.split(newline)
    already = _file_line_re(file)
    if any(already.match(line) for line in lines):
        return toc_text, ""            # idempotent

    chapter_at = [i for i, line in enumerate(lines)
                  if line.strip() == _TOC_CHAPTER_LINE]
    stanza_lines = [_TOC_CHAPTER_LINE, f'file = "{file}"']

    if not after or after == PLACEMENT_START:
        parent = None
        insert_at = chapter_at[0] if chapter_at else len(lines)
        block = stanza_lines + [""]
    else:
        anchor = _file_line_re(after)
        anchor_at = next((i for i, line in enumerate(lines)
                          if anchor.match(line)), None)
        if anchor_at is None:
            return None
        parent = next((c.get("parent") for c in _toc_chapters(toc_text)
                       if c.get("file") == after), None)
        if parent:
            stanza_lines.append(f'parent = "{parent}"')
        # Forward to the start of the next chapter table (or EOF), then
        # back over everything that belongs to THAT table rather than to
        # the anchor's: the blank lines that separate them, and any
        # comment lines, which annotate the table below them. Stopping at
        # a comment would drop the new stanza between the comment and the
        # table it describes — silently re-attributing the comment to the
        # new essay, and gluing the new stanza to its neighbour with no
        # separator.
        insert_at = next((i for i in chapter_at if i > anchor_at), len(lines))
        while insert_at > 0 and (not lines[insert_at - 1].strip()
                                 or lines[insert_at - 1].lstrip()
                                 .startswith("#")):
            insert_at -= 1
        block = [""] + stanza_lines

    new_lines = lines[:insert_at] + block + lines[insert_at:]
    return (newline.join(new_lines),
            newline.join(stanza_lines) + newline)


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


def select_chapters(files: dict[str, str],
                    names: list[str]) -> list[str]:
    """The named chapters plus their TOC descendants, in reading order.

    Naming a parent means naming the part it heads — `ascending.md`
    selects the chapters filed under it. Names may omit the `.md`.
    Raises LookupError on a name the manuscript does not have."""
    order, _unlisted = reading_order(files)
    positions = {name: i for i, name in enumerate(order)}
    depths = dict(parse_toc_tree(files.get(TOC_FILENAME) or ""))

    wanted: set[str] = set()
    for raw in names:
        name = raw.strip()
        if name not in positions and f"{name}.md" in positions:
            name = f"{name}.md"
        if name not in positions:
            raise LookupError(
                f"no such chapter '{raw}' (one of: {', '.join(order)})")
        wanted.add(name)
        # Descendants: the run of deeper entries following this one.
        depth = depths.get(name)
        if depth is None:
            continue
        for later in order[positions[name] + 1:]:
            if depths.get(later, 0) <= depth:
                break
            wanted.add(later)
    return [name for name in order if name in wanted]


def ordered_items(files: dict[str, str]) -> list[tuple[str, str]]:
    """(name, text) pairs of content files in reading order."""
    order, _ = reading_order(files)
    return [(name, files[name]) for name in order if name in files]
