"""`pronunciations.md` — the manuscript's pronunciation dictionary
(docs/autoregressive-writing-design.md §15.22).

A manuscript-root SIDECAR: markdown, a table, Doc-mirrored as an ordinary
tab so the author edits it there exactly like an essay, and excluded from
reading order, concept scanning, summaries, critique, filter targeting,
the manifest and every export by a filename flag (`structure.is_sidecar`).

Pure functions, no database, no imports from `api`. Everything here is
text in and text out, which is what lets the file be the author's: the
parser never writes, the writer never rewrites a line it did not add, and
the only caller that writes at all is `proposals.adopt` — the author's own
verdict.

The identity of a term is `key()`: NFC-normalize, casefold, collapse
internal whitespace. NFC matters — the same Sanskrit term arrives
precomposed from one editor and decomposed from another, and without
normalization the dictionary would carry two rows the eye cannot tell
apart.
"""

from __future__ import annotations

import re
import unicodedata

FILENAME = "pronunciations.md"

TITLE = "# Pronunciations"

PREAMBLE = (
    "How the terms in this book are said aloud. AuthorLM proposes a row "
    "when a filter meets a term it cannot be sure of; you rule on it. A "
    "row here is settled: no pass ever rewrites one, and the "
    "audio-friendly filter stops flagging its term. Edit freely — here "
    "or in the Doc."
)

HEADER = "| Term | Say it | Note |"
DELIMITER = "| --- | --- | --- |"

SEED = f"{TITLE}\n\n{PREAMBLE}\n\n{HEADER}\n{DELIMITER}\n"

# The Docs-export backslash escapes the parser must undo. The same class
# `gdocs._ESCAPE` handles, restricted to what can appear inside a cell.
_CELL_ESCAPE = re.compile(r"\\([|*_\\])")

# A cell split on unescaped pipes.
_SPLIT = re.compile(r"(?<!\\)\|")


def key(term: str) -> str:
    """The identity of a term: NFC, casefolded, internal whitespace
    collapsed. `Śūnyatā` (NFC), `Śūnyatā` (NFD) and `śūnyatā` are one."""
    return " ".join(
        unicodedata.normalize("NFC", term or "").split()).casefold()


def _unwrap_emphasis(cell: str) -> str:
    """Docs bolds header cells and sometimes first columns. A symmetric
    `**…**` or `*…*` wrapper is styling, never part of the term."""
    for mark in ("**", "*"):
        if (len(cell) > 2 * len(mark) and cell.startswith(mark)
                and cell.endswith(mark)):
            return cell[len(mark):-len(mark)].strip()
    return cell


def _cells(line: str) -> list[str]:
    """The normalized cells of one pipe-table line, or [] when the line
    is not a table row."""
    # NBSP → space before anything else. `gdocs.normalize_markdown` does
    # this on the Doc path, but the parser also reads the file straight
    # off disk (Obsidian, a hand paste), and an interior NBSP that
    # survived there would make the same row two different rows.
    body = (line or "").replace(" ", " ").strip()
    if not body.startswith("|"):
        return []
    body = body[1:]
    if body.endswith("|") and not body.endswith("\\|"):
        body = body[:-1]
    out = []
    for raw in _SPLIT.split(body):
        cell = _CELL_ESCAPE.sub(r"\1", raw.strip()).strip()
        out.append(_unwrap_emphasis(cell))
    return out


def _is_delimiter(cells: list[str]) -> bool:
    """`---`, `:---`, `:---:`, `----------` — every shape Docs emits."""
    if not cells:
        return False
    return all(c and set(c) <= set("-: \t") and "-" in c for c in cells)


def _is_header(cells: list[str]) -> bool:
    return bool(cells) and key(cells[0]) == "term"


