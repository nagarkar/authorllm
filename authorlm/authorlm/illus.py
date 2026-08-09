"""Illustration slots: the [Illustration: ...] tag grammar and the
scan-derived slot registry (design: docs/design-backlog.md).

A slot is declared in prose as a single-line paragraph:

    [Illustration: prompt text]
    [Illustration: prompt text | caption: reader-facing caption]

The tag IS the spec — plain prose that survives every push/pull and is
never consumed or replaced. Rendered candidates live in _illustrations/
(observation-invisible) named

    <slug>-<deschash8>-<stylehash4>-<NN>.png

where deschash covers the prompt part only (editing a caption never
stales an image) and NN numbers the candidates of a slot. The registry
is derived entirely by scanning text + directory — no DB state.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from .revisions import iter_manuscript_paths

ILLUS_DIR = "_illustrations"

_TAG = re.compile(r"\[Illustration:\s*(?P<body>.+?)\]\s*$", re.IGNORECASE)
_CAPTION = re.compile(r"\s*\|\s*caption:\s*(?P<caption>.+)\s*$", re.IGNORECASE)
_CANDIDATE = re.compile(
    r"^(?P<slug>.+)-(?P<desc>[0-9a-f]{8})-(?P<style>[0-9a-f]{4})"
    r"-(?P<n>\d{2})\.(?P<ext>png|jpe?g)$")
# Google Docs markdown export renders pasted images as reference-style
# links (`![][image1]`) plus, sometimes, a base64 definition line — both
# useless outside the Doc and silently corrupting once pulled.
_DANGLING_REF = re.compile(r"!\[\]\[image\d+\]")
_DANGLING_DEF = re.compile(r"^\[image\d+\]:\s.*$\n?", re.MULTILINE)


def parse_tag(line: str) -> dict | None:
    """The tag grammar. A slot is a full-line tag (trailing whitespace
    tolerated); returns {prompt, caption} or None."""
    m = _TAG.match(line.strip())
    if not m or not line.strip().startswith("["):
        return None
    body = m.group("body")
    caption = None
    cm = _CAPTION.search(body)
    if cm:
        caption = cm.group("caption").strip()
        body = body[: cm.start()]
    prompt = " ".join(body.split())
    return {"prompt": prompt, "caption": caption} if prompt else None


def desc_hash(prompt: str) -> str:
    """Slot identity: hash of the whitespace-collapsed prompt (the
    caption is deliberately outside it)."""
    canonical = " ".join(prompt.split())
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:8]


def slug(prompt: str) -> str:
    words = re.sub(r"[^a-z0-9 ]", "", prompt.lower()).split()
    return "-".join(words[:4]) or "illustration"


def scan_text(text: str) -> list[dict]:
    """Every slot declared in a text, in order: {prompt, caption, line}
    (1-based line numbers)."""
    slots = []
    for lineno, line in enumerate(text.split("\n"), start=1):
        tag = parse_tag(line)
        if tag:
            slots.append({**tag, "line": lineno,
                          "desc_hash": desc_hash(tag["prompt"])})
    return slots


def candidate_files(root: Path) -> list[dict]:
    """Parsed candidate PNGs on disk: {name, slug, desc, style, n}."""
    directory = Path(root) / ILLUS_DIR
    if not directory.is_dir():
        return []
    out = []
    for path in sorted(directory.iterdir()):
        m = _CANDIDATE.match(path.name)
        if m:
            out.append({"name": path.name, **m.groupdict()})
    return out


def slot_report(root: Path) -> dict:
    """The scan-derived registry delta a collect surfaces: slots with no
    rendered candidate ("new illustrations found"), and candidate files
    whose prompt no longer exists anywhere (edited or deleted tags)."""
    root = Path(root)
    slots = []
    for rel, path in iter_manuscript_paths(root).items():
        for slot in scan_text(path.read_text(encoding="utf-8")):
            slots.append({"file": rel, **slot})
    rendered = {c["desc"] for c in candidate_files(root)}
    declared = {s["desc_hash"] for s in slots}
    unrendered = [
        {"file": s["file"], "line": s["line"], "prompt": s["prompt"]}
        for s in slots if s["desc_hash"] not in rendered
    ]
    orphaned = [c["name"] for c in candidate_files(root)
                if c["desc"] not in declared]
    return {"unrendered": unrendered, "orphaned": orphaned}


def strip_dangling(text: str) -> tuple[str, list[str]]:
    """Remove Doc-export image artifacts; returns (clean text, refs
    removed). Idempotent, and safe on text that has none."""
    refs = _DANGLING_REF.findall(text) + [
        m.split(":", 1)[0].strip() for m in _DANGLING_DEF.findall(text)]
    text = _DANGLING_DEF.sub("", text)
    text = _DANGLING_REF.sub("", text)
    return text, refs


# ------------------------------------------------- figure law + rendering

def illustration_law(db, manuscript_id: str, file: str) -> str:
    """The effective illustration-aspect style law for a file, as the
    exact text prepended to every render prompt. The stylehash hashes
    THIS — 'hash what the model sees' — so a reworded ratified rule
    changes it and a candidate policy cannot. Only the 'illustration'
    aspect: 'figure' is figurative-language PROSE law and must never
    reach an image prompt (it-3e79bce24f73)."""
    from .styles import effective_style

    elements = [e for e in effective_style(db, manuscript_id, file)
                if e["aspect"] == "illustration"]
    if not elements:
        return ""
    lines = ["ILLUSTRATION STYLE (ratified by the author — follow strictly):"]
    for element in elements:
        line = f"- {element['statement']}"
        if element.get("notes"):
            line += f" ({element['notes']})"
        lines.append(line)
    return "\n".join(lines)


def style_hash(law: str) -> str:
    """4-hex fingerprint of the effective figure law; '0000' when no
    figure rules are ratified yet."""
    if not law:
        return "0000"
    return hashlib.sha256(law.encode("utf-8")).hexdigest()[:4]


def slot_candidates(root: Path, deschash: str) -> list[dict]:
    """This slot's candidates in render order (NN ascends across style
    generations — a slot's numbering never resets)."""
    return sorted((c for c in candidate_files(root) if c["desc"] == deschash),
                  key=lambda c: int(c["n"]))


