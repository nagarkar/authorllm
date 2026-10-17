"""Media for podcasts: `[Clip: …]`, `[Collage: …]` and `[Montage: …]`
tags, and the one umbrella listing, `media list`
(design: docs/video-clips-design.md).

    [Clip: he slumps at his desk as the office empties | cast: ecce-man]
    The prose the clip plays under, read aloud.
    [/Clip]

    [Collage: The Architect and Neo]
    [Clip: Neo answers, "The problem is choice." | youtube: <url> | start: 2:10 | end: 2:31]
    [/Collage]

    [Montage: Choice as prison and escape]
    [Clip: the Architect's room of screens | youtube: <url> | start: … | end: …]
    [Clip: Neo turns toward the door | youtube: <url> | start: … | end: …]
    The prose the montage plays under, read aloud.
    [/Montage]

Every tag is a full line, plain text on every road, like
`[Illustration: …]` and the `[Omit:]` regions. The container decides
the shape: outside a container a `[Clip]` is a SPAN (it plays under the
words up to its `[/Clip]`). A COLLAGE holds clips only: narration stops,
its clips play, narration resumes (a cutaway). A MONTAGE holds clips and
prose: its clips play in order under those words. Inside either, a clip
has no close. Book exports drop every tag line and keep every word of
prose. A tag never carries timeline timing; podlm owns the timeline and
aligns the words this module hands it to its own transcript.

Collect gives every tag its KEY (§8a): its text moves to a file in the
folder of its kind — a YOUTUBE BRIEF (`_clips/youtube-briefs/`: what to
search for), a GENERATION SPEC (`_clips/specs/`: what a video model
renders) or a NOTE (`_clips/notes/`: a title or label) — and the tag
keeps an excerpt and `⇢ <key>.md`. Each kind has its own check. `set_spec` lands a drafted spec and
`set_fields` writes confirmed fields (a YouTube url and times) into a
tag. Nothing here calls a model or spends. A clip not yet collected has
a PROVISIONAL key in `media list`.
"""

from __future__ import annotations

import re
from pathlib import Path

from .revisions import read_manuscript_files

DRAFT_WORDS = 150          # §8e: a generation spec under this is a draft
WORDS_PER_MINUTE = 150     # estimated_seconds is a generation hint only
MAX_CAST = 3

CLIP_OPEN = re.compile(r"^\[Clip:(?P<body>[^\]]*)\]\s*$", re.IGNORECASE)
CLIP_CLOSE = re.compile(r"^\[/Clip\]\s*$", re.IGNORECASE)
# The two containers (ruled 2026-10-17): a Collage holds clips only and
# narration stops while they play (a cutaway); a Montage holds clips AND
# prose, and its clips play in order under those words.
CONTAINER_OPEN = re.compile(r"^\[(?P<kind>Collage|Montage):(?P<body>[^\]]*)\]\s*$",
                            re.IGNORECASE)
CONTAINER_CLOSE = re.compile(r"^\[/(?P<kind>Collage|Montage)\]\s*$", re.IGNORECASE)
CONTAINERS = ("collage", "montage")
_HEADING = re.compile(r"^#{1,6}\s")
_FENCE = re.compile(r"^(```|~~~)")
_FIELD = re.compile(r"\s*\|\s*(?P<key>[A-Za-z][\w-]*)\s*:\s*")
_TIMESTAMP = re.compile(r"^(?:(?P<h>\d+):)?(?P<m>\d{1,2}):(?P<s>\d{2})$")
_REF = re.compile(r"\s*⇢\s*(?P<ref>[\w.-]+\.md)\s*$")
_SENTENCE_END = re.compile(r"(?<=[.!?…])[\"”’)\]]*\s+")
_FOOTNOTE_REF = re.compile(r"\[\^[^\]]+\]")
_SENTINEL = "⟦media{}⟧"            # ⟦media3⟧ — survives regions
_SENTINEL_RE = re.compile(r"⟦media(\d+)⟧")

FIELDS = ("start", "end", "cast", "prior", "look", "text", "use", "youtube")
VIDEO_EXT = (".mp4", ".mov", ".m4v", ".webm", ".mkv")
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp", ".gif")


# ------------------------------------------------------------------ grammar

def is_tag_line(line: str) -> bool:
    """Any media tag line: a clip, a container, or a close."""
    return bool(CLIP_OPEN.match(line) or CLIP_CLOSE.match(line)
                or CONTAINER_OPEN.match(line) or CONTAINER_CLOSE.match(line))


def parse_fields(body: str) -> dict:
    """A tag body → {description, ref, fields, errors}. The description
    is the text before the first `| field:`; field names are
    case-insensitive; an unknown or repeated field is an error, never
    silently dropped."""
    parts = _FIELD.split(body)
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


def timestamp_seconds(value: str) -> int | None:
    m = _TIMESTAMP.match(value.strip())
    if not m:
        return None
    return (int(m.group("h") or 0) * 3600 + int(m.group("m")) * 60
            + int(m.group("s")))


def _is_path(value: str) -> bool:
    return "/" in value or bool(re.search(r"\.[A-Za-z0-9]{2,4}$", value))


def _slug(text: str, words: int = 4) -> str:
    parts = re.sub(r"[^a-z0-9 ]", "", text.lower()).split()
    return "-".join(parts[:words])


