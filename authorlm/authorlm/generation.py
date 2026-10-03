"""The takes: generate, retake, stitch and status, on the folder
`audio export` writes (docs/audiobook-review-design.md §4–§5).

This module is the Python twin of audiostation's `book.rs`, `commands.rs`
and `stitch.rs`. Both programs read `chapters/<stem>.json` and write
`state/<stem>.json` in the same schema (`src-tauri/src/types.rs`), the
same `audio/<id>.<quality>.mp3` take files and the same
`audio/<stem>.<quality>.mp3` stitched file, so a take made by either is
done for both. "Done" is: the id has a state entry whose file exists at
the book's quality. Same id, same audio; nothing here ever re-renders a
done id except `retake`, on the author's word.

Guard against double renders with two writers on one state (§5): the
chapter's state is re-read from disk immediately before every render,
and again immediately before every write.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import audio
from .audio import (AUDIO_DIR, AudioError, BOOK_FILENAME, CHAPTERS_DIR,
                    STATE_DIR)

TAKES_DIR = "audio"
SILENCE_DIR = "silence"
PREVIEW_DIR = "preview"
SCHEMA = 1
# ElevenLabs honours neighbour request ids for two hours (pipeline §8).
STITCH_WINDOW_SECS = 7200
# The encoder recipe every stitch uses — byte-identical to book.rs so
# both programs read the same file as fresh or stale.
STITCH_RECIPE = "v2:44100Hz:mono:192k-cbr:loudnorm(I=-20,TP=-3,LRA=11)"
TWO_PASS_QUALITIES = ("mp3_44100_128", "mp3_44100_192")
Progress = Callable[[str], None]


# ----------------------------------------------------------- folder

def load_book(root: Path) -> dict:
    path = root / BOOK_FILENAME
    if not path.exists():
        raise AudioError(f"{path} does not exist — run 'audio export' first")
    book = json.loads(path.read_text(encoding="utf-8"))
    if book.get("schema") != SCHEMA:
        raise AudioError(f"{BOOK_FILENAME} is schema {book.get('schema')}; "
                         f"this AuthorLM reads schema {SCHEMA}")
    return book


def chapter_files(book: dict) -> list[tuple[str, str]]:
    """Every chapter file the book references, in reading order:
    opening credits, the chapters, about the author, closing credits —
    `(stem, relative path)`."""
    out: list[tuple[str, str]] = []
    if book.get("openingCredits"):
        out.append((Path(book["openingCredits"]).stem, book["openingCredits"]))
    for c in book.get("chapters", []):
        out.append((c["stem"], c["file"]))
    if book.get("aboutAuthor"):
        out.append((Path(book["aboutAuthor"]).stem, book["aboutAuthor"]))
    if book.get("closingCredits"):
        out.append((Path(book["closingCredits"]).stem, book["closingCredits"]))
    return out


def load_chapter(root: Path, book: dict, stem: str) -> dict:
    for s, rel in chapter_files(book):
        if s == stem:
            return json.loads((root / rel).read_text(encoding="utf-8"))
    known = ", ".join(s for s, _ in chapter_files(book))
    raise AudioError(f"no chapter '{stem}' in {BOOK_FILENAME} (chapters: {known})")


def speech_of(chapter: dict) -> list[dict]:
    return [s for s in chapter.get("sections", []) if s.get("type") == "speech"]


def ordinal_of(chapter: dict, section_id: str) -> int | None:
    """1-based position among the chapter's speech sections (headings
    count) — the number the page shows on each card."""
    for i, s in enumerate(speech_of(chapter), start=1):
        if s["id"] == section_id:
            return i
    return None


_SPAN = re.compile(r"^(\d+)(?:-(\d+))?$")


def resolve_paragraphs(chapter: dict, spec: str) -> list[str]:
    """`12,14-16` → the ids of those paragraphs, in chapter order. A
    32-hex id, or a hex prefix of eight or more characters, is accepted
    too. Refuses by name anything that resolves to nothing."""
    speech = speech_of(chapter)
    wanted: set[str] = set()
    for part in (p.strip() for p in spec.split(",")):
        if not part:
            continue
        # An id prefix first: eight hex digits can be all digits, and no
        # chapter has ten million paragraphs.
        if re.fullmatch(r"[0-9a-f]{8,32}", part):
            hits = [s["id"] for s in speech if s["id"].startswith(part)]
            if len(hits) != 1:
                raise AudioError(f"id {part}: {'no' if not hits else 'several'} "
                                 "paragraphs match")
            wanted.add(hits[0])
            continue
        m = _SPAN.match(part)
        if m:
            lo = int(m.group(1))
            hi = int(m.group(2) or lo)
            if lo < 1 or hi > len(speech) or lo > hi:
                raise AudioError(f"paragraph {part}: the chapter has "
                                 f"{len(speech)} paragraphs (1-{len(speech)})")
            for n in range(lo, hi + 1):
                wanted.add(speech[n - 1]["id"])
            continue
        raise AudioError(f"'{part}' is neither a paragraph number, a range "
                         "N-M, nor an id")
    return [s["id"] for s in speech if s["id"] in wanted]


# ------------------------------------------------------------ state

def empty_state() -> dict:
    return {"schema": SCHEMA, "sections": {}, "stitched": {}, "stitchKeys": {},
            "durationSecs": None, "loudnessLufs": None, "truePeakDbtp": None}


def load_state(root: Path, stem: str) -> dict:
    """The chapter's state as it is on disk right now. Unknown keys are
    kept so a save never drops what the other writer recorded.

    A missing file is an empty state. A file that is there but does not
    parse to an object raises AudioError: falling back to an empty state
    would let the next save overwrite every earlier take's record."""
    path = root / STATE_DIR / f"{stem}.json"
    state = empty_state()
    if path.exists():
        try:
            on_disk = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as e:
            raise AudioError(f"{path} is unreadable ({e}) — repair or remove "
                             "it; nothing was written") from None
        if not isinstance(on_disk, dict):
            raise AudioError(f"{path} is unreadable (holds a "
                             f"{type(on_disk).__name__}, not an object) — "
                             "repair or remove it; nothing was written")
        state.update(on_disk)
    state.setdefault("sections", {})
    state.setdefault("stitched", {})
    state.setdefault("stitchKeys", {})
    return state