def _itxt_chunk(keyword: str, text: str) -> bytes:
    import struct
    import zlib

    data = (keyword.encode("latin-1") + b"\x00\x00\x00\x00\x00"
            + text.encode("utf-8"))
    return (struct.pack(">I", len(data)) + b"iTXt" + data
            + struct.pack(">I", zlib.crc32(b"iTXt" + data) & 0xFFFFFFFF))


_PNG_SIG = b"\x89PNG\r\n\x1a\n"
_JPEG_SIG = b"\xff\xd8"


def image_ext(img: bytes) -> str:
    """Honest extension from magic bytes — the model decides the format
    (flash returns PNG, pro returns JPEG), the filename must not lie."""
    if img.startswith(_PNG_SIG):
        return "png"
    if img.startswith(_JPEG_SIG):
        return "jpg"
    return "png"  # unknown — store as-is under the default name


def image_with_metadata(img: bytes, fields: dict[str, str]) -> bytes:
    """The prompt (and its context) travels inside the image — iTXt
    chunks in a PNG, COM comment segments in a JPEG — a human-facing
    copy so files stay self-describing away from the manuscript; never
    the staleness mechanism."""
    if img.startswith(_PNG_SIG):
        ihdr_end = 8 + 8 + 13 + 4  # signature + IHDR header/data/crc
        chunks = b"".join(_itxt_chunk(k, v) for k, v in fields.items() if v)
        return img[:ihdr_end] + chunks + img[ihdr_end:]
    if img.startswith(_JPEG_SIG):
        import struct

        segments = b""
        for key, value in fields.items():
            if not value:
                continue
            body = f"{key}: {value}".encode("utf-8")[:65531]
            segments += b"\xff\xfe" + struct.pack(">H", len(body) + 2) + body
        return img[:2] + segments + img[2:]
    return img  # unknown format — store untouched


def _find_tag_line(lines: list[str], deschash: str) -> int | None:
    for i, line in enumerate(lines):
        tag = parse_tag(line)
        if tag and desc_hash(tag["prompt"]) == deschash:
            return i
    return None


