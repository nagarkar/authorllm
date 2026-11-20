"""Illustration slots: the [Illustration: ...] tag grammar and the
scan-derived slot registry (design: docs/design-backlog.md).

A slot is declared in prose as a single-line paragraph:

    [Illustration: prompt text]
    [Illustration: prompt text | caption: reader-facing caption]

The tag IS the spec — plain prose that survives every push/pull and is
never consumed or replaced. Rendered candidates live in _illustrations/
(observation-invisible) named

    <key>-<deschash8>-<stylehash4>-<NN>.png

where KEY is the slot's identity — the ref slug of its canonical prompt
file, minted once at externalize and never recomputed. A description may
afterwards be rewritten into something unrecognisable and the art stays
attached, which is the whole point (it-2e4a5ec3d809: rewording an INLINE
slot used to orphan every image under it, because identity was the
prompt hash). deschash and stylehash ride along as STALENESS MARKERS
only — "which prompt text, under which figure law, was this made for" —
never as identity. NN numbers the candidates of a slot, prefixed `i` for
an image that was imported rather than rendered. The registry is derived
entirely by scanning text + directory — no DB state.

The manuscript-free half lives in llmlm (`llmlm.cast`, nagarkar/llmlm#20),
shared with musiclm: looking up a picked plate and saying which one is
missing or unpicked, the reference text sent with the plates, rendering
candidates with the plates attached, candidate file names and their
hashes, the fields stored inside each image, and the pin file. This
module keeps what needs a manuscript: finding cast members by scanning
tags (`cast_index`), the style law from the database
(`illustration_law`), externalized description files with their
snapshots and restore, the embed line that IS the pick, and the Doc
round trip. It hands llmlm plain data: a list of characters with their
picked plate files, and one render request.
"""

from __future__ import annotations

import dataclasses
import re
import sys
from pathlib import Path

from llmlm import cast as shared_cast
from llmlm.cast import (  # noqa: F401  (this module's callers read them here)
    MAX_CAST,
    PIN_FILE,
    desc_hash,
    image_ext,
    image_with_metadata,
    read_metadata,
    slug,
    style_hash,
)

from .revisions import iter_manuscript_paths

ILLUS_DIR = "_illustrations"

# authorlm's words in the shared cast module: the reference text says
# "the Pilgrim of this book", every stored image field is `authorlm:…`
# (so images written before the move read as before), and an undefined
# cast id is explained in terms of the tag grammar.
CAST_SETTINGS = shared_cast.Settings(
    series="this book", prefix="authorlm",
    undefined_hint="no tag carries 'cast-id: {slug}'")

_TAG = re.compile(r"\[Illustration:\s*(?P<body>.+?)\]\s*$", re.IGNORECASE)
# The tag's options tail: `| caption: …`, and the cast grammar of
# verse-and-cast-design.md §5 — `| cast-id: <slug>` defines a cast member
# or a stage plate, `| cast: a, b, c` names the plates a render attaches
# (one to three), `| prior: <slug>` names one plate to follow for stage
# and composition. Order-independent; each value runs to the next option.
_OPTION = re.compile(
    r"\s*\|\s*(?P<key>caption|cast-id|cast|prior)\s*:\s*", re.IGNORECASE)
CAST_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]*$")


_REF = re.compile(r"\s*⇢\s*(?P<ref>[\w.-]+\.md)\s*$")

# Externalized descriptions: `[Illustration: excerpt… ⇢ slug.md]` keeps
# a short reader-facing hint inline while the full prompt lives in
# _illustrations/prompts/<slug>.md (the CANONICAL text — the excerpt is
# machine-maintained and edits to it do not hold). Delete the `⇢ ref`
# to make the inline text canonical again.
PROMPTS_SUBDIR = "prompts"