def _youtube_id(url: str) -> str | None:
    m = re.search(r"(?:youtu\.be/|[?&]v=|/shorts/|/embed/)([\w-]{6,})", url)
    return m.group(1) if m else None


# ------------------------------------------------------------------ structure

def scan_text(text: str) -> tuple[list[dict], list[str]]:
    """Clips, collages and montages in one file's text, in order, and the
    structural problems the walk found (§8i). Positions are 0-based line
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
        if _FENCE.match(line):
            fenced = not fenced
            continue
        if fenced:
            continue
        if _HEADING.match(line):
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


# ------------------------------------------------------------------ spoken text

def _spoken(text: str) -> str:
    """The words read aloud from a stretch of (region-resolved) text:
    directive tags, illustration and image lines, voice lines, footnote
    markers and display math removed; whitespace collapsed."""
    from . import directives
    from .export import strip_display_math, strip_voice_lines
    from .gdocs import normalize_markdown
    from .illus import parse_tag

    text, _ = directives.strip_tags(normalize_markdown(text))
    text = strip_voice_lines(text)
    try:
        text = strip_display_math(text)
    except ValueError:
        pass
    kept = [ln for ln in text.split("\n")
            if not parse_tag(ln) and not ln.lstrip().startswith("![")
            and not _SENTINEL_RE.fullmatch(ln.strip())
            and not _HEADING.match(ln)]
    text = _FOOTNOTE_REF.sub("", "\n".join(kept))
    # Markdown that is never spoken: blockquote markers and emphasis
    # asterisks would only stop podlm's words matching its transcript.
    text = re.sub(r"(?m)^\s*>\s?", "", text).replace("*", "")
    return " ".join(text.split())


def sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_END.split(text) if s.strip()]


def _spoken_segments(text: str, marks: list[int], where: str
                     ) -> tuple[dict[int, int], list[str] | None, str | None]:
    """Resolve the file for the `audio` build with every media tag line
    replaced by a sentinel. Returns ({line index: position in the
    resolved line list} for sentinels that survived, resolved lines,
    error). A sentinel that vanished sat in a region not spoken aloud."""
    from .export import resolve_regions

    lines = text.split("\n")
    for n, i in enumerate(marks):
        lines[i] = _SENTINEL.format(n)
    try:
        resolved = resolve_regions("\n".join(lines), {"audio"}, where).split("\n")
    except ValueError as err:
        return {}, None, str(err)
    survived = {}
    for pos, ln in enumerate(resolved):
        m = _SENTINEL_RE.fullmatch(ln.strip())
        if m:
            survived[marks[int(m.group(1))]] = pos
    return survived, resolved, None


# ------------------------------------------------------------------ validation

def _check(item: dict, root: Path, known: dict) -> None:
    """The field rules of §5 and the reference rules of §8i, in place:
    `kind_of` (generated | quoted | used), `errors`, `warnings`."""
    f, errors, warnings = item["fields"], item["errors"], item.setdefault("warnings", [])
    desc = item["description"]
    if "use" in f and "youtube" in f:
        errors.append("use: and youtube: together — a clip has one source")
    if "youtube" in f:
        item["kind_of"] = "quoted"
    elif "use" in f:
        item["kind_of"] = "used"
    else:
        item["kind_of"] = "generated"
        if not desc:
            errors.append("a clip without a description must point at "
                          "something: use: or youtube:")
    pointer = item["kind_of"] != "generated"

    if pointer:
        for name in ("cast", "prior", "look"):
            if name in f:
                errors.append(f"{name}: applies only to a generated clip "
                              "(one with a description and no use:/youtube:)")
        if "youtube" in f:
            url = f["youtube"]
            if not re.match(r"https?://", url) or not _youtube_id(url):
                errors.append(f"youtube: '{url}' is not a YouTube video url")
        video = True
        if "use" in f:
            path = root / f["use"]
            if not path.exists():
                errors.append(f"use: {f['use']} does not exist")
            video = f["use"].lower().endswith(VIDEO_EXT)
            if not video and not f["use"].lower().endswith(IMAGE_EXT):
                warnings.append(f"use: {f['use']} is neither a known video "
                                "nor image type")
        secs = {}
        for name in ("start", "end"):
            if name not in f:
                continue
            if not video:
                errors.append(f"{name}: on a still image — a still has no "
                              "in or out point")
                continue
            s = timestamp_seconds(f[name])
            if s is None:
                errors.append(f"{name}: '{f[name]}' is not a timestamp "
                              "(m:ss or h:mm:ss) — on quoted or used video "
                              "start/end are trim points")
            else:
                secs[name] = s
        if "start" in secs and "end" in secs and secs["end"] <= secs["start"]:
            errors.append("end: is not after start:")
        return

    # Generated: start/end are frames, end needs start.
    if "end" in f and "start" not in f:
        errors.append("end: needs start: on a generated clip (a last frame "
                      "needs a first)")
    for name in ("start", "end"):
        if name not in f:
            continue
        value = f[name]
        if timestamp_seconds(value) is not None:
            errors.append(f"{name}: '{value}' is a timestamp — on a generated "
                          "clip start/end are images (first/last frame)")
        elif _is_path(value):
            if not (root / value).exists():
                errors.append(f"{name}: {value} does not exist")
            elif not value.lower().endswith(IMAGE_EXT):
                errors.append(f"{name}: {value} is not an image")
        elif ":" in value:
            warnings.append(f"{name}: {value} is in a library — not checked "
                            "until looks and the Studio library are built")
        elif value not in known["illustrations"] and value not in known["cast"]:
            errors.append(f"{name}: '{value}' is neither an illustration key "
                          "nor a cast id in this manuscript")
    cast = f.get("cast") or []
    if len(cast) > MAX_CAST:
        errors.append(f"cast: names {len(cast)} ids; at most {MAX_CAST}")
    for cid in cast:
        if ":" in cid:
            warnings.append(f"cast: {cid} is in a library — not checked until "
                            "the Studio library is built")
        elif cid not in known["cast"]:
            errors.append(f"cast: '{cid}' is not defined — no tag carries "
                          f"'cast-id: {cid}'")
        elif not known["cast"][cid]:
            errors.append(f"cast: plate '{cid}' has no pick")
    if "look" in f:
        warnings.append(f"look: {f['look']} not checked — looks are not "
                        "built yet")


def _status(item: dict) -> str:
    if item["errors"]:
        return "invalid"
    if item.get("draft_reasons"):
        return "draft"
    return "ready"


# ------------------------------------------------------------------ listing

def _file_entries(name: str, raw: str, root: Path, known: dict) -> list[dict]:
    from .illus import embed_target, scan_text as illus_scan

    # Parse the file as it is on disk: line numbers are the author's, and
    # normalize_markdown drops the derived embed lines that carry an
    # illustration's chosen image. Spoken excerpts are normalized in _spoken.
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    items, problems = scan_text(text)
    lines = text.split("\n")
    illus_lines = {}
    if known["illustrations_on"].get(name, True):
        prompts = known["prompts"]
        for slot in illus_scan(text, prompts):
            illus_lines[slot["line"] - 1] = slot

    marks = sorted({it["i"] for it in items}
                   | {it["close"] for it in items if it["close"] is not None}
                   | set(illus_lines))
    survived, resolved, region_error = _spoken_segments(text, marks, name)

    def between(a: int | None, b: int | None) -> str:
        if resolved is None or a is None:
            return ""
        lo = survived.get(a)
        hi = survived.get(b) if b is not None else len(resolved)
        if lo is None or hi is None:
            return ""
        return _spoken("\n".join(resolved[lo + 1:hi]))

    marks_at = sorted(survived.values())

    def outside(i: int | None, side: str) -> str:
        """The spoken text just before line i (back to the previous media
        tag) or just after it (up to the next) — where a cutaway's
        narration stops and resumes."""
        if resolved is None or i is None or i not in survived:
            return ""
        pos = survived[i]
        if side == "before":
            lo = max([m for m in marks_at if m < pos], default=-1)
            return _spoken("\n".join(resolved[lo + 1:pos]))
        hi = min([m for m in marks_at if m > pos], default=len(resolved))
        return _spoken("\n".join(resolved[pos + 1:hi]))

    for item in items:
        if item["kind"] in CONTAINERS:
            continue
        item["spec_kind"] = spec_kind("clip", item["fields"])
        item["spec_text"] = item["description"]
        if item["ref"]:
            entry = known["specs"].get(item["ref"])
            if entry is None:
                item["errors"].append(
                    f"{spec_relpath(item['spec_kind'], item['ref'])} is missing")
            else:
                item["spec_text"] = entry["text"]
                item["description"] = " ".join(entry["text"].split())
                if entry["kind"] != item["spec_kind"]:
                    item["warnings"] = [f"its text is a {entry['kind']} — the "
                                        "next collect moves it; draft it anew "
                                        f"as a {item['spec_kind']}"]
        item["draft_reasons"] = check_text(item["spec_kind"], item["spec_text"])
        item["spec_words"] = len(item["description"].split())
        _check(item, root, known)
        if region_error:
            item["errors"].append(f"regions do not resolve: {region_error}")
        elif item["i"] not in survived:
            item["errors"].append("inside a region not spoken in audio "
                                  "(e.g. [Omit: audio]) — it would sit under "
                                  "words never read aloud")
    # Spoken words and neighbours.
    for idx, item in enumerate(items):
        if item["kind"] == "clip" and item["shape"] == "span":
            words = between(item["i"], item["close"])
            item["words"] = words
            ss = sentences(words)
            item["first_sentence"] = ss[0] if ss else ""
            item["last_sentence"] = ss[-1] if ss else ""
            item["estimated_seconds"] = round(
                len(words.split()) * 60 / WORDS_PER_MINUTE)
        elif item["kind"] == "clip" and item["shape"] == "point":
            # A Collage is a cutaway: narration stops before it and
            # resumes after it, so every clip in it shares those edges.
            box = items[item["container"]]
            before = sentences(outside(box["i"], "before"))
            after = sentences(outside(box["close"], "after"))
            item["before_sentence"] = before[-1] if before else ""
            item["after_sentence"] = after[0] if after else ""
        elif item["kind"] == "clip":
            # A Montage clip plays under the prose around it: the sentence
            # before it and the one after place it within the montage.
            box = items[item["container"]]
            kids = box["children"]
            pos = kids.index(idx)
            prev_line = items[kids[pos - 1]]["i"] if pos else box["i"]
            next_line = (items[kids[pos + 1]]["i"] if pos + 1 < len(kids)
                         else box["close"])
            before = sentences(between(prev_line, item["i"]))
            after = sentences(between(item["i"], next_line))
            item["before_sentence"] = before[-1] if before else ""
            item["after_sentence"] = after[0] if after else ""

    # Provisional keys, unique within the manuscript.
    out: list[dict] = []
    for order, item in enumerate(items):
        if item["kind"] in CONTAINERS:
            kind = item["kind"]
            words = between(item["i"], item["close"])
            errors = list(item["errors"])
            if not item["children"]:
                errors.append(f"{kind} holds no clips")
            if kind == "collage" and words:
                errors.append("a Collage holds clips only — narration stops "
                              "while they play; prose inside belongs in a "
                              "[Montage]")
            if kind == "montage" and not words:
                errors.append("a Montage plays under prose and holds none — "
                              "clips alone are a [Collage]")
            if item["ref"]:
                entry = known["specs"].get(item["ref"])
                if entry is None:
                    errors.append(f"{spec_relpath('note', item['ref'])} "
                                  "is missing")
                else:
                    item["title"] = " ".join(entry["text"].split())
            entry = {"kind": kind, "file": name, "line": item["line"],
                     "title": item["title"],
                     "keyed": bool(item["ref"]),
                     "spec_kind": "note",
                     "spec_path": (spec_relpath("note", item["ref"])
                                   if item["ref"] else None),
                     "key_base": (item["ref"][:-3] if item["ref"] else
                                  f"{kind}-" + (_slug(item["title"]) or "untitled")),
                     "children_idx": item["children"],
                     "status": "invalid" if errors else "ready",
                     "errors": errors, "warnings": []}
            if kind == "montage":
                ss = sentences(words)
                entry.update(words=words,
                             first_sentence=ss[0] if ss else "",
                             last_sentence=ss[-1] if ss else "",
                             estimated_seconds=round(
                                 len(words.split()) * 60 / WORDS_PER_MINUTE))
            else:
                before = sentences(outside(item["i"], "before"))
                after = sentences(outside(item["close"], "after"))
                entry.update(before_sentence=before[-1] if before else "",
                             after_sentence=after[0] if after else "")
        else:
            f = item["fields"]
            if item["ref"]:
                base = item["ref"][:-3]
            elif "youtube" in f and _youtube_id(f["youtube"]):
                base = "yt-" + _youtube_id(f["youtube"]) + (
                    "-" + f["start"].replace(":", "") if "start" in f else "")
            elif "use" in f:
                base = _slug(Path(f["use"]).stem.replace("-", " ").replace("_", " "))
            else:   # generated, or a quoted clip whose url is not set yet
                base = _slug(item["description"])
            entry = {"kind": "clip", "file": name, "line": item["line"],
                     "shape": item["shape"], "kind_of": item["kind_of"],
                     "keyed": bool(item["ref"]),
                     "spec_kind": item["spec_kind"],
                     "spec_path": (spec_relpath(item["spec_kind"], item["ref"])
                                   if item["ref"] else None),
                     "key_base": base or "clip",
                     "description": item["description"],
                     "draft_reasons": item["draft_reasons"],
                     "fields": f, "spec_words": item["spec_words"],
                     "status": _status(item),
                     "errors": item["errors"], "warnings": item["warnings"],
                     "takes": [], "chosen": None}
            for k in ("words", "first_sentence", "last_sentence",
                      "estimated_seconds", "before_sentence",
                      "after_sentence"):
                if k in item:
                    entry[k] = item[k]
            if item["spec_kind"] == "youtube_brief":
                entry["youtube_brief"] = parse_sections(item["spec_text"],
                                                        BRIEF_LABELS)
                entry.update(brief_terms(item["spec_text"]))
            elif item["spec_kind"] == "generation_spec":
                entry["generation_spec"] = parse_sections(item["spec_text"],
                                                          SPEC_LABELS)
        entry["_line_index"] = item["i"]
        out.append(entry)
    for i, slot in illus_lines.items():
        target = embed_target(text, slot["key"]) if slot.get("key") else None
        para = "\n".join(lines[:i]).strip().rsplit("\n\n", 1)[-1]
        before = sentences(_spoken(para))
        out.append({"kind": "illustration", "file": name, "line": i + 1,
                    "key_base": slot.get("key") or _slug(slot["prompt"]),
                    "keyed": bool(slot.get("key")),
                    "description": slot["prompt"],
                    "caption": slot.get("caption"),
                    "cast": slot.get("cast") or [],
                    "chosen": (f"_illustrations/{target}" if target else None),
                    "before_sentence": before[-1] if before else "",
                    "status": "ready" if target else "unrendered",
                    "errors": [], "warnings": [], "_line_index": i})
    out.sort(key=lambda e: e.get("_line_index", -1))
    return out + [{"kind": "problem", "file": name, "problem": p}
                  for p in problems]


def list_media(root: Path, files: list[str] | None = None,
               key: str | None = None) -> dict:
    """`media list`: every clip, collage, montage and illustration in reading
    order, each with everything this slice knows (§8f), plus the
    file-level problems. Paths only, never bytes."""
    from .api import is_placeholder
    from .illus import cast_index, load_prompts
    from .structure import TOC_FILENAME, reading_order, toc_attrs

    from .revisions import iter_manuscript_paths

    root = Path(root)
    disk = read_manuscript_files(root)
    paths = iter_manuscript_paths(root)
    order, unlisted = reading_order(disk)
    names = [n for n in list(order) + sorted(unlisted)
             if n.endswith(".md") and n != TOC_FILENAME]
    attrs = toc_attrs(disk.get(TOC_FILENAME) or "")
    index, _cast_problems = cast_index(root)
    from .illus import scan_text as illus_scan
    prompts = load_prompts(root)
    illus_keys = set()
    for n in names:
        for slot in illus_scan(disk.get(n) or "", prompts):
            if slot.get("key"):
                illus_keys.add(slot["key"])
    known = {"specs": load_specs(root),
             "cast": {cid: c.get("plate") for cid, c in index.items()},
             "illustrations": illus_keys, "prompts": prompts,
             "illustrations_on": {n: attrs.get(n, {}).get("illustrations")
                                  != "none" for n in names}}

    entries: list[dict] = []
    problems: list[str] = []
    for name in names:
        if files and name not in files:
            continue
        raw = disk.get(name) or ""
        if is_placeholder(raw):
            continue
        # The bytes on disk, not the observation seam: line numbers are
        # the author's, and the seam drops the embed lines that name an
        # illustration's chosen image.
        if name in paths:
            raw = paths[name].read_text(encoding="utf-8", errors="replace")
        for e in _file_entries(name, raw, root, known):
            if e["kind"] == "problem":
                problems.append(f"{e['file']}: {e['problem']}")
            else:
                entries.append(e)

    # Unique keys across the manuscript, then container children by key.
    seen: dict[str, int] = {}
    for e in entries:
        base = e.pop("key_base")
        seen[base] = seen.get(base, 0) + 1
        e["key"] = base if seen[base] == 1 else f"{base}-{seen[base]}"
    by_file: dict[str, list[dict]] = {}
    for e in entries:
        by_file.setdefault(e["file"], []).append(e)
    for file_entries in by_file.values():
        media_items = [e for e in file_entries if e["kind"] != "illustration"]
        for e in file_entries:
            if e["kind"] in CONTAINERS:
                e["children"] = [media_items[i]["key"]
                                 for i in e.pop("children_idx")
                                 if i < len(media_items)]
    clip_keys = {e["key"]: (e["file"], e["_line_index"])
                 for e in entries if e["kind"] == "clip"}
    for e in entries:
        prior = e.get("fields", {}).get("prior") if e["kind"] == "clip" else None
        if prior:
            where = clip_keys.get(prior)
            if where is None:
                e["errors"].append(f"prior: '{prior}' is not a clip key in "
                                   "this manuscript")
                e["status"] = "invalid"
            elif where[0] == e["file"] and where[1] > e["_line_index"]:
                e["warnings"].append(f"prior: '{prior}' comes later in the file")
    for n, e in enumerate(entries):
        e["order"] = n
        e.pop("_line_index", None)
    if key:
        entries = [e for e in entries if e["key"] == key]
    return {"media": entries, "problems": problems,
            "keys_provisional": any(e["kind"] in ("clip",) + CONTAINERS
                                    and not e["keyed"] for e in entries)}


def not_ready(root: Path, files: list[str] | None = None) -> list[dict]:
    """What collect and `get_status` surface (§8e): every clip, collage
    or montage that is not ready, and every structural problem — one row
    each. Read-only; never drafts."""
    listing = list_media(root, files)
    rows = [{"file": e["file"], "line": e["line"], "kind": e["kind"],
             "key": e["key"], "status": e["status"],
             "problems": e["errors"] + e.get("draft_reasons", [])}
            for e in listing["media"]
            if e["kind"] in ("clip",) + CONTAINERS and e["status"] != "ready"]
    rows.extend({"file": p.split(":", 1)[0], "line": None, "kind": "problem",
                 "key": None, "status": "invalid", "problems": [p]}
                for p in listing["problems"])
    return rows


# ------------------------------------------------------------------ keys and specs

CLIPS_DIR = "_clips"
EXCERPT_WORDS = 8

# Three kinds of canonical text, each in its own folder and checked by its
# own function (ruled 2026-10-17): a YOUTUBE BRIEF says what to look for,
# so the search and the quote location succeed; a GENERATION SPEC sets the
# stage for a video model; a NOTE is a container's title or an as-is
# asset's label.
SPEC_DIRS = {"youtube_brief": "youtube-briefs", "generation_spec": "specs",
             "note": "notes"}
BRIEF_LABELS = ("source", "moment", "quote", "on screen")
SPEC_LABELS = ("subject", "setting", "look", "camera", "light", "motion",
               "sound", "continuity", "on-screen text")


def spec_kind(kind: str, fields: dict) -> str:
    """Which canonical text a tag owns: a youtube: clip a YouTube brief, a
    clip that will be generated a generation spec, anything else a note."""
    if kind != "clip":
        return "note"
    if fields.get("youtube"):
        return "youtube_brief"
    if fields.get("use"):
        return "note"
    return "generation_spec"


def specs_dir(root: Path, kind: str) -> Path:
    return Path(root) / CLIPS_DIR / SPEC_DIRS[kind]


def spec_relpath(kind: str, ref: str) -> str:
    return f"{CLIPS_DIR}/{SPEC_DIRS[kind]}/{ref}"


def load_specs(root: Path) -> dict[str, dict]:
    """{<key>.md: {kind, text}} across the three folders. Keys are unique
    across all of them."""
    out: dict[str, dict] = {}
    for kind in SPEC_DIRS:
        directory = specs_dir(root, kind)
        if directory.is_dir():
            for p in sorted(directory.glob("*.md")):
                out[p.name] = {"kind": kind,
                               "text": p.read_text(encoding="utf-8").strip()}
    return out


def _labels_for(kind: str) -> tuple[str, ...]:
    return {"youtube_brief": BRIEF_LABELS,
            "generation_spec": SPEC_LABELS}.get(kind, ())


def parse_sections(text: str, labels: tuple[str, ...]) -> dict[str, str]:
    """`label: value` sections, on their own lines in a file or run
    together on one line inside a tag in the Doc. Text before the first
    label is kept under ''."""
    if not labels:
        return {}
    pattern = re.compile(r"(?i)(?:^|(?<=\s))(" + "|".join(
        re.escape(l) for l in sorted(labels, key=len, reverse=True))
        + r")\s*:\s*")
    found = list(pattern.finditer(text))
    out: dict[str, str] = {}
    if found and text[:found[0].start()].strip():
        out[""] = text[:found[0].start()].strip()
    for n, m in enumerate(found):
        end = found[n + 1].start() if n + 1 < len(found) else len(text)
        out[m.group(1).lower()] = " ".join(text[m.end():end].split())
    return out


def format_sections(text: str, kind: str) -> str:
    """The canonical file form: one `label: value` line per section in the
    kind's order; text without sections is kept as written."""
    labels = _labels_for(kind)
    sections = parse_sections(text, labels)
    if not sections or list(sections) == [""]:
        return text.strip()
    lines = [sections[""]] if "" in sections else []
    lines += [f"{l}: {sections[l]}" for l in labels if l in sections]
    return "\n".join(lines)


