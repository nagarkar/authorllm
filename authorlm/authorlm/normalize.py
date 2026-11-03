"""Canonical markdown: the normalizer that makes a no-op round trip through
the Doc byte-stable, whichever editor (local, Obsidian, a Docs export) last
wrote the text, with its fence and math lifters and the whitespace helpers
that go with it.

It canonicalizes export quirks (backslash escapes, CRLF, NBSP, bullet markers,
blank-line runs), and fenced code and TeX stay opaque throughout, because their
bytes are intentional and `push_doc` writes the normalized bytes back to disk.

Invariant: text in, text out, and normalizing twice changes nothing. Nothing
here reads a file or the database or imports from this package, so any layer
may use it. The Doc bridge's import-side escapes and API calls are `gdocs`.
"""

from __future__ import annotations

import re

# The colon: the exporter writes a URL typed or inserted as plain text as
# `https\://…` so it will not auto-link (seen on footnote lines an outside
# caller staged).
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
# Google Docs markdown export renders pasted images as reference-style
# links (`![][image1]`) plus, sometimes, a base64 definition line — both
# useless outside the Doc and silently corrupting once pulled.
_DANGLING_REF = re.compile(r"!\[\]\[image\d+\]")
_DANGLING_DEF = re.compile(r"^\[image\d+\]:\s.*$\n?", re.MULTILINE)


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
DISPLAY_MATH = re.compile(r"\$\$(.+?)\$\$", re.DOTALL)
INLINE_MATH = re.compile(
    r"(?<![\\$])\$(?![\s$])((?:\\.|[^$\\\n])+?)(?<![\s\\])\$(?![\d$])")
_MATH_SENTINEL = "\x00M%d\x00"
_SENTINEL_RE = re.compile(r"\x00M(\d+)\x00")
_ANY_MATH = re.compile(
    f"(?s:{DISPLAY_MATH.pattern})|{INLINE_MATH.pattern}")


# A pending form, as the tab and its export hold it: `<<old>>{{new}}`,
# the old half struck (`~~`) and its markers escaped (`\<\<`) by the
# exporter. A form whose halves each carry a `$` — a revision that moves
# a span's opening delimiter, `<<by element:$\neg>>{{by element: $\neg}}`
# — leaves the marked text with three delimiters where each reading has
# two, and pairing them on the marked text pairs the wrong ones: the new
# half kept its `\$` on pull, the resolve landed it, and the next push
# and normalize took the span for prose and stripped its `\{` `\}`
# (SMSTTD recapitulation.md R10). So math is found on each
# READING — every form collapsed to its old half, then to its new — and
# mapped back to the marked text.
_FORM = re.compile(
    r"(?:~~)*\\?<\\?<(?P<old>.*?)\\?>\\?>(?:~~)*\{\{(?P<new>.*?)\}\}",
    re.DOTALL)


def math_positions(text: str, pattern: re.Pattern) -> list[list[int]]:
    """Each math span `pattern` finds, as the positions in `text` it
    covers. Without forms that is the match itself; with forms, every
    span of either reading, each mapped back through its reading."""
    forms = list(_FORM.finditer(text))
    if not forms:
        return [list(range(*m.span())) for m in pattern.finditer(text)]
    spans: list[list[int]] = []
    for keep in ("old", "new"):
        idx: list[int] = []
        pos = 0
        for f in forms:
            idx += range(pos, f.start())
            idx += range(f.start(keep), f.end(keep))
            pos = f.end()
        idx += range(pos, len(text))
        reading = "".join(text[i] for i in idx)
        spans += [idx[m.start():m.end()] for m in pattern.finditer(reading)
                  if m.end() > m.start()]
    return spans


def _lift_math(text: str) -> tuple[str, list[str]]:
    spans: list[str] = []

    def display(m: re.Match) -> str:
        spans.append("$$ " + " ".join(m.group(1).split()) + " $$")
        return _MATH_SENTINEL % (len(spans) - 1)

    def inline(m: re.Match) -> str:
        spans.append(m.group(0))
        return _MATH_SENTINEL % (len(spans) - 1)

    if _FORM.search(text):
        text = _lift_marked_math(text, spans)
    else:
        text = DISPLAY_MATH.sub(display, text)
    # An unclosed $$ leaves TeX in the prose stream. _ESCAPE would then
    # gut row breaks (\\ → \) and \{ \| \= — and push_doc writes the
    # normalized bytes back to disk, so the damage is permanent. Keep
    # the remnant opaque (byte-identical aside from the file's trailing
    # newline, which normalize re-adds) until a closer exists.
    if "$$" in text:
        at = text.index("$$")
        head, tail = text[:at], text[at:]
        head = INLINE_MATH.sub(inline, head)
        spans.append(tail.rstrip("\n"))
        return head + (_MATH_SENTINEL % (len(spans) - 1)), spans
    return INLINE_MATH.sub(inline, text), spans


def _lift_marked_math(text: str, spans: list[str]) -> str:
    r"""_lift_math for text carrying pending forms: every stretch that is
    math in either reading goes opaque, so no prose rule reaches a `\{`
    inside it. A span a form cuts through is lifted piece by piece —
    the form markers between the pieces stay prose, for the rules that
    unescape them. Display layout is canonicalized only for a piece
    that is one whole display span."""
    runs: list[list[int]] = []
    for span in math_positions(text, _ANY_MATH):
        start = span[0]
        for prev, at in zip(span, span[1:] + [None]):
            if at != prev + 1:
                runs.append([start, prev + 1])
                start = at
    ranges: list[list[int]] = []
    for a, b in sorted(runs):
        if ranges and a < ranges[-1][1]:
            ranges[-1][1] = max(ranges[-1][1], b)
        else:
            ranges.append([a, b])
    out, pos = [], 0
    for a, b in ranges:
        chunk = text[a:b]
        m = DISPLAY_MATH.fullmatch(chunk)
        if m:
            chunk = "$$ " + " ".join(m.group(1).split()) + " $$"
        spans.append(chunk)
        out.append(text[pos:a] + _MATH_SENTINEL % (len(spans) - 1))
        pos = b
    return "".join(out) + text[pos:]


def _restore_math(text: str, spans: list[str]) -> str:
    return _SENTINEL_RE.sub(lambda m: spans[int(m.group(1))], text)


def strip_dangling(text: str) -> tuple[str, list[str]]:
    """Remove Doc-export image artifacts; returns (clean text, refs
    removed). Idempotent, and safe on text that has none."""
    refs = _DANGLING_REF.findall(text) + [
        m.split(":", 1)[0].strip() for m in _DANGLING_DEF.findall(text)]
    text = _DANGLING_DEF.sub("", text)
    text = _DANGLING_REF.sub("", text)
    return text, refs


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


def flatten_whitespace(text: str) -> str:
    return " ".join(text.split())


def clamp(text: str, limit: int = 60) -> str:
    flat = flatten_whitespace(text)
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"