def parse_tag(line: str) -> dict | None:
    """The tag grammar. A slot is a full-line tag (trailing whitespace
    tolerated); returns {prompt, caption, ref, malformed_ref} or None.
    `prompt` is the inline text — for a ref tag that is only the
    excerpt; resolution to the full description happens in scan_text
    via the prompts map.

    `malformed_ref` is True when a `⇢` is present but the text after it
    does not match `_REF` (e.g. it doesn't end in a bare `.md` name —
    the shape a corrupted doc-pull round trip can produce, it-d70ece778f55).
    That case is NOT the same as "no ref": silently falling through to
    treating the whole line as inline prompt text hides real damage, so
    callers that report on slots must surface `malformed_ref` loudly
    rather than let it read as an ordinary undecorated description."""
    m = _TAG.match(line.strip())
    if not m or not line.strip().startswith("["):
        return None
    body = m.group("body")
    caption = cast_id = prior = None
    cast: list[str] = []
    options = list(_OPTION.finditer(body))
    for i, om in enumerate(options):
        end = options[i + 1].start() if i + 1 < len(options) else len(body)
        value = body[om.end():end].strip()
        key = om.group("key").lower()
        if key == "caption":
            caption = value or None
        elif key == "cast-id":
            cast_id = value.lower() or None
        elif key == "cast":
            cast = [c.strip().lower() for c in value.split(",") if c.strip()]
        elif key == "prior":
            prior = value.lower() or None
    if options:
        body = body[: options[0].start()]
    ref = None
    rm = _REF.search(body)
    if rm:
        ref = rm.group("ref")
        body = body[: rm.start()]
    malformed_ref = ref is None and "⇢" in body
    prompt = " ".join(body.split())
    if not prompt and not ref:
        return None
    return {"prompt": prompt, "caption": caption, "ref": ref,
            "malformed_ref": malformed_ref, "cast_id": cast_id,
            "cast": cast, "prior": prior}


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


def slot_key(slot: dict) -> str | None:
    """A slot's IDENTITY: the slug of its canonical prompt file, without
    the .md. None until the slot is externalized — an inline slot has no
    stable name to hang art on, so `render` and `import` externalize
    first and mint one. The key never moves when the description is
    edited; that is what distinguishes it from desc_hash, which does."""
    ref = slot.get("ref")
    return ref[:-3] if ref and ref.endswith(".md") else None


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
        slot["key"] = slot_key(slot)
        slots.append(slot)
    return slots


def _excerpt_of(full: str, words: int = 8) -> str:
    """The machine-maintained inline excerpt: first words of the
    canonical text, ellipsized when trimmed."""
    tokens = " ".join(full.split()).split()
    head = " ".join(tokens[:words])
    return head + ("…" if len(tokens) > words else "")


def _tag_line(excerpt: str, ref: str | None, caption: str | None,
              cast_id: str | None = None, cast: list[str] | None = None,
              prior: str | None = None) -> str:
    body = excerpt + (f" ⇢ {ref}" if ref else "")
    if caption:
        body += f" | caption: {caption}"
    if cast_id:
        body += f" | cast-id: {cast_id}"
    if cast:
        body += " | cast: " + ", ".join(cast)
    if prior:
        body += f" | prior: {prior}"
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
    path = root / slot["file"]
    lines = path.read_text(encoding="utf-8").split("\n")
    # The tag line is rewritten in place, so the line must still BE this
    # tag. A line number taken before an earlier write in the same file
    # can point at prose; writing there overwrote six paragraphs on
    # 2026-11-10 (it-6bd797b7bd4a). Stop rather than touch prose.
    at = slot.get("line") or 0
    tag = parse_tag(lines[at - 1]) if 0 < at <= len(lines) else None
    if (tag is None or tag.get("ref")
            or desc_hash(tag["prompt"]) != slot["desc_hash"]):
        raise LookupError(
            f"{slot['file']}:{at} is no longer the tag "
            f"[Illustration: {_excerpt_of(slot['prompt'])}]; re-find the "
            "slot before writing (nothing was changed)")
    directory = root / ILLUS_DIR / PROMPTS_SUBDIR
    directory.mkdir(parents=True, exist_ok=True)
    base = slug(slot["prompt"])
    name, n = f"{base}.md", 2
    while (directory / name).exists():
        name, n = f"{base}-{n}.md", n + 1
    (directory / name).write_text(slot["prompt"] + "\n", encoding="utf-8")
    lines[slot["line"] - 1] = _tag_line(
        _excerpt_of(slot["prompt"]), name, slot.get("caption"),
        slot.get("cast_id"), slot.get("cast"), slot.get("prior"))
    path.write_text("\n".join(lines), encoding="utf-8")
    return {"file": slot["file"], "ref": name,
            "desc_hash": slot["desc_hash"]}


