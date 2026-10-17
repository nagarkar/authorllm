# Video Clips — Design Reference

Grilled and ratified 2026-10-16 to 2026-10-17. Slice 1 built 2026-10-17
(§10). §9 lists what is still open.

The author's ask, condensed: podcast episodes read manuscript text as
monologues (verbatim scripts) or speak from it (research notes for guest
episodes), and want video under or between the words: YouTube reaction
collages, stills with light motion and typed text, and short generated
stories that unfold during the telling (Ecce Homo: a man working hard,
coming home tired, a doctor's verdict, withdrawal from life). Writing
what a clip shows, and where, is writing; making the clip is not.

Related designs: `illustration-placement-design.md` (tag, embed line,
candidates and picks), `verse-and-cast-design.md` §5 (the cast:
`cast-id`, `cast`, `prior`, `stale-cast`), `math-and-physics-guidelines.md`
§5 (`[Omit:]` / `[Only:]` regions), `audiobook-pipeline-design.md`
(AuthorLM writes the manifest, a station generates). Sister repo: podlm
(`~/workspace/repos/podlm`), which owns the episode timeline.

## 1. The boundary (revised 2026-10-16)

AuthorLM supports tags for light annotation of what a clip shows and
the words it plays under, and is an **asset service** over them: for a
tag it reports whether an asset exists and where, (re)generates it on
request with its own subsystems (as `illus render` does for
illustrations), and accepts an asset made elsewhere and pins it to a
tag. It never composes video and never sets timeline timing.

podlm owns production: the timeline, how a clip is interpreted and
placed, and the final video. It calls AuthorLM's verbs when it needs an
asset, interleaved with its own steps, and uses the file paths returned.
Any other client (a parallel agent, another LM tool) uses the same
verbs. Precedent: `outside-caller-design.md` (ytlm's entry points).

### 1a. Transport (ruled 2026-10-16)

Each verb is implemented once in `api` and exposed as CLI `--json` (how
podlm's stages call it, across conda envs) and as MCP tools (how agents
call it). Same-env callers may import the library, as ytlm does. Every
surface returns **file paths only, never asset bytes**. Generation takes
minutes, so a verb that may generate returns at once with either the
pinned file's path or a job id, and a status verb reports the job.

## 2. Which kinds are in (ruled 2026-10-16)

| kind | in? | why |
|---|---|---|
| YouTube collage (excerpts stitched, play/pause, react) | in | decided while writing |
| still + light motion + typed text (e.g. Kailash push-in) | in | decided while writing |
| generated story clips | in | decided while writing |
| podlm cutaway slides | out | decided after recording, from the transcript; it is an illustration placed by timing, and stays wholly in podlm |

## 3. No timing in the tag (ruled 2026-10-16)

A tag never carries timeline timing: cuts, mutes and slips move it, so
podlm owns it. The one exception is not timing: on a quoted or used
video, `start:` / `end:` name *which seconds of the source* are taken,
like a page number on a quotation, and are part of the clip's identity.

## 4. Three shapes (ruled 2026-10-16; Montage split out 2026-10-17)

- **Span — one clip under the voice.** `[Clip: …]` prose `[/Clip]`. The
  clip plays while the wrapped words are spoken. The words are the length
  guide: the generator aims for roughly the narration's length; podlm fits
  the result to the real timeline.
- **Collage — a cutaway.** `[Collage: <title>]` … `[/Collage]` holds clips
  only, each a `[Clip: …]` with no close. Narration stops, the clips play
  at their own length, narration resumes. The collage becomes one stitched
  video. Prose inside a Collage is an error: it belongs in a Montage.
- **Montage — several clips under the voice.** `[Montage: <title>]` …
  `[/Montage]` holds clips and prose. The clips play in order under those
  words; where each clip line sits in the prose is a hint for where it
  starts. A Montage with no prose is an error: clips alone are a Collage.

Collage and Montage are separate tags, not one tag with a mode, because
their rules already differ (narration stops vs plays on; a clip's own
audio is wanted in a cutaway and not under the voice) and more
differences are expected. Neither may be empty of clips, nest in the
other, or open inside a span; each closes with its own close tag. Any
clip kind (§5) may sit in either.

```
[Collage: The Architect and Neo]
[Clip: Neo answers, "The problem is choice." | youtube: <url> | start: 2:10 | end: 2:31]
[/Collage]

[Montage: Choice as prison and escape]
[Clip: the Architect's room of screens | youtube: <url> | start: … | end: …]
[Clip: Neo turns toward the door to the Source | youtube: <url> | start: … | end: …]
Choice made the prison possible—and yet also made it imperfect. …
[/Montage]
```

A story is a run of consecutive span clips, one per beat, held together
by the cast.

## 5. The tag's fields (ruled 2026-10-16)

**A description generates; no description points.** A clip with a
description is generated: it gets `⇢ <key>.md` (the canonical spec) and
`_clips/<key>-…-NN.mp4`. A clip without one must point at something:
`use:` or `youtube:`. A bare clip is a gate error. Text on a `youtube:`
clip is a **label** only (shown in `clip list`, never sent to a model,
no `.md`). Whether a generated clip is made by a video model or matched
from stock is a production choice, not in the tag.

**Inputs are typed by role, not by file kind** — the pattern current
video models share (Veo 3.1 first frame / `lastFrame` /
`referenceImages`; Sora 2 `input_reference`; Runway Gen-4 references;
Kling start/end frames and elements). Researched 2026-10-16 from mostly
secondary sources; verify field names against vendor docs before build.

| field | role | value |
|---|---|---|
| `start:` / `end:` | generated: first / last frame (`end` needs `start`); `youtube:` or `use:` video: trim points | image (path, illustration key, cast id), or timestamp `m:ss` / `h:mm:ss` |
| `cast:` | references ("ingredients"), at most 3 | cast ids, qualified or not |
| `prior:` | continue from (the story's previous beat) | a clip key |
| `look:` | style + model | look name (§8d) |
| `text:` | exact on-screen words, never paraphrased | quoted string |
| `use:` | an asset used as is, no generation | path |
| `youtube:` | quoted video | url |

A value containing `/` or an extension is a path; otherwise it is a key
resolved in that field's namespace. `excerpt:`, `file:` and `slot:` are
not used: an illustration key works as `start:` or `cast:` depending on
how it is meant.

The gate checks combinations, deterministically: a timestamp on a
generated clip, an image on a `youtube:` clip, an image `use:` with
`start`/`end`, `end` before `start`, and `end` without `start` on a
generated clip are errors.

```
[Clip: Peterson's lobster line | youtube: <url> | start: 1:23 | end: 2:05]
[Clip: | use: _assets/own-footage/temple.mp4 | start: 0:40 | end: 0:52]
[Clip: slow push-in on the summit at dawn | start: _assets/kailash.jpg | text: "Mount Kailash, 6,638 m"]
[Clip: he slumps at his desk as the office empties | cast: studio:ecce-man | prior: man-commute]
```

Motion is part of the description. A still `use:` inside a Collage or
Montage has no natural length; the station holds a default and podlm adjusts.

Vendor note for later: forum reports say Sora 2's API rejects reference
images with human faces, which would block `ecce-man`-style plates there.

Out: podlm cutaways; audio-only sources (music, effects); anything that
exists only at record time (live or screen capture).

## 6. Where tags live (ruled 2026-10-16)

In the chapter file itself, not a podcast copy (a copy drifts). Every
book export (pdf, docx, epub, md, doc, audio) drops every Clip, Collage
and Montage tag line and keeps **every word of prose** (revised
2026-10-17: a tag never deletes the author's prose; the first ruling
dropped a whole Collage and, in the Ecce Homo trial, took two book
paragraphs with it). Podcast-only prose, if ever needed, gets its own
marking. The Doc tab carries the tags as text, separated from prose by
blank lines, as illustration tags are.

## 7. Materialization: tag, embed line, picks (confirmed 2026-10-16)

As illustrations do (`illus.py`):

- The tag is never rewritten; its description stays canonical (inline,
  or in a prompts file behind `⇢` when long).
- Results land as candidates `-01`, `-02`, …; one is pinned; an embed
  line directly under the tag points at the pick. Picks survive Doc
  round trips.
- Editing the description marks the slot `stale-desc`; re-picking a cast
  plate it used marks it `stale-cast`.
- A quoted clip has no takes in AuthorLM: podlm downloads and trims it
  (§8f).

## 8. Where files live (ruled 2026-10-16)

`_clips/` in the manuscript folder, beside `_illustrations/` and
`_audio/`, same naming convention. The Doc never carries video.

- Canonical text: a file in the folder of its kind (§8l:
  `_clips/youtube-briefs/`, `_clips/specs/`, `_clips/notes/`); the inline
  tag keeps a short excerpt and `⇢ <key>.md`.
- Candidates: `_clips/<key>-<deschash8>-<lawhash4>-NN.mp4`; inserted
  assets number `iNN`.

## 8a. Identity (ruled 2026-10-16)

A clip's identity is its **key**, the slug of its prompt file, minted
once and never recomputed (the illustration lesson: hash identity
orphaned art on rewording). Unlike illustrations, the key is minted at
**collect** for every `[Clip]` and `[Collage]` tag, so a caller can list
and name any saved clip. Callers address it as `<manuscript>:<key>`.

## 8b. Staleness (ruled 2026-10-16)

Recomputed on every read from text and directory; no DB state.

| marker | hash of | on change |
|---|---|---|
| `stale-desc` | the canonical spec, whitespace collapsed | flag |
| `stale-law` | the clip style law | flag |
| `stale-cast` | plate files recorded at generation | flag |
| `stale-span` | the words a span wraps, recorded at generation | warning only (length may be off) |

Stale only flags; nothing regenerates by itself.

## 8c. Trigger, gate, dry run (ruled 2026-10-16)

Generation is explicit only: a caller's verb, or the author in CLI or
chat. Never on collect (video is too costly for the illustrations'
render-without-asking rule). The ensuring verb is idempotent: a fresh
pick returns its path and spends nothing.

Every verb that can spend money takes `--dry-run` (MCP: `dry_run`).
There is no separate gate verb: a dry run runs the gate and reports the
cost estimate and what would be done, and spends nothing. A real run
runs the same gate first. The gate is deterministic, no model call, and
returns reasons:

1. The spec is not a draft (§8e): at least 150 words.
2. Every `cast:` id is defined and has a picked plate.
3. A span is closed and wraps at least one full sentence.
4. The field combinations of §5 hold; a quoted clip has a valid url and
   is under a maximum length.
5. Cost over a per-call or per-day cap needs `--confirm`.

A model-judged "is this filmable" check is deferred until bad
generations show it is needed.

## 8d. Looks and the Studio library (ruled 2026-10-16)

The image law stays in the style tables, one row per rule, as today:
per-file variation is in use (SMSTTD `metaphysic.md` overrides the
house engraving rule with line diagrams), and the belief → law loop,
the lifecycle (retire, `overrides`, proposed/rejected) and `stylehash`
all work per rule. Clip law is a new aspect, `clip`, in the same tables.

- **A look** = one image aspect (`clip` or `illustration`) of a named
  guide, plus a model pin (style is model-bound,
  verse-and-cast-design §7). It never touches a file's prose law and
  adds no `style_attachments` row.
- **Studio** is one library manuscript, registered with extraction off
  and never exported. Each look is a guide there (guides inherit, e.g.
  `Studio base` → `film`, `sumi-e`, `engraving`); `looks.toml` binds each
  guide to its model and size. A cast is a file of `cast-id` tags
  (`casts/ecce-homo.md`); each plate names the look it is drawn in; in
  steady state each tag has `⇢ <key>.md` and an embed line on its pinned
  plate, exactly as in a chapter. Ids are unique across Studio. More
  libraries only if names start colliding.
- **Qualified names**: `look: studio:film`, `cast: studio:ecce-man`. An
  unqualified name means the tag's own manuscript, so existing tags are
  unchanged.
- **Defaults**: a manuscript sets `[clips] look = "studio:film"` (and
  may set an illustration default) so clips differ from the book without
  per-tag overrides; a tag's `look:` is for exceptions.
- Staleness crosses manuscripts because it is file-computed: re-picking
  a Studio plate marks every dependant in every manuscript `stale-cast`.
- Code (estimate, not built): `styles.py` resolves `ms:guide` and
  composes a foreign guide's chain under its own `manuscript_id`;
  `illus.py` gains `look:`, library scanning in `cast_index`, and reads
  the pin from the look. No schema change.

Noted in passing: an `overrides` link is not cleared when its target is
retired (SMSTTD `metaphysic.md` labels rule points at a retired house
rule); harmless, since composition loads only active rows.

## 8e. Incomplete tags: the directive road (ruled 2026-10-17)

Mirrors the footnote directive (`footnote-directive-design.md` §2–5).
The author may write shorthand anywhere — `[Clip: tired man, office,
dusk]`, or an empty `[Collage: …]` — and fill it in later.

1. **Collect reports, drafts nothing.** It lists every clip not ready:
   spec under 150 words, missing asset, a broken field combination
   (§5). No model runs at collect. `get_status` carries the list; the
   chat collaborator raises it unprompted.
2. **Chat drafts.** From the shorthand plus context — the words a span
   plays under, the neighbouring clips in the story, cast plates, the
   file's `clip` law — the collaborator proposes a spec of 150+ words,
   specific enough to generate from or search stock with. The author
   reacts; it redrafts. Reasons that state a principle are recorded as
   evidence.
3. **Doc road.** On a checked-out file the proposal may be staged in the
   tab as a pending form (`<<shorthand>>{{full spec}}`), editable in
   place; `clip resolve` lands it, as `footnote resolve` does.
4. **Landing.** Unlike a footnote, the tag is not consumed: the accepted
   text goes to its kind's file (§8l, canonical) and the tag keeps
   the shorthand as its excerpt plus `⇢ <key>.md`.
5. **Draft status.** A YouTube brief without a source and a quote or
   moment, or a generation spec under 150 words or missing a section,
   is a `draft` (§8l); a generating
   verb declines with "spec is a draft".
6. For a quoted clip, incomplete means a missing url or times; the
   proposal suggests candidates for what is missing rather than a
   description.

## 8f. Verbs, takes and the choice (ruled 2026-10-17)

podlm drives production. It downloads and trims `youtube:` excerpts
itself (they live in podlm, not `_clips/`; AuthorLM only checks the
tag's url and times). For generated clips it calls AuthorLM's verbs and
AuthorLM makes the model call.

| verb | does | can spend |
|---|---|---|
| `media list [ms] [--file F] [--key K]` | the one umbrella verb: every clip, collage and illustration, each with its `kind` (below) | no |
| `clip ensure ms:key [--generate] [--dry-run] [--confirm]` | paths of fresh takes; generates only when none is fresh, or with `--generate` to add one; else a job id or gate reasons | yes |
| `clip job <id>` | state; when done the take's path; always paths to the composed prompt, request, response and error log, so podlm can diagnose a bad generation | no |
| `clip attach ms:key <file>` | inserts an outside asset as take `iNN`; returns its path | no |
| `clip pick ms:key NN` | registers the chosen take; moves the embed line | no |
| `clip resolve <file>` | lands staged spec proposals (§8e) | no |

- A collage key uses the same verbs; `ensure` covers every child.
  AuthorLM does not stitch.
- **The choice.** AuthorLM is the store of record: `_clips/chosen.json`
  maps each clip key to its chosen take (revised 2026-10-17 from an embed
  line under the tag, which the Doc never carries and a pull would lose). The author chooses in podlm's
  gallery (or a text listing when the takes are too many), as podlm's
  thumbnails are chosen; podlm registers it with `clip pick`. podlm never
  chooses on its own. One choice per clip, not per episode.
- Staleness is computed for every take from its filename's hashes.
- **`media list --json` returns everything AuthorLM knows** about each
  clip (ruled 2026-10-17): the canonical spec; the fields with cast and
  look resolved to plate paths and model; the words a span wraps,
  verbatim in the export's spoken form (tags, footnote markers and Omit
  regions removed), plus its first and last sentences; for a point clip,
  the sentence before it and the first sentence after; file, key, order;
  `estimated_seconds` at ~150 wpm (a generation hint, never timeline
  timing); every take with hashes and staleness; the chosen take; job
  log paths. About 5 KB a clip. Paths only, never bytes. `--file` and
  `--key` scope it for agents. podlm fuzzy-matches the words to its
  transcript; AuthorLM never sees transcript times.

## 8g. Naming and illustrations (ruled 2026-10-17)

- Tags keep their specific names: `[Clip]`, `[Collage]`,
  `[Illustration]`.
- **`media list` is the only umbrella verb.** Its entries carry `kind`
  (`clip`, `collage`, `illustration`). "Media" rather than "visual":
  generated video and YouTube excerpts carry sound. The kinds stay
  closed; music and sound effects remain out (§5).
- Every other verb is per kind: `clip ensure | job | attach | pick |
  resolve` for clips and collages; the existing `illus` verbs (`render`,
  `pick`, `import`, …) for illustrations, plus a new **`illus ensure`**
  with the same contract as `clip ensure` (paths of fresh renders; render
  only when none is fresh or on request; `--dry-run`), so podlm calls
  both kinds the same way.
- **Illustrations in a monologue reach podlm** (ruled 2026-10-17):
  `media list` includes them with the chosen image, the caption and the
  paragraph they follow, aligned like a point clip. podlm decides
  whether to show them, as cutaways or stills under the voice. Files with
  `illustrations = "none"` contribute nothing.

## 8h. No spot-finder (ruled 2026-10-17)

Clip and collage tags are planted by hand only; §8e expands shorthand.
No `clip scan` and no `clip-placement` law. Revisit after a few episodes
if the same kind of clip keeps being planted by hand.

## 8i. Validation (ruled 2026-10-17)

Checked at collect, reported, never auto-fixed.

| case | ruling |
|---|---|
| span not closed by `[/Clip]` before the next heading or end of file | error; the clip stays a draft |
| span inside a span | error |
| `[/Clip]` inside a Collage or Montage | error (a clip there has no close) |
| Collage or Montage inside either, or opened inside a span | error |
| closed by the other container's close tag | error |
| Collage holding prose / Montage holding none | error (§4) |
| Collage or Montage with no clips | error |
| any container or span crossing a heading | error |
| span crossing a paragraph break | allowed |
| `[Clip]` inside an `[Omit: audio]` region | error |
| unknown `cast:` / `prior:` / `look:` / `start:` key | error |
| `prior:` naming a clip later in the file | warning |
| `[Illustration]` inside a span | allowed; it is its own `media list` entry |

## 8j. Rulings of 2026-10-17 (afternoon)

- **The 150-word rule is for generated clips only.** A `youtube:` clip's
  text is a label and a **search brief**: no minimum, but the directive
  road (§8e) proposes a richer description when the url or times are
  missing, because a descriptive brief finds the right video faster.
- **Vendor: Google first** (Veo through the Gemini API), since the author
  already pays Google. Switch only if Veo proves clearly worse for this
  work. Note to verify at slice 3: a consumer Google AI subscription and
  Gemini API billing are separate accounts; the API is billed per second
  of video.
- **No take choice for quoted clips.** A collage or montage made only of
  `youtube:` clips has nothing to choose between (the url and times are
  the clip); the gallery and `clip pick` matter only once generated clips
  arrive.
- **Finding YouTube clips is podlm's road, using ytlm**: search and pull
  transcripts with ytlm to find the quote and its timestamps, propose
  `youtube:` + `start:`/`end:` for the author to confirm, then podlm
  fetches, trims and stitches. AuthorLM only checks and lists the tags.

## 8k. Where podcast text lives, and who fetches (ruled 2026-10-17)

- **Both roads stay.** (1) A book chapter tagged in place: a multimedia
  edition of the audiobook, collages and inserts over the author reading
  the book (Ecce Homo is the trial). (2) Monologues in their own
  manuscripts, which may be series: `SpiritualHindu` first, later e.g. a
  series on nothingness or on choice.
- **Existing monologues** are made by corpuslm (`monologue new/run/
  export`), one manuscript per monologue (`monologue-picture-story`,
  `monologue-two-returns`, on the external drive). Find manuscripts with
  `list_manuscripts`, never by listing a folder.
- **A series manuscript holds its monologues as files** (ruled
  2026-10-17): `SpiritualHindu/picture-story.md`, `…/two-returns.md`, one
  style guide, Doc and concept graph per series. corpuslm's
  one-manuscript-per-monologue pattern was never an explicit decision and
  is replaced: corpuslm writes new monologues into a series, and the two
  existing ones move by hand (no back-compat while prototyping).
- **ytlm owns YouTube sources**, as AuthorLM owns text: search, transcript,
  quote location, and (new) video download and excerpt cutting, cached on
  the external drive, returning paths. podlm builds reels and the timeline
  from those paths; its own yt-dlp download in `collage.py` moves to ytlm.

### Search bake-off (2026-10-17)

Eight briefs worded as clip tags (the Architect scene, "the problem is
choice", Kastrup's ten books, Peterson's lobsters, Jung's Face to Face,
Watts' Dream of Life, Feynman's magnets, Spira's screen analogy), top 5
from yt-dlp `ytsearch` (keyless) and the YouTube Data API `search.list`
(free quota). Both put a correct video at rank 1 for all eight on a loose
title match. Read closely, the API was more specific: it ranked the actual
Architect scene upload first (yt-dlp: a commentary video) and the exact
Spira screen-analogy clip first (yt-dlp: a general Spira talk). yt-dlp
found the bare "the problem is choice" clip first. Latency: API ~0.4 s,
yt-dlp ~1.4 s. An earlier web search could not surface the scene at all.

Transcripts settle the times: of 10 candidates, 9 had captions (one had
transcripts disabled) and the quote was located in each. The Architect
scene's line falls at [2:45 of part 1](https://www.youtube.com/watch?v=cHZl2naX1Xk&t=165), but split across two
caption segments ("Choice the problem is" / "choice"), so quote location
must match across neighbouring segments, not one at a time.

**Ruled 2026-10-17: the YouTube Data API only, no yt-dlp search
fallback.** When the API fails (no key, quota spent, network), the verb
stops and says so; it never falls back to a weaker search that would
quietly return worse candidates. Then: keep the candidates that have a
transcript; locate the quote across a sliding window of segments; propose
`youtube:` + `start:`/`end:` with a pad, for the author to confirm by
watching. (yt-dlp stays for downloading, which is a different job.)

### Where each change goes (recorded 2026-10-17; not yet filed as issues)

These rulings live here for now. Each change belongs to the repo that
owns the behaviour; that repo's skill is updated when the behaviour is
built, never before, so no skill describes something that does not exist.

| change | repo | code | skill and docs to update when built |
|---|---|---|---|
| Series manuscripts: write each monologue as a file in a series manuscript (`SpiritualHindu/<name>.md`); move `monologue-picture-story` and `monologue-two-returns` in by hand | corpuslm (`nagarkar/corpuslm`) | `src/corpuslm/cli.py` (`monologue new/run/export`), `moments.py`, the AuthorLM registration call | `corpuslm/.claude/skills/corpuslm/SKILL.md` (the monologue rows); corpuslm design docs |
| YouTube search via the Data API only, failing loudly | ytlm (`nagarkar/ytlm`) | new search verb beside `src/ytlm/api.py` | `ytlm/.claude/skills/ytlm/SKILL.md` (verbs); `ytlm/docs/design/` |
| Quote location across neighbouring caption segments → proposed `start:`/`end:`, expanded to whole sentences (start at the first sentence's start, end before the next sentence begins; §8l) | ytlm | the `segments` / `search` code | same as above |
| Video download and excerpt cutting, cached in the ytlm workspace on the external drive, returning paths | ytlm | `src/ytlm/adapters/ytdlp.py` (today audio only) | same as above |
| Read `authorlm media list --json`; get excerpt paths from ytlm; trim each excerpt precisely by looking at the section (frames, audio, word timings) so no stray fragment plays (§8l); build Collage reels and Montage layers | podlm (`nagarkar/podlm`) | `src/podlm/pipeline/editorial/collage.py` (its own yt-dlp download moves to ytlm), a new media stage | `podlm/.claude/skills/podlm/SKILL.md` (the collage section, today `collage.yaml`); `podlm/docs/` |
| Write confirmed `youtube:` / `start:` / `end:` back into a tag | authorllm | slice 2 (§10) | `authorllm/.claude/skills/authorlm/SKILL.md` (Media section) |

## 8l. YouTube briefs and generation specs (ruled 2026-10-17)

A YouTube clip and a generated clip need different writing, so they are
different kinds of text, stored apart and checked by different code.

| | YouTube brief | generation spec | note |
|---|---|---|---|
| for | a `youtube:` clip | a clip to be generated | a Collage or Montage title, a `use:` clip's label |
| read by | ytlm's search (`query`) and quote location (`quote`) | the video model (slice 3) | people |
| sections | `source:` the work or person (title, year, channel) · `moment:` the scene · `quote:` exact words spoken · `on screen:` who and what | `subject:` · `setting:` · `look:` · `camera:` · `light:` · `motion:` · `sound:` · `continuity:` · `on-screen text:` ('none' is an answer) | — |
| ready when | a source, and a quote or a moment; no word minimum | 150+ words and every section | always |
| file | `_clips/youtube-briefs/<key>.md` | `_clips/specs/<key>.md` | `_clips/notes/<key>.md` |
| code | `media.check_youtube_brief`, `media.brief_terms` | `media.check_generation_spec` | — |

**Whole sentences (ruled 2026-10-17).** A clip never starts or ends
mid-sentence. A brief's `quote:` is whole sentences, from a capital to the
closing `.`, `!` or `?`; `media.check_youtube_brief` marks a fragment as a
draft. The `start:`/`end:` written into the tag span exactly those
sentences: start at the first sentence's first word, end before the next
sentence begins. Example: the Architect's line is not "accepted the
program as long as they were given a choice" but the whole sentence "As I
was saying, she stumbled upon a solution whereby nearly 99% of all test
subjects accepted the program as long as they were given a choice, even if
they were only aware of the choice at a near unconscious level." (3:49 to
4:01 of [part 1](https://www.youtube.com/watch?v=cHZl2naX1Xk&t=229); the next sentence starts at 4:00.8).

**The tag's span is generous; podlm trims it (ruled 2026-10-17).** The
author's `start:`/`end:` cover the whole passage wanted, with room to
spare. podlm makes the final cut: it downloads the excerpt (from ytlm),
LOOKS at that section (frames and audio, with the transcript's word
timings), and sets the exact in and out points so only the needed
sentences play — no stray word or half-sentence from before or after
(e.g. "The first Matrix I designed…" starting at 2:49 after Neo's line).
The trimmed points are podlm's production record, not the tag's.

A brief never says how to render anything. In a file each section is a
line; inside a tag in the Doc (a review form) the sections run on one
line and are parsed the same way. `media list` reports `spec_kind`,
`draft_reasons`, and for a brief the parsed sections plus `query` and
`quote`. A tag whose kind changes (a description replaced by `youtube:`)
has its file moved by the next collect and is checked as the new kind,
which flags it for a fresh draft. `_clips/prompts/` (slice 2's single
folder) is gone; SMSTTD's files were moved by hand.

## 9. Still open

- A composed view, `style show <file> --aspect illustration` with each
  rule's origin — proposed.
- Which Veo model and tier (§8j); verify billing before slice 3.

## 10. Build slices

**Slice 1, built 2026-10-17 (uncommitted):** `authorlm/media.py` — the
tag grammar (§4, §5), validation (§5 combinations, §8i structure, draft
under 150 words), `media list` (CLI `media list [file] [--key] [--json]`,
MCP `list_media`) with spoken words, neighbours and illustrations;
collect and `get_status` report not-ready clips (`media`); every book
export strips the tags (`publish_markdown`, `combined_markdown`,
`audio.audio_source`) keeping every word of prose, and
`check_manuscript` reports structural faults. Same day, after the Ecce
Homo trial: prose inside a container is kept in the book, and `[Montage]`
is split from `[Collage]` (§4).
Tests: `tests/test_media.py`. No file writes, no model, no spend.

Deliberate gaps in slice 1:

- Keys were **provisional** in slice 1; slice 2 mints them.
- `look:` and library-qualified names (`studio:…`) are warnings, not
  checked: looks and Studio (§8d) are not built.
- `prior:` resolves against provisional keys.
- Line numbers and chosen images come from the bytes on disk, not the
  observation seam, which drops embed lines.

**Slice 2, built 2026-10-17:**

- **Keys minted at collect** (`media.maintain_keys`, run beside
  `maintain_excerpts` before the snapshot). Every `[Clip]` without `⇢`
  gets a file in its kind's folder (§8l) holding its text (canonical, even when
  empty: a pointer clip's label or a YouTube search brief), and the tag
  keeps an 8-word excerpt and `⇢ <key>.md`. Keys: a slug of the text,
  else `yt-<id>`, else the `use:` stem, made unique by `-2`, `-3`.
  Excerpts are regenerated from the spec; an excerpt edit does not hold
  and is reported. A missing spec file is reported, never recreated.
- **Landing a spec:** `clip spec <key> --text|--file`, or `clip spec
  --batch specs.json` ({key: spec}) / MCP `set_clip_spec(specs)`. The
  file's checkout chooses the road, as for the directives: a local file
  lands the specs at once; a file checked out to the Doc gets them as
  pending forms in its tab, `<<[Clip: excerpt… ⇢ key.md | …]>>{{[Clip:
  whole spec ⇢ key.md | …]}}`, local pristine. The author edits or accepts
  in place (deleting a form declines it); `clip resolve <file>` / MCP
  `resolve_clip_specs` reads the tab back, moves each accepted text into
  its kind's file (§8l), puts the tag back to excerpt + `⇢`, records
  `clip_spec_edit` evidence, collects under no episode and pushes the tab
  (`authorlm/media_road.py`). `doc push` names `clip resolve` while forms
  are out.
- **Writing fields:** `clip set <key> --youtube … --start … --end …
  [--clear FIELD]` / MCP `set_clip_fields`, for the values the author
  confirmed (e.g. from ytlm's quote location).
- Both verbs leave a file checked out to the Doc alone: pull, run, push.
- `media list` reads a keyed clip's description from its spec file and
  reports `keyed` and `spec_path`.

- **Collage and Montage keys** are minted the same way: `[Montage: Choice
  as prison and escape ⇢ montage-choice-as-prison-and.md]`; the spec file
  holds the title (room for a fuller note). Containers carry no fields.

Trial on SMSTTD `ecce-homo.md` (2026-10-17): the pull's collect minted
six keys, the push showed them in the tab, and four search-brief specs
were staged as forms there for review.

Still open: a direct (local) landing records no evidence row.

**Slice 3, built 2026-10-17 (`authorlm/media_gen.py`):** Veo through the
Gemini API over plain REST (no SDK), as illustrations call Gemini.

- **Pin and caps** in `_clips/settings.toml` over defaults: model
  `veo-3.1-fast-generate-preview` (about $0.10/s at 720p; Standard about
  $0.40/s, Lite $0.05/s, list prices of 2026-10-17 — verify), resolution
  720p, 16:9, `max_call_usd = 2.0`, `max_day_usd = 10.0`. Today's spend is
  summed from the job files.
- **Length** from the narration (`estimated_seconds`), rounded up to Veo's
  4, 6 or 8 seconds; reference images or 1080p force 8.
- **Prompt** = the generation spec's sections, the exact on-screen text
  (or "no on-screen text"), and the effective `clip` law — a new style
  aspect, video-only. Frames (`start:`/`end:`) go as `image`/`lastFrame`,
  `cast:` plates as `referenceImages` (asset); images switch person
  generation to `allow_adult`.
- **Verbs:** `clip ensure <key>` returns fresh takes' paths, or the plan
  with `--dry-run` (model, length, cost, prompt — nothing spent);
  `--generate` starts a job (billed), `--confirm` passes a cap; `clip job
  <id>` polls and downloads the take to
  `_clips/<key>-<deschash8>-<lawhash4>-NN.mp4`; the first take is chosen;
  `clip pick <key> --take NN`; `clip attach <key> --file F` adds `iNN`.
  MCP: `clip_ensure` (dry run by default), `clip_job`, `clip_pick`,
  `clip_attach`. Jobs, sent requests (image paths, not bytes) and raw
  responses are kept in `_clips/jobs/` for podlm to diagnose.
- `media list` reports each clip's takes with `stale-desc` and its chosen
  take.

Not built: `illus ensure`; extension of a take; a negative prompt.

**Next:** slice 4, looks and the Studio library (§8d); `illus ensure`.
