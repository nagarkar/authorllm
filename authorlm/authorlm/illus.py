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
import sys
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


_REF = re.compile(r"\s*⇢\s*(?P<ref>[\w.-]+\.md)\s*$")

# Externalized descriptions: `[Illustration: excerpt… ⇢ slug.md]` keeps
# a short reader-facing hint inline while the full prompt lives in
# _illustrations/prompts/<slug>.md (the CANONICAL text — the excerpt is
# machine-maintained and edits to it do not hold). Delete the `⇢ ref`
# to make the inline text canonical again.
PROMPTS_SUBDIR = "prompts"


def parse_tag(line: str) -> dict | None:
    """The tag grammar. A slot is a full-line tag (trailing whitespace
    tolerated); returns {prompt, caption, ref} or None. `prompt` is the
    inline text — for a ref tag that is only the excerpt; resolution to
    the full description happens in scan_text via the prompts map."""
    m = _TAG.match(line.strip())
    if not m or not line.strip().startswith("["):
        return None
    body = m.group("body")
    caption = None
    cm = _CAPTION.search(body)
    if cm:
        caption = cm.group("caption").strip()
        body = body[: cm.start()]
    ref = None
    rm = _REF.search(body)
    if rm:
        ref = rm.group("ref")
        body = body[: rm.start()]
    prompt = " ".join(body.split())
    if not prompt and not ref:
        return None
    return {"prompt": prompt, "caption": caption, "ref": ref}


# Preview embeds in prompt files: `![](../<candidate>)` lines at the end
# of a prompts/<slug>.md file are DERIVED machinery — machine-maintained
# at collect to mirror the owning tag's pinned embed, so a local editor
# (Obsidian) shows the image beside its description. They are invisible
# to the desc_hash, the composed render prompt, and the Doc bridge —
# exactly the essay embed-line convention, relocated ( `../` because the
# prompt file lives inside _illustrations/prompts/).
PROMPT_EMBED_LINE = re.compile(r"^!\[[^\]]*\]\(\.\./[^)]+\)[ \t]*$")


def split_prompt_embeds(text: str) -> tuple[str, list[str]]:
    """(canonical text, preview embed lines). The canonical half ends
    with exactly one trailing newline — the stable base every hash and
    Doc comparison uses."""
    kept = [ln for ln in text.split("\n")
            if not PROMPT_EMBED_LINE.match(ln)]
    embeds = [ln.strip() for ln in text.split("\n")
              if PROMPT_EMBED_LINE.match(ln)]
    body = "\n".join(kept).rstrip("\n")
    return (body + "\n" if body else ""), embeds


def join_prompt_embeds(body: str, embeds: list[str]) -> str:
    """Canonical text + preview embeds, in the canonical layout."""
    if not embeds:
        return body
    return body.rstrip("\n") + "\n\n" + "\n".join(embeds) + "\n"


def load_prompts(root: Path) -> dict[str, str]:
    """{<slug>.md: canonical text} from _illustrations/prompts/ —
    preview embed lines stripped."""
    directory = Path(root) / ILLUS_DIR / PROMPTS_SUBDIR
    if not directory.is_dir():
        return {}
    return {p.name: split_prompt_embeds(
                p.read_text(encoding="utf-8"))[0]
            for p in sorted(directory.glob("*.md"))}


def _read_prompt_files_raw(root: Path) -> dict[str, str]:
    """{<slug>.md: raw file text, preview embeds included} — the exact
    bytes on disk, unlike `load_prompts` (which strips embeds for
    identity comparisons). Used by `snapshot_prompts` so a restore
    reproduces the file exactly."""
    directory = Path(root) / ILLUS_DIR / PROMPTS_SUBDIR
    if not directory.is_dir():
        return {}
    out: dict[str, str] = {}
    for path in sorted(directory.glob("*.md")):
        try:
            out[path.name] = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            out[path.name] = path.read_text(encoding="utf-8", errors="replace")
    return out