def check_youtube_brief(text: str) -> list[str]:
    """Why a YouTube brief is not yet enough to search with: it names a
    source (the work or person), and a quote or a moment to find."""
    s = parse_sections(text, BRIEF_LABELS)
    reasons = []
    if not s.get("source"):
        reasons.append("YouTube brief has no 'source:' (the work or person: "
                       "title, year, channel)")
    if not (s.get("quote") or s.get("moment")):
        reasons.append("YouTube brief needs a 'quote:' (exact words spoken) or "
                       "a 'moment:' (the scene) to find")
    return reasons


def check_generation_spec(text: str) -> list[str]:
    """Why a generation spec is not yet enough to generate from: 150+
    words, and every section present ('none' is an answer)."""
    s = parse_sections(text, SPEC_LABELS)
    reasons = []
    words = len(text.split())
    if words < DRAFT_WORDS:
        reasons.append(f"generation spec has {words} words; it needs "
                       f"{DRAFT_WORDS}+")
    missing = [l for l in SPEC_LABELS if l not in s]
    if missing:
        reasons.append("generation spec is missing " +
                       ", ".join(f"'{l}:'" for l in missing))
    return reasons


def check_text(kind: str, text: str) -> list[str]:
    if kind == "youtube_brief":
        return check_youtube_brief(text)
    if kind == "generation_spec":
        return check_generation_spec(text)
    return []