def ensure_key(root: Path, slot: dict) -> dict:
    """The slot, guaranteed to carry a key — externalizing it first if
    it is still inline. The FIRST IMAGE is what mints a slot's key, so
    this runs ahead of every render and every import. Naming art after a
    description is what created the orphans this replaced, so an inline
    slot never gets art: it gets a canonical prompt file, and the art is
    named after that."""
    if slot.get("key"):
        return slot
    if slot.get("ref"):
        return {**slot, "key": slot_key(slot)}
    out = externalize(Path(root), slot)
    return {**slot, "ref": out["ref"], "key": out["ref"][:-3],
            "excerpt": _excerpt_of(slot["prompt"])}


def import_image(db, manuscript: dict, slot: dict, source) -> dict:
    """Bring an image made OUTSIDE AuthorLM into a slot as a first-class
    candidate: an illustrator's plate, a scan, a drawing drafted
    elsewhere. It lands under the slot's key exactly like a render, so
    every later verb — pick, render --from, prune, the Doc round trip —
    treats it identically. The `i` in its number is a LABEL for the
    reader (`illus list` must not call it a render it is not) and never
    a permission: an import may stand as the final plate, seed a render,
    or both. Existing work becomes reusable, which a system that could
    only consume its own output could not do (it-2e4a5ec3d809).

    The deschash and stylehash recorded are the ones current AT IMPORT —
    what the slot was asking for when the image arrived, which is what a
    later staleness check wants to know."""
    root = Path(manuscript["path"])
    source = Path(source).expanduser()
    img = source.read_bytes()
    if not img.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8")):
        raise ValueError(f"{source.name} is not a PNG or a JPEG — "
                         "convert it first; the store holds only what "
                         "the manuscript can embed")
    slot = ensure_key(root, slot)
    shash = style_hash(illustration_law(db, manuscript["id"], slot["file"]))
    directory = root / ILLUS_DIR
    n = shared_cast.next_number(directory, slot["key"])
    name = shared_cast.candidate_name(
        slot["key"], slot["desc_hash"], shash, n, image_ext(img),
        imported=True)
    directory.mkdir(exist_ok=True)
    (directory / name).write_bytes(image_with_metadata(img, {
        "authorlm:prompt": slot["prompt"],
        "authorlm:caption": slot.get("caption") or "",
        "authorlm:source": f"imported from {source.name}",
    }))
    path = root / slot["file"]
    embedded = embed_target(path.read_text(encoding="utf-8"), slot["key"])
    if embedded is None:
        set_embed(path, slot["key"], name)
    return {"name": name, "key": slot["key"], "ref": slot["ref"],
            "file": slot["file"], "n": n, "source": source.name,
            "had_embed": embedded is not None,
            "embedded": embedded or name}


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
                lines[i] = _tag_line(expected, tag["ref"], tag["caption"],
                                     tag.get("cast_id"), tag.get("cast"),
                                     tag.get("prior"))
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

    from .llm import resolve_image_setting

    path = paths.craft_path()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(DEFAULT_CRAFT, encoding="utf-8")
    text = path.read_text(encoding="utf-8")
    model = resolve_image_setting(config, "model", "image_model", "")
    sections = re.split(r"(?m)^## Model:\s*", text)
    out = sections[0].rstrip()
    for section in sections[1:]:
        name, _, body = section.partition("\n")
        if name.strip() == model:
            out += f"\n\n## Model: {name.strip()}\n{body.rstrip()}"
    return out


