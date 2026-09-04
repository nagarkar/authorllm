# Audiobook publishing pipeline — design

Status: DRAFT for grilling, 2026-09-04. Nothing here is built. Written
from a read-only scan of four repositories: this one (AuthorLM at
e21a4ac), `nagarkar/EditorLLM` (GitHub main at e841745, 2026-04-25),
`nagarkar/podlm` (5b01cb8), and the two Astro sites, which turned out to
carry no audio code and are not discussed further.

Scope the author set: AuthorLM writes and settles the text; the TTS studio
built on EditorLLM's audio layer performs it; together they should produce
an audiobook the author can submit. The studio is "well done, has a good
UI, but is designed for one essay at a time" and takes "an exported JSON
file that contains text and annotations about which voices to apply and
parameters to pass to the TTS API", inserting pauses between headings on
import. The pronunciation table already exists on the AuthorLM side and
should reach the TTS API.

## 0. The shape in one paragraph

Three tools, one seam each, no tool doing another's job. **AuthorLM owns
the text and the law**: reading order, matter, the audio-clean variant of
every essay, the pronunciation dictionary, and the readiness gate. It gains
one export, `authorlm export audio`, which writes a versioned **audiobook
script** — JSON, one chapter per TOC entry, typed blocks (heading,
paragraph, quote, verse, scene break), optional speaker roles, the
dictionary rows, and the book's identity — plus one audio-clean markdown
file per chapter beside it. **The TTS studio owns the performance**: it
imports the script, maps roles to its cast table (EditorLLM's `Cast Role
Policy`), applies its pause policy from the block types instead of
guessing from headings, lets the author audition and override per
directive exactly as today, and emits one MP3 per chapter with a manifest.
**Mastering and packaging are media work and live where ffmpeg already
lives**: podlm gains an `audiobook` workflow that takes the studio's MP3s
and the script, masters each file to the retailer's spec, adds room tone,
credits and the retail sample, runs a QC report, and assembles the
submission kit. The pronunciation dictionary crosses the seam twice: as
rows inside the script (for the studio's table and preview), and as an
ElevenLabs pronunciation dictionary that AuthorLM pushes and versions, so
every synthesis request carries the author's rulings. Everything between
tools is a file with a schema; nothing is a live coupling.

## 1. What exists today

| Capability | Where | State |
|---|---|---|
| Reading order, hierarchy, front/main/back matter | `structure.py`, `toc.toml` | Built; every export uses it. |
| Audio-clean text | `export.py`: output name `audio`, `[Omit: audio]`/`[Only: audio]` regions, `strip_display_math`, illustration tags stripped under `variant = stripped` | Built, reachable only as `export md --variant stripped`; still leaks footnote labels and definitions (see §4.3). |
| Pronunciation dictionary | `pronunciations.md` sidecar (`Term \| Say it \| Note`, plain respelling with the stressed syllable in capitals, never IPA); rows enter only through `proposal accept`; Doc-mirrored as a tab | Built. Nothing reads it but the filter payloads. |
| Audio-friendly prose pass | `audio-friendly` filter (sequential, voice-note state, pronunciation prelude); `filter_runs` records each run per essay | Built. The dictionary is its output channel and its suppression list. |
| Publication identity | `manuscripts` row: author, copyright owner, ISBNs, trim, bleed | Built. No narrator, no audiobook ISBN. |
| Readiness gate | `sweep readiness` (pure auditor) | Built. Knows nothing about audio. |
| Google Docs master | one Doc per bridge, one tab per essay, the dictionary as a tab | Built. This is the same Doc shape EditorLLM binds to. |
| TTS directives | EditorLLM `TtsAgent`: W1 writes `TTS Instructions` with the `Cast Role Policy` table (cast key, role type, speaker signals, voice, voice id, model id, stability, similarity boost); W2 places `tts` directives (`match_text`, `tts_model`, `voice_id`, `stability`, `similarity_boost`) as bookmark + named range + JSON in DocumentProperties; `break` directives carry `timeMs` and become `<break time="Nms" />` | Built (GitHub copy). One tab per run. |
| Synthesis | `elevenLabsTextToSpeechFromDirectives`: segments the tab at each directive, one request per segment, request-id stitching per voice, byte-concatenates the MP3s, saves to Drive `EditorLLM/Audio` | Built. Output format `mp3_44100_128`; a ~5,000-character request cap; GAS's 6-minute execution ceiling bounds one run. |
| Pronunciation at synthesis | `ElevenLabsService`: lists the account's dictionaries, caches their rules, attaches ONE locator when the text contains a rule's string | Built. Reads dictionaries; never creates or updates one. |
| Packaging | `publisherBuildAcxPackage` copies MP3s into a dated Drive folder; `Publisher` agent drafts credits and three "hooks" with durations | Built. No mastering, no measurement, no credits audio. |
| Studio (JSON import, heading pauses, batch UI) | Not on GitHub. The 2026-08-05 research note (`docs/editorllm-research.md`) saw "break heuristics at headings/ellipses, manifest builder, export/import bundles" and a stubbed Tauri companion in the local checkout, with 99 uncommitted files | **Unverified.** Everything this document says about the studio is inferred from the GitHub add-on plus that note. |
| Loudness tooling | podlm `media/ffmpeg.py`: `probe`, `duration`, `loudness_stats` (loudnorm measurement), `linear_level_af` (constant gain + transient limiter, with the reasons loudnorm's linear mode is not trusted), atomic writes, fingerprint-driven reruns, `approve` gates, per-workflow stage graphs | Built for podcasts. Directly reusable. |

Two consequences fall out of the table before any design:

1. **The GitHub EditorLLM is five months behind the tool the author is
   describing.** The first act of this project is to push the local
   EditorLLM and the studio, so the contract below can be built against
   real code rather than a research note. Until then the studio-side
   changes in §5 are a specification, not a diff.
2. **AuthorLM and EditorLLM already meet in Google Docs.** AuthorLM pushes
   one master Doc with one tab per essay; EditorLLM binds to a Doc and
   sweeps one tab at a time. The tempting integration is to bind the
   add-on to AuthorLM's master Doc. It is rejected in §3.

## 2. Principles carried over

- **LLMs decide words; tools execute media** (podlm's first principle).
  No model touches audio. The script is text; the cast table is text; the
  directives are text; ffmpeg does the rest.
- **The export directory is the database** (podlm's third principle,
  AuthorLM's `_exports/` already). Every stage reads and writes files under
  `_exports/audiobook/`; re-running with unchanged inputs is a no-op; a
  changed essay re-synthesizes exactly that chapter.
- **Law lives in AuthorLM, never in the performance layer.** The dictionary,
  the protected terms, the audio variant of the prose, and the reading
  order are settled here and travel outward. The studio may override a
  voice for a passage; it never edits a sentence, and it never adds a
  pronunciation that does not come back through `proposal accept`.
- **Machine-owned state is JSON with a schema; human-edited state is
  markdown or TOML with comments.** The script and the manifest are JSON,
  validated on write and on read (podlm's `state/schemas/` discipline). The
  cast table stays a markdown table the author edits.

## 3. The seam: why a script file and not the shared Doc

Binding EditorLLM to AuthorLM's master Doc looks free: the tabs are already
there, the dictionary is already a tab. It fails on three counts.

- **AuthorLM's push rebuilds a tab wholesale.** `gdocs.py` deletes and
  rewrites the body, so every bookmark and named range — the entire
  directive store — dies on the next push (the research note flagged this
  as the anchor-survival problem; it is unsolved and is not worth solving
  for this use).
- **The Doc carries the print text, not the audio text.** Region tags,
  illustration tags, display math and footnotes are all resolved at export.
  The add-on would be reading prose the audio variant removes.
- **A Doc tab is one essay; a book is a sequence.** Chapter order, matter,
  credits and file naming all come from `toc.toml`, which the add-on cannot
  see.

So the seam is a file. AuthorLM exports; the studio imports. The Doc stays
what it is today: the author's editing surface for AuthorLM, and the
add-on's editing surface for whatever the studio still keeps in a Doc.

## 4. AuthorLM side: `authorlm export audio`

### 4.1 Output

```
_exports/audiobook/
  script.json              the audiobook script (schema below)
  chapters/
    01-title.md            audio-clean markdown, one per chapter
    02-preface.md
    …
  pronunciations.json      the dictionary rows, alone, for tools that want only that
```

`export audio` is a fourth action beside `md|docx|epub|pdf`, built on
`publish_markdown(fmt="audio", variant="stripped")` so that everything the
existing audio output already does (regions, math, illustrations,
placeholders named and omitted) holds without a second code path. Like
`--chapters`, a part-build is possible and writes into the same directory.

### 4.2 Chaptering

One chapter per TOC entry, in reading order, **descendants folded into
their parent** so that a preface filed under chapter 1 becomes part of
chapter 1's audio file, unless the entry declares `audio_split = true` in
`toc.toml`. Matter rides through: `front` chapters follow the opening
credits, `back` chapters precede the closing credits. `title.md` is not a
chapter; it feeds the opening credits (§4.5).

The retailer wants one file per chapter of the printed book and no file
over 120 minutes. At a narration rate of roughly 150 words a minute that
is about 18,000 words; the export warns per chapter above 15,000 and
refuses above 18,000 unless split. These numbers are in the export, not in
the studio, because the studio does not know what a chapter is.

### 4.3 Blocks: what the studio needs to know that markdown hides

The studio inserts pauses at headings today by guessing from the text. The
script says what each block IS, and the pause policy becomes a table the
author can tune:

| block | from | default pause after (ms) |
|---|---|---|
| `heading` (with `level`) | `#`…`######` | 1500 / 1000 / 800 |
| `paragraph` | prose | 600 |
| `quote` | `>` blocks | 800, and a role hint `quoted_source` |
| `verse` | consecutive short lines / a `poem` marker | 400 per line, 1000 after |
| `scene_break` | `---`, `***` | 2000 |
| `list_item` | `-`, `1.` | 500 |

The defaults are the studio's business and ride in `script.json`'s
`pause_policy` only so the two sides agree on the vocabulary; the studio's
own configuration wins.

What the audio variant must still learn to drop or convert, all in
`publish_markdown` under `"audio" in outputs`:

- **Footnotes.** Today `[^a-1]` references and their definitions survive
  into the stripped markdown. Audio default: drop both. Option
  `footnotes = "aside"`: read the note inline as its own `aside` block at
  the end of the paragraph, prefixed by nothing (the narrator's pause does
  the work). The style guide's footnote directive decides which; the
  export only offers the switch.
- **Inline math.** Stays, because the style law already keeps it to what a
  voice can say; the export warns on any `$…$` that survives so the author
  sees the list.
- **Emphasis and capitals.** Markdown emphasis is stripped to plain text;
  the `audio-friendly` filter is what makes sure nothing load-bearing was
  in the italics.
- **Links and bare URLs.** Link text kept, target dropped.
- **Tables.** None exist in the essays (the only table is the sidecar);
  refuse the export by name if one appears, rather than reading a table
  aloud.

### 4.4 Speaker roles (optional, the Sermons' dialogue)

*Seven More Sermons to the Dead* carries dialogue between the Dead and the
Herdsman. EditorLLM's TTS agent infers speakers from quotation marks and
attribution, conservatively; it will get the sermons wrong exactly where a
reader would not, because the register has no "he said". The author's
prose must not be marked up for a machine, so the mechanism is the one the
manuscript already uses for output-specific control:

```
[Voice: herdsman]
Harken, ye that are dead …
[/Voice]
```

A full-line region tag in the `[Omit:]`/`[Only:]` family — plain text on
every road, resolved by `resolve_regions`, stripped from every output,
nested like the others, and a fault if unclosed. The name is a **role**,
not a voice: `narrator`, `dead`, `herdsman`, `quoted_source`, whatever the
author declares. The script carries `role` per block; the studio's cast
table maps roles to voices. An essay with no tags is all `narrator`, which
is today's behaviour. The studio's own inference stays available as a
proposal the author accepts or rejects there; it never writes a tag back.

### 4.5 Identity, credits, and the retail sample

Two manuscript properties, set with `manuscript set`, never inferred:
`--narrator "…"` (a person or the name of the synthetic voice, as the author
wants it credited) and `--audiobook-isbn`. The export refuses without a
narrator, the way the book PDF refuses without a trim size.

Opening and closing credits are chapters the export writes from metadata:
"<Title>. Written by <author>. Narrated by <narrator>." and "This has been
<Title>, written by <author>, narrated by <narrator>. Copyright <year>
<owner>. Production copyright <year> <owner>." Both are ordinary chapters
in the script with `kind = "credits"`, so the studio reads them in the
narrator voice and the packaging stage names them as the retailer expects.

The retail sample is a 1–5 minute excerpt the retailer requires as its own
file. EditorLLM's Publisher already proposes three spoiler-free "hooks"
with durations; the export accepts `--sample <file>:<heading>` or a
`[Only: sample]` region, and the script marks the span. Nothing is chosen
for the author.

### 4.6 Pronunciations: rows in the script, and a dictionary at ElevenLabs

Rows travel inside `script.json` as they are: `term`, `say`, `note`. The
studio shows them, uses them for its preview text, and never edits them.

Separately, `authorlm pronunciations push` creates or updates **one
ElevenLabs pronunciation dictionary per manuscript** from the rows and
records `{dictionary_id, version_id, pushed_at, row_hash}` in the
manuscript metadata. The studio attaches that locator to every request
(EditorLLM's `getPronunciationDictionaryLocators_` already does this once
a dictionary is selected; the script names which one). Facts the push
must respect, to be verified once against the vendor's current docs before
building (the docs site is not reachable from this session):

- Two rule kinds exist. **Alias** rules substitute text and work on every
  model; **phoneme** rules (IPA or CMU) are honoured only by the English
  v2 flash/turbo family and the old monolingual model, not by
  `eleven_multilingual_v2`, which the add-on defaults to. The author's
  rows are respellings, so they are alias rules by construction, and the
  model choice does not break them.
- A respelling like `shoon-YAH-tah` is an alias the model will read as
  text. Capitals may be read as an initialism; hyphens are read as pauses
  by some models. The push therefore emits a **derived alias** (lowercase,
  hyphens kept, or joined — decision D3) and keeps the author's row
  untouched as law. If the author ever wants IPA, the `note` column can
  carry `ipa: …` and the push emits a phoneme rule for it; nothing in the
  table changes shape.
- Updating a dictionary yields a **new version id**; the script and the
  studio must carry the version, not just the id, or an accepted row
  silently fails to reach the voice.
- The service allows a small number of locators per request (three, at
  last reading); one per manuscript keeps the studio's existing
  single-locator code correct.
- Alias matching is substring-based on the vendor's side. A term that is a
  prefix of another word (`Dharma` inside `Dharmic`) will be rewritten
  inside the longer word. The push warns on any row whose term is a
  prefix of another dictionary term or of a protected term, and the
  author decides.

`pronunciations push` is the second writer the dictionary file's docstring
forbids — except that it never writes the file. It reads the file and
writes the vendor. The docstring's invariant, "the only caller that writes
at all is `proposals.adopt`", stands.

### 4.7 Readiness

`sweep readiness` gains an `audio` section, reported only when
`_exports/audiobook/` exists or `--audio` is passed:

- `narrator set`; `audiobook ISBN` (warning, not blocking — the retailer
  can assign one).
- `audio-friendly run per essay`: from `filter_runs`, every content file
  in the TOC has a settled run at its current version; names the ones
  that do not.
- `pronunciations settled`: no row with an empty `say`; no open
  `pronunciation` proposal.
- `dictionary pushed`: `row_hash` in metadata equals the file's; else
  "N rows changed since the last push".
- `[Omit: audio] regions resolve` and `no footnote leak` (the export's own
  checks, surfaced).
- `chapters within length`: per §4.2.
- `[Voice:] roles declared`: every role used has a row in the studio's
  cast table, if the studio has exported one back (`cast.json`, §5.3);
  otherwise "unchecked".

### 4.8 The script schema (sketch)

```json
{
  "schema": "authorlm-audiobook-script/1",
  "generated_at": "2026-09-04T18:00:00Z",
  "manuscript": {"name": "SMSTTD", "version": 142},
  "book": {"title": "…", "author": "…", "narrator": "…",
           "copyright_owner": "…", "copyright_year": 2026,
           "audiobook_isbn": null, "language": "en"},
  "pronunciations": {"dictionary_id": "…", "version_id": "…",
                     "rows": [{"term": "Śūnyatā", "say": "shoon-YAH-tah", "note": ""}]},
  "roles": ["narrator", "dead", "herdsman", "quoted_source"],
  "pause_policy": {"heading": [1500, 1000, 800], "paragraph": 600, "…": "…"},
  "chapters": [
    {"id": "opening-credits", "order": 0, "kind": "credits", "matter": "front",
     "title": "Opening credits", "file": null, "source_version": null,
     "blocks": [{"type": "paragraph", "role": "narrator", "text": "…"}]},
    {"id": "ch01-ascending", "order": 1, "kind": "chapter", "matter": "main",
     "title": "Ascending", "file": "chapters/03-ascending.md",
     "source_files": ["ascending.md", "preface.md"], "source_hash": "…",
     "word_count": 6120, "estimated_minutes": 41,
     "sample": false,
     "blocks": [
       {"type": "heading", "level": 1, "role": "narrator", "text": "Ascending"},
       {"type": "paragraph", "role": "narrator", "text": "…"},
       {"type": "paragraph", "role": "herdsman", "text": "…"},
       {"type": "scene_break"}
     ]}
  ]
}
```

`source_hash` per chapter is what lets every downstream stage skip
unchanged chapters (podlm's fingerprint rule, applied to text). The JSON
Schema file lives in `authorlm/publication/audiobook-script.schema.json`
and is validated on export; the studio and podlm validate the same file
on import, and the schema is the contract that survives any of the three
tools being rewritten.

## 5. Studio side: from one essay to a book

Specification, because the code is not in this session (§1). What the
GitHub add-on already has is named so the author can see what is new.

### 5.1 Import the script, not a tab

`Import audiobook script` reads `script.json`, validates it, and creates
one studio chapter per script chapter: the blocks become the text, the
pause policy places `break` directives (the heading heuristic becomes the
fallback for text that arrives without block types), and every block's
`role` becomes a `tts` directive at the block's start wherever the role
changes — using the cast table (§5.3), which is exactly what the W2 agent
produces today, without the model in the loop for the tagged spans. The
agent stays available as a proposal pass over untagged prose.

### 5.2 Generate the book, resumably

Per-tab generation today is one GAS execution, one Drive file. A book is
hours of audio and hundreds of requests, which no single execution can
finish. The batch driver is:

- one **job** over the script, checkpointed per chapter (EditorLLM's team
  runner already has the `lastProcessedIndex` + status `continuing`
  pattern; reuse it);
- one MP3 per chapter, **named from the script** (`03-ascending.mp3`),
  written to the studio's audio folder, plus `manifest.json` beside them:
  chapter id, `source_hash`, voice ids used, character count, request ids,
  duration, dictionary version — everything the packaging stage needs to
  decide whether a chapter is fresh;
- skip a chapter whose `source_hash` and cast are unchanged since the last
  manifest entry (this is the rerun economics: a changed sentence costs one
  chapter, not a book);
- request `mp3_44100_192` where the account tier allows it, else the
  128 kbps default and let mastering transcode (§6.2 says why this is
  second best).

The per-tab UI — jump to directive, preview one segment, edit the payload,
regenerate one tab — is the review surface and stays as it is. The batch
job is the thing that is new.

### 5.3 The cast table travels back

The `Cast Role Policy` table the W1 agent maintains is the mapping from
role to voice. Export it as `cast.json` beside the manifest, keyed by
cast key. AuthorLM's readiness sweep reads it only to confirm every
`[Voice:]` role has a row. Nothing else in AuthorLM ever knows a voice id.

### 5.4 The dictionary locator

The studio selects the manuscript's dictionary by the id in the script
rather than "the first cached one", and refuses to generate when the
script's `version_id` is newer than the cached dictionary's, with a
"refresh dictionaries" affordance — the existing refresh button.

## 6. Mastering and packaging: podlm's `audiobook` workflow

### 6.1 Why podlm and not AuthorLM or the studio

AuthorLM's core is standard library plus pandoc; media belongs elsewhere.
The add-on runs in GAS, which cannot run ffmpeg. podlm already has the
ffmpeg wrappers, the measurement that was validated by ear, fingerprinted
stage reruns, human gates, atomic artifacts, JSON-schema state, and a
per-workflow stage graph (`isolated`, `edited-master`). A third workflow,
`audiobook`, is the shape its README says workflows take. Its brand assets
are irrelevant to the workflow and are not touched.

If the studio turns out to be a desktop app with ffmpeg at hand, the same
stage list can live there instead; the stages, not the home, are the
design.

### 6.2 Stages

```
podlm import <book> --audiobook <path to _exports/audiobook> --audio <studio audio dir>
podlm up <book>
```

| stage | does | gate |
|---|---|---|
| `verify` | every script chapter has a manifest entry and a file; `source_hash` matches; durations sane; warns on chapters whose text changed after synthesis | — |
| `master` | per file: decode, constant-gain level match to the spec window using `linear_level_af` (never loudnorm's dynamic mode), true-peak limit at the ceiling, measure noise floor, **add room tone** (0.5–1 s head, 1–5 s tail; generated silence at −70 dB, not digital zero), split any file over the length cap at a `scene_break` or `heading` boundary from the script, encode **MP3 192 kbps CBR, 44.1 kHz**, mono or stereo consistently across the book | — |
| `credits` | opening/closing credits files from the script's `credits` chapters (already synthesized by the studio; this stage only masters and names them) | — |
| `sample` | cut the retail sample from the marked span, master it as its own file | `approve sample` |
| `qc` | `qc.json` + `qc.md`: per file RMS, peak, noise floor, duration, format; the book-level consistency checks; a red list | `approve qc` |
| `kit` | `publish/<title>/NN_<slug>.mp3` in the retailer's naming, a checklist (ISBN, cover 2400×2400 square — the print cover is not square and needs its own art), the QC report, a total runtime | — |

The retailer's numbers the QC stage asserts, from memory and to be
confirmed against the current submission page before the stage is built
(the page is not reachable from this session): RMS between −23 dB and
−18 dB; peak at or below −3 dB; noise floor at or below −60 dB RMS; MP3 at
192 kbps or higher, constant bit rate, 44.1 kHz; 0.5–1 s of room tone at
the head and 1–5 s at the tail; no file longer than 120 minutes; every
file the same channel count and level; opening credits, closing credits
and a 1–5 minute retail sample as separate files.

Two synthesis facts that make mastering non-optional: the add-on's byte
concatenation of MP3 segments produces a playable but irregular stream
(frame headers from several encodes, no consistent bit reservoir), which
some ingest tools reject, and ffmpeg's re-encode fixes; and the default
128 kbps output is under the bit-rate floor. Transcoding 128 → 192 kbps is
lossy-to-lossy and second best, which is why §5.2 asks the studio for
192 kbps at the source when the tier allows.

## 7. The loop, end to end

1. Write and settle in AuthorLM as today. Run `audio-friendly` per essay;
   accept or dismiss the pronunciation proposals; the dictionary grows.
2. `authorlm pronunciations push` — once per dictionary change.
3. `authorlm sweep readiness --audio` — until green.
4. `authorlm export audio` — `_exports/audiobook/` refreshed; unchanged
   chapters keep their `source_hash`.
5. Studio: import the script, review the cast table and directives, run
   the book job; only changed chapters synthesize.
6. `podlm up <book>` — master, credits, sample, QC, kit; approve the sample
   and the QC report by ear.
7. Submit the kit. A changed sentence later re-enters at step 4 and costs
   one chapter.

## 8. Build order

| stage | repo | size | unblocks |
|---|---|---|---|
| A. Push the local EditorLLM and the studio to GitHub | EditorLLM | an afternoon of git hygiene | everything in §5 becomes a diff |
| B. `export audio`, the schema, narrator/ISBN metadata, footnote handling, chapter length checks | AuthorLM | 2–3 days | the studio has a real input |
| C. `pronunciations push` + metadata + readiness `audio` section | AuthorLM | 1–2 days | the dictionary reaches the voice |
| D. Script import, block-driven pauses, role → cast directives, book job with checkpoints, manifest + cast export | studio | the largest piece; unknown until A | per-chapter MP3s |
| E. podlm `audiobook` workflow | podlm | 2–3 days, mostly reuse | a submittable kit |
| F. `[Voice:]` regions | AuthorLM | half a day, after D proves the role path | the Sermons' dialogue |

B, C and E do not depend on D and can proceed while the studio is being
pushed; each is testable with a hand-written `manifest.json`.

## 9. Decisions to grill

- **D1. Chapter folding.** Descendants fold into their parent by default
  (§4.2). The alternative — one file per TOC entry regardless of depth —
  makes SMSTTD's preface a two-minute file. Recommendation: fold.
- **D2. Footnotes in audio.** Drop, or read as asides. Recommendation:
  drop by default; the footnote directive already says what a footnote is
  for in this book, and it is not the ear.
- **D3. Alias derivation.** Lowercase with hyphens kept, lowercase joined,
  or the row verbatim. Needs one listening test per model on three rows.
  Recommendation: decide by ear, record the rule in the push's docstring.
- **D4. Where roles are declared.** `[Voice:]` regions in the prose
  (§4.4) versus a per-essay sidecar listing paragraph ranges. Regions
  survive Doc round trips and rewrites; ranges do not. Recommendation:
  regions.
- **D5. The studio's home.** If it is a desktop app, §6 may live there
  and podlm stays a podcast tool. Cannot be decided until A.
- **D6. Narrator credit.** A synthetic voice credited by its product name,
  the author's name as narrator, or a house name. A retailer question as
  much as an editorial one; the field is free text so the answer can
  change.
- **D7. Sample selection.** Author-marked span versus the Publisher
  agent's hooks as proposals. Recommendation: both — hooks as proposals in
  the ordinary queue, the accepted one becoming the marked span.

## 10. What this design refuses

- No voice ids, model ids or vendor parameters in the manuscript or in
  AuthorLM's database, beyond the dictionary locator. The manuscript is
  the book; the performance is a rendering of it.
- No binding of the add-on to AuthorLM's master Doc (§3).
- No model in the mastering chain. Levels, splits and room tone are
  arithmetic.
- No new prose markup beyond one more full-line region tag, and that one
  only if the Sermons' dialogue proves to need it.
- No second dictionary. One `pronunciations.md`, one vendor dictionary per
  manuscript, one version id carried everywhere.