def brief_terms(text: str) -> dict:
    """What ytlm searches with: `query` (source + moment) for the video
    search, `quote` (unquoted) for locating the moment in its transcript."""
    s = parse_sections(text, BRIEF_LABELS)
    quote = s.get("quote", "").strip().strip('"“”')
    return {"query": " ".join(v for v in (s.get("source"), s.get("moment"))
                              if v),
            "quote": quote}


def _excerpt(full: str) -> str:
    tokens = " ".join(full.split()).split()
    head = " ".join(tokens[:EXCERPT_WORDS])
    return head + ("…" if len(tokens) > EXCERPT_WORDS else "")


def _split_body(body: str) -> tuple[str, str]:
    """(description part, raw field tail beginning at the first `|`)."""
    m = _FIELD.search(body)
    return (body, "") if not m else (body[:m.start()], body[m.start():])


def _tag_line(kind: str, text: str, ref: str, tail: str = "") -> str:
    body = (f"{text} ⇢ {ref}" if text else f"⇢ {ref}")
    return f"[{kind.title()}: {body}{tail}]"


def _clip_line(excerpt: str, ref: str, tail: str) -> str:
    return _tag_line("clip", excerpt, ref, tail)


def _mint_name(description: str, fields: dict, taken: set[str],
               prefix: str = "") -> str:
    if prefix:
        base = f"{prefix}-" + (_slug(description) or "untitled")
    elif description:
        base = _slug(description)
    elif fields.get("youtube") and _youtube_id(fields["youtube"]):
        base = "yt-" + _youtube_id(fields["youtube"]).lower()
    elif fields.get("use"):
        base = _slug(Path(fields["use"]).stem.replace("-", " ").replace("_", " "))
    else:
        base = ""
    base = base or "clip"
    name, n = f"{base}.md", 2
    while name in taken:
        name, n = f"{base}-{n}.md", n + 1
    return name


