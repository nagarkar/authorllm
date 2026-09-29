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

TITLE = "# **Pronunciations**"  # bold, like every chapter title: the Doc export bolds headings, and a plain one churned on every pull

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


def _unreadable_row(cells: list[str]) -> str | None:
    """Why this line cannot be read as ONE three-column row, or None.

    A pipe is the table's own column separator and it does not survive
    the Doc bridge in any form: `gdocs.normalize_markdown`'s `_ESCAPE`
    strips exactly the backslash an escape would write, and `push_doc`
    normalizes and WRITES BACK, so an escaped pipe is unescaped on the
    author's disk and the row reparses with its cells shifted one to the
    left — `| a\\|b | ay-bee | note |` became term 'a', say 'b', note
    'ay-bee'. The row COUNT is unchanged by that, so §2.6's zero-rows
    guard never sees it: it is a resolved row silently rewritten, which is
    the one thing this file exists to make impossible.

    So a row carrying a pipe is SKIPPED and named, never guessed at. Two
    signatures, both meaning the same thing:

    - a non-empty cell beyond the third — the shift a raw interior pipe
      leaves. Trailing EMPTY cells stay tolerated, because a trailing
      empty cell is exactly what Docs emits and means nothing.
    - a pipe surviving inside a cell — what a hand-written `\\|` leaves
      before the next push unescapes it.

    Skipping composes with the zero-rows guard rather than fighting it: a
    table whose rows all carry pipes parses to NOTHING, so a pull of it
    is refused by name and `--force` cannot reach past it."""
    extra = [c for c in cells[3:] if c]
    if extra:
        return "its cells are shifted"
    if any("|" in c for c in cells[:3]):
        return "escaped, which the Doc bridge unescapes"
    return None


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
        shifted = _unreadable_row(cells)
        if shifted:
            warnings.append(
                f"the row beginning '{term}' carries a '|' ({shifted}), so "
                "it cannot be read as one three-column row. Skipped, "
                "never guessed at — a pipe is the table's own column "
                "separator and no pronunciation needs one. Remove it and "
                "the row reads again.")
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


def render_row(row: dict) -> str:
    """One table line. A newline OR a pipe in any field is refused.

    A row is one line, and a multi-line cell does not survive a Docs
    table round trip in a shape the parser can trust. A pipe does not
    survive one either, and the earlier escape (`\\|`) was worse than
    nothing: `normalize_markdown` strips exactly that backslash, and
    `push_doc` normalizes and writes back, so the escape was defeated on
    the author's own disk and the row came back with its cells shifted.
    Refusing here rather than escaping means nothing this system writes
    can ever carry one — which is what makes
    `normalize_markdown(render(rows)) == render(rows)` true, and what
    keeps `proposals.adopt` the only writer of this file."""
    for field in ("term", "say", "note"):
        value = row.get(field) or ""
        if "\n" in value or "\r" in value:
            raise ValueError(
                f"a pronunciation row's `{field}` carries a newline; a "
                "row is one line.")
        if "|" in value:
            raise ValueError(
                f"a pronunciation row's `{field}` carries a '|', which is "
                "the table's own column separator and does not survive "
                "the Doc bridge in any form — an escaped pipe is "
                "unescaped by the next push and the row's cells shift. "
                "No pronunciation needs one.")
    return (f"| {row.get('term') or ''} "
            f"| {row.get('say') or ''} "
            f"| {row.get('note') or ''} |")


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
