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
    # Horizontal rules get uniform blank-line padding (tab exports emit
    # them flush against neighbors; local files usually pad them).
    text = re.sub(r"\n*^(---|\*\*\*|___)$\n*", r"\n\n---\n\n", text,
                  flags=re.MULTILINE)
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
        from google.auth.exceptions import RefreshError

        try:
            creds.refresh(Request())
            token_path.write_text(creds.to_json())
        except RefreshError:
            # Token expired or revoked upstream: fall through to the clean
            # non-interactive error, or to the consent flow under 'doc auth'
            # — never a raw traceback.
            creds = None
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


def transplant_requests(doc: dict, tab_id: str) -> list[dict]:
    """Convert an imported Doc (documents.get JSON) into batchUpdate
    requests that rebuild the same content inside `tab_id` of another
    document.

    This is the push-to-tab pipeline's core: markdown → (Drive import, so
    Google owns md→Doc conversion) → temp-doc JSON → these requests →
    target tab. Handles what the manuscripts use — headings, bold, italic,
    underline, links, and list bullets; anything else inserts as plain
    text rather than failing."""
    def numbered(list_id: str | None) -> bool:
        glyph = (doc.get("lists", {}).get(list_id or "", {})
                 .get("listProperties", {}).get("nestingLevels", [{}])[0]
                 .get("glyphType", ""))
        return glyph in ("DECIMAL", "ALPHA", "ROMAN", "UPPER_ALPHA",
                         "UPPER_ROMAN")

    requests: list[dict] = []
    cursor = 1  # tab bodies start at index 1
    for element in doc.get("body", {}).get("content", []):
        paragraph = element.get("paragraph")
        if not paragraph:
            continue  # section breaks, tables: not manuscript territory
        style = paragraph.get("paragraphStyle", {}).get(
            "namedStyleType", "NORMAL_TEXT")
        runs = [(e["textRun"].get("content", ""),
                 e["textRun"].get("textStyle", {}))
                for e in paragraph.get("elements", []) if "textRun" in e]
        text = "".join(t for t, _ in runs)
        # A rule paragraph carries a horizontalRule element plus a bare
        # newline run — the rule wins. A literal --- paragraph exports back
        # to a markdown rule (the Docs API has no insert-rule request).
        if any("horizontalRule" in e for e in paragraph.get("elements", [])):
            requests.append({"insertText": {
                "location": {"tabId": tab_id, "index": cursor},
                "text": "---\n"}})
            cursor += 4
            continue
        if not text:
            continue
        start = cursor
        requests.append({"insertText": {
            "location": {"tabId": tab_id, "index": start}, "text": text}})
        cursor += len(text)
        if style != "NORMAL_TEXT":
            requests.append({"updateParagraphStyle": {
                "range": {"tabId": tab_id,
                          "startIndex": start, "endIndex": cursor},
                "paragraphStyle": {"namedStyleType": style},
                "fields": "namedStyleType"}})
        offset = start
        for run_text, text_style in runs:
            fields = {k: True for k in ("bold", "italic", "underline")
                      if text_style.get(k)}
            link = text_style.get("link", {}).get("url")
            payload: dict = dict(fields)
            if link:
                payload["link"] = {"url": link}
            if payload and run_text.strip():
                requests.append({"updateTextStyle": {
                    "range": {"tabId": tab_id, "startIndex": offset,
                              "endIndex": offset + len(run_text)},
                    "textStyle": payload,
                    "fields": ",".join(sorted(payload))}})
            offset += len(run_text)
        if paragraph.get("bullet"):
            preset = ("NUMBERED_DECIMAL_ALPHA_ROMAN"
                      if numbered(paragraph["bullet"].get("listId"))
                      else "BULLET_DISC_CIRCLE_SQUARE")
            requests.append({"createParagraphBullets": {
                "range": {"tabId": tab_id,
                          "startIndex": start, "endIndex": cursor - 1},
                "bulletPreset": preset}})
    return requests


def get_service(config: dict, workspace: str | None = None,
                interactive: bool = True):
    from googleapiclient.discovery import build

    return build(
        "drive", "v3",
        credentials=get_credentials(config, workspace, interactive=interactive),
        cache_discovery=False,
    )


