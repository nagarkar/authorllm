"""The audiobook export — `docs/audiobook-pipeline-design.md`.

AuthorLM is the sole producer of the audio manifest; **audiostation**
(`audiostation/`, the Tauri studio) generates, stitches, audits and
packages; ElevenLabs is the voice. This module is the producer.

Two writers, two kinds of file, never the same file (design §1.2):

    <manuscript>/_audio/
      audiobook.toml        the author's; seeded ONCE by `audio init`, read here
      cast.md               the author's; one row written by `audio cast set`
      audiobook.json        written here — book metadata, chapter order, credits
      chapters/<stem>.json  written here — ordered sections with content ids
      state/<stem>.json     audiostation's — never touched by this module
      audio/                audiostation's

The shared key is the section id (§6): a hash of the normalized text,
the resolved voice parameters and the pronunciation rules that apply to
that text. Same id, same audio. Every rule that shapes the spoken text
is configuration in `audiobook.toml` (§3, §4), resolved here so that
audiostation never holds a second copy of the rules.

Pure where it can be: the text pipeline, the casting resolution and the
hashing take strings and dicts and return the same. The database is
touched only for manuscript metadata and the export settings; the
network only by the ElevenLabs client at the bottom, which the export
uses for one optional lookup (the dictionary version by name) and the
audition / dictionary verbs use deliberately.

Decisions:
- Audiobook cast selection is declared only in the manuscript's `_audio/audiobook.toml`:
  required `text.voice`, `headings.voice`, and `credits.voice`, plus optional
  `[[chapter]]` exact-file text overrides without parent inheritance. No Python
  role fallback or TOC voice is permitted; every configured cast reference is
  validated, even when no speech uses it. Explicit `headings.voice =
  "essay-default"` follows the file's text role. Plain voice tags persist until
  the next tag or heading. Export resolves every speech parameter for both
  consumers. Enforced by: tests/test_audio_voice_config.py::VoiceConfigTests::test_required_voice_fields_are_not_filled_in
  and tests/test_audio_voice_config.py::VoiceConfigTests::test_chapter_override_is_exact_file_without_inheritance. (owner)
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import struct
import subprocess
import sys
import time
import tomllib
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from . import directives
from . import pronunciations as pron
from .export import (VOICE_LINE, load_settings, resolve_regions,
                     strip_display_math)
from .gdocs import normalize_markdown
from .illus import parse_tag as parse_illustration_tag
from .revisions import read_manuscript_files
from .structure import reading_order, toc_attrs

AUDIO_DIR = "_audio"
CONFIG_FILENAME = "audiobook.toml"
CAST_FILENAME = "cast.md"
BOOK_FILENAME = "audiobook.json"
CHAPTERS_DIR = "chapters"
STATE_DIR = "state"
AUDITIONS_DIR = "auditions"
SCHEMA = 1

# The only file the export never treats as a chapter by NAME: its text
# feeds the credits (design §4). Everything else is routed by toc.toml.
TITLE_FILE = "title.md"
ABOUT_ROUTE = "about-author"

KEY_ENV = "ELEVENLABS_API_KEY"
API_BASE = "https://api.elevenlabs.io/v1"

# ---------------------------------------------------------------- config

_CONFIG_DEFAULTS: dict = {
    "schema": SCHEMA,
    "text": {
        "paragraph_gap_ms": 700,
        "rule_gap_ms": 1500,
        "head_gap_ms": 800,
        "tail_gap_ms": 2000,
        "footnotes": "drop",
        "inline_math": "warn",
        "lists": "one-section",
        "max_section_chars": 5000,
    },
    "headings": {
        "gap_ms": {"h1": 3250, "h2": 2250, "other": 1250},
    },
    "tts": {
        "model": "eleven_multilingual_v2",
        "quality": "mp3_44100_128",
        "dictionary": "",
    },
    "credits": {
        "opening": "{title}. {subtitle}. Written by {author}. "
                   "Narrated by {narrator}.",
        "closing": "This has been {title}, written by {author} and "
                   "narrated by {narrator}. Copyright {copyright_year} by "
                   "{copyright_owner}. Production copyright "
                   "{copyright_year} by {publisher}.",
    },
    "retail_sample": {"chapter": "", "heading": "", "paragraphs": 6},
    "cover": {"path": ""},
    # The free listen before any paid render (review design §6): a macOS
    # voice and a base rate in words per minute, multiplied by the cast
    # row's speed.
    "preview": {"voice": "Daniel", "rate": 175},
}

_ENUMS = {
    ("text", "footnotes"): ("drop", "inline", "end"),
    ("text", "inline_math"): ("warn", "strip", "refuse"),
    ("text", "lists"): ("one-section", "one-per-item"),
}

# Phoneme rules work only here (verified against the ElevenLabs docs,
# 2026-09-03); every other model needs alias rules (design §7).
PHONEME_MODELS = ("eleven_flash_v2", "eleven_v3")
QUALITIES = ("mp3_22050_32", "mp3_44100_64", "mp3_44100_128", "mp3_44100_192")

# The seed is a packaged, author-editable configuration file. Voice choices
# have no runtime defaults: load_config requires them in the manuscript copy.
DEFAULT_CONFIG_TEXT = Path(__file__).with_name("audiobook-defaults.toml").read_text(
    encoding="utf-8")

SEED_CAST = (
    "# **Cast**\n\n"
    "Who speaks, and how. One row per role; `[Voice: key]` in the prose "
    "and `voice = \"key\"` in audiobook.toml name a row here. Blank Model, "
    "Stability, Similarity or Speed take the defaults (audiobook.toml's "
    "model; 0.5 / 0.75 / 1.0). AuthorLM writes a row only through "
    "`audio cast set`; the rest of this file is yours.\n\n"
    "| Key | Voice | Voice ID | Model | Stability | Similarity | Speed | Note |\n"
    "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
    "| narrator |  |  |  | 0.5 | 0.75 | 1.0 | The default for every essay and heading |\n"
)

CAST_COLUMNS = ("key", "voice", "voice_id", "model", "stability",
                "similarity", "speed", "note")
_KEY_RE = re.compile(r"^[a-z][a-z0-9_-]*$")


class AudioError(ValueError):
    """A named refusal from the export or its verbs."""


def audio_dir(manuscript: dict) -> Path:
    return Path(manuscript["path"]) / AUDIO_DIR


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(root: Path) -> dict:
    """Required voice declarations plus defaults for other settings.
    An unknown key, unknown enum or wrong type is refused by name (design §3):
    a typo must never silently fall back to a default."""
    path = root / AUDIO_DIR / CONFIG_FILENAME
    if not path.exists():
        raise AudioError(f"{path}: does not exist — run 'audio init'")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as err:
        raise AudioError(f"{path.name}: not valid TOML — {err}") from None
    _validate_config(data, path.name)
    return _deep_merge(_CONFIG_DEFAULTS, data)


def _validate_config(data: dict, where: str) -> None:
    for section, value in data.items():
        if section == "chapter":
            _validate_chapter_config(value, where)
            continue
        if section == "schema":
            if value != SCHEMA:
                raise AudioError(f"{where}: schema = {value!r}; this "
                                 f"AuthorLM writes schema {SCHEMA}")
            continue
        if section not in _CONFIG_DEFAULTS:
            raise AudioError(f"{where}: unknown table [{section}] (one of: "
                             + ", ".join(k for k in _CONFIG_DEFAULTS
                                         if k != "schema") + ", chapter)")
        if not isinstance(value, dict):
            raise AudioError(f"{where}: [{section}] must be a table")
        for key, val in value.items():
            if key == "voice" and section in ("text", "headings", "credits"):
                _validate_voice_key(val, f"{where}: {section}.voice")
                continue
            if key not in _CONFIG_DEFAULTS[section]:
                raise AudioError(
                    f"{where}: unknown key {section}.{key} (one of: "
                    + ", ".join(_CONFIG_DEFAULTS[section]) + ")")
            default = _CONFIG_DEFAULTS[section][key]
            if isinstance(default, bool) or default is None:
                continue
            if isinstance(default, dict):
                if not isinstance(val, dict):
                    raise AudioError(f"{where}: {section}.{key} must be a "
                                     f"table like {default}")
                for sub, subval in val.items():
                    if sub not in default:
                        raise AudioError(
                            f"{where}: unknown key {section}.{key}.{sub} "
                            f"(one of: {', '.join(default)})")
                    if not isinstance(subval, int) or isinstance(subval, bool):
                        raise AudioError(
                            f"{where}: {section}.{key}.{sub} must be an "
                            f"integer number of milliseconds")
                continue
            if isinstance(default, int) and not isinstance(val, int):
                raise AudioError(f"{where}: {section}.{key} must be an integer")
            if isinstance(default, str) and not isinstance(val, str):
                raise AudioError(f"{where}: {section}.{key} must be a string")
            legal = _ENUMS.get((section, key))
            if legal and val not in legal:
                raise AudioError(f"{where}: {section}.{key} = {val!r}; one of "
                                 + ", ".join(legal))
    tts = data.get("tts", {})
    if tts.get("quality") and tts["quality"] not in QUALITIES:
        raise AudioError(f"{where}: tts.quality = {tts['quality']!r}; one of "
                         + ", ".join(QUALITIES))
    for section in ("text", "headings", "credits"):
        if "voice" not in data.get(section, {}):
            raise AudioError(f"{where}: {section}.voice is required — "
                             f"set a cast key in [{section}]")


def _validate_voice_key(value: object, where: str) -> None:
    if not isinstance(value, str) or not _KEY_RE.fullmatch(value):
        raise AudioError(f"{where} must be a nonempty cast key "
                         "([a-z][a-z0-9_-]*)")


def _validate_chapter_config(value: object, where: str) -> None:
    if not isinstance(value, list):
        raise AudioError(f"{where}: chapter must be repeatable [[chapter]] tables")
    seen: set[str] = set()
    for index, chapter in enumerate(value, 1):
        label = f"{where}: chapter[{index}]"
        if not isinstance(chapter, dict):
            raise AudioError(f"{label} must be a table with file and voice")
        unknown = chapter.keys() - {"file", "voice"}
        if unknown:
            raise AudioError(f"{label}: unknown key {sorted(unknown)[0]}")
        name = chapter.get("file")
        if (not isinstance(name, str) or not name.strip()
                or name != name.strip() or Path(name).is_absolute()
                or ".." in Path(name).parts or not name.endswith(".md")):
            raise AudioError(f"{label}.file must name a manuscript .md file")
        if name in seen:
            raise AudioError(f"{label}: duplicate file {name!r}")
        seen.add(name)
        _validate_voice_key(chapter.get("voice"), f"{label} ({name}).voice")


def init(manuscript: dict) -> dict:
    """Seed missing audio configuration files, preserving existing bytes.
    Refuses when both already exist: each file belongs to the author."""
    root = audio_dir(manuscript)
    config = root / CONFIG_FILENAME
    cast = root / CAST_FILENAME
    existing = [p.name for p in (config, cast) if p.exists()]
    if len(existing) == 2:
        raise AudioError(
            f"{', '.join(existing)} already exist(s) in {AUDIO_DIR}/ — "
            "AuthorLM writes them once and never again; edit them directly.")
    root.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    unchanged: list[str] = []
    for path, content in ((config, DEFAULT_CONFIG_TEXT), (cast, SEED_CAST)):
        if path.exists():
            unchanged.append(str(path))
            continue
        with path.open("x", encoding="utf-8") as stream:
            stream.write(content)
        written.append(str(path))
    return {"config": str(config), "cast": str(cast),
            "written": written, "unchanged": unchanged}


# ------------------------------------------------------------------ cast

def _cast_cells(line: str) -> list[str]:
    return pron._cells(line)


def parse_cast(text: str) -> tuple[list[dict], list[str]]:
    """`(rows, warnings)` from `cast.md`. Same pipe-table discipline as
    the pronunciation table: prose around the table is ignored, a
    shifted row is skipped and named, never guessed at."""
    rows: list[dict] = []
    warnings: list[str] = []
    seen: set[str] = set()
    in_table = False
    for line in (text or "").replace("\r\n", "\n").split("\n"):
        cells = _cast_cells(line)
        if not cells:
            if in_table:
                break
            continue
        if not in_table:
            if cells and cells[0].strip().casefold() == "key":
                in_table = True
            continue
        if pron._is_delimiter(cells):
            continue
        key = cells[0].strip()
        if not key:
            continue
        if len([c for c in cells[len(CAST_COLUMNS):] if c]) or any(
                "|" in c for c in cells[:len(CAST_COLUMNS)]):
            warnings.append(f"cast row '{key}' carries a '|' and cannot be "
                            f"read as one row — skipped")
            continue
        if not _KEY_RE.match(key):
            warnings.append(f"cast key '{key}' is not a key ([a-z][a-z0-9_-]*)"
                            " — skipped")
            continue
        if key in seen:
            warnings.append(f"cast key '{key}' appears twice — the first "
                            "row wins")
            continue
        seen.add(key)
        padded = cells + [""] * (len(CAST_COLUMNS) - len(cells))
        row = dict(zip(CAST_COLUMNS, (c.strip() for c in padded)))
        rows.append(row)
    return rows, warnings


def render_cast_row(row: dict) -> str:
    for field in CAST_COLUMNS:
        value = str(row.get(field) or "")
        if "|" in value or "\n" in value:
            raise AudioError(f"a cast row's `{field}` carries a '|' or a "
                             "newline; a row is one line")
    return "| " + " | ".join(str(row.get(f) or "") for f in CAST_COLUMNS) + " |"


def set_cast_row(text: str, row: dict) -> str:
    """Write one row: replace the row with the same key, else append
    after the table's last row. Rewrites no other line."""
    key = (row.get("key") or "").strip()
    if not _KEY_RE.match(key):
        raise AudioError(f"'{key}' is not a cast key ([a-z][a-z0-9_-]*)")
    line = render_cast_row(row)
    if not (text or "").strip():
        return SEED_CAST.rsplit("\n| narrator", 1)[0] + "\n" + line + "\n"
    crlf = text.count("\r\n")
    newline = "\r\n" if crlf > text.count("\n") - crlf else "\n"
    lines = text.split(newline)
    last_row = None
    in_table = False
    for i, raw in enumerate(lines):
        cells = _cast_cells(raw)
        if not cells:
            if in_table:
                break
            continue
        if not in_table:
            if cells[0].strip().casefold() == "key":
                in_table = True
                last_row = i
            continue
        last_row = i
        if not pron._is_delimiter(cells) and cells[0].strip() == key:
            lines[i] = line
            return newline.join(lines)
    if last_row is None:
        body = newline.join(lines).rstrip(newline)
        header = SEED_CAST.split("\n")[-4:-2]
        return body + newline + newline.join(["", *header, line, ""])
    return newline.join(lines[:last_row + 1] + [line] + lines[last_row + 1:])


