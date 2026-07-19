"""Google Docs bridge — markdown only, local files remain the system of
record.

`doc push <file>` uploads a manuscript file as a Google Doc (creating or
updating a linked Doc whose id lives in the manuscript metadata) and marks
it *checked out*: you're editing in Docs now. `doc pull <file>` exports the
Doc as markdown, runs the normalizer, writes the file, clears the checkout,
and collects — so transitions reflect real prose changes, not export churn.

The normalizer is the load-bearing piece: it makes a no-op round trip
byte-stable (collect says "No changes"), which keeps observation noise and
the proposal attention gate quiet. It runs on push (canonicalizing the
local file first) and on pull (canonicalizing Google's export quirks:
backslash escapes, CRLF, NBSP, bullet markers, blank-line runs).

Scope: https://www.googleapis.com/auth/drive.file — this app only ever
sees files it created. OAuth token cached at ~/.authorlm/gdocs_token.json.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .db import Database, loads
from .revisions import iter_manuscript_paths

SCOPES = ["https://www.googleapis.com/auth/drive.file"]
GDOC_MIME = "application/vnd.google-apps.document"
MARKDOWN_MIME = "text/markdown"


# ------------------------------------------------------------- normalizer

_ESCAPE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!>~|])")
_BULLET = re.compile(r"^(\s*)\*\s+", re.MULTILINE)
_HEADING = re.compile(r"^(#{1,6})\s+", re.MULTILINE)


def normalize_markdown(text: str) -> str:
    """Canonical markdown so identical prose is byte-identical regardless
    of which editor (local, Obsidian, Google Docs export) produced it.
    Idempotent: normalize(normalize(x)) == normalize(x)."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace(" ", " ")           # NBSP → space
    text = _ESCAPE.sub(r"\1", text)              # Docs-export backslash escapes
    text = _BULLET.sub(r"\1- ", text)            # '*' bullets → '-'
    text = _HEADING.sub(lambda m: m.group(1) + " ", text)
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    text = re.sub(r"\n{3,}", "\n\n", text)       # collapse blank-line runs
    text = text.strip("\n")
    return text + "\n" if text else ""


# ------------------------------------------------------------------- auth

