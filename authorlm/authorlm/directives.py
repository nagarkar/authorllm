"""Inline directives: `[Footnote: …]`, `[Explain: …]` and
`[Suggestion: …]` — shorthand instructions written in the prose, drafted
in CHAT, landed by the CLI (designs: docs/footnote-directive-design.md,
docs/explain-directive-design.md, docs/suggestion-directive-design.md).

    …the vital lie.[Footnote: cite Becker ch. 2 | label: RD]
    …the vital lie.[Explain: what Becker means by a vital lie]
    [Explain: why the Field is not a substance]           (a line of its own)
    …the vital lie.[Suggestion: simplify this sentence]
    [Suggestion: add an example]                          (the paragraph before)

A tag is plain text, so it survives Obsidian, the Doc tabs, and every
push and pull unchanged — the same grammar as `[Illustration: …]`.
Unlike an illustration slot a directive is CONSUMED: applying it
replaces the tag. No registered prompt, no LLM call path: collect
REPORTS the open tags, the chat collaborator drafts against the style
law, the concept notes, and the essay, the author disposes in chat, and
`apply` is the state machine and the evidence channel — the division of
labour beats and margin proposals already use.

The kinds differ only in what landing means:

- footnote: the tag becomes `[^label]` at its exact position and the
  definition `[^label]: text` is appended after the file's last
  definition (blank-line separated, the observed convention). Labels
  continue the file's series; a file with none takes the capitalized
  first letter of its stem; `| label: XX` in the tag overrides.
- explain: the drafted passage replaces the tag at its exact position —
  inline it continues the sentence's paragraph, alone on a line it is a
  paragraph of its own.
- suggestion: a request to EVALUATE the enclosing paragraph (alone on a
  line: the paragraph before it) and act on it in any way the gist names.
  The agreed text REPLACES that paragraph, tag and all; or the item is
  DECLINED with a reason — the tag goes, the paragraph stays, the reason
  is the evidence (the author's ruling of 2026-10-19: "Use 'suggestion'
  as a thing to evaluate … It's similar to a comment but doesn't have
  some of the problems with comments").

An unresolved tag never reaches a reader: every export strips it and
warns (footnote design §6).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .revisions import iter_manuscript_paths

KINDS = ("footnote", "explain", "suggestion", "judgment")
_TAGS = {
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
_DEFINITION = re.compile(r"^\[\^(?P<letters>[A-Za-z]+)(?P<n>\d+)\]:", re.MULTILINE)
_PARAGRAPH = re.compile(r"[^\n]+(?:\n[^\n]+)*")
_RESERVED = ("<<", ">>", "{{", "}}")
# A standalone Suggestion evaluates authorial prose (design §1), never a
# heading, footnote definition, directive/media tag, or close-tag unit
# sitting between the prose and the note.
_NON_PROSE_TARGET = re.compile(
    r"^(?:"
    r"#{1,6}\s|"
    r"\[\^[^\]]+\]:|"
    r"\[(?:/)?(?:Illustration|Footnote|Explain|Suggestion|Judgment|Voice|"
    r"Clip|Collage|Montage)\b"
    r")",
    re.IGNORECASE)


def _kind(kind: str) -> str:
    if kind not in KINDS:
        raise ValueError(f"unknown directive kind '{kind}' (one of: {', '.join(KINDS)})")
    return kind


# ------------------------------------------------------------------ grammar

def _standalone(text: str, start: int, end: int) -> bool:
    """True when the tag is the whole of its line (whitespace aside)."""
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    if line_end == -1:
        line_end = len(text)
    return not text[line_start:start].strip() and not text[end:line_end].strip()


def _is_prose_suggestion_target(unit: str) -> bool:
    """True when a blank-line unit is prose a Suggestion may replace."""
    first = unit.strip().split("\n", 1)[0].strip()
    return bool(first) and not _NON_PROSE_TARGET.match(first)


def _prior_prose_target(paragraphs: list[tuple[int, int, str]], start: int
                        ) -> tuple[int, int, str] | None:
    """The nearest prose blank-line unit entirely before `start`, skipping
    structural markup a margin note never evaluates."""
    return next(((a, b, p) for a, b, p in reversed(paragraphs)
                 if b <= start and _is_prose_suggestion_target(p)), None)


def scan_text(text: str, kind: str) -> list[dict]:
    """Every open tag of one kind in one file's text, in document order:
    the ordinal `n` (1-based within the file — the `--tag` handle), the
    line, the character span, the gist, the raw tag, whether it stands
    alone on its line, the paragraph that holds it, the TARGET paragraph
    a suggestion acts on (`target`, `target_start`, `target_end`: the
    holding paragraph inline, the paragraph BEFORE a standalone tag,
    None when nothing precedes it), and — footnotes — the `| label:`
    override when the author gave one.

    A standalone suggestion's "paragraph before" is the nearest prior
    *prose* unit (design §1: the note under what it comments on). An
    intervening illustration, clip, heading, footnote definition, or
    other directive line is not the target — taking it as one let apply
    destroy that markup (or plant `<<tag>>{{prose}}` on the Doc road)."""
    pattern = _TAGS[_kind(kind)]
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
        standalone = _standalone(text, start, end)
        if standalone:
            target = (_prior_prose_target(paragraphs, start)
                      if kind == "suggestion"
                      else next(((a, b, p) for a, b, p in reversed(paragraphs)
                                 if b <= start), None))
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


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="replace")


def scan(root: Path, kind: str, files: list[str] | None = None
         ) -> dict[str, list[dict]]:
    """Open tags of one kind by file (or the named files only). Files
    with no tag are absent. The TOC and a file holding only the
    mid-rewrite placeholder are never scanned."""
    from .api import is_placeholder
    from .structure import TOC_FILENAME

    _kind(kind)
    root = Path(root)
    wanted = set(files or [])
    found: dict[str, list[dict]] = {}
    for rel, path in iter_manuscript_paths(root).items():
        if path.name == TOC_FILENAME or (wanted and rel not in wanted):
            continue
        text = _read(path)
        if is_placeholder(text):
            continue
        tags = scan_text(text, kind)
        if tags:
            found[rel] = tags
    return found


def open_report(root: Path, kinds: tuple[str, ...] = KINDS,
                files: list[str] | None = None) -> list[dict]:
    """What a collect and `get_status` surface: every open tag, one row
    each — kind, file, ordinal, line, gist, label."""
    rows = []
    for kind in kinds:
        for rel, tags in scan(root, kind, files).items():
            rows.extend({"kind": kind, "file": rel, "n": t["n"],
                         "line": t["line"], "gist": t["gist"],
                         "label": t["label"]} for t in tags)
    return rows


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


def find_tag(tags: list[dict], handle) -> dict:
    """`--tag`: the tag's ordinal in the file (first open tag is 1) or
    an unambiguous excerpt of its gist, case-insensitive."""
    if isinstance(handle, int) or (isinstance(handle, str) and handle.strip().isdigit()):
        n = int(handle)
        for tag in tags:
            if tag["n"] == n:
                return tag
        raise ValueError(f"no open tag #{n} (open: 1–{len(tags)})"
                         if tags else "no open tag in this file")
    needle = " ".join(str(handle).split()).lower()
    if not needle:
        raise ValueError("name a tag by its ordinal or an excerpt of its gist")
    hits = [t for t in tags if needle in " ".join(t["gist"].split()).lower()]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise ValueError(f"no open tag's gist contains '{handle}'")
    raise ValueError(f"'{handle}' matches {len(hits)} tags — use the ordinal: "
                     + "; ".join(f"#{t['n']} {t['gist']}" for t in hits))


# ------------------------------------------------------------------- labels

def existing_labels(text: str) -> list[tuple[str, int]]:
    """(letters, number) of every footnote definition, in file order."""
    return [(m.group("letters"), int(m.group("n")))
            for m in _DEFINITION.finditer(text)]


def next_label(text: str, stem: str, override: str | None = None) -> str:
    """Footnote design §4. A file with footnotes continues its series —
    the letters of its LAST definition, next unused number; a file with
    none takes the capitalized first letter of its stem; `| label: XX`
    names the letters and takes that series' next unused number."""
    labels = existing_labels(text)
    if override:
        letters = override.upper()
    elif labels:
        letters = labels[-1][0]
    else:
        first = next((c for c in stem if c.isalpha()), "N")
        letters = first.upper()
    used = [n for ls, n in labels if ls == letters]
    return f"{letters}{(max(used) + 1) if used else 1}"