def _clip_lines(text: str, containers: bool = False):
    """(line index, kind, body) for every [Clip] tag line — and, with
    `containers`, every [Collage]/[Montage] opening — outside fences."""
    fenced = False
    for i, line in enumerate(text.split("\n")):
        if _FENCE.match(line):
            fenced = not fenced
            continue
        if fenced:
            continue
        m = CLIP_OPEN.match(line)
        if m:
            yield i, "clip", m.group("body")
            continue
        m = CONTAINER_OPEN.match(line) if containers else None
        if m:
            yield i, m.group("kind").lower(), m.group("body")


def maintain_keys(root: Path, files: list[str] | None = None) -> list[dict]:
    """Collect's step, run before the snapshot (§8a): every [Clip],
    [Collage] and [Montage] gets its key. An un-keyed tag's text moves to
    its kind's folder (`_clips/youtube-briefs/`, `_clips/specs/` or
    `_clips/notes/`, canonical even when empty) and the tag keeps an
    excerpt and `⇢ <key>.md`. A keyed tag's excerpt is regenerated from its
    file; an excerpt edit does not hold and is reported. A tag whose kind
    changed (a description replaced by `youtube:`) has its file MOVED to
    the new kind's folder, reported, and checked as the new kind — which
    flags it for a fresh draft. A missing file is reported, never
    recreated by guessing. Rows: {file, line, key, minted | excerpt_fixed |
    moved | missing}."""
    from .revisions import iter_manuscript_paths

    root = Path(root)
    specs = load_specs(root)
    taken = set(specs)
    report: list[dict] = []
    for rel, path in iter_manuscript_paths(root).items():
        if not rel.endswith(".md") or (files and rel not in files):
            continue
        raw = path.read_text(encoding="utf-8", errors="replace")
        lines = raw.split("\n")
        changed = False
        for i, kind, body in _clip_lines(raw, containers=True):
            if kind == "clip":
                parsed = parse_fields(body)
                _desc, tail = _split_body(body)
            else:
                # A container's text is its title; it has no fields.
                parsed = parse_fields(body.replace("|", "/"))
                parsed["fields"], tail = {}, ""
            want_kind = spec_kind(kind, parsed["fields"])
            ref = parsed["ref"]
            if ref:
                entry = specs.get(ref)
                if entry is None:
                    report.append({"file": rel, "line": i + 1,
                                   "key": ref[:-3], "missing": True})
                    continue
                if entry["kind"] != want_kind:
                    src = specs_dir(root, entry["kind"]) / ref
                    dst = specs_dir(root, want_kind) / ref
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    src.rename(dst)
                    report.append({"file": rel, "line": i + 1, "key": ref[:-3],
                                   "moved": True, "from": entry["kind"],
                                   "to": want_kind})
                    entry["kind"] = want_kind
                want = _tag_line(kind, _excerpt(entry["text"]), ref, tail)
                if lines[i] != want:
                    report.append({"file": rel, "line": i + 1, "key": ref[:-3],
                                   "excerpt_fixed": True})
                    lines[i] = want
                    changed = True
                continue
            name = _mint_name(parsed["description"], parsed["fields"], taken,
                              "" if kind == "clip" else kind)
            taken.add(name)
            directory = specs_dir(root, want_kind)
            directory.mkdir(parents=True, exist_ok=True)
            canonical = format_sections(parsed["description"], want_kind)
            (directory / name).write_text(canonical + "\n", encoding="utf-8")
            specs[name] = {"kind": want_kind, "text": canonical}
            lines[i] = _tag_line(kind, _excerpt(canonical), name, tail)
            report.append({"file": rel, "line": i + 1, "key": name[:-3],
                           "minted": True, "to": want_kind})
            changed = True
        if changed:
            path.write_text("\n".join(lines), encoding="utf-8")
    return report