def get_credentials(config: dict, workspace: str | None = None,
                    interactive: bool = True):
    """Cached-token OAuth (installed-app flow). With interactive=True the
    first call opens a consent browser; with interactive=False a missing or
    invalid token raises instead — scripts, probes, and agent-driven calls
    must never be able to pop a consent window as a side effect."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    base = Path(workspace).resolve() if workspace else Path.home()
    token_path = base / ".authorlm" / "gdocs_token.json"
    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token_path.write_text(creds.to_json())
    if not creds or not creds.valid:
        if not interactive:
            raise ValueError(
                "no valid Google token and interactive consent is disabled — "
                "run 'authorlm doc auth' first"
            )
        client_secret = config.get("gdocs", {}).get("client_secret")
        if not client_secret or not Path(client_secret).exists():
            raise ValueError(
                "Google OAuth is not configured. Add to config.toml:\n"
                "  [gdocs]\n"
                '  client_secret = "/path/to/client_secret_….json"'
            )
        from google_auth_oauthlib.flow import InstalledAppFlow

        flow = InstalledAppFlow.from_client_secrets_file(client_secret, SCOPES)
        creds = flow.run_local_server(port=0)
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(creds.to_json())
    return creds


def get_service(config: dict, workspace: str | None = None,
                interactive: bool = True):
    from googleapiclient.discovery import build

    return build(
        "drive", "v3",
        credentials=get_credentials(config, workspace, interactive=interactive),
        cache_discovery=False,
    )


# ---------------------------------------------------------------- mapping

def _mapping(db: Database, manuscript: dict) -> dict:
    return loads(
        db.one("SELECT metadata FROM manuscripts WHERE id = ?",
               (manuscript["id"],))["metadata"], {}
    )


def _save_mapping(db: Database, manuscript: dict, meta: dict) -> None:
    db.update("manuscripts", manuscript["id"], {"metadata": json.dumps(meta)})


def doc_status(db: Database, manuscript: dict) -> dict:
    """relpath → {doc_id, checked_out, …} for every linked file."""
    return _mapping(db, manuscript).get("gdocs", {})


def _resolve(manuscript: dict, query: str) -> tuple[str, Path]:
    from .docs import _match

    candidates = iter_manuscript_paths(Path(manuscript["path"]))
    path = _match(candidates, query)
    relpath = str(path.relative_to(Path(manuscript["path"])))
    return relpath, path


# --------------------------------------------------------------- push/pull

FOLDER_MIME = "application/vnd.google-apps.folder"


def _ensure_folder(manuscript: dict, meta: dict, service) -> str:
    """One Drive folder per manuscript ('AuthorLM — <name>'), created on
    first push and remembered by id. Moving or renaming it later is fine —
    everything is id-based."""
    entry = meta.setdefault("gdocs", {})
    if entry.get("_folder_id"):
        return entry["_folder_id"]
    result = service.files().create(
        body={"name": f"AuthorLM — {manuscript['name']}", "mimeType": FOLDER_MIME},
        fields="id",
    ).execute()
    entry["_folder_id"] = result["id"]
    return result["id"]


def push_doc(db: Database, manuscript: dict, query: str,
             title: str | None = None, service=None) -> dict:
    """Normalize the local file, then create/update its linked Google Doc
    from the markdown. Marks the file checked out to Docs."""
    from googleapiclient.http import MediaInMemoryUpload

    relpath, path = _resolve(manuscript, query)
    text = path.read_text(encoding="utf-8")
    normalized = normalize_markdown(text)
    locally_normalized = normalized != text
    if locally_normalized:
        path.write_text(normalized, encoding="utf-8")

    meta = _mapping(db, manuscript)
    entry = meta.setdefault("gdocs", {}).setdefault(relpath, {})
    media = MediaInMemoryUpload(normalized.encode("utf-8"), mimetype=MARKDOWN_MIME)
    created = False
    if entry.get("doc_id"):
        service.files().update(fileId=entry["doc_id"], media_body=media).execute()
    else:
        folder_id = _ensure_folder(manuscript, meta, service)
        result = service.files().create(
            body={"name": title or Path(relpath).stem, "mimeType": GDOC_MIME,
                  "parents": [folder_id]},
            media_body=media, fields="id",
        ).execute()
        entry["doc_id"] = result["id"]
        created = True
    entry["checked_out"] = True
    # Snapshot what was pushed: pull uses it to detect two-sided edits.
    import hashlib

    entry["pushed_hash"] = hashlib.sha256(normalized.encode()).hexdigest()[:16]
    _save_mapping(db, manuscript, meta)
    return {
        "relpath": relpath,
        "doc_id": entry["doc_id"],
        "url": f"https://docs.google.com/document/d/{entry['doc_id']}/edit",
        "created": created,
        "locally_normalized": locally_normalized,
    }


def pull_doc(db: Database, manuscript: dict, query: str, service=None,
             force: bool = False) -> dict:
    """Export the linked Doc as markdown, normalize, and write the local
    file. Clears the checkout. Caller collects afterwards.

    Conflict guard: if the local file changed since the push AND the Doc's
    content differs from it, both sides were edited — refuse unless
    `force` (Doc wins; the local edits remain in version history)."""
    import hashlib

    relpath, path = _resolve(manuscript, query)
    meta = _mapping(db, manuscript)
    entry = meta.get("gdocs", {}).get(relpath)
    if not entry or not entry.get("doc_id"):
        raise LookupError(f"'{relpath}' has no linked Google Doc — push it first")
    data = service.files().export(
        fileId=entry["doc_id"], mimeType=MARKDOWN_MIME
    ).execute()
    text = normalize_markdown(
        data.decode("utf-8") if isinstance(data, bytes) else str(data)
    )
    current = path.read_text(encoding="utf-8") if path.exists() else ""
    changed = text != current
    local_hash = hashlib.sha256(current.encode()).hexdigest()[:16]
    pushed_hash = entry.get("pushed_hash")
    if (changed and not force and pushed_hash
            and local_hash != pushed_hash):
        raise ValueError(
            f"conflict: {relpath} was edited locally since the push, and the "
            "Google Doc also changed. Reconcile by hand (doc open + local "
            "editor), or 'doc pull --force' to take the Doc's version — your "
            "local edits stay recoverable in version history"
        )
    if changed:
        path.write_text(text, encoding="utf-8")
    entry["checked_out"] = False
    # The pulled content is the new agreed base for three-way comparison.
    entry["pushed_hash"] = hashlib.sha256(text.encode()).hexdigest()[:16]
    _save_mapping(db, manuscript, meta)
    return {"relpath": relpath, "doc_id": entry["doc_id"], "changed": changed}


def reconcile(db: Database, manuscript: dict, service) -> dict:
    """Session-start reconciliation over every linked file, using the
    three-way state (agreed base / local / Doc):

    - Doc == local            → in sync (checkout cleared)
    - only the Doc changed    → auto-pull (safe: local matches the base)
    - only local changed      → auto-push (safe: Doc matches the base)
    - both changed            → CONFLICT — touch nothing, report loudly
    - no base recorded + drift → treated as a conflict (can't judge safety)

    Per-file errors are collected, never raised — reconciliation must not
    block a writing session."""
    import hashlib as _hashlib

    from googleapiclient.http import MediaInMemoryUpload

    report: dict = {"in_sync": [], "pulled": [], "pushed": [],
                    "conflicts": [], "errors": []}
    meta = _mapping(db, manuscript)
    links = meta.get("gdocs", {})
    dirty = False
    for relpath, entry in links.items():
        if relpath.startswith("_") or not isinstance(entry, dict) \
                or not entry.get("doc_id"):
            continue
        try:
            data = service.files().export(
                fileId=entry["doc_id"], mimeType=MARKDOWN_MIME
            ).execute()
            doc_text = normalize_markdown(
                data.decode("utf-8") if isinstance(data, bytes) else str(data)
            )
            path = Path(manuscript["path"]) / relpath
            local_text = normalize_markdown(
                path.read_text(encoding="utf-8") if path.exists() else ""
            )
            base = entry.get("pushed_hash")
            local_hash = _hashlib.sha256(local_text.encode()).hexdigest()[:16]
            doc_hash = _hashlib.sha256(doc_text.encode()).hexdigest()[:16]
            if doc_text == local_text:
                if entry.get("checked_out"):
                    entry["checked_out"] = False
                    dirty = True
                if entry.get("pushed_hash") != local_hash:
                    entry["pushed_hash"] = local_hash
                    dirty = True
                report["in_sync"].append(relpath)
            elif base and local_hash == base:
                path.write_text(doc_text, encoding="utf-8")
                entry.update(checked_out=False, pushed_hash=doc_hash)
                dirty = True
                report["pulled"].append(relpath)
            elif base and doc_hash == base:
                media = MediaInMemoryUpload(local_text.encode("utf-8"),
                                            mimetype=MARKDOWN_MIME)
                service.files().update(fileId=entry["doc_id"],
                                       media_body=media).execute()
                entry["pushed_hash"] = local_hash
                dirty = True
                report["pushed"].append(relpath)
            else:
                report["conflicts"].append(relpath)
        except Exception as err:  # network, API — never block the session
            report["errors"].append({"file": relpath, "error": str(err)})
    if dirty:
        _save_mapping(db, manuscript, meta)
    return report
