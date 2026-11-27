"""Change kinds: how one ruling changes the essay's text, in place.

The author rewrites an essay step by step (owner, 2026-11-27: "I remove
and replace things section by section, paragraph, modify paragraph, or
paragraphs and remove content. I do that step by step."), and he must see
what each ruling does (owner: "forms are for what have you deleted, what
have you removed, what have you changed ... I can't eyeball what has been
changed easily"). So every proposal is a list of changes, each of one
kind, each its own form (owner): "you can build, cut, insert and move,
section by section insertion."

- **replace**: `original` (quoted exactly from the essay, long enough to
  occur once) becomes `text`. A split is a replace with several
  paragraphs; a merge is a replace and a cut.
- **insert**: `text` goes in as new paragraphs at an anchor: `where`
  before or after the paragraph `quote` names (its opening words, enough
  to occur once), or at the `start` or `end` of the essay. Nothing is
  removed.
- **cut**: `original` is taken out. Explicit, so an empty proposal still
  means "nothing to change here" (passed), never a cut.
- **move**: `original` is taken out where it stands and `text` (the
  original when empty) goes in at the anchor: a cut and an insert as one
  linked change.

A change is applied to a text by `apply`, which resolves it into text
pairs (`original` → `new`) at paragraph grain: the paragraphs a change
touches, before and after it. Landing writes each pair where its
`original` stands in the file and a reopen swaps it back
(`workflow.landing`), matched by text, never by position, as every other
ruling is.

Matching (`find`) is exact, else after normalizing whitespace, quote marks
and dashes; between equal matches, the first at or after a cursor.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

KINDS = ("replace", "insert", "cut", "move")
WHERES = ("before", "after", "start", "end")
_BREAK = re.compile(r"\n[ \t]*\n")


# ---------------------------------------------------------------- matching

_QUOTES = str.maketrans({"‘": "'", "’": "'", "‚": "'",
                         "‛": "'", "“": '"', "”": '"',
                         "„": '"', "‟": '"', "–": "-",
                         "—": "-", " ": " "})


def _normalized(text: str) -> tuple[str, list[tuple[int, int]]]:
    """The text with whitespace runs collapsed to one space, curly quotes
    straightened and dashes made hyphens, and for each of its characters
    the span of the original it came from."""
    out: list[str] = []
    spans: list[tuple[int, int]] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            j = i
            while j < n and text[j].isspace():
                j += 1
            if out and out[-1] != " ":
                out.append(" ")
                spans.append((i, j))
            i = j
            continue
        ch = unicodedata.normalize("NFC", ch).translate(_QUOTES)
        out.append(ch)
        spans.append((i, i + 1))
        i += 1
    while out and out[-1] == " ":
        out.pop()
        spans.pop()
    return "".join(out), spans


def norm(text: str) -> str:
    return _normalized(text or "")[0]


def _all(hay: str, needle: str) -> list[int]:
    out, i = [], hay.find(needle)
    while i >= 0:
        out.append(i)
        i = hay.find(needle, i + 1)
    return out


def find(hay: str, needle: str, cursor: int = 0) -> tuple[int, int, str] | None:
    """(start, end, how) of `needle` in `hay`: exact, else normalized.
    Between equal matches, the first at or after `cursor`."""
    if not (needle or "").strip():
        return None
    hits = _all(hay, needle)
    if hits:
        i = next((h for h in hits if h >= cursor), hits[0])
        return i, i + len(needle), "exact"
    nh, spans = _normalized(hay)
    nn = norm(needle)
    if not nn:
        return None
    hits = _all(nh, nn)
    if not hits:
        return None
    starts = [spans[h][0] for h in hits]
    k = next((j for j, s in enumerate(starts) if s >= cursor), 0)
    h = hits[k]
    return spans[h][0], spans[h + len(nn) - 1][1], "normalized"


# ---------------------------------------------------------------- the shape

def clean(raw: Any) -> dict:
    """One change in its one shape: {kind, original, where, quote, text}.
    Raises ValueError on a change that is not one."""
    if not isinstance(raw, dict):
        raise ValueError(f"a change is an object, not {type(raw).__name__}")
    kind = str(raw.get("kind") or "").strip().lower()
    if kind not in KINDS:
        raise ValueError(f"a change's kind is one of {', '.join(KINDS)}, "
                         f"not '{kind}'")
    out = {"kind": kind}
    for key in ("original", "quote", "text"):
        value = raw.get(key)
        if value is not None and not isinstance(value, str):
            raise ValueError(f"a change's {key} is text")
        out[key] = (value or "").strip("\n")
    where = str(raw.get("where") or "").strip().lower()
    if where and where not in WHERES:
        raise ValueError(f"a change's where is one of {', '.join(WHERES)}")
    out["where"] = where
    if kind == "insert" and not out["text"].strip():
        raise ValueError("an insert carries its text")
    if kind == "replace" and not out["text"].strip():
        raise ValueError("a replace carries its new text (a cut is its own kind)")
    return out


def clean_list(raw: Any) -> list[dict]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("changes is a list")
    return [clean(c) for c in raw]


def clean_placement(raw: Any) -> dict | None:
    """A beat plan's placement: {where, quote, supersedes: [opening words]}:
    where its new text goes in, and which old paragraphs it takes the
    place of. None when the plan gives none."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("placement is an object")
    where = str(raw.get("where") or "").strip().lower()
    if where and where not in WHERES:
        raise ValueError(f"placement's where is one of {', '.join(WHERES)}")
    sup = raw.get("supersedes") or []
    if not isinstance(sup, list) or not all(isinstance(s, str) for s in sup):
        raise ValueError("placement's supersedes is a list of text")
    return {"where": where, "quote": str(raw.get("quote") or "").strip(),
            "supersedes": [s.strip() for s in sup if s.strip()]}