# ------------------------------------------------------------------- landing

def _spaced(text: str, tag: dict, draft: str) -> str:
    """The passage as it must read at the tag's position: inline, a
    space is supplied on whichever side the prose needs one. The same
    text goes into a Doc form's {{new}} half, so the two roads land
    byte-identical prose."""
    if tag["standalone"]:
        return draft
    start, end = tag["start"], tag["end"]
    before = text[start - 1] if start > 0 else ""
    after = text[end] if end < len(text) else ""
    if (before and not before.isspace() and before not in "([\"'“‘/-"
            and draft[0] not in ".,;:!?)"):
        draft = " " + draft
    if after and (after.isalnum() or after in "([\"'“‘"):
        draft = draft + " "
    return draft


def _land_explain(text: str, tag: dict, draft: str) -> str:
    return text[:tag["start"]] + _spaced(text, tag, draft) + text[tag["end"]:]


def _land_suggestion(text: str, tag: dict, draft: str) -> str:
    """The agreed text REPLACES the target paragraph. Inline, that
    paragraph holds the tag, so the tag goes with it; standalone, the
    tag's own line goes too — and any units BETWEEN the target and the
    tag (an illustration, a clip, …) stay put."""
    if tag["standalone"]:
        body = text[:tag["target_start"]] + draft + text[tag["target_end"]:]
        shift = len(draft) - (tag["target_end"] - tag["target_start"])
        moved = dict(tag)
        moved["start"] = tag["start"] + shift
        moved["end"] = tag["end"] + shift
        return remove_tag(body, moved)
    return text[:tag["target_start"]] + draft + text[tag["target_end"]:]


