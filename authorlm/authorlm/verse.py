"""Verse structure — the one seam that knows what a poem is.

Design: docs/verse-and-cast-design.md §1 (the `form` flag) and the
poem-grain ruling of 2026-09-25 ("we're simulating this class based on
where the quotes are; that's not a reliable mechanism"). A file marked
`form = "verse"` in toc.toml is made of poems: each `##` heading and
everything to the next heading — title, thesis line, stanzas, and later
the commentary. Everything that needs a poem as a unit (the lens runner's
poem grain, the verse sweep, the placement check) reads it from here, so
the poem is found by structure, never inferred from where a quote fell.
"""
from __future__ import annotations

import hashlib
import re

from .structure import TOC_FILENAME, toc_attrs

FORM_VALUES = ("prose", "verse")
DEFAULT_POEM_LEVEL = 2   # `## Title` — the toc attribute `poem_level` overrides
_HEADING = re.compile(r"^(#{1,6})\s+(?P<text>.+?)\s*$")
_BOLD = re.compile(r"^\*\*(.+)\*\*$")
_ITALIC_LINE = re.compile(r"^\*[^*].*[^*]\*$|^\*[^*]\*$")


def form_map(files: dict[str, str]) -> dict[str, str]:
    """filename → prose|verse, from the TOC's `form` attribute (default
    prose; unknown values read as prose)."""
    attrs = toc_attrs(files.get(TOC_FILENAME) or "")
    return {name: (a.get("form") if a.get("form") in FORM_VALUES else "prose")
            for name, a in attrs.items()}


def is_verse(files: dict[str, str], rel: str) -> bool:
    return form_map(files).get(rel) == "verse"


def poem_level(files: dict[str, str], rel: str) -> int:
    """The heading level that opens a poem in this file: the toc's
    `poem_level` attribute (1–6), default 2. Declared, never guessed —
    a book whose poems sit at H3 says so beside its `form` flag."""
    attrs = toc_attrs(files.get(TOC_FILENAME) or "").get(rel, {})
    level = attrs.get("poem_level", DEFAULT_POEM_LEVEL)
    try:
        level = int(level)
    except (TypeError, ValueError):
        return DEFAULT_POEM_LEVEL
    return level if 1 <= level <= 6 else DEFAULT_POEM_LEVEL


def verse_files(files: dict[str, str]) -> dict[str, int]:
    """Every verse file with its poem heading level."""
    return {rel: poem_level(files, rel)
            for rel, form in form_map(files).items() if form == "verse"}


def heading_levels(text: str) -> dict[int, int]:
    """How many headings the text has at each level — what the collect
    report shows when a verse file has no poems at its declared level."""
    counts: dict[int, int] = {}
    for ln in text.split("\n"):
        m = _HEADING.match(ln.strip())
        if m and heading_title(ln):
            lvl = len(m.group(1))
            counts[lvl] = counts.get(lvl, 0) + 1
    return counts


def verse_report(files: dict[str, str]) -> list[dict]:
    """Per verse file: poem count at the declared level, and a warning
    when it is zero while headings exist at other levels. Run at every
    collect so a wrong `poem_level` is caught on import, not when a lens
    refuses."""
    out = []
    for rel, level in verse_files(files).items():
        text = files.get(rel, "")
        n = len(poems_of(text, level))
        entry = {"file": rel, "level": level, "poems": n}
        if n == 0:
            found = {k: v for k, v in heading_levels(text).items()
                     if k != 1 and k != level}
            entry["warning"] = (
                f"{rel} is marked verse but has no poems at heading level "
                f"{level}" + (f"; headings found at level(s) "
                              + ", ".join(f"{k} ({v})" for k, v in sorted(found.items()))
                              + " — set poem_level in toc.toml" if found
                              else " and no other sub-headings"))
        out.append(entry)
    return out