def describe(c: dict) -> str:
    """One line saying what a change does, for the CLI and the record."""
    def q(t: str) -> str:
        t = " ".join((t or "").split())
        return f"“{t[:60]}…”" if len(t) > 60 else f"“{t}”"
    where = {"start": "at the start of the essay", "end": "at the end of the essay",
             "before": f"before {q(c.get('quote', ''))}",
             "after": f"after {q(c.get('quote', ''))}"}.get(c.get("where") or "",
                                                           "after this unit")
    if c["kind"] == "replace":
        return f"replace {q(c['original']) if c['original'] else 'this unit'}"
    if c["kind"] == "insert":
        return f"insert new text {where}"
    if c["kind"] == "cut":
        return f"cut {q(c['original']) if c['original'] else 'this unit'}"
    return (f"move {q(c['original']) if c['original'] else 'this unit'} {where}")


# ------------------------------------------------------------- paragraphs

def _block_at(text: str, i: int, j: int) -> tuple[int, int]:
    """The span (i, j) widened to the whole paragraphs it touches."""
    starts = [0] + [m.end() for m in _BREAK.finditer(text)]
    ends = [m.start() for m in _BREAK.finditer(text)] + [len(text.rstrip("\n"))]
    s = max((x for x in starts if x <= i), default=0)
    e = min((x for x in ends if x >= j), default=len(text))
    return s, max(e, j)


def blocks(text: str) -> list[tuple[int, int]]:
    """The (start, end) of each paragraph (a block between blank lines)."""
    out, pos = [], 0
    for m in _BREAK.finditer(text):
        if text[pos:m.start()].strip():
            out.append((pos, m.start()))
        pos = m.end()
    end = len(text.rstrip("\n"))
    if text[pos:end].strip():
        out.append((pos, end))
    return out


def _is_title(block: str) -> bool:
    return bool(re.match(r"^#\s", block))


def opening_match(paras: list[str], quote: str) -> int | None:
    """The index of the paragraph `quote` names by its opening words (else
    the first that holds it), or None."""
    nq = norm(quote)
    if not nq:
        return None
    for k, p in enumerate(paras):
        if norm(p).startswith(nq):
            return k
    for k, p in enumerate(paras):
        if nq in norm(p):
            return k
    return None


# ----------------------------------------------------------------- applying

class NotApplied(ValueError):
    """A change whose original or anchor is not in the text."""


def _anchor_point(text: str, where: str, quote: str,
                  cursor: int) -> tuple[int, str]:
    """(the position a new paragraph goes in at, which side it joins):
    "after" joins it after the text before the point, "before" before the
    text after it, "only" when the text is empty."""
    if not text.strip():
        return 0, "only"
    bl = blocks(text)
    if where in ("before", "after") and quote:
        hit = find(text, quote, cursor)
        if hit is None:
            raise NotApplied(f"the anchor “{quote[:60]}” is not in the essay")
        s, e = _block_at(text, hit[0], hit[1])
        return (s, "before") if where == "before" else (e, "after")
    if where == "end":
        return bl[-1][1], "after"
    # The start: after the essay's title line, before everything else.
    first = bl[0]
    if _is_title(text[first[0]:first[1]]):
        return first[1], "after"
    return first[0], "before"


