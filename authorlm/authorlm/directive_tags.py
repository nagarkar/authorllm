"""The inline directive tags as text: `[Footnote: …]`, `[Explain: …]`,
`[Suggestion: …]` and `[Judgment: …]`, how to find them in a file's text and
how to take them out.

Invariant: reading only. The text functions take text and return text, and
`scan` and `open_report` read a manuscript's files to report the open tags;
nothing here writes a file or touches the database. It reads files through
`revisions`, so it sits in the manuscript area and only that area or a higher
one may import it. Landing a tag, its evidence and its Doc road are
`directives`.
"""

from __future__ import annotations

import re
from pathlib import Path

from .revisions import is_placeholder, iter_manuscript_paths

KINDS = ("footnote", "explain", "suggestion", "judgment")
TAGS = {
    # Keyword case-insensitive; the body never spans a line and never
    # nests a bracket; an empty body is not a tag at all.
    "footnote": re.compile(r"\[Footnote:\s*(?P<body>[^\[\]\n]+?)\s*\]", re.IGNORECASE),
    "explain": re.compile(r"\[Explain:\s*(?P<body>[^\[\]\n]+?)\s*\]", re.IGNORECASE),
    "suggestion": re.compile(r"\[Suggestion:\s*(?P<body>[^\[\]\n]+?)\s*\]",
                             re.IGNORECASE),
    # A lens finding the author ACCEPTED in the tab: a work order at its
    # anchor, resolved by `lens repair` (a rewrite, a question, or an
    # intent), never by `apply`. Deleting it in the tab rejects it.
    "judgment": re.compile(r"\[Judgment:\s*(?P<body>[^\[\]\n]+?)\s*\]", re.IGNORECASE),
}
_LABEL_OPT = re.compile(r"\s*\|\s*label:\s*(?P<label>[A-Za-z]+)\s*$", re.IGNORECASE)
_HEADING = re.compile(r"#{1,6}\s")
_PARAGRAPH = re.compile(r"[^\n]+(?:\n[^\n]+)*")
# A standalone Suggestion evaluates authorial prose (design §1), never a
# heading, footnote definition, directive/media tag, or close-tag unit
# sitting between the prose and the note.
_NON_PROSE_TARGET = re.compile(
    r"^(?:"
    r"#{1,6}\s|"
    r"\[\^[^\]]+\]:|"
    r"\[(?:/)?(?:Illustration|Footnote|Explain|Suggestion|Judgment|Voice|"
    r"Clip|Collage|Montage)\b"
    r")",
    re.IGNORECASE)


def check_kind(kind: str) -> str:
    if kind not in KINDS:
        raise ValueError(f"unknown directive kind '{kind}' (one of: {', '.join(KINDS)})")
    return kind


def is_standalone(text: str, start: int, end: int) -> bool:
    """True when the tag is the whole of its line (whitespace aside)."""
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    if line_end == -1:
        line_end = len(text)
    return not text[line_start:start].strip() and not text[end:line_end].strip()


def _is_prose_suggestion_target(unit: str) -> bool:
    """True when a blank-line unit is prose a Suggestion may replace."""
    first = unit.strip().split("\n", 1)[0].strip()
    return bool(first) and not _NON_PROSE_TARGET.match(first)


def _prior_prose_target(paragraphs: list[tuple[int, int, str]], start: int
                        ) -> tuple[int, int, str] | None:
    """The nearest prose blank-line unit entirely before `start`, skipping
    structural markup a margin note never evaluates — but never across a
    heading: a note under a heading belongs to that section, and reaching
    into the section above made two notes in different sections collide
    and could rewrite the wrong paragraph (#420). None then."""
    for a, b, p in reversed(paragraphs):
        if b > start:
            continue
        if _HEADING.match(p.lstrip()):
            return None
        if _is_prose_suggestion_target(p):
            return (a, b, p)
    return None