def _num(value: str, default: float, name: str, key: str,
         low: float, high: float) -> float:
    if not (value or "").strip():
        return default
    try:
        f = float(value)
    except ValueError:
        raise AudioError(f"cast '{key}': {name} = {value!r} is not a number")
    if not low <= f <= high:
        raise AudioError(f"cast '{key}': {name} = {f:g} is outside "
                         f"{low:g}–{high:g}")
    return f


def load_cast(root: Path, config: dict) -> tuple[dict[str, dict], list[str]]:
    """key → resolved voice (voice_id, voice_name, model, stability,
    similarity, speed, note) with the defaults applied."""
    path = root / AUDIO_DIR / CAST_FILENAME
    rows, warnings = parse_cast(path.read_text(encoding="utf-8")
                                if path.exists() else "")
    cast: dict[str, dict] = {}
    for row in rows:
        key = row["key"]
        cast[key] = {
            "key": key,
            "voice_name": row["voice"],
            "voice_id": row["voice_id"],
            "model": row["model"] or config["tts"]["model"],
            "stability": _num(row["stability"], 0.5, "Stability", key, 0, 1),
            "similarity": _num(row["similarity"], 0.75, "Similarity", key,
                               0, 1),
            "speed": _num(row["speed"], 1.0, "Speed", key, 0.25, 4.0),
            "note": row["note"],
        }
    return cast, warnings


# ------------------------------------------------------------ voice tags

_OVERRIDE_KEYS = {"stability": (0.0, 1.0), "similarity": (0.0, 1.0),
                  "speed": (0.25, 4.0), "model": None}


def parse_voice_tag(line: str) -> dict | None:
    """`[Voice: key | stability=0.6 | speed=0.95]` → {key, overrides}.
    None when the line is not a voice tag; a malformed tag raises by
    name (design §5.3)."""
    m = VOICE_LINE.match(line or "")
    if not m:
        return None
    parts = [p.strip() for p in m.group("body").split("|")]
    key = parts[0]
    if not _KEY_RE.match(key):
        raise AudioError(f"[Voice: {key}] — '{key}' is not a cast key "
                         "([a-z][a-z0-9_-]*)")
    overrides: dict = {}
    for part in parts[1:]:
        if not part:
            continue
        name, sep, value = part.partition("=")
        name, value = name.strip().lower(), value.strip()
        if not sep or name not in _OVERRIDE_KEYS:
            raise AudioError(
                f"[Voice: {key} | {part}] — an override is name=value with "
                f"name one of {', '.join(_OVERRIDE_KEYS)}")
        bounds = _OVERRIDE_KEYS[name]
        if bounds is None:
            overrides[name] = value
        else:
            try:
                f = float(value)
            except ValueError:
                raise AudioError(f"[Voice: {key} | {part}] — {name} must be "
                                 "a number") from None
            if not bounds[0] <= f <= bounds[1]:
                raise AudioError(f"[Voice: {key} | {part}] — {name} is "
                                 f"outside {bounds[0]:g}–{bounds[1]:g}")
            overrides[name] = f
    return {"key": key, "overrides": overrides}


# ----------------------------------------------------------- text rules

def normalize(text: str) -> str:
    """The hash's text: NFC, odd spaces → space, zero-width dropped,
    whitespace runs collapsed. Curly quotes and dashes are KEPT (a
    voice reads them); they fold only in audiostation's display pairing."""
    t = unicodedata.normalize("NFC", text or "")
    t = t.replace("­", "").replace("​", "").replace("‌", "")
    t = t.replace("‍", "").replace("﻿", "").replace("⁠", "")
    for ch in (" ", " ", " ", " ", " ", " "):
        t = t.replace(ch, " ")
    return " ".join(t.split())


_FOOTNOTE_REF = re.compile(r"\[\^[A-Za-z0-9_-]+\]")
_FOOTNOTE_DEF = re.compile(r"^\[\^[A-Za-z0-9_-]+\]:")
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]+)\]\((?:[^)]*)\)")
_REF_LINK = re.compile(r"\[([^\]]+)\]\[[^\]]*\]")
_STRONG = re.compile(r"\*\*(.+?)\*\*|__(.+?)__", re.DOTALL)
_EM_STAR = re.compile(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", re.DOTALL)
_EM_UNDER = re.compile(r"(?<![\w_])_(?!\s)(.+?)(?<!\s)_(?![\w_])", re.DOTALL)
_CODE = re.compile(r"`([^`]*)`")
_INLINE_MATH = re.compile(r"(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)")
_ESCAPE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!|$])")
_HTML_TAG = re.compile(r"<[^>]+>")
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_HR = re.compile(r"^(?:-{3,}|\*{3,}|_{3,})\s*$")
_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_QUOTE = re.compile(r"^\s*>\s?")
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")


def speech_text(md: str, config: dict) -> tuple[str, list[str]]:
    """One block's markdown → what the voice says. Returns
    `(text, warnings)`; `warnings` names each inline-math span the
    export should report (design §4)."""
    warnings: list[str] = []
    t = md
    # A verse stanza is one paragraph whose lines end in backslash hard
    # breaks (verse-and-cast-design.md §2); the voice never says the
    # backslash, and the line end stays a line end for the pause.
    t = re.sub(r"\\\n", "\n", t)
    t = _IMAGE.sub("", t)
    t = _FOOTNOTE_REF.sub("", t)
    t = _LINK.sub(r"\1", t)
    t = _REF_LINK.sub(r"\1", t)
    t = _CODE.sub(r"\1", t)
    math_mode = config["text"]["inline_math"]
    for m in _INLINE_MATH.finditer(t):
        if math_mode == "refuse":
            raise AudioError(f"inline math ${m.group(1)}$ in audio text and "
                             "text.inline_math = \"refuse\"")
        warnings.append(f"inline math ${m.group(1)}$ read as written")
    t = _INLINE_MATH.sub(r"\1", t)
    for _ in range(2):
        t = _STRONG.sub(lambda m: m.group(1) or m.group(2), t)
        t = _EM_STAR.sub(r"\1", t)
        t = _EM_UNDER.sub(r"\1", t)
    t = _HTML_TAG.sub(" ", t)
    t = _ESCAPE.sub(r"\1", t)
    return normalize(t), warnings


def _block_kind(block: str) -> tuple[str, dict]:
    lines = block.split("\n")
    first = lines[0].strip()
    if VOICE_LINE.match(first):
        return "voice", {}
    if _FOOTNOTE_DEF.match(first):
        return "footnote", {}
    if _HR.match(first) and len(lines) == 1:
        return "rule", {}
    m = _HEADING.match(first)
    if m and len(lines) == 1:
        return "heading", {"level": len(m.group(1)), "text": m.group(2)}
    if parse_illustration_tag(first) or _IMAGE.fullmatch(first):
        return "illustration", {}
    if all(_LIST_ITEM.match(ln) or not ln.strip() or ln.startswith("  ")
           for ln in lines) and _LIST_ITEM.match(first):
        return "list", {}
    if all(_QUOTE.match(ln) or not ln.strip() for ln in lines):
        return "quote", {}
    if all(_TABLE_ROW.match(ln) or not ln.strip() for ln in lines):
        return "table", {}
    return "paragraph", {}


