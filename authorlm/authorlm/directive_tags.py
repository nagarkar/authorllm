"""The inline directive tags as text: `[Footnote: …]`, `[Explain: …]`,
`[Suggestion: …]` and `[Judgment: …]`, how to find them in a file's text and
how to take them out.

Invariant: text in, text out. Nothing here reads a file or the database or
imports from this package, so any layer may use it. Landing a tag, its
evidence and its Doc road are `directives`.
"""

from __future__ import annotations

import re

KINDS = ("footnote", "explain", "suggestion", "judgment")
TAGS = {
    # Keyword case-insensitive; the body never spans a line and never
    # nests a bracket; an empty body is not a tag at all.
    "footnote": re.compile(r"\[Footnote:\s*(?P<body>[^\[\]\n]+?)\s*\]", re.IGNORECASE),
    "explain": re.compile(r"\[Explain:\s*(?P<body>[^\[\]\n]+?)\s*\]", re.IGNORECASE),
    "suggestion": re.compile(r"\[Suggestion:\s*(?P<body>[^\[\]\n]+?)\s*\]",
                             re.IGNORECASE),
    # A lens finding the author ACCEPTED in the tab: a work order at its
    # anchor, resolved by `lens repair` (a rewrite, a question, or an
    # intent), never by `apply`. Deleting it in the tab rejects it.
    "judgment": re.compile(r"\[Judgment:\s*(?P<body>[^\[\]\n]+?)\s*\]", re.IGNORECASE),
}
_LABEL_OPT = re.compile(r"\s*\|\s*label:\s*(?P<label>[A-Za-z]+)\s*$", re.IGNORECASE)
_PARAGRAPH = re.compile(r"[^\n]+(?:\n[^\n]+)*")


def check_kind(kind: str) -> str:
    if kind not in KINDS:
        raise ValueError(f"unknown directive kind '{kind}' (one of: {', '.join(KINDS)})")
    return kind


def is_standalone(text: str, start: int, end: int) -> bool:
    """True when the tag is the whole of its line (whitespace aside)."""
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    if line_end == -1:
        line_end = len(text)
    return not text[line_start:start].strip() and not text[end:line_end].strip()


def scan_text(text: str, kind: str) -> list[dict]:
    """Every open tag of one kind in one file's text, in document order:
    the ordinal `n` (1-based within the file — the `--tag` handle), the
    line, the character span, the gist, the raw tag, whether it stands
    alone on its line, the paragraph that holds it, the TARGET paragraph
    a suggestion acts on (`target`, `target_start`, `target_end`: the
    holding paragraph inline, the paragraph BEFORE a standalone tag,
    None when nothing precedes it), and — footnotes — the `| label:`
    override when the author gave one."""
    pattern = TAGS[check_kind(kind)]
    paragraphs = [(m.start(), m.end(), m.group(0))
                  for m in _PARAGRAPH.finditer(text)]
    tags = []
    for n, m in enumerate(pattern.finditer(text), 1):
        start, end = m.span()
        body = m.group("body")
        label = None
        if kind == "footnote":
            opt = _LABEL_OPT.search(body)
            if opt:
                label = opt.group("label").upper()
                body = body[:opt.start()].strip()
        here = next(((a, b, p) for a, b, p in paragraphs if a <= start < b),
                    (start, end, ""))
        standalone = is_standalone(text, start, end)
        if standalone:
            target = next(((a, b, p) for a, b, p in reversed(paragraphs)
                           if b <= start), None)
        else:
            target = here
        tags.append({
            "kind": kind, "n": n,
            "line": text.count("\n", 0, start) + 1,
            "start": start, "end": end,
            "gist": body, "raw": m.group(0), "label": label,
            "standalone": standalone,
            "paragraph": here[2],
            "target": target[2] if target else None,
            "target_start": target[0] if target else None,
            "target_end": target[1] if target else None,
        })
    return tags


def strip_tags(text: str) -> tuple[str, list[dict]]:
    """Exports: an unresolved tag never reaches a reader. A standalone
    tag takes its line with it; an inline tag leaves the sentence's
    spacing as it was. Returns (text, [{kind, gist}] removed)."""
    removed: list[dict] = []
    out = text
    tags = sorted((t for kind in KINDS for t in scan_text(text, kind)),
                  key=lambda t: t["start"], reverse=True)
    for tag in tags:
        removed.append({"kind": tag["kind"], "gist": tag["gist"]})
        out = remove_tag(out, tag)
    removed.reverse()
    return out, removed


def remove_tag(text: str, tag: dict) -> str:
    """The text with ONE tag gone: a standalone tag takes its line with
    it (and the blank line that separated it); an inline tag leaves the
    sentence's spacing as it was. Offsets are the tag's in `text`."""
    start, end = tag["start"], tag["end"]
    if tag["standalone"]:
        line_start = text.rfind("\n", 0, start) + 1
        line_end = text.find("\n", end)
        line_end = len(text) if line_end == -1 else line_end + 1
        out = text[:line_start] + text[line_end:]
        if line_start > 0 and out[line_start - 1:line_start + 1] == "\n\n":
            out = out[:line_start] + out[line_start + 1:]
        elif line_start >= len(out) and out.endswith("\n\n"):
            out = out[:-1]      # the file's last line went: one newline stays
        return out
    before = text[start - 1] if start > 0 else ""
    after = text[end] if end < len(text) else ""
    if before == " " and (after == " " or after in ".,;:!?)"):
        start -= 1
    return text[:start] + text[end:]