def save_state(root: Path, stem: str, state: dict) -> Path:
    """Write-then-rename, the way audiostation writes, so a reader never
    sees a half-written file."""
    state_dir = root / STATE_DIR
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / f"{stem}.json"
    # One temp name per writer, so two writers never share (and tear) it.
    tmp = state_dir / f".{stem}.{os.getpid()}-{threading.get_ident()}.json.tmp"
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)
    return path


def take_of(root: Path, state: dict, section_id: str, quality: str) -> str | None:
    """The take's path relative to the folder when the id is done at
    `quality`, else None."""
    entry = state["sections"].get(section_id) or {}
    rel = (entry.get("audioFiles") or {}).get(quality)
    if rel and (root / rel).exists():
        return rel
    return None


def has_any_take(root: Path, state: dict, section_id: str) -> bool:
    entry = state["sections"].get(section_id) or {}
    return any((root / rel).exists()
               for rel in (entry.get("audioFiles") or {}).values())


def made_from(speech: dict) -> dict:
    return {"text": speech["text"], "cast": speech.get("cast", ""),
            "voiceId": speech["voiceId"], "model": speech["model"],
            "stability": float(speech["stability"]),
            "similarity": float(speech["similarity"]),
            "speed": float(speech.get("speed", 1.0)),
            "pronunciations": [{"term": p["term"], "say": p["say"]}
                               for p in speech.get("pronunciations", [])]}


def now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------- what changed since the take

_FOLD = {
    "‘": "'", "’": "'", "ʼ": "'",
    "“": '"', "”": '"',
    "−": "-", "­": "", "​": "", "‌": "", "‍": "",
    "﻿": "", " ": " ", " ": " ", " ": " ", "⁠": " ",
}
for _c in range(0x2010, 0x2016):
    _FOLD[chr(_c)] = "-"


def fold(text: str) -> str:
    """Display-only folding for pairing a paragraph to its last take
    (book.rs::fold): quotes and dashes to ASCII, odd spaces to space,
    zero-width dropped, whitespace collapsed, lowercase. Never feeds an
    id."""
    mapped = "".join(_FOLD.get(c, c) for c in text)
    return " ".join(mapped.split()).lower()


def pronunciation_diff(old: list[dict], new: list[dict]) -> list[str]:
    before = {p["term"]: p["say"] for p in old}
    after = {p["term"]: p["say"] for p in new}
    out: list[str] = []
    for p in new:
        was = before.get(p["term"])
        if was is None:
            out.append(f"added {p['term']} → {p['say']}")
        elif was != p["say"]:
            out.append(f"{p['term']}: {was} → {p['say']}")
    for p in old:
        if p["term"] not in after:
            out.append(f"removed {p['term']}")
    return out


def param_diff(old: dict, new: dict) -> list[str]:
    out: list[str] = []
    if old.get("voiceId") != new.get("voiceId") or old.get("cast") != new.get("cast"):
        out.append("voice")
    if old.get("model") != new.get("model"):
        out.append("model")
    for key in ("stability", "similarity", "speed"):
        if abs(float(old.get(key, 1.0)) - float(new.get(key, 1.0))) > 1e-9:
            out.append(key)
    out += pronunciation_diff(old.get("pronunciations", []),
                              new.get("pronunciations", []))
    return out or ["parameters"]