def blocks_of(text: str) -> list[dict]:
    """The file as classified blocks in order, each with its 1-based
    source line. A `[Voice:]` block attaches to nothing here — the
    casting walk (chapter_sections) consumes it."""
    out: list[dict] = []
    line_no = 1
    for raw in re.split(r"\n\s*\n", text):
        stripped = raw.strip("\n")
        count = raw.count("\n") + 1
        if not stripped.strip():
            line_no += count + 1
            continue
        lead = len(raw) - len(raw.lstrip("\n"))
        start = line_no + lead
        # A tag written directly above its paragraph (no blank line —
        # the natural way to write it in Obsidian) is still a full-line
        # tag: peel every leading tag line into its own block.
        lines = stripped.split("\n")
        while lines and VOICE_LINE.match(lines[0]):
            out.append({"kind": "voice", "text": lines[0], "line": start})
            lines = lines[1:]
            start += 1
        rest = "\n".join(lines).strip("\n")
        if rest.strip():
            kind, extra = _block_kind(rest)
            out.append({"kind": kind, "text": rest, "line": start, **extra})
        line_no += count + 1
    return out


def _spoken(block: dict, config: dict) -> tuple[str, list[str]]:
    kind = block["kind"]
    text = block["text"]
    if kind == "heading":
        return speech_text(block["text_heading"], config)
    if kind == "list":
        items = [_LIST_ITEM.sub("", ln).strip() for ln in text.split("\n")
                 if ln.strip()]
        return speech_text(" ".join(items), config)
    if kind == "quote":
        return speech_text(" ".join(_QUOTE.sub("", ln).strip()
                                    for ln in text.split("\n")), config)
    if kind == "table":
        rows = []
        for ln in text.split("\n"):
            cells = [c.strip() for c in ln.strip().strip("|").split("|")]
            if pron._is_delimiter(cells):
                continue
            rows.append(", ".join(c for c in cells if c))
        return speech_text(". ".join(rows), config)
    return speech_text(" ".join(ln.strip() for ln in text.split("\n")),
                       config)


# --------------------------------------------------------- audio source

def audio_source(name: str, raw: str) -> str:
    """The stripped export's per-file pipeline: normalize, resolve the
    `audio` regions, strip the inline directive tags and display math.
    Illustration and image lines fall out in `blocks_of`."""
    text = resolve_regions(normalize_markdown(raw), {"audio"}, name)
    text, _unresolved = directives.strip_tags(text)
    text = strip_display_math(text)
    return normalize_markdown(text)


# ----------------------------------------------------- pronunciations

def pronunciation_rules(text: str) -> list[dict]:
    """`[{term, say}]` for every settled row of `pronunciations.md`."""
    rows, _warnings = pron.parse(text or "")
    return [{"term": r["term"], "say": r["say"]} for r in rows if r["say"]]


def _term_pattern(term: str) -> re.Pattern:
    return re.compile(r"(?<!\w)" + re.escape(unicodedata.normalize("NFC", term))
                      + r"(?!\w)", re.IGNORECASE)


def applicable_rules(text: str, rules: list[dict]) -> list[tuple[str, str]]:
    """The (term, say) pairs whose term occurs whole-word in `text`,
    sorted — the pronunciation half of the section id (design §6)."""
    hits = [(r["term"], r["say"]) for r in rules
            if _term_pattern(r["term"]).search(text)]
    return sorted(hits, key=lambda p: pron.key(p[0]))


def alias_for(say: str) -> str:
    """The ElevenLabs alias IS the author's respelling, as written
    (`uh-BRAK-suss`), whitespace-normalized and nothing else. Ruled
    2026-09-04: the alias is text the voice reads, the table is the
    author's ear, and no derivation stands between them."""
    return " ".join((say or "").split())


# ------------------------------------------------------------- hashing

def section_id(text: str, voice: dict, rules: list[tuple[str, str]]) -> str:
    parts = [normalize(text), voice["voice_id"], voice["model"],
             f"{float(voice['stability']):.3f}",
             f"{float(voice['similarity']):.3f}",
             f"{float(voice['speed']):.3f}"]
    parts += [f"{t}\x1e{s}" for t, s in rules]
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:32]


def silence_id(duration_ms: int, ordinal: int) -> str:
    return hashlib.sha256(f"silence\x1f{duration_ms}\x1f{ordinal}"
                          .encode()).hexdigest()[:32]


# ------------------------------------------------------ chapter build

def _resolve_voice(cast: dict[str, dict], key: str, overrides: dict,
                   where: str) -> dict:
    if key not in cast:
        raise AudioError(
            f"{where}: cast key '{key}' is not a row in {AUDIO_DIR}/"
            f"{CAST_FILENAME} (rows: {', '.join(sorted(cast)) or 'none'})")
    row = cast[key]
    if not row["voice_id"]:
        raise AudioError(f"{where}: cast '{key}' has no Voice ID yet — "
                         "audition one and `audio cast set` it")
    voice = dict(row)
    voice.update(overrides)
    voice["key"] = key
    return voice


def chapter_sections(name: str, source: str, config: dict,
                     cast: dict[str, dict], default_key: str,
                     rules: list[dict]) -> tuple[list[dict], list[str], str]:
    """The ordered sections of one file (design §4, §5), the warnings,
    and the chapter title (its first H1, else the file stem)."""
    text_cfg = config["text"]
    gaps = config["headings"]["gap_ms"]
    heading_policy = config["headings"]["voice"]
    warnings: list[str] = []
    sections: list[dict] = []
    title = Path(name).stem
    saw_title = False
    current_key, current_over = default_key, {}
    pending_tag: dict | None = None
    pending_silence = 0

    # Every configured gap means "at least this much silence here", so
    # consecutive gaps collapse to the LARGEST (a rule after a paragraph
    # is the rule's gap, not the sum), and a silence is written only
    # when speech follows it.
    def push_silence(ms: int) -> None:
        nonlocal pending_silence
        pending_silence = max(pending_silence, int(ms))

    def flush_silence() -> None:
        nonlocal pending_silence
        if pending_silence:
            sections.append({"type": "silence", "id": "",
                             "durationMs": pending_silence})
            pending_silence = 0

    def push_speech(kind: str, spoken: str, voice: dict, line: int,
                    level: int | None = None, tag_line: int | None = None) -> None:
        flush_silence()
        hits = applicable_rules(spoken, rules)
        section = {
            "type": "speech", "id": section_id(spoken, voice, hits),
            "kind": kind, "text": spoken, "cast": voice["key"],
            "voiceId": voice["voice_id"], "voiceName": voice["voice_name"],
            "model": voice["model"], "stability": float(voice["stability"]),
            "similarity": float(voice["similarity"]),
            "speed": float(voice["speed"]),
            "pronunciations": [{"term": t, "say": s} for t, s in hits],
            "source": {"line": line, **({"tag": tag_line} if tag_line else {})},
        }
        if level is not None:
            section["level"] = level
        if len(spoken) > text_cfg["max_section_chars"]:
            warnings.append(f"{name}:{line}: section of {len(spoken)} "
                            f"characters exceeds max_section_chars "
                            f"({text_cfg['max_section_chars']})")
        sections.append(section)

    last_was_heading = False
    last_heading_gap = 0
    for block in blocks_of(source):
        kind = block["kind"]
        where = f"{name}:{block['line']}"
        if kind == "voice":
            tag = parse_voice_tag(block["text"].strip())
            if block["text"].count("\n"):
                raise AudioError(f"{where}: a [Voice:] tag is one line")
            _resolve_voice(cast, tag["key"], {}, where)   # refuse unknown keys
            current_key, current_over = tag["key"], tag["overrides"]
            pending_tag = {**tag, "line": block["line"]}
            continue
        if kind in ("footnote", "illustration"):
            continue
        if kind == "rule":
            push_silence(text_cfg["rule_gap_ms"])
            last_was_heading = False
            continue
        if kind == "heading":
            level = block["level"]
            if level == 1 and not saw_title:
                spoken, _w = speech_text(block["text"], config)
                title = spoken or title
                saw_title = True
            gap = gaps["h1"] if level == 1 else gaps["h2"] if level == 2 \
                else gaps["other"]
            if last_was_heading:
                # Adjacent headings collapse to the smaller gap.
                gap = min(gap, last_heading_gap)
            last_heading_gap = gap
            push_silence(gap)
            # A heading resets the cast to the essay default (§5.3).
            current_key, current_over = default_key, {}
            pending_tag = None
            key = default_key if heading_policy == "essay-default" \
                else heading_policy
            voice = _resolve_voice(cast, key, {}, where)
            block["text_heading"] = block["text"]
            spoken, w = _spoken(block, config)
            warnings += [f"{where}: {x}" for x in w]
            if spoken:
                push_speech("heading", spoken, voice, block["line"], level)
            last_was_heading = True
            continue
        # paragraph, list, quote, table
        voice = _resolve_voice(cast, current_key, current_over, where)
        if kind == "list" and text_cfg["lists"] == "one-per-item":
            items = [_LIST_ITEM.sub("", ln).strip()
                     for ln in block["text"].split("\n") if ln.strip()]
            for i, item in enumerate(items):
                spoken, w = speech_text(item, config)
                warnings += [f"{where}: {x}" for x in w]
                if spoken:
                    push_speech("paragraph", spoken, voice, block["line"] + i,
                                tag_line=(pending_tag or {}).get("line"))
                    push_silence(text_cfg["paragraph_gap_ms"])
            pending_tag = None
            last_was_heading = False
            continue
        spoken, w = _spoken(block, config)
        warnings += [f"{where}: {x}" for x in w]
        if spoken:
            push_speech("paragraph", spoken, voice, block["line"],
                        tag_line=(pending_tag or {}).get("line"))
            push_silence(text_cfg["paragraph_gap_ms"])
        pending_tag = None
        last_was_heading = False
    # A chapter is one ACX file: it opens with the head gap (the first
    # heading's gap would be three seconds of dead air) and closes with the
    # tail gap, whatever silence the text left there.
    pending_silence = 0
    while sections and sections[-1]["type"] == "silence":
        sections.pop()
    if sections:
        if sections[0]["type"] == "silence":
            sections[0]["durationMs"] = min(sections[0]["durationMs"],
                                            text_cfg["head_gap_ms"])
        else:
            sections.insert(0, {"type": "silence", "id": "",
                                "durationMs": text_cfg["head_gap_ms"]})
        sections.append({"type": "silence", "id": "",
                         "durationMs": text_cfg["tail_gap_ms"]})
    for i, s in enumerate(sections):
        if s["type"] == "silence":
            s["id"] = silence_id(s["durationMs"], i)
    return sections, warnings, title


# ---------------------------------------------------------- metadata