def snapshot_prompts(db, manuscript: dict, source: str) -> dict | None:
    """Recovery point for `_illustrations/prompts/*.md` (X7-3).

    These files hold the CANONICAL author text for externalized
    illustration descriptions, but `revisions.iter_manuscript_paths`
    deliberately skips '_'-prefixed directories (so drafts/exports stay
    observation-invisible), which means `api.collect()` /
    `collect_revision` never captures them — nothing else does either.
    Yet `gdocs.pull_doc` and `gdocs.reconcile` overwrite them from the
    Doc. This is the pre-write recovery point: call it immediately
    before either of those can touch a prompt file, never as part of
    ordinary observation (that would flood this table on every editing
    session instead of only the moments a Doc write could destroy
    something).

    Checksum-deduped like `revisions.collect_revision`: a snapshot
    identical to the last one is skipped, not stored again, so repeated
    pulls with no local prompt edits between them don't grow the table.
    Returns the new row, or None when nothing changed (including 'no
    prompts directory yet' — never raises, must not block a pull)."""
    import hashlib as _hashlib
    import json as _json

    from .db import ko_fields

    files = _read_prompt_files_raw(manuscript["path"])
    digest = _hashlib.sha256(
        _json.dumps(files, sort_keys=True).encode("utf-8")).hexdigest()
    last = db.one(
        "SELECT * FROM illustration_prompt_versions WHERE manuscript_id = ? "
        "ORDER BY version_no DESC LIMIT 1",
        (manuscript["id"],),
    )
    if last and last["checksum"] == digest:
        return None
    row = ko_fields("ipv")
    row.update(
        manuscript_id=manuscript["id"],
        version_no=(last["version_no"] + 1) if last else 1,
        checksum=digest,
        files=_json.dumps(files),
        source=source,
    )
    db.insert("illustration_prompt_versions", row)
    return row


def get_prompt_version(db, manuscript: dict,
                       version_no: int | None = None) -> "sqlite3.Row":
    """The stored `snapshot_prompts` row for `version_no` (default: the
    latest). Single source of truth for version lookup — shared by
    `restore_prompts`, `preview_restore`, and the CLI's `illus versions`.
    Raises `LookupError`, never guesses, when there is no such snapshot."""
    if version_no is None:
        version = db.one(
            "SELECT * FROM illustration_prompt_versions WHERE "
            "manuscript_id = ? ORDER BY version_no DESC LIMIT 1",
            (manuscript["id"],),
        )
    else:
        version = db.one(
            "SELECT * FROM illustration_prompt_versions WHERE "
            "manuscript_id = ? AND version_no = ?",
            (manuscript["id"], version_no),
        )
    if not version:
        which = "latest" if version_no is None else f"v{version_no}"
        raise LookupError(f"no illustration-prompt snapshot ({which}) "
                          f"to restore from")
    return version


def preview_restore(db, manuscript: dict,
                    version_no: int | None = None) -> dict:
    """What `restore_prompts(db, manuscript, version_no)` WOULD do,
    without writing anything — the CLI prints this before the write so
    the author sees which files, from which snapshot, before their
    current content is overwritten (X7-3: a silent restore that clobbers
    newer text would be the same class of defect this fixes)."""
    from .db import loads as _loads

    version = get_prompt_version(db, manuscript, version_no)
    files = sorted(_loads(version["files"], {}))
    return {"version_no": version["version_no"],
           "created_at": version["created_at"], "source": version["source"],
           "files": files}


def restore_prompts(db, manuscript: dict, version_no: int | None = None) -> dict:
    """Write a `snapshot_prompts` row back to `_illustrations/prompts/` —
    the recovery half of X7-3. `version_no=None` restores the latest
    snapshot. A prompt file present in the snapshot but currently
    missing or edited on disk is overwritten; a prompt file that exists
    now but wasn't in the snapshot is left alone (restore recovers what
    the snapshot held, it does not prune newer prompt files — additive,
    not a wholesale revert). Never called automatically — this is manual
    recovery only, reached via `authorlm illus restore` or by hand."""
    from .db import loads as _loads

    version = get_prompt_version(db, manuscript, version_no)
    files = _loads(version["files"], {})
    directory = Path(manuscript["path"]) / ILLUS_DIR / PROMPTS_SUBDIR
    directory.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (directory / name).write_text(text, encoding="utf-8")
    return {"restored": sorted(files), "version_no": version["version_no"]}


