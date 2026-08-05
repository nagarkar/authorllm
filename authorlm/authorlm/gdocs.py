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
        # Inserted text INHERITS the character style at the insertion point,
        # and deleteContentRange leaves the tab's residual paragraph carrying
        # the OLD content's style — an epigraph italicized once in the Doc UI
        # re-italicized an entire essay on every subsequent push. Every
        # insertion is therefore followed by an explicit style reset; the
        # temp doc's true run styles are applied after.
        def reset_style(start: int, end: int) -> dict:
            return {"updateTextStyle": {
                "range": {"tabId": tab_id,
                          "startIndex": start, "endIndex": end},
                "textStyle": {"bold": False, "italic": False,
                              "underline": False},
                "fields": "bold,italic,underline"}}

        if any("horizontalRule" in e for e in paragraph.get("elements", [])):
            requests.append({"insertText": {
                "location": {"tabId": tab_id, "index": cursor},
                "text": "---\n"}})
            requests.append(reset_style(cursor, cursor + 4))
            cursor += 4
            continue
        if not text:
            continue
        start = cursor
        requests.append({"insertText": {
            "location": {"tabId": tab_id, "index": start}, "text": text}})
        cursor += len(text)
        requests.append(reset_style(start, cursor))
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


# ---------------------------------------------------------------- comments
#
# Doc comments are author reactions — evidence to ingest, never Doc state
# to preserve (push rebuilds a tab wholesale, so anchors die on every round
# trip). Pull harvests all open comments by default, stores them verbatim,
# and resolves each in the Doc with a receipt: resolved comments collapse
# out of the margin (no orphan bloat) yet stay reopenable and auditable.
# No addressing prefix is required — filtering, if ever needed, keys on the
# comment's author identity, not a textual convention.

COMMENT_RECEIPT = ("Ingested into AuthorLM as review evidence — {date}. "
                   "— authorlm")


def fetch_open_comments(service, doc_id: str) -> list[dict]:
    """All open (unresolved) comments on the master Doc, oldest first.
    comments.list is file-level — the master Doc is one Drive file — so
    tab/essay attribution happens later, by quote matching."""
    comments, token = [], None
    while True:
        resp = service.comments().list(
            fileId=doc_id, pageToken=token, includeDeleted=False,
            fields="nextPageToken,comments(id,content,quotedFileContent,"
                   "resolved,author(displayName),createdTime,"
                   "replies(content,author(displayName)))",
        ).execute()
        comments += [c for c in resp.get("comments", [])
                     if not c.get("resolved")]
        token = resp.get("nextPageToken")
        if not token:
            return sorted(comments, key=lambda c: c.get("createdTime", ""))


def _flat(text: str) -> str:
    return " ".join(text.split())


def clamp(text: str, limit: int = 60) -> str:
    flat = _flat(text)
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def locate_quote(files: dict[str, str],
                 quoted: str) -> tuple[str | None, str | None]:
    """(relpath, nearest heading) for a comment's anchor text, matched with
    normalized whitespace against the local files. Missing or ambiguous
    (multiple files match) → (None, None): stored unattributed, never
    guessed."""
    from .revisions import _nearest_heading, _paragraphs

    needle = _flat(quoted)
    if not needle:
        return None, None
    hits = [rel for rel, text in files.items() if needle in _flat(text)]
    if len(hits) != 1:
        return None, None
    relpath = hits[0]
    paragraphs = _paragraphs(files[relpath])
    # A quote can span paragraphs; fall back to its head for the index.
    index = next(
        (i for i, p in enumerate(paragraphs) if needle in _flat(p)),
        next((i for i, p in enumerate(paragraphs)
              if needle[:40] in _flat(p)), len(paragraphs) - 1),
    )
    return relpath, _nearest_heading(paragraphs, index)


def ingest_comments(db: Database, manuscript: dict, comments: list[dict],
                    files: dict[str, str]) -> list[dict]:
    """Store open Doc comments as doc_comments rows (dedupe by comment id —
    a re-pull after a failed resolve must not double-ingest). Returns the
    compact projection for the pull report: the anchor quote clamped, the
    author's comment VERBATIM (clamp the quote, never the comment)."""
    from .db import ko_fields

    out = []
    for c in comments:
        quoted = (c.get("quotedFileContent") or {}).get("value") or ""
        relpath, heading = locate_quote(files, quoted)
        location = f"{relpath}#{heading}" if relpath and heading else relpath
        existing = db.one(
            "SELECT id FROM doc_comments "
            "WHERE manuscript_id = ? AND comment_id = ?",
            (manuscript["id"], c["id"]),
        )
        if existing is None:
            row = ko_fields("dc")
            row.update(
                manuscript_id=manuscript["id"], comment_id=c["id"],
                file=relpath, location=location, quoted=quoted,
                content=c.get("content", ""),
                author=(c.get("author") or {}).get("displayName"),
                comment_created=c.get("createdTime"),
                replies=json.dumps(
                    [r.get("content", "") for r in c.get("replies", [])]),
                state="ingested",
            )
            db.insert("doc_comments", row)
        out.append({
            "comment_id": c["id"],
            "location": location or "(unattributed)",
            "quote": clamp(quoted),
            "content": c.get("content", ""),
        })
    return out