def _title_page(files: dict[str, str]) -> tuple[str, str]:
    """(title, subtitle) from title.md: the H1, and the first paragraph
    after it. Either may be empty."""
    raw = files.get(TITLE_FILE) or ""
    title = subtitle = ""
    for block in blocks_of(normalize_markdown(raw)):
        if block["kind"] == "heading" and not title:
            title = normalize(_ESCAPE.sub(r"\1", _STRONG.sub(
                lambda m: m.group(1) or m.group(2), block["text"])))
            continue
        if title and block["kind"] == "paragraph" and not subtitle:
            subtitle = speech_text(block["text"], _CONFIG_DEFAULTS)[0]
            break
    return title, subtitle


def book_metadata(manuscript: dict, files: dict[str, str]) -> dict:
    settings = load_settings(manuscript)
    title, subtitle = _title_page(files)
    return {
        "title": (settings.get("title") or title or manuscript["name"]).strip(),
        "subtitle": subtitle,
        "author": (manuscript.get("author") or "").strip(),
        "narrator": (manuscript.get("narrator") or "").strip(),
        "publisher": (manuscript.get("publisher") or "").strip(),
        "copyright_year": int(manuscript.get("copyright_year") or 0) or None,
        "copyright_owner": (manuscript.get("copyright_owner") or "").strip(),
        "language": (manuscript.get("language") or "").strip() or "en",
    }


_PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")
_OPTIONAL_PLACEHOLDERS = ("subtitle",)


def render_credit(template: str, meta: dict, which: str) -> str:
    """Fill a credits template. A placeholder with no value is an error
    by name, except the optional ones (subtitle), which vanish along
    with the punctuation that framed them."""
    def fill(m: re.Match) -> str:
        key = m.group(1)
        if key not in meta:
            raise AudioError(f"credits.{which}: unknown placeholder {{{key}}} "
                             f"(one of: {', '.join(meta)})")
        value = meta[key]
        if value in (None, "", 0):
            if key in _OPTIONAL_PLACEHOLDERS:
                return ""
            raise AudioError(
                f"credits.{which} needs {{{key}}} and the manuscript has no "
                f"{key} — set it with 'authorlm manuscript set --{key.replace('_', '-')} …'")
        return str(value)
    text = _PLACEHOLDER.sub(fill, template)
    text = re.sub(r"\.\s*\.", ".", text)
    text = re.sub(r"\s+([.,;:])", r"\1", text)
    return normalize(text)


# --------------------------------------------------------------- cover