def _target_without_tag(tag: dict) -> str:
    """An inline suggestion's paragraph as it reads once the tag is
    gone — the Doc road's {{new}} half for a decline."""
    inner = dict(tag)
    inner["start"] = tag["start"] - tag["target_start"]
    inner["end"] = tag["end"] - tag["target_start"]
    inner["standalone"] = False
    return remove_tag(tag["target"], inner)


def _land_ref(text: str, tag: dict, label: str) -> str:
    """The superscript lands exactly where the tag was."""
    start, end = tag["start"], tag["end"]
    ref = f"[^{label}]"
    after = text[end] if end < len(text) else ""
    if after and (after.isalnum() or after in "([\"'“‘"):
        ref += " "
    return text[:start] + ref + text[end:]


def _append_definition(body: str, label: str, definition: str) -> str:
    """The definition joins the end of the file's definition block,
    blank-line separated (the observed convention)."""
    # Continuation lines of a multi-paragraph definition are indented,
    # the pandoc convention every export reads.
    lines = definition.strip().split("\n")
    definition = "\n".join([lines[0]] + [("    " + ln if ln.strip() else ln)
                                         for ln in lines[1:]])
    entry = f"[^{label}]: {definition}"
    defs = list(_DEFINITION.finditer(body))
    if not defs:
        return body.rstrip("\n") + "\n\n" + entry + "\n"
    # After the last definition AND its indented continuation lines.
    cursor = body.find("\n", defs[-1].start())
    cursor = len(body) if cursor == -1 else cursor
    while cursor < len(body):
        nxt = body.find("\n", cursor + 1)
        nxt = len(body) if nxt == -1 else nxt
        line = body[cursor + 1:nxt]
        if line.strip() and not line.startswith((" ", "\t")):
            break
        if not line.strip():
            # A blank line ends the definition unless an indented
            # continuation follows it.
            peek_end = body.find("\n", nxt + 1)
            peek_end = len(body) if peek_end == -1 else peek_end
            if not body[nxt + 1:peek_end].startswith((" ", "\t")):
                break
        cursor = nxt
    return body[:cursor].rstrip("\n") + "\n\n" + entry + "\n" + body[cursor:].lstrip("\n")


def _evidence_row(db, manuscript_id: str, file: str, tag: dict,
                  new: str, label: str | None, old: str | None = None,
                  state: str = "applied", reason: str | None = None) -> None:
    """The direct road's record: the tag (a suggestion: its target
    paragraph) as old, what landed as new, the gist as note. A declined
    suggestion is state `declined` with the reason in the metadata."""
    from .db import ko_fields

    row = ko_fields("dt")
    meta = {"kind": tag["kind"], "gist": tag["gist"], "line": tag["line"],
            "label": label, "standalone": tag["standalone"]}
    if reason is not None:
        meta["reason"] = reason
    row.update(
        manuscript_id=manuscript_id, origin_type=tag["kind"],
        origin_id=f"{tag['kind']}:{file}:{row['id'][-12:]}", file=file,
        anchor_quote=tag["paragraph"][:200] or None,
        proposed_old=tag["raw"] if old is None else old, proposed_new=new,
        note=tag["gist"], state=state, our_reply_ids="[]",
        last_author_reply_id=None, scope_kind="file", scope_ref=file,
        metadata=json.dumps(meta),
    )
    db.insert("doc_threads", row)


