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
import os
import re
from pathlib import Path

from .db import Database, loads
from . import threads as threads_mod
from .illus import (ILLUS_DIR, PROMPTS_SUBDIR, capture_embeds,
                    join_prompt_embeds, reembed, snapshot_prompts,
                    split_prompt_embeds, strip_dangling)
from .revisions import iter_manuscript_paths, strip_embed_lines
from .staging import is_marked as staging_is_marked

SCOPES = ["https://www.googleapis.com/auth/drive.file"]
GDOC_MIME = "application/vnd.google-apps.document"
MARKDOWN_MIME = "text/markdown"


# ------------------------------------------------------------- normalizer

# The colon: the exporter writes a URL typed or inserted as plain text as
# `https\://…` so it will not auto-link (seen 2026-10-02 on footnote lines
# an outside caller staged).
_ESCAPE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!<>~|=:])")
_BULLET = re.compile(r"^(\s*)\*\s+", re.MULTILINE)
# CommonMark thematic break: ≥3 of *, -, or _ with optional spaces
# between, ≤3 leading spaces. Must run BEFORE _BULLET — a spaced
# `* * *` break would otherwise become `- * *` (the bullet rule eats
# the first `* `) and push_doc would write that corruption to disk.
_THEMATIC_BREAK = re.compile(
    r"^[ \t]{0,3}(?:(?:\*[ \t]*){3,}|(?:-[ \t]*){3,}|(?:_[ \t]*){3,})$",
    re.MULTILINE)
_HEADING = re.compile(r"^(#{1,6})[ \t]+", re.MULTILINE)
_TABLE_DELIM = re.compile(r"^\|(?:\s*:?-{3,}:?\s*\|)+\s*$", re.M)
_EMPTY_HEADING = re.compile(r"^#{1,6}$\n?", re.MULTILINE)
_HARD_BREAK = re.compile(r"(?<=\S) {2,}\n(?=[^\s#>|*\-\[])(?!\d+[.)]\s)")
_FOOTNOTE_SYNTAX = re.compile(r"\[\^")


# ------------------------------------------------------------- code fences
#
# Fenced code blocks (``` / ~~~) are opaque to the normalizer for the
# same reason math is: their bytes are intentional examples, not prose
# the Doc bridge is free to canonicalize. Without lifting them, the
# prose rules rewrite the fence body in place — `*` bullets become `-`,
# `\*` loses its backslash, `| :--- |` alignment colons vanish — and
# `push_doc` writes that corruption back to the local manuscript
# whenever normalize differs from disk. Lifted before math so a fence
# that *shows* TeX is preserved byte-for-byte too.
_FENCE_OPEN = re.compile(r"^([`~]{3,})")
_FENCE_SENTINEL = "\x00F%d\x00"
_FENCE_SENTINEL_RE = re.compile(r"\x00F(\d+)\x00")


def _lift_fences(text: str) -> tuple[str, list[str]]:
    """Replace each fenced code block with a sentinel; return (text, spans).
    Unclosed fences consume through EOF (CommonMark)."""
    lines = text.split("\n")
    out: list[str] = []
    spans: list[str] = []
    i = 0
    while i < len(lines):
        m = _FENCE_OPEN.match(lines[i])
        if not m:
            out.append(lines[i])
            i += 1
            continue
        marker = m.group(1)
        ch, n = marker[0], len(marker)
        block = [lines[i]]
        i += 1
        while i < len(lines):
            block.append(lines[i])
            close = _FENCE_OPEN.match(lines[i])
            # Closing fence: same char, at least as long, no info string.
            if (close and close.group(1)[0] == ch
                    and len(close.group(1)) >= n
                    and lines[i].strip() == close.group(1)):
                i += 1
                break
            i += 1
        spans.append("\n".join(block))
        out.append(_FENCE_SENTINEL % (len(spans) - 1))
    return "\n".join(out), spans


def _restore_fences(text: str, spans: list[str]) -> str:
    return _FENCE_SENTINEL_RE.sub(lambda m: spans[int(m.group(1))], text)


# ------------------------------------------------------------- math spans
#
# TeX math — $…$ inline, $$…$$ display (docs/math-and-physics-guidelines.md)
# — is opaque to the normalizer: every backslash inside it is content,
# so the Docs-export escape strip that is right for prose would gut a
# `\\` row break or a `\{` brace. Spans are lifted out before the prose
# rules run and put back after; only display layout is canonicalized
# (one physical line — the Doc reflows a block to that anyway).
_DISPLAY_MATH = re.compile(r"\$\$(.+?)\$\$", re.DOTALL)
_INLINE_MATH = re.compile(
    r"(?<![\\$])\$(?![\s$])((?:\\.|[^$\\\n])+?)(?<![\s\\])\$(?![\d$])")
# The same spans as Google's exporter writes them since (at least)
# 2026-10: the `$` delimiters arrive backslash-escaped, which hides the
# span from the two patterns above (#152). A price (`\$5 and \$10`) has
# no closing delimiter after a non-space and stays escaped.
_EXPORT_DISPLAY_MATH = re.compile(r"\\\$\\\$(.+?)\\\$\\\$", re.DOTALL)
_EXPORT_INLINE_MATH = re.compile(
    r"(?<![\\$])\\\$(?![\s$])((?:\\.|[^$\\\n])+?)(?<!\s)\\\$(?![\d$])")
_MATH_SENTINEL = "\x00M%d\x00"
_SENTINEL_RE = re.compile(r"\x00M(\d+)\x00")


def map_math(text: str, fn) -> str:
    """fn over the inside of every math span, delimiters excluded."""
    text = _DISPLAY_MATH.sub(lambda m: "$$" + fn(m.group(1)) + "$$", text)
    return _INLINE_MATH.sub(lambda m: "$" + fn(m.group(1)) + "$", text)


def _lift_math(text: str) -> tuple[str, list[str]]:
    spans: list[str] = []

    def display(m: re.Match) -> str:
        spans.append("$$ " + " ".join(m.group(1).split()) + " $$")
        return _MATH_SENTINEL % (len(spans) - 1)

    def inline(m: re.Match) -> str:
        spans.append(m.group(0))
        return _MATH_SENTINEL % (len(spans) - 1)

    text = _DISPLAY_MATH.sub(display, text)
    # An unclosed $$ leaves TeX in the prose stream. _ESCAPE would then
    # gut row breaks (\\ → \) and \{ \| \= — and push_doc writes the
    # normalized bytes back to disk, so the damage is permanent. Keep
    # the remnant opaque (byte-identical aside from the file's trailing
    # newline, which normalize re-adds) until a closer exists.
    if "$$" in text:
        at = text.index("$$")
        head, tail = text[:at], text[at:]
        head = _INLINE_MATH.sub(inline, head)
        spans.append(tail.rstrip("\n"))
        return head + (_MATH_SENTINEL % (len(spans) - 1)), spans
    return _INLINE_MATH.sub(inline, text), spans


def _restore_math(text: str, spans: list[str]) -> str:
    return _SENTINEL_RE.sub(lambda m: spans[int(m.group(1))], text)


_MATH_ACTIVE = set("*_<>~|#[]`")

# Inline emphasis the markdown importer renders INTO FORMATTING: the
# asterisks and underscores are gone from the Doc's text runs. Order
# matters — the double forms first, so `**x**` is not read as two
# italic markers around a bare `*`.
_EMPHASIS = (
    re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", re.DOTALL),
    re.compile(r"__(?=\S)(.+?)(?<=\S)__", re.DOTALL),
    re.compile(r"(?<![\w*\\])\*(?=\S)(.+?)(?<=\S)\*(?![\w*])", re.DOTALL),
    re.compile(r"(?<![\w_\\])_(?=\S)(.+?)(?<=\S)_(?![\w_])", re.DOTALL),
)


_EMPHASIS_ANY = re.compile(
    r"\*\*(?=\S)(?P<b>.+?)(?<=\S)\*\*"
    r"|__(?=\S)(?P<b2>.+?)(?<=\S)__"
    r"|(?<![\w*\\])\*(?=\S)(?P<i>.+?)(?<=\S)\*(?![\w*])"
    r"|(?<![\w_\\])_(?=\S)(?P<i2>.+?)(?<=\S)_(?![\w_])", re.DOTALL)


HARD_BREAK_CHAR = chr(11)   # U+000B: a line break inside a Doc paragraph


def render_emphasis(markdown: str) -> tuple[str, list[tuple[int, int, dict]]]:
    r"""(text as the Doc holds it, [(start16, end16, textStyle)]) for a
    markdown span: the emphasis markers dropped and, for each run they
    marked, the style the importer would have given it — so a writer can
    insert the text and apply the runs, and the tab shows italics where
    the markdown had `*…*` rather than the asterisks themselves. One
    level of nesting (`**a *b* c**`) is honoured. Math spans are
    literal text throughout: escaped for the importer, shown as TeX,
    never emphasis."""
    # A verse stanza's hard line breaks (backslash-newline, the canonical
    # form since verse-and-cast-design.md section 2) are held by the Doc
    # as line-break characters (U+000B) inside one paragraph: the text
    # Google's importer produces, and what its markdown export gives back
    # as two trailing spaces. Map them here so the locator and the writer
    # both see the tab's own text (found 2026-09-27, first lens push on a
    # verse file: five of nine forms "old text not found verbatim").
    markdown = markdown.replace("\\\n", HARD_BREAK_CHAR)
    out: list[str] = []
    styles: list[tuple[int, int, dict]] = []
    length16 = 0

    def emit(text: str) -> None:
        nonlocal length16
        out.append(text)
        length16 += _utf16_len(text)

    def walk(text: str) -> None:
        pos = 0
        for m in _EMPHASIS_ANY.finditer(text):
            emit(text[pos:m.start()])
            inner = m.group("b") or m.group("b2") or m.group("i") or m.group("i2")
            style = ({"bold": True} if m.group("b") or m.group("b2")
                     else {"italic": True})
            start = length16
            walk(inner)
            styles.append((start, length16, style))
            pos = m.end()
        emit(text[pos:])

    pos = 0
    for m in re.finditer(_DISPLAY_MATH.pattern + "|" + _INLINE_MATH.pattern,
                         markdown, re.DOTALL):
        walk(markdown[pos:m.start()])
        emit(m.group(0))
        pos = m.end()
    walk(markdown[pos:])
    return "".join(out), styles


_HEADING_MARK = re.compile(r"^#{1,6}\s+")


def tab_anchor_text(paragraph: str) -> str:
    """What the tab holds for a paragraph used as an insertion ANCHOR:
    the rendered text (emphasis dropped, hard breaks as line-break
    characters) with a heading's `## ` marker gone too — the importer
    turns the marker into a paragraph style, and a judgment tag planted
    after a poem's title anchors on the title (found 2026-09-27)."""
    return _HEADING_MARK.sub("", rendered_text(paragraph).strip())


def rendered_text(markdown: str) -> str:
    r"""What the Doc's TEXT RUNS hold for a markdown span.

    `push_doc` hands the essay to Google's markdown importer, which
    renders `*prohairesis*` as italic "prohairesis": the emphasis lives
    in textStyle, and the asterisks are not in the text. A surgical
    write that searches the tab for the paragraph's raw markdown
    therefore never finds a paragraph carrying inline emphasis — the
    Connections essays, which italicize every borrowed term, could not
    take a single pending form (found 2026-09-06, first interlocutor
    beat: five of six forms "old text not found verbatim in the tab").

    This is the map from markdown to that text: emphasis markers
    dropped, everything else verbatim. Math is opaque (escaped for the
    importer, shown as TeX) and footnote syntax is escaped to literal
    brackets, so both keep their bytes. The markdown EXPORT is the
    inverse map, and it is what the resolve reads, which is why the
    record keeps the markdown and only the LOCATOR looks at this."""
    return render_emphasis(markdown)[0]


def escape_math(markdown: str) -> str:
    r"""Math spans for Google's markdown importer, which consumes one
    backslash escape from every punctuation character it meets: a `\\`
    row break, `\{`, `\|`, `\,` all lose their backslash, and bare `_`,
    `*`, `<` can read as emphasis or HTML. Doubling every backslash and
    escaping the markdown-active characters hands back exactly what was
    sent (measured 2026-09-02). Apply ONLY to bytes handed to the
    importer, exactly like escape_footnotes; the tab shows the TeX."""
    def esc(body: str) -> str:
        return "".join("\\\\" if c == "\\" else
                       "\\" + c if c in _MATH_ACTIVE else c for c in body)
    return map_math(markdown, esc)


def unescape_export_math(markdown: str) -> str:
    r"""Docs-export backslash escapes inside math spans — `\\frac` →
    `\frac`, `\_` → `_`, `\=` → `=` (a real TeX accent, which would
    corrupt every equation with an equals sign). Pull-side only:
    normalize_markdown leaves math alone because on a local file every
    backslash is TeX. Escaped delimiters (`\$…\$`, `\$\$…\$\$`) are
    unescaped first so the spans are found at all (#152)."""
    markdown = _EXPORT_DISPLAY_MATH.sub(
        lambda m: "$$" + m.group(1) + "$$", markdown)
    markdown = _EXPORT_INLINE_MATH.sub(
        lambda m: "$" + m.group(1) + "$", markdown)
    return map_math(markdown, lambda body: _ESCAPE.sub(r"\1", body))


def escape_footnotes(markdown: str) -> str:
    """Pandoc footnote syntax — [^X1] references and [^X1]: definitions —
    must reach the Doc as LITERAL text, matching the manuscript convention
    the author reads in the tabs. Google's markdown importer consumes the
    syntax, and the tab transplant then drops whatever it made of it
    (it-e63eabd58b11: every footnote silently lost on push). A
    backslash-escaped bracket imports as a literal bracket, and
    normalize_markdown's _ESCAPE strips the backslash on export, so the
    push→pull round trip stays byte-clean against the unescaped hash.
    Apply ONLY to bytes handed to a markdown import, never to the text
    that is hashed or written locally."""
    return _FOOTNOTE_SYNTAX.sub(r"\\[^", markdown)