def candidate_files(root: Path) -> list[dict]:
    """Parsed candidate PNGs on disk: {name, key, desc, style, src, n}.
    `src` is "i" for an imported image and "" for a native render — a
    provenance LABEL for the reader, never a permission: an import is
    pickable as the final plate, as a seed for `--from`, or anything
    between."""
    return shared_cast.candidates(Path(root) / ILLUS_DIR)


def slot_report(root: Path) -> dict:
    """The scan-derived registry delta a collect surfaces: slots with no
    rendered candidate ("new illustrations found"), candidate files
    whose prompt no longer exists anywhere (edited or deleted tags), and
    tags carrying a malformed `⇢` ref (arrow present but not a valid
    file reference — reported loudly rather than silently read as an
    ordinary inline description; it-d70ece778f55)."""
    root = Path(root)
    slots = []
    prompts = load_prompts(root)
    for rel, path in iter_manuscript_paths(root).items():
        for slot in scan_text(_read_manuscript_text(path, rel), prompts):
            slots.append({"file": rel, **slot})
    held = {c["key"] for c in candidate_files(root)}
    declared = {s["key"] for s in slots if s["key"]}
    unrendered = [
        {"file": s["file"], "line": s["line"], "prompt": s["prompt"],
         "caption": s.get("caption"), "ref": s.get("ref"), "key": s["key"],
         "desc_hash": s["desc_hash"], "cast_id": s.get("cast_id"),
         "cast": s.get("cast") or [], "prior": s.get("prior")}
        for s in slots if s["key"] not in held
    ]
    index, cast_problems = cast_index(root)
    for s in slots:
        if s.get("cast") or s.get("prior"):
            cast_problems += resolve_references(root, s, index)["errors"]
    orphaned = [c["name"] for c in candidate_files(root)
                if c["key"] not in declared]
    malformed_refs = [
        {"file": s["file"], "line": s["line"], "prompt": s["prompt"]}
        for s in slots if s.get("malformed_ref")
    ]
    return {"unrendered": unrendered, "orphaned": orphaned,
            "malformed_refs": malformed_refs, "cast_problems": cast_problems}


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


def slot_candidates(root: Path, key: str | None) -> list[dict]:
    """This slot's candidates in render order (NN ascends across style
    AND description generations — a slot's numbering never resets).
    A slot with no key has no candidates: nothing has been minted for it
    yet."""
    if not key:
        return []
    return shared_cast.candidates(Path(root) / ILLUS_DIR, key)


def _find_tag_line(lines: list[str], key: str) -> int | None:
    """Index of the tag whose KEY is this one. The key is in the tag
    itself (the ⇢ ref), so this needs no prompts map and — unlike the
    old hash lookup — survives any rewriting of the description."""
    for i, line in enumerate(lines):
        tag = parse_tag(line)
        if tag and slot_key(tag) == key:
            return i
    return None


def embed_target(text: str, key: str) -> str | None:
    """The candidate filename the slot's embed line points at (the
    line directly under the tag), or None if not embedded."""
    lines = text.split("\n")
    i = _find_tag_line(lines, key)
    if i is None or i + 1 >= len(lines):
        return None
    from .revisions import EMBED_LINE

    if EMBED_LINE.match(lines[i + 1]):
        return lines[i + 1].rsplit("/", 1)[-1].rstrip(") \t")
    return None


