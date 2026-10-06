# Audiobook Pipeline — Design Reference

Grilled and ratified 2026-09-03. Built 2026-09-04 (all four slices of
§13; see §18 for what was verified and what awaits a live key). This is
the reference for
turning an AuthorLM manuscript into an ACX-ready audiobook: AuthorLM is
the sole producer of the audio manifest; **audiostation** (the Tauri
desktop app formerly `EditorLLM/desktop`) is the audio studio that
generates, stitches, audits and packages; ElevenLabs is the voice. The
EditorLLM Google Apps Script add-on drops out of the path entirely.

Related designs: `math-and-physics-guidelines.md` §5 (the `[Omit:]` /
`[Only:]` regions and the `audio` output this builds on),
`filter-pass-design.md` (the filter class the casting pass joins),
`autoregressive-writing-design.md` §15.22 (`pronunciations.md` and the
pronunciation prelude), `illustration-placement-design.md` (the
full-line tag grammar `[Voice:]` copies), `editorllm-research.md`
(the 2026-08-05 survey of what EditorLLM had), `multi-tenancy-design.md`
(`_audio/` is per manuscript like every other underscore directory).

External facts verified 2026-09-03 against the ElevenLabs documentation
are marked **[verified]**; everything else is a ruling.

---

## 0. What it is, and what it is not

An audiobook is **another export of the manuscript**, built from the same
files, the same reading order, the same region tags and the same
pronunciation table as the print and EPUB editions — plus one thing the
page never needed: *who is speaking, in which voice*. That one thing is
the whole of the new authored surface (§5). Everything else is a
compilation step (§4, §6) and a studio (§10).

**Not the EditorLLM add-on.** Its Gemini TTS agent anchored voice
directives as named ranges inside a Google Doc; AuthorLM's push rebuilds
tabs and destroys every anchor, so the two cannot share a Doc. The author
does not use the add-on. Nothing in it is kept or made compatible.

**Not a generator.** AuthorLM never generates chapter audio. The only
speech it ever requests from ElevenLabs is an audition clip (§11). The
credits are spent from audiostation, one section at a time, by the author.

**Not a play.** Voice switches are per paragraph and rare (the Sermons need
about a dozen tags; every essay needs none). A manuscript that alternates
speakers every line would need a different mechanism; it is not designed
(§16).

## 1. Principles (ratified 2026-09-03)

1. **AuthorLM produces; audiostation consumes and generates.** One
   program owns the text, casting and structure; the other owns audio
   files and their bookkeeping.
2. **Two writers, two kinds of file, never the same file.** AuthorLM
   writes `audiobook.json` and `chapters/*.json`; audiostation writes
   `state/*.json` and the audio. The shared key is the section id (§6).
3. **The paragraph is the unit of audio.** A speaker change inside a
   paragraph is resolved by splitting the paragraph in the prose, never by
   an inline marker.
4. **Casting is three layers: essay default, cast rows, exceptions.**
   Only the exceptions are visible in the Doc, and they are rare.
5. **Nothing about a passage lives in `cast.md`; nothing about a voice
   lives in the prose.** The tag carries a key. The row carries the
   voice.
6. **One setting per voice per book.** Stability and similarity are cast
   decisions, settled once by audition. Drama comes from the prose;
   speed is the only per-passage knob, and rarely. (ElevenLabs' own
   guidance; parameter changes between adjacent sections are audible
   seams.)
7. **TOML for what a person edits, JSON for what two programs exchange.**
   No YAML: two new dependencies for a format with implicit typing.
8. **Every text rule is configuration, not code.** `audiobook.toml` holds
   the rules; the export resolves them; audiostation never reads them —
   their effect is already in the sections and silences.
9. **Regeneration is exact.** A change to one paragraph, one voice row,
   or one pronunciation regenerates exactly the sections it touches and
   no others. Credits are the scarce resource this design protects.
10. **Files AuthorLM seeds are the author's.** `audiobook.toml` and
    `cast.md` are written once with defaults and comments; afterwards
    AuthorLM reads them and only `audio cast set` writes a row.
11. **No back-compat during prototyping.** The May 2026 test manifest is
    not migrated; the add-on's HTTP endpoint, clipboard import, v1/v2
    single-file loading, in-app text editing and text-matching merge are
    deleted from audiostation.

## 2. The folder and the owner split

```
manuscripts/<M>/_audio/
  audiobook.toml          author-owned configuration (§3); seeded once by `audio init`
  cast.md                 author-owned cast table (§5.2); rows written by `audio cast set`
  audiobook.json          AuthorLM writes: book metadata, chapter order, credits,
                          about-author, retail sample, cover, dictionary locator,
                          resolved cast snapshot, schema version
  chapters/<stem>.json    AuthorLM writes: one per toc file; ordered sections
  state/<stem>.json       audiostation writes: section id → audio files, request id,
                          generated-at; stitched output, duration, loudness
  audio/<id>.<format>.mp3 audiostation writes
  _orphaned/…             audiostation moves audio whose id no longer appears anywhere
```