def resolve_comments_with_receipt(db: Database, service, doc_id: str,
                                  manuscript_id: str,
                                  comment_ids: list[str]) -> int:
    """Resolve ingested comments in the Doc with a receipt reply — never
    delete (irreversible destruction of the author's words if ingestion
    ever has a bug). A failed resolve is re-offered on the next pull."""
    import sys

    from .db import now_iso

    receipt = COMMENT_RECEIPT.format(date=now_iso()[:10])
    resolved = 0
    for comment_id in comment_ids:
        try:
            service.replies().create(
                fileId=doc_id, commentId=comment_id,
                body={"action": "resolve", "content": receipt},
                fields="id",
            ).execute()
        except Exception as err:
            print(f"warning: could not resolve comment {comment_id} ({err});"
                  " it will be re-offered on the next pull.", file=sys.stderr)
            continue
        row = db.one(
            "SELECT id FROM doc_comments "
            "WHERE manuscript_id = ? AND comment_id = ?",
            (manuscript_id, comment_id),
        )
        if row:
            db.update("doc_comments", row["id"], {"state": "resolved"})
        resolved += 1
    return resolved


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

class DocBridge:
    """Binding between one local directory and one tabbed master Doc.

    The manuscript bridge (default everywhere) syncs the manuscript root
    with the manuscript master Doc: TOC-coupled tab order/hierarchy, rich
    manifest. The workspace bridge syncs `_profiles/` — observation-
    invisible declared context (market intelligence, positioning) — with
    a separate workspace Doc: free tab order, minimal manifest, same
    conflict/comment/adoption machinery."""

    def __init__(self, meta_key: str, root: Path, doc_name: str,
                 toc_sync: bool, rich_manifest: bool,
                 display_prefix: str = ""):
        self.meta_key = meta_key
        self.root = root
        self.doc_name = doc_name
        self.toc_sync = toc_sync
        self.rich_manifest = rich_manifest
        self.display_prefix = display_prefix


def manuscript_bridge(manuscript: dict) -> DocBridge:
    return DocBridge("gdocs", Path(manuscript["path"]),
                     manuscript["name"], toc_sync=True, rich_manifest=True)


def workspace_bridge(manuscript: dict) -> DocBridge:
    return DocBridge("gdocs_workspace",
                     Path(manuscript["path"]) / "_profiles",
                     f"{manuscript['name']} (workspace)",
                     toc_sync=False, rich_manifest=False,
                     display_prefix="_profiles/")