def image_info(path: Path) -> dict:
    """{width, height, format, color} from a JPEG or PNG header, no
    dependencies. color is 'rgb', 'gray', 'cmyk' or 'unknown'."""
    data = path.read_bytes()
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        w, h = struct.unpack(">II", data[16:24])
        ctype = data[25]
        color = {0: "gray", 2: "rgb", 3: "rgb", 4: "gray", 6: "rgb"}.get(
            ctype, "unknown")
        return {"width": w, "height": h, "format": "png", "color": color}
    if data[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            length = struct.unpack(">H", data[i + 2:i + 4])[0]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9,
                          0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                h, w = struct.unpack(">HH", data[i + 5:i + 9])
                comps = data[i + 9]
                color = {1: "gray", 3: "rgb", 4: "cmyk"}.get(comps, "unknown")
                return {"width": w, "height": h, "format": "jpg",
                        "color": color}
            i += 2 + length
    raise AudioError(f"{path}: not a JPEG or PNG")


def cover_problems(root: Path, config: dict) -> tuple[dict | None, list[str]]:
    rel = (config["cover"]["path"] or "").strip()
    if not rel:
        return None, ["cover.path is empty in audiobook.toml"]
    path = root / rel
    if not path.exists():
        return None, [f"cover {rel} does not exist"]
    try:
        info = image_info(path)
    except AudioError as err:
        return None, [str(err)]
    problems = []
    if info["width"] != info["height"]:
        problems.append(f"cover {rel} is {info['width']}x{info['height']}, "
                        "not square")
    if min(info["width"], info["height"]) < 2400:
        problems.append(f"cover {rel} is {info['width']}x{info['height']}; "
                        "ACX wants at least 2400x2400")
    if info["color"] != "rgb":
        problems.append(f"cover {rel} is {info['color']}, not RGB")
    return {"path": rel, **info}, problems


# -------------------------------------------------------------- export

def _stem(name: str) -> str:
    return Path(name).stem


def _write_if_changed(path: Path, data: dict, ignore: tuple[str, ...] = ()
                      ) -> bool:
    """Write pretty JSON only when the content (minus `ignore` keys)
    differs from what is on disk, so audiostation's watcher fires only
    for real changes (design §2)."""
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    if path.exists():
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            old = None
        if isinstance(old, dict):
            a = {k: v for k, v in old.items() if k not in ignore}
            b = {k: v for k, v in data.items() if k not in ignore}
            if a == b:
                return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return True


def _credit_chapter(kind: str, spoken: str, voice: dict,
                    rules: list[dict], config: dict) -> dict:
    hits = applicable_rules(spoken, rules)
    head = config["text"]["head_gap_ms"]
    tail = config["text"]["tail_gap_ms"]
    return {
        "schema": SCHEMA, "file": None, "stem": f"_{kind}", "title": {
            "opening-credits": "Opening Credits",
            "closing-credits": "Closing Credits"}[kind],
        "voiceDefault": voice["key"],
        "sections": [
            {"type": "silence", "id": silence_id(head, 0), "durationMs": head},
            {"type": "speech", "id": section_id(spoken, voice, hits),
             "kind": "paragraph", "text": spoken, "cast": voice["key"],
             "voiceId": voice["voice_id"], "voiceName": voice["voice_name"],
             "model": voice["model"], "stability": float(voice["stability"]),
             "similarity": float(voice["similarity"]),
             "speed": float(voice["speed"]),
             "pronunciations": [{"term": t, "say": s} for t, s in hits],
             "source": {"line": 0}},
            {"type": "silence", "id": silence_id(tail, 2), "durationMs": tail}],
    }


def build(manuscript: dict, files: dict[str, str] | None = None,
          dictionary: dict | None = None) -> dict:
    """Everything the export writes, computed and not yet written:
    `{book, chapters: {stem: chapter}, warnings}`. Pure of the disk
    except for reading the manuscript. Raises AudioError by name."""
    root = Path(manuscript["path"])
    files = files if files is not None else read_manuscript_files(root)
    config = load_config(root)
    cast, cast_warnings = load_cast(root, config)
    warnings = [f"{CAST_FILENAME}: {w}" for w in cast_warnings]
    attrs = toc_attrs(files.get("toc.toml") or "")
    for name, file_attrs in attrs.items():
        if "voice" in file_attrs:
            raise AudioError(f"toc.toml: {name} has legacy voice; move it to "
                             f"{AUDIO_DIR}/{CONFIG_FILENAME} [[chapter]] "
                             "with file and voice")
    order, unlisted = reading_order(files)
    for section in ("text", "headings", "credits"):
        key = config[section]["voice"]
        if section == "headings" and key == "essay-default":
            continue
        _resolve_voice(cast, key, {}, f"{CONFIG_FILENAME} {section}.voice")
    chapter_voices: dict[str, str] = {}
    for chapter in config.get("chapter", []):
        name = chapter["file"]
        if name not in order or name in (TITLE_FILE, pron.FILENAME, "manifest.md"):
            raise AudioError(f"{CONFIG_FILENAME}: chapter file {name!r} "
                             "is not a manuscript audio chapter")
        _resolve_voice(cast, chapter["voice"], {},
                       f"{CONFIG_FILENAME} chapter ({name}).voice")
        chapter_voices[name] = chapter["voice"]
    for name in unlisted:
        warnings.append(f"{name}: not in toc.toml — appended at the end")
    rules = pronunciation_rules(files.get(pron.FILENAME) or "")
    meta = book_metadata(manuscript, files)

    chapters: dict[str, dict] = {}
    chapter_order: list[dict] = []
    about_stem = None
    from .api import is_placeholder
    for name in order:
        if name == TITLE_FILE or name in (pron.FILENAME, "manifest.md"):
            continue
        if is_placeholder(files[name]):
            warnings.append(f"{name}: mid-rewrite (a writeup is open) — "
                            "omitted from the audiobook")
            continue
        file_attrs = attrs.get(name, {})
        route = file_attrs.get("audio")
        if route not in (None, ABOUT_ROUTE):
            raise AudioError(f"toc.toml: {name} has audio = {route!r}; the "
                             f"only route is \"{ABOUT_ROUTE}\"")
        default_key = chapter_voices.get(name, config["text"]["voice"])
        source = audio_source(name, files[name])
        sections, w, title = chapter_sections(name, source, config, cast,
                                              default_key, rules)
        warnings += w
        if not any(s["type"] == "speech" for s in sections):
            warnings.append(f"{name}: yields no speech — omitted")
            continue
        stem = _stem(name)
        chapter = {"schema": SCHEMA, "file": name, "stem": stem,
                   "title": title, "voiceDefault": default_key,
                   "sections": sections}
        chapters[stem] = chapter
        if route == ABOUT_ROUTE:
            about_stem = stem
        else:
            chapter_order.append({"stem": stem, "file": f"{CHAPTERS_DIR}/{stem}.json",
                                  "title": title})

    credits_voice = _resolve_voice(cast, config["credits"]["voice"], {},
                                   "audiobook.toml [credits]")
    for kind, key in (("opening-credits", "opening"),
                      ("closing-credits", "closing")):
        spoken = render_credit(config["credits"][key], meta, key)
        chapters[f"_{kind}"] = _credit_chapter(kind, spoken, credits_voice,
                                               rules, config)

    retail = _retail_sample(config, chapters, chapter_order, warnings)
    cover, cover_warnings = cover_problems(root, config)
    warnings += cover_warnings

    book = {
        "schema": SCHEMA,
        **{k: v for k, v in meta.items() if k not in ("copyright_year",
                                                      "copyright_owner")},
        "copyrightYear": meta["copyright_year"],
        "copyrightHolder": meta["copyright_owner"],
        "chapters": chapter_order,
        "openingCredits": f"{CHAPTERS_DIR}/_opening-credits.json",
        "closingCredits": f"{CHAPTERS_DIR}/_closing-credits.json",
        "aboutAuthor": f"{CHAPTERS_DIR}/{about_stem}.json" if about_stem else None,
        "retailSample": retail,
        "cover": cover,
        "pronunciationDictionary": dictionary,
        "cast": {k: {"voiceId": v["voice_id"], "voiceName": v["voice_name"],
                     "model": v["model"], "stability": v["stability"],
                     "similarity": v["similarity"], "speed": v["speed"]}
                 for k, v in cast.items()},
        "model": config["tts"]["model"],
        "quality": config["tts"]["quality"],
        "paragraphGapMs": config["text"]["paragraph_gap_ms"],
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    return {"book": book, "chapters": chapters, "warnings": warnings,
            "config": config, "cast": cast, "meta": meta}


def _retail_sample(config: dict, chapters: dict, order: list[dict],
                   warnings: list[str]) -> list[str]:
    rs = config["retail_sample"]
    if not (rs["chapter"] or "").strip():
        return []
    stem = _stem(rs["chapter"])
    chapter = chapters.get(stem)
    if chapter is None or not any(c["stem"] == stem for c in order):
        warnings.append(f"retail_sample.chapter = {rs['chapter']!r} is not a "
                        "chapter — no retail sample")
        return []
    want = normalize(rs["heading"]).casefold()
    ids: list[str] = []
    started = not want
    for s in chapter["sections"]:
        if s["type"] != "speech":
            continue
        if s["kind"] == "heading":
            if started and ids:
                break
            started = started or normalize(s["text"]).casefold() == want
            continue
        if started:
            ids.append(s["id"])
            if len(ids) >= int(rs["paragraphs"] or 0):
                break
    if want and not started:
        warnings.append(f"retail_sample.heading = {rs['heading']!r} is not a "
                        f"heading in {rs['chapter']} — no retail sample")
        return []
    return ids


def export(db, manuscript: dict, only: list[str] | None = None,
           resolve_dictionary: bool = True) -> dict:
    """Write `audiobook.json` and `chapters/*.json`, each only when its
    content changed. `only` limits which chapter files are (re)written;
    the book file always reflects every chapter. The dictionary is
    resolved by name when a key is present (design §7); offline, the
    locator is written without an id and the export says so."""
    root = Path(manuscript["path"])
    out_dir = root / AUDIO_DIR
    config = load_config(root)
    dictionary = None
    name = (config["tts"]["dictionary"] or "").strip()
    warnings: list[str] = []
    if name:
        dictionary = {"name": name, "id": None, "versionId": None}
        key = api_key(required=False) if resolve_dictionary else None
        if key:
            try:
                found = ElevenLabs(key).dictionary_by_name(name)
            except AudioError as err:
                warnings.append(f"dictionary '{name}': {err}")
                found = None
            if found:
                dictionary.update(id=found["id"],
                                  versionId=found["latest_version_id"])
            else:
                warnings.append(f"dictionary '{name}' is not on the "
                                "ElevenLabs account yet — 'audio dictionary "
                                "push' creates it")
        else:
            warnings.append(f"dictionary '{name}': no {KEY_ENV} in the "
                            "environment — locator written without a version")
    built = build(manuscript, dictionary=dictionary)
    warnings = built["warnings"] + warnings
    wanted = {_stem(o) for o in only} if only else None
    written: list[str] = []
    unchanged: list[str] = []
    for stem, chapter in built["chapters"].items():
        if wanted is not None and stem not in wanted and not stem.startswith("_"):
            continue
        path = out_dir / CHAPTERS_DIR / f"{stem}.json"
        (written if _write_if_changed(path, chapter) else unchanged).append(
            str(path.relative_to(root)))
    book_path = out_dir / BOOK_FILENAME
    book_written = _write_if_changed(book_path, built["book"],
                                     ignore=("generatedAt",))
    (written if book_written else unchanged).append(
        str(book_path.relative_to(root)))
    # The export happened even when nothing it writes changed: its time
    # is what the stale check (generation.export_staleness) compares the
    # sources against, so a re-save that changed no text clears the
    # banner on the next export instead of keeping it forever.
    os.utime(book_path, None)
    speech = sum(1 for c in built["chapters"].values()
                 for s in c["sections"] if s["type"] == "speech")
    chars = sum(len(s["text"]) for c in built["chapters"].values()
                for s in c["sections"] if s["type"] == "speech")
    return {"written": written, "unchanged": unchanged,
            "chapters": [c["stem"] for c in built["book"]["chapters"]],
            "sections": speech, "characters": chars,
            "warnings": warnings, "book": str(book_path)}


# ------------------------------------------------------------- state

def read_state(root: Path, stem: str) -> dict:
    path = root / AUDIO_DIR / STATE_DIR / f"{stem}.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {}


def generation_status(manuscript: dict, built: dict, quality: str) -> dict:
    """Read-only over audiostation's state files: per chapter, how many
    speech sections have audio at `quality`, and whether it is stitched."""
    root = Path(manuscript["path"])
    out: dict[str, dict] = {}
    for stem, chapter in built["chapters"].items():
        state = read_state(root, stem)
        done = 0
        total = 0
        sections = state.get("sections", {})
        for s in chapter["sections"]:
            if s["type"] != "speech":
                continue
            total += 1
            entry = sections.get(s["id"]) or {}
            rel = (entry.get("audioFiles") or {}).get(quality)
            if rel and (root / AUDIO_DIR / rel).exists():
                done += 1
        stitched = bool((state.get("stitched") or {}).get(quality))
        out[stem] = {"generated": done, "total": total, "stitched": stitched}
    return out


# --------------------------------------------------------------- check

_QUOTE_MIX = re.compile(r"[“\"][^”\"]+[”\"]")


def check(db, manuscript: dict) -> dict:
    """The readiness report (design §12): blocking problems, warnings,
    and the read-only generation status."""
    root = Path(manuscript["path"])
    problems: list[str] = []
    warnings: list[str] = []
    files = read_manuscript_files(root)
    if not (root / AUDIO_DIR / CONFIG_FILENAME).exists():
        return {"problems": [f"{AUDIO_DIR}/{CONFIG_FILENAME} does not exist — "
                             "run 'audio init'"], "warnings": [], "state": {},
                "ready": False}
    # Metadata first, so a build that refuses on a missing field still
    # reports every missing field by name rather than only the first.
    meta = book_metadata(manuscript, files)
    for key in ("title", "author", "narrator", "publisher", "copyright_year",
                "copyright_owner", "language"):
        if not meta.get(key):
            problems.append(f"manuscript metadata: {key} is not set")
    try:
        built = build(manuscript, files)
    except AudioError as err:
        return {"problems": problems + [str(err)], "warnings": [],
                "state": {}, "ready": False}
    config = built["config"]
    if built["book"]["aboutAuthor"] is None:
        problems.append("no about-the-author file: list about.md in toc.toml "
                        f"with matter = \"back\" and audio = \"{ABOUT_ROUTE}\"")
    for w in built["warnings"]:
        (problems if w.startswith("cover") else warnings).append(w)
    if not (config["cover"]["path"] or "").strip():
        pass  # already a problem via cover warnings
    # Mixed-speaker paragraphs, in files that cast at all.
    attrs = toc_attrs(files.get("toc.toml") or "")
    for name, text in files.items():
        if name.endswith(".md") and VOICE_LINE.search(text):
            for block in blocks_of(normalize_markdown(text)):
                if block["kind"] != "paragraph":
                    continue
                stripped = block["text"].strip()
                m = _QUOTE_MIX.search(stripped)
                if m and (m.start() > 0 or m.end() < len(stripped)) and \
                        len(stripped) - (m.end() - m.start()) > 40:
                    warnings.append(f"{name}:{block['line']}: a quotation and "
                                    "prose share one paragraph — split it "
                                    "if the speaker changes")
    for name in built["chapters"]:
        pass
    # Unresolved directive tags.
    for name, text in files.items():
        if not name.endswith(".md") or name in (pron.FILENAME, "manifest.md"):
            continue
        _t, unresolved = directives.strip_tags(normalize_markdown(text))
        for tag in unresolved:
            problems.append(f"{name}: unresolved [{tag['kind'].title()}: "
                            f"{tag['gist'][:50]}]")
    # Dictionary.
    dname = (config["tts"]["dictionary"] or "").strip()
    rules = pronunciation_rules(files.get(pron.FILENAME) or "")
    if rules and not dname:
        problems.append(f"{pron.FILENAME} has {len(rules)} rows and "
                        "tts.dictionary is empty in audiobook.toml")
    elif dname:
        key = api_key(required=False)
        if not key:
            warnings.append(f"dictionary '{dname}': no {KEY_ENV} — remote "
                            "state not checked")
        else:
            try:
                plan = dictionary_plan(manuscript, ElevenLabs(key))
                if plan["create"]:
                    problems.append(f"dictionary '{dname}' does not exist on "
                                    "ElevenLabs — 'audio dictionary push'")
                elif plan["add"] or plan["change"] or plan["remove"]:
                    problems.append(
                        f"{pron.FILENAME} and dictionary '{dname}' differ: "
                        f"{len(plan['add'])} to add, {len(plan['change'])} "
                        f"changed, {len(plan['remove'])} to remove — "
                        "'audio dictionary push'")
            except AudioError as err:
                warnings.append(f"dictionary '{dname}': {err}")
    # Retail sample length.
    ids = built["book"]["retailSample"]
    if not ids:
        problems.append("no retail sample resolved — set [retail_sample] in "
                        "audiobook.toml")
    else:
        words = 0
        for c in built["chapters"].values():
            for s in c["sections"]:
                if s["type"] == "speech" and s["id"] in ids:
                    words += len(s["text"].split())
        minutes = words / 150.0
        if not 1.0 <= minutes <= 5.0:
            problems.append(f"retail sample is about {minutes:.1f} minutes at "
                            "150 wpm; ACX wants one to five")
    # Staleness: every written file newer than every source.
    out_dir = root / AUDIO_DIR
    sources = [root / n for n in files] + [out_dir / CONFIG_FILENAME,
                                           out_dir / CAST_FILENAME]
    newest_source = max((p.stat().st_mtime for p in sources if p.exists()),
                        default=0)
    book_path = out_dir / BOOK_FILENAME
    if not book_path.exists():
        problems.append(f"{AUDIO_DIR}/{BOOK_FILENAME} has not been exported")
    else:
        stale = [p.name for p in [book_path, *(out_dir / CHAPTERS_DIR).glob("*.json")]
                 if p.stat().st_mtime < newest_source]
        if stale:
            # Content may be identical (the export writes only on change),
            # so compare rather than trust mtimes.
            for stem, chapter in built["chapters"].items():
                path = out_dir / CHAPTERS_DIR / f"{stem}.json"
                if not path.exists():
                    problems.append(f"{path.relative_to(root)} not exported")
                    continue
                try:
                    on_disk = json.loads(path.read_text(encoding="utf-8"))
                except ValueError:
                    on_disk = None
                if on_disk != chapter:
                    problems.append(f"{path.relative_to(root)} is stale — "
                                    "'audio export'")
            try:
                on_disk = json.loads(book_path.read_text(encoding="utf-8"))
                a = {k: v for k, v in on_disk.items()
                     if k not in ("generatedAt", "pronunciationDictionary")}
                b = {k: v for k, v in built["book"].items()
                     if k not in ("generatedAt", "pronunciationDictionary")}
                if a != b:
                    problems.append(f"{AUDIO_DIR}/{BOOK_FILENAME} is stale — "
                                    "'audio export'")
            except ValueError:
                problems.append(f"{AUDIO_DIR}/{BOOK_FILENAME} is not JSON")
    state = generation_status(manuscript, built, config["tts"]["quality"])
    return {"problems": problems, "warnings": warnings, "state": state,
            "ready": not problems, "quality": config["tts"]["quality"]}


# ------------------------------------------------------- ElevenLabs

def api_key(required: bool = True) -> str | None:
    from . import paths
    paths.load_env()
    key = os.environ.get(KEY_ENV, "").strip()
    if not key and required:
        raise AudioError(f"{KEY_ENV} is not set — put it in the checkout's "
                         ".env (see .env.example)")
    return key or None


class ElevenLabs:
    """The four calls this module makes, over urllib. Every failure is
    an AudioError carrying the API's own message."""

    def __init__(self, key: str, base: str = API_BASE,
                 opener=None) -> None:
        self.key = key
        self.base = base
        self._open = opener or urllib.request.urlopen

    def _request(self, method: str, path: str, body: dict | None = None,
                 accept: str = "application/json",
                 raw: bool = False) -> tuple[object, dict]:
        url = path if path.startswith("http") else self.base + path
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            "xi-api-key": self.key, "Accept": accept,
            **({"Content-Type": "application/json"} if data else {})})
        try:
            with self._open(req, timeout=120) as resp:
                payload = resp.read()
                headers = dict(resp.headers.items())
        except urllib.error.HTTPError as err:
            detail = err.read().decode("utf-8", "replace")[:400]
            raise AudioError(f"ElevenLabs {err.code} on {method} {path}: "
                             f"{detail}") from None
        except urllib.error.URLError as err:
            raise AudioError(f"ElevenLabs unreachable: {err.reason}") from None
        if raw:
            return payload, headers
        return (json.loads(payload) if payload else {}), headers

    def voices(self) -> list[dict]:
        out: list[dict] = []
        cursor = None
        while True:
            q = "?show_legacy=true" + (f"&next_cursor={cursor}" if cursor else "")
            data, _h = self._request("GET", "/voices" + q)
            for v in data.get("voices", []):
                out.append({"voice_id": v.get("voice_id", ""),
                            "name": v.get("name", ""),
                            "category": v.get("category", ""),
                            "labels": v.get("labels") or {},
                            "preview_url": v.get("preview_url") or ""})
            cursor = data.get("next_cursor") or None
            if not cursor:
                break
        return sorted(out, key=lambda v: v["name"].casefold())

    def library_voices(self, search: str, page_size: int = 20) -> list[dict]:
        q = urllib.parse.urlencode({"search": search, "page_size": page_size})
        data, _h = self._request("GET", f"/shared-voices?{q}")
        return [{"voice_id": v.get("voice_id", ""), "name": v.get("name", ""),
                 "category": v.get("category", "library"),
                 "labels": {k: v.get(k) for k in ("gender", "age", "accent",
                                                  "descriptive", "use_case")
                            if v.get(k)},
                 "public_owner_id": v.get("public_owner_id", ""),
                 "preview_url": v.get("preview_url") or ""}
                for v in data.get("voices", [])]

    def tts(self, text: str, voice: dict, output_format: str,
            previous_text: str | None = None, next_text: str | None = None,
            locators: list[dict] | None = None,
            previous_request_ids: list[str] | None = None,
            next_request_ids: list[str] | None = None) -> tuple[bytes, str]:
        """One render. The neighbour request ids are request stitching
        (pipeline §8): at most three each, honoured for two hours, not
        on v3 — the caller decides what to send."""
        body: dict = {"text": text, "model_id": voice["model"],
                      "voice_settings": {"stability": voice["stability"],
                                         "similarity_boost": voice["similarity"],
                                         "speed": voice["speed"]}}
        if previous_text:
            body["previous_text"] = previous_text
        if next_text:
            body["next_text"] = next_text
        if previous_request_ids:
            body["previous_request_ids"] = previous_request_ids[:3]
        if next_request_ids:
            body["next_request_ids"] = next_request_ids[:3]
        if locators:
            body["pronunciation_dictionary_locators"] = locators
        payload, headers = self._request(
            "POST", f"/text-to-speech/{voice['voice_id']}?output_format="
                    f"{output_format}", body, accept="audio/mpeg", raw=True)
        request_id = {k.lower(): v for k, v in headers.items()}.get(
            "request-id", "")
        return payload, request_id

    def subscription(self) -> dict:
        data, _h = self._request("GET", "/user/subscription")
        return {"used": int(data.get("character_count") or 0),
                "limit": int(data.get("character_limit") or 0)}

    def dictionaries(self) -> list[dict]:
        out: list[dict] = []
        cursor = None
        while True:
            q = "?page_size=100" + (f"&cursor={cursor}" if cursor else "")
            data, _h = self._request("GET", "/pronunciation-dictionaries" + q)
            for d in data.get("pronunciation_dictionaries", []):
                out.append({"id": d.get("id", ""), "name": d.get("name", ""),
                            "latest_version_id": d.get("latest_version_id", "")})
            cursor = data.get("next_cursor") or None
            if not cursor:
                break
        return out

    def dictionary_by_name(self, name: str) -> dict | None:
        want = name.strip().casefold()
        for d in self.dictionaries():
            if d["name"].strip().casefold() == want:
                return d
        return None

    def dictionary_rules(self, dictionary_id: str, version_id: str) -> list[dict]:
        """The rules of one version, from its PLS download:
        [{string_to_replace, type, alias|phoneme}]."""
        payload, _h = self._request(
            "GET", f"/pronunciation-dictionaries/{dictionary_id}/{version_id}"
                   "/download", accept="application/pls+xml", raw=True)
        return parse_pls(payload.decode("utf-8", "replace"))

    def create_dictionary(self, name: str, rules: list[dict]) -> dict:
        data, _h = self._request("POST", "/pronunciation-dictionaries/add-from-rules",
                                 {"name": name, "rules": rules})
        return {"id": data.get("id", ""),
                "latest_version_id": data.get("version_id", "")}

    def add_rules(self, dictionary_id: str, rules: list[dict]) -> str:
        data, _h = self._request(
            "POST", f"/pronunciation-dictionaries/{dictionary_id}/add-rules",
            {"rules": rules})
        return data.get("version_id", "")

    def remove_rules(self, dictionary_id: str, strings: list[str]) -> str:
        data, _h = self._request(
            "POST", f"/pronunciation-dictionaries/{dictionary_id}/remove-rules",
            {"rule_strings": strings})
        return data.get("version_id", "")