def embed_target(text: str, deschash: str) -> str | None:
    """The candidate filename the slot's embed line points at (the
    line directly under the tag), or None if not embedded."""
    lines = text.split("\n")
    i = _find_tag_line(lines, deschash)
    if i is None or i + 1 >= len(lines):
        return None
    from .revisions import EMBED_LINE

    if EMBED_LINE.match(lines[i + 1]):
        return lines[i + 1].rsplit("/", 1)[-1].rstrip(") \t")
    return None


def set_embed(path: Path, deschash: str, candidate_name: str) -> bool:
    """Point the slot's embed line at a candidate — insert directly
    under the tag, or rewrite the existing embed. The embed is derived
    machinery: observation, push, and the beat loop never see it."""
    from .revisions import EMBED_LINE

    text = path.read_text(encoding="utf-8")
    lines = text.split("\n")
    i = _find_tag_line(lines, deschash)
    if i is None:
        return False
    embed = f"![]({ILLUS_DIR}/{candidate_name})"
    if i + 1 < len(lines) and EMBED_LINE.match(lines[i + 1]):
        lines[i + 1] = embed
    else:
        lines.insert(i + 1, embed)
    path.write_text("\n".join(lines), encoding="utf-8")
    return True


def find_slot(root: Path, fragment: str) -> list[dict]:
    """Slots whose prompt contains the fragment (case-insensitive),
    each as {file, line, prompt, caption, desc_hash}."""
    fragment = fragment.lower()
    matches = []
    for rel, path in iter_manuscript_paths(Path(root)).items():
        for slot in scan_text(path.read_text(encoding="utf-8")):
            if fragment in slot["prompt"].lower():
                matches.append({"file": rel, **slot})
    return matches


def render_slot(db, manuscript: dict, slot: dict, config: dict,
                from_n: int | None = None, count: int = 1,
                generator=None) -> dict:
    """Render `count` new candidates for a slot. `from_n` passes an
    existing candidate as the input image (continuity across a style
    change). A slot with no embed yet gets one pointing at the newest
    render; an existing embed is NEVER moved — that is `pick`'s job."""
    from datetime import datetime, timezone

    from .llm import DEFAULT_IMAGE_MODEL, generate_image

    generator = generator or (lambda prompt, input_png:
                              generate_image(config, prompt, input_png))
    root = Path(manuscript["path"])
    deschash = slot["desc_hash"]
    assembled = effective_prompt(db, manuscript, slot)
    shash = assembled["style_hash"]
    prompt = assembled["composed"]

    input_png = None
    if from_n is not None:
        source = next((c for c in slot_candidates(root, deschash)
                       if int(c["n"]) == from_n), None)
        if source is None:
            raise LookupError(f"slot has no candidate {from_n:02d}")
        input_png = (root / ILLUS_DIR / source["name"]).read_bytes()

    existing = slot_candidates(root, deschash)
    next_n = max((int(c["n"]) for c in existing), default=0) + 1
    directory = root / ILLUS_DIR
    directory.mkdir(exist_ok=True)
    model = (config.get("llm", {}) or {}).get("image_model",
                                              DEFAULT_IMAGE_MODEL)
    written = []
    for offset in range(count):
        img = generator(prompt, input_png)
        name = (f"{slug(slot['prompt'])}-{deschash}-{shash}"
                f"-{next_n + offset:02d}.{image_ext(img)}")
        payload = image_with_metadata(img, {
            "authorlm:prompt": slot["prompt"],
            "authorlm:caption": slot.get("caption") or "",
            "authorlm:style": assembled["law"],
            "authorlm:model": model,
            "authorlm:date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            **({"authorlm:from": f"{from_n:02d}"} if from_n else {}),
        })
        (directory / name).write_bytes(payload)
        written.append(name)
    embedded = embed_target((root / slot["file"]).read_text(encoding="utf-8"),
                            deschash)
    if embedded is None:
        set_embed(root / slot["file"], deschash, written[-1])
    return {"written": written, "embedded": embedded or written[-1],
            "style_hash": shash, "had_embed": embedded is not None}