def heading_title(line: str) -> str | None:
    """The plain title of a heading line: `## **Fear**` → `Fear`. None
    for a line that is not a heading."""
    m = _HEADING.match(line.strip())
    if not m:
        return None
    text = m.group("text").strip()
    b = _BOLD.match(text)
    return (b.group(1) if b else text).strip()


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def poems_of(text: str, level: int = DEFAULT_POEM_LEVEL) -> list[dict]:
    """Every poem in a verse file, in order. A poem is a heading at
    `level` (default `##`) and everything to the next heading at that
    level or shallower. Each entry:
    {n (1-based), title, start, end (line indices, end exclusive), text
    (the span, heading included), thesis (the italic line under the
    heading, or None), sha}. Text before the first `##` is the section
    frame (see `frame_of`), not a poem."""
    lines = text.split("\n")
    mark = "#" * level + " "

    def shallower_or_same(ln: str) -> bool:
        m = _HEADING.match(ln.strip())
        return bool(m and heading_title(ln) and len(m.group(1)) <= level)

    starts = [i for i, ln in enumerate(lines)
              if ln.startswith(mark) and heading_title(ln)]
    poems: list[dict] = []
    for k, start in enumerate(starts):
        end = len(lines)
        for j in range(start + 1, len(lines)):
            if shallower_or_same(lines[j]):
                end = j
                break
        span = "\n".join(lines[start:end]).rstrip("\n")
        thesis = None
        for ln in lines[start + 1:end]:
            s = ln.strip()
            if not s:
                continue
            if _ITALIC_LINE.match(s):
                thesis = s.strip("*").strip()
            break
        poems.append({"n": k + 1, "title": heading_title(lines[start]),
                      "start": start, "end": end, "text": span,
                      "thesis": thesis, "sha": _sha(span)})
    return poems


def frame_of(text: str, level: int = DEFAULT_POEM_LEVEL) -> str:
    """The section frame: the title and whatever stands before the first
    poem (the epigraph, any carried prose). Empty when the file opens
    straight into a poem."""
    lines = text.split("\n")
    mark = "#" * level + " "
    for i, ln in enumerate(lines):
        if ln.startswith(mark) and heading_title(ln):
            return "\n".join(lines[:i]).strip("\n")
    return ""


def find_poem(poems: list[dict], query: str) -> dict:
    """One poem by number, exact title, or unambiguous title fragment
    (case-insensitive). LookupError names the candidates otherwise."""
    q = query.strip()
    if q.isdigit():
        for p in poems:
            if p["n"] == int(q):
                return p
        raise LookupError(f"no poem {q}; the file has {len(poems)}")
    exact = [p for p in poems if p["title"].lower() == q.lower()]
    if len(exact) == 1:
        return exact[0]
    part = [p for p in poems if q.lower() in p["title"].lower()]
    if len(part) == 1:
        return part[0]
    if not part:
        raise LookupError(f"no poem titled like '{query}' — the file has: "
                          + "; ".join(f"{p['n']} {p['title']}" for p in poems))
    raise LookupError(f"'{query}' is ambiguous: "
                      + "; ".join(f"{p['n']} {p['title']}" for p in part))


def is_verse_unit(unit: str) -> bool:
    """True for a unit that is verse rather than prose: a stanza (its
    lines end in the backslash hard break, design §2) or a bare italic
    line (a thesis line or an epigraph). The prose-only passes — morph,
    the reader-load filters — skip these; the verse lenses own them."""
    s = unit.strip()
    return "\\\n" in unit or bool(_ITALIC_LINE.match(s))


def stanzas_of(poem_text: str) -> list[str]:
    """A poem's stanzas: the blank-line-separated blocks after the heading
    and thesis line. Lines carry the backslash hard break (design §2)."""
    blocks = [b for b in re.split(r"\n\s*\n", poem_text) if b.strip()]
    out: list[str] = []
    for b in blocks:
        s = b.strip()
        if s.startswith("#") or _ITALIC_LINE.match(s):
            continue
        out.append(s)
    return out