def find_clip(root: Path, key: str) -> dict:
    """The keyed clip's file, 0-based line index and parsed tag."""
    from .revisions import iter_manuscript_paths

    ref = key if key.endswith(".md") else f"{key}.md"
    for rel, path in iter_manuscript_paths(Path(root)).items():
        if not rel.endswith(".md"):
            continue
        raw = path.read_text(encoding="utf-8", errors="replace")
        for i, kind, body in _clip_lines(raw, containers=True):
            parsed = parse_fields(body if kind == "clip"
                                  else body.replace("|", "/"))
            if parsed["ref"] == ref:
                if kind != "clip":
                    parsed["fields"] = {}
                return {"file": rel, "path": path, "i": i, "body": body,
                        "kind": kind, **parsed}
    raise LookupError(f"no [Clip], [Collage] or [Montage] carries ⇢ {ref} — "
                      "'media list' shows the keys; a tag gets its key at "
                      "collect")


def set_spec(root: Path, key: str, text: str) -> dict:
    """Land a YouTube brief, generation spec or note (§8e) in the folder
    of the tag's kind, then refresh the tag's excerpt. Returns {file, key,
    spec_kind, words, spec_path, reasons} — `reasons` from the kind's
    check, empty when it is ready."""
    clip = find_clip(root, key)
    text = text.strip()
    if not text:
        raise ValueError("empty text — nothing to land")
    kind = spec_kind(clip["kind"], clip["fields"])
    canonical = format_sections(text, kind)
    path = specs_dir(root, kind) / clip["ref"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical + "\n", encoding="utf-8")
    maintain_keys(root, [clip["file"]])
    return {"file": clip["file"], "key": clip["ref"][:-3], "spec_kind": kind,
            "words": len(canonical.split()), "spec_path": str(path),
            "reasons": check_text(kind, canonical)}