_LEXEME = re.compile(r"<lexeme[^>]*>(.*?)</lexeme>", re.DOTALL)
_GRAPHEME = re.compile(r"<grapheme[^>]*>(.*?)</grapheme>", re.DOTALL)
_ALIAS = re.compile(r"<alias[^>]*>(.*?)</alias>", re.DOTALL)
_PHONEME = re.compile(r"<phoneme[^>]*>(.*?)</phoneme>", re.DOTALL)


def parse_pls(xml: str) -> list[dict]:
    """W3C PLS → rules. Regex, not an XML parser: the default namespace
    defeats namespace-naive element lookups and this is four tags."""
    import html
    rules: list[dict] = []
    for block in _LEXEME.findall(xml or ""):
        g = _GRAPHEME.search(block)
        if not g:
            continue
        grapheme = html.unescape(g.group(1).strip())
        p = _PHONEME.search(block)
        a = _ALIAS.search(block)
        if p:
            rules.append({"string_to_replace": grapheme, "type": "phoneme",
                          "phoneme": html.unescape(p.group(1).strip())})
        elif a:
            rules.append({"string_to_replace": grapheme, "type": "alias",
                          "alias": html.unescape(a.group(1).strip())})
    return rules


# ---------------------------------------------------------- dictionary

def desired_rules(manuscript: dict, config: dict) -> dict[str, dict]:
    """string_to_replace → rule, from `pronunciations.md`: every term as
    written and lowercased (matching is case sensitive), alias or
    phoneme by the model (design §7)."""
    root = Path(manuscript["path"])
    path = root / pron.FILENAME
    rows, _w = pron.parse(path.read_text(encoding="utf-8")
                          if path.exists() else "")
    phoneme = config["tts"]["model"] in PHONEME_MODELS
    out: dict[str, dict] = {}
    for row in rows:
        if not row["say"]:
            continue
        variants = {row["term"], row["term"].lower()}
        for term in variants:
            if phoneme:
                ipa = (row.get("ipa") or "").strip()
                if ipa:
                    out[term] = {"string_to_replace": term, "type": "phoneme",
                                 "phoneme": ipa, "alphabet": "ipa"}
                    continue
            out[term] = {"string_to_replace": term, "type": "alias",
                         "alias": alias_for(row["say"])}
    return out


def _rule_target(rule: dict) -> str:
    return rule.get("alias") or rule.get("phoneme") or ""


def dictionary_plan(manuscript: dict, client: ElevenLabs) -> dict:
    """What a push would do, without doing it (design §7)."""
    root = Path(manuscript["path"])
    config = load_config(root)
    name = (config["tts"]["dictionary"] or "").strip()
    if not name:
        raise AudioError("tts.dictionary is empty in audiobook.toml — name "
                         "the ElevenLabs dictionary this book uses")
    wanted = desired_rules(manuscript, config)
    remote = client.dictionary_by_name(name)
    if remote is None:
        return {"name": name, "create": True, "remote": None,
                "add": sorted(wanted.values(), key=lambda r: r["string_to_replace"]),
                "change": [], "remove": [], "unchanged": 0}
    have = {r["string_to_replace"]: r for r in client.dictionary_rules(
        remote["id"], remote["latest_version_id"])}
    add, change = [], []
    unchanged = 0
    for term, rule in sorted(wanted.items()):
        if term not in have:
            add.append(rule)
        elif _rule_target(have[term]) != _rule_target(rule) or \
                have[term].get("type") != rule["type"]:
            change.append(rule)
        else:
            unchanged += 1
    # Every remote rule the table does not carry goes on push: the table
    # is the one truth (author's ruling 2026-09-26 — "if we delete
    # something on our end in the pronunciation table, it's not making it
    # into the server"; the append-only rule of 2026-09-03 is reversed).
    remove = sorted(t for t in have if t not in wanted)
    return {"name": name, "create": False, "remote": remote, "add": add,
            "change": change, "remove": remove, "unchanged": unchanged}


def dictionary_apply(plan: dict, client: ElevenLabs) -> dict:
    """Mirror the table: adds through add-rules, a changed reading
    through remove-rules then add-rules (first match wins, so a
    duplicate appended behind the old rule would be shadowed), and every
    rule the table does not carry through remove-rules."""
    if plan["create"]:
        created = client.create_dictionary(plan["name"], plan["add"])
        return {"created": True, "id": created["id"],
                "version_id": created["latest_version_id"],
                "added": len(plan["add"]), "changed": 0}
    did = plan["remote"]["id"]
    version = plan["remote"]["latest_version_id"]
    gone = [r["string_to_replace"] for r in plan["change"]] + list(plan["remove"])
    if gone:
        version = client.remove_rules(did, gone)
    rules = plan["add"] + plan["change"]
    if rules:
        version = client.add_rules(did, rules)
    return {"created": False, "id": did, "version_id": version,
            "added": len(plan["add"]), "changed": len(plan["change"]),
            "removed": len(plan["remove"])}