def list_prompt_versions(db, manuscript: dict) -> list[dict]:
    """Every `snapshot_prompts` row for this manuscript, oldest first —
    what the author needs to see to pick a `--version N` meaningfully.
    {version_no, created_at, source, file_count, files}."""
    from .db import loads as _loads

    rows = db.all(
        "SELECT * FROM illustration_prompt_versions WHERE "
        "manuscript_id = ? ORDER BY version_no",
        (manuscript["id"],),
    )
    out = []
    for row in rows:
        files = sorted(_loads(row["files"], {}))
        out.append({"version_no": row["version_no"],
                    "created_at": row["created_at"], "source": row["source"],
                    "file_count": len(files), "files": files})
    return out


def desc_hash(prompt: str) -> str:
    """Slot identity: hash of the whitespace-collapsed prompt (the
    caption is deliberately outside it)."""
    canonical = " ".join(prompt.split())
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:8]


def slug(prompt: str) -> str:
    words = re.sub(r"[^a-z0-9 ]", "", prompt.lower()).split()
    return "-".join(words[:4]) or "illustration"


def scan_text(text: str, prompts: dict[str, str] | None = None) -> list[dict]:
    """Every slot declared in a text, in order: {prompt, excerpt,
    caption, ref, line} (1-based). For ref tags, `prompt` and the
    desc_hash come from the CANONICAL external file (via `prompts`,
    from load_prompts); a missing file leaves the excerpt standing in,
    flagged with missing_ref for the collect report."""
    prompts = prompts or {}
    slots = []
    for lineno, line in enumerate(text.split("\n"), start=1):
        tag = parse_tag(line)
        if not tag:
            continue
        slot = {**tag, "excerpt": tag["prompt"], "line": lineno}
        if tag["ref"]:
            full = prompts.get(tag["ref"])
            if full is None:
                slot["missing_ref"] = True
            else:
                slot["prompt"] = " ".join(full.split())
        slot["desc_hash"] = desc_hash(slot["prompt"])
        slots.append(slot)
    return slots


def _excerpt_of(full: str, words: int = 8) -> str:
    """The machine-maintained inline excerpt: first words of the
    canonical text, ellipsized when trimmed."""
    tokens = " ".join(full.split()).split()
    head = " ".join(tokens[:words])
    return head + ("…" if len(tokens) > words else "")


def _tag_line(excerpt: str, ref: str | None, caption: str | None) -> str:
    body = excerpt + (f" ⇢ {ref}" if ref else "")
    if caption:
        body += f" | caption: {caption}"
    return f"[Illustration: {body}]"


def externalize(root: Path, slot: dict) -> dict:
    """Move a slot's inline description to its canonical file in
    _illustrations/prompts/, leaving `excerpt… ⇢ slug.md` in the tag.
    The collapsed text is unchanged, so the desc_hash — and every
    rendered candidate — survives the move."""
    root = Path(root)
    if slot.get("ref"):
        raise ValueError("this slot is already externalized "
                         f"(⇢ {slot['ref']})")
    directory = root / ILLUS_DIR / PROMPTS_SUBDIR
    directory.mkdir(parents=True, exist_ok=True)
    base = slug(slot["prompt"])
    name, n = f"{base}.md", 2
    while (directory / name).exists():
        name, n = f"{base}-{n}.md", n + 1
    (directory / name).write_text(slot["prompt"] + "\n", encoding="utf-8")
    path = root / slot["file"]
    lines = path.read_text(encoding="utf-8").split("\n")
    lines[slot["line"] - 1] = _tag_line(
        _excerpt_of(slot["prompt"]), name, slot.get("caption"))
    path.write_text("\n".join(lines), encoding="utf-8")
    return {"file": slot["file"], "ref": name,
            "desc_hash": slot["desc_hash"]}