def get_docs_service(config: dict, workspace: str | None = None,
                     interactive: bool = True):
    from googleapiclient.discovery import build

    return build(
        "docs", "v1",
        credentials=get_credentials(config, workspace, interactive=interactive),
        cache_discovery=False,
    )


# ------------------------------------------------------------ tabbed model

def _reading_order_files(manuscript: dict) -> list[str]:
    from .revisions import read_manuscript_files
    from .structure import reading_order

    files = read_manuscript_files(Path(manuscript["path"]))
    order, _ = reading_order(files)
    return order


def _doc_tabs(docs_service, master_id: str) -> list[tuple[str, str]]:
    """[(tab_id, title)] for every tab of the master Doc, document order."""
    doc = docs_service.documents().get(
        documentId=master_id, includeTabsContent=True).execute()
    found: list[tuple[str, str]] = []

    def walk(tabs):
        for tab in tabs or []:
            props = tab.get("tabProperties", {})
            found.append((props.get("tabId"), props.get("title", "")))
            walk(tab.get("childTabs"))

    walk(doc.get("tabs"))
    return found


def _tab_end(docs_service, master_id: str, tab_id: str) -> int:
    """End index of a tab's body — the delete-before-rewrite bound."""
    doc = docs_service.documents().get(
        documentId=master_id, includeTabsContent=True).execute()

    def find(tabs):
        for tab in tabs or []:
            if tab.get("tabProperties", {}).get("tabId") == tab_id:
                return tab
            hit = find(tab.get("childTabs"))
            if hit:
                return hit
        return None

    tab = find(doc.get("tabs")) or {}
    content = tab.get("documentTab", {}).get("body", {}).get("content", [])
    return content[-1]["endIndex"] if content else 1


def tab_url(master_id: str, tab_id: str | None = None) -> str:
    base = f"https://docs.google.com/document/d/{master_id}/edit"
    return f"{base}?tab={tab_id}" if tab_id else base


def ensure_master(db: Database, manuscript: dict, meta: dict,
                  drive, docs_service) -> str:
    """One master Doc per manuscript, one tab per file (tab title = the
    filename, reading order). Creates whatever is missing, retires the
    blank default tab, drops old per-file doc links, and records
    _master_id plus per-file tab ids."""
    entry = meta.setdefault("gdocs", {})
    master_id = entry.get("_master_id")
    if not master_id:
        folder_id = _ensure_folder(manuscript, meta, drive)
        result = drive.files().create(
            body={"name": manuscript["name"], "mimeType": GDOC_MIME,
                  "parents": [folder_id]},
            fields="id",
        ).execute()
        master_id = entry["_master_id"] = result["id"]
    existing = {title: tid for tid, title in _doc_tabs(docs_service, master_id)}
    order = _reading_order_files(manuscript)
    missing = [f for f in order if f not in existing]
    if missing:
        reply = docs_service.documents().batchUpdate(
            documentId=master_id,
            body={"requests": [
                {"addDocumentTab": {"tabProperties": {"title": name}}}
                for name in missing
            ]},
        ).execute()
        for name, r in zip(missing, reply.get("replies", [])):
            existing[name] = r["addDocumentTab"]["tabProperties"]["tabId"]
    # The blank default tab is noise once real tabs exist.
    stray = [tid for title, tid in existing.items()
             if title not in order and title.startswith("Tab ")]
    if stray and len(existing) > len(stray):
        try:
            docs_service.documents().batchUpdate(
                documentId=master_id,
                body={"requests": [{"deleteTab": {"tabId": tid}}
                                   for tid in stray]},
            ).execute()
        except Exception:
            pass  # a lingering empty default tab is cosmetic, never fatal
    for name in order:
        file_entry = entry.setdefault(name, {})
        file_entry.pop("doc_id", None)  # old per-file doc link, superseded
        file_entry["tab_id"] = existing[name]
    # Tab order follows the TOC reading order, always: one move per call
    # with a fresh read between moves, until reality matches the TOC.
    desired_ids = [existing[name] for name in order]
    try:
        for _ in range(len(desired_ids) * 2):
            current_ids = [tid for tid, _ in _doc_tabs(docs_service, master_id)]
            move = next_tab_move(current_ids, desired_ids)
            if move is None:
                break
            docs_service.documents().batchUpdate(
                documentId=master_id, body={"requests": [move]}).execute()
    except Exception:
        pass  # a stale order is cosmetic, never fatal to a push
    _save_mapping(db, manuscript, meta)
    return master_id


