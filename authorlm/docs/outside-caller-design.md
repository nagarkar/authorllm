# Outside Callers — Design Reference

Built 2026-10-02 (issues #128 to #132). This is the reference for the
five entry points a tool that is not one of AuthorLM's own passes uses
to keep a document as a manuscript. The first such tool is ytlm, which
keeps one "corpus document" per corpus and calls AuthorLM as a Python
library through one adapter (ytlm's `docs/design/corpus-document.md`,
sections 3.2, 3.3 and 6).

Related designs: `margin-threads-design.md` (the `<<old>>{{new}}`
grammar), `critique-pass-design.md` §6.3 (the write, the pause, the
resolve), `filter-pass-design.md` §2 (the finding-to-edit door these
verbs sit beside), `footnote-directive-design.md` §5 (the resolve that
re-pushes the tab), `math-and-physics-guidelines.md` §5 (`[Omit:]`).

---

## 0. What it is, and what it is not

An outside caller proposes text the way a pass does: as pending forms
in a Doc tab, ruled on there by the author, landed by an explicit
resolve. It differs from a pass in what it starts from. It has no run
row, no finding, no margin comment and no prompt. It has a list of
revisions and its own name.

**Not a new transport.** The forms are written by
`gdocs.write_pending_forms` and read back by
`gdocs.tab_marked_markdown`, the same writer and reader every pass
uses. **Not a pass.** No model is called anywhere on this road: no
extraction, no summary rebuild, no learnings distiller. **Not a second
record.** The threads are ordinary `doc_threads` rows; the landed text
is an ordinary collected version.

The five entry points, in the order a caller meets them:

| # | Entry point | Module | Google | Writes |
|---|---|---|---|---|
| 1 | `register_manuscript(..., extraction=False)` | `api` | no | the manuscript row |
| 2 | `[Omit: all]` in a file | `export` | no | nothing (resolved at build) |
| 3 | `read_manuscript` | `gdocs` | read | nothing |
| 4 | `stage_revisions` | `api` | write | threads, the tab |
| 5 | `resolve_revisions` | `api` | write | threads, evidence, the file, a version, the tab |

`service` and `docs_service` are the Drive and Docs clients
(`gdocs.get_service`, `gdocs.get_docs_service`), passed in by the
caller as every `gdocs` function takes them.

---

## 1. The extraction switch (#128)

```python
api.register_manuscript(db, name, path, author="", copyright_owner="",
                        paperback_isbn="", hardcover_isbn="",
                        trim_size="", bleed=False,
                        extraction=True) -> dict
api.update_manuscript_metadata(db, manuscript, ...,
                               extraction=None) -> dict
extraction.manuscript_extraction_enabled(db, manuscript) -> bool
extraction.set_manuscript_extraction(db, manuscript, enabled) -> None
extraction.extraction_allowed(db, manuscript, config) -> bool
```

The setting lives in `manuscripts.metadata` (the JSON column in the
workspace database, `<workspace>/.authorlm/authorlm.db`) as
`{"extraction": {"enabled": false}}`, the same shape as the
`[extraction]` section of `config.toml` that it narrows. Absent means
on. It is read from the row on every check, never from the caller's
manuscript dict, because the Doc mapping and the extraction watermark
rewrite that column.

Command line: `authorlm init --extraction off` at registration,
`authorlm manuscript set --extraction on|off` afterwards, and
`authorlm manuscript show` prints a line when it is off.

Where it is honoured: `cmd_init`, `api.collect` (with `analyze` or
`auto`), `api.write_complete`, the `extract` verb, and
`extraction.extract_concepts` itself, which is the road the MCP tool
takes. The global switch (`[extraction] enabled` in `config.toml`)
still wins when it is off. `collect` reports `"extraction": "off"` for
either switch.

Left running on purpose: the illustration pin written by `cmd_init`,
and the other scans in `collect`. They find nothing on a manuscript
with no concepts.

---

## 2. `[Omit: all]` (#132)

`[Omit: all] ... [/Omit]` drops the region from every name in
`export.OUTPUTS` (`authorlm/export.py`), present and future: `pdf`,
`docx`, `epub`, `md`, `doc` (the export Doc) and `audio`. The Doc tabs
are not an output. A tab is the working surface, so the region and its
two tag lines go to the tab on a push and come back unchanged on a
pull.

`all` may sit beside named outputs and means the same. An `[Only:]`
nested inside still re-includes its body for its outputs. `[Only: all]`
is a fault, named by file and line like any other malformed tag. The
author-facing statement is in `math-and-physics-guidelines.md` §5.

---

## 3. `read_manuscript` (#131)

```python
gdocs.read_manuscript(db, manuscript, service, docs_service=None,
                      bridge=None) -> dict
```

The whole manuscript as it stands in the Doc, every tab at once.
One export of the master Doc, split with `split_tabbed_export`, and one
`fetch_open_comments`. It writes nothing: no local file, no hash, no
mapping, no thread or comment row, and no comment is replied to or
resolved.

```python
{"doc_id": str | None, "url": str | None,
 "files": {relpath: {
     "marked":  str | None,   # tab markdown, forms intact
     "settled": str | None,   # forms collapsed to OLD: what a pull lands
     "state":   "unchanged" | "changed" | "local_ahead" | "conflict"
                | "missing",
     "forms":   [{"kind": "replace" | "insert", "old": str, "new": str}],
     "comments": [{"comment_id", "quote", "content", "author",
                   "created", "heading", "replies": [str]}],
     "tab_id": str | None, "url": str | None, "pushed": bool,
     "dangling": [...], "marker_warnings": [...]}},
 "unattributed_comments": [...],   # same shape as a file's comments
 "comments_error": str | None}
```

- `files` covers every mapped file and every local manuscript file.
  A file with no tab, or whose tab is gone from the export, is
  `missing` with `marked` and `settled` None.
- `state` is `gdocs.three_way` of `settled` against the local file on
  the recorded base, exactly as `tab_marked_markdown` computes it.
- A comment is placed by its quoted text, matched against the tab text
  (settled, then marked). A quote found in no tab, or in more than one,
  goes to `unattributed_comments`.
- `forms` is `threads.pending_forms`: every form in the tab, whoever
  wrote it. A `{{…}}` the author typed inside a sentence is reported as
  an insert too.
- `pushed` is False for a tab that has never been agreed with its
  file. `gdocs.ensure_master` gives every file a tab the first time any
  file is pushed, so a file never pushed itself has an empty tab and
  reads `changed` with an empty `settled`. That is not the file's text.
- A manuscript with no master Doc makes no Google call and reports
  every local file as `missing`.

---

## 4. `stage_revisions` (#129)

```python
api.stage_revisions(db, manuscript, file, revisions, origin,
                    service, docs_service, config=None) -> dict
```

Each revision is `{"old", "new", "note", "anchor_paragraph"}`.

- **Replacement** (`old` non-empty): that exact text, inside one
  paragraph of the file. `anchor_paragraph` names the paragraph;
  omitted, the text must occur in exactly one.
- **Addition** (`old == ""`): a paragraph of its own after paragraph
  `anchor_paragraph`. Additions at one anchor appear in the order
  given.
- Paragraphs are counted from 1 over the file's blank-line-separated
  paragraphs, a heading being one of them (`revisions._paragraphs`, the
  writer's own count).
- `new` is one paragraph on one line, non-empty, with none of `<<`,
  `>>`, `{{`, `}}`.
- `origin` is the caller's name, for example `"ytlm"`.

```python
{"file": str, "origin": str,
 "written": [{"id", "index", "old", "new", "note", "anchor_paragraph"}],
 "failed": [(revision, reason)],
 "url": str | None, "warnings": [str]}
```

`index` is the revision's place in the list handed in. `failed` holds
the caller's own dicts.

The threads are `doc_threads` rows with `origin_type = 'external'`
(`api.EXTERNAL_ORIGIN`) for every outside caller, so none can collide
with a pass's rows. The caller's name is in the thread metadata under
`origin`, beside `kind`, `anchor_paragraph`, `original_new` and
`index`. States: `proposed` → `written`, or `withdrawn` when the writer
could not place the form.

What is held, and reported in `failed` rather than raised:

- **A tab with any producer's forms still out takes no new ones.**
  Every revision comes back with that reason. Nothing is written.
- A file that is mid-rewrite, carries forms on disk, or is in conflict
  with its tab.
- A single revision that cannot be staged (text not found, an anchor
  out of range, reserved markers, more than one line). The rest go
  ahead.

Only an unknown file, or an empty `origin`, raises.

Two things the verb does on the way:

- A file with no tab yet gets one from the writer's own levelling push,
  so a file made with `docs.add_doc` can arrive as its heading plus
  additions.
- A file left checked out by an earlier push is pulled first (the
  standing protocol, the same step `api.lens_push` takes). An edit the
  author made in the tab is brought into the local file and collected
  before the old halves are read from it. This is the one case where
  staging changes the local file, and it is named in `warnings`.

---

## 5. `resolve_revisions` (#130)

```python
api.resolve_revisions(db, manuscript, file, origin,
                      service, docs_service, config=None) -> dict
```

Reads one tab back and lands every form `origin` still has out in it.
One file per call. Resolving accepts whatever the author left
untouched, so resolving tabs they have not read is the caller's loop
and the caller's explicit choice.

| In the tab | Verdict |
|---|---|
| the form untouched | `accepted` |
| the `{{new}}` half edited | `modified`, `final` is the author's wording |
| braces removed by hand, new text kept | `accepted` |
| the `{{new}}` half emptied or deleted | `declined` |
| the old text put back, or an addition deleted | `declined` |
| a replacement's whole passage deleted | `modified`; `final` is the closest remaining paragraph, or None when none is close |

```python
{"file": str, "origin": str,
 "state": "unchanged" | "changed" | "local_ahead" | "conflict"
          | "missing" | None,
 "landed": bool,
 "revisions": [{"id", "index", "old", "new", "note", "anchor_paragraph",
                "verdict": "accepted" | "modified" | "declined" | None,
                "final": str | None}],
 "accepted": int, "modified": int, "declined": int,
 "version_no": int | None, "url": str | None,
 "tab_still_marked": bool, "warnings": [str]}
```

`revisions` is in the caller's order (`index`). `new` is what was
proposed.

Only the named caller's forms are resolved. Any other form in the tab
(another caller's, a pass's, a margin proposal) stays in the tab, and
the local file keeps its old half, as it always has until that
producer's own resolve.

The steps are the ones each pass repeats: `gdocs.tab_marked_markdown`
→ `passes.final_text_from_marked` → `passes.record_resolution` →
`api._write_resolved_text` → `api.collect(..., episode=NO_EPISODE)`.
Two things are done first, in `api._rule_external_marked`, because the
shared steps assume a pass that owns every form in its tab:

- Additions are paired with their own forms (the same text first, then
  the closest rewording) instead of by position.
- A replacement the author turned down by deleting the green half
  (`<<old>>{{}}` or a bare `<<old>>`) is put back to `old`, so it reads
  as a decline and not as a rewording to nothing.

`passes.record_resolution` gained one optional argument, `threads`, so
that it closes exactly the rows it is handed. Omitted, it behaves as
before. The existing passes do not pass it and are unchanged; they can
move to these shared steps later.

Evidence is `external_edit` (`api.EXTERNAL_EVIDENCE`), one row per
revision, under no episode.

Clearing the tab:

- When these were the only forms in the tab, a rebuild push
  (`gdocs.push_doc`), as `directives.resolve` does.
- When another producer's forms are still out, that push is held, and
  rightly: it would wipe them. Then
  `gdocs.settle_forms_in_tab(db, manuscript, file, outcomes, docs_service, bridge=None) -> list[str]`
  takes out these forms alone, each to the half that won. Leaving them
  would be worse than untidy: the other producer's resolve collapses
  every form it does not own to its old half and would undo, in the
  local file, text this resolve had landed.
- `tab_still_marked` says whether the tab was cleared. When it was not,
  the local file is still right and the reason is in `warnings`.

Nothing lands when the tab is `missing` or in `conflict`: `landed` is
False and each verdict is None. With none of this origin's forms out,
no Google call is made; `state` is None and `revisions` is empty.

---

## 6. What a caller must know

1. **Order.** Register, add files, then per update: read, stage,
   wait for the author, resolve. A tab takes one batch at a time; the
   next `stage_revisions` on it is held until `resolve_revisions`.
2. **No session is needed.** Every collect on this road works without
   an open session and attaches to no episode.
3. **A new manuscript needs nothing before the first stage.** There
   may be no master Doc; the first stage creates the folder, the Doc
   and a tab for every file. The file being staged onto must exist
   locally and should have at least a heading (`docs.add_doc` writes
   one), with additions anchored after paragraph 1.
4. **`anchor_paragraph` 0 is for a file with no paragraphs.** The
   shared writer places a head insertion on the first paragraph's own
   line, so on a file that has text an addition at 0 is returned in
   `failed`. Putting text before an existing first paragraph is not
   expressible as a revision today.
5. **Take old text from the same place the writer does.** A
   replacement's `old` is matched against the local file. After a
   resolve the local file and the tab agree, and `read_manuscript`'s
   `settled` is that text. If the author has edited the tab since,
   stage pulls first, and an `old` taken from `settled` still matches.
6. **Staging leaves the file checked out**, as every push does. The
   next stage pulls it; nothing else is needed.
7. **One line per paragraph.** A multi-paragraph addition is several
   revisions at one anchor.

---

## 6a. A caller's own record (added 2026-10-03)

Added for nagarkar/ytlm#23: a caller reaches AuthorLM's tables only
through these doors, never with its own SQL.

```python
api.caller_metadata(db, manuscript, origin) -> dict
api.set_caller_metadata(db, manuscript, origin, values) -> dict
api.outstanding_revisions(db, manuscript, origin) -> {file: int}
api.withdraw_revisions(db, manuscript, file, origin) -> [{"id", "old", "new", "note"}]
```

- **Settings.** A caller's own keys (ytlm keeps the Doc's title and
  whether it has named the Doc) live in `manuscripts.metadata` under
  `callers.<origin>`, read from the row. `set_caller_metadata` merges
  key by key and leaves the Doc mapping and the extraction switch
  alone. Keys a caller wrote at the top level under its own name before
  this door read back through it and move under `callers` on the first
  write.
- **Outstanding.** The caller's `written` threads per file: staged and
  neither resolved nor withdrawn.
- **Withdraw.** Closes the caller's `written` threads on one file as
  `withdrawn`; any other producer's are left alone. The forms stay in
  the tab until the caller rebuilds it from the local file with
  `gdocs.push_doc`, which those threads no longer hold.

---

## 7. Boundaries

- No command-line verb and no MCP tool for staging or resolving. The
  caller is a library caller.
- No structure changes: splitting, merging and reordering tabs are not
  revisions.
- No model call, and no belief learning from these verdicts beyond the
  evidence rows.
- The margin is not a verdict channel here. Comments are returned by
  `read_manuscript` as the author's notes and are otherwise left alone.
