"""Inline directives: `[Footnote: …]` and `[Explain: …]` — shorthand
instructions written in the prose, drafted in CHAT, landed by the CLI
(designs: docs/footnote-directive-design.md, docs/explain-directive-design.md).

    …the vital lie.[Footnote: cite Becker ch. 2 | label: RD]
    …the vital lie.[Explain: what Becker means by a vital lie]
    [Explain: why the Field is not a substance]           (a line of its own)

A tag is plain text, so it survives Obsidian, the Doc tabs, and every
push and pull unchanged — the same grammar as `[Illustration: …]`.
Unlike an illustration slot a directive is CONSUMED: applying it
replaces the tag. No registered prompt, no LLM call path: collect
REPORTS the open tags, the chat collaborator drafts against the style
law, the concept notes, and the essay, the author disposes in chat, and
`apply` is the state machine and the evidence channel — the division of
labour beats and margin proposals already use.

The two kinds differ only in what landing means:

- footnote: the tag becomes `[^label]` at its exact position and the
  definition `[^label]: text` is appended after the file's last
  definition (blank-line separated, the observed convention). Labels
  continue the file's series; a file with none takes the capitalized
  first letter of its stem; `| label: XX` in the tag overrides.
- explain: the drafted passage replaces the tag at its exact position —
  inline it continues the sentence's paragraph, alone on a line it is a
  paragraph of its own.

An unresolved tag never reaches a reader: every export strips it and
warns (footnote design §6).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .revisions import iter_manuscript_paths

KINDS = ("footnote", "explain", "judgment")
_TAGS = {
    # Keyword case-insensitive; the body never spans a line and never
    # nests a bracket; an empty body is not a tag at all.
    "footnote": re.compile(r"\[Footnote:\s*(?P<body>[^\[\]\n]+?)\s*\]", re.IGNORECASE),
    "explain": re.compile(r"\[Explain:\s*(?P<body>[^\[\]\n]+?)\s*\]", re.IGNORECASE),
    # A lens finding the author ACCEPTED in the tab: a work order at its
    # anchor, resolved by `lens repair` (a rewrite, a question, or an
    # intent), never by `apply`. Deleting it in the tab rejects it.
    "judgment": re.compile(r"\[Judgment:\s*(?P<body>[^\[\]\n]+?)\s*\]", re.IGNORECASE),
}
_LABEL_OPT = re.compile(r"\s*\|\s*label:\s*(?P<label>[A-Za-z]+)\s*$", re.IGNORECASE)
_DEFINITION = re.compile(r"^\[\^(?P<letters>[A-Za-z]+)(?P<n>\d+)\]:", re.MULTILINE)
_PARAGRAPH = re.compile(r"[^\n]+(?:\n[^\n]+)*")
_RESERVED = ("<<", ">>", "{{", "}}")


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


def scan_text(text: str, kind: str) -> list[dict]:
    """Every open tag of one kind in one file's text, in document order:
    the ordinal `n` (1-based within the file — the `--tag` handle), the
    line, the character span, the gist, the raw tag, whether it stands
    alone on its line, the paragraph that holds it, and — footnotes —
    the `| label:` override when the author gave one."""
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
        paragraph = next((p for a, b, p in paragraphs if a <= start < b), "")
        tags.append({
            "kind": kind, "n": n,
            "line": text.count("\n", 0, start) + 1,
            "start": start, "end": end,
            "gist": body, "raw": m.group(0), "label": label,
            "standalone": _standalone(text, start, end),
            "paragraph": paragraph,
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
        start, end = tag["start"], tag["end"]
        if tag["standalone"]:
            line_start = out.rfind("\n", 0, start) + 1
            line_end = out.find("\n", end)
            line_end = len(out) if line_end == -1 else line_end + 1
            out = out[:line_start] + out[line_end:]
            if line_start > 0 and out[line_start - 1:line_start + 1] == "\n\n":
                out = out[:line_start] + out[line_start + 1:]
            continue
        before = out[start - 1] if start > 0 else ""
        after = out[end] if end < len(out) else ""
        if before == " " and (after == " " or after in ".,;:!?)"):
            start -= 1
        out = out[:start] + out[end:]
    removed.reverse()
    return out, removed


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
                  new: str, label: str | None) -> None:
    from .db import ko_fields

    row = ko_fields("dt")
    row.update(
        manuscript_id=manuscript_id, origin_type=tag["kind"],
        origin_id=f"{tag['kind']}:{file}:{row['id'][-12:]}", file=file,
        anchor_quote=tag["paragraph"][:200] or None,
        proposed_old=tag["raw"], proposed_new=new, note=tag["gist"],
        state="applied", our_reply_ids="[]", last_author_reply_id=None,
        scope_kind="file", scope_ref=file,
        metadata=json.dumps({"kind": tag["kind"], "gist": tag["gist"],
                             "line": tag["line"], "label": label,
                             "standalone": tag["standalone"]}),
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


def _choose(text: str, kind: str, file: str, pairs: list) -> list[dict]:
    """Resolve every (handle, text[, footnote]) against ONE scan.
    Returns [{tag, text, footnote}] in document order. `footnote` is
    the explain directive's optional companion (the author's ruling of
    2026-09-03): an explanation that needs a source or a qualification
    the sentence cannot carry lands it as a footnote in the same
    review, instead of a second [Footnote:] round."""
    tags = scan_text(text, kind)
    if not tags:
        raise LookupError(f"{file} has no open [{kind.title()}: …] tag")
    chosen: list[dict] = []
    seen: set[int] = set()
    for pair in pairs:
        handle, draft = pair[0], pair[1]
        note = pair[2] if len(pair) > 2 else None
        tag = find_tag(tags, handle)
        if tag["n"] in seen:
            raise ValueError(f"tag #{tag['n']}: named twice")
        if note is not None and not str(note).strip():
            note = None
        if note is not None and kind != "explain":
            raise ValueError(f"tag #{tag['n']}: a footnote rides only on an "
                             f"explain apply — a [{kind.title()}:] tag IS "
                             "the footnote")
        seen.add(tag["n"])
        chosen.append({"tag": tag, "text": _clean(tag["n"], "text", draft),
                       "footnote": (_clean(tag["n"], "footnote", note)
                                    if note is not None else None)})
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
        if kind == "explain" and c["footnote"] is None:
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
    are (`--tag` handle, text); handles resolve against ONE scan.

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
    # Confinement: the Doc-mapping key and the write path are the same
    # manuscript-relative name. An absolute (or `..`-escaping) argument
    # that skipped this step missed `checked_out` and wrote locally while
    # the Doc was still the working copy (found 2026-10-01 / confirmed
    # 2026-10-04).
    try:
        file = api._resolve_relpath(manuscript, file)
    except LookupError:
        raise LookupError(f"no manuscript file '{file}'") from None
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
        if kind == "footnote":
            new_text = _land_ref(new_text, tag, labels[tag["n"]])
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
        if kind == "footnote":
            new = f"[^{label}]: {c['text']}"
        else:
            new = _passage(c, labels) + (f"\n\n[^{label}]: {c['footnote']}"
                                         if label else "")
        _evidence_row(db, manuscript["id"], file, tag, new, label)
        landed.append({"n": tag["n"], "line": tag["line"], "gist": tag["gist"],
                       "label": label, "text": c["text"],
                       "footnote": c["footnote"]})
    import contextlib
    import io

    with contextlib.redirect_stdout(io.StringIO()):
        after = api.collect(db, manuscript, config, source=f"{kind}-apply",
                            episode=api.NO_EPISODE)
    return {"kind": kind, "file": file, "mode": "applied", "landed": landed,
            "version_no": after.get("version_no"), "url": None, "failed": []}


def _thread_row(db, manuscript_id: str, kind: str, file: str, tag: dict,
                old: str, new: str, anchor: int, form: str,
                label: str | None) -> dict:
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
                             "directive": kind}),
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
        if kind == "footnote":
            new, definition = f"[^{label}]", c["text"]
        else:
            new, definition = _spaced(text, tag, _passage(c, labels)), c["footnote"]
        replaces.append(_thread_row(db, mid, kind, file, tag, tag["raw"], new,
                                    paragraph_of(tag), "replace", label))
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
    result = gdocs.write_pending_forms(db, manuscript, file, threads,
                                       service, docs_service)
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
                           "text": c["text"], "footnote": c["footnote"]})
    return {"kind": kind, "file": file, "mode": "marked", "landed": staged,
            "version_no": None, "url": result["url"], "failed": failed}


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
    try:
        file = api._resolve_relpath(manuscript, file)
    except LookupError:
        raise LookupError(f"no manuscript file '{file}'") from None
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
    final, forms = passes.final_text_from_marked(fetched["marked"],
                                                 written=written)
    import contextlib
    import io

    path = Path(manuscript["path"]) / file
    with contextlib.redirect_stdout(io.StringIO()):
        api.collect(db, manuscript, config, source=f"pre-{kind}-resolve")
    diffs = passes.record_resolution(db, mid, file, forms, origin_type=kind,
                                     evidence_type=f"{kind}_edit",
                                     final_text=final)
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
    return {"kind": kind, "file": file, "forms": len(forms),
            "accepted": states.count("cleaned"),
            "declined": states.count("declined"), "diffs": diffs,
            "version_no": after.get("version_no"), "url": url,
            "warnings": warnings}
