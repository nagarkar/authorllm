# Explain Directive — Design Reference

Built 2026-09-03 on the footnote directive's road, by the author's
rulings of that day: an earlier cut drafted these through the CLI's own
model path in batches at collect; the author preferred chat drafting for
both directives, and that cut was removed the same day. A second ruling
the same day standardized the landing: the `<<old>>{{new}}` form in the
Doc tab whenever the file is checked out, local pristine; direct apply
when it is not. This note records only what differs from
`footnote-directive-design.md`, which governs everything it does not
mention.

---

## 0. What it is

A `[Explain: …]` tag is a **request for an explanatory passage**,
written where the passage belongs, in the author's shorthand. Plain
text; consumed on apply; no registry — the file and the evidence rows
are the record. Not a footnote (body prose, not apparatus), not a
filter, not a beat.

## 1. The tag

```
…the armour holds.[Explain: what a vital lie is] Nothing else.
[Explain: why the armour must fail]
```

- Inline at the anchor, or alone on a line. Inline, the passage
  continues the sentence's paragraph; alone, it is a paragraph of its
  own (rarely two, blank-line separated).
- Grammar: `[Explain:` then the gist, then `]`. Keyword
  case-insensitive; no nesting; never across lines; an empty gist is not
  a tag. No `| label:` — nothing is labelled.

## 2. Collect reports, chat drafts, apply lands

Exactly the footnote road (§2, §3 there): collect and `get_status`
report the open tags (`directives` rows, `kind = explain`); the chat
collaborator assembles style law, concept notes, and the essay with the
tag marked, drafts one passage per tag; `explain apply <file> --tag <n>
--text "…"` / `apply_explain` lands it.

What an explanation is (the drafting rule the SKILL carries): the
author's prose at that position — register, person, tense, cadence of
the essay; it explains what the gist names and nothing more; it never
continues the argument, never adds a claim the essay does not make or
need, never comments on the essay.

**Companion footnote (ratified 2026-09-03).** The footnote structure
law (footnote design §7, house style) is applied to the gist while
drafting: a source, an etymology, or a qualification that would break
the paragraph's stride belongs in a footnote, not in the passage — and
the footnote supports the claim the passage makes at that point (the
anchor law, the author's words). When one
is warranted the collaborator drafts it beside the passage and passes
it as `footnote` in the same apply (`items: [{tag, text, footnote}]`;
CLI `--footnote TAG=TEXT`). The passage lands with `[^label]` at its
end and `[^label]: …` joins the file's block under the footnote
directive's label rules (§4 there: the series continues, an override
carries forward). One review, no second `[Footnote:]` round. A
footnote apply refuses a companion: its tag IS the footnote.

## 3. Two roads, chosen by the checkout (ratified 2026-09-03)

- **Not checked out — direct apply.** The passage replaces the tag at
  its exact offset (inline, a space is supplied on whichever side the
  prose needs one; standalone, the passage takes the tag's line).
  Evidence row: `doc_threads`, `origin_type = 'explain'`, the tag as
  `proposed_old`, the passage as `proposed_new`, the gist as `note`,
  state `applied`. Collected as its own version under **no episode**
  (source `explain-apply`). Chat was the review.
- **Checked out — the form in the tab, local pristine.** The Doc is the
  working copy and the tab is the review surface, as for every other
  machine proposal. Apply stages a thread per tag (state `proposed` →
  `written`) and writes `<<[Explain: …]>>{{passage}}` at the anchor
  with the same spacing the direct road would supply, so the two roads
  land byte-identical prose. Nothing is written locally. The author
  edits the green half or deletes the form; `explain resolve <file>` /
  `resolve_explains` reads the tab back (`tab_marked_markdown`), lands
  the final text with the author's edits winning
  (`final_text_from_marked`), records the verdicts as `explain_edit`
  evidence (`record_resolution`: reworded → revised, intact → cleaned,
  form deleted → declined and the tag stays open), collects under no
  episode, and pushes the tab clean — a resolve is not finished until
  the Doc has the result.
- One producer's forms per tab: while forms are out, a second apply of
  either kind on that file is refused, and `doc push` refuses naming
  `explain resolve` (`_SETTLE_REMEDY`).

## 4. Shared machinery

`authorlm/directives.py` serves both kinds: one grammar module, one
scan and report, one `--tag` resolver (ordinal or gist excerpt), one
apply with one write per command, one staging routine over the
existing form writer (`gdocs.write_pending_forms`), one resolve over
the existing read-back, one evidence row shape, one export strip. The
kinds differ in `_land_footnote` versus `_land_explain` and in the
footnote's extra `{{[^label]: …}}` insertion thread, anchored after the
file's last paragraph and handed to the writer in reverse so the tab
reads the definitions in series order.

## 5. Not built

- A local pause (forms written into the file for Obsidian, the filter's
  local road). The author's ruling: the local copy stays pristine; when
  the file is local, chat is the review and the text lands.
- (Resolved 2026-09-03: the footnote design's §7 structure rule and the
  author's anchor rule are ratified house law; its citation rule was
  rejected.)