def _read_manuscript_text(path: Path, rel: str) -> str:
    """Read a manuscript file, tolerating invalid UTF-8 the same way
    `revisions.read_manuscript_files` does: warn on stderr and re-read
    with `errors="replace"` (invalid bytes become U+FFFD) rather than
    raising. Every one of this module's manuscript reads sits on the
    `api.collect()` recovery path, so a decode failure must not take
    down the very path that exists to recover from it."""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        print(f"warning: {rel} is not valid UTF-8; reading it with "
              f"replacement characters in place of the invalid bytes",
              file=sys.stderr)
        return path.read_text(encoding="utf-8", errors="replace")


def maintain_excerpts(root: Path) -> list[dict]:
    """Regenerate every ref tag's excerpt from its canonical file —
    drift is impossible by construction. Returns the rewrites, each
    flagged `discarded_edit` when the standing excerpt matched neither
    the current canonical text (i.e. someone edited the excerpt, whose
    edits do not hold). Missing files are reported, never guessed.

    Also syncs each prompt file's preview embeds (`![](../<candidate>)`)
    to mirror the owning tags' pinned embeds — same derived-machinery
    contract as the excerpt: machine-maintained, edits do not hold."""
    from .revisions import EMBED_LINE

    root = Path(root)
    prompts = load_prompts(root)
    report = []
    picked: dict[str, list[str]] = {}
    for rel, path in iter_manuscript_paths(root).items():
        lines = _read_manuscript_text(path, rel).split("\n")
        changed = False
        for i, line in enumerate(lines):
            tag = parse_tag(line)
            if not tag or not tag["ref"]:
                continue
            if i + 1 < len(lines) and EMBED_LINE.match(lines[i + 1]):
                name = lines[i + 1].rsplit("/", 1)[-1].rstrip(") \t")
                picks = picked.setdefault(tag["ref"], [])
                if name not in picks:
                    picks.append(name)
            full = prompts.get(tag["ref"])
            if full is None:
                report.append({"file": rel, "ref": tag["ref"],
                               "missing": True})
                continue
            expected = _excerpt_of(full)
            if tag["prompt"] != expected:
                report.append({"file": rel, "ref": tag["ref"],
                               "discarded_edit": bool(tag["prompt"])})
                lines[i] = _tag_line(expected, tag["ref"], tag["caption"])
                changed = True
        if changed:
            path.write_text("\n".join(lines), encoding="utf-8")
    directory = root / ILLUS_DIR / PROMPTS_SUBDIR
    if directory.is_dir():
        for path in sorted(directory.glob("*.md")):
            raw = path.read_text(encoding="utf-8")
            body, embeds = split_prompt_embeds(raw)
            want = [f"![](../{n})" for n in picked.get(path.name, [])]
            if embeds != want:
                path.write_text(join_prompt_embeds(body, want),
                                encoding="utf-8")
                report.append({"ref": path.name,
                               "embed_synced": bool(want)})
    return report


def externalize_offers(root: Path, long_words: int = 50,
                       stable_days: int = 7) -> list[dict]:
    """Inline slots that have earned the offer: descriptions past the
    length threshold (they distract in the Doc and on screen readers),
    or rendered slots whose description has sat unchanged since a
    candidate at least stable_days old. Offers only — the author
    externalizes explicitly (illus externalize <fragment>)."""
    import time

    root = Path(root)
    now = time.time()
    offers = []
    prompts = load_prompts(root)
    for rel, path in iter_manuscript_paths(root).items():
        for slot in scan_text(_read_manuscript_text(path, rel), prompts):
            if slot.get("ref"):
                continue
            reason = None
            if len(slot["prompt"].split()) > long_words:
                reason = f"longer than {long_words} words"
            else:
                cands = slot_candidates(root, slot["desc_hash"])
                if cands:
                    oldest = min(
                        (root / ILLUS_DIR / c["name"]).stat().st_mtime
                        for c in cands)
                    if now - oldest > stable_days * 86400:
                        reason = (f"rendered and unchanged for "
                                  f"{stable_days}+ days")
            if reason:
                offers.append({"file": rel, "line": slot["line"],
                               "prompt": slot["prompt"][:60],
                               "reason": reason})
    return offers