def slot_status(db, manuscript: dict) -> list[dict]:
    """Every slot with its lifecycle state, for `illus list`:
    unrendered | rendered | stale-style (embed predates the current
    figure law) — plus the embedded candidate and candidate count."""
    root = Path(manuscript["path"])
    out = []
    for rel, path in iter_manuscript_paths(root).items():
        text = path.read_text(encoding="utf-8")
        current = style_hash(illustration_law(db, manuscript["id"], rel))
        for slot in scan_text(text):
            cands = slot_candidates(root, slot["desc_hash"])
            embedded = embed_target(text, slot["desc_hash"])
            state = "unrendered"
            if cands:
                state = "rendered"
                target = next((c for c in cands if c["name"] == embedded),
                              None)
                if target and target["style"] != current:
                    state = "stale-style"
                elif not target and all(c["style"] != current for c in cands):
                    state = "stale-style"
            out.append({"file": rel, "line": slot["line"],
                        "prompt": slot["prompt"],
                        "caption": slot.get("caption"),
                        "state": state, "candidates": len(cands),
                        "embedded": embedded,
                        "style_current": current})
    return out


def prune(manuscript: dict) -> list[str]:
    """Delete candidates that are not embedded anywhere plus orphans of
    vanished prompts. Explicit act only — render never deletes."""
    root = Path(manuscript["path"])
    keep: set[str] = set()
    declared: set[str] = set()
    for rel, path in iter_manuscript_paths(root).items():
        text = path.read_text(encoding="utf-8")
        for slot in scan_text(text):
            declared.add(slot["desc_hash"])
            target = embed_target(text, slot["desc_hash"])
            if target:
                keep.add(target)
    removed = []
    for candidate in candidate_files(root):
        if candidate["name"] in keep and candidate["desc"] in declared:
            continue
        (root / ILLUS_DIR / candidate["name"]).unlink()
        removed.append(candidate["name"])
    return removed


def capture_embeds(text: str) -> dict[str, str]:
    """The pick state a text holds: {desc_hash: embedded candidate name}.
    Captured before a pull overwrites the file, so picks survive Doc
    round trips even though the Doc never carries embed lines."""
    from .revisions import EMBED_LINE

    lines = text.split("\n")
    out: dict[str, str] = {}
    for i, line in enumerate(lines[:-1]):
        tag = parse_tag(line)
        if tag and EMBED_LINE.match(lines[i + 1]):
            out[desc_hash(tag["prompt"])] = (
                lines[i + 1].rsplit("/", 1)[-1].rstrip(") \t"))
    return out


def reembed(text: str, root: Path, prior: dict[str, str] | None = None) -> str:
    """Deterministically re-insert embed lines under each tag of an
    embed-free text: the prior pick wins while its file exists; a slot
    never picked gets its newest candidate; an unrendered slot stays
    bare. Safe on text that already carries embeds (never doubles)."""
    from .revisions import EMBED_LINE

    prior = prior or {}
    root = Path(root)
    lines = text.split("\n")
    out: list[str] = []
    for i, line in enumerate(lines):
        out.append(line)
        tag = parse_tag(line)
        if not tag:
            continue
        if i + 1 < len(lines) and EMBED_LINE.match(lines[i + 1]):
            continue  # already embedded
        h = desc_hash(tag["prompt"])
        name = prior.get(h)
        if name and not (root / ILLUS_DIR / name).exists():
            name = None
        if name is None:
            cands = slot_candidates(root, h)
            name = cands[-1]["name"] if cands else None
        if name:
            out.append(f"![]({ILLUS_DIR}/{name})")
    return "\n".join(out)


def effective_prompt(db, manuscript: dict, slot: dict) -> dict:
    """The exact prompt a render of this slot would send, assembled
    deterministically — THE debugging instrument for "why does this
    image look like that". render_slot composes through this same
    function, so the two can never diverge."""
    law = illustration_law(db, manuscript["id"], slot["file"])
    composed = (law + "\n\n" + slot["prompt"]) if law else slot["prompt"]
    return {
        "file": slot["file"],
        "line": slot.get("line"),
        "prompt": slot["prompt"],
        "caption": slot.get("caption"),
        "desc_hash": slot["desc_hash"],
        "style_hash": style_hash(law),
        "law": law,
        "composed": composed,
    }