`_audio/` is an underscore directory: invisible to collection, excluded
from every other export, per manuscript. `audiobook.toml` and `cast.md`
are the two files worth versioning; the author decides what else is.

An export rewrites `chapters/<stem>.json` **only if its content changed**
(byte-compare after serialization) so audiostation's watcher fires only
for real changes, and `audiobook.json` only when the book-level content
changed.

## 3. `audiobook.toml`

Seeded fully populated with a comment on every key. Keys and defaults:

```toml
schema = 1

[text]
voice = "narrator"           # required book-wide text role in cast.md
paragraph_gap_ms = 700        # silence after every paragraph section
rule_gap_ms = 1500            # a horizontal rule is silence, not speech
footnotes = "drop"            # drop | inline | end   (inline/end reserved, see §16)
inline_math = "warn"          # warn | strip | refuse  — warn strips delimiters and names the line
lists = "one-section"         # one-section | one-per-item
max_section_chars = 5000      # export warns above this; ElevenLabs per-request limits differ by model

[headings]
voice = "narrator"            # required heading role; explicit essay-default follows the file text role
gap_ms = { h1 = 3250, h2 = 2250, other = 1250 }   # silence BEFORE a heading; adjacent headings collapse to the smaller

[tts]
model = "eleven_multilingual_v2"   # decides the dictionary rule type (§7)
quality = "mp3_44100_128"          # ACX accepts 128 or 192
dictionary = "SMSTTD pronunciations"   # ElevenLabs dictionary NAME; looked up, never stored as an id

[credits]
voice = "narrator"
opening = "{title}. {subtitle}. Written by {author}. Narrated by {narrator}."
closing = "This has been {title}, written by {author} and narrated by {narrator}. Copyright {copyright_year} by {copyright_owner}. Production copyright {copyright_year} by {publisher}."

[retail_sample]               # ACX: one to five minutes from the body of the book
chapter = "sermons.md"
heading = "Sermon One"
paragraphs = 6                # the first N paragraph sections under that heading

[cover]
path = "_assets/audiobook-cover.jpg"   # square, ≥ 2400 px, RGB; the print cover is not square
```

Placeholders in `[credits]` resolve from manuscript metadata (§9). An
unknown key, wrong type, or invalid value is an export error naming the
field. The file itself and `text.voice`, `headings.voice`, `credits.voice`
are required; none is filled from a runtime default. A missing file names
`authorlm audio init`. Other settings retain their defaults. The seed is
packaged at `authorlm/audiobook-defaults.toml` and copied once by init.
Init creates only missing files, preserving an existing config or cast
byte-for-byte, and refuses when both already exist.

## 4. From markdown to speech text

The input is the existing stripped variant: `publish_markdown(variant=
"stripped", fmt="audio")`, so `[Omit: audio]`/`[Only: audio]` resolve,
display math drops, illustration tags strip, unresolved `[Footnote:]` and
`[Explain:]` tags strip and are named. Then, per file, per block:

| Source | Audio |
|---|---|
| Heading | Its own speech section in the voice `[headings].voice` names (essay default → §5.1). Silence before it by level. |
| Paragraph | One speech section, then `paragraph_gap_ms` of silence. |
| Horizontal rule | `rule_gap_ms` of silence, no speech. |
| Bold, italic, links | Plain text; link text kept, target dropped. |
| Footnote reference `[^x]` and definition `[^x]:` | Dropped (`footnotes = "drop"`). `[Only: audio]` is the tool when a note matters aloud. |
| Inline math `$…$` | Delimiters stripped, contents kept, one warning per span with file and line. Never refused unless configured. |
| Blockquote | Marker stripped, read plainly. |
| List | Markers stripped; the whole list is one section. |
| Ellipsis, em dash, semicolon | Untouched. ElevenLabs paces punctuation; with the paragraph as the unit there is no reason to split on an ellipsis as the add-on did. |
| `[Voice: …]` line | Consumed by casting (§5.3), never spoken. |
| `title.md` | Not a chapter; its text feeds the opening credits placeholders. |
| `about.md` (toc `audio = "about-author"`) | Not a chapter; routed to the about-author slot (§9). |
| Other front and back matter | Chapters in toc order, preface included. |

