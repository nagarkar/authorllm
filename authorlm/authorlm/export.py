"""Single-file manuscript export — a transient, regenerate-on-demand artifact.

`doc create-manuscript` combines every content file (reading order per
toc.toml) into `_exports/<Manuscript Name>.md` and mirrors it to one Google
Doc named after the manuscript. Both artifacts are disposable: neither is
ever observed (the underscore directory is invisible to collection; the
Doc is push-only, never reconciled or pulled), and every export overwrites
them in place — the same local file, the same Doc id. Delete either
freely; the next export recreates it.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date
from pathlib import Path

from . import directives
from .db import Database
from .gdocs import (GDOC_MIME, _ensure_folder, _mapping, _save_mapping,
                    normalize_markdown)
from .revisions import read_manuscript_files
from .structure import is_continuation, reading_order, select_chapters

EXPORT_DIR = "_exports"
EXPORT_KEY = "_export"  # metadata.gdocs key; '_' keeps reconcile/pull away
PUBLICATION_DIR = Path(__file__).with_name("publication")
_UNSAFE = re.compile(r'[\\/:*?"<>|#]')


def export_filename(name: str) -> str:
    cleaned = _UNSAFE.sub("-", name.strip()).strip()
    if not cleaned:
        raise ValueError("empty manuscript name")
    return cleaned + ".md"


_NOTE_LABEL = re.compile(r"\[\^([A-Za-z0-9_-]+)\]")


def footnote_prefixes(names: list[str]) -> dict[str, str]:
    """Per-file namespace tokens for footnote labels in combined exports:
    the shortest unique prefix of each file's stem — the first letter,
    extended to k+1 letters wherever two files collide on their first k
    (the author's scheme, it-9e6b7613a5c5). An exhausted stem uses its
    whole self; file names are unique, so the map is collision-free."""
    stems = {name: Path(name).stem for name in names}
    tokens: dict[str, str] = {}
    for name, stem in stems.items():
        others = [s for n, s in stems.items() if n != name]
        k = 1
        while k < len(stem) and any(o[:k] == stem[:k] for o in others):
            k += 1
        tokens[name] = stem[:k]
    return tokens


def namespace_footnotes(text: str, token: str) -> str:
    """[^X] → [^token-X] on every reference and definition, so labels
    stay unique after concatenation. Labels never render — the reader's
    footnote numbering is untouched; only the collision goes away."""
    return _NOTE_LABEL.sub(lambda m: f"[^{token}-{m.group(1)}]", text)


# ------------------------------------------------ per-output regions

# Output names an [Omit:] / [Only:] tag may address. `doc` is the
# push-only export Doc; `audio` is the stripped (audio-clean) markdown.
OUTPUTS = ("pdf", "docx", "epub", "md", "doc", "audio")
# `[Omit: all]` names every output above, present and future, the export
# Doc included. The Doc TABS are not an output: a tab is the working
# surface and carries the region, tags and all, on every push and pull.
ALL_OUTPUTS = "all"
_REGION_OPEN = re.compile(
    r"^\[(?P<kind>Omit|Only):\s*(?P<names>[^\]]*)\]\s*$", re.IGNORECASE)
_REGION_CLOSE = re.compile(r"^\[/(?P<kind>Omit|Only)\]\s*$", re.IGNORECASE)


# The full-line casting tag (docs/audiobook-pipeline-design.md §5.3):
# `[Voice: key | stability=0.6 | speed=0.95]`. Plain text on every road
# exactly like [Omit:] — it goes to the Doc tabs so the pull round trip
# survives — and stripped from every READER output here. The audio
# export consumes it (audio.py); nothing else reads it.
VOICE_LINE = re.compile(r"^\[Voice:\s*(?P<body>[^\]]*)\]\s*$", re.IGNORECASE)


def strip_voice_lines(text: str) -> str:
    return "\n".join(line for line in text.split("\n")
                     if not VOICE_LINE.match(line))


# The full-line signature tag: `[Signature: _assets/signature.png]`.
# Plain text on every road like [Voice:] and [Illustration:] — it stays
# in the file and on the Doc tabs — and expanded for the reader outputs
# alone (resolve_signatures): the image, the author's name, the export
# date. The audio-clean build drops it; nothing is read aloud.
SIGNATURE_LINE = re.compile(
    r"^\[Signature:\s*(?P<uri>[^\]]*)\]\s*$", re.IGNORECASE)
SIGNATURE_WIDTH = "40%"


def signature_path(uri: str, root: Path) -> Path:
    """The image a signature uri names: absolute, `~/`, or relative to
    the manuscript root. Empty or missing is a fault — a signature that
    silently vanished would read as an unsigned copy."""
    uri = uri.strip()
    if not uri:
        raise RuntimeError("[Signature:] names no image — give it a path "
                           "(absolute, ~/, or relative to the manuscript)")
    path = Path(uri).expanduser()
    if not path.is_absolute():
        path = root / path
    if not path.is_file():
        raise RuntimeError(f"[Signature: {uri}] — no such image at {path}")
    return path


def resolve_signatures(text: str, root: Path, author: str,
                       outputs: frozenset[str] | set[str],
                       when: date | None = None) -> str:
    """Expand every `[Signature: uri]` line for one build: the image at
    about two inches (SIGNATURE_WIDTH of the text width, left-aligned,
    empty alt so pandoc makes no figure and no caption), then the
    author's name, then the export date (`25 October 2026`). An audio
    build drops the line. The image is checked here, never later: a
    missing file raises naming the uri."""
    out: list[str] = []
    stamp = (when or date.today()).strftime("%-d %B %Y")
    for line in text.split("\n"):
        m = SIGNATURE_LINE.match(line)
        if not m:
            out.append(line)
            continue
        if "audio" in outputs:
            continue
        path = signature_path(m.group("uri"), root)
        if not author:
            raise RuntimeError(
                "[Signature:] needs the author's name — set it with "
                "'authorlm manuscript set --author'")
        uri = m.group("uri").strip()
        src = uri if (not Path(uri).is_absolute()
                      and not uri.startswith("~")) else str(path)
        out.extend(["", f"![]({src}){{width={SIGNATURE_WIDTH}}}", "",
                    author, "", stamp, ""])
    return "\n".join(out)


def publish_outputs(fmt: str, variant: str) -> frozenset[str]:
    """The output names a build answers to: its format, plus `audio`
    when the build IS the audio-clean markdown (`fmt=md` with
    `variant=stripped`). Illustration stripping is independent — a
    stripped PDF/DOCX/EPUB/Doc still drops plates — but must not inherit
    audio region membership or the display-math drop: a leftover
    `variant=stripped` in export settings would otherwise silently gut
    equations and `[Omit: audio]` passages from every print build."""
    names = {fmt}
    if fmt == "md" and variant == "stripped":
        names.add("audio")
    return frozenset(names)


def resolve_regions(text: str, outputs: frozenset[str] | set[str],
                    where: str = "") -> str:
    """Apply the full-line region tags for one build.

        [Omit: audio, epub]  …  [/Omit]   dropped from the named outputs
        [Omit: all]          …  [/Omit]   dropped from every output
        [Only: audio]        …  [/Only]   kept for the named outputs only

    Same grammar as [Illustration: …]: plain text on every road (Obsidian,
    the Doc bridge, pandoc), resolved here alone. Regions nest — an
    `[Only:]` inside an `[Omit:]` is the audio substitution pattern
    (docs/math-and-physics-guidelines.md §5): the inner Only re-includes
    its body for the named outputs even when an outer Omit would drop
    them. `all` is Omit's alone and stands for every name in OUTPUTS, so
    a region meant for no reader need not be kept in step with that list;
    an `[Only: all]` would be untagged text and is a fault. The tag lines
    themselves never reach a reader. A structural
    fault — an unknown output name, a stray or unmatched close, an
    unclosed region — raises, naming the line: a typo must never
    silently include or exclude a passage."""
    wanted = set(outputs)
    # (kind, allows): Only stores hit; Omit stores not-hit.
    stack: list[tuple[str, bool]] = []
    kept: list[str] = []
    prefix = f"{where}:" if where else "line "

    def _line_kept() -> bool:
        # Walk outside-in. Omit that hits this build hides; Only then
        # resets visibility to its own hit — so Only-inside-Omit can
        # substitute a spoken paraphrase without the conjunctive
        # all()-of-frames rule deleting both halves.
        visible = True
        for kind, allows in stack:
            if kind == "only":
                visible = allows
            elif not allows:
                visible = False
        return visible

    for lineno, line in enumerate(text.split("\n"), 1):
        m = _REGION_OPEN.match(line)
        if m:
            kind = m.group("kind").lower()
            names = {n.strip().lower()
                     for n in re.split(r"[,\s]+", m.group("names")) if n.strip()}
            if ALL_OUTPUTS in names:
                if kind == "only":
                    raise ValueError(
                        f"{prefix}{lineno}: [Only: {ALL_OUTPUTS}] is "
                        f"untagged text — '{ALL_OUTPUTS}' belongs to "
                        f"[Omit:] alone")
                names = (names - {ALL_OUTPUTS}) | set(OUTPUTS)
            unknown = sorted(names - set(OUTPUTS))
            if unknown or not names:
                raise ValueError(
                    f"{prefix}{lineno}: [{kind.title()}:] names "
                    f"{'no output' if not names else 'unknown output ' + ', '.join(unknown)}"
                    f" (one of: {', '.join(OUTPUTS)}"
                    f"{', ' + ALL_OUTPUTS if kind == 'omit' else ''})")
            hit = bool(names & wanted)
            stack.append((kind, hit if kind == "only" else not hit))
            continue
        m = _REGION_CLOSE.match(line)
        if m:
            kind = m.group("kind").lower()
            if not stack or stack[-1][0] != kind:
                raise ValueError(
                    f"{prefix}{lineno}: [/{kind.title()}] closes nothing"
                    + (f" (open region is [{stack[-1][0].title()}:])"
                       if stack else ""))
            stack.pop()
            continue
        if _line_kept():
            kept.append(line)
    if stack:
        raise ValueError(
            f"{prefix}end of file: [{stack[-1][0].title()}:] never closed")
    return "\n".join(kept)


def strip_display_math(text: str, where: str = "") -> str:
    """Drop display equations ($$ … $$, single-line by convention, or a
    multi-line block opened and closed by a lone $$) — the audio default:
    an equation read aloud by a synthetic voice is noise. Inline math
    stays; the style guide keeps it to what a voice can say.

    Malformed display math refuses by file and line (same posture as
    resolve_regions / docs/math-and-physics-guidelines.md §6): an unclosed
    block, or a line that starts with $$ but is neither a lone opener nor
    a single-line $$…$$, must never silently eat the rest of the essay.
    Before this guard, `$$\\nE=mc^2` with no closer (or a prose line like
    `$$100 was the price.`) dropped every following paragraph from the
    audio-clean export with no error."""
    out: list[str] = []
    in_block = False
    prefix = f"{where}:" if where else "line "
    for lineno, line in enumerate(text.split("\n"), 1):
        s = line.strip()
        if in_block:
            if "$$" in s:
                in_block = False
            continue
        if s == "$$":
            in_block = True
            continue
        if s.startswith("$$") and s.endswith("$$") and len(s) > 2:
            continue  # single-line $$…$$
        if s.startswith("$$"):
            raise ValueError(
                f"{prefix}{lineno}: display math must be a lone $$ "
                f"or a single-line $$…$$")
        out.append(line)
    if in_block:
        raise ValueError(
            f"{prefix}end of file: display math ($$) never closed")
    return "\n".join(out)


def combined_markdown(manuscript: dict) -> tuple[str, list[str], list[str]]:
    """(combined text, ordered file names, files missing from toc.toml).

    Pure concatenation of the normalized content files in reading order —
    no added headings or separators; the combination is mechanical, the
    prose stays exactly the author's. Footnote labels alone are
    namespaced per file (footnote_prefixes): they are only file-unique
    in the sources, and pandoc would bind colliding labels to one
    definition across essays. Region tags resolve for the `doc` output."""
    from .media import strip_for_book

    def strip_media(text: str, name: str) -> str:
        return strip_for_book(text, name)[0]

    files = read_manuscript_files(Path(manuscript["path"]))
    order, unlisted = reading_order(files)
    tokens = footnote_prefixes(order)
    parts = [namespace_footnotes(
        normalize_markdown(strip_voice_lines(strip_media(resolve_regions(
            normalize_markdown(files[name]), {"doc"}, name), name))).rstrip("\n"),
        tokens[name])
        for name in order]
    text = "\n\n".join(part for part in parts if part)
    return (text + "\n" if text else ""), order, unlisted


def _doc_gone(err: Exception) -> bool:
    return getattr(getattr(err, "resp", None), "status", None) == 404


DOCX_MIME = ("application/vnd.openxmlformats-officedocument"
             ".wordprocessingml.document")


def build_docx(manuscript: dict, md_path: Path, out_path: Path,
               settings: dict) -> None:
    """pandoc markdown → DOCX for the manuscript: images resolve from the
    manuscript root, author and copyright ride as document properties,
    no title block (title.md leads as front matter)."""
    import subprocess

    root = Path(manuscript["path"])
    command = ["pandoc", str(md_path), "-o", str(out_path),
               "--from", "markdown+smart", "--standalone",
               "--resource-path", str(root)]
    author = manuscript.get("author", "").strip()
    owner = manuscript.get("copyright_owner", "").strip()
    if author:
        command += ["--metadata", f"author={author}"]
    if owner:
        command += ["--metadata",
                    f"subject=Copyright © {date.today().year} {owner}"]
    if settings.get("reference_docx"):
        command += ["--reference-doc", settings["reference_docx"]]
    proc = subprocess.run(command, cwd=root, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"pandoc failed: {proc.stderr.strip()[:400]}")


def export_manuscript(db: Database, manuscript: dict, service=None,
                      title: str | None = None) -> dict:
    """Write the publishable markdown (illustrations embedded per the
    export settings, regions resolved for the `doc` output) to _exports/
    and, given a Drive service, build it to DOCX with pandoc and
    create-or-update the manuscript's single export Doc from that.
    Drive turns Word equations, footnotes, and images into native Doc
    objects — none of which the markdown importer carries
    (docs/doc-bridge-markdown-vs-docx.md). A Doc the author deleted by
    hand is recreated (transient artifacts carry no history)."""
    settings = load_settings(manuscript)
    text, order, warnings = publish_markdown(
        manuscript, settings["variant"] or "images", fmt="doc")
    if not order:
        raise LookupError("the manuscript has no content files to combine")
    _, unlisted = reading_order(read_manuscript_files(Path(manuscript["path"])))
    doc_title = title or manuscript["name"]
    export_dir = Path(manuscript["path"]) / EXPORT_DIR
    export_dir.mkdir(exist_ok=True)
    path = export_dir / export_filename(doc_title)

    meta = _mapping(db, manuscript)
    entry = meta.setdefault("gdocs", {}).setdefault(EXPORT_KEY, {})
    stale = entry.get("local_file")
    if stale and stale != path.name:
        (export_dir / stale).unlink(missing_ok=True)
        (export_dir / stale).with_suffix(".docx").unlink(missing_ok=True)
    entry["local_file"] = path.name
    path.write_text(text, encoding="utf-8")

    result = {
        "path": str(path), "files": order, "unlisted": unlisted,
        "warnings": warnings, "doc_title": doc_title, "doc_id": None,
        "url": None, "created": False,
    }
    if service is not None:
        import shutil

        from googleapiclient.http import MediaFileUpload

        if shutil.which("pandoc") is None:
            raise RuntimeError("pandoc is required to build the export Doc "
                               "— brew install pandoc")
        docx_path = path.with_suffix(".docx")
        build_docx(manuscript, path, docx_path, settings)
        media = MediaFileUpload(str(docx_path), mimetype=DOCX_MIME)
        if entry.get("doc_id"):
            try:
                service.files().update(fileId=entry["doc_id"],
                                       media_body=media).execute()
            except Exception as err:
                if not _doc_gone(err):
                    raise
                entry["doc_id"] = None  # deleted in Drive — recreate below
        if not entry.get("doc_id"):
            created = service.files().create(
                body={"name": doc_title, "mimeType": GDOC_MIME,
                      "parents": [_ensure_folder(manuscript, meta, service)]},
                media_body=media, fields="id",
            ).execute()
            entry["doc_id"] = created["id"]
            result["created"] = True
        result["doc_id"] = entry["doc_id"]
        result["url"] = f"https://docs.google.com/document/d/{entry['doc_id']}/edit"
    _save_mapping(db, manuscript, meta)
    return result


# ------------------------------------------------ publishing (docx / epub / pdf)

SETTINGS_KEYS = {
    "title": "book title (metadata + output filename); default: the "
             "manuscript name",
    "variant": "illustration handling: images (embed picked candidates) | "
               "slots (keep [Illustration: …] tags as production notes) | "
               "stripped (remove tags; with `export md`, also the "
               "audio-clean build — regions and display-math drop)",
    "language": "publication language code (epub metadata), default en",
    "reference_docx": "path to a pandoc reference .docx for Word styling "
                      "(fonts, margins); empty = pandoc defaults",
    "cover_image": "path to an epub cover image; empty = no cover",
    "pdf_engine": "pandoc --pdf-engine for pdf export, default xelatex "
                  "(pdflatex cannot set the manuscript's unicode)",
    "pdf_font": "main font for pdf export (a wide-coverage face such as "
                "'STIX Two Text'); empty = pandoc/LaTeX default",
    "pdf_mathfont": "math font for pdf export (unicode-math), default "
                    "'STIX Two Math' — the companion of STIX Two Text; "
                    "empty = LaTeX default",
    "continuation_level": "heading level (1-6) at or below which a file "
                          "continues the essay before it: a file whose "
                          "first heading is this deep or deeper gets no "
                          "page break and joins the previous audiobook "
                          "chapter; empty = every file starts anew",
}
_SETTINGS_DEFAULTS = {"title": "", "variant": "images",
                      "language": "en", "reference_docx": "",
                      "cover_image": "", "pdf_engine": "xelatex",
                      "pdf_font": "", "pdf_mathfont": "STIX Two Math",
                      "continuation_level": ""}


def continuation_level(manuscript: dict) -> int | None:
    """The manuscript's continuation_level setting as a number, or None."""
    value = str(load_settings(manuscript)["continuation_level"]).strip()
    return int(value) if value else None


def _settings_path(manuscript: dict) -> Path:
    return Path(manuscript["path"]) / EXPORT_DIR / "settings.toml"


def _toml_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def load_settings(manuscript: dict) -> dict:
    """Per-manuscript export settings: _exports/settings.toml over
    defaults. Deterministic tooling — the file is the state, and the
    author may edit it directly."""
    import tomllib

    settings = dict(_SETTINGS_DEFAULTS)
    path = _settings_path(manuscript)
    if path.exists():
        loaded = tomllib.loads(path.read_text(encoding="utf-8"))
        for key, value in loaded.items():
            if key in settings:
                settings[key] = str(value)
    return settings


def set_setting(manuscript: dict, key: str, value: str) -> dict:
    if key not in SETTINGS_KEYS:
        raise LookupError(
            f"unknown export setting '{key}' (one of: "
            f"{', '.join(sorted(SETTINGS_KEYS))})")
    if key == "variant" and value not in ("images", "slots", "stripped"):
        raise ValueError("variant must be images | slots | stripped")
    if key == "continuation_level" and value.strip() not in (
            "", "1", "2", "3", "4", "5", "6"):
        raise ValueError("continuation_level must be a heading level "
                         "1-6, or empty to turn it off")
    settings = load_settings(manuscript)
    settings[key] = value
    path = _settings_path(manuscript)
    path.parent.mkdir(exist_ok=True)
    lines = ["# AuthorLM export settings — 'authorlm export set <key> "
             "<value>', or edit directly."]
    for name in _SETTINGS_DEFAULTS:
        lines.append(f'{name} = "{_toml_escape(settings[name])}"')
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return settings


def publish_markdown(manuscript: dict, variant: str,
                     only: list[str] | None = None,
                     semantic_sections: bool = False,
                     fmt: str = "md",
                     extra: dict[str, str] | None = None,
                     ) -> tuple[str, list[str], list[str]]:
    """The publishable single-file markdown with illustration slots
    resolved per the variant and [Omit:]/[Only:] regions resolved for
    the build's outputs (publish_outputs(fmt, variant)); an audio build
    also drops display math. `semantic_sections` wraps each source file
    with format-neutral Pandoc roles; plain Markdown exports remain wrapper-free.
    `only` narrows it to the named chapters and their TOC descendants —
    a part of the book, built exactly like the whole. `extra` adds files
    observation does not see (a one-off excerpt under _drafts/) so that
    `only` can name them.
    Returns (text, order, warnings)."""
    from .illus import (ILLUS_DIR, capture_embeds, parse_tag,
                        slot_candidates, slot_key)
    from .media import strip_for_book

    root = Path(manuscript["path"])
    files = read_manuscript_files(root)
    if extra:
        files.update(extra)
    if only:
        order = select_chapters(files, only)
    else:
        order, _unlisted = reading_order(files)
    from .api import is_placeholder
    from .structure import matter_map

    matter = matter_map(files)
    level = continuation_level(manuscript)
    outputs = publish_outputs(fmt, variant)
    author = (manuscript.get("author") or "").strip() or (
        manuscript.get("copyright_owner") or "").strip()
    warnings: list[str] = []
    parts: list[str] = []
    tokens = footnote_prefixes(order)
    for name in order:
        if is_placeholder(files[name]):
            # A mid-rewrite essay used to disappear from the exported
            # book in SILENCE (the `if text:` below dropped the empty
            # file). The marker must never reach a reader either, so it
            # is still omitted — but named.
            warnings.append(f"{name}: mid-rewrite (a writeup is open) — "
                            "omitted from the export")
            continue
        text = resolve_regions(normalize_markdown(files[name]), outputs, name)
        # Clip, Collage and Montage tags are podcast-only (docs/video-clips-design.md
        # §6): the tag lines go, every word of prose stays.
        text, _media_tags = strip_for_book(text, name)
        text = strip_voice_lines(text)
        text = resolve_signatures(text, root, author, outputs)
        # An unresolved [Footnote: …] or [Explain: …] tag never reaches
        # a reader: a shorthand instruction inside a sentence reads as
        # damage, and a reviewer cannot act on it (footnote-directive
        # design §6). Stripped and named, never kept as a production
        # note the way an illustration slot is.
        text, unresolved = directives.strip_tags(text)
        for tag in unresolved:
            warnings.append(f"{name}: unresolved [{tag['kind'].title()}: "
                            f"{tag['gist'][:60]}] stripped — draft it in "
                            f"chat and '{tag['kind']} apply'")
        if "audio" in outputs:
            text = strip_display_math(text, name)
        text = normalize_markdown(text).rstrip("\n")
        if variant != "slots":
            raw = (root / name).read_text(encoding="utf-8")
            picks = capture_embeds(raw)
            lines: list[str] = []
            for line in text.split("\n"):
                tag = parse_tag(line)
                if not tag:
                    lines.append(line)
                    continue
                if variant == "stripped":
                    continue
                key = slot_key(tag)
                target = picks.get(key) if key else None
                if target and not (root / ILLUS_DIR / target).exists():
                    target = None
                if target is None:
                    cands = slot_candidates(root, key)
                    target = cands[-1]["name"] if cands else None
                if target is None:
                    warnings.append(
                        f"{name}: unrendered slot kept as a note — "
                        f"[Illustration: {tag['prompt'][:60]}]")
                    lines.append(line)
                else:
                    caption = tag.get("caption") or ""
                    lines.append(f"![{caption}]({ILLUS_DIR}/{target})")
            text = normalize_markdown("\n".join(lines)).rstrip("\n")
        if text:
            text = namespace_footnotes(text, tokens[name])
            if semantic_sections:
                role = ("authorlm-title-page" if name == "title.md"
                        else "authorlm-essay")
                if is_continuation(name, files[name], level):
                    role += " .authorlm-continuation"
                text = (f"::: {{.authorlm-file .{role} "
                        f".authorlm-matter-{matter.get(name, 'main')}}}\n"
                        f"{text}\n:::")
            parts.append(text)
    combined = "\n\n".join(parts)
    return (combined + "\n" if combined else ""), order, warnings


def check_manuscript(manuscript: dict) -> list[str]:
    """Pre-export lint, as 'file: what' lines. Every content file's
    region tags must resolve for every output, and every math span must
    convert through pandoc's TeX reader — the one EPUB (MathML), DOCX
    and the export Doc (Word equations) all depend on; what it refuses
    is outside the portable subset (docs/math-and-physics-guidelines.md
    §2). A PDF would still build such an equation, which is exactly why
    the check exists."""
    import os
    import shutil
    import subprocess

    files = read_manuscript_files(Path(manuscript["path"]))
    order, _unlisted = reading_order(files)
    problems: list[str] = []
    have_pandoc = shutil.which("pandoc") is not None
    if not have_pandoc:
        problems.append("pandoc is not installed — math spans unchecked "
                        "(brew install pandoc)")
    for name in order:
        text = normalize_markdown(files[name])
        for output in OUTPUTS:
            try:
                resolve_regions(text, {output}, name)
            except ValueError as err:
                problems.append(str(err))
                break
        # Clip, Collage and Montage structure (docs/video-clips-design.md §8i).
        from .media import scan_text as media_scan

        items, media_problems = media_scan(text)
        problems.extend(f"{name}: {p}" for p in media_problems)
        problems.extend(f"{name}:{it['line']}: {e}" for it in items
                        for e in it["errors"])
        # Display-math structure is independent of output: an unclosed
        # $$ would silently truncate the audio-clean export, so the
        # check refuses it the same way it refuses a stray [/Omit].
        try:
            strip_display_math(text, name)
        except ValueError as err:
            problems.append(str(err))
        if have_pandoc and "$" in text:
            proc = subprocess.run(
                ["pandoc", "-f", "markdown+smart+footnotes", "-t", "html",
                 "--mathml", "-o", os.devnull],
                input=text, capture_output=True, text=True)
            for chunk in proc.stderr.split("Could not convert TeX math")[1:]:
                tex = " ".join(chunk.split(", rendering as TeX")[0].split())
                problems.append(f"{name}: math outside the portable subset "
                                f"— {tex[:90]}")
    return problems


def selection_slug(order: list[str]) -> str:
    """Filename tag for a chapter selection — stems joined so a part-build
    never overwrites the whole-book artifacts or another part-build.

    Short selections stay fully readable. When the join would exceed the
    filename budget, keep a readable head and append a content digest so
    distinct selections (same first three stems, or long stems truncated
    to the same prefix) cannot collide."""
    stems = [Path(name).stem for name in order]
    full = "+".join(stems)
    if len(full) <= 60:
        return full
    digest = hashlib.sha256(full.encode("utf-8")).hexdigest()[:8]
    suffix = f"+more-{digest}" if len(stems) > 3 else f"-{digest}"
    budget = 60 - len(suffix)
    head = ("+".join(stems[:3]) if len(stems) > 3 else full)[:budget]
    return head.rstrip("+") + suffix


# ------------------------------------------------ the book profile (print interior)

PDF_PROFILES = ("review", "print", "book")

# KDP paperback interior: the INSIDE margin minimum grows with the page
# count (the gutter the binding eats); outside/top/bottom minimum is
# 0.25in without bleed, 0.375in with. Values in inches.
KDP_GUTTER = ((150, 0.375), (300, 0.5), (500, 0.625), (700, 0.75),
              (828, 0.875))
KDP_MIN_PAGES, KDP_MAX_PAGES = 24, 828
BLEED_IN = 0.125


def kdp_gutter(pages: int | None) -> float:
    """The inside-margin minimum for a page count; an unknown count
    assumes the 151–300 band (this manuscript's size) and the export
    says so."""
    if pages is None:
        return 0.5
    for limit, gutter in KDP_GUTTER:
        if pages <= limit:
            return gutter
    return KDP_GUTTER[-1][1]


def book_geometry(trim_width: float, trim_height: float, bleed: bool,
                  pages: int | None) -> str:
    """The geometry-package options for a print interior at this trim.
    Inside = KDP gutter + 0.375in of breathing room; outside 0.625in;
    top/bottom 0.75in (the running head and folio live inside them).
    With bleed the page grows 0.125in on the three outer sides and the
    outer margins grow with it, so the type area stays put."""
    extra = BLEED_IN if bleed else 0.0
    inner = kdp_gutter(pages) + 0.375
    return (f"paperwidth={trim_width + extra:g}in,"
            f"paperheight={trim_height + 2 * extra:g}in,"
            f"inner={inner:g}in,outer={0.625 + extra:g}in,"
            f"top={0.75 + extra:g}in,bottom={0.75 + extra:g}in,"
            f"headsep=0.2in,footskip=0.4in")


def pdf_page_count(path: Path) -> int | None:
    try:
        from pypdf import PdfReader
        return len(PdfReader(str(path)).pages)
    except Exception:  # noqa: BLE001 — a count we cannot read is None
        return None


def _latex_escape(text: str) -> str:
    table = {"\\": r"\textbackslash{}", "{": r"\{", "}": r"\}",
             "%": r"\%", "$": r"\$", "#": r"\#", "&": r"\&", "_": r"\_",
             "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}
    return "".join(table.get(ch, ch) for ch in text)


def kdp_checks(manuscript: dict, pages: int | None) -> list[str]:
    """What KDP will ask for that the build cannot supply on its own.
    Warnings, never refusals: the book still builds."""
    from .api import KDP_TRIM_SIZES

    notes: list[str] = []
    trim = (float(manuscript.get("trim_width") or 0),
            float(manuscript.get("trim_height") or 0))
    if not any(abs(trim[0] - w) < 0.005 and abs(trim[1] - h) < 0.005
               for w, h in KDP_TRIM_SIZES):
        notes.append(f"trim {trim[0]:g}x{trim[1]:g}in is not a standard "
                     "KDP size — custom sizes print, but cover templates "
                     "and expanded distribution favour the standard list")
    if not manuscript.get("paperback_isbn"):
        notes.append("no paperback ISBN on the manuscript — KDP assigns a "
                     "free one at upload, or set --paperback-isbn to use "
                     "your own (it then belongs on the copyright page)")
    if pages is None:
        notes.append("page count unreadable — gutter assumed for 151–300 "
                     "pages; check it against the finished PDF")
    elif pages < KDP_MIN_PAGES:
        notes.append(f"{pages} pages — KDP needs at least {KDP_MIN_PAGES}")
    elif pages > KDP_MAX_PAGES:
        notes.append(f"{pages} pages — over KDP's {KDP_MAX_PAGES}-page "
                     "maximum for this trim")
    return notes


def export_published(db: Database, manuscript: dict, fmt: str,
                     variant: str | None = None,
                     only: list[str] | None = None,
                     print_ready: bool = False,
                     profile: str | None = None,
                     file: str | None = None,
                     notice: str | None = None) -> dict:
    """Publishing export: write the publishable markdown to _exports/
    and, for docx/epub/pdf, convert it locally with pandoc — images
    embed from _illustrations/, no Doc or Drive involved.

    `only` builds just the named chapters (with their TOC descendants)
    into separately-named files alongside the full-book export.

    `file` builds exactly one manuscript file on its own, TOC or no TOC
    (the papers road, nagarkar/authorllm#192): the same pipeline and
    settings as the book, output named by the file's stem, titled by
    its stem rather than the book. A standalone file carries its own
    title line, so no book title is prepended. Exclusive with `only`.
    `file` may also name a markdown file observation does not see, such
    as a one-off excerpt in _drafts/: it is built, never collected.

    `notice` adds a recipient-specific line to a review PDF ("provided to
    X solely for ...") on the notice page and in every page footer,
    beside the standard confidential wording.

    PDF profiles: `review` (default — notice page, footer, watermark),
    `print` (the same page, review marks off; `print_ready=True`), and
    `book` — a print interior at the manuscript's trim size, built for
    KDP: book class, mirrored margins with the gutter chosen from the
    page count (two passes when it changes the band), running heads,
    captions under the plates, no review marks. The book profile
    refuses without a trim size: it is the one number nothing can
    default."""
    import shutil
    import subprocess

    if print_ready and fmt != "pdf":
        raise ValueError("--print-ready is only valid for PDF exports")
    if file and only:
        raise ValueError("--file and --chapters are exclusive")
    notice = (notice or "").strip()
    if notice and (fmt != "pdf" or print_ready or profile == "book"):
        raise ValueError("--notice is only valid for a review PDF "
                         "(not with --print-ready or --profile book)")
    if file:
        file = file.strip().lstrip("./")
        if not file.endswith(".md"):
            file += ".md"
        only = [file]
    if profile is not None and profile not in PDF_PROFILES:
        raise ValueError(f"unknown PDF profile '{profile}' — one of "
                         + ", ".join(PDF_PROFILES))
    if profile and fmt != "pdf":
        raise ValueError("--profile is only valid for PDF exports")
    profile = profile or ("print" if print_ready else "review")
    book = profile == "book"
    trim = (float(manuscript.get("trim_width") or 0),
            float(manuscript.get("trim_height") or 0))
    if book and not (trim[0] and trim[1]):
        raise RuntimeError(
            "the book profile needs the manuscript's trim size — set it "
            "with 'authorlm manuscript set --trim-size 6x9' (inches; "
            "KDP's standard list is in the docs)")
    bleed = bool(manuscript.get("bleed"))
    settings = load_settings(manuscript)
    variant = variant or settings["variant"] or "images"
    title = settings["title"] or manuscript["name"]
    review_copy = fmt == "pdf" and profile == "review"
    author = manuscript.get("author", "").strip()
    copyright_owner = manuscript.get("copyright_owner", "").strip()
    if review_copy and (not author or not copyright_owner):
        missing = [name for name, value in (
            ("author", author), ("copyright_owner", copyright_owner)
        ) if not value]
        raise RuntimeError(
            "review PDF needs manuscript metadata: " + ", ".join(missing)
            + " — set it with 'authorlm manuscript set'")
    root = Path(manuscript["path"])
    extra = None
    if file and file not in read_manuscript_files(root):
        candidate = (root / file).resolve()
        if (candidate.is_file() and candidate.suffix == ".md"
                and root.resolve() in candidate.parents):
            extra = {file: candidate.read_text(encoding="utf-8")}
    text, order, warnings = publish_markdown(
        manuscript, variant, only,
        semantic_sections=(fmt in ("pdf", "epub")), fmt=fmt, extra=extra,
    )
    if not order:
        raise LookupError("the manuscript has no content files to combine")
    if file:
        title = Path(file).stem
    elif only:
        title = f"{title} - {selection_slug(order)}"
    if book:
        title = f"{title} - book"  # never overwrites the review copy
    export_dir = root / EXPORT_DIR
    export_dir.mkdir(exist_ok=True)
    md_path = export_dir / export_filename(title)
    md_path.write_text(text, encoding="utf-8")
    result = {"markdown": str(md_path), "variant": variant,
              "files": order, "warnings": warnings}
    if fmt == "pdf":
        result["mode"] = profile
    if fmt == "md":
        return result

    if shutil.which("pandoc") is None:
        raise RuntimeError(
            "pandoc is required for docx/epub/pdf export — "
            "brew install pandoc")
    # Review and print-ready PDFs must not share a path: exporting one
    # mode after the other would silently overwrite the other artifact
    # (watermarked file sent to print, or clean file shared as "review").
    if fmt == "pdf":
        out_path = md_path.with_name(
            md_path.stem + (".review.pdf" if review_copy else ".print.pdf"))
    else:
        out_path = md_path.with_suffix(f".{fmt}")
    pandoc_cwd = root
    if fmt in ("pdf", "epub"):
        command = [
            "pandoc", str(md_path), "-o", str(out_path),
            "--defaults", str(PUBLICATION_DIR / "common.yaml"),
            "--defaults", str(PUBLICATION_DIR
                              / ("book.yaml" if book else f"{fmt}.yaml")),
            "--resource-path", str(root),
        ]
        # Pandoc resolves paths declared inside defaults files relative to
        # its working directory. Running at the packaged profile directory
        # keeps the profiles relocatable; resource-path keeps manuscript
        # images relative to the manuscript root.
        pandoc_cwd = PUBLICATION_DIR
    else:
        command = ["pandoc", str(md_path), "-o", str(out_path),
                   "--from", "markdown+smart", "--standalone"]
    copyright_line = (f"Copyright © {date.today().year} {copyright_owner}"
                      if copyright_owner else "")
    if author:
        command += ["--metadata", f"author={author}"]
    if copyright_line and fmt in ("docx", "pdf"):
        command += ["--metadata", f"subject={copyright_line}"]
    if fmt == "docx":
        # No metadata title block: title.md leads as front matter (the
        # ratified export shape) and pandoc would render a duplicate.
        if settings["reference_docx"]:
            command += ["--reference-doc", settings["reference_docx"]]
    if fmt == "pdf":
        if settings["pdf_engine"]:
            command += ["--pdf-engine", settings["pdf_engine"]]
        if settings["pdf_font"]:
            command += ["-V", f"mainfont={settings['pdf_font']}"]
        if settings["pdf_mathfont"]:
            command += ["-V", f"mathfont={settings['pdf_mathfont']}"]
        if review_copy:
            command += ["--metadata", "authorlm-review-copy=true",
                        "--metadata", f"copyright-owner={copyright_owner}",
                        "--metadata", f"copyright-year={date.today().year}"]
            if notice:
                command += ["--metadata", f"authorlm-review-notice={notice}"]
        if book:
            running = _latex_escape(settings["title"] or manuscript["name"])
            command += ["-V", "header-includes=\\providecommand{"
                        f"\\AuthorLMRunningBook}}{{{running}}}"]
    if fmt == "epub":
        command += ["--metadata", f"title={title}",
                    "--metadata", f"lang={settings['language'] or 'en'}"]
        if copyright_line:
            command += ["--metadata", f"rights={copyright_line}"]
        if settings["cover_image"]:
            cover = Path(settings["cover_image"])
            if not cover.is_absolute():
                cover = root / cover
            command += ["--epub-cover-image", str(cover)]
    def run_pandoc(extra: list[str]) -> None:
        proc = subprocess.run(command + extra, cwd=pandoc_cwd,
                              capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(
                f"pandoc failed: {proc.stderr.strip()[:400]}")

    if not book:
        run_pandoc([])
        result[fmt] = str(out_path)
        return result

    # Two passes at most: the gutter depends on the page count, and the
    # page count depends (slightly) on the gutter. Build at the assumed
    # band, read the count, rebuild only if the band moved.
    pages = None
    geometry = book_geometry(trim[0], trim[1], bleed, pages)
    run_pandoc(["-V", f"geometry={geometry}"])
    pages = pdf_page_count(out_path)
    final_geometry = book_geometry(trim[0], trim[1], bleed, pages)
    if final_geometry != geometry:
        geometry = final_geometry
        run_pandoc(["-V", f"geometry={geometry}"])
        pages = pdf_page_count(out_path) or pages
    result[fmt] = str(out_path)
    result["pages"] = pages
    result["geometry"] = geometry
    result["trim"] = f"{trim[0]:g}x{trim[1]:g}" + (" +bleed" if bleed else "")
    warnings.extend(kdp_checks(manuscript, pages))
    return result


# ------------------------------------------------ teleprompter

TELEPROMPTER_SUFFIX = ".teleprompter"
TELEPROMPTER_PT = 32        # body type; teleprompter apps reflow, print does not
TELEPROMPTER_LABEL_PT = 20
TELEPROMPTER_LINE = 360     # 240ths of a line: 1.5 spacing


def _file_arg(file: str) -> str:
    file = file.strip().lstrip("./")
    return file if file.endswith(".md") else file + ".md"


def teleprompter_passages(manuscript: dict, file: str) -> list[dict]:
    """The passages the author reads on camera in one file, in order:
    every clip marked `voice: author` (docs/video-clips-design.md §5),
    its text the clip's note (`_clips/notes/<key>.md`, paragraphs kept)
    or, unkeyed, its inline description. Labels are plain: `Intro` for a
    passage with nothing spoken before it, `Outro` for one with nothing
    spoken after, otherwise the Collage's title up to its first comma.
    Other clip problems (a missing `use:` file) do not matter here: the
    words are what the author needs."""
    from . import media

    root = Path(manuscript["path"])
    file = _file_arg(file)
    if not (root / file).is_file():
        raise LookupError(f"{file} is not in the manuscript folder")
    entries = media.list_media(root, [file])["media"]
    titles = {e["line"]: e["title"] for e in entries
              if e["kind"] == "collage"}
    passages: list[dict] = []
    for e in entries:
        if e["kind"] != "clip" or e["fields"].get("voice") != "author":
            continue
        text = e["description"]
        if e["spec_path"] and (root / e["spec_path"]).is_file():
            text = (root / e["spec_path"]).read_text(encoding="utf-8")
        paragraphs = [" ".join(p.split()) for p in re.split(r"\n\s*\n", text)
                      if p.strip()]
        if not e.get("before_sentence"):
            label = "Intro"
        elif not e.get("after_sentence"):
            label = "Outro"
        else:
            box = max((i for i in titles if i < e["line"]), default=None)
            label = (titles[box].split(",")[0].strip() if box is not None
                     else "") or f"Passage {len(passages) + 1}"
        passages.append({"label": label, "paragraphs": paragraphs,
                         "line": e["line"]})
    return passages


def _xml(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def write_teleprompter_docx(passages: list[dict], out_path: Path) -> None:
    """A bare WordprocessingML package, written by hand so nothing can
    slip in: large type, 1.5 line spacing, a plain bold label above each
    passage and a page break between passages. No header, no footer, no
    page numbers, no notice or watermark — none of those parts exist."""
    import zipfile

    size, label = TELEPROMPTER_PT * 2, TELEPROMPTER_LABEL_PT * 2

    def para(text: str, style: str, page_break: bool = False) -> str:
        # Formatting sits on every paragraph and run as well as in the
        # styles: importers that skip style inheritance (QuickLook, some
        # teleprompter apps) would otherwise show 12pt type.
        pt = label if style == "Label" else size
        ppr = (f'<w:pPr><w:pStyle w:val="{style}"/>'
               + ("<w:keepNext/>" if style == "Label" else "")
               + ("<w:pageBreakBefore/>" if page_break else "")
               + f'<w:spacing w:after="360" w:line="{TELEPROMPTER_LINE}" '
               'w:lineRule="auto"/></w:pPr>')
        rpr = ('<w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:cs="Arial"/>'
               + ("<w:b/>" if style == "Label" else "")
               + f'<w:sz w:val="{pt}"/><w:szCs w:val="{pt}"/></w:rPr>')
        return (f"<w:p>{ppr}<w:r>{rpr}<w:t xml:space=\"preserve\">"
                f"{_xml(text)}</w:t></w:r></w:p>")

    body = []
    for n, passage in enumerate(passages):
        body.append(para(passage["label"], "Label", page_break=n > 0))
        body.extend(para(p, "Normal") for p in passage["paragraphs"])
    w = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    document = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f"<w:document {w}><w:body>{''.join(body)}"
        '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/>'
        '<w:pgMar w:top="1080" w:right="1080" w:bottom="1080" w:left="1080" '
        'w:header="0" w:footer="0" w:gutter="0"/></w:sectPr>'
        "</w:body></w:document>")
    styles = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f"<w:styles {w}><w:docDefaults><w:rPrDefault><w:rPr>"
        '<w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:cs="Arial"/>'
        f'<w:sz w:val="{size}"/><w:szCs w:val="{size}"/></w:rPr></w:rPrDefault>'
        f'<w:pPrDefault><w:pPr><w:spacing w:after="360" '
        f'w:line="{TELEPROMPTER_LINE}" w:lineRule="auto"/></w:pPr>'
        "</w:pPrDefault></w:docDefaults>"
        '<w:style w:type="paragraph" w:default="1" w:styleId="Normal">'
        '<w:name w:val="Normal"/></w:style>'
        '<w:style w:type="paragraph" w:styleId="Label"><w:name w:val="Label"/>'
        '<w:basedOn w:val="Normal"/><w:next w:val="Normal"/>'
        '<w:pPr><w:keepNext/></w:pPr>'
        f'<w:rPr><w:b/><w:sz w:val="{label}"/><w:szCs w:val="{label}"/></w:rPr>'
        "</w:style></w:styles>")
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
        "</Types>")
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        "</Relationships>")
    doc_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        "</Relationships>")
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", document)
        z.writestr("word/_rels/document.xml.rels", doc_rels)
        z.writestr("word/styles.xml", styles)