def scan_text(text: str, kind: str) -> list[dict]:
    """Every open tag of one kind in one file's text, in document order:
    the ordinal `n` (1-based within the file — the `--tag` handle), the
    line, the character span, the gist, the raw tag, whether it stands
    alone on its line, the paragraph that holds it, the TARGET paragraph
    a suggestion acts on (`target`, `target_start`, `target_end`: the
    holding paragraph inline, the paragraph BEFORE a standalone tag,
    None when nothing precedes it), and — footnotes — the `| label:`
    override when the author gave one.

    A standalone suggestion's "paragraph before" follows how the author
    writes the note under what it comments on (design §1). When the tag
    shares a blank-line unit with lines above it (the common
    `para\\n[Suggestion: …]` with no blank line), those preceding lines
    ARE the target — never an earlier unit. A tag that is its own unit
    targets the nearest prior *prose* unit: an intervening illustration,
    clip, heading, footnote definition, or other directive line is not
    the target — taking it as one let apply destroy that markup (or
    plant `<<tag>>{{prose}}` on the Doc road)."""
    pattern = TAGS[check_kind(kind)]
    paragraphs = [(m.start(), m.end(), m.group(0))
                  for m in _PARAGRAPH.finditer(text)]
    tags = []
    for n, m in enumerate(pattern.finditer(text), 1):
        start, end = m.span()
        body = m.group("body")
        label = None
        if kind == "footnote":
            opt = _LABEL_OPT.search(body)
            if opt:
                label = opt.group("label").upper()
                body = body[:opt.start()].strip()
        here = next(((a, b, p) for a, b, p in paragraphs if a <= start < b),
                    (start, end, ""))
        standalone = is_standalone(text, start, end)
        if standalone:
            holding_start = here[0]
            line_start = text.rfind("\n", 0, start) + 1
            if holding_start < line_start:
                # Note on the next line of the same unit: the lines above
                # the tag, not the previous blank-line paragraph.
                chunk = text[holding_start:line_start].rstrip("\n")
                target = ((holding_start, holding_start + len(chunk), chunk)
                          if chunk.strip() else None)
            elif kind == "suggestion":
                target = _prior_prose_target(paragraphs, start)
            else:
                target = next(((a, b, p) for a, b, p in reversed(paragraphs)
                               if b <= start), None)
        else:
            target = here
        tags.append({
            "kind": kind, "n": n,
            "line": text.count("\n", 0, start) + 1,
            "start": start, "end": end,
            "gist": body, "raw": m.group(0), "label": label,
            "standalone": standalone,
            "paragraph": here[2],
            "target": target[2] if target else None,
            "target_start": target[0] if target else None,
            "target_end": target[1] if target else None,
        })
    return tags


def strip_tags(text: str) -> tuple[str, list[dict]]:
    """Exports: an unresolved tag never reaches a reader. A standalone
    tag takes its line with it; an inline tag leaves the sentence's
    spacing as it was. Returns (text, [{kind, gist}] removed)."""
    removed: list[dict] = []
    out = text
    tags = sorted((t for kind in KINDS for t in scan_text(text, kind)),
                  key=lambda t: t["start"], reverse=True)
    for tag in tags:
        removed.append({"kind": tag["kind"], "gist": tag["gist"]})
        out = remove_tag(out, tag)
    removed.reverse()
    return out, removed


def remove_tag(text: str, tag: dict) -> str:
    """The text with ONE tag gone: a standalone tag takes its line with
    it (and the blank line that separated it); an inline tag leaves the
    sentence's spacing as it was. Offsets are the tag's in `text`."""
    start, end = tag["start"], tag["end"]
    if tag["standalone"]:
        line_start = text.rfind("\n", 0, start) + 1
        line_end = text.find("\n", end)
        line_end = len(text) if line_end == -1 else line_end + 1
        out = text[:line_start] + text[line_end:]
        if line_start > 0 and out[line_start - 1:line_start + 1] == "\n\n":
            out = out[:line_start] + out[line_start + 1:]
        elif line_start >= len(out) and out.endswith("\n\n"):
            out = out[:-1]      # the file's last line went: one newline stays
        return out
    before = text[start - 1] if start > 0 else ""
    after = text[end] if end < len(text) else ""
    if before == " " and (after == " " or after in ".,;:!?)"):
        start -= 1
    return text[:start] + text[end:]


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="replace")


def scan(root: Path, kind: str, files: list[str] | None = None
         ) -> dict[str, list[dict]]:
    """Open tags of one kind by file (or the named files only). Files
    with no tag are absent. The TOC and a file holding only the
    mid-rewrite placeholder are never scanned."""
    from .structure import TOC_FILENAME

    check_kind(kind)
    root = Path(root)
    wanted = set(files or [])
    found: dict[str, list[dict]] = {}
    for rel, path in iter_manuscript_paths(root).items():
        if path.name == TOC_FILENAME or (wanted and rel not in wanted):
            continue
        text = _read(path)
        if is_placeholder(text):
            continue
        tags = scan_text(text, kind)
        if tags:
            found[rel] = tags
    return found


def open_report(root: Path, kinds: tuple[str, ...] = KINDS,
                files: list[str] | None = None) -> list[dict]:
    """What a collect and `get_status` surface: every open tag, one row
    each — kind, file, ordinal, line, gist, label."""
    rows = []
    for kind in kinds:
        for rel, tags in scan(root, kind, files).items():
            rows.extend({"kind": kind, "file": rel, "n": t["n"],
                         "line": t["line"], "gist": t["gist"],
                         "label": t["label"]} for t in tags)
    return rows