def normalize_markdown(text: str) -> str:
    """Canonical markdown so identical prose is byte-identical regardless
    of which editor (local, Obsidian, Google Docs export) produced it.
    Idempotent: normalize(normalize(x)) == normalize(x)."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace(" ", " ")           # NBSP → space
    # Fences before math: a code example of TeX must stay verbatim, and
    # the prose rules below must never rewrite inside a fence (push_doc
    # persists normalize to disk whenever the bytes move).
    text, fences = _lift_fences(text)
    text, math = _lift_math(text)                # TeX is opaque from here
    text = _ESCAPE.sub(r"\1", text)              # Docs-export backslash escapes
    text = _THEMATIC_BREAK.sub("---", text)      # before bullets (see above)
    text = _BULLET.sub(r"\1- ", text)            # '*' bullets → '-'
    text = _HEADING.sub(lambda m: m.group(1) + " ", text)
    # A Docs-exported table carries an aligned delimiter row (| :---- |);
    # the canonical form is | --- | per column, so a table that round-trips
    # through a tab is byte-identical to the one that was pushed.
    text = _TABLE_DELIM.sub(
        lambda m: "|" + "|".join(" --- " for _ in m.group(0).strip().split("|")[1:-1]) + "|",
        text)
    # Doc-pasted images export as dangling ![][imageN] refs — churn, not
    # content. Stripping here (not just in pull) keeps push, pull, and
    # session-start reconciliation agreeing on the canonical text.
    text, _ = strip_dangling(text)
    # A hard line break inside a paragraph (a stanza line; a Doc-side
    # shift-enter) exports as two trailing spaces. The canonical form is
    # the backslash break, which Google imports correctly, pandoc renders
    # in every output, and no whitespace pass can eat — so it is rewritten
    # BEFORE trailing whitespace is stripped. Only a break followed by a
    # continuation line counts: spaces before a blank line or a block
    # opener (heading, list, table, quote, tag) are churn, as before.
    # (verse-and-cast-design.md §2)
    text = _HARD_BREAK.sub("\\\\\n", text)
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    # Empty heading paragraphs (a Doc styling artifact, e.g. a blank
    # Subtitle line) are dropped, never merged into a neighbor.
    text = _EMPTY_HEADING.sub("", text)
    # Horizontal rules get uniform blank-line padding (tab exports emit
    # them flush against neighbors; local files usually pad them).
    text = re.sub(r"\n*^(---|\*\*\*|___)$\n*", r"\n\n---\n\n", text,
                  flags=re.MULTILINE)
    text = re.sub(r"\n{3,}", "\n\n", text)       # collapse blank-line runs
    text = _restore_math(text.strip("\n"), math)
    text = _restore_fences(text, fences)
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
            os.chmod(token_path, 0o600)
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
        os.chmod(token_path, 0o600)
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

    def reset_style(start: int, end: int) -> dict:
        return {"updateTextStyle": {
            "range": {"tabId": tab_id,
                      "startIndex": start, "endIndex": end},
            "textStyle": {"bold": False, "italic": False,
                          "underline": False},
            "fields": "bold,italic,underline"}}

    def run_style_requests(start: int, runs: list, limit: int) -> list[dict]:
        out: list[dict] = []
        offset = start
        for run_text, text_style in runs:
            end = min(offset + len(run_text), limit)
            fields = {k: True for k in ("bold", "italic", "underline")
                      if text_style.get(k)}
            link = text_style.get("link", {}).get("url")
            payload: dict = dict(fields)
            if link:
                payload["link"] = {"url": link}
            if payload and run_text.strip() and end > offset:
                out.append({"updateTextStyle": {
                    "range": {"tabId": tab_id, "startIndex": offset,
                              "endIndex": end},
                    "textStyle": payload,
                    "fields": ",".join(sorted(payload))}})
            offset += len(run_text)
        return out

    for element in doc.get("body", {}).get("content", []):
        table = element.get("table")
        if table:
            # A markdown pipe table imports as a Docs table. It is rebuilt
            # with insertTable and the cells filled afterwards (it-08b8b0a0c737:
            # the pronunciation dictionary never reached its tab). Index
            # arithmetic, measured on a Drive-imported table: insertTable
            # puts a newline at `index` and the table at index + 1; the
            # table costs 2 (start and end), each row 1, each cell 1 plus its paragraph
            # (a lone newline while empty). Cell texts are inserted from
            # the LAST cell backwards so every precomputed index stays
            # valid; the cursor then advances by the empty layout plus
            # every character inserted.
            rows = table.get("tableRows", [])
            ncols = max((len(r.get("tableCells", [])) for r in rows),
                        default=0)
            nrows = len(rows)
            if not nrows or not ncols:
                continue
            requests.append({"insertTable": {
                "rows": nrows, "columns": ncols,
                "location": {"tabId": tab_id, "index": cursor}}})
            table_start = cursor + 1
            cells: list[tuple[int, str, list]] = []
            for r, row in enumerate(rows):
                row_start = table_start + 1 + r * (1 + 2 * ncols)
                for c, cell in enumerate(row.get("tableCells", [])):
                    para_index = row_start + 1 + c * 2 + 1
                    runs = [(e["textRun"].get("content", ""),
                             e["textRun"].get("textStyle", {}))
                            for block in cell.get("content", [])
                            if "paragraph" in block
                            for e in block["paragraph"].get("elements", [])
                            if "textRun" in e]
                    text = "".join(t for t, _ in runs)
                    if text.endswith("\n"):
                        text = text[:-1]  # the cell's own paragraph ends it
                    cells.append((para_index, text, runs))
            inserted = 0
            for para_index, text, runs in reversed(cells):
                if not text:
                    continue
                requests.append({"insertText": {
                    "location": {"tabId": tab_id, "index": para_index},
                    "text": text}})
                end = para_index + len(text)
                requests.append(reset_style(para_index, end))
                requests += run_style_requests(para_index, runs, end)
                inserted += len(text)
            # newline before the table (1) + table start and end markers (2)
            # + rows and cells + every character inserted into the cells.
            cursor += 3 + nrows * (1 + 2 * ncols) + inserted
            continue
        paragraph = element.get("paragraph")
        if not paragraph:
            continue  # section breaks: not manuscript territory
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
        if any("horizontalRule" in e for e in paragraph.get("elements", [])):
            requests.append({"insertText": {
                "location": {"tabId": tab_id, "index": cursor},
                "text": "---\n"}})
            requests.append(reset_style(cursor, cursor + 4))
            cursor += 4
            continue
        if not text.strip():
            # Markdown's blank separator lines import as bare-newline
            # paragraphs; transplanting them doubles the Doc's paragraph
            # spacing (it-69b61b6d7fa2). Paragraph breaks survive without
            # them — every prose paragraph ends with its own newline —
            # and the exporter re-inserts blank separators on pull.
            continue
        start = cursor
        requests.append({"insertText": {
            "location": {"tabId": tab_id, "index": start}, "text": text}})
        cursor += len(text)
        requests.append(reset_style(start, cursor))
        # Paragraph style is set EXPLICITLY for every paragraph, prose
        # included: inserted paragraphs inherit the residual paragraph's
        # style after deleteContentRange (the paragraph-level twin of the
        # italic snowball above), so a tab that ended on a heading turned
        # every pushed prose paragraph into H1 (it-641d1aa4e3c0).
        requests.append({"updateParagraphStyle": {
            "range": {"tabId": tab_id,
                      "startIndex": start, "endIndex": cursor},
            "paragraphStyle": {"namedStyleType": style},
            "fields": "namedStyleType"}})
        requests += run_style_requests(start, runs, cursor)
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
# trip). Pull harvests all open comments by default and stores new ones
# verbatim, but never resolves them: each stays open in the Doc as a margin
# thread (a working conversation) until the author resolves it or a thread
# verdict closes it. resolve_comments_with_receipt() remains available but
# pull does not call it.
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
                   "replies(id,content,author(displayName)))",
        ).execute()
        # Drive entity-encodes quoted text and comment bodies (&#39;
        # for an apostrophe): unescape at the boundary so attribution
        # matches the manuscript and evidence stores the author's actual
        # characters (it-49edf0c323d1).
        import html as _html

        for c in resp.get("comments", []):
            if c.get("resolved"):
                continue
            if c.get("quotedFileContent", {}).get("value"):
                c["quotedFileContent"]["value"] = _html.unescape(
                    c["quotedFileContent"]["value"])
            if c.get("content"):
                c["content"] = _html.unescape(c["content"])
            for reply in c.get("replies", []):
                if reply.get("content"):
                    reply["content"] = _html.unescape(reply["content"])
            comments.append(c)
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

    seen = {r["comment_id"] for r in db.all(
        "SELECT comment_id FROM doc_comments WHERE manuscript_id = ?",
        (manuscript["id"],))}
    out = []
    for c in comments:
        quoted = (c.get("quotedFileContent") or {}).get("value") or ""
        relpath, heading = locate_quote(files, quoted)
        location = f"{relpath}#{heading}" if relpath and heading else relpath
        if c["id"] not in seen:
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
            seen.add(c["id"])
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


# Sockets to Google must never wait forever: a stalled connection with
# no timeout froze `authorlm shell` at start (reconcile's status call
# blocked in poll() indefinitely, 2026-08-10). googleapiclient's
# httplib2 transport takes its timeout at construction; AuthorizedHttp
# wraps it with the credentials the same way build() would.
HTTP_TIMEOUT_SECONDS = 60


def _authorized_http(config: dict, workspace: str | None,
                     interactive: bool):
    import httplib2
    from google_auth_httplib2 import AuthorizedHttp

    return AuthorizedHttp(
        get_credentials(config, workspace, interactive=interactive),
        http=httplib2.Http(timeout=HTTP_TIMEOUT_SECONDS))


def get_service(config: dict, workspace: str | None = None,
                interactive: bool = True):
    from googleapiclient.discovery import build

    return build(
        "drive", "v3",
        http=_authorized_http(config, workspace, interactive),
        cache_discovery=False,
    )


def get_docs_service(config: dict, workspace: str | None = None,
                     interactive: bool = True):
    from googleapiclient.discovery import build

    return build(
        "docs", "v1",
        http=_authorized_http(config, workspace, interactive),
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
    (reading_order falls back to alphabetical when no toc.toml exists —
    which is exactly right for the workspace directory).

    THE ONE INVERSION (§15.22, §2.5 row 19). Every other site excludes a
    sidecar; this one includes it, appended LAST, after the essays and
    before the manifest. The dictionary is edited in the Doc like an
    essay — that is the Sponsor's ruling — so it must have a tab, and a
    tab it does not have is a dictionary the author cannot reach from
    the place they actually work."""
    from .revisions import read_manuscript_files
    from .structure import is_sidecar, reading_order

    files = read_manuscript_files(bridge.root)
    order, _ = reading_order(files)
    return order + [n for n in sorted(files) if is_sidecar(n)]


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
        from .revisions import read_manuscript_files as _rmf
        from .structure import matter_map as _matter_map

        _write_manifest(docs_service, master_id, manifest_id,
                        bridge.doc_name, entry.get("_doc_version", 1),
                        inventory=build_manifest_inventory(bridge.root),
                        stats=(build_manifest_stats(bridge.root)
                               if bridge.rich_manifest else None),
                        matter=_matter_map(_rmf(bridge.root)))
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
    from .api import is_placeholder

    if is_placeholder(text):
        # As for an empty file: a readability score for a mid-rewrite
        # marker is noise in the manifest.
        return {}
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
                  pushed_at: str | None = None,
                  matter: dict[str, str] | None = None) -> str:
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
        # Back matter = files the TOC marks matter="back".
        back = sum(words for name, _, words in inventory
                   if (matter or {}).get(name) == "back")
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
                  "If a chapter listed above has no matching tab, it was "
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
                    stats: dict[str, dict] | None = None,
                    matter: dict[str, str] | None = None) -> None:
    requests: list[dict] = []
    end = _tab_end(docs_service, master_id, tab_id)
    if end > 2:
        requests.append({"deleteContentRange": {"range": {
            "tabId": tab_id, "startIndex": 1, "endIndex": end - 1}}})
    requests.append({"insertText": {
        "location": {"tabId": tab_id, "index": 1},
        "text": manifest_text(manuscript_name, doc_version, inventory,
                              stats, matter=matter)}})
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


def split_tabbed_export(text: str, known_files,
                        order: list[str] | None = None) -> dict[str, str]:
    """Split a whole-master markdown export into per-file sections. Tab
    titles export as top-level headings ('# **<title>**'); only headings
    naming a known mapped file are boundaries — content headings, even
    H1s, pass through untouched.

    `order` is the tab titles in true document order (the Docs API's tab
    tree, e.g. from `walk_tabs`). When given, a name in `order` only
    starts a new section at its correct position in that sequence — so a
    prose heading that happens to repeat some *other* tab's title, out of
    turn, is never mistaken for a boundary (it-x7-2). A known name absent
    from `order` (no positional info for it, e.g. docs_service wasn't
    available) still matches anywhere, as before."""
    pattern = re.compile(r"^#\s+\*{0,2}(.+?)\*{0,2}\s*$")
    sections: dict[str, list[str]] = {}
    current: str | None = None
    positioned = list(order) if order else []
    idx = 0
    for line in text.splitlines():
        match = pattern.match(line)
        name = match.group(1).strip() if match else None
        boundary = False
        if name is not None:
            if idx < len(positioned) and name == positioned[idx]:
                boundary = True
                idx += 1
            elif name in known_files and name not in positioned:
                boundary = True
        if boundary:
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


def comment_bearing(db: Database, manuscript: dict, bridge: DocBridge,
                    relpath: str, service) -> bool:
    """True when the live Doc holds an open comment that anchors to this
    file's tab — or one whose anchor cannot be attributed to any file
    (it could be anywhere, including here). A rebuild push would orphan
    those anchors (it-77ef98f5289e); the caller routes such tabs through
    the surgical diff push. Fetch failure counts as bearing: when the
    margin's state is unknown, the safe path is the one that cannot
    destroy it."""
    links = _mapping(db, manuscript).get(bridge.meta_key, {})
    entry = links.get(relpath)
    if not links.get("_master_id") or not (
            isinstance(entry, dict) and entry.get("tab_id")):
        return False  # no tab yet — nothing anchored to protect
    try:
        comments = fetch_open_comments(service, links["_master_id"])
    except Exception:
        return True
    if not comments:
        return False
    from .revisions import read_manuscript_files

    files = {bridge.display_prefix + rel: text
             for rel, text in read_manuscript_files(bridge.root).items()}
    mine = bridge.display_prefix + relpath
    for c in comments:
        quote = (c.get("quotedFileContent") or {}).get("value", "")
        rel, _ = locate_quote(files, quote)
        if rel is None or rel == mine:
            return True
    return False