def _pair(text: str, s: int, e: int, new_text: str) -> tuple[str, dict]:
    return text[:s] + new_text + text[e:], {"original": text[s:e], "new": new_text}


def _insert(text: str, new: str, where: str, quote: str,
            cursor: int) -> tuple[str, dict, int]:
    new = new.strip("\n")
    pos, side = _anchor_point(text, where, quote, cursor)
    if side == "only":
        return new + "\n", {"original": "", "new": new}, len(new)
    if side == "after":
        s, e = _block_at(text, pos, pos)
        region = text[s:e]
        out, pair = _pair(text, s, e, region + "\n\n" + new)
        return out, pair, s + len(pair["new"])
    s, e = _block_at(text, pos, pos)
    region = text[s:e]
    out, pair = _pair(text, s, e, new + "\n\n" + region)
    return out, pair, s + len(new)


def _cut(text: str, i: int, j: int) -> tuple[str, dict, int]:
    """Take text[i:j] out. A cut of whole paragraphs takes them out with
    a neighbour as the pair's context (so the pair is findable and its
    reverse puts them back in place); a cut inside a paragraph changes
    that paragraph only."""
    s, e = _block_at(text, i, j)
    if (s, e) != (i, j):
        para = text[s:e]
        rest = (para[:i - s].rstrip(" ") + (" " if para[:i - s].strip()
                                              and para[j - s:].strip() else "")
                + para[j - s:].lstrip(" "))
        if rest.strip():
            out, pair = _pair(text, s, e, rest)
            return out, pair, s + len(rest)
        i, j = s, e
    bl = blocks(text)
    before = [b for b in bl if b[1] <= i]
    after = [b for b in bl if b[0] >= j]
    if before:
        ps = before[-1][0]
        out, pair = _pair(text, ps, j, text[ps:before[-1][1]])
        return out, pair, before[-1][1]
    if after:
        pe = after[0][1]
        out, pair = _pair(text, i, pe, text[after[0][0]:pe])
        return out, pair, i
    out, pair = _pair(text, i, j, "")
    return out, pair, i


def apply(text: str, change: dict, *, cursor: int = 0,
          unit: str = "") -> tuple[str, list[dict], int]:
    """Apply one change to `text`: (the new text, its pairs, the position
    after it). `unit` is the text of the unit the change was proposed at:
    a replace, cut or move with no original works on it, and an insert
    with no anchor goes in after it. Raises NotApplied when its original
    or its anchor is not in the text."""
    kind = change["kind"]
    original = change.get("original") or unit
    where, quote = change.get("where") or "", change.get("quote") or ""
    if kind == "insert" and not where:
        where, quote = ("after", unit) if unit else ("end", "")
    if kind in ("replace", "cut", "move"):
        hit = find(text, original, cursor)
        if hit is None:
            raise NotApplied(f"“{(original or '')[:60]}” is not in the essay")
        i, j, _how = hit
        if kind == "replace":
            s, e = _block_at(text, i, j)
            para = text[s:e]
            new = para[:i - s] + change["text"].strip("\n") + para[j - s:]
            out, pair = _pair(text, s, e, new)
            return out, [pair], s + len(new)
        moved = change.get("text") or text[i:j]
        out, pair, pos = _cut(text, i, j)
        if kind == "cut":
            return out, [pair], pos
        if not where:
            where, quote = "end", ""
        out2, pair2, _p = _insert(out, moved, where, quote, 0)
        return out2, [pair, pair2], pos
    out, pair, pos = _insert(text, change["text"], where, quote, cursor)
    return out, [pair], pos


def apply_all(text: str, changes: list[dict], *, cursor: int = 0,
              unit: str = "") -> tuple[str, list[dict], int]:
    """Every change in order, all or none: NotApplied when any is not."""
    pairs: list[dict] = []
    pos = cursor
    for c in changes:
        text, ps, pos = apply(text, c, cursor=pos if c["kind"] != "insert" else cursor,
                              unit=unit)
        pairs += ps
    return text, pairs, pos
