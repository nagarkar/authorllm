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

import re
from pathlib import Path

from .db import Database
from .gdocs import (GDOC_MIME, MARKDOWN_MIME, _ensure_folder, _mapping,
                    _save_mapping, normalize_markdown)
from .revisions import read_manuscript_files
from .structure import reading_order, select_chapters

EXPORT_DIR = "_exports"
EXPORT_KEY = "_export"  # metadata.gdocs key; '_' keeps reconcile/pull away
_UNSAFE = re.compile(r'[\\/:*?"<>|#]')


def export_filename(name: str) -> str:
    cleaned = _UNSAFE.sub("-", name.strip()).strip()
    if not cleaned:
        raise ValueError("empty manuscript name")
    return cleaned + ".md"


def combined_markdown(manuscript: dict) -> tuple[str, list[str], list[str]]:
    """(combined text, ordered file names, files missing from toc.toml).

    Pure concatenation of the normalized content files in reading order —
    no added headings or separators; the combination is mechanical, the
    prose stays exactly the author's."""
    files = read_manuscript_files(Path(manuscript["path"]))
    order, unlisted = reading_order(files)
    parts = [normalize_markdown(files[name]).rstrip("\n") for name in order]
    text = "\n\n".join(part for part in parts if part)
    return (text + "\n" if text else ""), order, unlisted


def _doc_gone(err: Exception) -> bool:
    return getattr(getattr(err, "resp", None), "status", None) == 404


def export_manuscript(db: Database, manuscript: dict, service=None,
                      title: str | None = None) -> dict:
    """Write the combined markdown to _exports/ and, given a Drive service,
    create-or-update the manuscript's single export Doc. A Doc the author
    deleted by hand is recreated (transient artifacts carry no history)."""
    text, order, unlisted = combined_markdown(manuscript)
    if not order:
        raise LookupError("the manuscript has no content files to combine")
    doc_title = title or manuscript["name"]
    export_dir = Path(manuscript["path"]) / EXPORT_DIR
    export_dir.mkdir(exist_ok=True)
    path = export_dir / export_filename(doc_title)

    meta = _mapping(db, manuscript)
    entry = meta.setdefault("gdocs", {}).setdefault(EXPORT_KEY, {})
    stale = entry.get("local_file")
    if stale and stale != path.name:
        (export_dir / stale).unlink(missing_ok=True)
    entry["local_file"] = path.name
    path.write_text(text, encoding="utf-8")

    result = {
        "path": str(path), "files": order, "unlisted": unlisted,
        "doc_title": doc_title, "doc_id": None, "url": None, "created": False,
    }
    if service is not None:
        from googleapiclient.http import MediaInMemoryUpload

        media = MediaInMemoryUpload(text.encode("utf-8"), mimetype=MARKDOWN_MIME)
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


# ------------------------------------------------ publishing (docx / epub)

SETTINGS_KEYS = {
    "title": "book title (metadata + output filename); default: the "
             "manuscript name",
    "author": "author byline (docx/epub metadata)",
    "variant": "illustration handling: images (embed picked candidates) | "
               "slots (keep [Illustration: …] tags as production notes) | "
               "stripped (remove tags — audio-clean)",
    "language": "publication language code (epub metadata), default en",
    "reference_docx": "path to a pandoc reference .docx for Word styling "
                      "(fonts, margins); empty = pandoc defaults",
    "cover_image": "path to an epub cover image; empty = no cover",
    "pdf_engine": "pandoc --pdf-engine for pdf export, default xelatex "
                  "(pdflatex cannot set the manuscript's unicode)",
    "pdf_font": "main font for pdf export (a wide-coverage face such as "
                "'STIX Two Text'); empty = pandoc/LaTeX default",
}
_SETTINGS_DEFAULTS = {"title": "", "author": "", "variant": "images",
                      "language": "en", "reference_docx": "",
                      "cover_image": "", "pdf_engine": "xelatex",
                      "pdf_font": ""}


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
                settings[key] = value
    return settings


