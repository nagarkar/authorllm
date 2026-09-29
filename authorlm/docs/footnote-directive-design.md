# Footnote Directive — Design Reference

Grilled and ratified 2026-09-02. Build not started. This is the reference
for the `[Footnote: …]` directive: a shorthand instruction written in the
prose, drafted into a real `[^label]` footnote by the chat collaborator,
landed by the CLI.

Related designs: `illustration-placement-design.md` (the tag-as-spec
pattern this mirrors), `filter-pass-design.md` §8 (the two landing
transports and the direct-apply default this reuses),
`margin-threads-design.md` (the `<<old>>{{new}}` grammar),
`math-and-physics-guidelines.md` §5 (the sibling `[Omit:]`/`[Only:]` tags).

---

## 0. What it is, and what it is not

A `[Footnote: …]` tag is a **request for a footnote**, written where the
footnote's superscript belongs, in the author's shorthand. It is plain
text, so it survives Obsidian, the Doc tabs, and every pull and push
unchanged, exactly like `[Illustration: …]`. Unlike an illustration
slot, the tag is **consumed**: resolving it replaces the tag with
`[^label]` and appends the definition. There is no registry of past
footnotes; the file's own `[^X]:` block is the record.

**Not a filter** — a filter reads an essay unit by unit under a ratified
prompt; a footnote tag is one authored instruction at one anchor.
**Not a beat** — nothing is appended under a plan. **Not machine
prose** — no registered prompt, no LLM call path in the CLI. The chat
collaborator drafts, the author disposes in chat, the CLI is the state
machine and the evidence channel, the same division of labor as beats
and margin proposals.

## 1. The tag

```
…the vital lie.[Footnote: cite Becker ch. 2 on the vital lie]
…in 1916.[Footnote: Jung's Seven Sermons date | label: RD]
```

- Inline, at the anchor point. The superscript lands exactly where the
  tag was: `lie.[Footnote: …]` becomes `lie.[^F20]`. A footnote belongs
  to a sentence, not to a paragraph, which is why this differs from the
  full-line illustration tag.
- Grammar: `[Footnote:` then the gist, then optional `| label: XX`
  naming the letter series, then `]`. The keyword is case-insensitive:
  `[footnote: …]`, `[Footnote: …]`, and `[FOOTNOTE: …]` are one tag, as
  are the spellings of `[Illustration:`. No nesting. A tag never spans
  lines.
- The gist is shorthand: what the footnote should say or do, not the
  finished text. It may name a source; it need not.
- The tag is inert everywhere until resolved. Exports never show it
  (§6). The normalizer leaves it alone; Google's exporter escapes the
  bracket and the existing strip removes the backslash, so the round
  trip is clean.

## 2. Collect reports, nothing drafts

Collect (the end of a pull and of a local save alike) scans content
files for open tags and reports them per file, beside the illustration
slot report. `get_status` carries the same list. No LLM runs at collect
and nothing is written into a Doc unasked. One instruction from the
author — "resolve the footnotes in becker.md" — is one pass end to end,
per the Doc-road protocol.

## 3. Drafting, in chat

The collaborator assembles, with the tools it already has:

- the effective style law for the file (`get_style`): citation, lexicon,
  formatting, and the two footnote laws below;
- the ratified notes of concepts named in the anchor paragraph
  (`get_concepts` scoped to the file, then by name);
- the file's existing footnotes, verbatim, as voice exemplars — what
  keeps `[^F20]` sounding like `[^F1]` to `[^F19]`;
- the essay, with the tag marked.

Then it drafts one definition per tag and shows them in chat. The
author reacts in chat; the collaborator builds on the criticism and
redrafts. No separate LLM call, no revise verb. The author's reasons are
recorded verbatim as evidence when they state a principle
(`review_suggestion`), never by force of habit.

**Citations.** The draft may name a well-known source, edition, or
section from the model's own knowledge, and must mark each such claim
*unverified* in chat. The manuscript receives clean text only after the
author has ruled; the flag lives in the conversation, never in the file.
A citation the author supplied in the tag or that already appears in the
manuscript needs no flag.

**Decline.** A declined draft leaves the tag untouched and open; collect
keeps reporting it. Nothing is deleted on a "no".

## 4. Labels

- A file with footnotes continues its series: its letter, next unused
  number. `god.md` (`F1`–`F19`) mints `F20`.
- A file with none takes the capitalized first letter of its stem.
- `| label: XX` in the tag overrides the letter for that footnote and,
  by the rule above, for the file's future footnotes.
- Labels never render. Uniqueness matters within one file only; the
  export already namespaces labels across files.

## 5. Landing — two transports, one default

Mirrors `filter resolve` (filter-pass design §8, D-2): the same verb,
direct apply by default, a pause for reading in place.