def next_tab_move(current: list[str], desired: list[str]) -> dict | None:
    """The single next reorder request bringing `current` toward `desired`
    (tab ids; ids absent from `desired` keep their place at the end), or
    None when the order already matches. One move per API call with a fresh
    read in between — immune to the API's batched-move index semantics.
    Pure — unit-testable without Google."""
    ordered = [tid for tid in desired if tid in current]
    keep = set(ordered)
    filtered = [tid for tid in current if tid in keep]
    for position, want in enumerate(ordered):
        if filtered[position] != want:
            occupant = filtered[position]
            return {"updateDocumentTabProperties": {
                "tabProperties": {"tabId": want,
                                  "index": current.index(occupant)},
                "fields": "index"}}
    return None


def split_tabbed_export(text: str, known_files) -> dict[str, str]:
    """Split a whole-master markdown export into per-file sections. Tab
    titles export as top-level headings ('# **<title>**'); only headings
    naming a known mapped file are boundaries — content headings, even
    H1s, pass through untouched."""
    pattern = re.compile(r"^#\s+\*{0,2}(.+?)\*{0,2}\s*$")
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        match = pattern.match(line)
        name = match.group(1).strip() if match else None
        if name in known_files:
            current = name
            sections[current] = []
            continue
        if current is not None:
            sections[current].append(line)
    return {name: "\n".join(lines).strip() + "\n"
            for name, lines in sections.items()}


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
             title: str | None = None, service=None,
             docs_service=None) -> dict:
    """Tabbed push: normalize the local file, then rebuild its tab of the
    master Doc — markdown → temp-doc import (Google owns the conversion)
    → transplant into the tab → temp deleted. Marks the file checked out."""
    import hashlib

    from googleapiclient.http import MediaInMemoryUpload

    relpath, path = _resolve(manuscript, query)
    text = path.read_text(encoding="utf-8")
    normalized = normalize_markdown(text)
    locally_normalized = normalized != text
    if locally_normalized:
        path.write_text(normalized, encoding="utf-8")

    meta = _mapping(db, manuscript)
    created = not meta.get("gdocs", {}).get("_master_id")
    master_id = ensure_master(db, manuscript, meta, service, docs_service)
    entry = meta["gdocs"][relpath]
    tab_id = entry["tab_id"]

    media = MediaInMemoryUpload(normalized.encode("utf-8"),
                                mimetype=MARKDOWN_MIME)
    temp = service.files().create(
        body={"name": f"authorlm-temp-{Path(relpath).stem}",
              "mimeType": GDOC_MIME},
        media_body=media, fields="id",
    ).execute()
    try:
        temp_doc = docs_service.documents().get(documentId=temp["id"]).execute()
    finally:
        service.files().delete(fileId=temp["id"]).execute()

    requests: list[dict] = []
    end = _tab_end(docs_service, master_id, tab_id)
    if end > 2:
        requests.append({"deleteContentRange": {"range": {
            "tabId": tab_id, "startIndex": 1, "endIndex": end - 1}}})
    requests += transplant_requests(temp_doc, tab_id)
    if requests:
        docs_service.documents().batchUpdate(
            documentId=master_id, body={"requests": requests}).execute()

    entry["checked_out"] = True
    # Snapshot what was pushed: pull uses it to detect two-sided edits.
    entry["pushed_hash"] = hashlib.sha256(normalized.encode()).hexdigest()[:16]
    _save_mapping(db, manuscript, meta)
    return {
        "relpath": relpath,
        "doc_id": master_id,
        "url": tab_url(master_id, tab_id),
        "created": created,
        "locally_normalized": locally_normalized,
    }