def export_teleprompter(manuscript: dict, file: str) -> dict:
    """`export teleprompter --file F`: the author's on-camera passages of
    one file as `_exports/<stem>.teleprompter.docx` (what teleprompter
    apps import) and the same words as `.teleprompter.txt`. Stops with
    an error when the file marks no clip `voice: author`."""
    passages = teleprompter_passages(manuscript, file)
    stem = Path(_file_arg(file)).stem
    if not passages:
        raise LookupError(
            f"{stem}.md has no clip marked 'voice: author' — add "
            "'| voice: author' to the [Clip:] of each recorded intro or "
            "outro (a clip in a Collage)")
    export_dir = Path(manuscript["path"]) / EXPORT_DIR
    export_dir.mkdir(exist_ok=True)
    docx_path = export_dir / f"{stem}{TELEPROMPTER_SUFFIX}.docx"
    write_teleprompter_docx(passages, docx_path)
    txt_path = docx_path.with_suffix(".txt")
    txt_path.write_text("\n\n\n".join(
        "\n\n".join([p["label"], *p["paragraphs"]]) for p in passages) + "\n",
        encoding="utf-8")
    return {"docx": str(docx_path), "txt": str(txt_path),
            "passages": [p["label"] for p in passages], "warnings": []}


# ------------------------------------------------ upload to Drive