def _checked_out(db, manuscript: dict, file: str) -> bool:
    from .gdocs import doc_status

    entry = doc_status(db, manuscript).get(file)
    return isinstance(entry, dict) and bool(entry.get("checked_out"))


def _services(services, file: str, step: str):
    """The Drive/Docs pair, built only when the Doc road is actually
    taken (api._resolve_services' ruling: every state refusal must be
    reachable with no token and no network)."""
    if services is None:
        raise ValueError(
            f"{file} is checked out to Google Docs — the Doc is the working "
            f"copy — and {step} needs the Doc bridge, but none was supplied. "
            f"Run it from the CLI (which authorizes), or 'doc pull {file}' "
            f"first to take the local road.")
    try:
        return services()
    except ValueError as err:
        raise ValueError(f"{err}\nThe Doc road for {file} needs the [gdocs] "
                         f"bridge — authorize with 'doc auth'.") from err


def _clean(n: int, what: str, draft: str | None) -> str:
    draft = (draft or "").strip()
    if not draft:
        raise ValueError(f"tag #{n}: the {what} is empty")
    if any(mark in draft for mark in _RESERVED):
        raise ValueError(f"tag #{n}: the {what} carries reserved grammar "
                         "(<<, >>, {{, }})")
    if any(p.search(draft) for p in _TAGS.values()):
        raise ValueError(f"tag #{n}: the {what} carries a tag of its own")
    return draft


def _item(pair) -> dict:
    """One apply item, from a (handle, text[, footnote]) tuple or a
    {tag, text?, footnote?, decline?, reason?} dict."""
    if isinstance(pair, dict):
        return {"tag": pair.get("tag"), "text": pair.get("text"),
                "footnote": pair.get("footnote"),
                "decline": bool(pair.get("decline")),
                "reason": pair.get("reason")}
    return {"tag": pair[0], "text": pair[1],
            "footnote": pair[2] if len(pair) > 2 else None,
            "decline": False, "reason": None}


def _choose(text: str, kind: str, file: str, pairs: list) -> list[dict]:
    """Resolve every item against ONE scan. Returns [{tag, text,
    footnote, decline, reason}] in document order. `footnote` is the
    explain directive's optional companion (the author's ruling of
    2026-09-03): an explanation that needs a source or a qualification
    the sentence cannot carry lands it as a footnote in the same
    review, instead of a second [Footnote:] round. `decline` is the
    suggestion directive's "no": evaluated, not taken, with a reason."""
    tags = scan_text(text, kind)
    if not tags:
        raise LookupError(f"{file} has no open [{kind.title()}: …] tag")
    chosen: list[dict] = []
    seen: set[int] = set()
    targets: dict[int, int] = {}
    for pair in pairs:
        item = _item(pair)
        handle, draft, note = item["tag"], item["text"], item["footnote"]
        tag = find_tag(tags, handle)
        if tag["n"] in seen:
            raise ValueError(f"tag #{tag['n']}: named twice")
        if note is not None and not str(note).strip():
            note = None
        if note is not None and kind != "explain":
            raise ValueError(f"tag #{tag['n']}: a footnote rides only on an "
                             f"explain apply — a [{kind.title()}:] tag "
                             + ("IS the footnote" if kind == "footnote"
                                else "lands a paragraph, not an apparatus"))
        if item["decline"] and kind != "suggestion":
            raise ValueError(f"tag #{tag['n']}: only a [Suggestion:] tag is "
                             "declined on apply — a declined draft for a "
                             f"[{kind.title()}:] tag simply stays open")
        if kind == "suggestion":
            if tag["target"] is None:
                raise ValueError(f"tag #{tag['n']}: a standalone [Suggestion:] "
                                 "acts on the paragraph before it, and "
                                 "nothing precedes this one")
            other = targets.get(tag["target_start"])
            if other is not None:
                raise ValueError(f"tag #{tag['n']} and tag #{other} act on "
                                 "the same paragraph — land one, then the "
                                 "other")
            targets[tag["target_start"]] = tag["n"]
        seen.add(tag["n"])
        if item["decline"]:
            if draft is not None and str(draft).strip():
                raise ValueError(f"tag #{tag['n']}: a decline carries a "
                                 "reason, not a text")
            chosen.append({"tag": tag, "text": None, "footnote": None,
                           "decline": True,
                           "reason": _clean(tag["n"], "reason",
                                            item["reason"])})
            continue
        chosen.append({"tag": tag, "text": _clean(tag["n"], "text", draft),
                       "footnote": (_clean(tag["n"], "footnote", note)
                                    if note is not None else None),
                       "decline": False, "reason": None})
    return sorted(chosen, key=lambda c: c["tag"]["start"])