def _reading_order_files(bridge: DocBridge) -> list[str]:
    """Tab order for a bridge's files: TOC order for the manuscript root
    (reading_order falls back to alphabetical when no toc.md exists —
    which is exactly right for the workspace directory)."""
    from .revisions import read_manuscript_files
    from .structure import reading_order

    files = read_manuscript_files(bridge.root)
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
                  drive, docs_service, bridge: DocBridge | None = None) -> str:
    """One master Doc per bridge, one tab per file (tab title = the
    filename, reading order). Creates whatever is missing, retires the
    blank default tab, drops old per-file doc links, and records
    _master_id plus per-file tab ids."""
    bridge = bridge or manuscript_bridge(manuscript)
    entry = meta.setdefault(bridge.meta_key, {})
    master_id = entry.get("_master_id")
    if not master_id:
        folder_id = _ensure_folder(manuscript, meta, drive)
        result = drive.files().create(
            body={"name": bridge.doc_name, "mimeType": GDOC_MIME,
                  "parents": [folder_id]},
            fields="id",
        ).execute()
        master_id = entry["_master_id"] = result["id"]
        # Each new incarnation of the master Doc bumps the doc version.
        entry["_doc_version"] = entry.get("_doc_version", 0) + 1
    existing = {title: tid for tid, title in _doc_tabs(docs_service, master_id)}
    order = _reading_order_files(bridge)

    # The container tab: one protected root tab named exactly the
    # manuscript's DB name — the identifier of an AuthorLM-managed Doc.
    # Content tabs live nested under it, where the API can still move them
    # (root-level tabs reject all property updates — verified 2026-08-05).
    # Existing root tabs cannot be tucked in programmatically for the same
    # reason: that one-time migration is a human drag per tab.
    container_id = entry.get("_container_tab")
    try:
        if not container_id:
            container_id = existing.get(bridge.doc_name)
            if not container_id:
                reply = docs_service.documents().batchUpdate(
                    documentId=master_id,
                    body={"requests": [{"addDocumentTab": {
                        "tabProperties": {"title": bridge.doc_name}}}]},
                ).execute()
                container_id = reply["replies"][0]["addDocumentTab"][
                    "tabProperties"]["tabId"]
            entry["_container_tab"] = container_id
        _write_container(docs_service, master_id, container_id)
    except Exception:
        container_id = entry.get("_container_tab")  # scaffolding, never fatal

    missing = [f for f in order if f not in existing]
    if missing:
        reply = docs_service.documents().batchUpdate(
            documentId=master_id,
            body={"requests": [
                {"addDocumentTab": {"tabProperties": {
                    "title": name,
                    **({"parentTabId": container_id}
                       if container_id else {})}}}
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
    # The manifest tab identifies the manuscript and the Doc incarnation —
    # renaming the Doc in Drive is fine; the manifest stays authoritative.
    try:
        manifest_id = existing.get(MANIFEST_TITLE)
        if not manifest_id:
            reply = docs_service.documents().batchUpdate(
                documentId=master_id,
                body={"requests": [{"addDocumentTab": {
                    "tabProperties": {"title": MANIFEST_TITLE}}}]},
            ).execute()
            manifest_id = reply["replies"][0]["addDocumentTab"][
                "tabProperties"]["tabId"]
        entry["_manifest_tab"] = manifest_id
        _write_manifest(docs_service, master_id, manifest_id,
                        bridge.doc_name, entry.get("_doc_version", 1),
                        inventory=build_manifest_inventory(bridge.root),
                        stats=(build_manifest_stats(bridge.root)
                               if bridge.rich_manifest else None))
    except Exception:
        pass  # the manifest is metadata — never fatal to a push

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


MANIFEST_TITLE = "manifest"


def build_manifest_inventory(root: Path) -> list[tuple[str, int, int]]:
    """(name, depth, word count) for every content file in TOC order,
    computed from the local files being pushed — the manifest records what
    the Doc SHOULD contain, so a Doc-side deletion is detectable from the
    Doc alone, without reference to local files."""
    from .revisions import read_manuscript_files
    from .structure import (TOC_FILENAME, content_files, parse_toc_tree,
                            reading_order)

    files = read_manuscript_files(root)
    prose = content_files(files)
    depth_by = dict(parse_toc_tree(files.get(TOC_FILENAME, "")))
    order, _ = reading_order(files)
    return [(n, depth_by.get(n, 0), len(prose[n].split()))
            for n in order if n in prose]


_VOWEL_GROUPS = re.compile(r"[aeiouy]+", re.I)


def _syllables(word: str) -> int:
    w = word.lower().strip(".,;:!?\"'()[]—–")
    if not w:
        return 0
    groups = len(_VOWEL_GROUPS.findall(w))
    if w.endswith("e") and groups > 1:
        groups -= 1
    return max(groups, 1)


def chapter_stats(text: str) -> dict:
    """Deterministic readability signals for one chapter. ASL = average
    sentence length in words; AWL = average word length in characters;
    FRE = Flesch Reading Ease with a heuristic syllable count (treat as
    approximate; higher is easier, 60–70 is plain English)."""
    prose = re.sub(r"^#.*$", "", text, flags=re.M)   # headings aren't prose
    words = re.findall(r"[A-Za-z'’]+", prose)
    if not words:
        return {}
    sentences = max(len(re.findall(r"[.!?]+(?:[\s\"'’”)]|$)", prose)), 1)
    syllables = sum(_syllables(w) for w in words)
    asl = len(words) / sentences
    fre = 206.835 - 1.015 * asl - 84.6 * (syllables / len(words))
    return {"asl": asl,
            "awl": sum(len(w) for w in words) / len(words),
            "fre": max(0.0, min(100.0, fre))}


def manifest_text(manuscript_name: str, doc_version: int,
                  inventory: list[tuple[str, int, int]] | None = None,
                  stats: dict[str, dict] | None = None,
                  pushed_at: str | None = None) -> str:
    """The manifest tab's content: identifies the manuscript regardless of
    what the user renames the Doc to, records which incarnation of the
    master document this is, and inventories every chapter with its word
    count (hierarchy shown by indentation, magnitude by bar). Write-only:
    rewritten on every push, never read back — a Doc-side loss shows up as
    a tab missing from this list."""
    from .db import now_iso

    lines = [f"Manuscript: {manuscript_name}",
             f"Doc version: {doc_version}",
             "Managed by AuthorLM — rewritten on every push. Write-only: "
             "never edited by hand, never read back as content."]
    if inventory:
        total = sum(w for _, _, w in inventory)
        lines += ["", f"Contents at last push — {len(inventory)} chapter(s), "
                      f"{total:,} words:", ""]
        peak = max((w for _, _, w in inventory), default=1) or 1
        for name, depth, words in inventory:
            bar = "█" * (round(words / peak * 20) or (1 if words else 0))
            line = f"{'    ' * depth}{name} — {words:,} words"
            s = (stats or {}).get(name)
            if s:
                line += (f" · ASL {s['asl']:.0f} · AWL {s['awl']:.1f}"
                         f" · FRE {s['fre']:.0f}")
            lines.append(f"{line}  {bar}")

        # Summary statistics (the numbers publishers ask about first).
        # Back matter = the appendix.md subtree, by convention.
        top = None
        back = 0
        for name, depth, words in inventory:
            if depth == 0:
                top = name
            if top == "appendix.md":
                back += words
        substantive = sorted(w for _, _, w in inventory if w > 200)
        median = (substantive[len(substantive) // 2] if substantive else 0)
        summary = (f"Statistics: {total:,} words total"
                   + (f" ({total - back:,} main / {back:,} back matter)"
                      if back else "")
                   + f" · ~{total // 275} pages @275 w/pg"
                   + f" · ~{total / 240 / 60:.1f} h read")
        if substantive:
            summary += (f" · {len(substantive)} chapter(s) >200 words "
                        f"(median {median:,})")
        lines += ["", summary]
        if stats:
            lines.append("Codes: ASL avg sentence length (words) · AWL avg "
                         "word length (chars) · FRE Flesch Reading Ease, "
                         "approximate (higher = easier; 60–70 ≈ plain "
                         "English).")
        lines += ["", f"Pushed: {pushed_at or now_iso()[:16] + 'Z'}",
                  "A chapter listed above but missing from the tabs was "
                  "deleted after this push."]
    return "\n".join(lines) + "\n"


def build_manifest_stats(root: Path) -> dict[str, dict]:
    """Per-chapter readability signals for the manifest, from local files."""
    from .revisions import read_manuscript_files
    from .structure import content_files

    files = content_files(read_manuscript_files(root))
    out = {}
    for name, text in files.items():
        s = chapter_stats(text)
        if s:
            out[name] = s
    return out


def _write_manifest(docs_service, master_id: str, tab_id: str,
                    manuscript_name: str, doc_version: int,
                    inventory: list[tuple[str, int, int]] | None = None,
                    stats: dict[str, dict] | None = None) -> None:
    requests: list[dict] = []
    end = _tab_end(docs_service, master_id, tab_id)
    if end > 2:
        requests.append({"deleteContentRange": {"range": {
            "tabId": tab_id, "startIndex": 1, "endIndex": end - 1}}})
    requests.append({"insertText": {
        "location": {"tabId": tab_id, "index": 1},
        "text": manifest_text(manuscript_name, doc_version, inventory,
                              stats)}})
    docs_service.documents().batchUpdate(
        documentId=master_id, body={"requests": requests}).execute()


CONTAINER_SENTINEL = ("Automatically generated by AuthorLM. "
                      "Do not delete.\n")


def _write_container(docs_service, master_id: str, tab_id: str) -> None:
    """The container tab's body is a fixed sentinel — rewritten on every
    push so hand edits never accumulate there. Content writes to root
    tabs work fine; only tabProperties updates are refused."""
    requests: list[dict] = []
    end = _tab_end(docs_service, master_id, tab_id)
    if end > 2:
        requests.append({"deleteContentRange": {"range": {
            "tabId": tab_id, "startIndex": 1, "endIndex": end - 1}}})
    requests.append({"insertText": {
        "location": {"tabId": tab_id, "index": 1},
        "text": CONTAINER_SENTINEL}})
    docs_service.documents().batchUpdate(
        documentId=master_id, body={"requests": requests}).execute()


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


def _resolve(bridge: DocBridge, query: str) -> tuple[str, Path]:
    from .docs import _match

    candidates = iter_manuscript_paths(bridge.root)
    path = _match(candidates, query)
    relpath = str(path.relative_to(bridge.root))
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
             docs_service=None, bridge: DocBridge | None = None) -> dict:
    """Tabbed push: normalize the local file, then rebuild its tab of the
    master Doc — markdown → temp-doc import (Google owns the conversion)
    → transplant into the tab → temp deleted. Marks the file checked out."""
    import hashlib

    from googleapiclient.http import MediaInMemoryUpload

    bridge = bridge or manuscript_bridge(manuscript)
    relpath, path = _resolve(bridge, query)
    text = path.read_text(encoding="utf-8")
    normalized = normalize_markdown(text)
    locally_normalized = normalized != text
    if locally_normalized:
        path.write_text(normalized, encoding="utf-8")

    meta = _mapping(db, manuscript)
    created = not meta.get(bridge.meta_key, {}).get("_master_id")
    master_id = ensure_master(db, manuscript, meta, service, docs_service,
                              bridge=bridge)
    entry = meta[bridge.meta_key][relpath]
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


def classify_tabs(tabs: list[tuple[str, str]], links: dict,
                  local_files: set[str]) -> dict:
    """Classify the master Doc's tabs against the mapping. The stable key is
    the tab ID (permanent, survives renames); titles are display names.

    - known id, changed title      → renamed (warn: titles must stay filenames)
    - unknown id, '*.md' title, no local file or link → adopted (new essay)
    - unknown id, '*.md' title, existing file/link    → readopted (relink;
      content drift is the author's call — no push-base survives a relink)
    - unknown id, title duplicating a LIVE mapped tab → ambiguous (a hand-made
      duplicate; relinking would hijack the real tab's mapping)
    - manifest / non-.md titles    → ignored
    """
    from collections import Counter

    known_ids = {e["tab_id"]: f for f, e in links.items()
                 if isinstance(e, dict) and e.get("tab_id")}
    doc_ids = {tid for tid, _ in tabs}
    unknown_title_counts = Counter(
        title for tid, title in tabs if tid not in known_ids)
    out: dict = {"adopted": [], "readopted": [], "renamed": [],
                 "ignored": [], "ambiguous": []}
    for tid, title in tabs:
        if tid in known_ids:
            if title != known_ids[tid]:
                out["renamed"].append((known_ids[tid], title))
            continue
        if title == MANIFEST_TITLE or not title.endswith(".md"):
            out["ignored"].append(title)
            continue
        entry = links.get(title)
        if isinstance(entry, dict) and entry.get("tab_id") in doc_ids:
            out["ambiguous"].append(title)   # duplicate of a live mapped tab
            continue
        if unknown_title_counts[title] > 1:
            out["ambiguous"].append(title)   # two unknown tabs, same name
            continue
        if title in links or title in local_files:
            out["readopted"].append((title, tid))
        else:
            out["adopted"].append((title, tid))
    return out


def walk_tabs(tabs: list, tab_props: list, doc_pairs: list,
              parent_md: str | None = None) -> None:
    """DFS over the Doc's tab tree. Fills tab_props with (tabId, title) for
    every tab, and doc_pairs with (title, parent-md-title) for '*.md' tabs —
    a non-md tab is transparent to hierarchy (its md ancestor passes
    through), so scratch tabs can nest anywhere without entering the TOC."""
    for t in tabs:
        p = t.get("tabProperties", {})
        tid, title = p.get("tabId"), p.get("title", "")
        tab_props.append((tid, title))
        if title.endswith(".md"):
            doc_pairs.append((title, parent_md))
            child_md = title
        else:
            child_md = parent_md
        walk_tabs(t.get("childTabs", []), tab_props, doc_pairs, child_md)


def classify_structure(doc_pairs: list, local_pairs: list,
                       base_pairs: list | None) -> str:
    """Three-way state of the TOC↔tab-tree sync, on DFS (name, parent)
    pairs: insync | doc_moved (rewrite toc.md) | local_moved (push
    repositions tabs) | conflict (both moved) | unsynced (no base and the
    sides disagree beyond pure additions)."""
    if doc_pairs == local_pairs:
        return "insync"
    if base_pairs is None:
        # Doc-side pure additions (adopted tabs) are safe to apply even
        # without a base: the common part must agree exactly.
        local_names = {n for n, _ in local_pairs}
        doc_common = [(n, p if p in local_names else None)
                      for n, p in doc_pairs if n in local_names]
        return "doc_moved" if doc_common == local_pairs else "unsynced"
    if local_pairs == base_pairs:
        return "doc_moved"
    if doc_pairs == base_pairs:
        return "local_moved"
    return "conflict"


def rewrite_toc_from_doc(manuscript: dict, doc_pairs: list,
                         local_tree: list) -> list:
    """Rewrite toc.md to the Doc's tab structure. Entries with no tab
    (never-pushed local files) are preserved after their previous
    predecessor. Returns the new (name, depth) tree."""
    from .structure import (TOC_FILENAME, parents_to_tree,
                            serialize_toc_tree)

    tabbed = {n for n, _ in doc_pairs}
    new_tree = parents_to_tree(doc_pairs)
    old_names = [n for n, _ in local_tree]
    for i, (name, depth) in enumerate(local_tree):
        if name in tabbed:
            continue
        pred = next((old_names[j] for j in range(i - 1, -1, -1)
                     if old_names[j] in tabbed), None)
        pos = (next((k + 1 for k, (n, _) in enumerate(new_tree)
                     if n == pred), 0) if pred else 0)
        new_tree.insert(pos, (name, depth))
    (Path(manuscript["path"]) / TOC_FILENAME).write_text(
        serialize_toc_tree(new_tree), encoding="utf-8")
    return new_tree


def sync_tab_structure(db: Database, manuscript: dict, docs_service) -> dict:
    """Push direction of the TOC↔tab sync: reposition/re-parent the master
    Doc's tabs to match toc.md. Applies only when the Doc side hasn't
    moved since the last sync (local_moved / insync); a doc-side change
    means 'pull first'."""
    from .structure import TOC_FILENAME, parse_toc_tree, tree_to_parents

    meta = _mapping(db, manuscript)
    links = meta.get("gdocs", {})
    master_id = links.get("_master_id")
    if not master_id:
        return {"skipped": "no master doc"}
    toc_path = Path(manuscript["path"]) / TOC_FILENAME
    if not toc_path.exists():
        return {"skipped": "no toc.md"}
    tab_props: list = []
    doc_pairs: list = []
    doc = docs_service.documents().get(
        documentId=master_id, includeTabsContent=True).execute()
    walk_tabs(doc.get("tabs", []), tab_props, doc_pairs)
    linked = {f for f, e in links.items()
              if not f.startswith("_") and isinstance(e, dict)
              and e.get("tab_id")}
    doc_pairs = [(n, p if p in linked else None)
                 for n, p in doc_pairs if n in linked]
    desired = [(n, p if p in linked else None)
               for n, p in tree_to_parents(parse_toc_tree(
                   toc_path.read_text(encoding="utf-8")))
               if n in linked]
    base = ([tuple(x) for x in links["_tab_structure"]]
            if links.get("_tab_structure") else None)
    state = classify_structure(doc_pairs, desired, base)
    if state == "insync":
        links["_tab_structure"] = [list(x) for x in doc_pairs]
        _save_mapping(db, manuscript, meta)
        return {"insync": True}
    if state not in ("local_moved", "unsynced"):
        return {"skipped": f"Doc tab order also changed ({state}) — "
                           "pull first"}
    tid_of = {f: links[f]["tab_id"] for f in linked}
    current_parent = dict(doc_pairs)
    # Current sibling index per parent, from the walk's document order.
    current_index: dict = {}
    seen_per_parent: dict = {}
    for name, parent in doc_pairs:
        current_index[name] = seen_per_parent.get(parent, 0)
        seen_per_parent[parent] = current_index[name] + 1
    # One move per API call, placing each tab at its final slot in desired
    # DFS order — batched tab moves 500 on the server, the same semantics
    # problem next_tab_move solves for whole-tab ordering.
    moved = 0
    sibling_index: dict = {}
    for name, parent in desired:
        idx = sibling_index.get(parent, 0)
        sibling_index[parent] = idx + 1
        parent_changed = current_parent.get(name) != parent
        if not parent_changed and current_index.get(name) == idx:
            continue  # already in place
        props: dict = {"tabId": tid_of[name], "index": idx}
        fields = "index"
        if parent_changed:
            props["parentTabId"] = tid_of.get(parent, "")
            fields = "index,parentTabId"
        try:
            docs_service.documents().batchUpdate(
                documentId=master_id,
                body={"requests": [{"updateDocumentTabProperties": {
                    "tabProperties": props, "fields": fields}}]},
            ).execute()
        except Exception:
            # Live finding 2026-08-05 (verified by matrix test): the Docs
            # API 500s on ALL property updates to ROOT-LEVEL tabs — index,
            # parentTabId, even iconEmoji — while nested tabs accept every
            # operation. Children are irrelevant. Root-tab order therefore
            # needs a human drag in the Doc UI; the next pull records it.
            is_root = parent is None
            hint = ("Google's API rejects updates to root-level tabs "
                    f"(server 500) — drag '{name}' into place in the Doc, "
                    "then pull" if is_root else "rerun push")
            return {"skipped": f"tab move failed at {name}: {hint}",
                    "moved": moved}
        moved += 1
    links["_tab_structure"] = [list(x) for x in desired]
    _save_mapping(db, manuscript, meta)
    return {"moved": moved}


def three_way(tab_text: str, local_text: str, base_hash: str | None) -> str:
    """Classify a pull target by the three-way state (agreed base / local /
    tab). 'conflict' only when BOTH sides moved off the base — a tab that
    still matches the base is merely stale ('local_ahead'), never a
    conflict. No recorded base and drift → 'changed' (the Doc wins, the
    historical behavior; local stays in version history)."""
    import hashlib

    if tab_text == local_text:
        return "unchanged"
    if not base_hash:
        return "changed"
    if hashlib.sha256(local_text.encode()).hexdigest()[:16] == base_hash:
        return "changed"       # only the tab moved — safe to pull
    if hashlib.sha256(tab_text.encode()).hexdigest()[:16] == base_hash:
        return "local_ahead"   # only local moved — keep local, tab is stale
    return "conflict"


def pull_doc(db: Database, manuscript: dict, query: str | None = None,
             service=None, force: bool = False,
             with_comments: bool = True, docs_service=None,
             bridge: DocBridge | None = None) -> dict:
    """Tabbed pull: export the master Doc once, split it into per-tab
    sections, normalize, and write the queried file — or every mapped file
    when query is None. Clears checkouts. Caller collects afterwards.

    By default also harvests every open comment on the master Doc
    (regardless of which file was pulled — comments are author feedback),
    ingests them into doc_comments, and resolves each in the Doc with a
    receipt. The report's `comments` list is the to-address queue.

    Per-file conflict guard: if a file changed locally since the push AND
    its tab differs from it, both sides were edited — skipped unless
    `force` (the Doc wins; local edits stay in version history)."""
    import hashlib

    bridge = bridge or manuscript_bridge(manuscript)
    meta = _mapping(db, manuscript)
    links = meta.get(bridge.meta_key, {})
    master_id = links.get("_master_id")
    if not master_id:
        raise LookupError("no master Google Doc yet — 'doc push' first")
    report: dict = {"changed": [], "unchanged": [], "conflicts": [],
                    "local_ahead": [], "missing": [], "doc_id": master_id}

    # Tab reconciliation (best-effort — a pull must survive without it):
    # the Docs API is the authority on what tabs exist. Unknown *.md tabs
    # the author created by hand are adopted (new essays materialize on
    # this very pull) or re-adopted (relinked to an existing file; content
    # drift then surfaces as a conflict, never a silent overwrite).
    tab_props: list[tuple[str, str]] = []
    doc_pairs: list = []
    readopted_files: set[str] = set()
    if docs_service is not None:
        try:
            tab_doc = docs_service.documents().get(
                documentId=master_id, includeTabsContent=True).execute()
            walk_tabs(tab_doc.get("tabs", []), tab_props, doc_pairs)
            # Container integrity: the root tab named for the manuscript is
            # the Doc's identity and is protected — detect renames (the API
            # cannot fix a root tab; the author must rename it back), and
            # list content tabs still sitting at true root awaiting their
            # one-time drag into the container.
            container_id = links.get("_container_tab")
            if container_id:
                actual = next((t for tid, t in tab_props
                               if tid == container_id), None)
                if actual is not None and actual != bridge.doc_name:
                    report["container_renamed"] = (bridge.doc_name, actual)
                root_titles = [t.get("tabProperties", {}).get("title", "")
                               for t in tab_doc.get("tabs", [])]
                pending = [t for t in root_titles if t.endswith(".md")]
                if pending:
                    report["container_pending"] = pending
        except Exception as err:
            report["tabs_error"] = str(err)
    if tab_props:
        local_files = set(iter_manuscript_paths(bridge.root))
        cls = classify_tabs(tab_props, links, local_files)
        ignored = [t for t in cls["ignored"] if t != MANIFEST_TITLE]
        if ignored:
            report["ignored_tabs"] = ignored
        for title, tid in cls["adopted"]:
            links[title] = {"tab_id": tid, "checked_out": False}
            report.setdefault("adopted", []).append(title)
        for title, tid in cls["readopted"]:
            entry = links.get(title) if isinstance(links.get(title), dict) else {}
            entry["tab_id"] = tid
            entry.pop("pushed_hash", None)  # no base survives a relink
            entry["checked_out"] = False
            links[title] = entry
            readopted_files.add(title)
            report.setdefault("readopted", []).append(title)
        if cls["renamed"]:
            report["renamed"] = cls["renamed"]
        if cls["ambiguous"]:
            report["ambiguous_tabs"] = cls["ambiguous"]

    data = service.files().export(
        fileId=master_id, mimeType=MARKDOWN_MIME).execute()
    whole = data.decode("utf-8") if isinstance(data, bytes) else str(data)
    mapped = [f for f, e in links.items()
              if not f.startswith("_") and isinstance(e, dict)
              and e.get("tab_id")]
    # Every tab title is a split boundary — a tab that isn't a boundary
    # would have its lines swallowed into whichever tab precedes it in the
    # export (the manifest always is one, even without the tab listing).
    boundaries = set(mapped) | {MANIFEST_TITLE} | {t for _, t in tab_props if t}
    sections = split_tabbed_export(whole, boundaries)
    targets = mapped
    if query:
        relpath, _ = _resolve(bridge, query)
        if relpath not in mapped:
            raise LookupError(f"'{relpath}' has no tab in the master Doc — "
                              "'doc push' first")
        targets = [relpath]
    for relpath in targets:
        if relpath not in sections:
            report["missing"].append(relpath)
            continue
        text = normalize_markdown(sections[relpath])
        path = bridge.root / relpath
        current = path.read_text(encoding="utf-8") if path.exists() else ""
        entry = links[relpath]
        state = three_way(text, current, entry.get("pushed_hash"))
        if relpath in readopted_files and state == "changed" and current:
            # A relink has no trustworthy base: when both sides hold
            # content and differ, choosing is the author's call — never
            # the Doc-wins default.
            state = "conflict"
        if state == "conflict" and not force:
            report["conflicts"].append(relpath)
            continue
        if state == "local_ahead" and not force:
            # The Doc was never edited after the push; only local moved.
            # Keeping local is safe — the tab is stale, not rival. The base
            # stays valid (it still equals the tab), so no hash update.
            report["local_ahead"].append(relpath)
            entry["checked_out"] = False
            continue
        if text != current:
            path.write_text(text, encoding="utf-8")
            report["changed"].append(relpath)
        elif not path.exists():
            # An adopted tab with no content still materializes: the file
            # must exist for toc placement and future pushes.
            path.write_text(text, encoding="utf-8")
            report["changed"].append(relpath)
        else:
            report["unchanged"].append(relpath)
        entry["checked_out"] = False
        # The pulled content is the new agreed base for three-way compare.
        entry["pushed_hash"] = hashlib.sha256(text.encode()).hexdigest()[:16]

    # TOC ↔ tab-structure sync (pull direction): the Doc's tab order and
    # nesting are the author's reordering interface. Three-way, like file
    # content: only-Doc-moved rewrites toc.md; only-local-moved defers to
    # the next push; both → conflict, touch nothing.
    if doc_pairs and query is None and bridge.toc_sync:
        from .structure import TOC_FILENAME, parse_toc_tree, tree_to_parents

        linked = {f for f, e in links.items()
                  if not f.startswith("_") and isinstance(e, dict)
                  and e.get("tab_id")}
        doc_struct = [(n, p if p in linked else None)
                      for n, p in doc_pairs if n in linked]
        toc_path = bridge.root / TOC_FILENAME
        local_tree = (parse_toc_tree(toc_path.read_text(encoding="utf-8"))
                      if toc_path.exists() else [])
        tabbed = {n for n, _ in doc_struct}
        local_struct = [(n, p if p in tabbed else None)
                        for n, p in tree_to_parents(local_tree)
                        if n in tabbed]
        base = ([tuple(x) for x in links["_tab_structure"]]
                if links.get("_tab_structure") else None)
        toc_state = classify_structure(doc_struct, local_struct, base)
        if toc_state == "doc_moved":
            rewrite_toc_from_doc(manuscript, doc_struct, local_tree)
            links["_tab_structure"] = [list(x) for x in doc_struct]
            report["toc_updated"] = True
        elif toc_state == "insync":
            links["_tab_structure"] = [list(x) for x in doc_struct]
        elif toc_state == "local_moved":
            report["toc_ahead"] = True
        else:
            report["toc_conflict"] = toc_state
    _save_mapping(db, manuscript, meta)

    if with_comments:
        from .revisions import read_manuscript_files

        try:
            open_comments = fetch_open_comments(service, master_id)
        except Exception as err:  # harvest failure must never break a pull
            report["comments_error"] = str(err)
            open_comments = []
        if open_comments:
            files = {bridge.display_prefix + rel: text
                     for rel, text in
                     read_manuscript_files(bridge.root).items()}
            report["comments"] = ingest_comments(
                db, manuscript, open_comments, files)
            report["comments_resolved"] = resolve_comments_with_receipt(
                db, service, master_id, manuscript["id"],
                [c["comment_id"] for c in report["comments"]])
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
    sections = split_tabbed_export(whole, set(mapped) | {MANIFEST_TITLE})
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