def describe_plan(plan: dict) -> list[str]:
    lines = []
    if plan["create"]:
        lines.append(f"Dictionary '{plan['name']}' does not exist on "
                     f"ElevenLabs; it would be created with "
                     f"{len(plan['add'])} rule(s).")
    else:
        lines.append(f"Dictionary '{plan['name']}' ({plan['remote']['id']}): "
                     f"{plan['unchanged']} rule(s) already current.")
    for r in plan["add"]:
        lines.append(f"  add     {r['string_to_replace']} → {_rule_target(r)}")
    for r in plan["change"]:
        lines.append(f"  change  {r['string_to_replace']} → {_rule_target(r)}")
    for t in plan["remove"]:
        lines.append(f"  remove  {t}  (not in the table)")
    return lines


# --------------------------------------------------------------- say
#
# The fast pronunciation loop. An alias rule is text substitution, so a
# respelling can be heard WITHOUT a dictionary push: take a real sentence
# from the book that carries the term, in the voice that will say it,
# substitute the alias inline the way ElevenLabs would, render it. When a
# reading is settled, the row goes into pronunciations.md and the next
# push carries it. `--dictionary` renders the untouched sentence with the
# pushed dictionary attached instead, to prove the rule fires.

_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")


def sentence_around(text: str, term: str) -> str:
    """The sentence of `text` that carries `term` (whole word,
    case-insensitive), or the whole text when it is one sentence."""
    for sentence in _SENTENCE_END.split(text):
        if _term_pattern(term).search(sentence):
            return sentence.strip()
    return normalize(text)


def substitute_alias(text: str, term: str, alias: str) -> str:
    """What the alias rule does to the text: every whole-word occurrence
    of the term, in either case, becomes the alias."""
    return _term_pattern(term).sub(alias, text)


def say_contexts(manuscript: dict, term: str, file: str | None = None,
                 limit: int | None = 3, per_cast: bool = False,
                 built: dict | None = None) -> list[dict]:
    """Where the term is spoken, in reading order: the sentence, the
    cast, and the resolved voice of the section that carries it.
    `per_cast` keeps the first sentence for each cast key — every voice
    that has to say the word, once."""
    built = built or build(manuscript)
    stems = [c["stem"] for c in built["book"]["chapters"]]
    if built["book"]["aboutAuthor"]:
        stems.append(_stem(built["book"]["aboutAuthor"]))
    want = _stem(file) if file else None
    out: list[dict] = []
    seen_casts: set[str] = set()
    for stem in stems:
        if want and stem != want:
            continue
        chapter = built["chapters"][stem]
        for s in chapter["sections"]:
            if s["type"] != "speech" or not _term_pattern(term).search(s["text"]):
                continue
            if per_cast and s["cast"] in seen_casts:
                continue
            seen_casts.add(s["cast"])
            out.append({"file": chapter["file"], "stem": stem, "cast": s["cast"],
                        "voice": {"key": s["cast"], "voice_id": s["voiceId"],
                                  "voice_name": s["voiceName"], "model": s["model"],
                                  "stability": s["stability"],
                                  "similarity": s["similarity"], "speed": s["speed"]},
                        "sentence": sentence_around(s["text"], term),
                        "section_id": s["id"]})
            if limit is not None and len(out) >= limit:
                return out
    return out


def render_context(manuscript: dict, client: ElevenLabs, ctx: dict, term: str,
                   respelling: str, use_dictionary: bool = False,
                   quality: str = "mp3_44100_64", locators: list | None = None,
                   tag: str = "") -> dict:
    """One clip: the context's sentence in its voice, with `respelling`
    substituted inline as the alias rule would; with `use_dictionary`
    the pushed dictionary (`locators`) rides along so every other word
    reads by its rule (an empty respelling then hears the word by ITS
    pushed rule); with neither, the untouched sentence as the voice
    reads it by default, which is what a removed row would sound like.
    Returns {path, spoken, say, cast, chars}."""
    root = Path(manuscript["path"])
    voice = dict(ctx["voice"])
    # The box wins for the word in focus in every mode that has one; with
    # the dictionary attached, every OTHER word reads by its pushed rule
    # (author's ask 2026-09-26: hear the respelling "in company").
    if (respelling or "").strip():
        spoken = substitute_alias(ctx["sentence"], term, alias_for(respelling))
    else:
        spoken = ctx["sentence"]
    audio_bytes, _rid = client.tts(spoken, voice, quality,
                                   locators=locators if use_dictionary else None)
    clips_dir = root / AUDIO_DIR / AUDITIONS_DIR / "say"
    clips_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    kind = respelling or ("dict" if use_dictionary else "default")
    name = (f"{stamp}-{_slug(term)}-{_slug(tag or kind)}-"
            f"{_slug(voice.get('voice_name') or voice['voice_id'])}.mp3")
    path = clips_dir / name
    path.write_bytes(audio_bytes)
    return {"path": str(path), "name": name, "spoken": spoken,
            "say": respelling, "cast": voice["key"],
            "voice_name": voice.get("voice_name") or voice["voice_id"],
            "file": ctx["file"], "chars": len(spoken)}


def dictionary_locators(manuscript: dict, client: ElevenLabs) -> list[dict]:
    """The pushed dictionary's locator, by name, or a named refusal."""
    config = load_config(Path(manuscript["path"]))
    name = (config["tts"]["dictionary"] or "").strip()
    found = client.dictionary_by_name(name) if name else None
    if not found:
        raise AudioError(f"'{name or '(unnamed)'}' is not on ElevenLabs — "
                         "'audio dictionary push' first")
    return [{"pronunciation_dictionary_id": found["id"],
             "version_id": found["latest_version_id"]}]


def table_say(manuscript: dict, term: str) -> str:
    """The table's current reading for the term, or ''."""
    path = Path(manuscript["path"]) / pron.FILENAME
    rows, _w = pron.parse(path.read_text(encoding="utf-8") if path.exists() else "")
    return pron.lookup(rows).get(pron.key(term), {}).get("say", "")


def say(manuscript: dict, client: ElevenLabs, term: str,
        says: list[str] | None = None, cast_key: str | None = None,
        file: str | None = None, use_dictionary: bool = False,
        quality: str = "mp3_44100_64") -> dict:
    """Render the term's sentence once per respelling (default: the
    table's), in the voice of the first section that carries it (or
    `cast_key`'s). Returns the clips, their character cost, and the
    gallery page."""
    root = Path(manuscript["path"])
    config = load_config(root)
    contexts = say_contexts(manuscript, term, file, limit=1)
    if not contexts:
        raise AudioError(f"'{term}' is not spoken anywhere in the audiobook"
                         + (f" ({file})" if file else ""))
    ctx = contexts[0]
    voice = dict(ctx["voice"])
    if cast_key:
        cast, _w = load_cast(root, config)
        voice = _resolve_voice(cast, cast_key, {}, "audio say --cast")
    variants = [s.strip() for s in (says or []) if s.strip()] or \
        ([table_say(manuscript, term)] if table_say(manuscript, term) else [])
    if not variants and not use_dictionary:
        raise AudioError(f"'{term}' has no reading in {pron.FILENAME} — give one "
                         "with --say")
    locators = None
    if use_dictionary:
        locators = dictionary_locators(manuscript, client)
        variants = variants or [""]
    ctx = {**ctx, "voice": voice}
    entries: list[dict] = []
    clips: list[dict] = []
    chars = 0
    for i, respelling in enumerate(variants, 1):
        # `--dictionary` on the CLI proves the PUSHED rule fires: the
        # sentence goes untouched. (The workbench's dictionary mode is the
        # one that substitutes the box and attaches the dictionary.)
        clip = render_context(manuscript, client, ctx, term,
                              "" if use_dictionary else respelling,
                              use_dictionary=use_dictionary, quality=quality,
                              locators=locators, tag=str(i))
        chars += clip["chars"]
        label = ("via dictionary" if use_dictionary
                 else f"{respelling} → “{alias_for(respelling)}”")
        caption = (f"SAY {term}: {label} — {clip['voice_name']} "
                   f"({voice['key']}) in {ctx['file']} — «{clip['spoken'][:110]}"
                   f"{'…' if len(clip['spoken']) > 110 else ''}»")
        entries.append({"path": clip["path"], "caption": caption})
        clips.append({"say": respelling, "spoken": clip["spoken"],
                      "path": clip["path"], "cast": voice["key"],
                      "file": ctx["file"]})
    page = append_gallery(root, f"Auditions — {manuscript['name']}", entries)
    return {"term": term, "context": ctx, "clips": clips, "characters": chars,
            "page": str(page)}


def remove_say(manuscript: dict, term: str,
               client: "ElevenLabs | None" = None) -> dict:
    """Drop the term's row from pronunciations.md — the author's verdict
    that the voice's default reading is the better one — and, when a
    client is given and the book's dictionary carries a rule for the
    term, remove that ONE rule remotely too (an alias left behind would
    keep overriding the default the author just chose). Push's
    push now mirrors the table (2026-09-26), so this only hurries what the next push would do: it removes
    exactly the term the author named, nothing the table never had."""
    path = Path(manuscript["path"]) / pron.FILENAME
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    new_text, removed_row = pron.remove_row(text, term)
    if removed_row:
        path.write_text(new_text, encoding="utf-8")
    removed_rule, version_id = _remove_remote_rules(manuscript, term, client)
    return {"path": str(path), "term": term, "removed_row": removed_row,
            "removed_rule": removed_rule, "version_id": version_id}


def _remove_remote_rules(manuscript: dict, term: str,
                         client: "ElevenLabs | None") -> tuple[bool, str | None]:
    """Every remote rule for the term — the term as written and its
    lowercase variant, both of which a push writes — off the book's
    dictionary. (removed?, new version id)."""
    if client is None:
        return False, None
    config = load_config(Path(manuscript["path"]))
    name = (config["tts"]["dictionary"] or "").strip()
    remote = client.dictionary_by_name(name) if name else None
    if not remote:
        return False, None
    hits = [r["string_to_replace"] for r in client.dictionary_rules(
                remote["id"], remote["latest_version_id"])
            if pron.key(r["string_to_replace"]) == pron.key(term)]
    if not hits:
        return False, None
    return True, client.remove_rules(remote["id"], hits)


def clear_say(manuscript: dict, term: str,
              client: "ElevenLabs | None" = None) -> dict:
    """Keep the row, drop its reading: the word stays listed (starred, no
    reading) so it is not forgotten, and its rule leaves ElevenLabs now
    rather than at the next push. The author's ask, 2026-09-26: "there
    is a way to save the reading, but there is no way to delete it"."""
    path = Path(manuscript["path"]) / pron.FILENAME
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    rows, _w = pron.parse(text)
    existing = pron.lookup(rows).get(pron.key(term))
    if existing is None:
        raise AudioError(f"'{term}' is not a row in {pron.FILENAME}")
    settle_say(manuscript, existing["term"], "", note=existing.get("note", ""))
    removed_rule, version_id = _remove_remote_rules(manuscript, term, client)
    return {"path": str(path), "term": existing["term"], "cleared": True,
            "removed_rule": removed_rule, "version_id": version_id}