Every configured gap means "at least this much silence here", so
consecutive gaps collapse to the largest (a rule after a paragraph is the
rule's gap, not the sum), a silence is written only when speech follows
it, and a trailing silence is dropped. A file that yields no speech
section is omitted and named. Silence is a manifest section exactly as
audiostation already understands it; the stitcher is unchanged.

**Normalization for the hash and for speech** is AuthorLM's, in one
function: NFC, NBSP and thin spaces → space, whitespace runs collapsed,
zero-width characters dropped. Curly quotes and dashes are *kept* in the
spoken text (ElevenLabs reads them); they are folded only for the
pairing heuristic in §10.3.

## 5. Casting

### 5.1 Layer one — explicit roles in `_audio/audiobook.toml`

`[text].voice` is the book-wide text role. `[headings].voice` and
`[credits].voice` independently name their roles. All three are required;
`audio init` writes `"narrator"` explicitly for each. A heading voice of
`"essay-default"` is allowed only when explicitly configured and follows
the file's text role.

An optional exact-file text override lives in the same per-manuscript file:

```toml
[[chapter]]
file = "sermons.md"
voice = "herdsman"          # required key in _audio/cast.md
```

Repeat `[[chapter]]` for additional files. A parent chapter's override never
flows to its children; a file without an override uses `[text].voice`.
Unknown keys, wrong types, duplicate files, missing or invalid targets,
empty role keys, unknown cast roles, and roles without a Voice ID are
refused by name, including declarations unused by current speech.
`toc.toml` remains structural; any legacy `voice` there refuses export
with a hint to move it into `_audio/audiobook.toml`.

The export resolves all speech parameters before writing the existing JSON
schema. The review page, CLI generation, and audiostation consume those
resolved sections without selecting a fallback voice.

### 5.2 Layer two — `cast.md`

A manuscript-root-style sidecar living in `_audio/` (it is audio's, not
the book's): markdown, one table, never Doc-mirrored.

```markdown
# **Cast**

| Key | Voice | Voice ID | Model | Stability | Similarity | Speed | Note |
| --- | --- | --- | --- | --- | --- | --- | --- |
| narrator | Adam Stone | pNIn… | eleven_multilingual_v2 | 0.60 | 0.75 | 1.00 | Essays and all headings |
| herdsman | Adam Stone | pNIn… | eleven_multilingual_v2 | 0.65 | 0.80 | 0.97 | First-person voice of the Sermons |
| the_dead | Julian | 3MTv… | eleven_multilingual_v2 | 0.50 | 0.75 | 1.00 | Weary; many as one |
```

- `Key` is `[a-z][a-z0-9_-]*`, the identity; parsed with the same
  pipe-table discipline as `pronunciations.md` (no pipes, no newlines in
  a cell; a shifted row is skipped and named, never guessed).
- `Model` may be blank → `[tts].model`. Stability, Similarity, Speed may
  be blank → 0.5 / 0.75 / 1.0.
- Seeded by `audio init` with a `narrator` row whose Voice ID is empty;
  the export refuses a key with no Voice ID by name.
- Written by `audio cast set <key> …` and by the author. Nothing else.

### 5.3 Layer three — the `[Voice:]` tag

```
[Voice: the_dead]
“O Stranger, all this is new to us. …”

[Voice: herdsman | speed=0.92]
Harken. Do ye recall the words of Basilides …
```

- **Full-line**, plain text, same grammar family as `[Illustration:]` and
  `[Omit:]`: keyword case-insensitive, then a key, then optional
  `| name=value` overrides drawn from {stability, similarity, speed,
  model}. No nesting; never spans lines. It may sit directly above its
  paragraph or one blank line above it; both read the same.
- Switches the cast **from the next paragraph on** until the next tag or
  the next heading, which resets to the essay default. (A heading is a
  natural boundary and the reset means a dropped closing tag cannot leak
  a voice into the next section.)
- Overrides are the exception path and read as one in Obsidian. Many of
  them are a signal to change the row instead (§1.6).
- **Goes to the Doc** as plain text like every other tag, so the pull
  round trip survives. Stripped from every reader output (pdf, docx,
  epub, md, doc-export) and from audio text. The author accepted the
  Doc visibility on the strength of the count: none in the essays, about
  a dozen in the Sermons.
- A key that is not in `cast.md` is an export error and a readiness
  failure, by name.

### 5.4 The casting filter

`_filters/casting.md`, on the chat road like every filter:

- `class = "sequential"`, `cast = true` → block A carries the CAST block
  (keys and role notes, never voice ids or parameters) the way
  audio-friendly's carries the pronunciation dictionary. It is a
  context declaration like `summaries` and `profiles`, not a prelude:
  nothing is proposed before the units are judged. (Built 2026-09-04;
  the grilling said `prelude = "cast"`, which would have implied a
  proposal-producing pre-pass that does not exist.) `state` = the active
  cast, the last few switches and why.
- It judges *who is speaking now* against the roles `cast.md` defines and
  proposes tags. It never proposes a key that is not a row. It never
  touches a word of prose.
- **The one harness change:** a unit is *the paragraph plus any full-line
  tags immediately above it*. A casting proposal is a replacement of that
  unit with the tag line and the unchanged paragraph; removing or changing
  a tag is the same shape. Filters still replace whole units and never
  insert (`filter-pass-design.md` §"Filters replace WHOLE units").
- Proposals ride the ordinary queue; the author rules in chat or the
  triage app; accepted ones land through the existing edit door.
- Kept separate from `audio-friendly` deliberately: one proposes rewrites
  and its verdicts are evidence about prose; the other places tags and
  its verdicts are evidence about casting. They must not look alike in
  the evidence stream.
- A paragraph that mixes speakers (five in `sermons.md` today: the Dead's
  quoted words and the Herdsman's narration in one paragraph) cannot be
  cast; the filter names it and the remedy is to split the paragraph.
  `audio check` reports the same.

## 6. Section identity — the hash

Every speech section's `id` is

```
sha256( normalized_text
      | voice_id | model | stability | similarity | speed
      | sorted applicable pronunciation rules (term, replacement) )[:32]
```

where *applicable* means the rule's term (case-insensitively, whole
word) occurs in the text. Silences hash `("silence", duration_ms,
ordinal)` — they carry no audio of their own beyond the ffmpeg clip.

Consequences, all intended:

- Same id → same audio; audiostation reuses it without asking.
- Edit a paragraph → one new id in one chapter file.
- Change a `cast.md` row → every paragraph that role speaks gets a new id;
  nothing else does.
- Change one pronunciation → only paragraphs containing that term get a
  new id. A dictionary push therefore never invalidates a book.
- There is no dirty flag anywhere. "Done" is: the id has a state entry
  whose file exists.

Continuity is *not* in the hash (a neighbour's text change would cascade)
— the trade the author accepted for exactness of regeneration; §8 keeps
seams soft.

## 7. Pronunciations → ElevenLabs dictionary

**Amended 2026-09-26: the push mirrors the table.** The author's words:
"if we delete something on our end in the pronunciation table, it's not
making it into the server" and "can we clean out the server every time
we send a batch of updates". Every remote rule whose string the table
does not carry is removed on push; a row without a reading carries
nothing, so its remote rule goes too. The append-only rule below is
reversed. The sixteen remote-only rules found that day were imported
into `pronunciations.md` as rows (note: "imported from the ElevenLabs
dictionary") so nothing was lost silently and every rule shows in the
workbench's dropdown, and four empty rows took the readings the server
had for them.

**[verified]** Phoneme rules work only on `eleven_flash_v2` and
`eleven_v3`; every other model ignores them and needs alias rules.
Matching is case sensitive; the dictionary is scanned start to end and
the first match wins. Up to three dictionary locators per request.
Rules are created via add-from-rules; versions advance on every change.

Rulings:

- `[tts].model` decides the rule type. The pronunciation **prelude reads
  it**: for an alias model it proposes respellings (today's behaviour);
  for a phoneme model it also fills an `IPA` column. The `Say it` column
  stays the author's human reading either way — it is what they rule on.
- **The alias is the `Say it` text as written** (`uh-BRAK-suss`),
  whitespace-normalized and nothing else. Ruled 2026-09-04, after the
  author heard the derived spaced form (`uh brak suss`) gap between
  syllables: the alias is text the voice reads, the table is the
  author's ear, and no derivation and no option stands between them.
  (A configurable form was built and removed the same day.)
- Every term is pushed **twice**: as written and lowercased.
- `audio dictionary push` is **append-only with replacement**:
  1. look the dictionary up by `[tts].dictionary` name; create it if
     absent;
  2. read the remote rules; diff against the table;
  3. show the diff as prose — rows to add, rows whose reading changed,
     remote rules with no table row — and **ask before applying**;
  4. adds → add-rules; a changed reading → remove-rules then add-rules
     (a duplicate appended behind the old rule would be shadowed by
     first-match-wins);
  5. remote-only rules are reported and **left alone** (author's
     append-only rule); `audio check` keeps reporting them.
- No id is stored anywhere. Export resolves the dictionary by name to its
  `latest_version_id` and writes the locator into `audiobook.json`; the
  locator is per book, applied to every section. `audio check` reports
  when `pronunciations.md` is ahead of the remote.

## 8. Continuity across sections

**[verified]** ElevenLabs request stitching: `previous_request_ids` and
`next_request_ids`, at most three each, "should be no older than two
hours", not available on `eleven_v3`; the id is the `request-id`
response header. `previous_text` / `next_text` have no expiry; when both
a text and an id are sent for the same side, the id wins. (Sources:
request-stitching guide, text-to-speech convert reference.)

Audiostation today sends none of these; the "voice stitching constraint"
in its design notes is a UI ordering rule only. Rulings:

- Always send `previous_text` and `next_text` from the nearest
  same-voice speech sections in the chapter (a heading or silence between
  them is skipped over).
- Add `previous_request_ids` / `next_request_ids` when the neighbour's
  stored `request_id` is under two hours old and the model is not v3.
- Store `request_id` and `generated_at` per section in state.
- The same-voice "blocked until prior generated" rule becomes a soft
  ordering (Generate All still goes in order) rather than a hard block,
  since text conditioning makes out-of-order regeneration acceptable.

## 9. Front and back matter, and metadata

**Metadata** (database, `manuscript set` / `set_manuscript_metadata`,
beside author, copyright owner and ISBNs): `narrator`, `publisher`,
`copyright_year`, `language`. The EPUB export's `language` setting moves
here; one copy. Nothing about voices or models is metadata — those are the
audiobook's, not the book's.

**Credits**: rendered from `[credits]` templates into two single-section
chapters in the named voice; mapped to audiostation's opening and closing
credit slots. A placeholder with no value is an export error by name.

**About the author**: `about.md`, listed in `toc.toml` with
`matter = "back"` and `audio = "about-author"`. Print and EPUB carry it as
a back-matter page; audio routes it to the about-author slot instead of
the chapter list. A real file so it gets the Doc mirror, the filters and
the style guide.

**Retail sample**: resolved from `[retail_sample]` to section ids at
export and written into `audiobook.json`. If the heading is gone the
export warns and drops the sample; it never guesses.

**Cover**: `[cover].path`; export checks square, ≥ 2400 px, RGB, and
names the failure before audiostation's audit would.

## 10. Audiostation

### 10.1 Move and rename

`EditorLLM/desktop/` moves (with history) to `authorllm/audiostation/`.
Bundle id, window title, store directory and README follow the name. The
schema fixture (§15) lives once, read by both test suites.

### 10.2 What changes

1. **Open a folder.** "Open Audiobook Folder…" reads `audiobook.json`,
   `chapters/*.json`, `state/*.json` and assembles the in-memory
   `MasterManifest` the UI renders today. Chapter order is
   `audiobook.json`'s.
2. **Write only state.** Autosave writes `state/<stem>.json` (relative
   paths, as now). Chapter files and `audiobook.json` are read-only.
3. **Done = id has state.** Delete `is_dirty`, text-matching merge,
   `normalize_text`-based preservation. Orphan sweep keys on ids no
   chapter file references.
4. **Watch and reload.** Watch `audiobook.json` and `chapters/`; reload
   on change with no prompt; diff (§10.3) and highlight.
5. **Continuity** per §8.
6. **Delete:** the Axum HTTP server, partial-manifest merge, clipboard
   import, v1/v2 single-file load and save, in-app text editing, voice
   dropdown, insert/delete section, "Add Silences", dictionary set
   (locator comes from `audiobook.json`; "resolve to latest" stays as a
   convenience that only touches memory and warns that the export is the
   authority).
7. **Keep:** generate / regenerate / Generate All, play, quality tiers,
   clear-lower-quality, per-chapter stitch with two-pass loudness, ACX
   audit, ACX package, credits display, raw view (read-only).
8. **Refresh** README and `docs/design-decisions.md`, which still say
   generation is stubbed and describe the add-on handoff.

### 10.3 Live reload and highlights

On reload, pair old and new sections per chapter:

| Pairing | Meaning | Card shows |
|---|---|---|
| same id | unchanged | nothing |
| same folded text, different id | parameters or pronunciation changed | which: voice, stability, similarity, speed, model, and each pronunciation rule by name (old → new, added, removed), with the time of the take it is measured against (since 2026-09-04: from the take's recorded `madeFrom`, so the flag also appears on a cold load) |
| different text at the aligned position | text changed | "text" |
| new, unpaired | added | "new" |
| old, unpaired | removed | listed in the chapter header once |

Book-level differences (order, credits, cover, dictionary version,
metadata) show at the top. A chapter with any highlight is marked in the
chapter selector. A section's highlight clears the moment its new id has
generated audio at the selected quality; a chapter's clears when none of
its sections carry one. Folded text for pairing uses audiostation's
existing quote/dash folding; it is display logic only and never feeds an
id.

## 11. Verbs, tools and the SKILL

| Verb | Does | MCP tool |
|---|---|---|
| `audio init` | Seeds missing `audiobook.toml` or `cast.md`; preserves existing files. Refuses when both exist. | — |
| `audio export [--chapters …]` | Compiles `audiobook.json` and `chapters/*.json`; rewrites only changed files; warns per inline-math span, per unresolved tag, per over-long section. | `export_audio` |
| `audio check` | Readiness (§12). Exit 1 on any blocking item. | `audio_readiness` |
| `audio voices [--search …]` | Account and library voices with their free preview clips into the gallery. | `list_voices` |
| `audio audition --cast-candidates A,B [--text … \| --file F --paragraphs 3-5] [--stability … --similarity … --speed …]` | Renders the candidates on the given words at `[tts].model`; appends to the gallery with parameters printed; states the character cost and asks first. | `audition_voices` |
| `audio cast set <key> --voice-id … [--voice … --model … --stability … --similarity … --speed … --note …]` | Writes one `cast.md` row. | `set_cast` |
| `audio dictionary push` | §7. | `push_pronunciations` |
| `filter run casting <file>` | Not new; the casting filter on the existing verbs. | existing |

Absent on purpose: `audio generate` (audiostation's), any writer of
`toc.toml` (hand-edited like every other key), any writer of
`audiobook.toml`.

**The pronunciation workbench (added 2026-09-04).** `audio say <term>`
renders the word's own sentence in the voice that says it with a
respelling substituted inline as the alias rule would; `--settle` writes
the row. `authorlm workbench` is the same loop as a **local page**
served on a loopback port and opened in the browser, exactly like the
Triage App (`web/workbench/` → `authorlm/workbench_dist/`, served by
`workbench_server.py`, transport `POST /api/workbench`): every word,
every voice that says it with its sentence, Regenerate-and-listen per
voice, Save (writes `pronunciations.md`), Push, Export.

**Remove (added 2026-09-04, author's ask: "the ability to remove the
pronunciation dictionary entry if the default (no dictionary) works
better").** Regenerate has three modes — the respelling as written, the
voice's default with no rule at all, and via the pushed dictionary — so
the default can be heard beside the rule. **Remove from dictionary** is
the author's verdict that the default wins: it drops the row from
`pronunciations.md` and, when the book's dictionary is on ElevenLabs and
carries a rule for that term, removes exactly that one rule (a rule left
behind would keep overriding the default just chosen). It is free — no
speech is rendered — and Export afterwards carries the new version to
audiostation, which regenerates only the paragraphs that carry the word.
The append-only rule for push (remote-only rules are left alone) stands:
Remove touches the one term the author named, never a rule the table
never had.

Same day, three more of the author's asks: the sentence each voice reads
is editable in the page ("sometimes it's too long" — a shorter sentence
costs less and says as much; it must still carry the word, and the last
one used opens in the box); **every clip the workbench renders is kept
and keyed by what produced it** — a hash of the term, the voice and its
settings, the mode, the alias as read and the sentence — in
`_audio/auditions/say/workbench-clips.json`, and the page shows, per
voice, the clip whose key matches what is in the boxes right now. The
author's ruling, verbatim: "if I have modified the alias five times, and
there are three voices, and in each case, at some point, I regenerated
the audio files for them, then there would be 15 files on disk, and if I
were to bring back those aliases in the textbox and play the audio, the
corresponding files would play." The same key re-rendered is a re-take
and replaces the take; nothing is ever re-rendered to be shown. (The
dictionary's version is not in the key — the page cannot know it — and
is shown on the record instead.) And
the card shows the table's current reading beside the unsaved one with
its own Save, because the page-wide Save at the foot was being missed.
A new word may be added to the table with no reading yet; a save never
blanks an existing row.

It was first built the same morning as an MCP App rendered inside the
chat window, with the standalone page ruled out as a fourth surface.
**Reversed the same day (2026-09-04, author's call):** the MCP App road
never rendered in the client the author actually uses (the Claude
desktop app's Code tab does not render MCP Apps, and the AuthorLM server
is not connected to its Chat tab), so it was removed from both the
workbench and the Triage App rather than kept as a third door beside
the local page. Still standing: not hosted in audiostation, because the
output is a manuscript file and the dictionary push, both AuthorLM's.
Whether the three pages should share one shell ("authorstation") is an
open question filed for its own grilling, not decided here.

**The gallery** reuses podlm's pattern (`podlm/tools/gallery.py`): a spec
JSON plus an HTML page with clips embedded as data URIs, printed with
"publish this as an artifact". One page per manuscript, appended to
across `voices` and `audition` calls, so the whole audition history is one
artifact. Clips are short and at low quality by default.

**The SKILL protocol** (to be added to `.claude/skills/authorlm/SKILL.md`):

1. Free first: `audio voices` on a description of the role; the author
   listens and names a shortlist in chat.
2. Then paid: `audio audition` on the author's own text — the paragraphs
   that will actually be generated, never hand-prepared text — with the
   character cost stated before the call.
3. Vary one parameter per round. Never write a cast row the author has not
   heard; never audition on paid clips before a free pass has narrowed the
   field.
4. Settle: propose the row as prose; `audio cast set` on the author's
   word.
5. Casting the passages is a separate act: run the casting filter after
   the cast is settled, so proposals only ever name real voices.

## 12. `audio check` — the readiness report

Source side (blocking unless noted):

- metadata complete: title, author, narrator, publisher, copyright_year,
  copyright_owner, language;
- `about.md` present and routed; credits templates resolve;
- cover present, square, ≥ 2400 px, RGB;
- every toc file resolves to a cast key that exists and has a Voice ID;
  every `[Voice:]` key exists; no override names an unknown parameter;
- no mixed-speaker paragraph unsplit (heuristic: a paragraph containing
  both a closed quotation and prose outside it, in a file with any
  `[Voice:]` tag) — warning;
- no unresolved `[Footnote:]`/`[Explain:]`; inline math spans — warning
  with count;
- dictionary exists remotely, table not ahead of it, no remote-only rules
  — the last is a warning;
- retail sample resolves to one to five minutes at ~150 wpm;
- `audiobook.json` and every chapter file newer than every source they
  depend on (toc, toml, cast, the essays, pronunciations, metadata).

State side (read-only, informational): sections generated / total per
chapter at `[tts].quality`, chapters stitched, credits and about-author
generated, orphans present. The final gate on the audio itself remains
audiostation's ACX audit.

## 13. Build order (ratified)

1. **One voice, end to end.** `audio init`, `audio export`, §4 rules, §6
   ids, `audiobook.toml` voice declarations, audiostation moved/renamed/folder-open/state
   /watch/highlights/continuity, deletions in §10.2. Proves the two
   programs meet on the hash. Output: stitched chapters in the narrator.
2. **Voices and pronunciation.** `voices`, `audition`, `cast set`, the
   gallery, `dictionary push`, the SKILL protocol.
3. **Casting the Sermons.** The casting filter, the unit rule, the five
   paragraph splits.
4. **Publishing.** `about.md`, credits, retail sample, cover, the four
   metadata fields, `audio check`, an ACX package out of audiostation.

Slices 3 and 4 are independent and may swap.

## 14. Example rows

`chapters/sermons.json` (excerpt):

```json
{
  "schema": 1,
  "file": "sermons.md",
  "title": "Seven More Sermons To The Dead",
  "sections": [
    {"type": "silence", "id": "…", "durationMs": 3250},
    {"type": "speech", "id": "a3f9…", "kind": "heading", "level": 1,
     "text": "Seven More Sermons To The Dead",
     "cast": "herdsman", "voiceId": "pNIn…", "voiceName": "Adam Stone",
     "model": "eleven_multilingual_v2", "stability": 0.65, "similarity": 0.80, "speed": 0.97,
     "source": {"line": 1}},
    {"type": "silence", "id": "…", "durationMs": 700},
    {"type": "speech", "id": "c71e…", "kind": "paragraph",
     "text": "“O Stranger, all this is new to us. …”",
     "cast": "the_dead", "voiceId": "3MTv…", "voiceName": "Julian",
     "model": "eleven_multilingual_v2", "stability": 0.50, "similarity": 0.75, "speed": 1.0,
     "source": {"line": 45, "tag": {"line": 44}}}
  ]
}
```

`state/sermons.json` (excerpt):

```json
{
  "schema": 1,
  "sections": {
    "c71e…": {"audioFiles": {"mp3_44100_128": "audio/c71e….mp3_44100_128.mp3"},
              "requestId": "8f2c…", "generatedAt": "2026-09-10T18:02:11Z"}
  },
  "stitched": {"mp3_44100_128": "audio/sermons.mp3_44100_128.mp3"},
  "durationSecs": 1811.4, "loudnessLufs": -20.0, "truePeakDbtp": -3.0
}
```

`audiobook.json` (excerpt):

```json
{
  "schema": 1,
  "title": "Seven More Sermons To The Dead", "subtitle": "…",
  "author": "…", "narrator": "…", "publisher": "…", "copyrightYear": 2026,
  "copyrightHolder": "…", "language": "en",
  "chapters": ["chapter1", "preface", "sermons", "…"],
  "openingCredits": "chapters/_opening-credits.json",
  "closingCredits": "chapters/_closing-credits.json",
  "aboutAuthor": "chapters/about.json",
  "retailSample": ["c71e…", "…"],
  "cover": {"path": "../_assets/audiobook-cover.jpg", "width": 2400, "height": 2400},
  "pronunciationDictionary": {"name": "SMSTTD pronunciations", "id": "GZBu…", "versionId": "…"},
  "cast": {"narrator": {"voiceId": "…", "…": "…"}, "herdsman": {"…": "…"}},
  "quality": "mp3_44100_128", "generatedAt": "2026-09-10T17:55:00Z"
}
```

## 15. Tests

- **Schema fixture**: `tests/fixtures/audiobook/` holds one complete
  folder (toml, cast, json, state). Python asserts the export reproduces
  the json from the sources byte-for-byte; Rust asserts it parses,
  assembles and round-trips state. One commit changes both.
- Text rules: each row of §4 as a golden case; the collapse of adjacent
  silences; heading reset of casting; `[Voice:]` grammar accept/refuse;
  the every-tag-goes-to-Doc rule (`normalize_markdown` idempotence on a
  tag line).
- Hash: each of the five §6 consequences as a test that changes one
  thing and asserts exactly which ids move.
- Dictionary: alias derivation; both-cases push; diff classification
  (add / changed / remote-only); replacement is remove-then-add; the
  confirmation is asked and honoured; no id persisted.
- Continuity: neighbour selection skips headings and silences; ids sent
  only under two hours; not sent for v3.
- Reload pairing table of §10.3, one test per row.
- `audio check`: one failing fixture per blocking item.
- Casting filter: unit boundary includes the tag line; a proposal cannot
  name an unknown key; a mixed-speaker paragraph is named, not cast.

## 16. Deliberately not done

- **Plays and line-level dialogue.** Per-paragraph casting only.
- **Mid-paragraph voice switches.** Split the paragraph.
- **`footnotes = "inline" | "end"`.** Reserved values; only `drop` is
  built until an author wants a footnote read aloud.
- **Per-paragraph parameter modulation as a workflow.** Overrides exist;
  the practice is discouraged (§1.6).
- ~~**`audio generate` in AuthorLM.** Generation stays in audiostation.~~
  Reversed 2026-09-25: generation, retake, stitch, previews and a served
  review page are AuthorLM verbs; audiostation stays as an equal second
  door on the same `state/`. See `audiobook-review-design.md`.
- **Migration of the May 2026 test manifest.** Re-export instead.
- **`eleven_v3`.** No request stitching there; nothing prevents choosing
  it in `[tts].model`, but §8's id path is skipped and the export says so.
- **YAML anywhere.**
- **A writer for `toc.toml` or `audiobook.toml`.**

## 18. Implementation status (2026-09-04)

Built in one pass from this document:

- **AuthorLM**: `authorlm/audio.py` (config, cast, text rules, casting
  resolution, ids, export, readiness, the ElevenLabs client, auditions,
  the dictionary push); `authorlm audio …` and the six MCP tools of
  §11; `[Voice:]` stripped from reader outputs in `export.py`; the
  unit rule in `passes.paragraphs_of`; `cast = true` for filters (the
  CAST block in `filtering.py`); narrator / publisher / copyright_year /
  language as manuscript metadata (schema migration, `manuscript set`,
  `set_manuscript_metadata`); `tools/gallery.py` embeds audio; the
  casting filter installed at `manuscripts/SMSTTD/_filters/casting.md`
  (appendix below); the SKILL's audition protocol.
- **audiostation**: moved from `EditorLLM/desktop` with history
  (`git subtree`), renamed, rewritten per §10 — folder open, state-only
  writes, id-done semantics, watcher and highlights, continuity, orphan
  sweep, ACX audit and package; HTTP server, merge, clipboard, in-app
  editing, dictionary commands deleted.
- **Tests**: `tests/test_audio.py` (95 checks) and the Rust suite (14
  tests) both read `tests/fixtures/audiobook/`. `python3 tests/test_audio.py`
  and `cd audiostation/src-tauri && cargo test`.

Verified end to end on a scratch manuscript: `audio init` → `cast set`
→ `export --offline` (six files) → edit one paragraph → re-export
rewrites one chapter file → `audio check` names the short retail sample
and reads the (empty) state. Rust loads the same folder, diffs a reload,
sweeps orphans, builds a request with neighbour text.

**Not verified live**: no ElevenLabs call was made. The only key on this
machine (audiostation's stored key, copied into `.env`) is rejected by
the API as a key *id* ("API keys start with 'sk_'"), so `audio voices`,
`audio audition`, `audio dictionary push`, the export's dictionary lookup
and generation in audiostation all await a current `sk_` key in
`authorlm/.env` and in audiostation's Settings.

### Appendix — the casting filter (ratified shape, installed 2026-09-04)

```markdown
---
class = "sequential"
cast = true
state = "the active cast and the last few switches: who is speaking now, where the voice last changed and why, and any passage where the speaker was ambiguous and the default was kept"
---

# Casting

This essay will be read aloud by more than one voice. Decide, unit by
unit, who is speaking, and mark the switches with a `[Voice: key]` line
above the paragraph. You place tags; you never change a word of prose and
you never choose a voice or a parameter.

- The essay's default voice is already set (`_audio/audiobook.toml`). A unit stays in the
  active voice unless the speaker plainly changes.
- A switch is a replace of the unit with the tag line, one blank line, and
  the paragraph exactly as it was. Nothing else in the unit changes.
- A key must be one of the CAST block's keys. Never invent one.
- A heading resets the voice to the essay default on its own.
- A paragraph that mixes two speakers cannot be cast: keep it and name it
  in the state as "mixed: unit N"; the author splits it.
- Quotation marks alone do not justify a switch. When in doubt, no switch.
```

## 17. Open questions

- ElevenLabs' per-request character limit differs by model and was not
  found on the reference page; `max_section_chars` defaults to 5000 as a
  conservative warning threshold until measured.
- Whether the alias derivation (`uh brak suss`) is read well enough by
  multilingual v2, or whether the book should move to `eleven_flash_v2`
  for phoneme support — to be settled by audition in slice 2, which is
  why `[tts].model` is configuration.
- Whether `about.md` should also carry a `[Voice:]` default of its own or
  simply take `narrator` — trivial; decide when the file exists.