def _mint_labels(text: str, file: str, chosen: list[dict],
                 kind: str) -> dict[int, str]:
    """Labels in DOCUMENT order, so the series reads left to right;
    each minted label is reserved against the next within the batch.
    A footnote apply labels every entry; an explain apply labels only
    the entries carrying a footnote."""
    labels: dict[int, str] = {}
    probe = text
    for c in chosen:
        if kind != "footnote" and c["footnote"] is None:
            continue
        tag = c["tag"]
        label = next_label(probe, Path(file).stem, tag["label"])
        labels[tag["n"]] = label
        probe += f"\n[^{label}]: -\n"
    return labels


def _passage(c: dict, labels: dict[int, str]) -> str:
    """An explain entry's landed prose: the passage, with the companion
    footnote's superscript at its end when one rides."""
    label = labels.get(c["tag"]["n"])
    return c["text"] + (f"[^{label}]" if label else "")


def _definition_text(definition: str) -> str:
    lines = definition.strip().split("\n")
    return "\n".join([lines[0]] + [("    " + ln if ln.strip() else ln)
                                   for ln in lines[1:]])


def apply(db, manuscript: dict, config: dict, kind: str, file: str,
          pairs: list[tuple[str | int, str]], services=None) -> dict:
    """Land — or stage — one or more agreed drafts in one file. `pairs`
    are (`--tag` handle, text) tuples or {tag, text, footnote?, decline?,
    reason?} dicts; handles resolve against ONE scan. A suggestion item
    with `decline: true` and a `reason` removes the tag and leaves the
    paragraph as it stands — evaluated, not taken.

    Two roads, chosen by the file's checkout state (the author's ruling
    of 2026-09-03):

    - NOT checked out: direct apply. The text lands, one write, one
      evidence row per landing, one collect under NO episode. Chat was
      the review.
    - CHECKED OUT: the Doc is the working copy and the tab is the review
      surface, exactly as for every other machine proposal. The drafts
      are staged as `doc_threads` rows and written into the tab as
      `<<tag>>{{new}}` forms (a footnote's definition as a `{{…}}`
      insertion after the last paragraph); the LOCAL FILE STAYS
      PRISTINE. The author edits or accepts in the Doc; `resolve` lands
      it. `services` is the Drive/Docs pair as a callable, built only
      on this road.

    Returns {kind, file, mode: applied|marked, landed, version_no,
    url, failed}."""
    from . import api

    _kind(kind)
    if kind == "judgment":
        raise ValueError("a [Judgment: …] tag is resolved by 'lens repair "
                         f"{file}' (a rewrite, a question, or an intent), or "
                         "dismissed with 'lens repair --dismiss'; it is never "
                         "applied as text.")
    if not pairs:
        raise ValueError("nothing to apply — give at least one --tag/--text pair")
    root = Path(manuscript["path"])
    path = root / file
    if not path.is_file():
        raise LookupError(f"no manuscript file '{file}'")
    text = _read(path)
    chosen = _choose(text, kind, file, pairs)
    labels = _mint_labels(text, file, chosen, kind)

    if _checked_out(db, manuscript, file):
        return _stage_doc(db, manuscript, kind, file, text, chosen, labels,
                          services)

    new_text = text
    for c in reversed(chosen):
        tag = c["tag"]
        if c["decline"]:
            new_text = remove_tag(new_text, tag)
        elif kind == "footnote":
            new_text = _land_ref(new_text, tag, labels[tag["n"]])
        elif kind == "suggestion":
            new_text = _land_suggestion(new_text, tag, c["text"])
        else:
            new_text = _land_explain(new_text, tag, _passage(c, labels))
    # Definitions join in DOCUMENT order, so the block reads as the
    # series does; the references above were landed last-to-first only
    # to keep offsets valid.
    for c in chosen:
        label = labels.get(c["tag"]["n"])
        if label:
            new_text = _append_definition(
                new_text, label,
                c["text"] if kind == "footnote" else c["footnote"])
    path.write_text(new_text, encoding="utf-8")
    landed: list[dict] = []
    for c in chosen:
        tag, label = c["tag"], labels.get(c["tag"]["n"])
        if c["decline"]:
            _evidence_row(db, manuscript["id"], file, tag, "", None,
                          old=tag["target"], state="declined",
                          reason=c["reason"])
        elif kind == "footnote":
            _evidence_row(db, manuscript["id"], file, tag,
                          f"[^{label}]: {c['text']}", label)
        elif kind == "suggestion":
            _evidence_row(db, manuscript["id"], file, tag, c["text"], None,
                          old=tag["target"])
        else:
            new = _passage(c, labels) + (f"\n\n[^{label}]: {c['footnote']}"
                                         if label else "")
            _evidence_row(db, manuscript["id"], file, tag, new, label)
        landed.append({"n": tag["n"], "line": tag["line"], "gist": tag["gist"],
                       "label": label, "text": c["text"],
                       "footnote": c["footnote"], "declined": c["decline"],
                       "reason": c["reason"]})
    import contextlib
    import io

    with contextlib.redirect_stdout(io.StringIO()):
        after = api.collect(db, manuscript, config, source=f"{kind}-apply",
                            episode=api.NO_EPISODE)
    return {"kind": kind, "file": file, "mode": "applied", "landed": landed,
            "version_no": after.get("version_no"), "url": None, "failed": []}