def flags_from_state(root: Path, chapter: dict, state: dict) -> dict[str, dict]:
    """For every speech section without a take: the most recent take
    whose recorded text is this text, and what differs from it —
    book.rs::flags_from_state. `{id: {kind: "params", detail: [...],
    since: <generatedAt>}}`."""
    flags: dict[str, dict] = {}
    for s in speech_of(chapter):
        if has_any_take(root, state, s["id"]):
            continue
        f = fold(s["text"])
        best = None
        for sid, entry in state["sections"].items():
            m = entry.get("madeFrom")
            if not m or sid == s["id"] or fold(m.get("text", "")) != f:
                continue
            if best is None or (entry.get("generatedAt") or "") > (best[0].get("generatedAt") or ""):
                best = (entry, m)
        if best:
            flags[s["id"]] = {"kind": "params",
                              "detail": param_diff(best[1], made_from(s)),
                              "since": best[0].get("generatedAt")}
    return flags


# -------------------------------------------------------- continuity

def neighbours(chapter: dict, section_id: str) -> tuple[dict | None, dict | None]:
    """The nearest same-voice speech sections before and after, silences
    and other voices skipped (pipeline §8)."""
    sections = chapter.get("sections", [])
    idx = next((i for i, s in enumerate(sections) if s.get("id") == section_id), None)
    if idx is None or sections[idx].get("type") != "speech":
        return None, None
    voice = sections[idx]["voiceId"]
    prev = next((s for s in reversed(sections[:idx])
                 if s.get("type") == "speech" and s["voiceId"] == voice), None)
    nxt = next((s for s in sections[idx + 1:]
                if s.get("type") == "speech" and s["voiceId"] == voice), None)
    return prev, nxt