def set_embed(path: Path, key: str, candidate_name: str) -> bool:
    """Point the slot's embed line at a candidate — insert directly
    under the tag, or rewrite the existing embed. The embed is derived
    machinery: observation, push, and the beat loop never see it."""
    from .revisions import EMBED_LINE

    text = path.read_text(encoding="utf-8")
    lines = text.split("\n")
    i = _find_tag_line(lines, key)
    if i is None:
        return False
    embed = f"![]({ILLUS_DIR}/{candidate_name})"
    if i + 1 < len(lines) and EMBED_LINE.match(lines[i + 1]):
        lines[i + 1] = embed
    else:
        lines.insert(i + 1, embed)
    path.write_text("\n".join(lines), encoding="utf-8")
    return True


def relocate_slot(root: Path, slot: dict) -> dict:
    """The slot as its file declares it NOW: re-read the file and find
    the same slot (by key, or for an inline slot by its description
    hash, nearest its old line), returning the full scan dict at its
    current line, with cast and prior. A slot dict goes stale the moment
    any write lands in its file (an externalize, an embed line), so
    every render re-finds its slot first; a multi-slot run then renders
    exactly what one-at-a-time renders would (it-6bd797b7bd4a,
    it-d088feca40aa). LookupError when the slot is gone."""
    root = Path(root)
    rel = slot["file"]
    text = _read_manuscript_text(root / rel, rel)
    found = scan_text(text, load_prompts(root))
    key = slot.get("key")
    if key:
        hits = [s for s in found if s["key"] == key]
    else:
        hits = [s for s in found
                if not s["key"] and s["desc_hash"] == slot["desc_hash"]]
    if not hits:
        raise LookupError(
            f"{rel}: the slot [Illustration: {_excerpt_of(slot['prompt'])}]"
            " is no longer in the file")
    old = slot.get("line") or 0
    best = min(hits, key=lambda s: abs(s["line"] - old))
    return {**slot, **best, "file": rel}


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


# --------------------------------------------------------------- the cast
# verse-and-cast-design.md §5–§7. A cast member or stage plate is an
# ordinary slot whose tag carries `cast-id:`; a poem's tag names the
# plates it wants attached with `cast:` and one plate to follow with
# `prior:`. The PINNED PICK of each named slot is the file the renderer
# sends. An unpicked plate is a render-time error, which is what forces
# the plates to come first.

def cast_index(root) -> tuple[dict[str, dict], list[str]]:
    """Every `cast-id:` slot in the manuscript, keyed by id, each with
    `plate` (its pinned candidate name, or None when unpicked), plus the
    problems the scan found: a malformed id, an id defined twice."""
    root = Path(root)
    prompts = load_prompts(root)
    index: dict[str, dict] = {}
    problems: list[str] = []
    for rel, path in iter_manuscript_paths(root).items():
        text = _read_manuscript_text(path, rel)
        for slot in scan_text(text, prompts):
            cid = slot.get("cast_id")
            if not cid:
                continue
            where = f"{rel}:{slot['line']}"
            if not CAST_SLUG.match(cid):
                problems.append(f"{where}: cast-id '{cid}' is not a slug "
                                "(lowercase letters, digits, hyphens)")
                continue
            if cid in index:
                first = index[cid]
                problems.append(f"{where}: cast-id '{cid}' is already "
                                f"defined at {first['file']}:{first['line']}")
                continue
            plate = embed_target(text, slot["key"]) if slot["key"] else None
            if plate and not (root / ILLUS_DIR / plate).exists():
                plate = None
            index[cid] = {**slot, "file": rel, "plate": plate}
    return index, problems


def cast_characters(root: Path, index: dict[str, dict]) -> list:
    """The cast as the plain list llmlm takes: each id with its picked
    plate file (None while unpicked) and where its tag stands. Clips
    hand the same list to llmlm (`media.py`, `media_gen.py`)."""
    return [shared_cast.Character(
                cid, (Path(root) / ILLUS_DIR / entry["plate"])
                if entry.get("plate") else None,
                f"{entry['file']}:{entry['line']}")
            for cid, entry in index.items()]