def set_setting(manuscript: dict, key: str, value: str) -> dict:
    if key not in SETTINGS_KEYS:
        raise LookupError(
            f"unknown export setting '{key}' (one of: "
            f"{', '.join(sorted(SETTINGS_KEYS))})")
    if key == "variant" and value not in ("images", "slots", "stripped"):
        raise ValueError("variant must be images | slots | stripped")
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
                     only: list[str] | None = None
                     ) -> tuple[str, list[str], list[str]]:
    """The publishable single-file markdown: the machinery-free
    concatenation with illustration slots resolved per the variant.
    `only` narrows it to the named chapters and their TOC descendants —
    a part of the book, built exactly like the whole.
    Returns (text, order, warnings)."""
    from .illus import (ILLUS_DIR, capture_embeds, desc_hash, parse_tag,
                        slot_candidates)

    root = Path(manuscript["path"])
    files = read_manuscript_files(root)
    if only:
        order = select_chapters(files, only)
    else:
        order, _unlisted = reading_order(files)
    warnings: list[str] = []
    parts: list[str] = []
    for name in order:
        text = normalize_markdown(files[name]).rstrip("\n")
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
                h = desc_hash(tag["prompt"])
                target = picks.get(h)
                if target and not (root / ILLUS_DIR / target).exists():
                    target = None
                if target is None:
                    cands = slot_candidates(root, h)
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
            parts.append(text)
    combined = "\n\n".join(parts)
    return (combined + "\n" if combined else ""), order, warnings


def selection_slug(order: list[str]) -> str:
    """Filename tag for a chapter selection — the selected files' stems,
    joined, so a part-build never overwrites the whole-book artifacts."""
    stems = [Path(name).stem for name in order]
    slug = "+".join(stems[:3]) + ("+more" if len(stems) > 3 else "")
    return slug[:60]


def export_published(db: Database, manuscript: dict, fmt: str,
                     variant: str | None = None,
                     only: list[str] | None = None) -> dict:
    """Publishing export: write the publishable markdown to _exports/
    and, for docx/epub/pdf, convert it locally with pandoc — images
    embed from _illustrations/, no Doc or Drive involved.

    `only` builds just the named chapters (with their TOC descendants)
    into separately-named files alongside the full-book export."""
    import shutil
    import subprocess

    settings = load_settings(manuscript)
    variant = variant or settings["variant"] or "images"
    title = settings["title"] or manuscript["name"]
    text, order, warnings = publish_markdown(manuscript, variant, only)
    if not order:
        raise LookupError("the manuscript has no content files to combine")
    if only:
        title = f"{title} - {selection_slug(order)}"
    root = Path(manuscript["path"])
    export_dir = root / EXPORT_DIR
    export_dir.mkdir(exist_ok=True)
    md_path = export_dir / export_filename(title)
    md_path.write_text(text, encoding="utf-8")
    result = {"markdown": str(md_path), "variant": variant,
              "files": order, "warnings": warnings}
    if fmt == "md":
        return result

    if shutil.which("pandoc") is None:
        raise RuntimeError(
            "pandoc is required for docx/epub/pdf export — "
            "brew install pandoc")
    out_path = md_path.with_suffix(f".{fmt}")
    command = ["pandoc", str(md_path), "-o", str(out_path),
               "--from", "markdown+smart", "--standalone"]
    if fmt == "docx":
        # No metadata title block: title.md leads as front matter (the
        # ratified export shape) and pandoc would render a duplicate.
        if settings["reference_docx"]:
            command += ["--reference-doc", settings["reference_docx"]]
    if fmt == "pdf":
        # xelatex, not the pdflatex default: the prose carries arrows,
        # diacritics, and Greek that 8-bit TeX cannot set.
        command += ["--pdf-engine", settings["pdf_engine"] or "xelatex"]
        if settings["pdf_font"]:
            command += ["-V", f"mainfont={settings['pdf_font']}"]
    if fmt == "epub":
        command += ["--metadata", f"title={title}",
                    "--metadata", f"lang={settings['language'] or 'en'}"]
        if settings["author"]:
            command += ["--metadata", f"author={settings['author']}"]
        if settings["cover_image"]:
            command += ["--epub-cover-image", settings["cover_image"]]
    proc = subprocess.run(command, cwd=root, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"pandoc failed: {proc.stderr.strip()[:400]}")
    result[fmt] = str(out_path)
    return result