# ---------------------------------------------- description craft file

CRAFT_FILENAME = "illustration-craft.md"

DEFAULT_CRAFT = """\
# Illustration description craft — all manuscripts

Guidelines for WRITING illustration descriptions (the spot-finder and
the chat critique both read this file; edit freely — general rules
first, model-specific carveouts in `## Model:` sections, of which only
the active image model's section is used).

- Concrete nouns with bound attributes — "a gray hooded sweatshirt and
  running shoes", never abstractions like "modern clothes"
  (abstractions get resolved in the style's era, not the text's).
- Positive phrasing only: say what IS in the image; never "no X" or
  "without X" (negations plant the very thing they forbid).
- One idea per clause; short declarative clauses.
- Camera and composition language is welcome: angle, distance, light
  source, where the negative space sits.

## Model: gemini/gemini-3-pro-image-preview

- Garbles lettering: keep every surface unlettered; ask for "worn
  illegible marks" rather than "faint script" when texture is wanted.
- Strong period pull from style vocabulary ("engraving", "woodcut"):
  when the scene is contemporary, name the era and specific garments
  explicitly in the description.
"""


def craft_text(config: dict, workspace: str | None = None) -> str:
    """The description-craft guidelines: general rules plus the active
    image model's carveout section. Seeded on first read; the file is
    the single source both the spot-finder and chat critique use.

    It lives with the project config, not in the workspace: craft rules
    are versioned decisions, not machine state. `workspace` is accepted
    and ignored so callers need not care."""
    from . import paths

    path = paths.craft_path()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(DEFAULT_CRAFT, encoding="utf-8")
    text = path.read_text(encoding="utf-8")
    model = (config.get("llm", {}) or {}).get("image_model", "")
    sections = re.split(r"(?m)^## Model:\s*", text)
    out = sections[0].rstrip()
    for section in sections[1:]:
        name, _, body = section.partition("\n")
        if name.strip() == model:
            out += f"\n\n## Model: {name.strip()}\n{body.rstrip()}"
    return out


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
    prompts = load_prompts(root)
    for rel, path in iter_manuscript_paths(root).items():
        for slot in scan_text(_read_manuscript_text(path, rel), prompts):
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
    changes it and a candidate belief cannot. Only the 'illustration'
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


def _find_tag_line(lines: list[str], deschash: str,
                   prompts: dict[str, str] | None = None) -> int | None:
    """Index of the tag whose CANONICAL description hashes to deschash.
    Ref tags carry only the excerpt inline, so identity needs the
    prompts map (load_prompts) — without it an externalized slot is
    invisible here."""
    prompts = prompts or {}
    for i, line in enumerate(lines):
        tag = parse_tag(line)
        if not tag:
            continue
        full = (prompts.get(tag["ref"], tag["prompt"]) if tag["ref"]
                else tag["prompt"])
        if desc_hash(" ".join(full.split())) == deschash:
            return i
    return None


def embed_target(text: str, deschash: str,
                 prompts: dict[str, str] | None = None) -> str | None:
    """The candidate filename the slot's embed line points at (the
    line directly under the tag), or None if not embedded."""
    lines = text.split("\n")
    i = _find_tag_line(lines, deschash, prompts)
    if i is None or i + 1 >= len(lines):
        return None
    from .revisions import EMBED_LINE

    if EMBED_LINE.match(lines[i + 1]):
        return lines[i + 1].rsplit("/", 1)[-1].rstrip(") \t")
    return None


