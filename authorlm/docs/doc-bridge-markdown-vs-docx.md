# The Doc bridge: markdown or DOCX on the wire?

Assessment written 2026-09-02, after equations entered the manuscript.
The question: should the Google Doc bridge move from markdown to DOCX to
carry rendered equations, and what would it cost? Numbers below were
measured against the live Drive and Docs APIs with a physics sample.

## Short answer

Keep markdown for the reconciled tabs. Switch only the push-only export
Doc to DOCX. Nothing is lost, because the tabs' ceiling is not the wire
format at all: it is the transplant step, which the Docs API forces on
us, and no wire format gets an equation through it.

## How the bridge works today

There are two different Docs, built two different ways.

1. **The master Doc with one tab per file.** Push: the file's markdown is
   uploaded to a temporary Doc (Drive converts it), the temp Doc is read
   back as JSON, `transplant_requests` rebuilds its paragraphs inside the
   target tab with `batchUpdate`, and the temp Doc is deleted. Pull: the
   whole master is exported as markdown, split at tab titles, normalized,
   and written to the files. Reconcile compares hashes of the normalized
   text. This Doc is where the author edits, comments, and answers
   margin threads and critique forms.
2. **The export Doc.** The combined book is uploaded as markdown,
   replacing the Doc's whole body. It is never pulled, never reconciled.

Both roads use both Google APIs, and each API has a fixed role:

| | Drive API (`files.create/update/export`) | Docs API (`documents.get/batchUpdate`) |
|---|---|---|
| Unit of work | a whole file, converted on the way in or out | a range inside a tab |
| Conversion | Google's own: markdown, DOCX, HTML, PDF | none; JSON in, requests out |
| Can address a tab | no | yes |
| Can insert | anything the source format carries | text, styles, bullets, footnotes, images from a public URL, tables |
| Cannot insert | into an existing Doc without replacing it | equations (no request exists) |
| Measured cost | markdown import 2.7 s, DOCX import 2.7 s, export 0.7 s | one batch per tab |

The tab road therefore has a fixed shape: Drive converts, Docs
transplants. Whatever the converter produces, only what the transplant
carries reaches the tab. Today that is text runs, bold, italic, underline, links, headings, bullets, and (since 2026-09-03, it-08b8b0a0c737) tables, rebuilt with `insertTable` and filled cell by cell. Equations, footnotes, and images are dropped at that step whether the source was markdown or
DOCX.

## What DOCX would change, step by step

**Import into the temp Doc.** DOCX carries equations (as native Docs
equations, verified: five `equation` elements from the sample), real
footnotes, embedded images, and tables. Markdown carries none of these
and consumes backslash escapes, which is why `escape_footnotes` exists.
Time is the same.

**Transplant into the tab.** Unchanged, and this is the ceiling. The
Docs API has no request that creates an equation, so a DOCX-born temp
Doc's equations cannot reach the tab. Footnotes and images could in
principle (there are `createFootnote` and `insertInlineImage` requests),
but that is transplant work, independent of the wire format.

**Export on pull.** Two choices. Google's markdown export, which the
normalizer is tuned to, or a DOCX export run through pandoc's DOCX reader.
Measured: markdown export 0.65 s; DOCX export 0.71 s plus 0.45 s of
pandoc. Fidelity is the real difference. Pandoc's markdown of a
Docs-exported DOCX rewrote the sample: `\vec` became `\overrightarrow`,
`\hbar` became `\hslash`, `\hat` became `\widehat`, display math lost its
`$$`, and `aligned` collapsed. Prose fares no better: pandoc chooses its
own emphasis markers, escapes, heading attributes, and footnote
numbering. The current normalizer is the residue of every round-trip
incident recorded in `gdocs.py`; a pandoc-based pull would start that
list again from zero, and the whole-master export split at tab titles
would need re-deriving for DOCX structure.

**Hashes, comments, and markers.** Every reconciliation feature assumes
the tab is plain text equal to the file after normalization: pushed
hashes for conflict detection, comment anchoring by quoted text, margin
threads as `<<old>>{{new}}` markers, critique and filter forms,
illustration tags, region tags. A rendered equation in a tab is not
text. A comment on it has no quote to locate, a marker cannot wrap it,
and a filter cannot propose a replacement for it. Rendered math in the
reconciled tabs is structurally at odds with the margin machinery, not
merely unsupported.

## Risks of the recommended move, with mitigations

Only the export Doc changes: build the DOCX with pandoc, upload it with
Drive conversion, whole-body replace as now.

- **A pandoc dependency inside a Doc command.** Already required for
  every publishing export; the export Doc becomes one more publishing
  output. Mitigation: refuse with the same "brew install pandoc" message.
- **Conversion fidelity.** Drive maps Word heading styles to Docs named
  styles cleanly. Fonts default to the Docs theme, which is fine for a
  review copy. Pandoc's `title` metadata is already omitted on the DOCX
  road to avoid a duplicated title page. Mitigation: build from
  `publish_markdown` with the `images` variant so the export Doc gains
  embedded illustrations, which the markdown road cannot carry at all,
  and real footnotes instead of literal `[^x]` labels.
- **Size.** The book is under half a megabyte of text; illustrations
  add a few megabytes of PNG. Drive's conversion limit is far above
  that. Mitigation: none needed; the export reports the file size.
- **Reviewer comments.** The export Doc is push-only today and stays so;
  comments there were never pulled. No change.
- **Equations edited in the export Doc.** Not pulled, so harmless. In
  the tabs the rule stands: math is edited in Obsidian. Google's own
  export spells TeX its own way (`{c}^{2}`, `{\varepsilon }_{0}`), so an
  equation typed into a Doc would churn the file on every pull.

## What is not lost, and what remains possible

- **Tabs stay markdown.** With the math-aware normalizer and the math
  escape twin, TeX survives the round trip byte for byte, and every
  reconciliation feature keeps working on equations as text.
- **Rendered math in tabs later, if wanted.** The additive path is an
  image beside the TeX: render each display equation to PNG, host it on
  Drive with link sharing, insert it with `insertInlineImage` after the
  equation's text paragraph. Pull already strips Doc images, so the text
  round trip is untouched. This is transplant work on the existing
  markdown road; DOCX would not shorten it.
- **Real footnotes and images in tabs later.** Same story: `createFootnote`
  and `insertInlineImage` requests in the transplant. Wire format is
  irrelevant.
- **A one-Doc-per-file architecture.** DOCX would carry everything into
  such Docs by whole-body replace. It would give up the single master
  Doc with tabs, and the pull-fidelity problem above would remain. Not
  recommended, but not foreclosed by anything decided here.

## Work list — done 2026-09-02

1. Export Doc from DOCX: `export_manuscript` builds through
   `publish_markdown(fmt="doc")` and `build_docx`, uploads with the DOCX
   MIME type, same Doc id and title.
2. Normalizer: math spans lifted out of the prose rules; `=` added to
   the stripped Docs-export escapes; display math on one line.
3. `escape_math` beside `escape_footnotes` on the two importer sites;
   `unescape_export_math` on the four export sites.
4. Hermetic round-trip test modelling the measured importer and
   exporter (tests/test_api.py, "math round-trips the Doc road").