def settle_say(manuscript: dict, term: str, say_text: str,
               note: str | None = None) -> dict:
    """Write or replace the term's row in pronunciations.md — the
    author's own verdict, on their explicit word (`audio say --settle`).
    Adds the row when absent; otherwise rewrites only that row's line."""
    path = Path(manuscript["path"]) / pron.FILENAME
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    rows, _w = pron.parse(text)
    existing = pron.lookup(rows).get(pron.key(term))
    if existing is None:
        new = pron.add_row(text, {"term": term, "say": say_text,
                                  "note": note or ""})
        path.write_text(new, encoding="utf-8")
        return {"path": str(path), "added": True, "term": term, "say": say_text}
    row = {"term": existing["term"], "say": say_text,
           "note": note if note is not None else existing["note"]}
    line = pron.render_row(row)
    crlf = text.count("\r\n")
    newline = "\r\n" if crlf > text.count("\n") - crlf else "\n"
    lines = text.split(newline)
    for i, raw in enumerate(lines):
        cells = pron._cells(raw)
        if cells and not pron._is_delimiter(cells) and \
                pron.key(cells[0]) == pron.key(term):
            lines[i] = line
            break
    path.write_text(newline.join(lines), encoding="utf-8")
    return {"path": str(path), "added": False, "term": existing["term"],
            "say": say_text}


# --------------------------------------------------------- audiostation

APP_ENV = "AUDIOSTATION_APP"


def audiostation_root() -> Path:
    """The `audiostation/` folder beside the Python package."""
    return Path(__file__).resolve().parents[2] / "audiostation"


def audiostation_launch(manuscript: dict, env: dict | None = None,
                        root: Path | None = None) -> dict:
    """How to open audiostation on this manuscript's `_audio/` folder:
    `{argv, cwd, mode, dir}` for the caller to spawn. Pure: nothing is
    started here. Resolution order (design §11): `AUDIOSTATION_APP`
    (a .app bundle or a binary), the built bundles under the repo's
    `audiostation/src-tauri/target/`, a debug binary, and last the dev
    server (`npx tauri dev`), which compiles first."""
    env = os.environ if env is None else env
    root = root or audiostation_root()
    book_dir = audio_dir(manuscript)
    if not (book_dir / BOOK_FILENAME).exists():
        raise AudioError(f"{book_dir / BOOK_FILENAME} does not exist — run "
                         "'audio export' first")
    target = root / "src-tauri" / "target"
    candidates: list[Path] = []
    override = (env.get(APP_ENV) or "").strip()
    if override:
        candidates.append(Path(override).expanduser())

    def newest(paths: list[Path]) -> list[Path]:
        # Release and debug bundles both exist on a working machine; the
        # one to open is whichever was BUILT last, not the release one —
        # a stale release bundle silently hid a day of fixes (2026-09-06).
        def built(p: Path) -> float:
            binary = p / "Contents" / "MacOS" / "audiostation" if p.suffix == ".app" else p
            probe = binary if binary.exists() else p
            return probe.stat().st_mtime if probe.exists() else -1
        return sorted((p for p in paths if built(p) >= 0), key=built, reverse=True)

    candidates += newest([
        target / "release" / "bundle" / "macos" / "audiostation.app",
        target / "debug" / "bundle" / "macos" / "audiostation.app",
        Path("/Applications/audiostation.app"),
    ])
    candidates += newest([
        target / "release" / "audiostation",
        target / "debug" / "audiostation",
    ])
    for cand in candidates:
        if cand.suffix == ".app" and cand.is_dir():
            return {"mode": "bundle", "cwd": None, "dir": str(book_dir),
                    "argv": ["open", "-a", str(cand), "--args", str(book_dir)]}
        if cand.suffix != ".app" and cand.is_file():
            return {"mode": "binary", "cwd": None, "dir": str(book_dir),
                    "argv": [str(cand), str(book_dir)]}
    if (root / "package.json").exists():
        return {"mode": "dev", "cwd": str(root), "dir": str(book_dir),
                "argv": ["npx", "tauri", "dev", "--", str(book_dir)]}
    raise AudioError(
        f"no audiostation found: build it ('cd {root} && npm run build') or "
        f"set {APP_ENV} to the .app bundle")


# ------------------------------------------------------------ gallery

def gallery_tool() -> Path:
    return Path(__file__).resolve().parents[2] / "tools" / "gallery.py"


def _gallery_spec_path(root: Path) -> Path:
    return root / AUDIO_DIR / AUDITIONS_DIR / "gallery_spec.json"


def append_gallery(root: Path, title: str, entries: list[dict],
                   note: str | None = None) -> Path:
    """Append clips to the manuscript's one audition gallery and rebuild
    the page. Returns the page path."""
    spec_path = _gallery_spec_path(root)
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec = {"title": title, "note": note or "", "images": []}
    if spec_path.exists():
        try:
            spec = json.loads(spec_path.read_text(encoding="utf-8"))
        except ValueError:
            pass
    spec["title"] = title
    if note:
        spec["note"] = note
    spec["images"] = [i for i in spec.get("images", [])
                      if Path(i["path"]).exists()] + entries
    spec_path.write_text(json.dumps(spec, indent=2, ensure_ascii=False),
                         encoding="utf-8")
    page = spec_path.with_name("audition.html")
    subprocess.run([sys.executable, str(gallery_tool()), "--json",
                    str(spec_path), "--out", str(page)], check=True,
                   capture_output=True, text=True)
    return page


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "clip"


def voices_gallery(manuscript: dict, client: ElevenLabs,
                   search: str = "", library: bool = False,
                   limit: int = 12) -> dict:
    """Free discovery: the account's voices (or the shared library) with
    their preview clips, downloaded once into the gallery (design §11)."""
    root = Path(manuscript["path"])
    if library:
        if not search:
            raise AudioError("--library needs --search words")
        found = client.library_voices(search, page_size=limit)
    else:
        found = client.voices()
        if search:
            words = search.casefold().split()
            def hit(v: dict) -> bool:
                hay = " ".join([v["name"], v["category"],
                                *map(str, v["labels"].values())]).casefold()
                return all(w in hay for w in words)
            found = [v for v in found if hit(v)]
        found = found[:limit]
    entries: list[dict] = []
    clips_dir = root / AUDIO_DIR / AUDITIONS_DIR / "previews"
    clips_dir.mkdir(parents=True, exist_ok=True)
    for v in found:
        if not v["preview_url"]:
            continue
        target = clips_dir / f"{_slug(v['name'])}-{v['voice_id'][:8]}.mp3"
        if not target.exists():
            try:
                with urllib.request.urlopen(v["preview_url"], timeout=60) as r:
                    target.write_bytes(r.read())
            except (urllib.error.URLError, OSError):
                continue
        labels = ", ".join(f"{k}: {val}" for k, val in v["labels"].items()
                           if val)
        entries.append({"path": str(target),
                        "caption": f"PREVIEW {v['name']} — {v['voice_id']}"
                                   + (f" — {labels}" if labels else "")
                                   + (" — library; add it to the account "
                                      "before auditioning" if library else "")})
    page = append_gallery(root, f"Auditions — {manuscript['name']}", entries,
                          note="Free previews first; then audition the "
                               "shortlist on the book's own words.")
    return {"voices": found, "page": str(page), "clips": len(entries)}


def audition(manuscript: dict, client: ElevenLabs, candidates: list[str],
             text: str, overrides: dict | None = None,
             quality: str = "mp3_44100_64", label: str = "") -> dict:
    """Paid: render `text` in each candidate cast (a cast key, or a raw
    voice id) at the book's model, append to the gallery."""
    root = Path(manuscript["path"])
    config = load_config(root)
    cast, _w = load_cast(root, config)
    overrides = overrides or {}
    entries: list[dict] = []
    clips: list[dict] = []
    clips_dir = root / AUDIO_DIR / AUDITIONS_DIR / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    spoken = normalize(text)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for cand in candidates:
        if cand in cast:
            voice = dict(cast[cand])
            if not voice["voice_id"]:
                raise AudioError(f"cast '{cand}' has no Voice ID")
        else:
            voice = {"key": cand, "voice_id": cand, "voice_name": cand,
                     "model": config["tts"]["model"], "stability": 0.5,
                     "similarity": 0.75, "speed": 1.0}
        voice.update({k: v for k, v in overrides.items() if v is not None})
        audio, request_id = client.tts(spoken, voice, quality)
        name = f"{stamp}-{_slug(voice.get('voice_name') or cand)}-{_slug(label) if label else 'clip'}.mp3"
        path = clips_dir / name
        path.write_bytes(audio)
        caption = (f"{voice.get('voice_name') or cand} ({cand}) — "
                   f"{voice['model']} stab {voice['stability']:.2f} "
                   f"sim {voice['similarity']:.2f} speed {voice['speed']:.2f}"
                   + (f" — {label}" if label else "")
                   + f" — «{spoken[:80]}{'…' if len(spoken) > 80 else ''}»")
        entries.append({"path": str(path), "caption": caption})
        clips.append({"cast": cand, "voice_id": voice["voice_id"],
                      "path": str(path), "request_id": request_id,
                      "stability": voice["stability"],
                      "similarity": voice["similarity"],
                      "speed": voice["speed"], "model": voice["model"]})
    page = append_gallery(root, f"Auditions — {manuscript['name']}", entries)
    return {"clips": clips, "page": str(page),
            "characters": len(spoken) * len(candidates)}


def audition_text(manuscript: dict, file: str, paragraphs: str) -> str:
    """The audition's words from the book itself: `--file F --paragraphs
    3-5` are paragraph sections of that file's audio text, 1-based."""
    root = Path(manuscript["path"])
    files = read_manuscript_files(root)
    name = file if file in files else (file + ".md" if file + ".md" in files
                                       else None)
    if name is None:
        raise AudioError(f"{file} is not a manuscript file")
    source = audio_source(name, files[name])
    paras = [b for b in blocks_of(source)
             if b["kind"] in ("paragraph", "quote", "list")]
    m = re.fullmatch(r"(\d+)(?:-(\d+))?", (paragraphs or "").strip())
    if not m:
        raise AudioError("--paragraphs is N or N-M (1-based paragraph "
                         "sections of the file's audio text)")
    lo, hi = int(m.group(1)), int(m.group(2) or m.group(1))
    if not 1 <= lo <= hi <= len(paras):
        raise AudioError(f"{name} has {len(paras)} paragraph sections")
    config = _CONFIG_DEFAULTS
    return " ".join(_spoken(b, config)[0] for b in paras[lo - 1:hi])