def spec_form(root: Path, key: str, text: str) -> dict:
    """The pending form that shows a drafted spec in place (§8e Doc road):
    {file, i, old, new} where `new` is the tag with the WHOLE spec inline,
    still carrying `⇢ <key>.md`, so the author reads and edits it in the
    tab. `absorb_specs` turns an accepted form back into excerpt + file."""
    clip = find_clip(root, key)
    _desc, tail = _split_body(clip["body"]) if clip["kind"] == "clip" else ("", "")
    lines = clip["path"].read_text(encoding="utf-8").split("\n")
    return {"file": clip["file"], "i": clip["i"], "old": lines[clip["i"]],
            "new": _tag_line(clip["kind"], " ".join(text.split()),
                             clip["ref"], tail),
            "key": clip["ref"][:-3]}


def absorb_specs(root: Path, text: str, keys: set[str]) -> tuple[str, list[dict]]:
    """A resolved tab text → its spec files. For each named key whose tag
    now carries more than its excerpt (an accepted or edited spec form),
    the inline text becomes the canonical spec and the tag goes back to
    excerpt + `⇢`. A declined form left the excerpt standing: nothing
    lands. Returns (text, [{key, words}])."""
    specs = load_specs(root)
    lines = text.split("\n")
    landed: list[dict] = []
    for i, kind, body in list(_clip_lines(text, containers=True)):
        parsed = parse_fields(body if kind == "clip" else body.replace("|", "/"))
        ref = parsed["ref"]
        if not ref or ref[:-3] not in keys:
            continue
        inline = parsed["description"]
        # A declined form leaves the excerpt; parse_fields has already
        # dropped its trailing ellipsis.
        current = (specs.get(ref) or {}).get("text", "")
        if not inline or inline == _excerpt(current).rstrip(" …"):
            continue
        want_kind = spec_kind(kind, parsed["fields"] if kind == "clip" else {})
        canonical = format_sections(inline, want_kind)
        directory = specs_dir(root, want_kind)
        directory.mkdir(parents=True, exist_ok=True)
        old = specs.get(ref)
        if old and old["kind"] != want_kind:
            (specs_dir(root, old["kind"]) / ref).unlink(missing_ok=True)
        (directory / ref).write_text(canonical + "\n", encoding="utf-8")
        _desc, tail = _split_body(body) if kind == "clip" else ("", "")
        lines[i] = _tag_line(kind, _excerpt(canonical), ref, tail)
        landed.append({"key": ref[:-3], "spec_kind": want_kind,
                       "words": len(canonical.split()),
                       "reasons": check_text(want_kind, canonical)})
    return "\n".join(lines), landed