UPLOADS_KEY = "_uploads"    # metadata.gdocs key: output name → Drive file
UPLOAD_MIME = {".pdf": "application/pdf", ".epub": "application/epub+zip",
               ".md": "text/markdown", ".docx": DOCX_MIME,
               ".txt": "text/plain"}


def upload_export(db: Database, manuscript: dict, path: Path, service,
                  as_doc: bool = False, drive_id: str | None = None) -> dict:
    """Put one export file on the author's Drive, in the manuscript's
    folder, and remember its id per output (`metadata.gdocs._uploads`,
    beside the export Doc's `_export`): the next upload of the same file
    replaces it in place — same id, same link. A file deleted in Drive is
    created anew. `as_doc` converts a DOCX to a Google Doc (a separate
    Drive file from the plain .docx); `drive_id` adopts a file already on
    Drive (one uploaded by hand) as this output's target."""
    from googleapiclient.http import MediaFileUpload

    path = Path(path)
    if as_doc and path.suffix != ".docx":
        raise ValueError("--as-doc converts a DOCX to a Google Doc; this "
                         f"export is {path.suffix.lstrip('.')}")
    mime = UPLOAD_MIME.get(path.suffix, "application/octet-stream")
    key = path.name + (" (Google Doc)" if as_doc else "")
    meta = _mapping(db, manuscript)
    uploads = meta.setdefault("gdocs", {}).setdefault(UPLOADS_KEY, {})
    entry = uploads.setdefault(key, {})
    if drive_id:
        entry["file_id"] = drive_id
    media = MediaFileUpload(str(path), mimetype=mime, resumable=False)
    created, info = False, None
    if entry.get("file_id"):
        try:
            info = service.files().update(
                fileId=entry["file_id"], media_body=media,
                fields="id,webViewLink").execute()
        except Exception as err:
            if not _doc_gone(err):
                raise
            entry["file_id"] = None  # deleted in Drive — create below
    if not entry.get("file_id"):
        body = {"name": path.stem if as_doc else path.name,
                "parents": [_ensure_folder(manuscript, meta, service)]}
        if as_doc:
            body["mimeType"] = GDOC_MIME
        info = service.files().create(body=body, media_body=media,
                                      fields="id,webViewLink").execute()
        entry["file_id"] = info["id"]
        created = True
    entry["as_doc"] = as_doc
    _save_mapping(db, manuscript, meta)
    fid = entry["file_id"]
    url = (f"https://docs.google.com/document/d/{fid}/edit" if as_doc
           else (info or {}).get("webViewLink")
           or f"https://drive.google.com/file/d/{fid}/view")
    return {"file_id": fid, "url": url, "created": created, "name": key}