def _request(slot: dict, law: str = "", count: int = 1,
             input_image: bytes | None = None,
             extra: dict | None = None):
    """A slot as llmlm's render request."""
    return shared_cast.Request(
        description=slot["prompt"],
        cast=tuple(slot.get("cast") or []),
        prior=slot.get("prior") or None,
        look=law, count=count, input_image=input_image,
        where=f"{slot.get('file', '?')}:{slot.get('line', '?')}",
        caption=slot.get("caption") or "", extra=extra or {})


def _resolve(root, slot: dict, index: dict[str, dict] | None = None):
    root = Path(root)
    if index is None:
        index, _ = cast_index(root)
    return shared_cast.resolve_references(
        cast_characters(root, index), _request(slot), CAST_SETTINGS)


def _reference_dict(ref) -> dict:
    return {"slug": ref.slug, "role": ref.role, "name": ref.name,
            "path": ref.path}


def resolve_references(root, slot: dict,
                       index: dict[str, dict] | None = None) -> dict:
    """The plates a render of `slot` attaches, in order — its `cast:` ids
    then its `prior:` — as {slug, role, name, path}; and every reason one
    could not be attached, as a sentence. Errors are returned, never
    raised: `effective_prompt` reports them and `render_slot` stops on
    them. The lookup and the sentences are llmlm's
    (`llmlm.cast.resolve_references`)."""
    resolved = _resolve(root, slot, index)
    return {"references": [_reference_dict(r) for r in resolved.references],
            "errors": resolved.errors}


# ---------------------------------------------------------------- the pin
# verse-and-cast-design.md §7, §15. A style is model-bound, so each
# manuscript pins the illustration model and size in its own folder:
# stamped COMPLETE from the global default the first time it is needed
# (init, or the first render of a manuscript that predates the pin),
# read on every render after, changed only by `illus pin`. Copy, then
# own: there is no override chain back to config.toml.

# The file itself (reading, writing whole, the off-model rule) is
# llmlm's (`llmlm.cast`); the folder, the config it is stamped from and
# the comment at its head are authorlm's.

_PIN_HEADER = (
    "# _illustrations/settings.toml — this manuscript's illustration "
    "pin.\n"
    "# Stamped once from config.toml [illustrations] and owned here "
    "from then on:\n"
    "# the global setting no longer reaches this book. Change with\n"
    "# 'authorlm illus pin --model … --size …'. A render on any other "
    "model or\n"
    "# size stops unless named with --model, and is then marked "
    "off-model.\n"
    "# (docs/verse-and-cast-design.md §7, §15)\n")


def pin_path(root) -> Path:
    return shared_cast.pin_path(Path(root) / ILLUS_DIR)


def load_pin(root) -> dict | None:
    return shared_cast.load_pin(Path(root) / ILLUS_DIR)


def write_pin(root, model: str, image_size: str) -> Path:
    return shared_cast.write_pin(Path(root) / ILLUS_DIR, model, image_size,
                                 header=_PIN_HEADER)


def ensure_pin(root, config: dict) -> dict:
    """The pin, stamping it from the global default when absent."""
    pin = load_pin(root)
    if pin is not None:
        return pin
    from .llm import DEFAULT_IMAGE_MODEL, resolve_image_setting

    return shared_cast.ensure_pin(
        Path(root) / ILLUS_DIR,
        resolve_image_setting(config, "model", "image_model",
                              DEFAULT_IMAGE_MODEL),
        resolve_image_setting(config, "image_size", "image_size", ""),
        header=_PIN_HEADER)


def _resolve_pin(root, config: dict, model_override: str | None = None,
                 size_override: str | None = None):
    return shared_cast.resolve_pin(ensure_pin(root, config), model_override,
                                   size_override)


def resolve_pin(root, config: dict, model_override: str | None = None,
                size_override: str | None = None) -> dict:
    """What a render runs on: the pin, unless the author named another
    model or size for this render — which is permitted and marked
    off-model, never silent."""
    return dataclasses.asdict(
        _resolve_pin(root, config, model_override, size_override))