def _thread_row(db, manuscript_id: str, kind: str, file: str, tag: dict,
                old: str, new: str, anchor: int, form: str,
                label: str | None, reason: str | None = None) -> dict:
    from .db import ko_fields

    row = ko_fields("dt")
    row.update(
        manuscript_id=manuscript_id, origin_type=kind,
        origin_id=f"{kind}:{file}:{row['id'][-12:]}", file=file,
        anchor_quote=tag["paragraph"][:200] or None,
        proposed_old=old, proposed_new=new, note=tag["gist"],
        state="proposed", our_reply_ids="[]", last_author_reply_id=None,
        scope_kind="file", scope_ref=file,
        metadata=json.dumps({"kind": form, "anchor_paragraph": anchor,
                             "intent_id": None, "original_new": new,
                             "gist": tag["gist"], "line": tag["line"],
                             "label": label, "standalone": tag["standalone"],
                             "directive": kind, "raw": tag["raw"],
                             "reason": reason}),
    )
    db.insert("doc_threads", row)
    return row


def _stage_doc(db, manuscript: dict, kind: str, file: str, text: str,
               chosen: list[dict], labels: dict[int, str],
               services) -> dict:
    """The Doc road of `apply`: stage, write the forms, local pristine."""
    from . import gdocs
    from .revisions import _paragraphs

    mid = manuscript["id"]
    pending = gdocs.forms_pending(db, mid, file)
    if pending:
        remedy = gdocs._SETTLE_REMEDY.get(pending, "settle the staged edits")
        raise ValueError(
            f"{file} already carries {pending} forms in its tab — run "
            f"'{remedy.format(file=file)}' before staging more.")
    paragraphs = _paragraphs(text)

    def paragraph_of(tag: dict) -> int:
        before = text[:tag["start"]]
        return len(_paragraphs(before + "x")) if before.strip() else 1

    service, docs_service = _services(services, file, "apply")
    replaces: list[dict] = []
    inserts: list[dict] = []
    for c in chosen:
        tag, label = c["tag"], labels.get(c["tag"]["n"])
        old, anchor, definition = tag["raw"], paragraph_of(tag), None
        if kind == "suggestion":
            # The form wraps the TARGET paragraph — inline, tag and all;
            # standalone, the prose unit before it (skipping intervening
            # markup), the tag line left standing until resolve consumes
            # it on acceptance. A decline wraps the tag itself: the
            # author sees it going and the paragraph staying. Anchor from
            # target_start so an intervening illustration/clip is not the
            # planted unit (`anchor -= 1` only worked when the target was
            # the immediate previous blank-line paragraph).
            if c["decline"]:
                old = tag["raw"] if tag["standalone"] else tag["target"]
                new = "" if tag["standalone"] else _target_without_tag(tag)
            else:
                old, new = tag["target"], c["text"]
                if tag["target_start"] is not None:
                    before = text[:tag["target_start"]]
                    anchor = (len(_paragraphs(before + "x"))
                              if before.strip() else 1)
        elif kind == "footnote":
            new, definition = f"[^{label}]", c["text"]
        else:
            new, definition = _spaced(text, tag, _passage(c, labels)), c["footnote"]
        replaces.append(_thread_row(db, mid, kind, file, tag, old, new,
                                    anchor, "replace", label, c["reason"]))
        if label:
            inserts.append(_thread_row(
                db, mid, kind, file, tag, "",
                f"[^{label}]: {_definition_text(definition)}",
                len(paragraphs), "insert", label))
    # The writer lands each insertion at the END of its anchor paragraph,
    # so at one anchor the LAST written sits first: hand the insertions
    # over in reverse so the tab reads the definitions in series order.
    # (`pending_write_order` is a stable sort and keeps that order.)
    threads = replaces + list(reversed(inserts))
    # Atomic: a superscript whose definition could not be placed is a
    # marker pointing nowhere, so the file's forms land together or not
    # at all (it-816cd7998fec).
    result = gdocs.write_pending_forms(db, manuscript, file, threads,
                                       service, docs_service, atomic=True)
    written_ids = {t["id"] for t in result["written"]}
    for t in threads:
        if t["id"] in written_ids:
            db.update("doc_threads", t["id"], {"state": "written"})
        else:
            db.update("doc_threads", t["id"], {"state": "withdrawn"})
    failed = [(loads_gist(t), why) for t, why in result["failed"]]
    staged = []
    for c in chosen:
        tag = c["tag"]
        ok = all(r["id"] in written_ids for r in replaces + inserts
                 if json.loads(r["metadata"])["line"] == tag["line"]
                 and json.loads(r["metadata"])["gist"] == tag["gist"])
        if ok:
            staged.append({"n": tag["n"], "line": tag["line"],
                           "gist": tag["gist"], "label": labels.get(tag["n"]),
                           "text": c["text"], "footnote": c["footnote"],
                           "declined": c["decline"], "reason": c["reason"]})
    return {"kind": kind, "file": file, "mode": "marked", "landed": staged,
            "version_no": None, "url": result["url"], "failed": failed}


