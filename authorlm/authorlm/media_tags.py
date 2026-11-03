"""The podcast media tags as text: `[Clip: …]`, `[Collage: …]` and
`[Montage: …]` lines, their fields, the structure a file's tags make, and a
book export's copy of a file with them gone.

Every tag is a full line. Outside a container a `[Clip]` is a span, playing
under the words up to its `[/Clip]`; inside a Collage or a Montage a clip has
no close.

Invariant: text in, text out. Nothing here reads a file or the database or
imports from this package, so any layer may use it. Keys, validation against
the manuscript and the listing are `media`.
"""

from __future__ import annotations

import re

CLIP_OPEN = re.compile(r"^\[Clip:(?P<body>[^\]]*)\]\s*$", re.IGNORECASE)
CLIP_CLOSE = re.compile(r"^\[/Clip\]\s*$", re.IGNORECASE)
# The two containers: a Collage holds clips only and
# narration stops while they play (a cutaway); a Montage holds clips AND
# prose, and its clips play in order under those words.
CONTAINER_OPEN = re.compile(r"^\[(?P<kind>Collage|Montage):(?P<body>[^\]]*)\]\s*$",
                            re.IGNORECASE)
CONTAINER_CLOSE = re.compile(r"^\[/(?P<kind>Collage|Montage)\]\s*$", re.IGNORECASE)
CONTAINERS = ("collage", "montage")
HEADING = re.compile(r"^#{1,6}\s")
FENCE = re.compile(r"^(```|~~~)")
FIELD = re.compile(r"\s*\|\s*(?P<key>[A-Za-z][\w-]*)\s*:\s*")
_REF = re.compile(r"\s*⇢\s*(?P<ref>[\w.-]+\.md)\s*$")
FIELDS = ("start", "end", "cast", "prior", "look", "text", "use", "youtube",
          "audio", "voice")


def is_tag_line(line: str) -> bool:
    """Any media tag line: a clip, a container, or a close."""
    return bool(CLIP_OPEN.match(line) or CLIP_CLOSE.match(line)
                or CONTAINER_OPEN.match(line) or CONTAINER_CLOSE.match(line))


def parse_fields(body: str) -> dict:
    """A tag body → {description, ref, fields, errors}. The description
    is the text before the first `| field:`; field names are
    case-insensitive; an unknown or repeated field is an error, never
    silently dropped."""
    parts = FIELD.split(body)
    description = parts[0].strip()
    ref = None
    m = _REF.search(description)
    if m:
        ref = m.group("ref")
        description = description[:m.start()].rstrip(" …").strip()
    fields: dict = {}
    errors: list[str] = []
    for i in range(1, len(parts) - 1, 2):
        key, value = parts[i].lower(), parts[i + 1].strip()
        if key not in FIELDS:
            errors.append(f"unknown field '{key}:' (one of: {', '.join(FIELDS)})")
            continue
        if key in fields:
            errors.append(f"field '{key}:' given twice")
            continue
        if key == "cast":
            value = [c.strip() for c in value.split(",") if c.strip()]
        elif key == "text" and len(value) >= 2 and value[0] == value[-1] == '"':
            value = value[1:-1]
        fields[key] = value
    return {"description": description, "ref": ref, "fields": fields,
            "errors": errors}


def scan_text(text: str) -> tuple[list[dict], list[str]]:
    """Clips, collages and montages in one file's text, in order, and the
    structural problems the walk found. Positions are 0-based line
    indexes (`i`, `close`); `line` is 1-based for people. Whether a
    container holds prose is checked later, against the spoken text."""
    lines = text.split("\n")
    items: list[dict] = []
    problems: list[str] = []
    span: dict | None = None
    box: dict | None = None          # the open Collage or Montage
    fenced = False

    def _end_span(at: int, why: str) -> None:
        nonlocal span
        span["errors"].append(why)
        span["close"] = at
        span = None

    for i, line in enumerate(lines):
        if FENCE.match(line):
            fenced = not fenced
            continue
        if fenced:
            continue
        if HEADING.match(line):
            if span:
                _end_span(i, f"span crosses the heading at line {i + 1} "
                             "(close it with [/Clip] before the heading)")
            if box:
                box["errors"].append(
                    f"{box['kind']} crosses the heading at line {i + 1}")
                box["close"] = i
                box = None
            continue
        m = CLIP_OPEN.match(line)
        if m:
            item = {"kind": "clip", "i": i, "line": i + 1, "close": None,
                    "container": box["n"] if box else None,
                    "shape": ("point" if box["kind"] == "collage"
                              else "montage") if box else "span",
                    **parse_fields(m.group("body"))}
            if box:
                box["children"].append(len(items))
            elif span:
                item["errors"].append(
                    f"span inside the span opened at line {span['line']}")
                _end_span(i, f"a second [Clip] opens at line {i + 1} "
                             "before this span's [/Clip]")
            if item["shape"] == "span":
                span = item
            items.append(item)
            continue
        if CLIP_CLOSE.match(line):
            if span:
                span["close"] = i
                span = None
            elif box:
                problems.append(f"line {i + 1}: [/Clip] inside a "
                                f"{box['kind'].title()} — a clip in a "
                                f"{box['kind']} has no close")
            else:
                problems.append(f"line {i + 1}: [/Clip] with no open span")
            continue
        m = CONTAINER_OPEN.match(line)
        if m:
            kind = m.group("kind").lower()
            title, ref = m.group("body").strip(), None
            rm = _REF.search(title)
            if rm:
                ref = rm.group("ref")
                title = title[:rm.start()].rstrip(" …").strip()
            item = {"kind": kind, "i": i, "line": i + 1, "close": None,
                    "title": title, "ref": ref, "children": [],
                    "n": len(items), "errors": []}
            if box:
                item["errors"].append(
                    f"{kind} inside the {box['kind']} opened at line "
                    f"{box['line']}")
            if span:
                _end_span(i, f"a {kind.title()} opens at line {i + 1} "
                             "inside this span")
            box = item
            items.append(item)
            continue
        m = CONTAINER_CLOSE.match(line)
        if m:
            kind = m.group("kind").lower()
            if box and box["kind"] == kind:
                box["close"] = i
                box = None
            elif box:
                box["errors"].append(f"closed by [/{kind.title()}] at line "
                                     f"{i + 1} — it needs [/{box['kind'].title()}]")
                box["close"] = i
                box = None
            else:
                problems.append(f"line {i + 1}: [/{kind.title()}] with no "
                                f"open {kind}")
    if span:
        span["errors"].append("span never closed with [/Clip]")
    if box:
        box["errors"].append(f"{box['kind']} never closed with "
                             f"[/{box['kind'].title()}]")
    return items, problems


def strip_for_book(text: str, where: str = "") -> tuple[str, int]:
    """Every book export (pdf, docx, epub, md, doc, audio): the Clip,
    Collage and Montage tag lines and clip embed lines go; every word of
    prose stays, wherever it sits (a tag never deletes the author's
    prose). Returns (text, number of opening tags removed).
    `where` is kept for the callers' symmetry with resolve_regions."""
    out: list[str] = []
    removed = 0
    fenced = False
    for line in text.split("\n"):
        if FENCE.match(line):
            fenced = not fenced
            out.append(line)
            continue
        if fenced:
            out.append(line)
            continue
        if is_tag_line(line):
            removed += bool(CLIP_OPEN.match(line) or CONTAINER_OPEN.match(line))
            continue
        if line.lstrip().startswith("![") and "_clips/" in line:
            continue
        out.append(line)
    return "\n".join(out), removed