def _rewrite_tab(service, docs_service, master_id: str, tab_id: str,
                 markdown: str, temp_name: str) -> None:
    """Rebuild one tab's body from markdown: temp-doc import (Google owns
    the conversion) → transplant into the tab → temp deleted."""
    from googleapiclient.http import MediaInMemoryUpload

    media = MediaInMemoryUpload(markdown.encode("utf-8"),
                                mimetype=MARKDOWN_MIME)
    temp = service.files().create(
        body={"name": temp_name, "mimeType": GDOC_MIME},
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


# The remedy each producer's pending forms are settled by. A refusal
# that names the wrong verb is worse than a refusal that names none.
_SETTLE_REMEDY = {"critique": "critique resolve {file}",
                  "filter": "filter resolve {file}",
                  "footnote": "footnote resolve {file}",
                  "explain": "explain resolve {file}"}


def forms_pending(db: Database, manuscript_id: str,
                  relpath: str) -> str | None:
    """The `origin_type` of any pending form on this file, or None.

    Generalized from `critique_forms_pending` over `origin_type`
    (filter-pass design §2.3 / §6 step 4b). It filtered on
    `origin_type = 'critique'` only, so a file carrying a FILTER's
    written forms sailed past it and pushed its markers straight into
    the Doc — where the next pull would overwrite the local file and
    silently discard the resolve. Returning the origin rather than a bool
    is what lets the refusal name the right remedy."""
    row = db.one(
        "SELECT origin_type FROM doc_threads WHERE manuscript_id = ? "
        "AND file = ? AND state = 'written' "
        "ORDER BY created_at, id LIMIT 1",
        (manuscript_id, relpath),
    )
    return row["origin_type"] if row else None


def critique_forms_pending(db: Database, manuscript_id: str,
                           relpath: str) -> bool:
    """True when the essay still has critique pending forms in the Doc
    (threads in state 'written'). open_threads deliberately excludes
    these — critique resolve owns them — so push must check separately.

    Kept as the critique-specific predicate for callers that mean
    exactly that; the PUSH guard now uses `forms_pending`."""
    return db.one(
        "SELECT id FROM doc_threads WHERE manuscript_id = ? "
        "AND origin_type = 'critique' AND file = ? AND state = 'written' "
        "LIMIT 1",
        (manuscript_id, relpath),
    ) is not None


def _refuse_mid_rewrite(relpath: str, text: str) -> None:
    """A push makes the Doc the working copy (api._checkout_gate) — for a
    file that has no text yet, the next pull would then be the authority
    over an essay that lives only in the database.

    Two cases, both byte checks on the RAW file. `push_doc` and
    `diff_push` read the file with `path.read_text`, never through
    `read_manuscript_files`, so the canonicalization that hides pending
    forms from every observer does NOT hide them from here: without the
    second case a marked file would push its markers into the Doc.
    Byte-level rather than a database query, deliberately — it is the
    guard that survives a database that has lost the run row, and the
    state is fully described by the bytes (filter-pass design §2.3)."""
    from .api import is_placeholder
    from .staging import is_marked

    if is_placeholder(text):
        raise LookupError(
            f"'{relpath}' is mid-rewrite: an active writeup holds it and "
            f"the file is a placeholder, not the essay. Pushing would make "
            f"the Doc the working copy for text that does not exist yet. "
            f"Finish the writeup ('write complete') or put the old essay "
            f"back ('write abandon') first.")
    if is_marked(text):
        raise LookupError(
            f"'{relpath}' is mid-settle: the file on disk carries "
            f"<<old>>{{{{new}}}} pending-change forms, which are staged "
            f"proposals and not the essay. Pushing them would put the "
            f"markers in the Doc, and the next pull would overwrite the "
            f"local file and silently discard the resolve. Finalize it "
            f"('filter resolve {relpath}') or put the original text back "
            f"('filter unmark {relpath}') first.")


def push_doc(db: Database, manuscript: dict, query: str,
             title: str | None = None, service=None,
             docs_service=None, bridge: DocBridge | None = None) -> dict:
    """Tabbed push: normalize the local file, then rebuild its tab of the
    master Doc — markdown → temp-doc import (Google owns the conversion)
    → transplant into the tab → temp deleted. Marks the file checked out."""
    import hashlib

    bridge = bridge or manuscript_bridge(manuscript)
    relpath, path = _resolve(bridge, query)
    # The pause: <<old>>{{new}} / {{insert}} forms the author may have
    # post-edited live either in the Doc (critique) or on disk (filter).
    # open_threads only sees author_comment rows, so without this gate a
    # rebuild (or a surgical push that only guards '<<') would wipe the
    # forms — including via session-start reconcile auto-push.
    #
    # ABOVE _refuse_mid_rewrite, deliberately: when the database still
    # knows who staged the forms, the refusal can name that producer's
    # own settle verb. The byte-level check below is the SECOND guard,
    # and it is the one that survives a database that has lost the run
    # row — so the two are ordered most-informative first, and both are
    # separately reachable (filter-pass design §2.3 / F31).
    origin = forms_pending(db, manuscript["id"], relpath)
    if origin:
        remedy = _SETTLE_REMEDY.get(origin, "settle the staged edits")
        raise LookupError(
            f"'{relpath}' has {origin} pending forms — "
            f"run '{remedy.format(file=relpath)}' before pushing "
            "(a rebuild would wipe the author's post-edits)")
    _refuse_mid_rewrite(relpath, path.read_text(encoding="utf-8"))
    # A tab carrying a table cannot be pushed by paragraph diff: the Docs
    # API reports every cell as a paragraph while the markdown export
    # carries the table as one block, so the two sides never align and
    # diff_push refuses (seen on manifest.md the day tables started
    # reaching tabs, it-08b8b0a0c737). Such tabs rebuild instead; the
    # comment anchors that a rebuild orphans are the known price.
    # Fence bodies can contain markdown table examples; those are not
    # Docs tables and must not force a rebuild (which orphans open
    # margin-thread anchors). Detect delimiters in prose only.
    has_table = bool(_TABLE_DELIM.search(
        _lift_fences(path.read_text(encoding="utf-8"))[0]))
    if not has_table and (
            threads_mod.open_threads(db, manuscript["id"], relpath)
            or comment_bearing(db, manuscript, bridge, relpath, service)):
        # Surgical path: a rebuild would orphan the open margin threads
        # AND every open comment anchor (it-77ef98f5289e), so tabs
        # carrying either push by paragraph diff instead — anchors on
        # untouched text survive by construction.
        result = diff_push(db, manuscript, relpath, service, docs_service,
                           bridge=bridge)
        meta_now = _mapping(db, manuscript)
        master = meta_now.get(bridge.meta_key, {}).get("_master_id")
        tab = (meta_now.get(bridge.meta_key, {}).get(relpath) or {}).get(
            "tab_id")
        result.update(doc_id=master, url=tab_url(master, tab),
                      created=False, locally_normalized=False)
        return result
    text = path.read_text(encoding="utf-8")
    normalized = normalize_markdown(text)
    locally_normalized = normalized != text
    if locally_normalized:
        path.write_text(normalized, encoding="utf-8")
    # Illustration embed lines are local derived machinery — the Doc
    # shows only the readable [Illustration: …] tags. The stripped text
    # is also the hashed base, so pull comparisons stay embed-free.
    normalized = strip_embed_lines(normalized)

    meta = _mapping(db, manuscript)
    created = not meta.get(bridge.meta_key, {}).get("_master_id")
    master_id = ensure_master(db, manuscript, meta, service, docs_service,
                              bridge=bridge)
    entry = meta[bridge.meta_key][relpath]
    tab_id = entry["tab_id"]

    _rewrite_tab(service, docs_service, master_id, tab_id,
                 escape_math(escape_footnotes(normalized)),
                 f"authorlm-temp-{Path(relpath).stem}")
    apply_tab_spacing(docs_service, master_id, tab_id,
                      doc_spacing(manuscript))

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


def _sidecar_would_be_emptied(relpath: str, incoming: str,
                              current: str) -> bool:
    """True when writing `incoming` over a sidecar would drop every row
    it has. Deterministic, and it consults the sidecar's OWN parser
    rather than counting lines: what matters is whether the rows survive
    the round trip, not whether the bytes look like a table."""
    from . import pronunciations as pron

    # Scoped to the DICTIONARY by name, not to sidecars in general: this
    # guard consults the pronunciation parser, and a second sidecar whose
    # rows that parser cannot read would be judged empty on every push.
    # `manifest.md` needs no guard of its own — it is derived, so the push
    # that would "empty" it is the push that regenerates it.
    if relpath != pron.FILENAME:
        return False

    if not (current or "").strip():
        return False
    return bool(pron.parse(current)[0]) and not pron.parse(incoming)[0]


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
    pairs: insync | doc_moved (rewrite toc.toml) | local_moved (push
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
    """Rewrite toc.toml to the Doc's tab structure. Entries with no tab
    (never-pushed local files) are preserved after their previous
    predecessor; per-file attributes (matter) ride through unchanged.
    Returns the new (name, depth) tree."""
    from .structure import (TOC_FILENAME, is_sidecar, parents_to_tree,
                            serialize_toc_tree, toc_attrs)

    # SIDECAR GUARD 3 of 3 (§15.22 §2.5 row 25), and the one that does the
    # most damage if it is missing: writing `pronunciations.md` into
    # toc.toml makes it a CHAPTER, at which point every exclusion in
    # §2.5 unravels at once — it enters the reading order, the concept
    # scan mines it, and it ships inside the book. Guarded here as well
    # as at both call sites, because this function writes the file and a
    # guard on the caller is a guard on the caller.
    doc_pairs = [(n, p) for n, p in doc_pairs if not is_sidecar(n)]
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
    toc_path = Path(manuscript["path"]) / TOC_FILENAME
    attrs = (toc_attrs(toc_path.read_text(encoding="utf-8"))
             if toc_path.exists() else {})
    toc_path.write_text(serialize_toc_tree(new_tree, attrs),
                        encoding="utf-8")
    return new_tree


def sync_tab_structure(db: Database, manuscript: dict, docs_service) -> dict:
    """Push direction of the TOC↔tab sync: reposition/re-parent the master
    Doc's tabs to match toc.toml. Applies only when the Doc side hasn't
    moved since the last sync (local_moved / insync); a doc-side change
    means 'pull first'."""
    from .structure import (TOC_FILENAME, is_sidecar, parse_toc_tree,
                            tree_to_parents)

    meta = _mapping(db, manuscript)
    links = meta.get("gdocs", {})
    master_id = links.get("_master_id")
    if not master_id:
        return {"skipped": "no master doc"}
    toc_path = Path(manuscript["path"]) / TOC_FILENAME
    if not toc_path.exists():
        return {"skipped": "no toc.toml"}
    tab_props: list = []
    doc_pairs: list = []
    doc = docs_service.documents().get(
        documentId=master_id, includeTabsContent=True).execute()
    walk_tabs(doc.get("tabs", []), tab_props, doc_pairs)
    linked = {f for f, e in links.items()
              if not f.startswith("_") and isinstance(e, dict)
              and e.get("tab_id")}
    # SIDECAR GUARD 1 of 3 (§15.22 §2.5 row 23). `linked` contains the
    # dictionary — it has a tab — and `parse_toc_tree` never can, because
    # a sidecar is not in the reading order. Comparing a doc list that
    # holds it against a desired list that cannot would be a PERMANENT
    # `unsynced`, and tab-order sync would stop working the day the
    # dictionary first appeared.
    doc_pairs = [(n, p if p in linked else None)
                 for n, p in doc_pairs if n in linked and not is_sidecar(n)]
    desired = [(n, p if p in linked else None)
               for n, p in tree_to_parents(parse_toc_tree(
                   toc_path.read_text(encoding="utf-8")))
               if n in linked and not is_sidecar(n)]
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


def tab_marked_markdown(db: Database, manuscript: dict, file: str,
                          service, docs_service,
                          bridge: DocBridge | None = None) -> dict:
    """Fetch `file`'s pending-review text straight from the master Doc —
    the authoritative marked text for BOTH doc-transport settles
    (`critique resolve` and a doc-mode `filter resolve`), the
    same way pull_doc does: whole-Doc markdown export + order-aware
    split_tabbed_export (never the textRun walk that critique_tab_text
    used, which discards headings/bold/lists/link targets — it-x7-1).

    Three-ways the form-collapsed (OLD-only) rendering against local, on
    the same base hash push/pull use, so:
      - an edit the author made elsewhere in the tab (outside any pending
        form) still lands, because `state` only reflects that collapsed
        comparison — the returned `marked` text carries the elsewhere
        edit through untouched, same as pull would;
      - a genuine two-sided edit (local AND the Doc's settled content both
        moved off the last agreed base) comes back as state='conflict'
        instead of a side being picked silently.

    Returns {"marked": str | None, "state": "unchanged"|"changed"
    |"local_ahead"|"conflict"|"missing", "dangling": [...],
    "marker_warnings": [...]}."""
    bridge = bridge or manuscript_bridge(manuscript)
    meta = _mapping(db, manuscript)
    links = meta.get(bridge.meta_key, {})
    master_id = links.get("_master_id")
    entry = links.get(file) or {}
    tab_id = entry.get("tab_id")
    if not (master_id and tab_id):
        raise LookupError(f"'{file}' has no tab in the master Doc")

    tab_props: list[tuple[str, str]] = []
    if docs_service is not None:
        try:
            tab_doc = docs_service.documents().get(
                documentId=master_id, includeTabsContent=True).execute()
            walk_tabs(tab_doc.get("tabs", []), tab_props, [])
        except Exception:  # best-effort ordering — see split_tabbed_export
            pass

    data = service.files().export(
        fileId=master_id, mimeType=MARKDOWN_MIME).execute()
    whole = unescape_export_math(
        data.decode("utf-8") if isinstance(data, bytes) else str(data))
    mapped = [f for f, e in links.items()
              if not f.startswith("_") and isinstance(e, dict)
              and e.get("tab_id")]
    pmapped = prompt_links(links) if bridge.meta_key == "gdocs" else {}
    boundaries = (set(mapped) | {MANIFEST_TITLE} | {ILLUS_TAB_TITLE}
                  | set(pmapped) | {t for _, t in tab_props if t})
    # order-aware: a prose heading elsewhere that repeats another tab's
    # title must never be mistaken for that tab's boundary (it-x7-2).
    tab_order = [t for _, t in tab_props if t]
    sections = split_tabbed_export(whole, boundaries, order=tab_order)
    if file not in sections:
        return {"marked": None, "state": "missing", "dangling": [],
                "marker_warnings": []}
    _, dangling = strip_dangling(sections[file])
    marked = normalize_markdown(sections[file])
    # Collapse pending forms to their OLD half purely to classify sync
    # state — `marked` (forms intact) is what actually gets resolved.
    collapsed, marker_warns = threads_mod.strip_pending(marked)
    path = bridge.root / file
    current_raw = path.read_text(encoding="utf-8") if path.exists() else ""
    current = strip_embed_lines(current_raw)
    state = three_way(collapsed, current, entry.get("pushed_hash"))
    return {"marked": marked, "state": state, "dangling": dangling,
            "marker_warnings": marker_warns}


def read_manuscript(db: Database, manuscript: dict, service,
                    docs_service=None,
                    bridge: DocBridge | None = None) -> dict:
    """The whole manuscript AS IT STANDS IN THE DOC, every tab at once,
    writing nothing: no local file, no hash, no mapping, no thread or
    comment row changes, and no comment is replied to or resolved. The
    read an outside caller plans new revisions from
    (docs/outside-caller-design.md §3) — `tab_marked_markdown` reads one
    tab and `pull_doc` reads them all but lands them locally and strips
    the forms; this is the all-tabs read that does neither.

    One export of the master Doc, split with `split_tabbed_export` on
    the same boundary set and tab order `pull_doc` uses, plus one
    `fetch_open_comments`. Per file:

      marked    the tab's markdown with every pending form intact, or
                None when the file has no tab in the export
      settled   the same text with the forms collapsed to their OLD
                half — what a pull would land
      state     `three_way` of `settled` against the local file on the
                recorded base: unchanged | changed | local_ahead |
                conflict, or `missing` (no tab yet, or the tab is gone
                from the export)
      forms     the open forms in document order, {kind, old, new};
                kind is replace or insert, old is '' for an insertion
      comments  the open margin comments whose quoted text sits in this
                tab, oldest first
      pushed    whether a base is on record for the tab (a push or a
                pull has agreed its text). `ensure_master` gives every
                file a tab the first time ANY file is pushed, so a file
                never pushed itself has an EMPTY tab and no base: its
                `settled` is empty and its state reads `changed`. That
                tab is not the file's text; `pushed` is how to tell.

    A comment is attributed by its quote, matched against the TAB text
    (the settled text, then the marked text, so a comment anchored on a
    green half still finds its tab). A quote found in no tab or in
    several is never guessed at: it lands in `unattributed_comments`.

    Returns {"doc_id", "url", "files": {relpath: {...}},
    "unattributed_comments": [...], "comments_error": str | None}. A
    manuscript with no master Doc yet makes no Google call and reports
    every local file as `missing`."""
    bridge = bridge or manuscript_bridge(manuscript)
    links = _mapping(db, manuscript).get(bridge.meta_key, {})
    master_id = links.get("_master_id")
    mapped = [f for f, e in links.items()
              if not f.startswith("_") and isinstance(e, dict)
              and e.get("tab_id")]
    from .structure import TOC_FILENAME

    local = sorted(n for n in iter_manuscript_paths(bridge.root)
                   if n != TOC_FILENAME)
    report: dict = {"doc_id": master_id,
                    "url": tab_url(master_id) if master_id else None,
                    "files": {}, "unattributed_comments": [],
                    "comments_error": None}

    def blank(relpath: str) -> dict:
        link = links.get(relpath)
        tab_id = link.get("tab_id") if isinstance(link, dict) else None
        return {"marked": None, "settled": None, "state": "missing",
                "forms": [], "comments": [], "tab_id": tab_id,
                "pushed": bool(isinstance(link, dict)
                               and link.get("pushed_hash")),
                "url": tab_url(master_id, tab_id) if (master_id and tab_id)
                else None,
                "dangling": [], "marker_warnings": []}

    names = list(dict.fromkeys(mapped + local))
    if not master_id:
        report["files"] = {name: blank(name) for name in names}
        return report

    tab_props: list[tuple[str, str]] = []
    if docs_service is not None:
        try:
            tab_doc = docs_service.documents().get(
                documentId=master_id, includeTabsContent=True).execute()
            walk_tabs(tab_doc.get("tabs", []), tab_props, [])
        except Exception:  # best-effort ordering — see split_tabbed_export
            pass
    data = service.files().export(
        fileId=master_id, mimeType=MARKDOWN_MIME).execute()
    whole = unescape_export_math(
        data.decode("utf-8") if isinstance(data, bytes) else str(data))
    pmapped = prompt_links(links) if bridge.meta_key == "gdocs" else {}
    boundaries = (set(mapped) | {MANIFEST_TITLE} | {ILLUS_TAB_TITLE}
                  | set(pmapped) | {t for _, t in tab_props if t})
    tab_order = [t for _, t in tab_props if t]
    sections = split_tabbed_export(whole, boundaries, order=tab_order)

    for name in names:
        entry = blank(name)
        report["files"][name] = entry
        if name not in mapped or name not in sections:
            continue
        _, entry["dangling"] = strip_dangling(sections[name])
        marked = normalize_markdown(sections[name])
        settled, entry["marker_warnings"] = threads_mod.strip_pending(marked)
        path = bridge.root / name
        current = strip_embed_lines(
            path.read_text(encoding="utf-8") if path.exists() else "")
        entry.update(
            marked=marked, settled=settled,
            state=three_way(settled, current,
                            links[name].get("pushed_hash")),
            forms=[{"kind": f["kind"], "old": f["old"], "new": f["new"]}
                   for f in threads_mod.pending_forms(marked)])

    try:
        open_comments = fetch_open_comments(service, master_id)
    except Exception as err:  # the tabs are still worth returning
        report["comments_error"] = str(err)
        return report
    settled_of = {n: e["settled"] for n, e in report["files"].items()
                  if e["settled"] is not None}
    marked_of = {n: e["marked"] for n, e in report["files"].items()
                 if e["marked"] is not None}
    for c in open_comments:
        quoted = (c.get("quotedFileContent") or {}).get("value") or ""
        relpath, heading = locate_quote(settled_of, quoted)
        if relpath is None:
            relpath, heading = locate_quote(marked_of, quoted)
        row = {"comment_id": c["id"], "quote": quoted,
               "content": c.get("content", ""),
               "author": (c.get("author") or {}).get("displayName"),
               "created": c.get("createdTime"), "heading": heading,
               "replies": [r.get("content", "")
                           for r in c.get("replies", [])]}
        if relpath is None:
            report["unattributed_comments"].append(row)
        else:
            report["files"][relpath]["comments"].append(row)
    return report


def pull_doc(db: Database, manuscript: dict, query: str | None = None,
             service=None, force: bool = False,
             with_comments: bool = True, docs_service=None,
             bridge: DocBridge | None = None) -> dict:
    """Tabbed pull: export the master Doc once, split it into per-tab
    sections, normalize, and write the queried file — or every mapped file
    when query is None. Clears checkouts. Caller collects afterwards.

    By default also harvests every open comment on the master Doc
    (regardless of which file was pulled — comments are author feedback),
    ingests new ones into doc_comments, and leaves them open in the Doc as
    margin threads (never auto-resolved). The report's `comments` list is
    the to-address queue.

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
    illus_children: list[tuple[str, str]] = []
    illus_ids: set[str] = set()
    if docs_service is not None:
        try:
            tab_doc = docs_service.documents().get(
                documentId=master_id, includeTabsContent=True).execute()
            walk_tabs(tab_doc.get("tabs", []), tab_props, doc_pairs)
            if bridge.meta_key == "gdocs":
                _, illus_children, illus_ids = illus_subtree(
                    tab_doc.get("tabs", []), links)
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
        # The reserved illustrations subtree is invisible to essay
        # classification: its child tabs are titled '<slug>.md' and would
        # otherwise be adopted as new essays.
        cls = classify_tabs([(tid, t) for tid, t in tab_props
                             if tid not in illus_ids], links, local_files)
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
    whole = unescape_export_math(
        data.decode("utf-8") if isinstance(data, bytes) else str(data))
    mapped = [f for f, e in links.items()
              if not f.startswith("_") and isinstance(e, dict)
              and e.get("tab_id")]
    # Every tab title is a split boundary — a tab that isn't a boundary
    # would have its lines swallowed into whichever tab precedes it in the
    # export (the manifest always is one, even without the tab listing).
    # Mapped prompt-tab titles join unconditionally so a docs_service-less
    # pull still separates them.
    pmapped = (prompt_links(links) if bridge.meta_key == "gdocs" else {})
    boundaries = (set(mapped) | {MANIFEST_TITLE} | {ILLUS_TAB_TITLE}
                  | set(pmapped) | {t for _, t in tab_props if t})
    # tab_props is the Docs API's live tab order — pass it through so a
    # prose heading that repeats another tab's title, out of turn, is
    # never mistaken for that tab's boundary (it-x7-2).
    tab_order = [t for _, t in tab_props if t]
    sections = split_tabbed_export(whole, boundaries, order=tab_order)
    targets = mapped
    prompt_targets = sorted(pmapped)
    if query:
        try:
            relpath, _ = _resolve(bridge, query)
        except LookupError:
            # Not a manuscript file — an illustration prompt, perhaps.
            pname = _match_prompt(set(pmapped), query)
            if pname is None:
                raise
            targets, prompt_targets = [], [pname]
        else:
            if relpath not in mapped:
                raise LookupError(f"'{relpath}' has no tab in the master Doc "
                                  "— 'doc push' first")
            targets, prompt_targets = [relpath], []
    for relpath in targets:
        if relpath not in sections:
            report["missing"].append(relpath)
            continue
        # Warn on Doc-pasted images before normalize strips their refs:
        # the Doc's markdown bridge cannot carry images, so they'd vanish.
        _, dangling = strip_dangling(sections[relpath])
        if dangling:
            report.setdefault("dangling_images", {})[relpath] = dangling
        text = normalize_markdown(sections[relpath])
        # Margin threads: pending spans (<<old>>{{new}}) are review
        # state living only in the Doc; canonical local text is the old
        # half until the author approves (docs/margin-threads-design.md).
        text, marker_warns = threads_mod.strip_pending(text)
        if marker_warns:
            report.setdefault("marker_warnings", {})[relpath] = marker_warns
        path = bridge.root / relpath
        current_raw = path.read_text(encoding="utf-8") if path.exists() else ""
        # A marked local file is MID-SETTLE: its bytes carry staged
        # <<old>>{{new}} proposals the author may have post-edited, and
        # overwriting them with the tab would discard the resolve without
        # saying so. The checkout gate makes this mostly unreachable (a
        # marked file is not checked out, so nothing pulls it) — but
        # "mostly unreachable" is not a guard, and --force must not be
        # able to reach past it either. Skipped and reported by name,
        # the shape `conflicts` and `local_ahead` already use; the
        # remedy is `filter resolve` or `filter unmark`
        # (filter-pass design §2.3).
        if staging_is_marked(current_raw):
            report.setdefault("marked", []).append(relpath)
            entry = links[relpath]
            entry["checked_out"] = False
            continue
        # The sidecar guard (§15.22 §2.6) — the highest-value line in
        # this feature. One bad Docs export (a table converted to
        # bullets, a table dropped) would otherwise destroy the whole
        # pronunciation dictionary on the next pull, silently, with the
        # only recovery in a version history nobody thinks to look at.
        # A pull that parses to FEWER rows is NOT guarded: that is an
        # author deleting a row in the Doc, which is legitimate and must
        # work. Zero rows against a local file that has rows is not an
        # edit, it is a loss — and `--force` must not reach past it, for
        # the same reason the marked-file guard above refuses it:
        # "mostly unreachable" is not a guard, and neither is a guard
        # with a flag that turns it off.
        if _sidecar_would_be_emptied(relpath, text, current_raw):
            report.setdefault("sidecar_unparsable", []).append(relpath)
            continue
        # The bridge's canonical text is embed-free: illustration embed
        # lines are local derived machinery, so every comparison strips
        # them and a pull re-inserts them (the prior pick wins).
        current = strip_embed_lines(current_raw)
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
            path.write_text(reembed(text, bridge.root,
                                    capture_embeds(current_raw)),
                            encoding="utf-8")
            report["changed"].append(relpath)
        elif not path.exists():
            # An adopted tab with no content still materializes: the file
            # must exist for toc placement and future pushes.
            path.write_text(reembed(text, bridge.root), encoding="utf-8")
            report["changed"].append(relpath)
        else:
            report["unchanged"].append(relpath)
        entry["checked_out"] = False
        # The pulled content is the new agreed base for three-way compare.
        entry["pushed_hash"] = hashlib.sha256(text.encode()).hexdigest()[:16]

    # Illustration prompt tabs: same three-way discipline, reported under
    # their display path so conflicts and changes read like file paths.
    # Doc-side deletions never delete locally (the file is canonical; the
    # next push recreates the tab), so a missing section only reports.
    if prompt_targets:
        # Pre-write recovery point (X7-3): _illustrations/prompts/*.md
        # holds canonical author text nothing else snapshots — collect()
        # skips '_'-prefixed dirs on purpose. Must run before the loop
        # below writes anything.
        snapshot_prompts(db, manuscript, source="pre-doc-pull")
        title_of = dict(illus_children)
        prompts_dir = bridge.root / ILLUS_DIR / PROMPTS_SUBDIR
        if illus_children:
            known = {e.get("tab_id") for e in pmapped.values()}
            unknown = [t for tid, t in illus_children if tid not in known]
            if unknown:
                report["prompt_unknown"] = unknown
        for name in prompt_targets:
            entry = pmapped[name]
            display = ILLUS_DISPLAY_PREFIX + name
            actual = title_of.get(entry.get("tab_id"))
            if actual is not None and actual != name:
                report.setdefault("prompt_renamed", []).append((name, actual))
                continue
            if name not in sections:
                report["missing"].append(display)
                continue
            text = normalize_markdown(sections[name])
            path = prompts_dir / name
            raw = (path.read_text(encoding="utf-8")
                   if path.exists() else "")
            # Preview embeds are local derived machinery: compared
            # embed-free, preserved across the pull's rewrite.
            current, prior_embeds = split_prompt_embeds(raw)
            state = three_way(text, current, entry.get("pushed_hash"))
            if state == "conflict" and not force:
                report["conflicts"].append(display)
                continue
            if state == "local_ahead" and not force:
                report["local_ahead"].append(display)
                continue
            if text != current:
                prompts_dir.mkdir(parents=True, exist_ok=True)
                path.write_text(join_prompt_embeds(text, prior_embeds),
                                encoding="utf-8")
                report["changed"].append(display)
            else:
                report["unchanged"].append(display)
            entry["pushed_hash"] = hashlib.sha256(
                text.encode()).hexdigest()[:16]

    # TOC ↔ tab-structure sync (pull direction): the Doc's tab order and
    # nesting are the author's reordering interface. Three-way, like file
    # content: only-Doc-moved rewrites toc.toml; only-local-moved defers to
    # the next push; both → conflict, touch nothing.
    if doc_pairs and query is None and bridge.toc_sync:
        from .structure import (TOC_FILENAME, is_sidecar, parse_toc_tree,
                                tree_to_parents)

        linked = {f for f, e in links.items()
                  if not f.startswith("_") and isinstance(e, dict)
                  and e.get("tab_id")}
        # SIDECAR GUARD 2 of 3 (§15.22 §2.5 row 24). Same mismatched
        # comparison as sync_tab_structure's, on the pull side: the
        # dictionary has a tab and can never be in toc.toml, so leaving
        # it in `doc_struct` would report a permanent conflict — and,
        # worse, a `doc_moved` verdict would hand it to
        # rewrite_toc_from_doc.
        doc_struct = [(n, p if p in linked else None)
                      for n, p in doc_pairs
                      if n in linked and not is_sidecar(n)]
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
        harvest_comments(db, manuscript, master_id, service, docs_service,
                         bridge, report)
    report["threads"] = threads_mod.ledger(db, manuscript["id"])
    return report


def harvest_comments(db: Database, manuscript: dict, master_id: str,
                     service, docs_service, bridge: DocBridge,
                     report: dict) -> None:
    """Fetch every open Doc comment, ingest the fresh ones, reconcile
    stale rows against ones the author resolved by hand in the Doc, and
    run the verdict machine — the comment half of a pull, shared with
    session-start reconcile (it-d469ecbf3999: the margin is a working
    conversation; a session must not open blind to it). Failures land in
    report['comments_error'], never raise."""
    from .revisions import read_manuscript_files

    try:
        open_comments = fetch_open_comments(service, master_id)
    except Exception as err:  # harvest failure must never break a pull
        report["comments_error"] = str(err)
        return

    # Reconcile first, on every harvest — including an empty open set,
    # which is exactly the case an author resolving everything by hand
    # produces (it-ce3f6078b674: a comment resolved in the Doc UI, not
    # through 'doc decide' or a written-thread receipt, otherwise stays
    # 'ingested' forever). No reply is posted here — the author already
    # closed it in the Doc; only the local record needs to catch up.
    open_ids = {c["id"] for c in open_comments}
    stale = db.all(
        "SELECT id, comment_id FROM doc_comments WHERE manuscript_id = ? "
        "AND state = 'ingested'", (manuscript["id"],))
    reconciled = [row["comment_id"] for row in stale
                 if row["comment_id"] not in open_ids]
    for row in stale:
        if row["comment_id"] not in open_ids:
            db.update("doc_comments", row["id"], {"state": "resolved"})
    if reconciled:
        report["comments_reconciled"] = reconciled

    if open_comments:
        files = {bridge.display_prefix + rel: text
                 for rel, text in
                 read_manuscript_files(bridge.root).items()}
        # Margin threads: comments are conversations now — never
        # auto-resolved. New comments are ingested and surfaced to
        # chat for proposal drafting; replies on threads we own are
        # the verdict machine's input (step 2).
        fresh = [c for c in open_comments
                 if threads_mod.get_thread(
                     db, manuscript["id"], c["id"]) is None
                 and db.one(
                     "SELECT id FROM doc_comments WHERE "
                     "manuscript_id = ? AND comment_id = ?",
                     (manuscript["id"], c["id"])) is None]
        report["comments"] = ingest_comments(
            db, manuscript, fresh, files)
        report["thread_replies"] = []
        for c in open_comments:
            thread = threads_mod.get_thread(
                db, manuscript["id"], c["id"])
            if thread is None:
                continue
            for reply in c.get("replies", []):
                content = reply.get("content", "")
                if content and not threads_mod.is_ours(content):
                    report["thread_replies"].append({
                        "comment_id": c["id"],
                        "state": thread["state"],
                        "reply": content,
                        "verdict": threads_mod.classify_reply(content),
                    })
    # The verdict machine runs even when nothing is open: a fully
    # resolved margin is exactly when withdraw sweeps must fire.
    report["thread_actions"] = advance_threads(
        db, manuscript, open_comments, service, docs_service,
        bridge)


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
    whole = unescape_export_math(
        data.decode("utf-8") if isinstance(data, bytes) else str(data))
    mapped = [f for f, e in links.items()
              if not f.startswith("_") and isinstance(e, dict)
              and e.get("tab_id")]
    pmapped = prompt_links(links)
    # Every tab title is a split boundary, mapped or not — the same rule
    # pull_doc follows. A hand-made tab that is NOT a boundary has its
    # heading and body swallowed into whichever tab precedes it in the
    # export, and the three-way check then reads that as a Doc-side edit
    # and writes it over the local file (it-307dc1279a7e).
    tab_props: list[tuple[str, str]] = []
    if docs_service is not None:
        try:
            tab_doc = docs_service.documents().get(
                documentId=master_id, includeTabsContent=True).execute()
            walk_tabs(tab_doc.get("tabs", []), tab_props, [])
        except Exception as err:  # never block a writing session
            report["errors"].append({"file": "(tab listing)",
                                     "error": str(err)})
    known = set(mapped) | {MANIFEST_TITLE} | {ILLUS_TAB_TITLE} | set(pmapped)
    titles = {t for _, t in tab_props if t}
    # tab_props is the Docs API's live tab order — pass it through so a
    # prose heading that repeats another tab's title, out of turn, is
    # never mistaken for that tab's boundary (it-x7-2).
    tab_order = [t for _, t in tab_props if t]
    sections = split_tabbed_export(whole, known | titles, order=tab_order)
    ignored = sorted(t for t in titles - known
                     if t != manuscript["name"])
    if ignored:
        report["ignored_tabs"] = ignored
    dirty = False
    for relpath in mapped:
        entry = links[relpath]
        try:
            # Missing from the export ≠ empty tab. `sections.get(..., "")`
            # treated a deleted/renamed tab as Doc-cleared content, so a
            # local file still matching pushed_hash was auto-pulled to
            # empty — session-start data loss (pull_doc already guards
            # with membership; prompts below do too).
            if relpath not in sections:
                report["errors"].append({
                    "file": relpath,
                    "error": "tab missing from the Doc export — local "
                             "file left untouched; the next 'doc push' "
                             "recreates the tab"})
                continue
            doc_text = normalize_markdown(sections[relpath])
            # Pending margin-thread spans are review state, not content:
            # canonical comparison uses the old half on the Doc side too.
            doc_text, _ = threads_mod.strip_pending(doc_text)
            path = Path(manuscript["path"]) / relpath
            local_raw = (path.read_text(encoding="utf-8")
                         if path.exists() else "")
            # Embed-free comparison, like push/pull: local embed lines
            # are derived machinery the Doc never carries.
            local_text = strip_embed_lines(normalize_markdown(local_raw))
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
                # Same zero-rows guard as pull_doc (§15.22 §2.6). Session
                # start reconcile used to auto-pull here whenever local
                # still matched the base — so a Docs export that mangled
                # pronunciations.md into bullets wiped the dictionary on
                # every shell open, with no --force flag and no pull_doc
                # in the path. Refuse by name; leave local and the base
                # untouched so the next start keeps shouting.
                if _sidecar_would_be_emptied(relpath, doc_text, local_raw):
                    report.setdefault("sidecar_unparsable", []).append(
                        relpath)
                    continue
                path.write_text(reembed(doc_text, Path(manuscript["path"]),
                                        capture_embeds(local_raw)),
                                encoding="utf-8")
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

    # Illustration prompt tabs: same three-way discipline. A section
    # missing from the export (tab deleted Doc-side) only reports — the
    # local file is canonical and the next push recreates the tab. A
    # locally deleted file whose tab is unchanged auto-pushes, which
    # prunes the tab.
    prompt_ahead = []
    if pmapped:
        # Pre-write recovery point (X7-3) — same seam as pull_doc above:
        # session-start reconcile can also auto-pull a prompt tab, and
        # nothing else snapshots these files before that write.
        snapshot_prompts(db, manuscript, source="pre-reconcile")
    for name, entry in sorted(pmapped.items()):
        display = ILLUS_DISPLAY_PREFIX + name
        try:
            if name not in sections:
                report["errors"].append({
                    "file": display, "error": "tab missing from the Doc — "
                    "the next 'doc push' recreates it"})
                continue
            doc_text = normalize_markdown(sections[name])
            path = (Path(manuscript["path"]) / ILLUS_DIR / PROMPTS_SUBDIR
                    / name)
            raw = (path.read_text(encoding="utf-8")
                   if path.exists() else "")
            # Embed-free comparison, like push/pull: preview embeds are
            # derived machinery the Doc never carries.
            local_text, prior_embeds = split_prompt_embeds(
                normalize_markdown(raw))
            base = entry.get("pushed_hash")
            local_hash = _hashlib.sha256(
                local_text.encode()).hexdigest()[:16]
            doc_hash = _hashlib.sha256(doc_text.encode()).hexdigest()[:16]
            if doc_text == local_text:
                if entry.get("pushed_hash") != local_hash:
                    entry["pushed_hash"] = local_hash
                    dirty = True
                report["in_sync"].append(display)
            elif base and local_hash == base:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(join_prompt_embeds(doc_text, prior_embeds),
                                encoding="utf-8")
                entry["pushed_hash"] = doc_hash
                dirty = True
                report["pulled"].append(display)
            elif base and doc_hash == base:
                prompt_ahead.append(display)
            else:
                report["conflicts"].append(display)
        except Exception as err:  # network, API — never block the session
            report["errors"].append({"file": display, "error": str(err)})
    if prompt_ahead:
        if docs_service is None:
            report["pending_push"].extend(prompt_ahead)
        else:
            if dirty:
                _save_mapping(db, manuscript, meta)
                dirty = False
            try:
                push_prompt_tabs(db, manuscript, service, docs_service)
                meta = _mapping(db, manuscript)
                links = meta.get("gdocs", {})
                report["pushed"].extend(prompt_ahead)
            except Exception as err:
                report["errors"].append({"file": "(illustration prompts)",
                                         "error": str(err)})

    if dirty:
        _save_mapping(db, manuscript, meta)
    # The margin is a working conversation: a session must not open
    # blind to it (it-d469ecbf3999). Same harvest as a pull; failures
    # land in the report, never block the session.
    harvest_comments(db, manuscript, master_id, service, docs_service,
                     manuscript_bridge(manuscript), report)
    return report


# ------------------------------------------- illustration prompt mirror
# (the reserved 'illustrations' tab tree: one child tab per file in
# _illustrations/prompts/, body = the canonical prompt text, editable in
# the Doc and pulled back with the same three-way machinery as essays.
# Local files stay canonical: Doc-side tab deletions are recoverable —
# the next push recreates the tab — while a locally deleted prompt file
# prunes its tab at the next push.)

ILLUS_TAB_TITLE = "illustrations"
ILLUS_KEY_PREFIX = "_illusprompt/"          # reserved mapping keys
ILLUS_DISPLAY_PREFIX = "_illustrations/prompts/"
ILLUS_SENTINEL = ("Illustration prompt descriptions — one subtab per "
                  "prompt file; the text is the canonical description. "
                  "Edit freely (synced back on pull); do not rename or "
                  "hand-add subtabs.\n")


def prompt_links(links: dict) -> dict[str, dict]:
    """{<slug>.md: entry} for every reserved prompt key in the mapping."""
    return {k[len(ILLUS_KEY_PREFIX):]: e for k, e in links.items()
            if k.startswith(ILLUS_KEY_PREFIX) and isinstance(e, dict)}


def illus_subtree(tabs: list, links: dict) -> tuple[str | None, list, set]:
    """Locate the reserved 'illustrations' tab in a raw tab tree (by the
    remembered id, falling back to its reserved title): (tab_id, direct
    children as [(tab_id, title)], every subtree id including the root).
    The id set is the classification guard — nothing under the reserved
    tab may ever be adopted as an essay."""
    wanted = links.get("_illustrations_tab")

    def find(nodes):
        for node in nodes or []:
            props = node.get("tabProperties", {})
            if (props.get("tabId") == wanted
                    or (wanted is None
                        and props.get("title") == ILLUS_TAB_TITLE)):
                return node
            hit = find(node.get("childTabs"))
            if hit:
                return hit
        return None

    root = find(tabs)
    if root is None:
        return None, [], set()
    children = [(c.get("tabProperties", {}).get("tabId"),
                 c.get("tabProperties", {}).get("title", ""))
                for c in root.get("childTabs") or []]
    ids = set()

    def collect(node):
        ids.add(node.get("tabProperties", {}).get("tabId"))
        for child in node.get("childTabs") or []:
            collect(child)

    collect(root)
    return root.get("tabProperties", {}).get("tabId"), children, ids


def plan_prompt_sync(local: dict[str, str], mapped: dict[str, dict],
                     children: list[tuple[str, str]]) -> dict:
    """Pure diff of local prompt files against the mapping and the Doc's
    actual child tabs: {create, rewrite, prune, unknown, renamed}.
    Changed-only — rewrite fires on a pushed_hash mismatch; a mapped tab
    that vanished Doc-side lands in create (deletions there are
    recoverable); a hand-made tab is unknown and left untouched."""
    import hashlib

    title_of = dict(children)
    known_ids = {e.get("tab_id") for e in mapped.values()}
    plan: dict = {"create": [], "rewrite": [], "prune": [],
                  "unknown": [], "renamed": []}
    for name, text in sorted(local.items()):
        entry = mapped.get(name) or {}
        tab_id = entry.get("tab_id")
        if tab_id in title_of:
            if title_of[tab_id] != name:
                plan["renamed"].append((name, title_of[tab_id]))
            pushed = hashlib.sha256(text.encode()).hexdigest()[:16]
            if entry.get("pushed_hash") != pushed:
                plan["rewrite"].append(name)
        else:
            plan["create"].append(name)
    name_of = {e.get("tab_id"): n for n, e in mapped.items()}
    for tab_id, title in children:
        if tab_id in known_ids:
            if name_of[tab_id] not in local:
                plan["prune"].append((name_of[tab_id], tab_id))
        else:
            plan["unknown"].append(title)
    return plan


def _match_prompt(names, query: str) -> str | None:
    """Resolve a push/pull query against prompt file names: exact name,
    display path, or an unambiguous substring."""
    q = query.strip()
    if q.startswith(ILLUS_DISPLAY_PREFIX):
        q = q[len(ILLUS_DISPLAY_PREFIX):]
    if q in names:
        return q
    hits = [n for n in names if q.lower() in n.lower()]
    return hits[0] if len(hits) == 1 else None


def _write_illus_root(docs_service, master_id: str, tab_id: str) -> None:
    """The reserved tab's own body is a fixed sentinel, rewritten on
    every prompt push so hand edits never accumulate there."""
    requests: list[dict] = []
    end = _tab_end(docs_service, master_id, tab_id)
    if end > 2:
        requests.append({"deleteContentRange": {"range": {
            "tabId": tab_id, "startIndex": 1, "endIndex": end - 1}}})
    requests.append({"insertText": {
        "location": {"tabId": tab_id, "index": 1},
        "text": ILLUS_SENTINEL}})
    docs_service.documents().batchUpdate(
        documentId=master_id, body={"requests": requests}).execute()


def push_prompt_tabs(db: Database, manuscript: dict, service,
                     docs_service) -> dict:
    """Mirror _illustrations/prompts/ into the reserved tab tree —
    changed-only. Creates the root tab on first need (root-level like the
    manifest, created once and never property-updated — the API 500s on
    all root-tab property changes), creates/rewrites child tabs whose
    pushed_hash moved, prunes tabs whose local file is gone. Never
    touches essay tabs; the manuscript bridge only."""
    import hashlib

    meta = _mapping(db, manuscript)
    links = meta.setdefault("gdocs", {})
    master_id = links.get("_master_id")
    if not master_id:
        return {"skipped": "no master doc"}
    root = Path(manuscript["path"])
    local: dict[str, str] = {}
    directory = root / ILLUS_DIR / PROMPTS_SUBDIR
    for path in (sorted(directory.glob("*.md"))
                 if directory.is_dir() else []):
        raw = path.read_text(encoding="utf-8")
        normalized = normalize_markdown(raw)
        if normalized != raw:
            path.write_text(normalized, encoding="utf-8")
        # Preview embeds are derived machinery — the Doc, the hash, and
        # the three-way base never see them.
        local[path.name] = split_prompt_embeds(normalized)[0]

    doc = docs_service.documents().get(
        documentId=master_id, includeTabsContent=True).execute()
    illus_id, children, _ = illus_subtree(doc.get("tabs", []), links)
    report: dict = {"created": [], "updated": [], "pruned": [],
                    "unknown": [], "renamed": []}
    if illus_id is None:
        if not local:
            return report  # no prompts and no tab: nothing to mirror
        reply = docs_service.documents().batchUpdate(
            documentId=master_id,
            body={"requests": [{"addDocumentTab": {
                "tabProperties": {"title": ILLUS_TAB_TITLE}}}]},
        ).execute()
        illus_id = reply["replies"][0]["addDocumentTab"][
            "tabProperties"]["tabId"]
        children = []
    links["_illustrations_tab"] = illus_id

    plan = plan_prompt_sync(local, prompt_links(links), children)
    report["unknown"] = plan["unknown"]
    report["renamed"] = plan["renamed"]
    for name in plan["create"]:
        reply = docs_service.documents().batchUpdate(
            documentId=master_id,
            body={"requests": [{"addDocumentTab": {"tabProperties": {
                "title": name, "parentTabId": illus_id}}}]},
        ).execute()
        tab_id = reply["replies"][0]["addDocumentTab"][
            "tabProperties"]["tabId"]
        links[ILLUS_KEY_PREFIX + name] = {"tab_id": tab_id}
        report["created"].append(name)
    for name in plan["create"] + plan["rewrite"]:
        entry = links[ILLUS_KEY_PREFIX + name]
        _rewrite_tab(service, docs_service, master_id, entry["tab_id"],
                     local[name], f"authorlm-temp-prompt-{Path(name).stem}")
        entry["pushed_hash"] = hashlib.sha256(
            local[name].encode()).hexdigest()[:16]
        if name in plan["rewrite"]:
            report["updated"].append(name)
    for name, tab_id in plan["prune"]:
        docs_service.documents().batchUpdate(
            documentId=master_id,
            body={"requests": [{"deleteTab": {"tabId": tab_id}}]},
        ).execute()
        links.pop(ILLUS_KEY_PREFIX + name, None)
        report["pruned"].append(name)
    try:
        _write_illus_root(docs_service, master_id, illus_id)
    except Exception:
        pass  # the sentinel is cosmetic, never fatal to a push
    _save_mapping(db, manuscript, meta)
    return report


# ------------------------------------------------------- margin threads
# (docs/margin-threads-design.md — the Docs/Drive side; deterministic
# grammar and records live in threads.py)

def _utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _paragraphs_in_body(content: list) -> list[dict]:
    """Structural paragraph elements from a Docs body, descending into
    table cells. Top-level `item.get("paragraph")` alone misses every
    cell; after tables started reaching tabs (transplant_requests),
    `_tab_runs` / `_locate_in_tab` then mapped post-table prose onto
    indices inside the table and planted forms in the wrong place."""
    out: list[dict] = []
    for item in content or []:
        if "paragraph" in item:
            out.append(item)
            continue
        table = item.get("table")
        if not table:
            continue
        for row in table.get("tableRows", []):
            for cell in row.get("tableCells", []):
                out.extend(_paragraphs_in_body(cell.get("content", [])))
    return out


def _tab_runs(docs_service, master_id: str, tab_id: str) -> list[tuple[int, str]]:
    """(doc_start_index, content) for every text run in a tab, in order —
    including runs inside table cells."""
    doc = docs_service.documents().get(
        documentId=master_id, includeTabsContent=True).execute()
    runs: list[tuple[int, str]] = []

    def walk(tabs):
        for tab in tabs:
            if tab.get("tabProperties", {}).get("tabId") == tab_id:
                body = tab.get("documentTab", {}).get("body", {}).get(
                    "content", [])
                for item in _paragraphs_in_body(body):
                    for el in item.get("paragraph", {}).get("elements", []):
                        run = el.get("textRun")
                        if run and run.get("content"):
                            runs.append((el.get("startIndex", 0),
                                         run["content"]))
            walk(tab.get("childTabs", []))
    walk(doc.get("tabs", []))
    return runs


def _locate_in_tab(docs_service, master_id: str, tab_id: str,
                   needle: str, occurrence: int = 0) -> tuple[int, int] | None:
    """(start, end) doc indices (UTF-16 units) of the
    (`occurrence`+1)-th verbatim occurrence of `needle` in the tab, or
    None when the tab holds fewer than that many. Exactness is law: no
    normalization, no fuzz (design: an approval authorizes one exact
    transformation).

    `occurrence` defaults to 0 — the FIRST match, which is exactly what
    a hand-quoted span means and what `propose_change` still passes.

    A producer writing SEVERAL forms into one tab must compute it.
    Two staged edits whose old halves are byte-identical (two identical
    paragraphs in one essay — a refrain, a liturgical repetition) both
    searched for the same needle and both resolved to the same span; the
    descending write order then landed the second write INSIDE the
    wrapper the first had planted, and `threads.PENDING`, being
    non-greedy, matched `<<<<old>>{{new}}` at resolve and left the rest
    of the wrapper in the author's manuscript as literal text. Text
    corruption, not a cosmetic fault (design-filter-doc-settle §9.3)."""
    runs = _tab_runs(docs_service, master_id, tab_id)
    full = "".join(content for _, content in runs)
    offset = -1
    for _ in range(occurrence + 1):
        offset = full.find(needle, offset + 1)
        if offset < 0:
            return None

    def doc_index_at(py_offset: int) -> int:
        """Doc index of the character at `py_offset` in `full`.

        Runs are not always contiguous in Doc index space: a table's
        structural markers sit between the last cell run and the next
        paragraph. Mapping a boundary with `<=` pinned the start of
        post-table prose to the end of the preceding cell (and, before
        `_tab_runs` walked cells, into the table itself)."""
        seen = 0
        for start, content in runs:
            if py_offset < seen + len(content):
                return start + _utf16_len(content[: py_offset - seen])
            seen += len(content)
        raise ValueError("offset beyond tab text")

    def doc_index_end(py_offset: int) -> int:
        """Exclusive end index for a span ending at `py_offset` in `full`."""
        if py_offset <= 0:
            return runs[0][0] if runs else 1
        if py_offset > len(full):
            raise ValueError("offset beyond tab text")
        if py_offset == len(full):
            start, content = runs[-1]
            return start + _utf16_len(content)
        # One past the last character of the span: that character's Doc
        # index plus its UTF-16 width, so a gap after a cell is not
        # swallowed into the range.
        ch = full[py_offset - 1]
        return doc_index_at(py_offset - 1) + _utf16_len(ch)

    return doc_index_at(offset), doc_index_end(offset + len(needle))


def propose_change(db: Database, manuscript: dict, comment_id: str,
                   old: str, new: str, note: str,
                   service=None, docs_service=None,
                   bridge: DocBridge | None = None) -> dict:
    """Register a proposal on an author comment: edit the tab into the
    pending-change form <<old>>{{new}} (anchor survives — boundary
    insertions only), style it like track-changes, post the prefixed
    reply, and record the thread. The prose itself was drafted in chat;
    this is the state machine's write."""
    from .threads import (PREFIX, create_thread, get_thread,
                          render_pending)

    bridge = bridge or manuscript_bridge(manuscript)
    mid = manuscript["id"]
    comment = db.one(
        "SELECT * FROM doc_comments WHERE manuscript_id = ? AND comment_id = ?",
        (mid, comment_id),
    )
    if comment is None:
        raise LookupError(f"no ingested comment '{comment_id}' — pull first")
    if get_thread(db, mid, comment_id):
        raise ValueError("this comment already has a thread")
    # Delimiter refusal is in `_mark_replace_requests` (form construction):
    # {{…}} closes at the first `}}`, so nested braces would truncate.
    relpath = (comment["file"] or "").removeprefix(bridge.display_prefix)
    if not relpath:
        raise LookupError("the comment's file could not be attributed — "
                          "propose needs a located anchor")
    meta = _mapping(db, manuscript)
    links = meta.get(bridge.meta_key, {})
    master_id = links.get("_master_id")
    entry = links.get(relpath) or {}
    tab_id = entry.get("tab_id")
    if not (master_id and tab_id):
        raise LookupError(f"'{relpath}' has no tab in the master Doc")

    span = _locate_in_tab(docs_service, master_id, tab_id, old)
    if span is None:
        raise LookupError(
            "the proposal's old text was not found verbatim in the tab — "
            "the passage may have changed; re-draft against current text")
    start, end = span
    requests = _mark_replace_requests(tab_id, start, end, old, new)
    docs_service.documents().batchUpdate(
        documentId=master_id, body={"requests": requests}).execute()

    reply_id = None
    try:
        reply = service.replies().create(
            fileId=master_id, commentId=comment_id,
            body={"content": PREFIX + "proposed — " + note},
            fields="id",
        ).execute()
        reply_id = reply.get("id")
    except Exception:
        pass  # the marker edit is the proposal; the reply is a courtesy

    thread = create_thread(
        db, mid, comment_id, relpath, comment["quoted"], old, new, note,
        reply_id, scope_kind="file", scope_ref=relpath,
    )
    return {"thread_id": thread["id"], "file": relpath,
            "pending": render_pending(old, new), "reply_id": reply_id}


GREEN = {"color": {"rgbColor": {"red": 0.13, "green": 0.55, "blue": 0.13}}}
# The changed-word highlights (author request 2026-08-31: a one-word edit
# inside a paragraph-sized form was invisible). COLOR ONLY, never bold or
# italics: the doc settle reads the whole-Doc MARKDOWN export, which
# preserves bold as **…** — a bolded highlight would leak literal
# asterisks into the resolved prose. foregroundColor is proven invisible
# to that export (the green half already round-trips clean).
RED_GONE = {"color": {"rgbColor": {"red": 0.8, "green": 0.1, "blue": 0.1}}}
BLUE_NEW = {"color": {"rgbColor": {"red": 0.07, "green": 0.33, "blue": 0.8}}}


def _word_diff_spans(old: str, new: str) -> tuple[list[tuple[int, int]],
                                                  list[tuple[int, int]]]:
    """Word-level diff of old vs new: two lists of (start16, end16)
    utf-16 offset spans — the words of `old` that do not survive, and
    the words of `new` that were not there. Deterministic (difflib on
    whitespace tokens). When BOTH sides are mostly changed (a rewrite),
    the highlight is noise rather than signal and both lists come back
    empty — but a one-sided change stays highlighted: a cut that
    removes most of the old half is exactly where the red must pop."""
    import difflib
    import re as _re

    old_toks = list(_re.finditer(r"\S+", old))
    new_toks = list(_re.finditer(r"\S+", new))
    if not old_toks or not new_toks:
        return [], []
    sm = difflib.SequenceMatcher(None, [t.group() for t in old_toks],
                                 [t.group() for t in new_toks])
    old_spans, new_spans = [], []
    old_changed = new_changed = 0
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            continue
        if i2 > i1:
            old_changed += i2 - i1
            old_spans.append((_utf16_len(old[:old_toks[i1].start()]),
                              _utf16_len(old[:old_toks[i2 - 1].end()])))
        if j2 > j1:
            new_changed += j2 - j1
            new_spans.append((_utf16_len(new[:new_toks[j1].start()]),
                              _utf16_len(new[:new_toks[j2 - 1].end()])))
    if (old_changed * 2 > len(old_toks)) and (new_changed * 2 > len(new_toks)):
        return [], []
    return old_spans, new_spans


def _mark_replace_requests(tab_id: str, start: int, end: int, old: str,
                           new: str) -> list[dict]:
    """Requests turning the span [start,end) (holding `old`) into the
    styled pending form <<old>>{{new}}: old struck through, new green —
    and the DIFF made visible: the words of old that go are red, the
    words of new that arrive are blue. Highlights ride after the base
    styles so they win on their subranges; color only (see RED_GONE).

    `old` is the text AS THE TAB HOLDS IT (rendered — see
    `rendered_text`), never the markdown record: the strikethrough range
    is measured from it, and the old span's own italics stay in the Doc
    untouched so the markdown export gives the record back. `new` is the
    markdown record, and it is RENDERED on the way in (`render_emphasis`):
    the tab shows italics where the record has `*…*`, never the
    asterisks — the author rules on the green half and should see prose
    there (author, 2026-09-06: "The italic showed up incorrectly (with
    stars)") — and the markdown export gives the asterisks back. The
    inserted markers and the new half have italic and bold reset first:
    an insertion inherits the style of the character before it, and a
    paragraph that ends in italics would otherwise export its form as
    `*>>{{…}}*`, which the pending grammar cannot read."""
    threads_mod.assert_no_pending_markers(old, new)
    old16 = _utf16_len(old)
    new, new_styles = render_emphasis(new)
    new16 = _utf16_len(new)
    requests = [
        {"insertText": {"location": {"tabId": tab_id, "index": end},
                        "text": ">>" + "{{" + new + "}}"}},
        {"insertText": {"location": {"tabId": tab_id, "index": start},
                        "text": "<<"}},
        {"updateTextStyle": {
            "range": {"tabId": tab_id, "startIndex": start,
                      "endIndex": start + 4 + old16},
            "textStyle": {"strikethrough": True}, "fields": "strikethrough"}},
        {"updateTextStyle": {
            "range": {"tabId": tab_id, "startIndex": start + 4 + old16,
                      "endIndex": start + 8 + old16 + new16},
            "textStyle": {"foregroundColor": GREEN},
            "fields": "foregroundColor"}},
    ]
    old_spans, new_spans = _word_diff_spans(old, new)
    old_base = start + 2                      # first char of old text
    new_base = start + 4 + old16 + 2          # first char of new text
    for s16, e16 in old_spans:
        requests.append({"updateTextStyle": {
            "range": {"tabId": tab_id, "startIndex": old_base + s16,
                      "endIndex": old_base + e16},
            "textStyle": {"foregroundColor": RED_GONE},
            "fields": "foregroundColor"}})
    for s16, e16 in new_spans:
        requests.append({"updateTextStyle": {
            "range": {"tabId": tab_id, "startIndex": new_base + s16,
                      "endIndex": new_base + e16},
            "textStyle": {"foregroundColor": BLUE_NEW},
            "fields": "foregroundColor"}})
    # Last, after the highlights the request-shape tests pin by position:
    # the two inserted runs take the style of the character before them,
    # so reset emphasis on the markers and the new half — a form written
    # after an italic must never export as `*>>{{…}}*`.
    for s, e in ((start, start + 2),
                 (start + 2 + old16, start + 8 + old16 + new16)):
        requests.append({"updateTextStyle": {
            "range": {"tabId": tab_id, "startIndex": s, "endIndex": e},
            "textStyle": {"italic": False, "bold": False},
            "fields": "italic,bold"}})
    # …and then the new half's own emphasis, as runs, on top of the reset.
    for s16, e16, style in new_styles:
        requests.append({"updateTextStyle": {
            "range": {"tabId": tab_id, "startIndex": new_base + s16,
                      "endIndex": new_base + e16},
            "textStyle": style, "fields": ",".join(style)}})
    return requests


def _unmark_replace_requests(tab_id: str, start: int, tab_old: str,
                             new: str) -> list[dict]:
    """The exact inverse of `_mark_replace_requests` for one form whose
    `<<` sits at doc index `start`: the tail `>>{{new}}` and the head
    `<<` are deleted (tail first, so the head's index still holds) and
    the old span's strikethrough and colour are taken back. Italic and
    bold on the old span were never touched, so they need no restoring."""
    old16 = _utf16_len(tab_old)
    new16 = _utf16_len(rendered_text(new))
    return [
        {"deleteContentRange": {"range": {
            "tabId": tab_id, "startIndex": start + 2 + old16,
            "endIndex": start + 8 + old16 + new16}}},
        {"deleteContentRange": {"range": {
            "tabId": tab_id, "startIndex": start, "endIndex": start + 2}}},
        {"updateTextStyle": {
            "range": {"tabId": tab_id, "startIndex": start,
                      "endIndex": start + old16},
            "textStyle": {"strikethrough": False,
                          "foregroundColor": {"color": {"rgbColor": {}}}},
            "fields": "strikethrough,foregroundColor"}},
    ]


def _mark_insert_requests(tab_id: str, at: int, new: str) -> list[dict]:
    """Requests inserting a green {{new}} paragraph at doc index `at`
    (a paragraph boundary): the critique pass's insertion form. The new
    half is rendered like a replace form's (`render_emphasis`).

    The new paragraph is set to normal text. Docs gives a paragraph made
    by a line break the style of the one it was split from, so an
    insertion after a heading came out as a heading (found 2026-10-02 on
    the first tab an outside caller built from its `# title` down)."""
    threads_mod.assert_no_pending_markers("", new)
    new, new_styles = render_emphasis(new)
    text = "\n" + "{{" + new + "}}"
    requests = [
        {"insertText": {"location": {"tabId": tab_id, "index": at},
                        "text": text}},
        # Before the text styles, never after: applying a named style
        # resets the paragraph's text to that style's own colour, which
        # took the green off every insertion (seen 2026-10-02).
        {"updateParagraphStyle": {
            "range": {"tabId": tab_id, "startIndex": at + 1,
                      "endIndex": at + _utf16_len(text)},
            "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
            "fields": "namedStyleType"}},
        {"updateTextStyle": {
            "range": {"tabId": tab_id, "startIndex": at + 1,
                      "endIndex": at + _utf16_len(text)},
            "textStyle": {"foregroundColor": GREEN},
            "fields": "foregroundColor"}},
        {"updateTextStyle": {
            "range": {"tabId": tab_id, "startIndex": at + 1,
                      "endIndex": at + _utf16_len(text)},
            "textStyle": {"italic": False, "bold": False},
            "fields": "italic,bold"}},
    ]
    base = at + 3                              # first char after "\n{{"
    for s16, e16, style in new_styles:
        requests.append({"updateTextStyle": {
            "range": {"tabId": tab_id, "startIndex": base + s16,
                      "endIndex": base + e16},
            "textStyle": style, "fields": ",".join(style)}})
    return requests


def pending_write_order(threads: list[dict]) -> list[dict]:
    """Surgical-write order for a producer's accepted threads: higher
    anchors first (so earlier indices stay valid); at the same
    anchor, inserts BEFORE replaces.

    Replacing paragraph n wraps it as <<old>>{{new}}. An insert after
    n then locates the pristine paragraph text via substring search —
    which matches inside the wrapped form and plants {{insert}} between
    `old` and `>>`, corrupting the pending grammar. Inserts must land
    first while the anchor text is still verbatim.

    The descending order is also half of the twins invariant: see
    `_occurrence`, which is correct only because of it."""
    def anchor_of(t):
        return (loads(t.get("metadata"), {}) or {}).get("anchor_paragraph", 0)

    return sorted(
        threads,
        key=lambda t: (-anchor_of(t), 0 if t["proposed_old"] == "" else 1),
    )


def _occurrence(paragraphs: list[str], n: int, needle: str) -> int:
    """The occurrence index of unit `n`'s own text, counted in the SAME
    universe `_locate_in_tab` searches.

    That universe is SUBSTRING matches in the tab's joined text — not
    paragraphs equal to the needle. The distinction is the whole of this
    function's correctness. Counting paragraph EQUALITY agrees with the
    locator only while no paragraph strictly CONTAINS another
    paragraph's whole text; a refrain that also opens a longer paragraph
    breaks that, and this manuscript is full of refrains. Where the two
    counts diverge, the writer plants each form in the WRONG paragraph
    and the read-back proof cannot see it — the form IS present, just
    not where it belongs — so the resolve resolves every thread `cleaned`
    and the manuscript is silently corrupted. A silent wrong write is
    strictly worse than the loud nesting failure this parameter exists
    to fix, so the counting universe is matched exactly.

    So: rebuild the text the tab holds, find where unit `n` starts in
    it, and count the matches that BEGIN before that — which is exactly
    how many the locator will step over on its way there.

    Computed from the LOCAL paragraph list, which is byte-identical to
    the tab because the levelling `push_doc` has just put it there, and
    correct only in company with the DESCENDING write order: when unit n
    is written, every paragraph before it is still pristine, so a match
    before it is still where it was. Paragraphs AFTER n may already be
    wrapped — their old text still occurs inside the wrapper — but they
    sit past n and cannot shift a lower occurrence index. Descending
    order and occurrence-from-original are jointly correct or not at all
    (design-filter-doc-settle §9.3)."""
    if n <= 0 or not needle:
        return 0
    # Counted in the RENDERED universe — the tab's text has no emphasis
    # markers (see `rendered_text`), and the writer searches it for the
    # rendered needle when the raw one is not there. A plain paragraph
    # renders to itself, so the count is unchanged where no markup is.
    paragraphs = [rendered_text(p) for p in paragraphs]
    needle = rendered_text(needle)
    full = "".join(p + "\n" for p in paragraphs)
    start = sum(len(p) + 1 for p in paragraphs[: n - 1])
    count, at = 0, full.find(needle)
    while 0 <= at < start:
        count += 1
        at = full.find(needle, at + 1)
    return count


def write_pending_forms(db: Database, manuscript: dict, file: str,
                        threads: list[dict], service, docs_service,
                        bridge: DocBridge | None = None) -> dict:
    """Render every thread it is GIVEN into `file`'s Doc tab as a
    pending form (critique design §6.3 step 3). The tab is first brought
    level with the local file (a plain push, since the local file is
    pristine), then each span is marked surgically, last-to-first so
    earlier indices stay valid, and located by OCCURRENCE INDEX so two
    identical old halves land in their own paragraphs (§9.3). Local
    keeps OLD. Returns {written, failed:[(thread, reason)]}.

    Two producers now: `critique write` and `filter push`. Neither is
    named in the signature — the threads arrive as an argument and the
    caller owns their state transitions — which is why this took a
    rename and not an `origin_type` parameter."""
    from .revisions import _paragraphs

    bridge = bridge or manuscript_bridge(manuscript)
    push_doc(db, manuscript, file, service=service,
             docs_service=docs_service, bridge=bridge)
    meta = _mapping(db, manuscript)
    links = meta.get(bridge.meta_key, {})
    master_id = links.get("_master_id")
    tab_id = (links.get(file) or {}).get("tab_id")
    if not (master_id and tab_id):
        raise LookupError(f"'{file}' has no tab in the master Doc")
    text = (bridge.root / file).read_text(encoding="utf-8")
    paragraphs = _paragraphs(text)
    # EVERY thread handed in is written. The caller chooses the set and
    # owns the state transitions — which is what the two producers now
    # need to differ about: `critique write` sends its accepted threads,
    # and `filter push` sends the untriaged proposals too, because on
    # the Doc road the tab IS the review surface. A state filter in here
    # would silently drop half of one producer's push.
    ordered = pending_write_order(threads)
    written, failed = [], []
    tab_old_of: dict[str, str] = {}     # thread id → the old text AS THE TAB HOLDS IT
    for t in ordered:
        n = (loads(t.get("metadata"), {}) or {}).get("anchor_paragraph", 0)
        try:
            if t["proposed_old"]:
                old = t["proposed_old"]
                occurrence = _occurrence(paragraphs, n, old)
                # Exactness first — a tab holding the raw bytes (nothing
                # rendered) is the record's own text. Then the RENDERED
                # form: the importer turned the paragraph's emphasis into
                # formatting, and the text runs carry no asterisks. The
                # fallback is explicit here rather than hidden in the
                # locator, because `_locate_in_tab`'s law is exactness
                # and every other caller means it literally.
                tab_old = old
                span = _locate_in_tab(docs_service, master_id, tab_id, old,
                                      occurrence)
                if span is None and rendered_text(old) != old:
                    tab_old = rendered_text(old)
                    span = _locate_in_tab(docs_service, master_id, tab_id,
                                          tab_old, occurrence)
                if span is None:
                    raise LookupError("old text not found verbatim in the tab")
                tab_old_of[t["id"]] = tab_old
                requests = _mark_replace_requests(
                    tab_id, span[0], span[1], tab_old, t["proposed_new"])
            else:
                if n == 0:
                    at = 1  # tab body start
                else:
                    if n > len(paragraphs):
                        raise LookupError(f"anchor paragraph {n} out of range")
                    anchor = paragraphs[n - 1]
                    span = _locate_in_tab(
                        docs_service, master_id, tab_id, anchor,
                        _occurrence(paragraphs, n, anchor))
                    if span is None and tab_anchor_text(anchor) != anchor:
                        # A stanza (hard breaks), an italic paragraph, or
                        # a heading: the tab holds it rendered — same
                        # fallback the replace path takes, plus the
                        # heading marker the importer turned into style.
                        span = _locate_in_tab(
                            docs_service, master_id, tab_id,
                            tab_anchor_text(anchor),
                            _occurrence(paragraphs, n, anchor))
                    if span is None:
                        raise LookupError("anchor paragraph not found "
                                          "verbatim in the tab")
                    at = span[1]
                requests = _mark_insert_requests(tab_id, at,
                                                 t["proposed_new"])
            docs_service.documents().batchUpdate(
                documentId=master_id, body={"requests": requests}).execute()
            written.append(t)
        except Exception as err:  # noqa: BLE001 — per-thread, keep going
            failed.append((t, str(err)))
    # Read-back proof: every written form must be present verbatim — in
    # the tab's TEXT, which holds the old half as the tab renders it.
    full = "".join(c for _, c in _tab_runs(docs_service, master_id, tab_id))
    for t in list(written):
        tab_old = tab_old_of.get(t["id"], t["proposed_old"])
        tab_new = rendered_text(t["proposed_new"])
        form = (threads_mod.render_pending(tab_old, tab_new)
                if tab_old else threads_mod.render_insertion(tab_new))
        if form not in full:
            written.remove(t)
            failed.append((t, "read-back: form not found after write"))
    # The export proof, for forms either half of which carried markup:
    # the resolve reads the tab back through the MARKDOWN export and
    # matches each form's old half to the record byte for byte, and takes
    # the new half from there as final. A form the writer placed by its
    # rendered text, or whose new half it rendered, is only good if that
    # export gives the markdown back — italics inside a struck-through
    # span are the exporter's business, not ours to assume. One export
    # per push, only when it is needed; a form the export cannot
    # reproduce is taken back out, exactly, and reported — never left in
    # the tab for a resolve to mis-read.
    rendered = [t for t in written
                if tab_old_of.get(t["id"], t["proposed_old"]) != t["proposed_old"]
                or rendered_text(t["proposed_new"]) != t["proposed_new"]]
    if rendered:
        try:
            fetched = tab_marked_markdown(db, manuscript, file, service,
                                          docs_service, bridge)
            forms = threads_mod.pending_forms(fetched.get("marked") or "")
        except Exception as err:  # noqa: BLE001 — proof unavailable is failure
            forms, err_text = None, str(err)
        for t in rendered:
            ok = forms is not None and any(
                f["old"] == t["proposed_old"] and f["new"] == t["proposed_new"]
                and (f["kind"] == "replace") == bool(t["proposed_old"])
                for f in forms)
            if ok:
                continue
            tab_old = tab_old_of.get(t["id"], t["proposed_old"])
            if not tab_old:
                # An insertion the export cannot give back: leave it and
                # report — there is no exact inverse for a form with no
                # old half, and the resolve collapses an unmatched
                # insertion to nothing rather than to wrong text.
                written.remove(t)
                failed.append((t, "export proof: the markdown export does "
                                  "not give this insertion back verbatim "
                                  "(inline markup) — left in the tab"))
                continue
            form = threads_mod.render_pending(tab_old,
                                              rendered_text(t["proposed_new"]))
            span = _locate_in_tab(docs_service, master_id, tab_id, form)
            if span is not None:
                docs_service.documents().batchUpdate(
                    documentId=master_id,
                    body={"requests": _unmark_replace_requests(
                        tab_id, span[0], tab_old, t["proposed_new"])}
                ).execute()
            written.remove(t)
            failed.append((t, "export proof: the markdown export does not "
                              "give this paragraph's old half back verbatim "
                              "(inline markup) — form taken back out"
                           if forms is not None else
                           f"export proof unavailable ({err_text}) — form "
                           "taken back out"))
    # Inserted paragraphs carry no spacing of their own, and Google's
    # markdown export joins neighbouring paragraphs that have none into
    # ONE paragraph with hard line breaks, which no resolve can read back
    # as separate forms (found 2026-10-02: a tab built from its title down
    # by additions only). Re-apply the standard, as every content write does.
    if any(not t["proposed_old"] for t in written):
        try:
            apply_tab_spacing(docs_service, master_id, tab_id,
                              doc_spacing(manuscript))
        except Exception:  # noqa: BLE001 — spacing is re-applied by resolve
            pass
    return {"written": written, "failed": failed,
            "url": tab_url(master_id, tab_id)}


def settle_forms_in_tab(db: Database, manuscript: dict, file: str,
                        outcomes: list[dict], docs_service,
                        bridge: DocBridge | None = None) -> list[str]:
    """Take SOME forms out of a tab, each to the half its resolve chose,
    and leave every other form in the tab exactly as it stands.

    A resolve normally ends with a rebuild push, which clears every
    marker at once. That push is held while another producer's forms
    are still out in the tab (`forms_pending`), and a rebuild would wipe
    them if it were not — so a resolve that settles only ITS OWN forms
    (`api.resolve_revisions`, docs/outside-caller-design.md §5) cannot
    use it. Leaving its settled forms in the tab is not an option
    either: the other producer's resolve reads the tab back and
    collapses every form it does not own to the OLD half, which would
    undo, in the local file, text this resolve has already landed.

    `outcomes` are {old, new, keep}: `old` the record's old half (''
    for an insertion), `new` the new half as the markdown export gave it
    (the author's wording), `keep` 'new' or 'old'. They are handled in
    the order given — document order, so that of two identical forms
    the first outcome meets the first form. Only markers and the losing
    half are deleted; the kept half stays where it is with its own
    emphasis, and loses the strikethrough and the colour. A form the
    author already took the markers off is simply not found, which is
    not a fault. Returns the faults, as sentences; empty when the tab
    holds none of these forms any more — and then the local file is
    recorded as the tab's agreed base, as a push would."""
    import hashlib

    bridge = bridge or manuscript_bridge(manuscript)
    meta = _mapping(db, manuscript)
    links = meta.get(bridge.meta_key, {})
    master_id = links.get("_master_id")
    tab_id = (links.get(file) or {}).get("tab_id")
    if not (master_id and tab_id):
        raise LookupError(f"'{file}' has no tab in the master Doc")

    def tab_text() -> str:
        return "".join(c for _, c in _tab_runs(docs_service, master_id,
                                               tab_id))

    def find_form(full: str, outcome: dict) -> tuple[str, str, str] | None:
        """(head, kept-or-dropped old, tail) AS THE TAB HOLDS THEM: the
        form is head + old + tail-with-new, located by its old half for
        a replacement and by its new half for an insertion."""
        if outcome["old"]:
            for tab_old in (outcome["old"], rendered_text(outcome["old"])):
                at = full.find(f"<<{tab_old}>>")
                if at < 0:
                    continue
                after = at + len(tab_old) + 4
                if not full.startswith("{{", after):
                    return "<<", tab_old, ">>"      # green half deleted
                close = full.find("}}", after + 2)
                if close < 0:
                    return None
                return "<<", tab_old, full[after - 2: close + 2]
            return None
        for tab_new in (outcome["new"], rendered_text(outcome["new"])):
            if f"{{{{{tab_new}}}}}" in full:
                return "{{", "", tab_new + "}}"
        return None

    faults: list[str] = []
    for outcome in outcomes:
        found = find_form(tab_text(), outcome)
        if found is None:
            continue
        head, tab_old, tail = found
        form = head + tab_old + tail
        span = _locate_in_tab(docs_service, master_id, tab_id, form)
        if span is None:
            faults.append(f"a form on «{clamp(outcome['old'] or outcome['new'])}» "
                          "could not be located in the tab")
            continue
        start, end = span

        def cut(a: int, b: int) -> dict:
            return {"deleteContentRange": {"range": {
                "tabId": tab_id, "startIndex": a, "endIndex": b}}}

        old16 = _utf16_len(tab_old)
        if outcome["old"] and outcome["keep"] == "new" and tail != ">>":
            # <<old>>{{new}} → new: the closing braces, then everything
            # up to and including the opening ones.
            requests = [cut(end - 2, end), cut(start, start + old16 + 6)]
            kept = (start, end - old16 - 8)
        elif outcome["old"]:
            # → old: the tail (>>, and the green half when it is still
            # there), then the opening marker.
            requests = [cut(start + 2 + old16, end), cut(start, start + 2)]
            kept = (start, start + old16)
        elif outcome["keep"] == "new":
            requests = [cut(end - 2, end), cut(start, start + 2)]
            kept = (start, end - 4)
        else:
            # A declined insertion leaves with the paragraph break the
            # writer put in front of it.
            requests = [cut(start - 1 if start > 1 else start, end)]
            kept = None
        if kept and kept[1] > kept[0]:
            requests.append({"updateTextStyle": {
                "range": {"tabId": tab_id, "startIndex": kept[0],
                          "endIndex": kept[1]},
                "textStyle": {}, "fields": "strikethrough,foregroundColor"}})
        docs_service.documents().batchUpdate(
            documentId=master_id, body={"requests": requests}).execute()
    # Read-back: none of these forms may still be in the tab.
    full = tab_text()
    for outcome in outcomes:
        if find_form(full, outcome) is not None:
            faults.append(f"a form on «{clamp(outcome['old'] or outcome['new'])}» "
                          "is still in the tab")
    if not faults:
        path = bridge.root / file
        local = strip_embed_lines(normalize_markdown(
            path.read_text(encoding="utf-8")))
        entry = links[file]
        entry["pushed_hash"] = hashlib.sha256(local.encode()).hexdigest()[:16]
        entry["checked_out"] = True
        _save_mapping(db, manuscript, meta)
    return faults


def _tab_paragraph_texts(docs_service, master_id: str,
                         tab_id: str) -> list[str]:
    """Non-empty paragraph strings from a tab (table cells included),
    Docs trailing newlines stripped. Blank separator paragraphs
    (transplant skips them on push) are omitted — callers that need
    markdown structure must rejoin with `\\n\\n`."""
    doc = docs_service.documents().get(
        documentId=master_id, includeTabsContent=True).execute()
    out: list[str] = []

    def walk(tabs):
        for tab in tabs:
            if tab.get("tabProperties", {}).get("tabId") == tab_id:
                body = tab.get("documentTab", {}).get("body", {}).get(
                    "content", [])
                for item in _paragraphs_in_body(body):
                    paragraph = item.get("paragraph") or {}
                    text = "".join(
                        el.get("textRun", {}).get("content", "")
                        for el in paragraph.get("elements", [])
                        if "textRun" in el)
                    text = text.rstrip("\n")
                    if text.strip():
                        out.append(text)
            walk(tab.get("childTabs", []))
    walk(doc.get("tabs", []))
    return out


def critique_tab_text(db: Database, manuscript: dict, file: str,
                      docs_service, bridge: DocBridge | None = None) -> str:
    """The tab's current text as markdown paragraphs (the resolve verb
    reads the author's post-edits from the pending forms here).

    Docs API runs end each paragraph with a single `\\n` and push skips
    blank separator paragraphs (to avoid double Doc spacing). Joining
    runs raw would collapse every essay into one `_paragraphs` blob on
    resolve — rejoin non-empty paragraphs with `\\n\\n` so structure
    survives the round trip (markdown export does the same on pull)."""
    bridge = bridge or manuscript_bridge(manuscript)
    meta = _mapping(db, manuscript)
    links = meta.get(bridge.meta_key, {})
    master_id = links.get("_master_id")
    tab_id = (links.get(file) or {}).get("tab_id")
    if not (master_id and tab_id):
        raise LookupError(f"'{file}' has no tab in the master Doc")
    paras = _tab_paragraph_texts(docs_service, master_id, tab_id)
    return "\n\n".join(paras) + ("\n" if paras else "")


def _replace_pending(db: Database, manuscript: dict, thread: dict,
                     replacement: str, service, docs_service,
                     bridge: DocBridge) -> bool:
    """Replace a thread's exact pending span with `replacement` in the
    Doc tab, clear the track-changes styling, and mirror the outcome
    into the local file. Exactness is law: returns False (stale) when
    the span is not found verbatim."""
    from .threads import render_pending

    meta = _mapping(db, manuscript)
    links = meta.get(bridge.meta_key, {})
    master_id = links.get("_master_id")
    tab_id = (links.get(thread["file"]) or {}).get("tab_id")
    if not (master_id and tab_id):
        return False
    # Structure-exact, content-flexible: the OLD half must match the
    # record verbatim (it is what gets deleted), but the author may have
    # edited the {{new}} half in place — a modified acceptance, honored
    # exactly like the beat loop's (their text wins; the diff is
    # evidence).
    prefix = f"<<{thread['proposed_old']}>>"
    runs = _tab_runs(docs_service, master_id, tab_id)
    full = "".join(content for _, content in runs)
    at = full.find(prefix)
    if at < 0 or not full[at + len(prefix):].startswith("{{"):
        return False
    close = full.find("}}", at + len(prefix) + 2)
    if close < 0:
        return False
    actual_new = full[at + len(prefix) + 2: close]
    # The tab holds the new half RENDERED (its emphasis as formatting), so
    # the record's markdown is compared through the same map; only a
    # difference beyond that is the author's own rewording.
    if replacement == thread["proposed_new"] \
            and actual_new != rendered_text(replacement):
        import json as _json

        from .db import loads as _loads

        meta = _loads(thread.get("metadata"), {}) or {}
        meta["original_new"] = thread["proposed_new"]
        db.update("doc_threads", thread["id"],
                  {"proposed_new": actual_new,
                   "metadata": _json.dumps(meta)})
        replacement = actual_new
    pending = full[at: close + 2]
    span = _locate_in_tab(docs_service, master_id, tab_id, pending)
    if span is None:
        return False
    start, end = span
    requests = [
        {"deleteContentRange": {"range": {
            "tabId": tab_id, "startIndex": start, "endIndex": end}}},
        {"insertText": {"location": {"tabId": tab_id, "index": start},
                        "text": replacement}},
        {"updateTextStyle": {
            "range": {"tabId": tab_id, "startIndex": start,
                      "endIndex": start + _utf16_len(replacement)},
            "textStyle": {}, "fields": "strikethrough,foregroundColor"}},
    ]
    docs_service.documents().batchUpdate(
        documentId=master_id, body={"requests": requests}).execute()

    # Read-back verification (ratified: equivalence or loud failure).
    runs = _tab_runs(docs_service, master_id, tab_id)
    if pending in "".join(content for _, content in runs):
        return False

    # Mirror into the local file: canonical text was the OLD half all
    # along, so approval replaces old→final there; a revert needs no
    # local change (local never held the proposal).
    path = bridge.root / thread["file"]
    if path.exists() and replacement != thread["proposed_old"]:
        text = path.read_text(encoding="utf-8")
        if thread["proposed_old"] in text:
            path.write_text(
                text.replace(thread["proposed_old"], replacement, 1),
                encoding="utf-8")
    return True


def advance_threads(db: Database, manuscript: dict, open_comments: list,
                    service, docs_service,
                    bridge: DocBridge) -> list[dict]:
    """The verdict machine (docs/margin-threads-design.md): act on the
    latest unprocessed author reply of each open thread. Approve →
    cleanup (pending span becomes the new text); decline → revert (span
    becomes the old text); anything else is conversation and is left
    for chat. Every action replies a receipt and records state."""
    from . import threads as th

    actions: list[dict] = []
    if docs_service is None:
        return actions
    mid = manuscript["id"]
    by_id = {c["id"]: c for c in open_comments}
    for thread in th.open_threads(db, mid):
        comment = by_id.get(thread["origin_id"])
        if comment is None:
            continue
        verdict = None
        verdict_reply_id = None
        for reply in comment.get("replies", []):
            content = reply.get("content", "")
            if not content or th.is_ours(content):
                continue
            if (thread["last_author_reply_id"]
                    and reply.get("id") == thread["last_author_reply_id"]):
                verdict = None  # everything up to the watermark is old
                continue
            verdict = th.classify_reply(content)
            verdict_reply_id = reply.get("id")
        if verdict not in ("approve", "decline") \
                or thread["state"] != "proposed":
            continue
        replacement = (thread["proposed_new"] if verdict == "approve"
                       else thread["proposed_old"])
        ok = _replace_pending(db, manuscript, thread, replacement,
                              service, docs_service, bridge)
        if not ok:
            receipt = ("stale — the passage changed since this proposal; "
                       "re-proposing in chat")
            th.set_state(db, thread, "stale",
                         author_reply_id=verdict_reply_id)
            actions.append({"comment_id": thread["origin_id"],
                            "file": thread["file"], "action": "stale"})
        else:
            # The verdict WAS the author's decision: terminal verdicts
            # close their thread (the receipt is the resolving reply).
            # In-context review lives in the pending phase, where the
            # anchor survives; an orphaned open thread maps nothing.
            state = "cleaned" if verdict == "approve" else "declined"
            receipt = ("applied and closed — reopen or comment anew if "
                       "it reads wrong" if verdict == "approve"
                       else "reverted and closed")
            th.set_state(db, thread, state,
                         author_reply_id=verdict_reply_id)
            fresh = dict(th.get_thread(db, mid, thread["origin_id"]))
            meta_now = loads(fresh.get("metadata"), {}) or {}
            signal = ("declined" if verdict == "decline" else
                      "modified" if meta_now.get("original_new")
                      else "accepted")
            th.record_margin_verdict(db, mid, fresh, signal)
            action = {"comment_id": thread["origin_id"],
                      "file": thread["file"], "action": state}
            if signal == "modified":
                action["diff"] = (f"«{clamp(meta_now['original_new'])}» → "
                                  f"«{clamp(fresh['proposed_new'] or '')}»")
            actions.append(action)
        try:
            from .threads import PREFIX

            body = {"content": PREFIX + receipt}
            if actions[-1]["action"] in ("cleaned", "declined"):
                body["action"] = "resolve"
            reply = service.replies().create(
                fileId=_mapping(db, manuscript)[bridge.meta_key]["_master_id"],
                commentId=thread["origin_id"],
                body=body, fields="id",
            ).execute()
            th.set_state(db, dict(th.get_thread(db, mid,
                                                thread["origin_id"])),
                         actions[-1]["action"], reply_id=reply.get("id"))
            if "action" in body:
                row = db.one(
                    "SELECT id FROM doc_comments WHERE manuscript_id = ? "
                    "AND comment_id = ?", (mid, thread["origin_id"]))
                if row:
                    db.update("doc_comments", row["id"],
                              {"state": "resolved"})
        except Exception:
            pass  # the text edit is the act; the receipt is a courtesy

    # Withdraw sweep: a PROPOSED thread whose comment vanished from the
    # open set means the author resolved it without approving — the
    # proposal is withdrawn: revert the pending span, record a bare
    # rejection-shaped closure (no re-asking in the margin).
    open_ids = {c["id"] for c in open_comments}
    for thread in th.open_threads(db, mid):
        if thread["state"] != "proposed" or thread["origin_id"] in open_ids:
            continue
        _replace_pending(db, manuscript, thread, thread["proposed_old"],
                         service, docs_service, bridge)
        th.set_state(db, thread, "withdrawn")
        th.record_margin_verdict(db, mid, thread, "withdrawn")
        actions.append({"comment_id": thread["origin_id"],
                        "file": thread["file"], "action": "withdrawn"})
    return actions


# ---------------------------------------------------- surgical diff push
# (margin-threads step 3: partial in means, total in result)

def _shift_requests(requests: list[dict], delta: int) -> list[dict]:
    """Rebase transplant requests (built against index 1) to an
    arbitrary insertion point."""
    def shift(obj):
        if isinstance(obj, dict):
            return {k: (v + delta if k in ("index", "startIndex", "endIndex")
                        and isinstance(v, int) else shift(v))
                    for k, v in obj.items()}
        if isinstance(obj, list):
            return [shift(x) for x in obj]
        return obj
    return shift(requests)


# The standard tab format, ratified 2026-08-10: the author picked the
# discernment tab's spacing as manuscript law. Body text only — headings
# keep the Doc's own heading spacing.
DOC_SPACING = {"line_spacing": 115, "space_above": 0, "space_below": 6}


def doc_spacing(manuscript: dict) -> dict:
    """[gdocs.spacing] in _exports/settings.toml overrides DOC_SPACING."""
    from .export import load_settings

    spacing = dict(DOC_SPACING)
    spacing.update((load_settings(manuscript).get("gdocs") or {})
                   .get("spacing") or {})
    return spacing


def apply_tab_spacing(docs_service, master_id: str, tab_id: str,
                      spacing: dict) -> int:
    """Impose the standard paragraph spacing on a tab's NORMAL_TEXT
    paragraphs. Imported content arrives with Google's defaults (no
    space after paragraph), so every content write re-applies the
    standard. Returns the number of style requests sent."""
    doc = docs_service.documents().get(
        documentId=master_id, includeTabsContent=True).execute()
    ranges: list[list[int]] = []

    def walk(tabs):
        for tab in tabs:
            if tab.get("tabProperties", {}).get("tabId") == tab_id:
                for item in tab.get("documentTab", {}).get("body", {}).get(
                        "content", []):
                    para = item.get("paragraph")
                    if not para:
                        continue
                    named = para.get("paragraphStyle", {}).get(
                        "namedStyleType", "NORMAL_TEXT")
                    if named != "NORMAL_TEXT":
                        continue
                    start = item.get("startIndex", 0)
                    end = item.get("endIndex", 0)
                    if not end or end <= start:
                        continue
                    if ranges and ranges[-1][1] == start:
                        ranges[-1][1] = end
                    else:
                        ranges.append([start, end])
            walk(tab.get("childTabs", []))

    walk(doc.get("tabs", []))
    requests = [{"updateParagraphStyle": {
        "range": {"tabId": tab_id, "startIndex": s, "endIndex": e},
        "paragraphStyle": {
            "lineSpacing": spacing["line_spacing"],
            "spaceAbove": {"magnitude": spacing["space_above"],
                           "unit": "PT"},
            "spaceBelow": {"magnitude": spacing["space_below"],
                           "unit": "PT"},
        },
        "fields": "lineSpacing,spaceAbove,spaceBelow"}}
        for s, e in ranges]
    if requests:
        docs_service.documents().batchUpdate(
            documentId=master_id, body={"requests": requests}).execute()
    return len(requests)


def _doc_paragraphs(docs_service, master_id: str,
                    tab_id: str) -> list[dict]:
    """Non-empty paragraphs of a tab with their doc index ranges:
    [{start, end, text}] in order. Rule paragraphs surface as '---' so
    they align positionally with the markdown export."""
    doc = docs_service.documents().get(
        documentId=master_id, includeTabsContent=True).execute()
    out: list[dict] = []

    def walk(tabs):
        for tab in tabs:
            if tab.get("tabProperties", {}).get("tabId") == tab_id:
                for item in tab.get("documentTab", {}).get("body", {}).get(
                        "content", []):
                    para = item.get("paragraph")
                    if not para:
                        continue
                    if any("horizontalRule" in e
                           for e in para.get("elements", [])):
                        out.append({"start": item.get("startIndex", 0),
                                    "end": item.get("endIndex", 0),
                                    "text": "---"})
                        continue
                    text = "".join(
                        e["textRun"].get("content", "")
                        for e in para.get("elements", [])
                        if "textRun" in e)
                    if text.strip():
                        out.append({"start": item.get("startIndex", 0),
                                    "end": item.get("endIndex", 0),
                                    "text": text})
            walk(tab.get("childTabs", []))
    walk(doc.get("tabs", []))
    return out


def _md_paragraphs(markdown: str) -> list[str]:
    return [p for p in re.split(r"\n\s*\n", markdown) if p.strip()]


def diff_push(db: Database, manuscript: dict, relpath: str,
              service, docs_service,
              bridge: DocBridge | None = None) -> dict:
    """Push local changes into a tab by editing only the changed
    paragraphs — anchors (margin threads, ingested comments) on
    untouched text survive by construction. Diffs in EXPORT space (both
    sides normalized markdown), maps paragraphs positionally onto the
    tab's structure, imports replacement paragraphs through the
    temp-doc pipeline so Google owns md→rich conversion, and PROVES the
    result by read-back equivalence (one retry, then a loud conflict —
    never a silent rebuild). Bails to a conflict whenever an edit
    overlaps a pending thread span."""
    import hashlib as _hashlib

    from difflib import SequenceMatcher

    from googleapiclient.http import MediaInMemoryUpload

    from .revisions import strip_embed_lines as _strip_embeds

    bridge = bridge or manuscript_bridge(manuscript)
    mid = manuscript["id"]
    meta = _mapping(db, manuscript)
    links = meta.get(bridge.meta_key, {})
    master_id = links.get("_master_id")
    entry = links.get(relpath) or {}
    tab_id = entry.get("tab_id")
    if not (master_id and tab_id):
        raise LookupError(f"'{relpath}' has no tab in the master Doc")
    # AFTER the no-tab refusal, deliberately: this reads the file, and a
    # mapped-but-missing file would otherwise surface a raw
    # FileNotFoundError in place of the precise message above.
    _refuse_mid_rewrite(
        relpath, (bridge.root / relpath).read_text(encoding="utf-8"))

    local_md = _strip_embeds(normalize_markdown(
        (bridge.root / relpath).read_text(encoding="utf-8")))

    # Same boundary set and positional order pull_doc/reconcile use — every
    # live tab title, not just mapped essay files, is a split boundary; a
    # tab that follows this one in the export but isn't recognized would
    # have its heading and body swallowed into this section instead
    # (it-x7-2, diff_push parity). docs_service is required here (already
    # used unconditionally below for paragraph reads and batchUpdate), so
    # this is not best-effort the way pull_doc/reconcile's is.
    mapped = [f for f, e in links.items()
              if not f.startswith("_") and isinstance(e, dict)
              and e.get("tab_id")]
    pmapped = prompt_links(links) if bridge.meta_key == "gdocs" else {}
    tab_props: list[tuple[str, str]] = []
    tab_doc = docs_service.documents().get(
        documentId=master_id, includeTabsContent=True).execute()
    walk_tabs(tab_doc.get("tabs", []), tab_props, [])
    known = set(mapped) | {MANIFEST_TITLE} | {ILLUS_TAB_TITLE} | set(pmapped)
    titles = {t for _, t in tab_props if t}
    boundaries = known | titles
    tab_order = [t for _, t in tab_props if t]

    def tab_markdown() -> str:
        data = service.files().export(
            fileId=master_id, mimeType=MARKDOWN_MIME).execute()
        whole = unescape_export_math(
        data.decode("utf-8") if isinstance(data, bytes) else str(data))
        return normalize_markdown(
            split_tabbed_export(whole, boundaries, order=tab_order)
            .get(relpath, ""))

    for attempt in (1, 2):
        tab_md = tab_markdown()
        tab_paras = _md_paragraphs(tab_md)
        canonical = [threads_mod.strip_pending(p)[0].strip("\n")
                     for p in tab_paras]
        local_paras = [p.strip("\n") for p in _md_paragraphs(local_md)]
        if canonical == local_paras:
            # Same canonical bytes as push_doc / three_way / reconcile
            # (normalize_markdown's trailing newline). Hashing the
            # join(paras) form made only-local edits look like both
            # sides moved → false CONFLICT → --force data loss.
            entry["pushed_hash"] = _hashlib.sha256(
                local_md.encode()).hexdigest()[:16]
            entry["checked_out"] = True
            links[relpath] = entry
            _save_mapping(db, manuscript, meta)
            return {"relpath": relpath, "mode": "diff", "ops": 0}

        doc_paras = _doc_paragraphs(docs_service, master_id, tab_id)
        if len(doc_paras) != len(tab_paras):
            raise LookupError(
                f"diff push: tab structure and export disagree "
                f"({len(doc_paras)} vs {len(tab_paras)} paragraphs) — "
                "settle threads and use a rebuild push")

        requests: list[dict] = []
        opcodes = SequenceMatcher(None, canonical, local_paras,
                                  autojunk=False).get_opcodes()
        ops = 0
        for tag, i1, i2, j1, j2 in reversed(opcodes):
            if tag == "equal":
                continue
            ops += 1
            # Replaces use <<old>>{{new}}; critique insertions are
            # {{new}} alone — both must block overlapping surgical edits.
            if any(("<<" in tab_paras[i] or "{{" in tab_paras[i])
                   for i in range(i1, i2)):
                raise LookupError(
                    "diff push: the edit overlaps a pending margin "
                    "thread — settle the thread first "
                    f"[{tag} {i1}:{i2} tab={tab_paras[i1:i2]!r} "
                    f"local={local_paras[j1:j2]!r}]")
            if tag in ("replace", "delete") and i2 > i1:
                start = doc_paras[i1]["start"]
                end = doc_paras[i2 - 1]["end"]
                if i2 == len(doc_paras):
                    end -= 1  # the tab's final newline is not deletable
                requests.append({"deleteContentRange": {"range": {
                    "tabId": tab_id, "startIndex": start,
                    "endIndex": end}}})
            if tag in ("replace", "insert") and j2 > j1:
                chunk = "\n\n".join(local_paras[j1:j2]) + "\n"
                chunk = escape_math(escape_footnotes(chunk))
                media = MediaInMemoryUpload(chunk.encode("utf-8"),
                                            mimetype=MARKDOWN_MIME)
                temp = service.files().create(
                    body={"name": f"authorlm-diff-{Path(relpath).stem}",
                          "mimeType": GDOC_MIME},
                    media_body=media, fields="id",
                ).execute()
                try:
                    temp_doc = docs_service.documents().get(
                        documentId=temp["id"]).execute()
                finally:
                    service.files().delete(fileId=temp["id"]).execute()
                if i1 < len(doc_paras):
                    # Insert BEFORE an existing paragraph: imported
                    # paragraphs each end with a newline, so the block
                    # closes cleanly against what follows.
                    position = doc_paras[i1]["start"]
                    requests.append(_shift_requests(
                        transplant_requests(temp_doc, tab_id),
                        position - 1))
                else:
                    # Append at tab end: open a fresh paragraph first —
                    # inserting at end-1 lands INSIDE the last
                    # paragraph, before its trailing newline.
                    position = doc_paras[-1]["end"] - 1
                    requests.append([{"insertText": {
                        "location": {"tabId": tab_id, "index": position},
                        "text": "\n"}}])
                    requests.append(_shift_requests(
                        transplant_requests(temp_doc, tab_id), position))

        flat: list[dict] = []
        for r in requests:
            flat.extend(r if isinstance(r, list) else [r])
        if flat:
            docs_service.documents().batchUpdate(
                documentId=master_id, body={"requests": flat}).execute()

        verify = [threads_mod.strip_pending(p)[0].strip("\n")
                  for p in _md_paragraphs(tab_markdown())]
        if verify == local_paras:
            if flat:
                apply_tab_spacing(docs_service, master_id, tab_id,
                                  doc_spacing(manuscript))
            entry["pushed_hash"] = _hashlib.sha256(
                local_md.encode()).hexdigest()[:16]
            entry["checked_out"] = True
            links[relpath] = entry
            _save_mapping(db, manuscript, meta)
            return {"relpath": relpath, "mode": "diff", "ops": ops}
        if attempt == 2:
            raise LookupError(
                "diff push: read-back verification failed twice — the "
                "tab and local disagree; resolve by pull or an explicit "
                "rebuild push")
    raise LookupError("diff push: unreachable")


def decide_thread(db: Database, manuscript: dict, comment_id: str,
                  verdict: str, reason: str | None = None,
                  service=None, docs_service=None, llm=None,
                  bridge: DocBridge | None = None) -> dict:
    """Chat stays sovereign: decide a proposed thread from outside the
    margin. Same text operations, same receipts, same evidence — and the
    one path where an EXPLAINED verdict is natural, so the reason (the
    author's words verbatim) feeds the scoped distiller."""
    from . import threads as th
    from .styles import guide_chain as _guide_chain

    if verdict not in ("approve", "decline"):
        raise ValueError("verdict must be approve or decline")
    bridge = bridge or manuscript_bridge(manuscript)
    mid = manuscript["id"]
    thread = th.get_thread(db, mid, comment_id)
    if thread is None:
        raise LookupError(f"no thread for comment '{comment_id}'")
    thread = dict(thread)
    if thread["state"] != "proposed":
        raise ValueError(f"thread is {thread['state']}, not proposed")
    replacement = (thread["proposed_new"] if verdict == "approve"
                   else thread["proposed_old"])
    if not _replace_pending(db, manuscript, thread, replacement,
                            service, docs_service, bridge):
        th.set_state(db, thread, "stale")
        raise LookupError("the passage changed since this proposal — "
                          "it is now stale; re-propose against current text")
    state = "cleaned" if verdict == "approve" else "declined"
    th.set_state(db, thread, state)
    fresh = dict(th.get_thread(db, mid, comment_id))
    signal = ("declined" if verdict == "decline" else
              "modified" if (loads(fresh.get("metadata"), {}) or {})
              .get("original_new") else "accepted")
    th.record_margin_verdict(
        db, mid, fresh, signal, explanation=reason, llm=llm,
        guide_chain=_guide_chain(db, mid, thread["file"]))
    receipt = ("applied and closed (decided in chat)"
               if verdict == "approve"
               else "reverted and closed (decided in chat)")
    if reason:
        receipt += f" — {reason}"
    try:
        service.replies().create(
            fileId=_mapping(db, manuscript)[bridge.meta_key]["_master_id"],
            commentId=comment_id,
            body={"content": th.PREFIX + receipt, "action": "resolve"},
            fields="id",
        ).execute()
        row = db.one(
            "SELECT id FROM doc_comments WHERE manuscript_id = ? "
            "AND comment_id = ?", (mid, comment_id))
        if row:
            db.update("doc_comments", row["id"], {"state": "resolved"})
    except Exception:
        pass
    return {"comment_id": comment_id, "state": state, "signal": signal}