def _settle_empty_declines(db, manuscript_id: str, marked: str,
                           written: list[dict]) -> tuple[str, list[dict], int]:
    """A standalone suggestion's decline is staged as `<<tag>>{{}}` on a
    line of its own. Left as it is, the author accepted the decline:
    the WHOLE line goes (the generic resolve would leave an empty
    paragraph where the tag stood), the thread is cleaned and its
    evidence written here. A green half the author typed into, or a
    deleted form (the tag standing again), is left to the generic
    resolve. Returns (marked, the threads still to resolve, settled)."""
    from . import passes, threads as th

    remaining, settled = [], 0
    for t in written:
        meta = json.loads(t.get("metadata") or "{}")
        if not (meta.get("standalone") and meta.get("reason")
                and t["proposed_old"] and t["proposed_new"] == ""):
            remaining.append(t)
            continue
        form = th.render_pending(t["proposed_old"], "")
        at = marked.find(form)
        if at < 0 or not _standalone(marked, at, at + len(form)):
            remaining.append(t)
            continue
        line_start = marked.rfind("\n", 0, at) + 1
        line_end = marked.find("\n", at + len(form))
        line_end = len(marked) if line_end == -1 else line_end + 1
        marked = marked[:line_start] + marked[line_end:]
        if line_start > 0 and marked[line_start - 1:line_start + 1] == "\n\n":
            marked = marked[:line_start] + marked[line_start + 1:]
        db.update("doc_threads", t["id"], {"state": "cleaned"})
        passes._edit_evidence(db, manuscript_id, t, "resolved",
                              explanation=meta.get("reason"),
                              evidence_type="suggestion_edit")
        settled += 1
    return marked, remaining, settled


def _consume_standalone_tags(db, final: str, written: list[dict]) -> str:
    """A standalone suggestion's form wraps the paragraph BEFORE the tag
    and leaves the tag line standing in the tab; once the author has
    accepted it (the thread ended `cleaned`), the tag is consumed here —
    the first matching tag line after the landed paragraph. A declined
    form leaves its tag open. (A decline staged as `<<tag>>{{}}` has
    no standalone paragraph to find: the form itself removed the tag.)"""
    for t in written:
        meta = json.loads(t.get("metadata") or "{}")
        if (not meta.get("standalone") or meta.get("kind") != "replace"
                or meta.get("reason")):
            continue
        fresh = db.one("SELECT state, proposed_new FROM doc_threads "
                       "WHERE id = ?", (t["id"],))
        if not fresh or fresh["state"] != "cleaned":
            continue
        at = 0
        landed = " ".join((fresh["proposed_new"] or "").split())
        pos = " ".join(final.split()).find(landed) if landed else -1
        if pos >= 0:
            # Map the flattened position back: count non-space characters.
            need = len("".join(" ".join(final.split())[:pos + len(landed)].split()))
            seen = 0
            for i, ch in enumerate(final):
                seen += 0 if ch.isspace() else 1
                if seen >= need:
                    at = i
                    break
        for tag in scan_text(final, "suggestion"):
            if (tag["standalone"] and tag["raw"] == meta.get("raw")
                    and tag["start"] >= at):
                final = remove_tag(final, tag)
                break
    return final