def render_slot(db, manuscript: dict, slot: dict, config: dict,
                from_n: int | None = None, count: int = 1,
                generator=None, model: str | None = None,
                size: str | None = None) -> dict:
    """Render `count` new candidates for a slot. `from_n` passes an
    existing candidate as the input image — continuity across a style
    change, and the seed door for an imported plate. A slot with no
    embed yet gets one pointing at the newest render; an existing embed
    is NEVER moved — that is `pick`'s job. An inline slot is
    EXTERNALIZED first: the first image is what mints a slot's key."""
    from .llm import generate_image

    root = Path(manuscript["path"])
    directory = root / ILLUS_DIR
    pin = _resolve_pin(root, config, model, size)
    generator = generator or (
        lambda prompt, input_png, references=None:
        generate_image(config, prompt, input_png, references=references,
                       model=pin.model, size=pin.size))
    # The file is the truth: re-find the slot so a stale or partial
    # dict (an old line number, a dropped cast) can neither write at the
    # wrong line nor render without its references.
    slot = relocate_slot(root, slot)
    law = illustration_law(db, manuscript["id"], slot["file"])
    index, _ = cast_index(root)
    characters = cast_characters(root, index)
    # Before anything is written: a slot that names a cast member with
    # no plate stops here (llmlm's CastError, a LookupError), so its tag
    # is not externalized for a render that will not happen.
    shared_cast.attach(characters, _request(slot), CAST_SETTINGS)
    slot = ensure_key(root, slot)

    input_png = None
    if from_n is not None:
        source = next((c for c in slot_candidates(root, slot["key"])
                       if int(c["n"]) == from_n), None)
        if source is None:
            raise LookupError(f"slot has no candidate {from_n:02d}")
        input_png = (directory / source["name"]).read_bytes()

    rendered = shared_cast.render(
        directory, slot["key"],
        _request(slot, law, count, input_png,
                 {"from": f"{from_n:02d}"} if from_n else None),
        characters, pin, CAST_SETTINGS,
        generator=lambda prompt, image, plates: (
            generator(prompt, image, references=plates) if plates
            else generator(prompt, image)))
    written = rendered.written
    embedded = embed_target((root / slot["file"]).read_text(encoding="utf-8"),
                            slot["key"])
    if embedded is None:
        set_embed(root / slot["file"], slot["key"], written[-1])
    return {"written": written, "embedded": embedded or written[-1],
            "style_hash": rendered.style_hash,
            "had_embed": embedded is not None,
            "model": pin.model, "off_model": pin.off_model,
            "pinned_model": pin.pinned_model,
            "references": [f"{r.role} {r.slug}"
                           for r in rendered.references]}