def parse(text: str) -> tuple[list[dict], list[str]]:
    """`(rows, warnings)` from the file's text.

    Everything before and after the table is prose and is IGNORED, never
    rewritten — the author's own notes in the file survive. Nothing here
    writes: a file that parses badly is reported, never repaired."""
    rows: list[dict] = []
    warnings: list[str] = []
    seen: dict[str, str] = {}
    in_table = False
    for line in (text or "").replace("\r\n", "\n").replace("\r", "\n") \
            .split("\n"):
        cells = _cells(line)
        if not cells:
            if in_table:
                break          # the table ended; the rest is prose
            continue
        if not in_table:
            if _is_header(cells):
                in_table = True
            continue
        if _is_delimiter(cells):
            continue
        term = cells[0]
        if not term:
            # Docs leaves blank trailing rows in tables constantly.
            continue
        say = cells[1] if len(cells) > 1 else ""
        note = cells[2] if len(cells) > 2 else ""
        k = key(term)
        if k in seen:
            warnings.append(
                f"'{term}' appears more than once (first as "
                f"'{seen[k]}') — the first row wins; nothing was deleted.")
            continue
        seen[k] = term
        if not say:
            warnings.append(
                f"'{term}' has no pronunciation yet — the row is kept, "
                "and no pronunciation will be proposed for it.")
        rows.append({"term": term, "say": say, "note": note})
    return rows, warnings


def lookup(rows: list[dict]) -> dict[str, dict]:
    return {key(r["term"]): r for r in rows}


def has_term(text: str, term: str) -> bool:
    rows, _ = parse(text)
    return key(term) in lookup(rows)


def _escape(value: str) -> str:
    return (value or "").replace("|", "\\|")


def render_row(row: dict) -> str:
    """One table line. A newline in any field is refused — a row is one
    line, and a multi-line cell does not survive a Docs table round trip
    in a shape the parser can trust."""
    for field in ("term", "say", "note"):
        value = row.get(field) or ""
        if "\n" in value or "\r" in value:
            raise ValueError(
                f"a pronunciation row's `{field}` carries a newline; a "
                "row is one line.")
    return (f"| {_escape(row.get('term') or '')} "
            f"| {_escape(row.get('say') or '')} "
            f"| {_escape(row.get('note') or '')} |")


def render(rows: list[dict]) -> str:
    """The whole file for a list of rows — the seed plus each row. Used
    by tests and by nothing that writes the author's file."""
    body = "\n".join(render_row(r) for r in rows)
    return SEED + (body + "\n" if body else "")


def add_row(text: str, row: dict) -> str:
    """Append one row after the table's last row, rewriting NO line it
    did not add. Idempotent: a term already present returns `text`
    unchanged.

    `structure.insert_toc_entry`'s discipline, applied to a second table,
    including its hard-won line-ending rule:

        Split and re-join on the file's OWN dominant line ending.
        `splitlines` + `"\\n".join` silently relaid a CRLF toc in LF,
        rewriting every line the insertion never touched.

    A Doc round trip is a recurring source of CRLF, so the same test is
    stronger here than it was there."""
    line = render_row(row)                    # refuses newlines first
    if not (text or "").strip():
        return SEED + line + "\n"
    existing, _warnings = parse(text)
    if key(row.get("term") or "") in lookup(existing):
        return text
    crlf = text.count("\r\n")
    newline = "\r\n" if crlf > text.count("\n") - crlf else "\n"
    lines = text.split(newline)

    last_row = None
    in_table = False
    for i, raw in enumerate(lines):
        cells = _cells(raw)
        if not cells:
            if in_table:
                break
            continue
        if not in_table:
            if _is_header(cells):
                in_table = True
                last_row = i
            continue
        last_row = i
    if last_row is None:
        # No table at all: the author's prose stays, the table is
        # appended after it.
        body = newline.join(lines).rstrip(newline)
        tail = newline.join(["", HEADER, DELIMITER, line, ""])
        return body + tail
    new_lines = lines[:last_row + 1] + [line] + lines[last_row + 1:]
    return newline.join(new_lines)


def block_lines(text: str) -> list[str]:
    """The PRONUNCIATION DICTIONARY body for the filter payload, sorted
    by `key(term)`. Note in parentheses, omitted when empty."""
    rows, _warnings = parse(text or "")
    out = []
    for row in sorted(rows, key=lambda r: key(r["term"])):
        say = row["say"] or "(not settled yet)"
        note = f" ({row['note']})" if row["note"] else ""
        out.append(f"- {row['term']} — {say}{note}")
    return out


def terms(text: str) -> list[str]:
    """Every term the dictionary carries, as written. These join the
    protected set (§1.1 list 3): a term the author has ruled on aloud is
    by construction a term of art."""
    rows, _warnings = parse(text or "")
    return [r["term"] for r in rows]
