"""Single-file manuscript export — a transient, regenerate-on-demand artifact.

`doc create-manuscript` combines every content file (reading order per
toc.md) into `_exports/<Manuscript Name>.md` and mirrors it to one Google
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
from .structure import reading_order

EXPORT_DIR = "_exports"
EXPORT_KEY = "_export"  # metadata.gdocs key; '_' keeps reconcile/pull away
_UNSAFE = re.compile(r'[\\/:*?"<>|#]')


def export_filename(name: str) -> str:
    cleaned = _UNSAFE.sub("-", name.strip()).strip()
    if not cleaned:
        raise ValueError("empty manuscript name")
    return cleaned + ".md"


def combined_markdown(manuscript: dict) -> tuple[str, list[str], list[str]]:
    """(combined text, ordered file names, files missing from toc.md).

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
