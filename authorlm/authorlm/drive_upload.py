"""Export files on the author's Drive: the export Doc and per-output uploads.

Both remember their Drive ids in the manuscript's `metadata.gdocs` and replace
the same file in place on every later run; a file deleted in Drive is created
anew. `export` below this module writes the files locally and knows nothing of
Drive or of that metadata; `export_manuscript` is the one workflow that wraps
its local half, because the name of the last combined file is kept beside the
Doc id even when no service is given.
"""

from __future__ import annotations

from pathlib import Path

from .db import Database
from .export import DOCX_MIME, build_docx, load_settings, write_combined_export
from .gdocs import GDOC_MIME, _ensure_folder, _mapping, _save_mapping

EXPORT_KEY = "_export"  # metadata.gdocs key; '_' keeps reconcile/pull away


def _doc_gone(err: Exception) -> bool:
    return getattr(getattr(err, "resp", None), "status", None) == 404


def export_manuscript(db: Database, manuscript: dict, service=None,
                      title: str | None = None) -> dict:
    """Write the combined markdown to _exports/
    (`export.write_combined_export`) and, given a Drive service, build it
    to DOCX with pandoc and create-or-update the manuscript's single
    export Doc from that.
    Drive turns Word equations, footnotes, and images into native Doc
    objects — none of which the markdown importer carries
    (docs/doc-bridge-markdown-vs-docx.md). A Doc the author deleted by
    hand is recreated (transient artifacts carry no history)."""
    meta = _mapping(db, manuscript)
    entry = meta.setdefault("gdocs", {}).setdefault(EXPORT_KEY, {})
    local = write_combined_export(manuscript, title,
                                  stale=entry.get("local_file"))
    path = Path(local["path"])
    doc_title = local["doc_title"]
    entry["local_file"] = path.name

    result = {**local, "doc_id": None, "url": None, "created": False}
    if service is not None:
        import shutil

        from googleapiclient.http import MediaFileUpload

        if shutil.which("pandoc") is None:
            raise RuntimeError("pandoc is required to build the export Doc "
                               "— brew install pandoc")
        docx_path = path.with_suffix(".docx")
        build_docx(manuscript, path, docx_path, load_settings(manuscript))
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