def recent_request_id(entry: dict | None, now: datetime) -> str | None:
    if not entry or not entry.get("requestId") or not entry.get("generatedAt"):
        return None
    try:
        at = datetime.strptime(entry["generatedAt"], "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        try:
            at = datetime.fromisoformat(entry["generatedAt"].replace("Z", "+00:00"))
            at = at.astimezone(timezone.utc).replace(tzinfo=None)
        except ValueError:
            return None
    if (now.replace(tzinfo=None) - at).total_seconds() < STITCH_WINDOW_SECS:
        return entry["requestId"]
    return None


def build_request(book: dict, chapter: dict, state: dict, section_id: str,
                  now: datetime | None = None) -> dict:
    """Everything one ElevenLabs call needs (commands.rs::build_request)."""
    speech = next((s for s in speech_of(chapter) if s["id"] == section_id), None)
    if speech is None:
        raise AudioError(f"section {section_id} is not speech in {chapter.get('stem')}")
    now = now or datetime.now(timezone.utc)
    prev, nxt = neighbours(chapter, section_id)
    stitchable = not speech["model"].startswith("eleven_v3")
    prev_ids = [] if not stitchable or not prev else [
        r for r in [recent_request_id(state["sections"].get(prev["id"]), now)] if r]
    next_ids = [] if not stitchable or not nxt else [
        r for r in [recent_request_id(state["sections"].get(nxt["id"]), now)] if r]
    dictionary = book.get("pronunciationDictionary") or {}
    locators = ([{"pronunciation_dictionary_id": dictionary["id"],
                  "version_id": dictionary["versionId"]}]
                if dictionary.get("id") and dictionary.get("versionId") else [])
    return {"speech": speech,
            "voice": {"voice_id": speech["voiceId"], "model": speech["model"],
                      "stability": float(speech["stability"]),
                      "similarity": float(speech["similarity"]),
                      "speed": float(speech.get("speed", 1.0))},
            "previous_text": prev["text"] if prev else None,
            "next_text": nxt["text"] if nxt else None,
            "previous_request_ids": prev_ids, "next_request_ids": next_ids,
            "locators": locators}


# --------------------------------------------------------- generate

def plan(root: Path, book: dict, chapter: dict, spec: str | None,
         remaining: bool, retake: bool) -> dict:
    """Which ids a generate or retake would touch, and what it would
    cost. `{ids, skipped, characters, quality}` — `skipped` are the ids
    the caller named that are already done (generate never re-renders
    them)."""
    quality = book["quality"]
    state = load_state(root, chapter["stem"])
    speech = {s["id"]: s for s in speech_of(chapter)}
    if spec:
        named = resolve_paragraphs(chapter, spec)
    elif remaining:
        named = [sid for sid in speech if not take_of(root, state, sid, quality)]
    else:
        raise AudioError("name paragraphs (-p 12,14-16) or pass --remaining")
    if retake:
        ids, skipped = named, []
    else:
        ids = [sid for sid in named if not take_of(root, state, sid, quality)]
        skipped = [sid for sid in named if sid not in ids]
    return {"ids": ids, "skipped": skipped, "quality": quality,
            "characters": sum(len(speech[sid]["text"]) for sid in ids)}


def render_one(root: Path, book: dict, chapter: dict, section_id: str,
               client, quality: str) -> dict:
    """One take: re-read the state (guard 2), build the request, call,
    write the file, re-read the state again and record the take. Returns
    `{id, rel, requestId, characters}`."""
    stem = chapter["stem"]
    state = load_state(root, stem)
    req = build_request(book, chapter, state, section_id)
    speech = req["speech"]
    audio_bytes, request_id = client.tts(
        speech["text"], req["voice"], quality,
        previous_text=req["previous_text"], next_text=req["next_text"],
        locators=req["locators"] or None,
        previous_request_ids=req["previous_request_ids"] or None,
        next_request_ids=req["next_request_ids"] or None)
    takes = root / TAKES_DIR
    takes.mkdir(parents=True, exist_ok=True)
    rel = f"{TAKES_DIR}/{section_id}.{quality}.mp3"
    (root / rel).write_bytes(audio_bytes)
    state = load_state(root, stem)
    entry = state["sections"].setdefault(section_id, {})
    entry.setdefault("audioFiles", {})[quality] = rel
    entry["requestId"] = request_id or None
    entry["generatedAt"] = now_stamp()
    entry["madeFrom"] = made_from(speech)
    # The stitched file no longer reflects this chapter.
    state["stitched"].pop(quality, None)
    state["stitchKeys"].pop(quality, None)
    save_state(root, stem, state)
    return {"id": section_id, "rel": rel, "requestId": request_id,
            "characters": len(speech["text"]),
            "ordinal": ordinal_of(chapter, section_id)}


def generate(manuscript: dict, stem: str, spec: str | None = None,
             remaining: bool = False, retake: bool = False,
             dry_run: bool = False, client=None,
             progress: Progress | None = None) -> dict:
    """Render the named paragraphs (or every one without a take) at the
    book's quality. `generate` never touches a done id; `retake` does
    exactly that, and only that. Returns `{rendered, skipped, characters,
    quality, stem, account}` where `account` is the subscription after
    the calls (None when nothing was spent or the call failed)."""
    root = audio.audio_dir(manuscript)
    book = load_book(root)
    chapter = load_chapter(root, book, stem)
    p = plan(root, book, chapter, spec, remaining, retake)
    result = {"stem": stem, "quality": p["quality"], "characters": p["characters"],
              "planned": [{"id": sid, "ordinal": ordinal_of(chapter, sid),
                           "characters": len(next(s["text"] for s in speech_of(chapter)
                                                  if s["id"] == sid))}
                          for sid in p["ids"]],
              "skipped": [{"id": sid, "ordinal": ordinal_of(chapter, sid)}
                          for sid in p["skipped"]],
              "rendered": [], "account": None, "dry_run": dry_run}
    if dry_run or not p["ids"]:
        return result
    client = client or audio.ElevenLabs(audio.api_key())
    for n, sid in enumerate(p["ids"], start=1):
        # Guard 2, per render: the state may have moved under us.
        if not retake and take_of(root, load_state(root, stem), sid, p["quality"]):
            result["skipped"].append({"id": sid, "ordinal": ordinal_of(chapter, sid)})
            continue
        if progress:
            progress(f"{stem} ¶ {ordinal_of(chapter, sid)} ({n}/{len(p['ids'])})")
        result["rendered"].append(render_one(root, book, chapter, sid, client,
                                             p["quality"]))
    result["characters"] = sum(r["characters"] for r in result["rendered"])
    try:
        sub = client.subscription()
        result["account"] = {"used": sub["used"], "limit": sub["limit"],
                             "remaining": max(sub["limit"] - sub["used"], 0)}
    except AudioError:
        result["account"] = None
    return result


# ----------------------------------------------------------- stitch

def find_tool(name: str) -> str | None:
    for candidate in (name, f"/usr/local/bin/{name}", f"/opt/homebrew/bin/{name}"):
        try:
            subprocess.run([candidate, "-version"], capture_output=True, check=False)
            return candidate
        except OSError:
            continue
    return None


def stitch_key(chapter: dict) -> str:
    """FNV-1a over the recipe and the section sequence — book.rs::stitch_key,
    bit for bit, so both programs agree on whether a stitched file is
    stale."""
    h = 0xcbf29ce484222325
    def feed(data: bytes) -> None:
        nonlocal h
        for b in data:
            h ^= b
            h = (h * 0x100000001b3) & 0xFFFFFFFFFFFFFFFF
    feed(STITCH_RECIPE.encode())
    for s in chapter.get("sections", []):
        feed(b"\x1f")
        if s.get("type") == "speech":
            feed(s["id"].encode())
        else:
            feed(f"silence:{s['durationMs']}".encode())
    return f"{h:016x}"


def _run(argv: list[str], what: str) -> subprocess.CompletedProcess:
    proc = subprocess.run(argv, capture_output=True, text=True, check=False,
                          stdin=subprocess.DEVNULL)
    if proc.returncode != 0:
        raise AudioError(f"{what} exited {proc.returncode}: "
                         f"{proc.stderr.strip()[-600:]}")
    return proc


def generate_silence(ffmpeg: str, secs: float, out_path: Path) -> None:
    _run([ffmpeg, "-y", "-f", "lavfi", "-i",
          "anullsrc=channel_layout=mono:sample_rate=44100",
          "-t", f"{secs}", "-c:a", "libmp3lame", "-q:a", "9", str(out_path)],
         "ffmpeg silence")


def prepare_inputs(root: Path, chapter: dict, state: dict, quality: str,
                   ffmpeg: str) -> list[str]:
    """Every segment as an absolute path, silence clips made on demand
    and shared by the book. Fails on the first paragraph without a take."""
    silence_dir = root / SILENCE_DIR
    silence_dir.mkdir(parents=True, exist_ok=True)
    inputs: list[str] = []
    for s in chapter.get("sections", []):
        if s.get("type") == "speech":
            rel = take_of(root, state, s["id"], quality)
            if not rel:
                raise AudioError(f"¶ {ordinal_of(chapter, s['id'])} "
                                 f"({s['id'][:8]}…) has no take at {quality} yet")
            inputs.append(str(root / rel))
        else:
            path = silence_dir / f"silence_{s['durationMs']}ms.mp3"
            if not path.exists():
                generate_silence(ffmpeg, s["durationMs"] / 1000.0, path)
            inputs.append(str(path))
    if not inputs:
        raise AudioError("no sections to stitch")
    return inputs


def _concat_list(work_dir: Path, name: str, inputs: list[str]) -> Path:
    path = work_dir / name
    path.write_text("".join(f"file '{p.replace(chr(39), chr(39) + chr(92) + chr(39) + chr(39))}'\n"
                            for p in inputs), encoding="utf-8")
    return path


_LOUDNORM = "loudnorm=I=-20:TP=-3:LRA=11"


def measure_loudness(ffmpeg: str, work_dir: Path, inputs: list[str]) -> dict:
    """Pass 1 of two-pass EBU R128: the measurements pass 2 normalises by."""
    lst = _concat_list(work_dir, "stitch_measure_list.txt", inputs)
    try:
        proc = subprocess.run([ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
                               "-af", _LOUDNORM + ":print_format=json", "-f", "null", "-"],
                              capture_output=True, text=True, check=False,
                              stdin=subprocess.DEVNULL)
    finally:
        lst.unlink(missing_ok=True)
    if proc.returncode != 0:
        raise AudioError(f"ffmpeg loudnorm pass 1 exited {proc.returncode}: "
                         f"{proc.stderr.strip()[-600:]}")
    start = proc.stderr.rfind("{")
    end = proc.stderr.find("}", start)
    if start < 0 or end < 0:
        raise AudioError("loudnorm JSON not found in ffmpeg output")
    data = json.loads(proc.stderr[start:end + 1])
    try:
        return {k: float(data[k]) for k in
                ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")}
    except (KeyError, ValueError) as err:
        raise AudioError(f"loudnorm JSON lacks {err}") from None


def concat_inputs(ffmpeg: str, root: Path, stem: str, quality: str,
                  inputs: list[str], measurement: dict | None) -> str:
    """Pass 2: concat, loudnorm, encode to `audio/<stem>.<quality>.mp3`
    (ACX: 44.1 kHz, constant 192 kbps, mono). Returns the relative path."""
    out_dir = root / TAKES_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    rel = f"{TAKES_DIR}/{sanitise_filename(stem)}.{quality}.mp3"
    lst = _concat_list(root, "stitch_list.txt", inputs)
    if measurement:
        m = measurement
        loudnorm = (f"{_LOUDNORM}:measured_I={m['input_i']:.2f}:measured_TP={m['input_tp']:.2f}:"
                    f"measured_LRA={m['input_lra']:.2f}:measured_thresh={m['input_thresh']:.2f}:"
                    f"offset={m['target_offset']:.2f}:linear=true")
    else:
        loudnorm = _LOUDNORM
    try:
        _run([ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
              # loudnorm resamples internally; pin the rate after it.
              "-af", f"{loudnorm},aresample=44100", "-ar", "44100", "-ac", "1",
              "-c:a", "libmp3lame", "-b:a", "192k", str(root / rel)], "ffmpeg stitch")
    finally:
        lst.unlink(missing_ok=True)
    return rel


def measure_duration(path: Path) -> float | None:
    ffprobe = find_tool("ffprobe")
    if not ffprobe:
        return None
    proc = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration",
                           "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                          capture_output=True, text=True, check=False)
    try:
        return float(proc.stdout.strip())
    except ValueError:
        return None


def sanitise_filename(s: str) -> str:
    out = "".join(c if (c.isalnum() or c in "-_.") else "_" for c in s).strip("_")
    return out or "untitled"


def stitch(manuscript: dict, stem: str, progress: Progress | None = None) -> dict:
    """One chapter into one file, and the state records it with its key
    (commands.rs::stitch_internal). Returns `{rel, durationSecs, key}`."""
    root = audio.audio_dir(manuscript)
    book = load_book(root)
    chapter = load_chapter(root, book, stem)
    quality = book["quality"]
    ffmpeg = find_tool("ffmpeg")
    if not ffmpeg:
        raise AudioError("ffmpeg not found on PATH — install ffmpeg to stitch")
    state = load_state(root, stem)
    inputs = prepare_inputs(root, chapter, state, quality, ffmpeg)
    two_pass = quality in TWO_PASS_QUALITIES
    if two_pass:
        if progress:
            progress(f"{stem}: measuring loudness of {len(inputs)} segments (pass 1/2)")
        m = measure_loudness(ffmpeg, root, inputs)
        # Silence measures -inf, which pass 2 refuses; a chapter that quiet
        # gets the single-pass filter instead of an error.
        if not all(-99.0 <= m[k] <= 0.0 for k in ("input_i", "input_tp", "input_thresh")):
            m = None
        if progress:
            progress(f"{stem}: encoding with measured normalisation (pass 2/2)")
        rel = concat_inputs(ffmpeg, root, stem, quality, inputs, m)
    else:
        if progress:
            progress(f"{stem}: mixing {len(inputs)} segments")
        rel = concat_inputs(ffmpeg, root, stem, quality, inputs, None)
    duration = measure_duration(root / rel)
    key = stitch_key(chapter)
    state = load_state(root, stem)
    state["stitched"][quality] = rel
    state["stitchKeys"][quality] = key
    state["durationSecs"] = duration
    if two_pass:
        state["loudnessLufs"] = -20.0
        state["truePeakDbtp"] = -3.0
    save_state(root, stem, state)
    return {"stem": stem, "rel": rel, "durationSecs": duration, "key": key,
            "quality": quality, "segments": len(inputs)}


# ---------------------------------------------------------- previews
#
# The free listen (review design §6). A preview is the export's text —
# with every applicable respelling substituted inline, since `say`
# cannot read an ElevenLabs dictionary — read by a macOS voice into
# `_audio/preview/<id>.mp3`. Keyed by id like a take, but nothing is
# ever kept: a changed paragraph gets a new preview and the old file is
# swept, because nothing cost anything.

def preview_text(speech: dict) -> str:
    text = speech["text"]
    for rule in speech.get("pronunciations", []):
        if rule.get("say"):
            text = audio.substitute_alias(text, rule["term"],
                                          audio.alias_for(rule["say"]))
    return text


def preview_path(root: Path, section_id: str) -> Path:
    return root / PREVIEW_DIR / f"{section_id}.mp3"


def render_preview(root: Path, speech: dict, voice: str, rate: int,
                   say: str = "say", ffmpeg: str | None = None) -> Path:
    """`say` to AIFF, ffmpeg to 48 kbps mono mp3. The text goes through a
    file, never an argument, so length and quoting are no concern."""
    ffmpeg = ffmpeg or find_tool("ffmpeg")
    if not ffmpeg:
        raise AudioError("ffmpeg not found on PATH — install ffmpeg to preview")
    out = preview_path(root, speech["id"])
    out.parent.mkdir(parents=True, exist_ok=True)
    wpm = max(80, round(rate * float(speech.get("speed", 1.0))))
    with tempfile.TemporaryDirectory(prefix="authorlm-preview-") as tmp:
        text_file = Path(tmp) / "text.txt"
        aiff = Path(tmp) / "take.aiff"
        text_file.write_text(preview_text(speech) + "\n", encoding="utf-8")
        _run([say, "-v", voice, "-r", str(wpm), "-f", str(text_file),
              "-o", str(aiff)], f"say ({voice})")
        # The part file is unique to this render: two renderers on the
        # same id (a CLI run beside the server's job) must not race on
        # one temporary name. The last replace wins, with the same bytes.
        part = out.parent / f"{speech['id']}.{os.getpid()}-{threading.get_ident()}.part.mp3"
        _run([ffmpeg, "-y", "-loglevel", "error", "-i", str(aiff), "-ac", "1",
              "-b:a", "48k", str(part)], "ffmpeg preview")
        os.replace(part, out)
    return out


def sweep_previews(root: Path, book: dict) -> int:
    """Delete previews whose id no chapter references. Returns the count."""
    folder = root / PREVIEW_DIR
    if not folder.exists():
        return 0
    live: set[str] = set()
    for stem, _rel in chapter_files(book):
        live.update(s["id"] for s in speech_of(load_chapter(root, book, stem)))
    gone = 0
    for path in folder.glob("*.mp3"):
        if ".part" in path.name:
            continue
        if path.stem not in live:
            path.unlink()
            gone += 1
    return gone


def preview(manuscript: dict, stem: str | None = None, spec: str | None = None,
            force: bool = False, workers: int = 4,
            renderer: Callable[[Path, dict, str, int], Path] | None = None,
            progress: Progress | None = None) -> dict:
    """Render the previews that are missing — one chapter, the named
    paragraphs of it, or (stem None) the whole book — on `workers`
    threads, then sweep the previews of ids that are gone. `force`
    re-renders even where a preview exists. Returns `{rendered, kept,
    swept, voice, rate, chapters}`."""
    from concurrent.futures import ThreadPoolExecutor

    root = audio.audio_dir(manuscript)
    book = load_book(root)
    config = audio.load_config(Path(manuscript["path"]))
    voice = config["preview"]["voice"]
    rate = int(config["preview"]["rate"])
    renderer = renderer or (lambda r, sp, v, rt: render_preview(r, sp, v, rt))
    stems = [s for s, _ in chapter_files(book)] if stem is None else [stem]
    todo: list[tuple[str, dict]] = []
    kept = 0
    for st in stems:
        chapter = load_chapter(root, book, st)
        wanted = set(resolve_paragraphs(chapter, spec)) if spec else None
        for sp in speech_of(chapter):
            if wanted is not None and sp["id"] not in wanted:
                continue
            if not force and preview_path(root, sp["id"]).exists():
                kept += 1
                continue
            todo.append((st, sp))
    done = 0
    rendered: list[dict] = []
    errors: list[str] = []

    def one(item: tuple[str, dict]) -> dict:
        st, sp = item
        renderer(root, sp, voice, rate)
        return {"stem": st, "id": sp["id"], "characters": len(sp["text"])}

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for st_sp, fut in [(i, pool.submit(one, i)) for i in todo]:
            try:
                rendered.append(fut.result())
            except AudioError as err:
                errors.append(f"{st_sp[0]} {st_sp[1]['id'][:8]}: {err}")
            done += 1
            if progress and (done % 10 == 0 or done == len(todo)):
                progress(f"{done}/{len(todo)} previews")
    swept = sweep_previews(root, book)
    return {"rendered": rendered, "kept": kept, "swept": swept, "voice": voice,
            "rate": rate, "chapters": stems, "errors": errors}


# --------------------------------------------------------- staleness

def export_staleness(manuscript: dict) -> dict:
    """Sources newer than the export: a disk check only, cheap enough for
    every status poll (review design, built 2026-09-26). `changed` lists
    the manuscript-relative files saved after the newest exported file;
    `stale` is whether there are any. An edit that changes nothing the
    export reads (a comment, a re-save) shows up too — the price of not
    rebuilding on every poll; the next export touches the book file even
    when it rewrites nothing, and the banner clears."""
    from .revisions import iter_manuscript_paths

    root = Path(manuscript["path"])
    out_dir = root / AUDIO_DIR
    exported = [out_dir / BOOK_FILENAME, *(out_dir / CHAPTERS_DIR).glob("*.json")]
    exported = [p for p in exported if p.exists()]
    if not exported:
        return {"stale": True, "changed": [], "exported": None,
                "reason": "never exported"}
    # The book file is touched by every export; the newest exported file
    # is therefore the export's time.
    export_time = max(p.stat().st_mtime for p in exported)
    sources: dict[str, Path] = dict(iter_manuscript_paths(root))
    for name in (audio.CONFIG_FILENAME, audio.CAST_FILENAME):
        sources[f"{AUDIO_DIR}/{name}"] = out_dir / name
    changed = sorted(rel for rel, path in sources.items()
                     if path.exists() and path.stat().st_mtime > export_time + 1)
    return {"stale": bool(changed), "changed": changed,
            "exported": datetime.fromtimestamp(export_time, timezone.utc)
                        .strftime("%Y-%m-%dT%H:%M:%SZ"),
            "reason": "sources changed since the export" if changed else ""}


# ----------------------------------------------------------- status

def first_words(text: str, n: int = 8) -> str:
    words = text.split()
    return " ".join(words[:n]) + ("…" if len(words) > n else "")


def chapter_status(root: Path, book: dict, chapter: dict) -> dict:
    quality = book["quality"]
    state = load_state(root, chapter["stem"])
    speech = speech_of(chapter)
    flags = flags_from_state(root, chapter, state)
    done = [s for s in speech if take_of(root, state, s["id"], quality)]
    remaining = [s for s in speech if not take_of(root, state, s["id"], quality)]
    stitched = state["stitched"].get(quality)
    stitched_exists = bool(stitched and (root / stitched).exists())
    previewed = [s for s in speech if preview_path(root, s["id"]).exists()]
    return {
        "stem": chapter["stem"], "title": chapter.get("title", chapter["stem"]),
        "quality": quality, "total": len(speech), "generated": len(done),
        "previewed": len(previewed),
        "remaining": len(remaining),
        "remainingCharacters": sum(len(s["text"]) for s in remaining),
        "moved": [{"id": sid, "ordinal": ordinal_of(chapter, sid),
                   "detail": f["detail"], "since": f["since"]}
                  for sid, f in flags.items()],
        "stitched": stitched if stitched_exists else None,
        "stitchStale": bool(stitched_exists
                            and state["stitchKeys"].get(quality) != stitch_key(chapter)),
        "durationSecs": state.get("durationSecs") if stitched_exists else None,
        "sections": [{"id": s["id"], "ordinal": i, "kind": s.get("kind", "paragraph"),
                      "cast": s.get("cast", ""), "characters": len(s["text"]),
                      "firstWords": first_words(s["text"]),
                      "take": take_of(root, state, s["id"], quality),
                      "preview": (f"{PREVIEW_DIR}/{s['id']}.mp3"
                                  if preview_path(root, s["id"]).exists() else None),
                      "generatedAt": (state["sections"].get(s["id"]) or {}).get("generatedAt"),
                      "moved": flags.get(s["id"])}
                     for i, s in enumerate(speech, start=1)],
    }


def status(manuscript: dict, stem: str | None = None,
           find: str | None = None) -> dict:
    """The board (§7): every chapter, or one, with its counts, what moved
    since its takes, and its stitch. `find` lists the paragraphs whose
    text contains the words, for the session to resolve a chat request
    into a paragraph."""
    root = audio.audio_dir(manuscript)
    book = load_book(root)
    stems = [s for s, _ in chapter_files(book)]
    if stem and stem not in stems:
        raise AudioError(f"no chapter '{stem}' (chapters: {', '.join(stems)})")
    chapters = [load_chapter(root, book, s) for s in stems if not stem or s == stem]
    out = {"book": {"title": book.get("title", ""), "quality": book["quality"],
                    "exportedAt": book.get("generatedAt")},
           "export": export_staleness(manuscript),
           "chapters": [chapter_status(root, book, c) for c in chapters],
           "find": [], "findFor": find}
    if find:
        needle = fold(find)
        for c in chapters:
            for i, s in enumerate(speech_of(c), start=1):
                if needle in fold(s["text"]):
                    out["find"].append({"stem": c["stem"], "ordinal": i, "id": s["id"],
                                        "firstWords": first_words(s["text"], 10),
                                        "take": bool(take_of(root, load_state(root, c["stem"]),
                                                             s["id"], book["quality"]))})
    return out


def describe_status(report: dict, verbose: bool = False) -> list[str]:
    """One line per fact, for a chat context. A find prints only its
    matches — the board is noise around a lookup."""
    lines: list[str] = []
    exported = (report["book"].get("exportedAt") or "")[:16].replace("T", " ")
    if report.get("findFor") is not None:
        for f in report["find"]:
            lines.append(f"{f['stem']} ¶ {f['ordinal']} {'●' if f['take'] else '○'} "
                         f"{f['id'][:8]}… «{f['firstWords']}»")
        return lines
    for c in report["chapters"]:
        head = (f"{c['stem']}  {c['total']} paragraphs · {c['previewed']} previewed"
                f" · {c['generated']} generated at {c['quality']}")
        if c["remaining"]:
            head += f" · {c['remaining']} remaining ({c['remainingCharacters']:,} chars)"
        lines.append(head)
        if c["moved"]:
            parts = [f"¶ {m['ordinal']} {', '.join(m['detail'])}" for m in c["moved"]]
            since = next((m["since"] for m in c["moved"] if m["since"]), None)
            when = f" since {since[:16].replace('T', ' ')}" if since else ""
            lines.append(f"  {len(c['moved'])} moved{when}: " + "; ".join(parts))
        if c["stitched"]:
            dur = c.get("durationSecs")
            mins = f" · {int(dur // 60)}:{int(dur % 60):02d}" if dur else ""
            lines.append(f"  stitched{' (stale)' if c['stitchStale'] else ''}: "
                         f"{c['stitched']}{mins}")
        elif c["generated"] and not c["remaining"]:
            lines.append("  every paragraph has a take · not stitched")
        if verbose:
            for s in c["sections"]:
                mark = ("●" if s["take"] else "◐" if s["preview"] else "○")
                lines.append(f"  {mark} ¶ {s['ordinal']:>3} {s['cast']:<10} "
                             f"{s['characters']:>5}  {s['firstWords']}")
    if exported:
        lines.append(f"exported {exported}")
    ex = report.get("export") or {}
    if ex.get("stale"):
        lines.append("STALE — changed since the export: " + ", ".join(ex["changed"])
                     + " · 'audio export' (or Refresh on the page)")
    return lines