def slot_status(db, manuscript: dict) -> list[dict]:
    """Every slot with its lifecycle state, for `illus list`:
    unrendered | rendered | imported (the standing plate came in from
    outside rather than from a render) | stale-desc (the plate was made
    for a description since rewritten) | stale-style (it predates the
    current figure law). A plate can be behind on both; stale-desc wins
    the label, being the larger divergence. Plus the embedded candidate
    and the candidate count."""
    root = Path(manuscript["path"])
    out = []
    prompts = load_prompts(root)
    index, _problems = cast_index(root)
    for rel, path in iter_manuscript_paths(root).items():
        text = path.read_text(encoding="utf-8")
        current = style_hash(illustration_law(db, manuscript["id"], rel))
        for slot in scan_text(text, prompts):
            cands = slot_candidates(root, slot["key"])
            embedded = embed_target(text, slot["key"]) if slot["key"] else None
            state = "unrendered"
            off_model = False
            if cands:
                target = next((c for c in cands if c["name"] == embedded),
                              None) or cands[-1]
                state = "imported" if target["src"] else "rendered"
                if target["style"] != current:
                    state = "stale-style"
                meta = read_metadata(root / ILLUS_DIR / target["name"])
                off_model = meta.get("authorlm:off-model") == "true"
                if slot.get("cast") or slot.get("prior"):
                    # stale-cast: the plate was made against picks that
                    # have since moved (or against none) — design §5.
                    wanted = _resolve(root, slot, index)
                    now = {(r.role, r.slug, r.name)
                           for r in wanted.references}
                    then = shared_cast.recorded_references(
                        meta, CAST_SETTINGS.prefix)
                    if now != then and not target["src"]:
                        state = "stale-cast"
                if target["desc"] != slot["desc_hash"]:
                    state = "stale-desc"
            out.append({"file": rel, "line": slot["line"],
                        "prompt": slot["prompt"],
                        "caption": slot.get("caption"),
                        "key": slot["key"],
                        "cast_id": slot.get("cast_id"),
                        "cast": slot.get("cast") or [],
                        "prior": slot.get("prior"),
                        "state": state, "candidates": len(cands),
                        "embedded": embedded, "off_model": off_model,
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
            if not slot["key"]:
                continue
            declared.add(slot["key"])
            target = embed_target(text, slot["key"])
            if target:
                keep.add(target)
    removed = []
    for candidate in candidate_files(root):
        if candidate["name"] in keep and candidate["key"] in declared:
            continue
        (root / ILLUS_DIR / candidate["name"]).unlink()
        removed.append(candidate["name"])
    return removed


def capture_embeds(text: str,
                   prompts: dict[str, str] | None = None) -> dict[str, str]:
    """The pick state a text holds: {key: embedded candidate name}.
    Captured before a pull overwrites the file, so picks survive Doc
    round trips even though the Doc never carries embed lines. Keyed by
    the slot KEY, not the description hash: the author reworks prompts
    IN the Doc, and a hash-keyed capture lost the image on exactly that
    move (it-2e4a5ec3d809). `prompts` is accepted and unused — the key
    is in the tag, so no canonical text is needed to read it."""
    from .revisions import EMBED_LINE

    lines = text.split("\n")
    out: dict[str, str] = {}
    for i, line in enumerate(lines[:-1]):
        tag = parse_tag(line)
        if tag and slot_key(tag) and EMBED_LINE.match(lines[i + 1]):
            out[slot_key(tag)] = (
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
        key = slot_key(tag)
        name = prior.get(key) if key else None
        if name and not (root / ILLUS_DIR / name).exists():
            name = None
        if name is None:
            cands = slot_candidates(root, key)
            name = cands[-1]["name"] if cands else None
        if name:
            out.append(f"![]({ILLUS_DIR}/{name})")
    return "\n".join(out)


def effective_prompt(db, manuscript: dict, slot: dict) -> dict:
    """The exact prompt a render of this slot would send, assembled
    deterministically — THE debugging instrument for "why does this
    image look like that". It composes through `llmlm.cast.compose`, the
    call a render makes, so the two can never diverge."""
    law = illustration_law(db, manuscript["id"], slot["file"])
    resolved = _resolve(Path(manuscript["path"]), slot)
    # Subject first, the reference text, style last, with the precedence
    # in words: `llmlm.cast.compose`, the same call `llmlm.cast.render`
    # makes. The stylehash still hashes the LAW text; a change to the
    # template ships with a law edit, which bumps the hash and marks
    # prior renders stale-style honestly.
    composed = shared_cast.compose(
        slot["prompt"],
        shared_cast.reference_block(resolved.references, CAST_SETTINGS), law)
    return {
        "file": slot["file"],
        "line": slot.get("line"),
        "prompt": slot["prompt"],
        "caption": slot.get("caption"),
        "desc_hash": slot["desc_hash"],
        "style_hash": style_hash(law),
        "law": law,
        "composed": composed,
        "references": [_reference_dict(r) for r in resolved.references],
        "reference_errors": resolved.errors,
    }