```
footnote apply <file> --tag <n> --text "<definition>"          apply now
footnote apply <file> --tag <n> --text "<definition>" --pause  stage, stop
footnote resolve <file>                                        finalize staged
```

- **Apply** replaces the tag with `[^label]` at its exact position,
  appends `[^label]: <definition>` after the file's last definition
  (blank-line separated, the observed convention: definitions cluster at
  the end, in series order), records a `doc_threads` row with
  `origin_type='footnote'` as evidence, and — if the file is checked out
  to the Doc — pushes the tab. Always push after (Doc-road protocol).
- **Pause** stages the pending grammar instead: `<<[Footnote: …]>>{{[^F20]}}`
  inline and `{{[^F20]: <definition>}}` at the end. In doc mode the
  markers go into the tab; in local mode into the file, for reading in
  Obsidian. The canonical text stays the old half until resolve.
- **Resolve** reads the marked text from the run's transport (tab or
  file), honours any edit the author made to a `{{…}}` half, and lands
  it as apply would. Same composition routine, same rollback.
- `--tag <n>` is the tag's ordinal in the file (first open tag is 1) or
  an unambiguous excerpt of its gist. Several `--tag/--text` pairs may
  ride on one command.

## 6. Exports

An unresolved tag never reaches a reader. Every output strips it and
warns, naming the file and the gist. This deliberately differs from
illustration slots, which are kept as production notes: a shorthand
instruction inside a sentence reads as damage, and a reviewer cannot act
on it.

## 7. Style laws (to ratify at build, SMSTTD house style)

- (structure) A footnote carries what the sentence cannot: a source, a
  term's etymology, a qualification that would break the paragraph's
  stride. It never continues the argument.
- (citation) A footnote drafted from a `[Footnote:]` tag cites only
  what the tag, the manuscript, or a source the author has verified
  supplies; a source proposed from general knowledge is flagged
  unverified in chat and never lands unflagged-but-unchecked.

## 8. Verbs, tools, and the SKILL

- CLI: `footnote list [file]`, `footnote apply`, `footnote resolve`.
- MCP, CLI/MCP parity: `list_footnote_tags`, `apply_footnote`,
  `resolve_footnotes`; the tag report joins `get_status`.
- `.claude/skills/authorlm/SKILL.md` gains "resolve the footnotes in
  <file>": assemble (§3), draft in chat, wait for the ruling, then one
  `apply_footnote` per agreed footnote, then confirm the push.

## 9. Example rows

1. Author, in the Doc tab of `becker.md`: `…what Becker called the vital
   lie.[Footnote: cite Becker, ch. 2]`. Pull. Collect: "becker.md: 1
   open footnote tag".
2. Author, in chat: "resolve the footnotes in becker.md". Collaborator
   drafts: *Ernest Becker, The Denial of Death (1973), ch. 2, "The Terror
   of Death"; the phrase names the character armour by which a person
   keeps the fact of death out of view.* Flags: chapter title unverified.
3. Author: "drop the paraphrase, the essay already says that; keep the
   citation and add the Free Press edition." Collaborator redrafts in
   chat. Author: "apply."
4. `apply_footnote(becker.md, tag 1, text)`. File: `lie.[^B1]` and, at
   the end, `[^B1]: Ernest Becker, *The Denial of Death* (New York: Free
   Press, 1973), ch. 2.` Tab pushed. Evidence row written.
5. Same tag, `--pause` instead: the tab shows `<<[Footnote: cite Becker,
   ch. 2]>>{{[^B1]}}` inline and `{{[^B1]: …}}` at the end; the author
   fixes a comma in the green half and replies "apply";
   `footnote resolve becker.md` lands the corrected text.

## 10. Tests

- Tag grammar: inline match, label override, case, no match on
  `[Illustration:` or on a tag split across lines.
- Scan: counts per file; collect and `get_status` carry them.
- Apply: exact anchor replacement mid-sentence; series continuation
  (`F19` → `F20`); new-file letter; override letter; definition appended
  after the last existing definition with the blank-line convention;
  evidence row; push when checked out (stub service).
- Pause and resolve: markers in local mode and in doc mode (stub tab);
  an author edit to the `{{…}}` half survives resolve; canonical text is
  the old half while pending.
- Exports: every output strips the tag and warns.
- Round trip: a file with a tag pushes and pulls byte-clean.

## 11. Deliberately not done

- No registered prompt, no CLI LLM path, no revise verb: drafting and
  revision are chat.
- No drafting at collect, cached or otherwise.
- No triage-app queue: one candidate per tag has nothing to compare.
- No renumbering of existing footnotes; series only ever extend.
- No full-line tag form; a footnote has one anchor.