def pull_doc(db: Database, manuscript: dict, query: str | None = None,
             service=None, force: bool = False) -> dict:
    """Tabbed pull: export the master Doc once, split it into per-tab
    sections, normalize, and write the queried file — or every mapped file
    when query is None. Clears checkouts. Caller collects afterwards.

    Per-file conflict guard: if a file changed locally since the push AND
    its tab differs from it, both sides were edited — skipped unless
    `force` (the Doc wins; local edits stay in version history)."""
    import hashlib

    meta = _mapping(db, manuscript)
    links = meta.get("gdocs", {})
    master_id = links.get("_master_id")
    if not master_id:
        raise LookupError("no master Google Doc yet — 'doc push' first")
    data = service.files().export(
        fileId=master_id, mimeType=MARKDOWN_MIME).execute()
    whole = data.decode("utf-8") if isinstance(data, bytes) else str(data)
    mapped = [f for f, e in links.items()
              if not f.startswith("_") and isinstance(e, dict)
              and e.get("tab_id")]
    sections = split_tabbed_export(whole, set(mapped))
    targets = mapped
    if query:
        relpath, _ = _resolve(manuscript, query)
        if relpath not in mapped:
            raise LookupError(f"'{relpath}' has no tab in the master Doc — "
                              "'doc push' first")
        targets = [relpath]
    report: dict = {"changed": [], "unchanged": [], "conflicts": [],
                    "missing": [], "doc_id": master_id}
    for relpath in targets:
        if relpath not in sections:
            report["missing"].append(relpath)
            continue
        text = normalize_markdown(sections[relpath])
        path = Path(manuscript["path"]) / relpath
        current = path.read_text(encoding="utf-8") if path.exists() else ""
        entry = links[relpath]
        local_hash = hashlib.sha256(current.encode()).hexdigest()[:16]
        if (text != current and not force and entry.get("pushed_hash")
                and local_hash != entry["pushed_hash"]):
            report["conflicts"].append(relpath)
            continue
        if text != current:
            path.write_text(text, encoding="utf-8")
            report["changed"].append(relpath)
        else:
            report["unchanged"].append(relpath)
        entry["checked_out"] = False
        # The pulled content is the new agreed base for three-way compare.
        entry["pushed_hash"] = hashlib.sha256(text.encode()).hexdigest()[:16]
    _save_mapping(db, manuscript, meta)
    return report


def reconcile(db: Database, manuscript: dict, service,
              docs_service=None) -> dict:
    """Session-start reconciliation over the master Doc's tabs, using the
    three-way state (agreed base / local / tab):

    - tab == local            → in sync (checkout cleared)
    - only the tab changed    → auto-pull (safe: local matches the base)
    - only local changed      → auto-push (safe: tab matches the base);
                                needs the Docs service — reported as
                                pending_push when it's unavailable
    - both changed            → CONFLICT — touch nothing, report loudly
    - no base recorded + drift → treated as a conflict (can't judge safety)

    Per-file errors are collected, never raised — reconciliation must not
    block a writing session."""
    import hashlib as _hashlib

    report: dict = {"in_sync": [], "pulled": [], "pushed": [],
                    "pending_push": [], "conflicts": [], "errors": []}
    meta = _mapping(db, manuscript)
    links = meta.get("gdocs", {})
    master_id = links.get("_master_id")
    if not master_id:
        return report
    try:
        data = service.files().export(
            fileId=master_id, mimeType=MARKDOWN_MIME).execute()
    except Exception as err:  # network, API — never block the session
        report["errors"].append({"file": "(master doc)", "error": str(err)})
        return report
    whole = data.decode("utf-8") if isinstance(data, bytes) else str(data)
    mapped = [f for f, e in links.items()
              if not f.startswith("_") and isinstance(e, dict)
              and e.get("tab_id")]
    sections = split_tabbed_export(whole, set(mapped))
    dirty = False
    for relpath in mapped:
        entry = links[relpath]
        try:
            doc_text = normalize_markdown(sections.get(relpath, ""))
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
                if docs_service is None:
                    report["pending_push"].append(relpath)
                else:
                    if dirty:
                        _save_mapping(db, manuscript, meta)
                        dirty = False
                    push_doc(db, manuscript, relpath, service=service,
                             docs_service=docs_service)
                    meta = _mapping(db, manuscript)
                    links = meta.get("gdocs", {})
                    report["pushed"].append(relpath)
            else:
                report["conflicts"].append(relpath)
        except Exception as err:  # network, API — never block the session
            report["errors"].append({"file": relpath, "error": str(err)})
    if dirty:
        _save_mapping(db, manuscript, meta)
    return report