def set_fields(root: Path, key: str, updates: dict) -> dict:
    """Write fields into a keyed clip's tag: a value sets the field, None
    removes it; the others keep their order. Validated by name only —
    `media list` checks the combinations. Returns {file, line, tag}."""
    clip = find_clip(root, key)
    if clip["kind"] != "clip":
        raise ValueError(f"{key} is a {clip['kind']}: a container has a "
                         "title, not fields")
    unknown = sorted(set(updates) - set(FIELDS))
    if unknown:
        raise ValueError(f"unknown field(s) {', '.join(unknown)} "
                         f"(one of: {', '.join(FIELDS)})")
    fields = dict(clip["fields"])
    for name, value in updates.items():
        if value is None:
            fields.pop(name, None)
        else:
            fields[name] = value
    tail = ""
    for name, value in fields.items():
        if name == "cast" and isinstance(value, list):
            value = ", ".join(value)
        elif name == "text":
            value = '"' + str(value).strip('"') + '"'
        tail += f" | {name}: {value}"
    desc, _old_tail = _split_body(clip["body"])
    line = f"[Clip:{desc.rstrip()}{tail}]" if desc.strip() else f"[Clip:{tail}]"
    lines = clip["path"].read_text(encoding="utf-8").split("\n")
    lines[clip["i"]] = line
    clip["path"].write_text("\n".join(lines), encoding="utf-8")
    return {"file": clip["file"], "line": clip["i"] + 1, "tag": line}


# ------------------------------------------------------------------ exports

def strip_for_book(text: str, where: str = "") -> tuple[str, int]:
    """Every book export (pdf, docx, epub, md, doc, audio): the Clip,
    Collage and Montage tag lines and clip embed lines go; every word of
    prose stays, wherever it sits (ruled 2026-10-17: a tag never deletes
    the author's prose). Returns (text, number of opening tags removed).
    `where` is kept for the callers' symmetry with resolve_regions."""
    out: list[str] = []
    removed = 0
    fenced = False
    for line in text.split("\n"):
        if _FENCE.match(line):
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
