# Suggestion Directive — Design Reference

Built 2026-10-19 on the explain directive's road, by the author's ruling
of that day (verbatim): "Use 'suggestion' as a thing to evaluate -
explain is more of a directive to explain something further. Suggestion
can be anything - improve the verbiage, simplify, or whatever. It's
similar to a comment but doesn't have some of the problems with comments
(e.g. we cannot perform certain operations on paragraphs that have
comments in them)." This note records only what differs from
`explain-directive-design.md`, which (with
`footnote-directive-design.md` behind it) governs everything it does not
mention.

---

## 0. What it is

A `[Suggestion: …]` tag is a **request to evaluate a paragraph and act
on it** in whatever way the gist names — rewrite, simplify, tighten, add
an example, reconsider a claim, anything. It is the author's margin
comment written INTO the prose: plain text, so it survives Obsidian, the
Doc tabs, and every push and pull, and so the paragraph that carries it
stays an ordinary paragraph (a Doc comment anchors a range and blocks
the surgical writes that the pending-form grammar needs; a tag does
not). Consumed on apply; no registry — the file and the evidence rows
are the record.

Not an explain: `[Explain:]` asks for a passage that does not yet exist
and lands it at the tag; `[Suggestion:]` asks for a judgment on prose
that does, and lands a replacement of it — or a reasoned "no". Not a
filter (one authored instruction at one anchor, no ratified prompt), not
a beat.

## 1. The tag

```
…and so the armour holds.[Suggestion: simplify this sentence]
[Suggestion: add an example here]
```

- Grammar identical to explain: `[Suggestion:` then the gist, then `]`.
  Keyword case-insensitive; no nesting; never across lines; an empty
  gist is not a tag. No `| label:`.
- **Target (the choice this design makes).** Inline, the suggestion
  applies to the paragraph that holds it. Alone on a line, it applies to
  the paragraph BEFORE it — the author reads a paragraph, then writes
  the note under it, as a margin comment follows what it comments on.
  (Explain's standalone tag is a paragraph of its own because it asks
  for new prose; a suggestion has nothing of its own to be.) A standalone
  tag with nothing before it is refused on apply. Two open tags on one
  paragraph are landed one at a time.

## 2. Collect reports, chat evaluates, apply lands

Exactly the explain road: collect, `get_status` and `doc pull` report
the open tags (`directives` rows, `kind = suggestion`); `suggestion list
[file]` / `list_suggestion_tags` on demand, with `in_doc` for forms out
in a tab. No LLM runs inside the tool.

**Drafting is chat.** The collaborator reads the gist against the target
paragraph, the style law and the concept notes it touches, and either
drafts the WHOLE revised paragraph, or concludes the suggestion should
not be taken and says why. The author rules in chat. Then one
`apply_suggestion` per file carrying every ruling as `items`:

- `{tag, text}` — `text` is the revised paragraph; it REPLACES the
  target paragraph, tag and all.
- `{tag, decline: true, reason}` — evaluated and not taken: the tag
  goes, the paragraph stays as it was, the reason is the evidence.
  (Explain has no decline on apply because a declined explanation simply
  stays open; a suggestion is a thing to evaluate, and "no" is one of
  its two answers.) CLI: `suggestion apply <file> --tag N --text …`,
  `--decline N='reason'`, or `--from-json` with both entry shapes.

No companion footnote: a suggestion lands a paragraph, and a footnote
the revision needs is written into that paragraph's text or requested
with its own `[Footnote:]` tag.

## 3. Two roads, chosen by the checkout (as explain, ratified 2026-09-03)

- **Not checked out — direct apply.** The target paragraph is replaced
  (standalone: the tag line goes with it); a decline removes the tag by
  the export-strip rule (inline: the sentence's spacing stays; standalone:
  the line goes). Evidence row per item: `doc_threads`, `origin_type =
  'suggestion'`, the TARGET PARAGRAPH as `proposed_old`, the landed
  paragraph as `proposed_new`, the gist as `note`, state `applied`; a
  decline is state `declined`, `proposed_new` empty, the reason in the
  metadata. Collected as its own version under **no episode** (source
  `suggestion-apply`). Chat was the review.
- **Checked out — the form in the tab, local pristine.** One form per
  item, written by `gdocs.write_pending_forms` as for explain:
  - inline text: `<<paragraph with its tag>>{{revised paragraph}}`;
  - standalone text: `<<paragraph before the tag>>{{revised paragraph}}`,
    the tag line left standing beneath it — the form must be one
    paragraph, and the tag is consumed by `resolve` when the form is
    accepted;
  - inline decline: `<<paragraph with its tag>>{{the same paragraph
    without it}}` — the author sees the tag going and the prose staying;
  - standalone decline: `<<[Suggestion: …]>>{{}}` — an empty green half;
    left alone, the line goes.

  `suggestion resolve <file>` / `resolve_suggestions` reads the tab
  back; the author's edits to a green half win; a deleted form is a
  decline (the paragraph and its tag stay, the tag open); an accepted
  standalone form consumes its tag line; evidence `suggestion_edit`;
  collect under no episode; push clean. One producer's forms per tab:
  a second apply is refused while forms are out, and `doc push` names
  `suggestion resolve` (`_SETTLE_REMEDY`).

## 4. Shared machinery

`authorlm/directives.py` serves the three kinds through the one `kind`
parameter: one grammar table, one scan (now carrying the target
paragraph for every tag), one `--tag` resolver, one apply, one staging
routine, one resolve, one evidence shape, one export strip. What the
suggestion kind adds: `_land_suggestion` (a paragraph replacement
instead of a tag replacement), the decline item, the standalone-decline
settle and the tag consumption on resolve. `morph` treats a standalone
tag line as non-prose, as it does the others.

## 5. Exports

An unresolved tag never reaches a reader: every export strips it and
warns, naming the file and the gist (footnote design §6).

## 6. Not built

- A decline recorded without touching the file (a "no" that leaves the
  tag open for the author to see). The tag IS the open question; the
  author's answer in chat closes it, and the reason is the record.
- A suggestion on a span smaller than a paragraph. The paragraph is the
  unit every road shares (`_paragraphs`, the tab's paragraphs, the
  pending form); the gist names the sentence when it needs to.
- Producers planting `[Suggestion:]` tags (footnote design §12). A
  suggestion is the author's evaluation request, by the ruling above.

## 7. Tests

- `tests/test_api.py::check_suggestions` — grammar and the target
  paragraph; collect and status report; direct apply as a paragraph
  replacement (inline and standalone); decline on apply; evidence rows;
  refusals (empty reason, text with a decline, a footnote companion, two
  tags on one paragraph, a standalone tag with nothing before it, a
  decline on an explain); the CLI's `--decline` and `--from-json`;
  `suggestion list`; the export strip.
- `tests/test_passes.py::_suggestions_on_the_doc_road` — the checked-out
  road against the surgical Doc fake: the four form shapes, local
  pristine, the push and second-apply refusals naming the resolve verb,
  `in_doc`, and a resolve with an edited green half, a deleted form, two
  accepted declines and an accepted standalone whose tag is consumed.