def set_embed(path: Path, deschash: str, candidate_name: str,
              prompts: dict[str, str] | None = None) -> bool:
    """Point the slot's embed line at a candidate — insert directly
    under the tag, or rewrite the existing embed. The embed is derived
    machinery: observation, push, and the beat loop never see it."""
    from .revisions import EMBED_LINE

    text = path.read_text(encoding="utf-8")
    lines = text.split("\n")
    i = _find_tag_line(lines, deschash, prompts)
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
    prompts = load_prompts(root)
    for rel, path in iter_manuscript_paths(Path(root)).items():
        for slot in scan_text(path.read_text(encoding="utf-8"), prompts):
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
    prompts = load_prompts(root)
    embedded = embed_target((root / slot["file"]).read_text(encoding="utf-8"),
                            deschash, prompts)
    if embedded is None:
        set_embed(root / slot["file"], deschash, written[-1], prompts)
    return {"written": written, "embedded": embedded or written[-1],
            "style_hash": shash, "had_embed": embedded is not None}


def slot_status(db, manuscript: dict) -> list[dict]:
    """Every slot with its lifecycle state, for `illus list`:
    unrendered | rendered | stale-style (embed predates the current
    figure law) — plus the embedded candidate and candidate count."""
    root = Path(manuscript["path"])
    out = []
    prompts = load_prompts(root)
    for rel, path in iter_manuscript_paths(root).items():
        text = path.read_text(encoding="utf-8")
        current = style_hash(illustration_law(db, manuscript["id"], rel))
        for slot in scan_text(text, prompts):
            cands = slot_candidates(root, slot["desc_hash"])
            embedded = embed_target(text, slot["desc_hash"], prompts)
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
    prompts = load_prompts(root)
    for rel, path in iter_manuscript_paths(root).items():
        text = path.read_text(encoding="utf-8")
        for slot in scan_text(text, prompts):
            declared.add(slot["desc_hash"])
            target = embed_target(text, slot["desc_hash"], prompts)
            if target:
                keep.add(target)
    removed = []
    for candidate in candidate_files(root):
        if candidate["name"] in keep and candidate["desc"] in declared:
            continue
        (root / ILLUS_DIR / candidate["name"]).unlink()
        removed.append(candidate["name"])
    return removed


def capture_embeds(text: str,
                   prompts: dict[str, str] | None = None) -> dict[str, str]:
    """The pick state a text holds: {desc_hash: embedded candidate name}.
    Captured before a pull overwrites the file, so picks survive Doc
    round trips even though the Doc never carries embed lines."""
    from .revisions import EMBED_LINE

    prompts = prompts or {}
    lines = text.split("\n")
    out: dict[str, str] = {}
    for i, line in enumerate(lines[:-1]):
        tag = parse_tag(line)
        if tag and EMBED_LINE.match(lines[i + 1]):
            full = (prompts.get(tag["ref"], tag["prompt"])
                    if tag["ref"] else tag["prompt"])
            out[desc_hash(" ".join(full.split()))] = (
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
    prompts = load_prompts(root)
    lines = text.split("\n")
    out: list[str] = []
    for i, line in enumerate(lines):
        out.append(line)
        tag = parse_tag(line)
        if not tag:
            continue
        if i + 1 < len(lines) and EMBED_LINE.match(lines[i + 1]):
            continue  # already embedded
        full = (prompts.get(tag["ref"], tag["prompt"])
                if tag["ref"] else tag["prompt"])
        h = desc_hash(" ".join(full.split()))
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
    # Subject first, style second, precedence explicit — image models
    # weight early tokens and resolve conflicts toward whatever claims
    # authority, so the depiction must outrank the style's era/content
    # implications (live finding 2026-08-10: 'modern clothes' lost to
    # the engraving law's period pull). The stylehash still hashes the
    # LAW text; a change to this template ships with a law edit, which
    # bumps the hash and marks prior renders stale-style honestly.
    composed = ((
        "DEPICT (content commands — costume, era, objects, and "
        "composition here override anything the style implies):\n"
        f"{slot['prompt']}\n\n"
        "STYLE (technique and rendering only):\n"
        f"{law}") if law else slot["prompt"])
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