def loads_gist(thread: dict) -> str:
    try:
        return json.loads(thread.get("metadata") or "{}").get("gist") or ""
    except ValueError:
        return ""


def pending(db, manuscript_id: str, kind: str,
            file: str | None = None) -> list[dict]:
    """Forms of one kind out in a tab (state `written`), by file."""
    from .passes import staged_threads

    files = [file] if file else sorted({
        r["file"] for r in db.all(
            "SELECT DISTINCT file FROM doc_threads WHERE manuscript_id = ? "
            "AND origin_type = ? AND state = 'written'", (manuscript_id, kind))})
    return [t for f in files
            for t in staged_threads(db, manuscript_id, f, states=("written",),
                                    origin_type=kind)]


def resolve(db, manuscript: dict, config: dict, kind: str, file: str,
            services=None) -> dict:
    """Finish the Doc road: read the tab's marked text back, honour the
    author's edits to every `{{…}}` half (a deleted form is a decline;
    the tag stays open), land the final text locally, record the
    evidence, collect under NO episode, and push the tab so the Doc
    shows the result — a resolve is not finished until the Doc has it.

    Returns {kind, file, forms, accepted, declined, diffs, version_no,
    url, warnings}."""
    from . import api, gdocs, passes

    _kind(kind)
    mid = manuscript["id"]
    written = passes.staged_threads(db, mid, file, states=("written",),
                                    origin_type=kind)
    if not written:
        raise LookupError(f"{file} has no {kind} forms out in its tab — "
                          f"nothing to resolve.")
    service, docs_service = _services(services, file, "resolve")
    fetched = gdocs.tab_marked_markdown(db, manuscript, file, service,
                                        docs_service)
    if fetched["state"] == "missing":
        raise LookupError(
            f"'{file}' has no matching section in the master Doc export — "
            f"the tab the forms were written to is gone. 'doc push {file}' "
            f"rebuilds it from the local file, which still holds the tags.")
    if fetched["state"] == "conflict":
        raise ValueError(
            f"'{file}' changed both locally and in the Doc since the last "
            f"sync — the resolve refuses to guess which wins. Compare the "
            f"local file against the Doc tab by hand, then re-run "
            f"'{kind} resolve {file}'.")
    warnings = list(fetched["marker_warnings"])
    marked, to_resolve = fetched["marked"], list(written)
    settled = 0
    if kind == "suggestion":
        marked, to_resolve, settled = _settle_empty_declines(
            db, mid, marked, written)
    final, forms = passes.final_text_from_marked(marked, written=to_resolve)
    import contextlib
    import io

    path = Path(manuscript["path"]) / file
    with contextlib.redirect_stdout(io.StringIO()):
        api.collect(db, manuscript, config, source=f"pre-{kind}-resolve")
    diffs = passes.record_resolution(db, mid, file, forms, origin_type=kind,
                                     evidence_type=f"{kind}_edit",
                                     final_text=final, threads=to_resolve)
    n_forms = len(forms) + settled
    if kind == "suggestion":
        final = _consume_standalone_tags(db, final, written)
    api._write_resolved_text(path, Path(manuscript["path"]), final)
    with contextlib.redirect_stdout(io.StringIO()):
        after = api.collect(db, manuscript, config, source=f"{kind}-resolve",
                            episode=api.NO_EPISODE)
    states = [db.one("SELECT state FROM doc_threads WHERE id = ?",
                     (t["id"],))["state"] for t in written]
    url = None
    try:
        url = gdocs.push_doc(db, manuscript, file, service=service,
                             docs_service=docs_service).get("url")
    except Exception as err:                            # noqa: BLE001
        warnings.append(f"the resolve landed locally but the push failed "
                        f"({err}) — 'doc push {file}' clears the marks.")
    return {"kind": kind, "file": file, "forms": n_forms,
            "accepted": states.count("cleaned"),
            "declined": states.count("declined"), "diffs": diffs,
            "version_no": after.get("version_no"), "url": url,
            "warnings": warnings}
